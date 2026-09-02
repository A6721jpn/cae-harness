from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib
import os
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest

from febio_cae_harness.solver import validate_requested_fields


def _module() -> ModuleType:
    return importlib.import_module("febio_cae_harness.solver.official_fbs")


def _embedded_helper_namespace() -> dict[str, Any]:
    source = _module()._HELPER_SOURCE
    namespace: dict[str, Any] = {"__name__": "official_fbs_helper_test"}
    exec(compile(source.rsplit("\nmain()\n", 1)[0], "<official-fbs-helper>", "exec"), namespace)
    return namespace


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _normalized_pressure_result(module: ModuleType, xplt_sha256: str) -> dict[str, object]:
    model_manifest = {
        "available_fields": [{"index": 0, "name": "pressure"}],
        "element_count": 1,
        "node_count": 4,
        "requested_fields": {
            "pressure": {
                "association": "CELL_DATA",
                "component_index": 0,
                "component_name": "pressure",
                "components": 1,
                "field_index": 0,
                "tensor_type": "DATA_SCALAR",
                "vtk_name": "pressure",
            }
        },
        "state_count": 1,
        "state_times": [0.0],
    }
    return {
        "protocol": 1,
        "available_fields": ["pressure"],
        "model_manifest": model_manifest,
        "model_sha256": module._model_manifest_sha256(model_manifest),
        "values": {
            "pressure": {
                "components": 1,
                "count": 1,
                "entity_count": 1,
                "field_index": 0,
                "minimum": 0.0,
                "maximum": 1.0,
                "state_count": 1,
            }
        },
        "non_finite_fields": [],
        "xplt_sha256": xplt_sha256,
    }


def _tet4_geometry_summary() -> dict[str, object]:
    return {
        "topology_sha256": "f" * 64,
        "node_count": 4,
        "element_count": 1,
        "cell_types": [
            {
                "vtk_id": 10,
                "name": "tet4",
                "nodes": 4,
                "elements": 1,
                "integration_rule": "gauss1",
                "integration_points": 1,
            }
        ],
        "states": [
            {
                "index": 0,
                "time": 0.0,
                "integration_point_count": 1,
                "minimum_jacobian": 1.0,
                "maximum_jacobian": 1.0,
                "minimum_element_index": 0,
                "minimum_vtk_id": 10,
                "minimum_integration_point_index": 0,
            }
        ],
    }


def _runtime_files(tmp_path: Path) -> tuple[dict[str, Path], dict[str, str]]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    python_dir = tmp_path / "python313"
    python_dir.mkdir()
    module_dir = tmp_path / "module"
    module_dir.mkdir()
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    payloads = {
        "python_executable": b"fixture-python-executable",
        "python_dll": b"fixture-python-dll",
        "python_stdlib": b"fixture-python-stdlib",
        "python_path_config": b"fixture-python-path-config",
        "fbs_module": b"fixture-official-fbs-module",
        "zlib": b"fixture-zlib",
    }
    paths = {
        "python_executable": python_dir / "python.exe",
        "python_dll": python_dir / "python313.dll",
        "python_stdlib": python_dir / "python313.zip",
        "python_path_config": python_dir / "python313._pth",
        "fbs_module": module_dir / "fbs.cp313-win_amd64.pyd",
        "zlib": runtime_dir / "zlib1.dll",
    }
    for name, path in paths.items():
        path.write_bytes(payloads[name])
    return paths, {name: _sha256(payload) for name, payload in payloads.items()}


def _probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[ModuleType, Any, dict[str, Path]]:
    module = _module()
    paths, hashes = _runtime_files(tmp_path)
    monkeypatch.setattr(module, "_APPROVED_SHA256", hashes)
    monkeypatch.setattr(
        module,
        "_APPROVED_PYTHON_TREE",
        {path.name: hashes[name] for name, path in paths.items() if name.startswith("python_")},
    )
    monkeypatch.setattr(
        module,
        "_probe_helper",
        lambda held, timeout_seconds: module._ProbeHelperResult(
            {
                "protocol": 1,
                "python": "3.13",
                "module": "fbs",
                "api": ["ReadPlotFile", "vtkExport"],
            },
            "a" * 64,
        ),
    )
    runtime = module.probe_official_fbs_runtime(
        paths["python_executable"],
        paths["fbs_module"],
        paths["zlib"],
    )
    return module, runtime, paths


