"""Owned, authenticated single-solid STEP meshing with CAD line3 selections."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, cast

from febio_cae.domain import FrameId, Quantity, RigidTransform, Translation3
from febio_cae.domain.artifacts import (
    TET10_FACE_ORDER_ID,
    TET10_NODE_ORDER_ID,
    MeshArtifact,
    MeshProvenance,
    MeshQualityRecord,
    MeshSet,
)
from febio_cae.domain.canonical import canonical_bytes
from febio_cae.domain.codec import decode_record
from febio_cae.domain.static_load import StaticLoadRequest, integrate_edge_totals

from ._gmsh_runtime import (
    capture_runtime_binding,
    load_verified_gmsh,
    verify_runtime_identity,
)
from .adapter import StepGeometryMeshAdapter
from .backend import BackendLocalRefinement, GeometryMeshBackend
from .gmsh_occ import GmshOCCBackend, GmshOCCConfig
from .preparation import _run_owned, resource_snapshot


class StaticGmshBackend(GmshOCCBackend):
    """Static-only native-unit and algorithm policy; never retries or heals CAD."""

    def __init__(self, request: StaticLoadRequest, runtime_binding: dict[str, Any]) -> None:
        super().__init__(
            GmshOCCConfig(
                expected_occt_version="7.8.1",
                require_step_ap214=True,
                cpu_workers=request.cpu_workers,
            )
        )
        self.request = request
        self.runtime_binding = runtime_binding
        self.runtime_identity: dict[str, Any] | None = None
        self.curves: dict[int, tuple[tuple[int, int, int], ...]] = {}

    @property
    def native_scale(self) -> float:
        return 0.001 if self.request.native_coordinate_unit == "MM" else 1.0

    def _load_module(self) -> Any:
        module, identity = load_verified_gmsh(self.runtime_binding)
        self.runtime_identity = identity
        return module

    def _import_step(self, gmsh: Any, path: Path) -> None:
        if self.request.native_coordinate_unit == "M":
            super()._import_step(gmsh, path)
            return
        gmsh.option.setString("Geometry.OCCTargetUnit", "MM")
        gmsh.model.add("febio_cae_step")
        gmsh.model.occ.importShapes(str(path))
        gmsh.model.occ.synchronize()

    def _inspect_contexts(self, gmsh: Any, native_scale_to_si: float) -> Any:
        return super()._inspect_contexts(gmsh, self.native_scale)

    def _mesh_context(
        self,
        gmsh: Any,
        context: Any,
        source_digest: str,
        geometry_digest: str,
        native_scale_to_si: float,
        global_size_si: float,
        **kwargs: Any,
    ) -> Any:
        gmsh.option.setNumber("Mesh.Algorithm", self.request.algorithm_2d)
        gmsh.option.setNumber("Mesh.Algorithm3D", self.request.algorithm_3d)
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", self.request.curvature_points)
        gmsh.option.setNumber("Mesh.SecondOrderLinear", int(self.request.second_order_linear))
        gmsh.option.setNumber("General.Terminal", 1)
        mesh = super()._mesh_context(
            gmsh,
            context,
            source_digest,
            geometry_digest,
            self.native_scale,
            global_size_si,
            **kwargs,
        )
        if (
            len(mesh.nodes) > self.request.max_nodes
            or len(mesh.elements) > self.request.max_elements
        ):
            raise ValueError("static mesh exceeds declared node/element caps")
        surfaces = gmsh.model.getBoundary([(3, context.volume_tag)], False, False)
        owned_curves = {
            abs(tag) for dim, tag in gmsh.model.getBoundary(surfaces, False, False) if dim == 1
        }
        node_ids = {node.node_id for node in mesh.nodes}
        for load in self.request.loads:
            if load.curve_id not in owned_curves:
                raise ValueError(f"selected CAD curve {load.curve_id} is not on the source solid")
            types, tags, connectivity = gmsh.model.mesh.getElements(1, load.curve_id)
            lines: list[tuple[int, int, int]] = []
            for kind, ids, nodes in zip(types, tags, connectivity, strict=True):
                properties = gmsh.model.mesh.getElementProperties(int(kind))
                if int(kind) != 8 or tuple(int(properties[i]) for i in (1, 2, 3, 5)) != (
                    1,
                    2,
                    3,
                    2,
                ):
                    raise ValueError(
                        "selected curve must contain complete quadratic line3 elements"
                    )
                if len(nodes) != 3 * len(ids):
                    raise ValueError("truncated line3 connectivity")
                for offset in range(0, len(nodes), 3):
                    line = (int(nodes[offset]), int(nodes[offset + 1]), int(nodes[offset + 2]))
                    if len(set(line)) != 3 or not set(line) <= node_ids:
                        raise ValueError("curve line3 nodes are not source-body mesh nodes")
                    lines.append(line)
            if not lines:
                raise ValueError("selected CAD curve has no line3 mesh")
            self.curves[load.curve_id] = tuple(lines)
        return mesh


def produce(content: bytes, request: StaticLoadRequest, binding: dict[str, Any]) -> dict[str, Any]:
    if hashlib.sha256(content).hexdigest() != request.source_sha256:
        raise ValueError("source bytes differ from typed request SHA-256")
    backend = StaticGmshBackend(request, binding)
    inspection = backend.inspect(content, ())
    if len(inspection.bodies) != 1 or not inspection.bodies[0].closed_solid:
        raise ValueError("static preparation requires exactly one closed solid")
    body = inspection.bodies[0].body_id
    refinements = tuple(
        BackendLocalRefinement(
            body,
            refinement.region.center.frame,
            tuple(getattr(refinement.region.center, axis).to_si().value for axis in "xyz"),
            refinement.region.radius.to_si().value,
            refinement.size.to_si().value,
        )
        for refinement in request.local_refinements
    )
    native = backend.mesh(content, body, request.global_size_m, local_refinements=refinements)
    if (
        native.source_digest != request.source_sha256
        or native.geometry_digest != inspection.geometry_digest
    ):
        raise ValueError("inspection/mesh source binding mismatch")
    frame = FrameId("World")
    transform = RigidTransform(
        frame,
        frame,
        Translation3(frame, Quantity(0, "m"), Quantity(0, "m"), Quantity(0, "m")),
        ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
    )
    mapped = StepGeometryMeshAdapter(cast(GeometryMeshBackend, backend))._map_backend_mesh(
        native,
        transform=transform,
        expected_body_id=body,
        node_start=1,
        element_start=1,
        expected_geometry_digest=inspection.geometry_digest,
        measure_quadratic_volume=True,
    )
    node_map = {
        node.node_id: index + 1
        for index, node in enumerate(sorted(native.nodes, key=lambda n: n.node_id))
    }
    curves = {
        tag: tuple((node_map[line[0]], node_map[line[1]], node_map[line[2]]) for line in lines)
        for tag, lines in backend.curves.items()
    }
    mapped_faces = {face.face_id: face for face in mapped.faces}
    boundary_edges = {
        (
            min(face.node_ids[a], face.node_ids[b]),
            max(face.node_ids[a], face.node_ids[b]),
            face.node_ids[mid],
        )
        for face in mapped.faces
        for a, b, mid in ((0, 1, 3), (1, 2, 4), (2, 0, 5))
    }
    if any(
        (min(line[0], line[1]), max(line[0], line[1]), line[2]) not in boundary_edges
        for lines in curves.values()
        for line in lines
    ):
        raise ValueError("CAD line3 does not match complete quadratic exterior mesh edges")
    sets: list[MeshSet] = []
    fixed: set[int] = set()
    for tag in request.fixed_face_ids:
        faces = [face for face in native.faces if face.source_face_id == f"{body}:face-{tag}"]
        if not faces:
            raise ValueError(f"fixed CAD face {tag} is absent")
        members = sorted({n for face in faces for n in mapped_faces[face.face_id].node_ids})
        fixed.update(members)
        sets.append(
            MeshSet(
                f"fixed-face-{tag}",
                "node",
                body,
                tuple(members),
                request.selection_digest("face", tag),
            )
        )
    for tag, lines in curves.items():
        sets.append(
            MeshSet(
                f"loaded-curve-{tag}",
                "node",
                body,
                tuple(sorted({n for line in lines for n in line})),
                request.selection_digest("curve", tag),
            )
        )
    provenance = MeshProvenance(
        inspection.geometry_digest,
        (body,),
        tuple(s.source_selection_digest for s in sets),
        request.digest,
        backend.backend_id,
        backend.backend_version,
        "backend-tet10-to-domain-tet10-v1",
        TET10_NODE_ORDER_ID,
        TET10_FACE_ORDER_ID,
    )
    quality = (
        MeshQualityRecord(
            "minimum_corner_volume",
            min(mapped.corner_volumes),
            "m3",
            0.0,
            "PASS",
            "Exact whole-Tet10 positive mapping proof and complete exterior coverage passed",
        ),
        MeshQualityRecord(
            "minimum_quadratic_volume",
            min(mapped.signed_volumes),
            "m3",
            0.0,
            "PASS",
            "Integrated represented quadratic Jacobian",
        ),
        MeshQualityRecord(
            "cad_approximation",
            0.0,
            "m",
            None,
            "UNVERIFIED",
            "No certified arbitrary-CAD approximation bound",
        ),
    )
    selected_faces = tuple(
        face.to_dict()
        for face in inspection.bodies[0].faces
        if face.face_id in {f"{body}:face-{tag}" for tag in request.fixed_face_ids}
    )
    nodes, elements, exterior_faces = mapped.nodes, mapped.elements, mapped.faces
    del native, node_map, mapped_faces, boundary_edges, faces, mapped, content
    mesh = MeshArtifact(
        "static-mesh-" + request.digest[:24],
        frame,
        provenance,
        nodes,
        elements,
        exterior_faces,
        tuple(sets),
        quality,
    )
    forces = integrate_edge_totals(request, mesh, curves, fixed)
    return {
        "mesh": mesh.to_dict(),
        "source_sha256": request.source_sha256,
        "request_digest": request.digest,
        "inspection_geometry_digest": inspection.geometry_digest,
        "native_coordinate_unit": request.native_coordinate_unit,
        "fixed_cad_face_geometry": list(selected_faces),
        "curves": {str(tag): [list(line) for line in lines] for tag, lines in curves.items()},
        "nodal_forces_n": {str(node): list(force) for node, force in sorted(forces.items())},
        "runtime_identity": backend.runtime_identity,
    }


def prepare_native(content: bytes, request: StaticLoadRequest, directory: Path) -> dict[str, Any]:
    resources = resource_snapshot(request.cpu_workers)
    if (
        request.cpu_workers > resources["available_cpus"]
        or request.memory_bytes > resources["memory_bytes"]
    ):
        raise ValueError("requested resources exceed available owned native budget")
    directory.mkdir(parents=True, exist_ok=False)
    binding = capture_runtime_binding()
    (directory / "input.json").write_bytes(
        canonical_bytes(
            {"content_hex": content.hex(), "request": request.to_dict(), "runtime_binding": binding}
        )
    )
    package_root = Path(__file__).resolve().parents[3]
    bootstrap = f"import sys; sys.path.insert(0, {str(package_root)!r}); from febio_cae.adapters.geometry.static_load import _main; _main()"
    process = _run_owned(
        (sys.executable, "-I", "-c", bootstrap),
        directory,
        cpu_workers=request.cpu_workers,
        timeout_seconds=request.mesh_wall_seconds,
        memory_bytes=request.memory_bytes,
    )
    output = directory / "output.json"
    if output.stat().st_size > request.memory_bytes // 2:
        raise ValueError("native static response exceeds bounded memory")
    result = json.loads(output.read_bytes())
    verify_runtime_identity(binding, result["runtime_identity"])
    mesh = decode_record(canonical_bytes(result["mesh"]), MeshArtifact)
    if (
        result["source_sha256"] != request.source_sha256
        or result["request_digest"] != request.digest
        or mesh.provenance.mesh_recipe_digest != request.digest
    ):
        raise ValueError("native response source/recipe binding mismatch")
    result["process"] = process
    result["runtime_binding"] = binding
    return result


def _main() -> None:
    payload = json.loads(Path("input.json").read_bytes())
    result = produce(
        bytes.fromhex(payload.pop("content_hex")),
        StaticLoadRequest.from_dict(payload.pop("request")),
        payload["runtime_binding"],
    )
    Path("output.json").write_bytes(canonical_bytes(result))
