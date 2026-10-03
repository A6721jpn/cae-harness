"""FEBio 4.12 single-solid static nodal-force compiler (no contact or rigid tool)."""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from febio_cae.domain import (
    CompatibilityProfile,
    ExecutionBundle,
    ExecutionSetting,
    FileEntry,
    MeshArtifact,
)
from febio_cae.domain.artifacts import TET10_NODE_ORDER_ID
from febio_cae.domain.static_load import StaticLoadRequest, integrate_edge_totals

from ._native_qualification import qualification_document
from .compiler import LocalBundleStore


def compile_static(
    request: StaticLoadRequest,
    mesh: MeshArtifact,
    curves: dict[int, tuple[tuple[int, int, int], ...]],
    profile: CompatibilityProfile,
    store: LocalBundleStore,
    executable: Path,
) -> ExecutionBundle:
    if (
        profile.solver.version != "4.12.0"
        or mesh.frame.value != "World"
        or mesh.provenance.node_ordering_id != TET10_NODE_ORDER_ID
    ):
        raise ValueError("static compiler requires FEBio 4.12 and canonical SI World Tet10")
    if (
        mesh.provenance.mesh_recipe_digest != request.digest
        or len(mesh.provenance.source_body_ids) != 1
    ):
        raise ValueError("mesh is not bound to this single-solid static recipe")
    expected = {
        ("displacement", "displacement", "node", "VEC3F", "m", 1),
        ("reaction", "reaction forces", "node", "VEC3F", "N", -1),
        ("stress", "stress", "element", "MAT3FS", "Pa", 1),
    }
    actual = {
        (
            m.canonical_id,
            m.native_name,
            m.location,
            m.value_type,
            m.unit,
            m.raw_sign * m.canonical_sign,
        )
        for m in profile.output_mappings
    }
    if actual != expected or any(m.frame.value != "World" for m in profile.output_mappings):
        raise ValueError("static profile must map exactly displacement, reaction forces and stress")
    fixed: set[int] = set()
    for tag in request.fixed_face_ids:
        matches = [
            s
            for s in mesh.sets
            if s.set_id == f"fixed-face-{tag}"
            and s.kind == "node"
            and s.source_selection_digest == request.selection_digest("face", tag)
        ]
        if len(matches) != 1:
            raise ValueError("fixed face source binding is missing")
        fixed.update(cast(Sequence[int], matches[0].member_ids))
    for load in request.loads:
        matches = [
            s
            for s in mesh.sets
            if s.set_id == f"loaded-curve-{load.curve_id}"
            and s.kind == "node"
            and s.source_selection_digest == request.selection_digest("curve", load.curve_id)
        ]
        expected_nodes = {n for line in curves.get(load.curve_id, ()) for n in line}
        if len(matches) != 1 or set(matches[0].member_ids) != expected_nodes:
            raise ValueError("loaded curve source binding is missing")
    forces = integrate_edge_totals(request, mesh, curves, fixed)
    root = ET.Element("febio_spec", {"version": "4.0"})
    module = ET.SubElement(root, "Module", {"type": "solid"})
    ET.SubElement(module, "units").text = "SI"
    control = ET.SubElement(root, "Control")
    ET.SubElement(control, "analysis", {"type": "static"})
    for name, value in (
        ("time_steps", "10"),
        ("step_size", "0.1"),
        ("plot_level", "PLOT_MAJOR_ITRS"),
        ("plot_zero_state", "1"),
    ):
        ET.SubElement(control, name).text = value
    solver = ET.SubElement(control, "solver", {"type": "solid"})
    for name, value in (
        ("dtol", ".001"),
        ("etol", ".01"),
        ("rtol", "0"),
        ("min_residual", "0"),
        ("max_refs", "15"),
    ):
        ET.SubElement(solver, name).text = value
    materials = ET.SubElement(root, "Material")
    material = ET.SubElement(
        materials, "material", {"id": "1", "name": "part-material", "type": "isotropic elastic"}
    )
    ET.SubElement(material, "E").text = f"{request.youngs_modulus_pa:.17g}"
    ET.SubElement(material, "v").text = f"{request.poisson_ratio:.17g}"
    native_mesh = ET.SubElement(root, "Mesh")
    nodes = ET.SubElement(native_mesh, "Nodes", {"name": "part-nodes"})
    for node in mesh.nodes:
        ET.SubElement(nodes, "node", {"id": str(node.node_id)}).text = ",".join(
            f"{v:.17g}" for v in node.coordinates_si
        )
    elements = ET.SubElement(native_mesh, "Elements", {"type": "tet10", "name": "part"})
    for element in mesh.elements:
        if element.element_type != "tet10":
            raise ValueError("static compiler only supports Tet10")
        ET.SubElement(elements, "elem", {"id": str(element.element_id)}).text = ",".join(
            str(n) for n in element.node_ids
        )
    support = ET.SubElement(native_mesh, "NodeSet", {"name": "fixed"})
    support.text = ",".join(str(n) for n in sorted(fixed))
    for node_id in sorted(forces):
        ET.SubElement(native_mesh, "NodeSet", {"name": f"force-node-{node_id}"}).text = str(node_id)
    domains = ET.SubElement(root, "MeshDomains")
    ET.SubElement(domains, "SolidDomain", {"name": "part", "mat": "part-material"})
    boundary = ET.SubElement(root, "Boundary")
    # Zero prescribed DOFs also retain reactions in FEBio's nodal force output.
    for axis in "xyz":
        bc = ET.SubElement(
            boundary,
            "bc",
            {"type": "prescribed displacement", "name": f"fixed-{axis}", "node_set": "fixed"},
        )
        ET.SubElement(bc, "dof").text = axis
        ET.SubElement(bc, "value", {"lc": "1"}).text = "0"
        ET.SubElement(bc, "relative").text = "0"
    loads = ET.SubElement(root, "Loads")
    # FEBio nodal_force.value is a force vector per node, not a line density.
    for node_id, force in sorted(forces.items()):
        nodal_load = ET.SubElement(
            loads, "nodal_load", {"type": "nodal_force", "node_set": f"force-node-{node_id}"}
        )
        ET.SubElement(nodal_load, "value", {"lc": "1"}).text = ",".join(f"{v:.17g}" for v in force)
    data = ET.SubElement(root, "LoadData")
    controller = ET.SubElement(data, "load_controller", {"id": "1", "type": "loadcurve"})
    ET.SubElement(controller, "interpolate").text = "LINEAR"
    ET.SubElement(controller, "extend").text = "CONSTANT"
    points = ET.SubElement(controller, "points")
    ET.SubElement(points, "point").text = "0,0"
    ET.SubElement(points, "point").text = "1,1"
    output = ET.SubElement(root, "Output")
    plot = ET.SubElement(output, "plotfile", {"type": "febio"})
    ET.SubElement(plot, "compression").text = "0"
    for mapping in profile.output_mappings:
        ET.SubElement(plot, "var", {"type": mapping.native_name})
    ET.indent(root, space="  ")
    content = b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8")
    descriptor = qualification_document(profile.solver)
    bundle_id = (
        "static-bundle-"
        + hashlib.sha256(
            request.digest.encode()
            + mesh.artifact_digest.encode()
            + profile.to_bytes()
            + content
            + str(executable.resolve()).encode()
        ).hexdigest()[:24]
    )
    entries = [
        FileEntry("input/case.feb", hashlib.sha256(content).hexdigest(), len(content), "input")
    ]
    if descriptor is not None:
        entries.append(
            FileEntry(
                "input/native-runtime.json",
                hashlib.sha256(descriptor).hexdigest(),
                len(descriptor),
                "native-runtime",
            )
        )
    settings = tuple(
        ExecutionSetting(k, v)
        for k, v in (
            ("solver_threads", 1),
            ("xplt_version", "0x35"),
            ("feb_schema_version", "4.0"),
            ("compatibility_profile_digest", hashlib.sha256(profile.to_bytes()).hexdigest()),
            ("compiler_dialect", "feb4-single-solid-edge-total-v1"),
            ("output_plot_path", "output/results.xplt"),
            ("output_log_path", "output/solver.log"),
        )
    )
    bundle = ExecutionBundle(
        bundle_id,
        "static-" + request.digest[:24],
        "static-spec-" + request.digest[:24],
        request.digest,
        mesh.artifact_digest,
        profile.profile_id,
        profile.solver,
        tuple(entries),
        (
            str(executable.resolve()),
            "-i",
            "input/case.feb",
            "-o",
            "output/solver.log",
            "-p",
            "output/results.xplt",
            "-noappend",
            "-noconfig",
        ),
        str(store.root),
        1,
        settings,
    )
    store.stage(bundle_id, "input/case.feb", content)
    if descriptor is not None:
        store.stage(bundle_id, "input/native-runtime.json", descriptor)
    return bundle