def test_probe_issues_exact_pinned_official_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, runtime, paths = _probe(tmp_path, monkeypatch)

    assert runtime.python_executable == paths["python_executable"].resolve()
    assert runtime.fbs_module == paths["fbs_module"].resolve()
    assert runtime.zlib == paths["zlib"].resolve()
    assert runtime.fbs_module_sha256 == module._APPROVED_SHA256["fbs_module"]
    assert runtime.runtime_identity.startswith("official-fbs-3.1-cp313:")
    assert module.validate_official_fbs_runtime(runtime) is runtime


def test_probe_rejects_unapproved_module_before_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    paths, hashes = _runtime_files(tmp_path)
    hashes["fbs_module"] = "0" * 64
    monkeypatch.setattr(module, "_APPROVED_SHA256", hashes)
    monkeypatch.setattr(
        module,
        "_APPROVED_PYTHON_TREE",
        {path.name: hashes[name] for name, path in paths.items() if name.startswith("python_")},
    )
    called = False

    def forbidden_probe(held: object, timeout_seconds: float) -> Mapping[str, object]:
        nonlocal called
        del held, timeout_seconds
        called = True
        return {}

    monkeypatch.setattr(module, "_probe_helper", forbidden_probe)

    with pytest.raises(module.OfficialFbsRuntimeError, match="fbs_module.*SHA-256"):
        module.probe_official_fbs_runtime(
            paths["python_executable"],
            paths["fbs_module"],
            paths["zlib"],
        )
    assert called is False


def test_official_manager_uses_only_live_probe_and_fails_closed_on_bad_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, runtime, _ = _probe(tmp_path / "runtime-fixture", monkeypatch)
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    xplt = attempt / "result.xplt"
    xplt.write_bytes(b"synthetic-xplt")
    model_manifest = {
        "available_fields": [{"index": 0, "name": "stress"}],
        "element_count": 1,
        "node_count": 4,
        "requested_fields": {
            "stress": {
                "association": "CELL_DATA",
                "component_index": 0,
                "component_name": "stress",
                "components": 9,
                "field_index": 0,
                "tensor_type": "DATA_TENSOR2",
                "vtk_name": "stress",
            }
        },
        "state_count": 1,
        "state_times": [0.0],
    }
    model_sha256 = module._model_manifest_sha256(model_manifest)
    xplt_sha256 = _sha256(b"synthetic-xplt")
    responses: list[Mapping[str, object]] = [
        {
            "protocol": 1,
            "available_fields": ["stress"],
            "model_manifest": model_manifest,
            "model_sha256": model_sha256,
            "values": {
                "stress": {
                    "components": 9,
                    "count": 9,
                    "entity_count": 1,
                    "field_index": 0,
                    "minimum": -1.0,
                    "maximum": 2.0,
                    "state_count": 1,
                }
            },
            "non_finite_fields": [],
            "xplt_sha256": xplt_sha256,
        },
        {
            "protocol": 1,
            "available_fields": ["stress"],
            "model_manifest": model_manifest,
            "model_sha256": model_sha256,
            "values": {},
            "non_finite_fields": ["stress"],
            "xplt_sha256": xplt_sha256,
        },
    ]
    monkeypatch.setattr(
        module,
        "_invoke_helper",
        lambda checked, path, fields, root: responses.pop(0),
    )

    manager = module.open_official_fbs_manager(runtime, attempt)
    with manager:
        authority = manager.issue_authority()
        valid = validate_requested_fields(authority, xplt, ("stress",))
        non_finite = validate_requested_fields(authority, xplt, ("stress",))

        assert valid.valid
        assert valid.official is True
        assert valid.provenance == "official"
        assert valid.values["stress"] == {
            "components": 9,
            "count": 9,
            "entity_count": 1,
            "field_index": 0,
            "minimum": -1.0,
            "maximum": 2.0,
            "state_count": 1,
        }
        assert non_finite.valid is False
        assert non_finite.non_finite_fields == ("stress",)


