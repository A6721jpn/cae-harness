"""Private current-source inspection child; observation, never preparation admission."""

from __future__ import annotations

import hashlib
import json
import stat
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from febio_cae.domain import GeometryInspectionRequest, SourceAssetContent, SourceAssetRef
from febio_cae.domain.canonical import canonical_bytes
from febio_cae.domain.codec import decode_record
from febio_cae.domain.ports import PortError, PortErrorCategory

from ._gmsh_runtime import capture_runtime_binding, load_verified_gmsh, verify_runtime_identity
from .adapter import StepGeometryMeshAdapter
from .backend import BackendInspection, GeometryMeshBackend
from .gmsh_occ import GmshOCCBackend, GmshOCCConfig, _require_ap214_header
from .preparation import CurrentInspection, _run_owned, inspection_from_dict


def remaining(deadline: float) -> float:
    value = deadline - time.monotonic()
    if value <= 0:
        raise TimeoutError("native inspection operation deadline exceeded")
    return value


class _ObservedGmsh(GmshOCCBackend):
    """Measure only; all admission and geometry operations stay in the existing backend."""

    def __init__(self, cpu: int, runtime_binding: dict[str, object] | None = None) -> None:
        super().__init__(
            GmshOCCConfig(expected_occt_version="7.8.1", require_step_ap214=True, cpu_workers=cpu)
        )
        self.evidence: dict[str, Any] = {}
        self._runtime_binding = runtime_binding
        self._runtime_identity: dict[str, object] | None = None

    def _load_module(self) -> Any:
        if self._runtime_binding is None:
            return super()._load_module()
        module, identity = load_verified_gmsh(self._runtime_binding)
        self._runtime_identity = identity
        return module

    def _prepare_owned_session(self, gmsh: Any) -> None:
        super()._prepare_owned_session(gmsh)
        if self._runtime_identity is not None:
            module_record = self._runtime_identity["module"]
            if not isinstance(module_record, dict) or not isinstance(
                module_record.get("path"), str
            ):
                raise ValueError("verified Gmsh module identity is malformed")
            module = Path(module_record["path"])
            module_sha256 = module_record.get("sha256")
            if not isinstance(module_sha256, str):
                raise ValueError("verified Gmsh module digest is malformed")
            runtime_identity = self._runtime_identity
        else:
            module = Path(gmsh.__file__).resolve(strict=True)
            module_sha256 = hashlib.sha256(module.read_bytes()).hexdigest()
            runtime_identity = None
        self.evidence = {
            "module": str(module),
            "module_sha256": module_sha256,
            "gmsh_version": gmsh.__version__,
            "occt_version": "7.8.1",
            "build_info": gmsh.option.getString("General.BuildInfo"),
        }
        if runtime_identity is not None:
            self.evidence["runtime_identity"] = runtime_identity


def _make_backend(cpu: int, runtime_binding: dict[str, object] | None = None) -> _ObservedGmsh:
    return _ObservedGmsh(cpu, runtime_binding)


def _identity(value: Any, expected_runtime: object | None = None) -> dict[str, Any]:
    fields = {"module", "module_sha256", "gmsh_version", "occt_version", "build_info"}
    expected_fields = fields | {"runtime_identity"} if expected_runtime is not None else fields
    if (
        not isinstance(value, dict)
        or set(value) != expected_fields
        or any(
            not isinstance(value[field], str)
            or not value[field]
            or "\x00" in value[field]
            for field in fields
        )
    ):
        raise ValueError("invalid measured inspection backend identity")
    digest = value["module_sha256"]
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("invalid measured inspection module digest")
    versions = [
        field.partition(":")[2].strip()
        for field in value["build_info"].split(";")
        if field.partition(":")[0].strip() == "OCC version"
    ]
    if (
        value["gmsh_version"] != "4.15.2"
        or value["occt_version"] != "7.8.1"
        or versions != ["7.8.1"]
    ):
        raise PortError(
            PortErrorCategory.UNSUPPORTED_CAPABILITY, "inspection Gmsh/OCCT evidence mismatch"
        )
    result = dict(value)
    if expected_runtime is not None:
        verify_runtime_identity(expected_runtime, result["runtime_identity"])
    return result


def produce(
    source: SourceAssetContent,
    limits: dict[str, Any],
    runtime_binding: dict[str, object] | None = None,
) -> dict[str, Any]:
    _require_ap214_header(source.content)
    backend = (
        _make_backend(limits["cpu_workers"], runtime_binding)
        if runtime_binding is not None
        else _make_backend(limits["cpu_workers"])
    )
    adapter = StepGeometryMeshAdapter(
        cast(GeometryMeshBackend, backend), source_asset=source.source_asset
    )
    geometry = adapter.inspect(GeometryInspectionRequest(source.source_asset), source)
    return {
        "status": "INSPECTED",
        "inspection": adapter.inspection_details(geometry).to_dict(),
        "geometry": geometry.to_dict(),
        "backend": _identity(backend.evidence, runtime_binding),
    }


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate inspection JSON key")
        result[key] = value
    return result


def _constant(value: str) -> Any:
    raise ValueError("non-finite inspection JSON value")


def _json(content: bytes) -> Any:
    return json.loads(content, object_pairs_hook=_pairs, parse_constant=_constant)


