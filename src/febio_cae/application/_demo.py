"""Concrete private planar-demo composition over registered native GM03 evidence."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from febio_cae.adapters.febio import (
    CompilerAdapter,
    LocalResultDataStore,
    QualityAdapter,
    RunnerAdapter,
    XpltReaderAdapter,
    xplt_reader,
)
from febio_cae.adapters.geometry import (
    BackendBody,
    BackendFace,
    BackendInspection,
    BackendMesh,
    StepGeometryMeshAdapter,
)
from febio_cae.domain import (
    AttemptRecord,
    CaseRevision,
    ExecutionBundle,
    FileEntry,
    FrameId,
    MeshArtifact,
    OwnershipPort,
    PortError,
    PortErrorCategory,
    ResolvedFileContent,
    ResultManifest,
)
from febio_cae.domain.codec import decode_record, encode_record
from febio_cae.domain.lifecycle import TaskStatus
from febio_cae.storage import CaseStorage
from febio_cae.storage.mesh_quality import PlanarDemoRegistration, PlanarPreparationRegistration

from ._required_quality import required_quality_summary

if TYPE_CHECKING:
    from .service import RegisteredCaseService


class _BundleBytes:
    """Compiler staging without creating its future owned publication directory."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.contents: dict[tuple[str, str], bytes] = {}

    def stage(self, bundle_id: str, logical_path: str, content: bytes) -> None:
        key = bundle_id, logical_path
        if key in self.contents and self.contents[key] != content:
            raise ValueError("compiler staging identity changed")
        self.contents[key] = content

    def resolve(self, bundle: ExecutionBundle, logical_path: str) -> bytes:
        entry = next(e for e in bundle.files if e.logical_path == logical_path)
        payload = self.contents[bundle.bundle_id, logical_path]
        return ResolvedFileContent(entry, payload).content


class _RecordedInspection:
    def __init__(self, report: BackendInspection, original: MeshArtifact) -> None:
        self.report = report
        self.backend_id = original.provenance.tool_id
        self.backend_version = original.provenance.tool_version

    def inspect(self, content: bytes, requested_body_ids: Sequence[str]) -> BackendInspection:
        if hashlib.sha256(content).hexdigest() != self.report.source_digest or not set(
            requested_body_ids
        ) <= {b.body_id for b in self.report.bodies}:
            raise ValueError("registered native inspection source/body mismatch")
        return self.report

    def mesh(self, content: bytes, body_id: str, global_size_si: float) -> BackendMesh:
        raise PortError(PortErrorCategory.CONFLICT, "Gmsh demo budget is exhausted")


def recorded_geometry(
    storage: CaseStorage, registration: PlanarDemoRegistration
) -> StepGeometryMeshAdapter:
    """Replay pinned inspection bytes; never import or invoke a native backend."""

    def source(asset_id: str) -> bytes:
        return storage.resolve_source(storage.source_asset(asset_id)).content

    original = decode_record(source("gm03-mesh"), MeshArtifact)
    raw = json.loads(source("gm03-backend-inspection"))

    def frame(value: Any) -> FrameId:
        return FrameId(value["value"] if isinstance(value, dict) else value)

    bodies = tuple(
        BackendBody(
            **{
                **body,
                "faces": tuple(
                    BackendFace(**{**face, "frame": frame(face["frame"])}) for face in body["faces"]
                ),
            }
        )
        for body in raw["bodies"]
    )
    report = BackendInspection(**{**raw, "frame": frame(raw["frame"]), "bodies": bodies})
    if report.geometry_digest != registration.geometry_digest:
        raise ValueError("native inspection geometry differs from admission")
    return StepGeometryMeshAdapter(
        _RecordedInspection(report, original), source_asset=storage.source_asset("cad")
    )