def test_official_fbs_receipt_retains_model_manifest_and_private_pipe_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, runtime, _ = _probe(tmp_path / "runtime-fixture", monkeypatch)
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    xplt = attempt / "result.xplt"
    xplt.write_bytes(b"synthetic-xplt")
    model_manifest = {
        "available_fields": [{"index": 0, "name": "stress"}],
        "element_count": 1,
        "node_count": 4,
        "requested_fields": {
            "stress": {
                "association": "CELL_DATA",
                "component_index": 0,
                "component_name": "stress",
                "components": 9,
                "field_index": 0,
                "tensor_type": "DATA_TENSOR2",
                "vtk_name": "stress",
            }
        },
        "state_count": 2,
        "state_times": [0.0, 1.0],
    }
    model_sha256 = module._model_manifest_sha256(model_manifest)
    xplt_sha256 = _sha256(b"synthetic-xplt")
    monkeypatch.setattr(
        module,
        "_invoke_helper",
        lambda checked, path, fields, root: {
            "protocol": 1,
            "available_fields": ["stress"],
            "model_manifest": model_manifest,
            "model_sha256": model_sha256,
            "values": {
                "stress": {
                    "components": 9,
                    "count": 18,
                    "entity_count": 1,
                    "field_index": 0,
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "state_count": 2,
                }
            },
            "non_finite_fields": [],
            "xplt_sha256": xplt_sha256,
        },
    )

    manager = module.open_official_fbs_manager(runtime, attempt)
    with manager:
        validation = validate_requested_fields(manager.issue_authority(), xplt, ("stress",))
        receipt = module.require_official_fbs_result(validation)

        assert receipt.validation is validation
        assert receipt.xplt_path == xplt.resolve()
        assert receipt.xplt_sha256 == xplt_sha256
        assert receipt.model_manifest_sha256 == model_sha256
        assert receipt.node_count == 4
        assert receipt.element_count == 1
        assert receipt.state_count == 2
        assert receipt.state_times == (0.0, 1.0)
        assert receipt.requested_fields == ("stress",)
        assert receipt.transport == "private-named-pipe"
        assert receipt.runtime_identity == runtime.runtime_identity
        with pytest.raises(TypeError):
            module.OfficialFbsResultReceipt()

    with pytest.raises(TypeError, match="closed|authority|unavailable"):
        _ = receipt.node_count


def test_official_fbs_receipt_retains_validated_geometry_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, runtime, _ = _probe(tmp_path / "runtime-fixture", monkeypatch)
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    xplt = attempt / "result.xplt"
    xplt.write_bytes(b"synthetic-xplt")
    response = _normalized_pressure_result(module, _sha256(b"synthetic-xplt"))
    response["geometry"] = _tet4_geometry_summary()
    monkeypatch.setattr(
        module,
        "_invoke_helper",
        lambda checked, path, fields, root: response,
    )

    with module.open_official_fbs_manager(runtime, attempt) as manager:
        validation = validate_requested_fields(manager.issue_authority(), xplt, ("pressure",))
        receipt = module.require_official_fbs_result(validation)
        geometry = receipt.geometry_summary

        assert geometry is not None
        assert geometry["topology_sha256"] == "f" * 64
        assert geometry["node_count"] == 4
        assert geometry["element_count"] == 1
        assert geometry["cell_types"][0]["name"] == "tet4"
        assert geometry["cell_types"][0]["integration_rule"] == "gauss1"
        assert geometry["states"][0]["minimum_jacobian"] == 1.0


@pytest.mark.parametrize(
    ("target", "value"),
    [
        ("node_count", 5),
        ("vtk_id", 12),
        ("integration_rule", "gauss4"),
        ("time", 1.0),
        ("integration_point_count", 2),
        ("minimum_vtk_id", 24),
        ("minimum_jacobian", float("nan")),
        ("maximum_jacobian", 0.5),
    ],
)
def test_official_fbs_geometry_summary_rejects_unbound_or_invalid_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    value: object,
) -> None:
    module, _, _ = _probe(tmp_path / "runtime-fixture", monkeypatch)
    response = _normalized_pressure_result(module, "a" * 64)
    geometry = _tet4_geometry_summary()
    if target in geometry:
        geometry[target] = value
    elif target in {"vtk_id", "integration_rule"}:
        cast(list[dict[str, object]], geometry["cell_types"])[0][target] = value
    else:
        cast(list[dict[str, object]], geometry["states"])[0][target] = value
    response["geometry"] = geometry

    with pytest.raises(module.OfficialFbsRuntimeError):
        module._validate_normalized_result(response, ("pressure",))


