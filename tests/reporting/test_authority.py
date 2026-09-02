from __future__ import annotations

import copy
import hashlib
import importlib
import os
import pickle
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import cast
from unittest.mock import patch

import pytest

from febio_cae_harness.contracts import IntentContract
from febio_cae_harness.evidence import EvidenceStore
from febio_cae_harness.reporting import (
    AttemptIdentity,
    EvidenceKind,
    ReportAuthority,
    ReportAuthorityManager,
    evaluate_success_gates,
)
from febio_cae_harness.reporting.authority import _require_issued_authority
from febio_cae_harness.solver import (
    FbsAdapterManager,
    FbsValidation,
    OfficialFbsResultReceipt,
    OfficialFbsRuntime,
    SolverRunResult,
    SolverSupervisor,
)
from febio_cae_harness.solver import (
    headless as headless_module,
)
from febio_cae_harness.solver.runtime import probe_febio
from febio_cae_harness.workspace import AttemptWorkspace, ValidatedCaseWorkspace


class _Adapter:
    def read_fields(self, _path: Path, fields: tuple[str, ...]) -> dict[str, float]:
        return {field: 1.0 for field in fields}


def _case(
    tmp_path: Path,
) -> tuple[ReportAuthorityManager, SolverSupervisor, SolverRunResult, dict[EvidenceKind, Path]]:
    workspace = ValidatedCaseWorkspace(tmp_path / "tool", tmp_path / "02_CAE")
    case = workspace.create_case("case-a")
    store = EvidenceStore(case, IntentContract())
    store.record_attempt("attempt-a")
    attempt = AttemptWorkspace._from_manager(
        case,
        "attempt-a",
        case.temporary_root / "attempts" / "attempt-a",
    )
    intent = store.issue_intent_snapshot()
    log = "time step 1\ntime = 1.0\nnormal termination\n"
    code = (
        "import os; from pathlib import Path; "
        f"Path(os.environ['FEBIO_CAE_HARNESS_LOG']).write_text({log!r}); "
        "Path(os.environ['FEBIO_CAE_HARNESS_XPLT']).write_bytes(b'synthetic-xplt')"
    )
    input_path = attempt.write_text("model.feb", code)

    class ProbeProcess:
        returncode = 0

        def communicate(self, input: bytes, timeout: float) -> tuple[bytes, bytes]:
            del timeout
            assert input == b"quit\n"
            return b"version 4.12.0\n", b""

    with patch(
        "febio_cae_harness.solver.runtime.subprocess.Popen",
        lambda command, **kwargs: ProbeProcess(),
    ):
        runtime = probe_febio(Path(sys.executable))
    capability = headless_module._issue_launch_capability(
        attempt,
        intent,
        runtime,
        input_path,
        expected_steps=1,
        expected_final_time=1.0,
        timeout_seconds=None,
        requested_fields=("stress",),
    )
    root = capability.spec.attempt_root
    fbs = FbsAdapterManager(_Adapter(), "synthetic-runtime", root).issue_authority()
    supervisor = SolverSupervisor(
        capability,
        fbs_adapter=fbs,
        requested_fields=("stress",),
    )
    result = supervisor.run()
    evidence = {kind: root / f"{kind.value}.json" for kind in EvidenceKind}
    for kind, path in evidence.items():
        path.write_text(f"synthetic {kind.value}", encoding="utf-8")
    manager = ReportAuthorityManager(
        supervisor,
        result,
        AttemptIdentity(supervisor._case_id, supervisor._intent_id, supervisor._attempt_id),
    )
    return manager, supervisor, result, evidence


def _official_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[ModuleType, OfficialFbsRuntime]:
    module = importlib.import_module("febio_cae_harness.solver.official_fbs")
    python_dir = tmp_path / "python313"
    module_dir = tmp_path / "module"
    runtime_dir = tmp_path / "runtime"
    for directory in (python_dir, module_dir, runtime_dir):
        directory.mkdir(parents=True)
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
    hashes = {name: hashlib.sha256(payload).hexdigest() for name, payload in payloads.items()}
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
    return module, runtime