def run_demo(
    service: RegisteredCaseService,
    case_id: str,
    revision_id: str,
    *,
    executable: str,
    preflight: bool,
) -> dict[str, object]:
    storage = service._storage(case_id)

    def source(asset_id: str) -> bytes:
        return storage.resolve_source(storage.source_asset(asset_id)).content

    revision = storage.get_revision(case_id, revision_id)
    registration = storage.resolve_revision_mesh_quality(revision)
    if not isinstance(registration, PlanarDemoRegistration):
        raise PortError(
            PortErrorCategory.INVALID_INPUT, "run-demo requires registered planar admission"
        )
    mesh = service._planar_execution_mesh(storage, registration, revision)
    service._verify_execution_mesh(storage, registration, revision, mesh)
    if isinstance(registration, PlanarPreparationRegistration):
        from febio_cae.storage.preparation import PreparationStore

        from ._preparation import geometry_from_output

        geometry = geometry_from_output(
            PreparationStore(storage).origin_output(registration, revision),
            storage.resolve_source(storage.source_asset("cad")),
        )
    else:
        geometry = recorded_geometry(storage, registration)
    service.geometry = geometry
    service._placed_selection = geometry.resolve_placed_selection
    profile = service.compatibility.get_profile(revision.spec.solver_policy.profile.profile_id)
    solver_path = Path(executable).absolute()
    if hashlib.sha256(solver_path.read_bytes()).hexdigest() != profile.solver.executable_digest:
        raise ValueError("solver executable differs from registered identity")
    reader_payload = Path(xplt_reader.__file__).read_bytes()
    if hashlib.sha256(reader_payload).hexdigest() != profile.reader.executable_digest or (
        not isinstance(registration, PlanarPreparationRegistration)
        and reader_payload != source("registered-reader-source")
    ):
        raise ValueError("active reader bytes differ from registered identity")
    stores: list[_BundleBytes] = []

    def build(
        current: CaseRevision, destination: Path
    ) -> tuple[MeshArtifact, ExecutionBundle, Mapping[str, bytes]]:
        if current != revision:
            raise ValueError("demo revision changed")
        store = _BundleBytes(destination)
        bundle = CompilerAdapter(store=store, executable=solver_path).compile(
            current, mesh, profile
        )
        stores.append(store)
        return (
            mesh,
            bundle,
            {e.logical_path: store.resolve(bundle, e.logical_path) for e in bundle.files},
        )

    def runner(ownership: OwnershipPort, root: Path, inputs: Mapping[str, bytes]) -> RunnerAdapter:
        return RunnerAdapter(ownership=ownership, root=root, bundle_store=stores[-1])

    def read(
        attempt: AttemptRecord,
        bundle: ExecutionBundle,
        files: tuple[FileEntry, ...],
        registered: CaseStorage,
    ) -> ResultManifest:
        entry = next(e for e in files if e.logical_path == "output/results.xplt")
        data = LocalResultDataStore()
        part = revision.spec.geometry.body_id.value
        tool = revision.spec.rigid_tool.primitive.body_id.value
        data.register_source(
            attempt,
            bundle,
            registered._file_content(attempt, entry),
            mesh=mesh,
            state_times=tuple(t.to_si().value for t in revision.spec.outputs.saved_times),
            part_bodies={1: part, 2: tool},
            entity_ids={
                "displacement": tuple(str(n.node_id) for n in mesh.nodes),
                "stress": tuple(str(e.element_id) for e in mesh.elements if e.body_id == part),
                "contact_force": (tool,),
            },
        )
        manifest = XpltReaderAdapter(profile=profile, data_store=data).read(attempt, bundle)
        for observation in manifest.read_result.observations:
            if observation.data_ref is None:
                raise ValueError("reader produced no numeric reference")
            registered.register_numeric_data(data.resolve(observation.data_ref))
        return replace(manifest, files=files)

    if preflight:
        with storage.evidence_snapshot(), storage.revision_snapshot(case_id, revision_id):
            validated = service._validate(case_id)
            if isinstance(registration, PlanarPreparationRegistration):
                draft = validated.draft
                if (
                    validated.status != "VALIDATED"
                    or draft is None
                    or storage.revision_generation(revision_id) != draft.generation
                    or draft.values.to_case_spec().to_bytes() != revision.spec.to_bytes()
                    or tuple(draft.evidence) != tuple(revision.evidence)
                ):
                    raise PortError(
                        PortErrorCategory.CONFLICT,
                        "preflight requires current validated frozen revision",
                    )
            if validated.status != "VALIDATED":
                raise ValueError("demo current validation failed")
            _, bundle, _ = build(revision, storage.root / "preflight-not-published")
        return {
            "status": "PREFLIGHT_PASSED",
            "case_id": case_id,
            "revision_id": revision_id,
            "native_starts": 0,
            "bundle": bundle.to_dict(),
            "surface_approximation": "UNVERIFIED",
        }
    manifest = service._execute_ports(
        case_id, revision_id, build=build, runner_factory=runner, read=read
    )
    quality = QualityAdapter().assess(manifest, revision, mesh, profile, storage)
    storage.ingest_source(
        asset_id="quality-" + quality.assessment_id[:24],
        source_kind="registered_document",
        media_type="application/json",
        content=encode_record(quality),
    )
    quality_status, coverage = required_quality_summary(
        manifest, revision, mesh, profile, quality, storage
    )
    force = storage.resolve_manifest_output(manifest.manifest_id, "contact_force")
    final_force = force.values[-1]
    connected = all(math.isfinite(v) for v in final_force) and any(v != 0 for v in final_force)
    return {
        "status": "NEEDS_PREVIEW" if connected and quality_status == "PASS" else "NEEDS_REVIEW",
        "run_status": "SUCCEEDED",
        "case_id": case_id,
        "revision_id": revision_id,
        "manifest": manifest.to_dict(),
        "quality": quality.to_dict(),
        "quality_status": quality_status,
        "quality_registration_status": quality.overall_status.value,
        "required_quality": coverage,
        "quality_reason": "mandatory numerical coverage is unverified; see required_quality",
        "task_status": TaskStatus.FAILED.value
        if quality_status == "FAIL"
        else TaskStatus.NEEDS_QUALITY.value
        if quality_status == "UNVERIFIED"
        else TaskStatus.NEEDS_PREVIEW.value,
        "final_tool_force": list(final_force),
        "finite_nonzero_tool_force": connected,
        "surface_approximation": "UNVERIFIED",
        "studio": "NOT_RUN",
    }