@pytest.mark.parametrize("replacement", [False, True], ids=["mutate", "replace"])
def test_official_fbs_receipt_keeps_exact_xplt_live_until_manager_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement: bool,
) -> None:
    module, runtime, _ = _probe(tmp_path / "runtime-fixture", monkeypatch)
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    xplt = attempt / "result.xplt"
    xplt.write_bytes(b"synthetic-xplt")
    response = _normalized_pressure_result(module, _sha256(b"synthetic-xplt"))
    monkeypatch.setattr(
        module,
        "_invoke_helper",
        lambda checked, path, fields, root: response,
    )

    with module.open_official_fbs_manager(runtime, attempt) as manager:
        validation = validate_requested_fields(manager.issue_authority(), xplt, ("pressure",))
        receipt = module.require_official_fbs_result(validation)

        blocked = False
        try:
            if replacement:
                candidate = attempt / "replacement.xplt"
                candidate.write_bytes(b"synthetic-xplt")
                os.replace(candidate, xplt)
            else:
                xplt.write_bytes(b"mutated-xplt")
        except PermissionError:
            blocked = True

        if blocked:
            assert receipt.node_count == 4
        else:
            with pytest.raises(TypeError, match="XPLT|binding|unavailable"):
                _ = receipt.node_count


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("component_index", False),
        ("components", True),
        ("field_index", False),
    ],
)
def test_official_fbs_model_manifest_rejects_boolean_integer_fields(
    name: str,
    value: bool,
) -> None:
    module = _module()
    response = _normalized_pressure_result(module, "a" * 64)
    model = cast(dict[str, Any], response["model_manifest"])
    requested = cast(dict[str, dict[str, object]], model["requested_fields"])
    requested["pressure"][name] = value

    with pytest.raises(module.OfficialFbsRuntimeError, match="field identity"):
        module._validate_model_manifest(model, ("pressure",))


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("components", True),
        ("count", True),
        ("entity_count", True),
        ("field_index", False),
        ("state_count", True),
    ],
)
def test_official_fbs_result_rejects_boolean_summary_cardinality(
    name: str,
    value: bool,
) -> None:
    module = _module()
    response = _normalized_pressure_result(module, "a" * 64)
    mutated = copy.deepcopy(response)
    values = cast(dict[str, dict[str, object]], mutated["values"])
    values["pressure"][name] = value

    with pytest.raises(module.OfficialFbsRuntimeError, match="summary"):
        module._validate_normalized_result(mutated, ("pressure",))