def _official_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[
    FbsAdapterManager,
    SolverSupervisor,
    SolverRunResult,
    dict[EvidenceKind, Path],
    OfficialFbsResultReceipt,
]:
    workspace = ValidatedCaseWorkspace(tmp_path / "tool", tmp_path / "02_CAE")
    case = workspace.create_case("case-a")
    store = EvidenceStore(case, IntentContract())
    store.record_attempt("attempt-a")
    attempt = AttemptWorkspace._from_manager(
        case,
        "attempt-a",
        case.temporary_root / "attempts" / "attempt-a",
    )
    intent = store.issue_intent_snapshot()
    log = "time step 1\ntime = 1.0\nnormal termination\n"
    xplt_payload = b"synthetic-xplt"
    code = (
        "import os; from pathlib import Path; "
        f"Path(os.environ['FEBIO_CAE_HARNESS_LOG']).write_text({log!r}); "
        f"Path(os.environ['FEBIO_CAE_HARNESS_XPLT']).write_bytes({xplt_payload!r})"
    )
    input_path = attempt.write_text("model.feb", code)

    class ProbeProcess:
        returncode = 0

        def communicate(self, input: bytes, timeout: float) -> tuple[bytes, bytes]:
            del timeout
            assert input == b"quit\n"
            return b"version 4.12.0\n", b""

    with patch(
        "febio_cae_harness.solver.runtime.subprocess.Popen",
        lambda command, **kwargs: ProbeProcess(),
    ):
        febio_runtime = probe_febio(Path(sys.executable))
    module, fbs_runtime = _official_runtime(tmp_path / "official-runtime", monkeypatch)
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
    monkeypatch.setattr(
        module,
        "_invoke_helper",
        lambda checked, path, fields, root: {
            "protocol": 1,
            "available_fields": ["stress"],
            "model_manifest": model_manifest,
            "model_sha256": module._model_manifest_sha256(model_manifest),
            "values": {
                "stress": {
                    "components": 9,
                    "count": 9,
                    "entity_count": 1,
                    "field_index": 0,
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "state_count": 1,
                }
            },
            "non_finite_fields": [],
            "xplt_sha256": hashlib.sha256(xplt_payload).hexdigest(),
        },
    )
    fbs_manager = module.open_official_fbs_manager(fbs_runtime, attempt.root)
    fbs_authority = fbs_manager.issue_authority()
    capability = headless_module._issue_launch_capability(
        attempt,
        intent,
        febio_runtime,
        input_path,
        expected_steps=1,
        expected_final_time=1.0,
        timeout_seconds=None,
        requested_fields=("stress",),
        fbs_adapter=fbs_authority,
    )
    supervisor = SolverSupervisor(
        capability,
    )
    result = supervisor.run()
    validation = result.fbs_validation
    assert isinstance(validation, FbsValidation)
    receipt = module.require_official_fbs_result(validation)
    evidence = {kind: capability.spec.attempt_root / f"{kind.value}.json" for kind in EvidenceKind}
    for kind, path in evidence.items():
        path.write_text(f"synthetic {kind.value}", encoding="utf-8")
    return fbs_manager, supervisor, result, evidence, receipt


def test_issue_binds_all_live_files_and_explicit_provenance(tmp_path: Path) -> None:
    manager, supervisor, result, evidence = _case(tmp_path)
    authority = manager.issue(evidence)

    assert type(authority) is ReportAuthority
    assert authority.manager is manager and authority.supervisor is supervisor
    identity = cast(AttemptIdentity, authority.identity)
    digests = cast(dict[Path, str], authority.digests)
    assert authority.result is result and identity.attempt_id == "attempt-a"
    assert authority.attempt_root == supervisor.spec.attempt_root
    assert authority.evidence == evidence
    assert authority.requested_fields == ("stress",)
    assert authority.runtime_identity == "synthetic-runtime"
    assert authority.provenance == "synthetic-unverified"
    assert len(digests) == 6
    assert all(len(digest) == 64 for digest in digests.values())
    for name in ("success", "succeeded", "fresh", "verified", "official"):
        assert not hasattr(authority, name)