def _topology(raw: Any, cap: int) -> BackendInspection:
    if not isinstance(raw, dict) or not isinstance(raw.get("bodies"), list):
        raise TypeError("invalid inspection topology shape")
    bodies = raw["bodies"]
    # Every list member occupies at least one byte; count admission remains tied to byte cap.
    if not bodies or len(bodies) > cap:
        raise ValueError("invalid inspection body count")
    face_count = 0
    for body in bodies:
        if not isinstance(body, dict) or type(body.get("closed_solid")) is not bool:
            raise ValueError("invalid inspection body shape")
        faces = body.get("faces")
        if not isinstance(faces, list):
            raise TypeError("invalid inspection face list")
        face_count += len(faces)
        if face_count > cap:
            raise ValueError("invalid inspection face count")
    report = inspection_from_dict(raw)
    if canonical_bytes(report.to_dict()) != canonical_bytes(raw):
        raise ValueError("inspection topology is not its exact validated projection")
    return report


def _response(
    raw: Any,
    source: SourceAssetContent,
    cap: int,
    expected_runtime: object | None = None,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise TypeError("invalid inspection response")
    if raw.get("status") == "ERROR":
        if set(raw) != {"status", "category", "message"}:
            raise ValueError("invalid inspection error response")
        category = PortErrorCategory(raw["category"])
        if category not in {
            PortErrorCategory.INVALID_INPUT,
            PortErrorCategory.UNSUPPORTED_CAPABILITY,
            PortErrorCategory.ENVIRONMENT,
            PortErrorCategory.INTEGRITY,
        }:
            raise ValueError("invalid inspection child error category")
        raise PortError(category, raw["message"])
    if set(raw) != {"status", "inspection", "geometry", "backend"} or raw["status"] != "INSPECTED":
        raise ValueError("invalid successful inspection response")
    backend = _identity(raw["backend"], expected_runtime)
    report = _topology(raw["inspection"], cap)
    if report.source_digest != source.source_asset.content_digest:
        raise ValueError("inspection topology source digest differs from registered source")
    adapter = StepGeometryMeshAdapter(
        CurrentInspection(report, "gmsh-occ", "4.15.2"), source_asset=source.source_asset
    )
    geometry = adapter.inspect(GeometryInspectionRequest(source.source_asset), source)
    if geometry.to_bytes() != canonical_bytes(raw["geometry"]):
        raise ValueError("inspection geometry identity/digest differs from topology")
    return {"geometry": geometry.to_dict(), "topology": report.to_dict(), "backend": backend}


def run_inspection(
    source: SourceAssetContent,
    limits: dict[str, Any],
    directory: Path,
    deadline: float,
    before_launch: Callable[[], None],
) -> dict[str, Any]:
    remaining(deadline)
    runtime_binding = capture_runtime_binding()
    payload = {
        "source": source.source_asset.to_dict(),
        "content_hex": source.content.hex(),
        "limits": limits,
        "runtime_binding": runtime_binding,
    }
    (directory / "input.json").write_bytes(canonical_bytes(payload))
    package_root = Path(__file__).resolve().parents[3]
    bootstrap = (
        f"import sys; sys.path.insert(0, {str(package_root)!r}); "
        "from febio_cae.adapters.geometry.inspection import _main; _main()"
    )
    argv = (sys.executable, "-I", "-c", bootstrap)
    before_launch()
    process = _run_owned(
        argv,
        directory,
        cpu_workers=limits["cpu_workers"],
        timeout_seconds=remaining(deadline),
        memory_bytes=limits["memory_bytes"],
    )
    remaining(deadline)
    cap = limits["max_response_bytes"]
    try:
        output = directory / "output.json"
        info = output.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ValueError("inspection response is not a private regular file")
        if info.st_size > cap:
            raise ValueError("inspection response exceeds finite byte limit")
        with output.open("rb") as stream:
            content = stream.read(cap + 1)
        if len(content) > cap:
            raise ValueError("inspection response grew beyond finite byte limit")
        remaining(deadline)
        result = _response(_json(content), source, cap, runtime_binding)
    except (OSError, ValueError, TypeError, KeyError, RecursionError, OverflowError) as error:
        if isinstance(error, TimeoutError):
            raise
        raise PortError(
            PortErrorCategory.INTEGRITY, f"invalid inspection response: {error}"
        ) from error
    remaining(deadline)
    result["process"] = {**process, "argv": list(argv)}
    result["runtime_binding"] = runtime_binding
    return result


def _main() -> None:
    from .backend import BackendError

    try:
        payload = _json(Path("input.json").read_bytes())
        if not isinstance(payload, dict) or set(payload) != {
            "source",
            "content_hex",
            "limits",
            "runtime_binding",
        }:
            raise ValueError("invalid private inspection input")
        source_ref = decode_record(canonical_bytes(payload["source"]), SourceAssetRef)
        source = SourceAssetContent(source_ref, bytes.fromhex(payload["content_hex"]))
        runtime_binding = payload["runtime_binding"]
        if not isinstance(runtime_binding, dict):
            raise ValueError("inspection runtime binding is missing")
        result = produce(source, payload["limits"], runtime_binding)
    except (PortError, BackendError) as error:
        result = {"status": "ERROR", "category": error.category.value, "message": str(error)}
    except (OSError, RuntimeError) as error:
        result = {"status": "ERROR", "category": "environment", "message": str(error)}
    except (ValueError, TypeError, KeyError) as error:
        result = {"status": "ERROR", "category": "integrity", "message": str(error)}
    Path("output.json").write_bytes(canonical_bytes(result))