def test_runtime_mutation_after_probe_prevents_official_manager(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, runtime, paths = _probe(tmp_path, monkeypatch)
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    paths["fbs_module"].write_bytes(b"replacement")

    with pytest.raises(module.OfficialFbsRuntimeError, match="changed after probe"):
        module.open_official_fbs_manager(runtime, attempt)


def test_helper_json_rejects_duplicate_keys_and_excessive_nesting() -> None:
    module = _module()

    with pytest.raises(module.OfficialFbsRuntimeError, match="invalid JSON"):
        module._json_object(b'{"protocol":1,"protocol":2}')
    nested = b"[" * 80 + b"0" + b"]" * 80
    with pytest.raises(module.OfficialFbsRuntimeError, match="nested"):
        module._json_object(b'{"value":' + nested + b"}")


def test_field_response_must_echo_exact_request_binding() -> None:
    module = _module()
    binding = module._RequestBinding(
        nonce="a" * 64,
        request_sha256="b" * 64,
        xplt_sha256="c" * 64,
    )
    response = {
        "protocol": 1,
        "profile": "official-fbs-3.1-cp313",
        "nonce": "d" * 64,
        "request_sha256": "b" * 64,
        "xplt_sha256": "c" * 64,
        "available_fields": ["stress"],
        "field_data": {},
        "model_sha256": "e" * 64,
    }

    with pytest.raises(module.OfficialFbsRuntimeError, match="binding"):
        module._validate_field_response(response, ("stress",), binding)


def test_field_response_binds_fbs_field_id_and_model_derived_cardinality() -> None:
    module = _module()
    model_manifest: dict[str, Any] = {
        "available_fields": [
            {"index": 0, "name": "displacement"},
            {"index": 1, "name": "stress"},
        ],
        "element_count": 1,
        "node_count": 4,
        "requested_fields": {
            "stress": {
                "association": "CELL_DATA",
                "component_index": 0,
                "component_name": "stress",
                "components": 9,
                "field_index": 1,
                "tensor_type": "DATA_TENSOR2",
                "vtk_name": "stress",
            }
        },
        "state_count": 2,
        "state_times": [0.0, 1.0],
    }
    model_sha256 = module._model_manifest_sha256(model_manifest)
    binding = module._RequestBinding(
        nonce="a" * 64,
        request_sha256="b" * 64,
        xplt_sha256="c" * 64,
        model_sha256=model_sha256,
        model_manifest=model_manifest,
    )
    response: dict[str, Any] = {
        "protocol": 1,
        "profile": "official-fbs-3.1-cp313",
        "nonce": "a" * 64,
        "request_sha256": "b" * 64,
        "xplt_sha256": "c" * 64,
        "model_sha256": model_sha256,
        "available_fields": ["displacement", "stress"],
        "field_data": {
            "stress": {
                "association": "CELL_DATA",
                "components": 9,
                "field_index": 1,
                "states": [
                    {"entity_count": 1, "index": 0, "time": 0.0, "values": [0.0] * 9},
                    {"entity_count": 1, "index": 1, "time": 1.0, "values": [1.0] * 9},
                ],
                "vtk_name": "stress",
            }
        },
    }
    geometry = _tet4_geometry_summary()
    second_state = copy.deepcopy(cast(list[dict[str, object]], geometry["states"])[0])
    second_state["index"] = 1
    second_state["time"] = 1.0
    cast(list[dict[str, object]], geometry["states"]).append(second_state)
    response["geometry"] = geometry

    validated = module._validate_field_response(response, ("stress",), binding)
    assert validated["values"]["stress"]["entity_count"] == 1
    assert validated["geometry"] == geometry
    response["field_data"]["stress"]["states"][1]["entity_count"] = 2
    response["field_data"]["stress"]["states"][1]["values"] = [1.0] * 18
    with pytest.raises(module.OfficialFbsRuntimeError, match="cardinality"):
        module._validate_field_response(response, ("stress",), binding)


def test_invoke_helper_never_authorizes_filesystem_vtk_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    owner = object()
    xplt_entry = module._HeldEntry("XPLT", tmp_path / "attempt.xplt", owner, "c" * 64)
    runtime = SimpleNamespace(
        python_executable=tmp_path / "python.exe",
        fbs_module=tmp_path / "fbs.pyd",
        zlib=tmp_path / "zlib1.dll",
        module_attestation_sha256="f" * 64,
    )
    calls = 0

    @contextlib.contextmanager
    def hold_runtime(*_args: object) -> Iterator[object]:
        yield object()

    class ReadPhaseReached(Exception):
        pass

    def run_helper(
        _held: object,
        request: Mapping[str, object],
        _root: Path,
        _timeout: float,
        **kwargs: object,
    ) -> Mapping[str, object]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "model": {
                    "available_fields": [],
                    "element_count": 1,
                    "node_count": 1,
                    "requested_fields": {},
                    "state_count": 1,
                    "state_times": [0.0],
                },
                "nonce": request["nonce"],
                "profile": request["profile"],
                "protocol": request["protocol"],
                "request_sha256": request["request_sha256"],
                "xplt_sha256": request["xplt_sha256"],
            }
        assert "output_names" not in request
        assert "mutable_outputs" not in kwargs
        raise ReadPhaseReached

    monkeypatch.setattr(module, "validate_official_fbs_runtime", lambda value: value)
    monkeypatch.setattr(module, "_open_file", lambda *_args: xplt_entry)
    monkeypatch.setattr(module, "_hold_runtime", hold_runtime)
    monkeypatch.setattr(module, "_run_helper", run_helper)
    monkeypatch.setattr(module._fbs, "_close_owned_handle", lambda candidate: None)

    with pytest.raises(ReadPhaseReached):
        module._invoke_helper(runtime, xplt_entry.path, ("stress",), tmp_path)

    assert calls == 2


def test_bounded_pipe_reader_drains_oversized_response_without_retaining_it() -> None:
    module = _module()
    read_fd, write_fd = os.pipe()
    stream = os.fdopen(read_fd, "rb", buffering=0)
    payload = b"x" * 8192

    def write_payload() -> None:
        with os.fdopen(write_fd, "wb", buffering=0) as writer:
            writer.write(payload)

    writer = threading.Thread(target=write_payload)
    reader = module._BoundedPipeReader(stream, 64, "response")
    reader.start()
    writer.start()
    try:
        with pytest.raises(module.OfficialFbsRuntimeError, match="oversized"):
            reader.finish(time.monotonic() + 2.0)
    finally:
        writer.join(2.0)
        stream.close()

    assert not writer.is_alive()
    assert len(reader.captured) <= 64