def test_official_receipt_enables_official_fbs_gate_but_not_physical_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fbs_manager, supervisor, result, evidence, receipt = _official_case(tmp_path, monkeypatch)
    identity = AttemptIdentity(supervisor._case_id, supervisor._intent_id, supervisor._attempt_id)
    try:
        with pytest.raises(TypeError, match="official FBS"):
            ReportAuthorityManager(supervisor, result, identity).issue(evidence)
        manager = ReportAuthorityManager(
            supervisor,
            result,
            identity,
            official_fbs_result=receipt,
        )
        authority = manager.issue(evidence)
        evaluation = evaluate_success_gates(authority)

        assert authority.official_fbs_result is receipt
        assert authority.provenance == "official"
        assert evaluation.checks["official_fbs"] is True
        assert evaluation.checks["required_fields_finite"] is True
        assert evaluation.checks["mesh_evidence"] is False
        assert evaluation.checks["jacobian_evidence"] is False
        assert evaluation.checks["roi_evidence"] is False
        assert evaluation.checks["evaluation_evidence"] is False
        assert evaluation.success is False
    finally:
        fbs_manager.close()

    with pytest.raises(TypeError, match="authority|FBS|FbsValidation|receipt"):
        evaluate_success_gates(authority)


def test_issue_requires_exact_evidence_set_and_live_regular_paths(tmp_path: Path) -> None:
    manager, _, _, evidence = _case(tmp_path)
    with pytest.raises(ValueError):
        manager.issue({EvidenceKind.MESH: evidence[EvidenceKind.MESH]})
    outside = tmp_path / "outside.json"
    outside.write_text("outside", encoding="utf-8")
    invalid = dict(evidence)
    invalid[EvidenceKind.ROI] = outside
    with pytest.raises(ValueError):
        manager.issue(invalid)
    invalid[EvidenceKind.ROI] = evidence[EvidenceKind.MESH]
    with pytest.raises(ValueError):
        manager.issue(invalid)
    hardlink = evidence[EvidenceKind.MESH].parent / "hardlink.json"
    os.link(evidence[EvidenceKind.MESH], hardlink)
    invalid[EvidenceKind.ROI] = hardlink
    with pytest.raises(ValueError):
        manager.issue(invalid)


def test_consumer_rejects_other_bindings_and_live_mutations(tmp_path: Path) -> None:
    manager, supervisor, result, evidence = _case(tmp_path)
    authority = manager.issue(evidence)
    with pytest.raises(TypeError):
        _require_issued_authority(authority, object())
    evidence[EvidenceKind.MESH].write_text("changed", encoding="utf-8")
    with pytest.raises(TypeError):
        _require_issued_authority(authority, manager, supervisor, result)
    evidence[EvidenceKind.MESH].write_text("synthetic mesh", encoding="utf-8")
    replacement = tmp_path / "replacement.json"
    replacement.write_text("synthetic mesh", encoding="utf-8")
    os.replace(replacement, evidence[EvidenceKind.MESH])
    with pytest.raises(TypeError):
        _require_issued_authority(authority)


def test_fbs_forgery_and_bad_live_log_are_rejected(tmp_path: Path) -> None:
    manager, supervisor, result, evidence = _case(tmp_path)
    fbs = result.fbs_validation
    assert isinstance(fbs, FbsValidation)
    forged = replace(fbs)
    object.__setattr__(result, "fbs_validation", forged)
    try:
        with pytest.raises(TypeError):
            manager.issue(evidence)
    finally:
        object.__setattr__(result, "fbs_validation", fbs)
    result.log_path.write_text("fatal error", encoding="utf-8")
    with pytest.raises(ValueError):
        manager.issue(evidence)


def test_authorities_reject_forgery_copy_pickle_and_subclassing(tmp_path: Path) -> None:
    manager, _, _, evidence = _case(tmp_path)
    authority = manager.issue(evidence)
    for forged in (object.__new__(ReportAuthority),):
        with pytest.raises(TypeError):
            _require_issued_authority(forged)
    with pytest.raises(TypeError):
        ReportAuthority()
    with pytest.raises(AttributeError):
        object.__setattr__(authority, "success", True)
    for operation in (copy.copy, copy.deepcopy, pickle.dumps):
        with pytest.raises(TypeError):
            operation(authority)
    with pytest.raises(TypeError):

        class AuthorityChild(ReportAuthority):
            pass
