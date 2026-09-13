"""Private, bounded current-source producer. No public replay/fake mode."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from febio_cae.adapters.febio._windows_job import LaunchCleanupPending, WindowsJobProcess
from febio_cae.application._preparation_request import normalize_request
from febio_cae.domain import (
    CaseRevision,
    FrameId,
    GeometryInspectionRequest,
    NumericalProfileRef,
    PortError,
    PortErrorCategory,
    RigidPrimitive,
    SourceAssetContent,
    SourceAssetRef,
    Quantity,
)
from febio_cae.domain.canonical import canonical_bytes
from febio_cae.domain.codec import decode_record

from .adapter import StepGeometryMeshAdapter
from .backend import (
    BackendBody,
    BackendFace,
    BackendInspection,
    BackendLocalRefinement,
    BackendMesh,
)
from .gmsh_occ import GmshOCCBackend, GmshOCCConfig, _require_ap214_header

_pending: list[WindowsJobProcess] = []


def resource_snapshot(cpu_limit: int) -> dict[str, int]:
    if os.name != "nt":
        raise OSError("planar preparation requires Windows owned-process protection")

    class MemoryStatus(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
            (name, ctypes.c_ulonglong)
            for name in (
                "total",
                "available",
                "total_page",
                "available_page",
                "total_virtual",
                "available_virtual",
                "extended",
            )
        ]

    memory = MemoryStatus()
    memory.length = ctypes.sizeof(memory)
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(MemoryStatus)]
    api.GlobalMemoryStatusEx.restype = ctypes.c_int
    if not api.GlobalMemoryStatusEx(ctypes.byref(memory)):
        raise ctypes.WinError(ctypes.get_last_error())
    available = os.cpu_count() or 1
    process_mask, system_mask = ctypes.c_size_t(), ctypes.c_size_t()
    api.GetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    api.GetProcessAffinityMask.restype = ctypes.c_int
    if api.GetProcessAffinityMask(
        ctypes.c_void_p(-1), ctypes.byref(process_mask), ctypes.byref(system_mask)
    ):
        available = min(available, process_mask.value.bit_count() or available)
    limit = min(memory.total, memory.available) * 4 // 5
    if limit <= 0:
        raise OSError("available preparation memory is exhausted")
    return {
        "available_cpus": available,
        "cpu_workers": min(available, cpu_limit),
        "total_physical_bytes": memory.total,
        "available_physical_bytes": memory.available,
        "memory_bytes": limit,
    }


def _run_owned(
    argv: tuple[str, ...],
    directory: Path,
    *,
    timeout_seconds: float,
    memory_bytes: int,
) -> dict[str, int]:
    deadline = time.monotonic() + timeout_seconds
    for retained in tuple(_pending):
        if not retained.retry_launch_cleanup():
            raise OSError("preparation process cleanup is still unconfirmed")
        _pending.remove(retained)
    with (
        (directory / "stdout.log").open("wb") as stdout,
        (directory / "stderr.log").open("wb") as stderr,
    ):
        try:
            process = WindowsJobProcess(
                argv, directory, stdout, stderr, memory_limit_bytes=memory_bytes
            )
        except LaunchCleanupPending as error:
            _pending.append(error.process)
            raise
        try:
            while process.poll() is None or process.active_processes() != 0:
                if time.monotonic() >= deadline:
                    process.terminate_tree()
                    raise TimeoutError("preparation owned-process wall deadline exceeded")
                time.sleep(min(0.02, max(0.001, deadline - time.monotonic())))
            code = process.poll()
            if code != 0:
                raise OSError(f"preparation child failed with exit {code}; see its stderr.log")
            return {"pid": process.pid, "creation_time": process.creation_time, "exit_code": 0}
        finally:
            if not process.retry_launch_cleanup():
                # Keep the exact handles; never discover or kill a process by PID.
                _pending.append(process)
                raise LaunchCleanupPending(process)


class _MeasuredGmsh(GmshOCCBackend):
    def __init__(self, cpu: int) -> None:
        super().__init__(
            GmshOCCConfig(expected_occt_version="7.8.1", require_step_ap214=True, cpu_workers=cpu)
        )
        self.evidence: dict[str, Any] = {}

    def _prepare_owned_session(self, gmsh: Any) -> None:
        super()._prepare_owned_session(gmsh)
        module = Path(gmsh.__file__).resolve(strict=True)
        current = {
            "module": str(module),
            "module_sha256": hashlib.sha256(module.read_bytes()).hexdigest(),
            "gmsh_version": gmsh.__version__,
            "occt_version": "7.8.1",
            "build_info": gmsh.option.getString("General.BuildInfo"),
        }
        if self.evidence and self.evidence != current:
            raise ValueError("Gmsh module/build identity changed during preparation")
        self.evidence = current

    def _inspect_faces(
        self, gmsh: Any, body_id: str, volume_tag: int, native_scale_to_si: float
    ) -> tuple[BackendFace, ...]:
        faces = super()._inspect_faces(gmsh, body_id, volume_tag, native_scale_to_si)
        for face in faces:
            tag = int(face.face_id.rsplit("-", 1)[1])
            if gmsh.model.getType(2, tag) != "Plane":
                raise ValueError("public preparation currently requires planar STEP faces")
        return faces


def _make_backend(cpu: int) -> Any:
    return _MeasuredGmsh(cpu)


class CurrentInspection:
    """Re-use only inspection emitted by this producer, never caller/demo assets."""

    def __init__(
        self,
        report: BackendInspection,
        backend_id: str,
        backend_version: str,
        *,
        native_tool_inspection: BackendInspection | None = None,
        native_tool_primitive: RigidPrimitive | None = None,
        native_tool_geometry_digest: str | None = None,
    ) -> None:
        if not isinstance(report, BackendInspection):
            raise TypeError("current inspection report must be a BackendInspection")
        for value, name in ((backend_id, "backend_id"), (backend_version, "backend_version")):
            if not isinstance(value, str) or not value or value != value.strip():
                raise ValueError(f"{name} must be non-empty text")
        if (native_tool_inspection is None) != (native_tool_primitive is None):
            raise ValueError("native tool inspection and primitive must be recorded together")
        if native_tool_inspection is not None and native_tool_primitive is not None:
            if not isinstance(native_tool_primitive, RigidPrimitive):
                raise TypeError("native tool primitive must be a RigidPrimitive")
            if (
                not isinstance(native_tool_geometry_digest, str)
                or len(native_tool_geometry_digest) != 64
                or any(c not in "0123456789abcdef" for c in native_tool_geometry_digest)
            ):
                raise ValueError("native tool geometry digest must be a SHA256 identity")
            self._validate_native_record(
                native_tool_inspection,
                native_tool_primitive,
                native_tool_geometry_digest,
            )
        elif native_tool_geometry_digest is not None:
            raise ValueError("native tool geometry identity requires a native tool record")
        self.report = report
        self.backend_id = backend_id
        self.backend_version = backend_version
        self.native_tool_inspection = native_tool_inspection
        self.native_tool_primitive = native_tool_primitive
        self.native_tool_geometry_digest = native_tool_geometry_digest

    @staticmethod
    def _validate_native_record(
        report: BackendInspection,
        primitive: RigidPrimitive,
        expected_geometry_digest: str | None = None,
    ) -> None:
        if primitive.kind not in {"sphere", "cylinder"}:
            raise ValueError("current native tool record requires a curved primitive")
        if expected_geometry_digest is not None and report.geometry_digest != expected_geometry_digest:
            raise ValueError("current native tool geometry identity differs")
        if report.frame != primitive.local_frame or tuple(report.declared_units) != ("m",):
            raise ValueError("current native tool record must remain in its local metre frame")
        if len(report.bodies) != 1 or report.bodies[0].body_id != primitive.body_id.value:
            raise ValueError("current native tool body identity differs")
        body = report.bodies[0]
        if not body.closed_solid or report.unsupported_topology or report.defects or body.defects:
            raise ValueError("current native tool inspection contains unsupported topology")
        if any(face.defects or face.frame != primitive.local_frame for face in body.faces):
            raise ValueError("current native tool face identity differs")

    def inspect(self, content: bytes, requested_body_ids: Sequence[str]) -> BackendInspection:
        if hashlib.sha256(content).hexdigest() != self.report.source_digest or not set(
            requested_body_ids
        ) <= {b.body_id for b in self.report.bodies}:
            raise ValueError("current preparation inspection source/body mismatch")
        return self.report

    def mesh(
        self,
        content: bytes,
        body_id: str,
        global_size_si: float,
        *,
        local_refinements: tuple[BackendLocalRefinement, ...] = (),
    ) -> BackendMesh:
        del local_refinements
        raise PortError(
            PortErrorCategory.CONFLICT,
            "current preparation generation allowance has already been consumed",
        )

    def inspect_rigid_primitive(
        self, primitive: RigidPrimitive, *, geometry_digest: str
    ) -> BackendInspection:
        """Replay the producer-owned local native inspection only."""

        if self.native_tool_inspection is None or self.native_tool_primitive is None:
            raise PortError(
                PortErrorCategory.UNSUPPORTED_CAPABILITY,
                "current preparation has no authenticated native tool inspection",
            )
        if primitive.to_bytes() != self.native_tool_primitive.to_bytes():
            raise PortError(
                PortErrorCategory.INTEGRITY,
                "current native tool primitive differs from the producer record",
            )
        if geometry_digest != self.native_tool_inspection.geometry_digest:
            raise PortError(
                PortErrorCategory.INTEGRITY,
                "current native tool geometry differs from the producer record",
            )
        self._validate_native_record(
            self.native_tool_inspection,
            primitive,
            self.native_tool_geometry_digest,
        )
        return self.native_tool_inspection

    def mesh_rigid_primitive(
        self,
        primitive: RigidPrimitive,
        *,
        geometry_digest: str,
        global_size_si: float,
        local_refinements: tuple[BackendLocalRefinement, ...] = (),
    ) -> BackendMesh:
        del primitive, geometry_digest, global_size_si, local_refinements
        raise PortError(
            PortErrorCategory.CONFLICT,
            "current preparation native mesh generation is not permitted during replay",
        )


def _criteria_from_limits(
    limits: dict[str, Any], profile: NumericalProfileRef, kind: str
) -> Any:
    raw = limits.get("_generation_criteria")
    if kind == "box":
        if raw is not None:
            raise ValueError("box preparation must not carry curved generation criteria")
        return None
    if not isinstance(raw, dict):
        raise ValueError("curved preparation requires parent-bound generation criteria")
    expected = {
        "profile",
        "max_boundary_deviation_si",
        "supported_kinds",
        "algorithm_id",
        "evidence_scope",
        "max_elements",
    }
    if set(raw) != expected or not isinstance(raw.get("profile"), dict):
        raise ValueError("generation criteria record is not canonical")
    profile_raw = raw["profile"]
    if set(profile_raw) != {"schema_version", "profile_id", "purpose", "record_digest"}:
        raise ValueError("generation criteria profile is not canonical")
    from febio_cae.adapters.meshing.approximation import ApproximationCriteria, NATIVE_ALGORITHM

    try:
        criteria = ApproximationCriteria(
            profile=NumericalProfileRef(
                profile_raw["profile_id"],
                profile_raw["purpose"],
                profile_raw["record_digest"],
            ),
            max_boundary_deviation=Quantity(float(raw["max_boundary_deviation_si"]), "m"),
            supported_kinds=tuple(raw["supported_kinds"]),
            algorithm_id=raw["algorithm_id"],
            evidence_scope=raw["evidence_scope"],
            max_elements=raw["max_elements"],
        )
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("generation criteria record is invalid") from error
    if (
        canonical_bytes(criteria.to_dict()) != canonical_bytes(raw)
        or criteria.profile != profile
        or criteria.algorithm_id != NATIVE_ALGORITHM
        or kind not in criteria.supported_kinds
        or criteria.max_elements > limits["max_tetrahedra"]
    ):
        raise ValueError("generation criteria binding differs from the registered profile")
    return criteria


def inspection_from_dict(raw: dict[str, Any]) -> BackendInspection:
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
    return BackendInspection(**{**raw, "frame": frame(raw["frame"]), "bodies": bodies})


class _Source:
    def __init__(self, source: SourceAssetContent) -> None:
        self.source = source

    def resolve(self, source_asset: SourceAssetRef) -> SourceAssetContent:
        if source_asset != self.source.source_asset:
            raise ValueError("preparation source identity mismatch")
        return self.source


def produce(
    source: SourceAssetContent, request: dict[str, Any], limits: dict[str, Any]
) -> dict[str, Any]:
    from febio_cae.application.service import (
        _merge_evidence,
        _nested_evidence,
        _registered_selections,
        _resolved_values,
    )

    preliminary = normalize_request(request)
    spec = preliminary.values.to_case_spec()
    if source.source_asset.content_digest != spec.geometry.source_step_digest:
        raise ValueError("preparation STEP source digest mismatch")
    _require_ap214_header(source.content)
    primitive_kind = spec.rigid_tool.primitive.kind
    criteria = _criteria_from_limits(
        limits, spec.mesh_policy.quality_profile, primitive_kind
    )
    backend = _make_backend(limits["cpu_workers"])
    if primitive_kind in {"sphere", "cylinder"}:
        if not all(
            callable(getattr(backend, name, None))
            for name in ("inspect_rigid_primitive", "mesh_rigid_primitive")
        ):
            raise ValueError("curved preparation requires a complete native backend pair")
    elif primitive_kind != "box":
        raise ValueError("preparation supports only box, sphere, or cylinder tools")
    resolve_criteria = None
    if criteria is not None:
        resolve_criteria = lambda profile: criteria if profile == criteria.profile else None
    adapter = StepGeometryMeshAdapter(
        backend,
        source_asset=source.source_asset,
        source_resolver=_Source(source),
        resolve_mesh_quality=resolve_criteria,
    )
    inspection = adapter.inspect(
        GeometryInspectionRequest(source.source_asset, (spec.geometry.body_id.value,)), source
    )
    report = adapter.inspection_details(inspection)
    if (
        backend.evidence.get("gmsh_version") != "4.15.2"
        or backend.evidence.get("occt_version") != "7.8.1"
    ):
        raise ValueError("preparation requires exact current Gmsh/OCCT version evidence")
    parsed = normalize_request(
        request,
        geometry_digest=report.geometry_digest,
        inspection_digest=inspection.inspection_digest,
    )
    spec = parsed.values.to_case_spec()
    if (
        spec.geometry.step_unit != inspection.declared_unit
        or spec.geometry.body_id.value not in inspection.closed_solid_body_ids
    ):
        raise ValueError("explicit STEP unit/body differs from current inspection")
    native_tool_inspection = None
    native_tool_primitive = None
    if primitive_kind in {"sphere", "cylinder"}:
        native_tool_inspection = backend.inspect_rigid_primitive(
            spec.rigid_tool.primitive,
            geometry_digest=spec.rigid_tool.contact_surface.geometry_digest,
        )
        if not isinstance(native_tool_inspection, BackendInspection):
            raise ValueError("native backend returned an invalid primitive inspection record")
        CurrentInspection._validate_native_record(
            native_tool_inspection,
            spec.rigid_tool.primitive,
            spec.rigid_tool.contact_surface.geometry_digest,
        )
        native_tool_primitive = spec.rigid_tool.primitive
    resolutions = {
        selection.to_bytes(): adapter.resolve_placed_selection(
            source, spec.geometry, spec.rigid_tool, selection
        )
        for selection in _registered_selections(parsed.values)
    }
    values = _resolved_values(parsed.values, resolutions)
    carrier = CaseRevision(
        "preparation",
        "generated",
        None,
        None,
        values.to_case_spec(),
        _merge_evidence(parsed.evidence, _nested_evidence(values)),
    )
    mesh = adapter.mesh(carrier)
    if len(mesh.nodes) > limits["max_nodes"] or len(mesh.elements) > limits["max_tetrahedra"]:
        raise ValueError("preparation mesh exceeds explicit node/element limits")
    return {
        "carrier": carrier.to_dict(),
        "mesh": mesh.to_dict(),
        "inspection": report.to_dict(),
        "generation_criteria": None if criteria is None else criteria.to_dict(),
        "native_tool_inspection": None
        if native_tool_inspection is None
        else native_tool_inspection.to_dict(),
        "native_tool_primitive": None
        if native_tool_primitive is None
        else native_tool_primitive.to_dict(),
        "backend": backend.evidence,
        "backend_id": backend.backend_id,
        "backend_version": backend.backend_version,
        "mesh_generations": 1,
    }


def run_preparation(
    source: SourceAssetContent, request: dict[str, Any], limits: dict[str, Any], directory: Path
) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=False)
    payload = {
        "source": source.source_asset.to_dict(),
        "content_hex": source.content.hex(),
        "request": request,
        "limits": limits,
    }
    (directory / "input.json").write_bytes(canonical_bytes(payload))
    package_root = Path(__file__).resolve().parents[3]
    bootstrap = f"import sys; sys.path.insert(0, {str(package_root)!r}); from febio_cae.adapters.geometry.preparation import _main; _main()"
    argv = (sys.executable, "-I", "-c", bootstrap)
    process = _run_owned(
        argv, directory, timeout_seconds=limits["wall_seconds"], memory_bytes=limits["memory_bytes"]
    )
    result_file = directory / "output.json"
    if result_file.stat().st_size > limits["memory_bytes"]:
        raise ValueError("preparation output exceeds memory budget")
    result = json.loads(result_file.read_bytes())
    if not isinstance(result, dict):
        raise TypeError("invalid producer output")
    result["process"] = {**process, "argv": list(argv)}
    return result


def _main() -> None:
    payload = json.loads(Path("input.json").read_bytes())
    source_ref = decode_record(canonical_bytes(payload["source"]), SourceAssetRef)
    source = SourceAssetContent(source_ref, bytes.fromhex(payload["content_hex"]))
    try:
        result = produce(source, payload["request"], payload["limits"])
        Path("output.json").write_bytes(canonical_bytes(result))
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(1) from error