def test_bounded_pipe_reader_rejects_long_stderr_handshake_line() -> None:
    module = _module()
    read_fd, write_fd = os.pipe()
    stream = os.fdopen(read_fd, "rb", buffering=0)

    def write_payload() -> None:
        with os.fdopen(write_fd, "wb", buffering=0) as writer:
            writer.write(b"A" * 8192 + b"\n")

    writer = threading.Thread(target=write_payload)
    reader = module._BoundedPipeReader(stream, 64, "stderr")
    reader.start()
    writer.start()
    try:
        with pytest.raises(module.OfficialFbsRuntimeError, match="oversized"):
            reader.wait_line(0, time.monotonic() + 2.0)
    finally:
        writer.join(2.0)
        stream.close()

    assert not writer.is_alive()
    assert len(reader.captured) <= 64


def test_embedded_helper_bounds_total_vtk_payload_across_all_states() -> None:
    source = _module()._HELPER_SOURCE

    assert "MAX_TOTAL_VTK_BYTES = 512 * 1024 * 1024" in source
    assert "remaining = MAX_TOTAL_VTK_BYTES - total_bytes" in source


def test_embedded_helper_summarizes_all_default_tet4_integration_points() -> None:
    namespace = _embedded_helper_namespace()
    vtk = b"""# vtk DataFile Version 3.0
synthetic tet4
ASCII
DATASET UNSTRUCTURED_GRID
POINTS 4 float
0 0 0
1 0 0
0 1 0
0 0 1
CELLS 1 5
4 0 1 2 3
CELL_TYPES 1
10
CELL_DATA 1
SCALARS pressure float
LOOKUP_TABLE default
0
"""

    parsed = namespace["parse_vtk"](vtk)
    geometry = namespace["summarize_geometry"]([parsed], [0.0])

    assert parsed["arrays"]["pressure"][:3] == ("CELL_DATA", 1, 1)
    assert geometry["node_count"] == 4
    assert geometry["element_count"] == 1
    assert geometry["cell_types"] == [
        {
            "vtk_id": 10,
            "name": "tet4",
            "nodes": 4,
            "elements": 1,
            "integration_rule": "gauss1",
            "integration_points": 1,
        }
    ]
    assert geometry["states"] == [
        {
            "index": 0,
            "time": 0.0,
            "integration_point_count": 1,
            "minimum_jacobian": 1.0,
            "maximum_jacobian": 1.0,
            "minimum_element_index": 0,
            "minimum_vtk_id": 10,
            "minimum_integration_point_index": 0,
        }
    ]
    assert len(geometry["topology_sha256"]) == 64


def test_embedded_helper_summarizes_all_default_tet10_integration_points() -> None:
    namespace = _embedded_helper_namespace()
    vtk = b"""# vtk DataFile Version 3.0
synthetic tet10
ASCII
DATASET UNSTRUCTURED_GRID
POINTS 10 float
0 0 0
1 0 0
0 1 0
0 0 1
0.5 0 0
0.5 0.5 0
0 0.5 0
0 0 0.5
0.5 0 0.5
0 0.5 0.5
CELLS 1 11
10 0 1 2 3 4 5 6 7 8 9
CELL_TYPES 1
24
CELL_DATA 1
SCALARS pressure float
LOOKUP_TABLE default
0
"""

    geometry = namespace["summarize_geometry"]([namespace["parse_vtk"](vtk)], [0.0])

    assert geometry["cell_types"][0] == {
        "vtk_id": 24,
        "name": "tet10",
        "nodes": 10,
        "elements": 1,
        "integration_rule": "gauss4",
        "integration_points": 4,
    }
    assert geometry["states"][0]["integration_point_count"] == 4
    assert geometry["states"][0]["minimum_jacobian"] == pytest.approx(1.0)
    assert geometry["states"][0]["maximum_jacobian"] == pytest.approx(1.0)


def test_embedded_helper_reports_negative_jacobian_and_rejects_topology_change() -> None:
    namespace = _embedded_helper_namespace()
    vtk = b"""# vtk DataFile Version 3.0
synthetic tet4
ASCII
DATASET UNSTRUCTURED_GRID
POINTS 4 float
0 0 0
1 0 0
0 1 0
0 0 1
CELLS 1 5
4 0 1 2 3
CELL_TYPES 1
10
CELL_DATA 1
SCALARS pressure float
LOOKUP_TABLE default
0
"""
    initial = namespace["parse_vtk"](vtk)
    inverted = copy.deepcopy(initial)
    inverted["points"][1], inverted["points"][2] = (
        inverted["points"][2],
        inverted["points"][1],
    )

    geometry = namespace["summarize_geometry"]([initial, inverted], [0.0, 1.0])

    assert geometry["states"][0]["minimum_jacobian"] == 1.0
    assert geometry["states"][1]["minimum_jacobian"] == -1.0
    assert geometry["states"][1]["minimum_vtk_id"] == 10

    changed = copy.deepcopy(inverted)
    changed["cells"][0] = [0, 2, 1, 3]
    with pytest.raises(RuntimeError, match="topology changed"):
        namespace["summarize_geometry"]([initial, changed], [0.0, 1.0])


def test_helper_holds_staged_runtime_against_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    paths, hashes = _runtime_files(tmp_path / "runtime")
    monkeypatch.setattr(module, "_APPROVED_SHA256", hashes)
    monkeypatch.setattr(
        module,
        "_APPROVED_PYTHON_TREE",
        {path.name: hashes[name] for name, path in paths.items() if name.startswith("python_")},
    )
    replacement_succeeded = False

    def launch(command: list[str], **kwargs: object) -> int:
        nonlocal replacement_succeeded
        del kwargs
        executable = Path(command[0])
        try:
            executable.write_bytes(b"replacement")
        except PermissionError:
            pass
        else:
            replacement_succeeded = True
        assert all(Path(item).name != "response.json" for item in command)
        return SimpleNamespace(return_code=0, response=b"{}")  # type: ignore[return-value]

    monkeypatch.setattr(module, "_launch_helper", launch)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    with module._hold_runtime(
        paths["python_executable"], paths["fbs_module"], paths["zlib"]
    ) as held:
        assert module._run_helper(held, {"protocol": 1}, scratch, 1.0) == {}

    assert replacement_succeeded is False
    assert list(scratch.iterdir()) == []


def test_helper_response_uses_the_bound_process_pipe_not_a_shared_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    paths, hashes = _runtime_files(tmp_path / "runtime")
    monkeypatch.setattr(module, "_APPROVED_SHA256", hashes)
    monkeypatch.setattr(
        module,
        "_APPROVED_PYTHON_TREE",
        {path.name: hashes[name] for name, path in paths.items() if name.startswith("python_")},
    )

    def launch(command: list[str], **kwargs: object) -> int:
        stage = Path(str(kwargs["stage"]))
        assert Path(command[-1]).name == "request.json"
        assert not (stage / "response.json").exists()
        return SimpleNamespace(return_code=0, response=b"{}")  # type: ignore[return-value]

    monkeypatch.setattr(module, "_launch_helper", launch)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    with module._hold_runtime(
        paths["python_executable"], paths["fbs_module"], paths["zlib"]
    ) as held:
        assert module._run_helper(held, {"protocol": 1}, scratch, 1.0) == {}

    assert list(scratch.iterdir()) == []


def test_private_cleanup_never_deletes_a_substituted_foreign_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    paths, hashes = _runtime_files(tmp_path / "runtime")
    monkeypatch.setattr(module, "_APPROVED_SHA256", hashes)
    monkeypatch.setattr(
        module,
        "_APPROVED_PYTHON_TREE",
        {path.name: hashes[name] for name, path in paths.items() if name.startswith("python_")},
    )
    stage_substitution_succeeded = False

    def launch(command: list[str], **kwargs: object) -> int:
        nonlocal stage_substitution_succeeded
        assert Path(command[-1]).name == "request.json"
        stage = Path(str(kwargs["stage"]))
        moved = stage.with_name(stage.name + "-owned")
        try:
            stage.rename(moved)
        except PermissionError:
            pass
        else:
            stage_substitution_succeeded = True
            moved.rename(stage)
        return SimpleNamespace(return_code=0, response=b"{}")  # type: ignore[return-value]

    monkeypatch.setattr(module, "_launch_helper", launch)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    with module._hold_runtime(
        paths["python_executable"], paths["fbs_module"], paths["zlib"]
    ) as held:
        assert module._run_helper(held, {"protocol": 1}, scratch, 1.0) == {}

    assert stage_substitution_succeeded is False


def test_xplt_close_failure_retains_the_exact_owner_for_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    owner = object()
    xplt_entry = module._HeldEntry("XPLT", tmp_path / "attempt.xplt", owner, "a" * 64)
    runtime = SimpleNamespace(
        python_executable=tmp_path / "python.exe",
        fbs_module=tmp_path / "fbs.pyd",
        zlib=tmp_path / "zlib1.dll",
    )
    retained: list[object] = []

    @contextlib.contextmanager
    def hold_runtime(*_args: object) -> Iterator[object]:
        yield object()

    def close_exact(candidate: object) -> None:
        assert candidate is owner
        raise OSError("close failed")

    monkeypatch.setattr(module, "validate_official_fbs_runtime", lambda value: value)
    monkeypatch.setattr(module, "_open_file", lambda *_args: xplt_entry)
    monkeypatch.setattr(module, "_hold_runtime", hold_runtime)
    monkeypatch.setattr(module, "_run_helper", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(module._fbs, "_close_owned_handle", close_exact)
    monkeypatch.setattr(module._fbs, "_retain_cleanup_owner", retained.append)

    with pytest.raises(OSError, match="close failed"):
        module._invoke_helper(runtime, xplt_entry.path, ("stress",), tmp_path)

    assert retained == [owner]


def test_helper_never_stages_a_mutable_vtk_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    paths, hashes = _runtime_files(tmp_path / "runtime")
    monkeypatch.setattr(module, "_APPROVED_SHA256", hashes)
    monkeypatch.setattr(
        module,
        "_APPROVED_PYTHON_TREE",
        {path.name: hashes[name] for name, path in paths.items() if name.startswith("python_")},
    )

    def launch(command: list[str], **kwargs: object) -> object:
        del command
        stage = Path(str(kwargs["stage"]))
        release_entries = cast(Sequence[Any], kwargs["release_entries"])
        assert not tuple(stage.glob("*.vtk"))
        assert all(not hasattr(entry, "child_writable") for entry in release_entries)
        return SimpleNamespace(return_code=0, response=b"{}")

    monkeypatch.setattr(module, "_launch_helper", launch)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    with module._hold_runtime(
        paths["python_executable"], paths["fbs_module"], paths["zlib"]
    ) as held:
        response = module._run_helper(held, {"protocol": 1}, scratch, 1.0)

    assert response == {}
    assert list(scratch.iterdir()) == []


def test_release_import_closure_rejects_an_unmanifested_non_system_dll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    files = {
        "python.exe": b"python",
        "python313.dll": b"runtime",
        "fbs.cp313-win_amd64.pyd": b"fbs",
        "zlib1.dll": b"zlib",
    }
    imports = {
        b"python": ("python313.dll", "KERNEL32.dll"),
        b"runtime": ("KERNEL32.dll",),
        b"fbs": ("python313.dll", "zlib1.dll", "foreign.dll"),
        b"zlib": ("KERNEL32.dll",),
    }
    monkeypatch.setattr(module, "_pe_imports", imports.__getitem__)

    with pytest.raises(module.OfficialFbsRuntimeError, match="foreign.dll"):
        module._validate_release_import_closure(files)


def test_loaded_module_attestation_rejects_paths_outside_stage_or_systemroot(
    tmp_path: Path,
) -> None:
    module = _module()
    stage = tmp_path / "stage"
    stage.mkdir()
    entries = {
        name: SimpleNamespace(path=stage / name)
        for name in (
            "python.exe",
            "python313.dll",
            "fbs.cp313-win_amd64.pyd",
            "zlib1.dll",
        )
    }

    with pytest.raises(module.OfficialFbsRuntimeError, match="loaded module path"):
        module._validate_loaded_module_paths(
            (
                stage / "python.exe",
                stage / "python313.dll",
                stage / "fbs.cp313-win_amd64.pyd",
                stage / "zlib1.dll",
                tmp_path / "foreign.dll",
            ),
            stage,
            entries,
            Path(r"C:\Windows"),
        )
