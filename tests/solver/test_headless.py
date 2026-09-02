from __future__ import annotations

import hashlib
import inspect
import os
import stat
import sys
from copy import copy, deepcopy
from pathlib import Path
from typing import Any, cast

import pytest

from febio_cae_harness.contracts import IntentContract
from febio_cae_harness.evidence import EvidenceStore, IntentSnapshotAuthority
from febio_cae_harness.solver import headless as headless_module
from febio_cae_harness.solver import official_fbs as official_fbs_module
from febio_cae_harness.solver.execution import issue_execution_authority
from febio_cae_harness.solver.fbs import (
    FbsAdapterAuthority,
    FbsAdapterManager,
    validate_requested_fields,
)
from febio_cae_harness.solver.headless import (
    HeadlessConfigurationError,
    HeadlessRunDiagnostic,
    headless_exit_code,
    run_headless_febio,
)
from febio_cae_harness.solver.runtime import (
    FebioRuntimeDiagnostic,
    RuntimeProbeError,
    probe_febio,
)
from febio_cae_harness.solver.supervisor import SolverSupervisor
from febio_cae_harness.solver.types import (
    SolverClassification,
    SolverConfigurationError,
    SolverLaunchCapability,
    SolverLaunchSpec,
    SolverRunResult,
    SolverState,
    _require_issued_launch_capability,
    _validate_launch_capability,
)
from febio_cae_harness.workspace import AttemptWorkspace, ValidatedCaseWorkspace


def _authority_context(
    tmp_path: Path, *, case_id: str
) -> tuple[AttemptWorkspace, IntentSnapshotAuthority, Path]:
    manager = ValidatedCaseWorkspace(tmp_path / "tool", tmp_path / "cae")
    case = manager.create_case(case_id)
    store = EvidenceStore(case, IntentContract())
    store.record_attempt("attempt-a")
    attempt = AttemptWorkspace._from_manager(
        case,
        "attempt-a",
        case.temporary_root / "attempts" / "attempt-a",
    )
    intent = store.issue_intent_snapshot()
    input_path = attempt.write_text("model.feb", "synthetic completed FEB")
    return attempt, intent, input_path


def _forged_runtime() -> FebioRuntimeDiagnostic:
    return FebioRuntimeDiagnostic(Path(sys.executable).absolute(), "a" * 64, 1, "4.12.0")


def test_headless_diagnostic_derives_official_without_caller_boolean(tmp_path: Path) -> None:
    diagnostic = HeadlessRunDiagnostic(
        runtime_identity=_forged_runtime(),
        state=SolverState.NORMAL_EXIT,
        classification=SolverClassification.SUCCESS,
        return_code=0,
        pid=123,
        log_path=tmp_path / "model.log",
        xplt_path=tmp_path / "model.xplt",
    )

    assert diagnostic.classification is SolverClassification.SUCCESS
    assert diagnostic.official_fbs is False
    assert diagnostic.success is False
    assert headless_exit_code(diagnostic) == 4
    with pytest.raises(TypeError):
        cast(Any, HeadlessRunDiagnostic)(
            runtime_identity=_forged_runtime(),
            state=SolverState.NORMAL_EXIT,
            classification=SolverClassification.SUCCESS,
            return_code=0,
            pid=123,
            log_path=tmp_path / "model.log",
            xplt_path=tmp_path / "model.xplt",
            official_fbs=False,
        )


def _issued_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FebioRuntimeDiagnostic:
    executable = tmp_path / "fake-febio"
    executable.write_bytes(b"synthetic FEBio executable")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)

    class CompletedProcess:
        returncode = 0

        def communicate(self, input: bytes, timeout: float) -> tuple[bytes, bytes]:
            assert input == b"quit\n"
            return b"version 4.12.0\n", b""

    monkeypatch.setattr(
        "febio_cae_harness.solver.runtime.subprocess.Popen",
        lambda command, **kwargs: CompletedProcess(),
    )
    return probe_febio(executable)


def _issued_official_manager(
    attempt: AttemptWorkspace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[official_fbs_module.OfficialFbsRuntime, FbsAdapterManager]:
    root = tmp_path / "official-fbs"
    python_root = root / "python313"
    python_root.mkdir(parents=True)
    payloads = {
        "python_executable": (python_root / "python.exe", b"fixture-python"),
        "python_dll": (python_root / "python313.dll", b"fixture-python-dll"),
        "python_stdlib": (python_root / "python313.zip", b"fixture-stdlib"),
        "python_path_config": (python_root / "python313._pth", b"fixture-path"),
        "fbs_module": (root / "fbs.cp313-win_amd64.pyd", b"fixture-fbs"),
        "zlib": (root / "zlib1.dll", b"fixture-zlib"),
    }
    for path, payload in payloads.values():
        path.write_bytes(payload)
    hashes = {name: hashlib.sha256(payload).hexdigest() for name, (_, payload) in payloads.items()}
    monkeypatch.setattr(official_fbs_module, "_APPROVED_SHA256", hashes)
    monkeypatch.setattr(
        official_fbs_module,
        "_APPROVED_PYTHON_TREE",
        {
            path.name: hashes[name]
            for name, (path, _) in payloads.items()
            if name.startswith("python_")
        },
    )
    monkeypatch.setattr(
        official_fbs_module,
        "_probe_helper",
        lambda held, timeout: official_fbs_module._ProbeHelperResult(
            {
                "protocol": 1,
                "python": "3.13",
                "module": "fbs",
                "api": ["ReadPlotFile", "vtkExport"],
            },
            "a" * 64,
        ),
    )
    monkeypatch.setattr(
        official_fbs_module,
        "_invoke_helper",
        lambda checked, path, fields, attempt_root: {
            "protocol": 1,
            "available_fields": list(fields),
            "values": {field: {"count": 2, "minimum": 0.0, "maximum": 1.0} for field in fields},
            "non_finite_fields": [],
        },
    )
    receipt = official_fbs_module.probe_official_fbs_runtime(
        payloads["python_executable"][0],
        payloads["fbs_module"][0],
        payloads["zlib"][0],
    )
    return receipt, official_fbs_module.open_official_fbs_manager(receipt, attempt.root)


def test_headless_rejects_forged_runtime_diagnostic(tmp_path: Path) -> None:
    attempt, intent, input_path = _authority_context(tmp_path, case_id="case-a")

    with pytest.raises(RuntimeProbeError, match="issued"):
        run_headless_febio(attempt, intent, _forged_runtime(), input_path)


def test_headless_rejects_raw_attempt_root(tmp_path: Path) -> None:
    attempt, intent, input_path = _authority_context(tmp_path, case_id="case-a")

    with pytest.raises(HeadlessConfigurationError, match="AttemptWorkspace"):
        run_headless_febio(attempt.root, intent, _forged_runtime(), input_path)  # type: ignore[arg-type]


def test_headless_rejects_fully_populated_forged_attempt_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempt, intent, input_path = _authority_context(tmp_path, case_id="case-a")
    runtime = _issued_runtime(tmp_path, monkeypatch)
    forged = object.__new__(AttemptWorkspace)
    object.__setattr__(forged, "case_id", attempt.case_id)
    object.__setattr__(forged, "attempt_id", attempt.attempt_id)
    object.__setattr__(forged, "root", attempt.root)

    class RejectingSupervisor:
        def __init__(self, spec: SolverLaunchSpec, **context: object) -> None:
            del spec, context
            raise AssertionError("forged AttemptWorkspace reached SolverSupervisor")

    monkeypatch.setattr(headless_module, "SolverSupervisor", RejectingSupervisor)

    with pytest.raises(HeadlessConfigurationError, match="registered|issued|live"):
        run_headless_febio(forged, intent, runtime, input_path)


def test_headless_rejects_mutated_issued_attempt_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempt, intent, input_path = _authority_context(tmp_path, case_id="case-a")
    runtime = _issued_runtime(tmp_path, monkeypatch)
    del input_path
    store = object.__getattribute__(intent, "_store")
    store.record_attempt("attempt-b")
    intent = store.issue_intent_snapshot()
    mutated_root = attempt.root.parent / "attempt-b"
    mutated_input = mutated_root / "model.feb"
    mutated_input.write_text("synthetic completed FEB", encoding="utf-8")
    object.__setattr__(attempt, "root", mutated_root)

    class RejectingSupervisor:
        def __init__(self, spec: SolverLaunchSpec, **context: object) -> None:
            del spec, context
            raise AssertionError("mutated AttemptWorkspace reached SolverSupervisor")

    monkeypatch.setattr(headless_module, "SolverSupervisor", RejectingSupervisor)

    with pytest.raises(HeadlessConfigurationError, match="registered|binding|live"):
        run_headless_febio(attempt, intent, runtime, mutated_input)


def test_headless_rejects_foreign_attempt_and_intent(tmp_path: Path) -> None:
    attempt_a, intent_a, input_a = _authority_context(tmp_path / "a", case_id="case-a")
    attempt_b, intent_b, input_b = _authority_context(tmp_path / "b", case_id="case-b")

    with pytest.raises(HeadlessConfigurationError, match="case"):
        run_headless_febio(attempt_b, intent_a, _forged_runtime(), input_b)
    with pytest.raises(HeadlessConfigurationError, match="case"):
        run_headless_febio(attempt_a, intent_b, _forged_runtime(), input_a)


def test_headless_has_no_caller_supplied_context_labels_or_arguments() -> None:
    parameters = inspect.signature(run_headless_febio).parameters

    assert not {
        "executable",
        "attempt_root",
        "case_id",
        "intent_id",
        "attempt_id",
        "arguments",
        "probe_arguments",
    }.intersection(parameters)


def test_headless_requires_input_inside_issued_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempt, intent, _ = _authority_context(tmp_path, case_id="case-a")
    outside = tmp_path / "outside.feb"
    outside.write_text("synthetic completed FEB", encoding="utf-8")
    runtime = _issued_runtime(tmp_path, monkeypatch)

    with pytest.raises(HeadlessConfigurationError, match="inside the attempt root"):
        run_headless_febio(attempt, intent, runtime, outside)


def test_headless_derives_context_and_preserves_fbs_unverified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempt, intent, input_path = _authority_context(tmp_path, case_id="case-a")
    runtime = _issued_runtime(tmp_path, monkeypatch)
    captured_capability: SolverLaunchCapability | None = None

    class FakeSupervisor:
        def __init__(self, capability: SolverLaunchCapability) -> None:
            nonlocal captured_capability
            captured_capability = capability

        def run(self) -> SolverRunResult:
            assert captured_capability is not None
            spec = captured_capability.spec
            outputs = spec.expected_outputs
            return SolverRunResult(
                state=SolverState.NORMAL_EXIT,
                classification=SolverClassification.FBS_UNVERIFIED,
                return_code=0,
                pid=123,
                command=spec.command,
                log_path=outputs.log_path,
                xplt_path=outputs.xplt_path,
            )

    monkeypatch.setattr(headless_module, "SolverSupervisor", FakeSupervisor)
    diagnostic = run_headless_febio(attempt, intent, runtime, input_path)

    assert captured_capability is not None
    spec = captured_capability.spec
    assert spec.attempt_root == attempt.root
    assert spec.input_path == input_path
    assert spec.command == (os.fspath(runtime.path), "-i", os.fspath(input_path))
    assert diagnostic.runtime_identity is runtime
    assert diagnostic.classification is SolverClassification.FBS_UNVERIFIED
    assert diagnostic.success is False
    assert headless_exit_code(diagnostic) == 5


def test_headless_diagnostic_does_not_preserve_official_fbs_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempt, intent, input_path = _authority_context(tmp_path, case_id="case-a")
    runtime = _issued_runtime(tmp_path, monkeypatch)
    captured_authority: FbsAdapterAuthority | None = None
    captured_capability: SolverLaunchCapability | None = None

    class FakeSupervisor:
        def __init__(self, capability: SolverLaunchCapability) -> None:
            nonlocal captured_authority, captured_capability
            self.capability = capability
            captured_capability = capability
            record = _require_issued_launch_capability(capability)
            captured_authority = cast(FbsAdapterAuthority | None, record.fbs_adapter)

        def run(self) -> SolverRunResult:
            assert captured_authority is not None
            outputs = self.capability.spec.expected_outputs
            outputs.xplt_path.write_bytes(b"synthetic-xplt")
            validation = validate_requested_fields(
                captured_authority,
                outputs.xplt_path,
                self.capability.spec.requested_fields,
            )
            return SolverRunResult(
                state=SolverState.NORMAL_EXIT,
                classification=SolverClassification.SUCCESS,
                return_code=0,
                pid=123,
                command=self.capability.spec.command,
                log_path=outputs.log_path,
                xplt_path=outputs.xplt_path,
                fbs_validation=validation,
            )

    monkeypatch.setattr(headless_module, "SolverSupervisor", FakeSupervisor)
    official_runtime, manager = _issued_official_manager(attempt, tmp_path, monkeypatch)
    with manager:
        authority = manager.issue_authority()
        diagnostic = run_headless_febio(
            attempt,
            intent,
            runtime,
            input_path,
            requested_fields=("stress",),
            fbs_adapter=authority,
        )

        assert captured_authority is authority
        assert diagnostic.success is False
        assert diagnostic.official_fbs is False
        assert diagnostic.to_dict()["official_fbs"] is False
        assert headless_exit_code(diagnostic) == 4
        assert captured_capability is not None
        *_, launch_context, _ = _validate_launch_capability(captured_capability)
        assert launch_context["fbs"] == {
            "official": True,
            "profile": official_fbs_module._PROFILE,
            "provenance": "official",
            "runtime_identity": official_runtime.runtime_identity,
        }

    assert diagnostic.official_fbs is False


def test_recover_headless_rebinds_only_recorded_official_fbs_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt, intent, input_path = _authority_context(tmp_path, case_id="case-a")
    runtime = _issued_runtime(tmp_path, monkeypatch)
    official_runtime, manager = _issued_official_manager(attempt, tmp_path, monkeypatch)
    execution = issue_execution_authority(
        attempt,
        intent,
        runtime,
        input_path,
        requested_fields=("stress",),
        official_fbs_runtime=official_runtime,
    )
    store = object.__getattribute__(intent, "_store")
    reopened_store = EvidenceStore.open(store.case_workspace)
    reopened_intent = reopened_store.issue_intent_snapshot()
    captured: SolverLaunchCapability | None = None

    class FakeSupervisor:
        @classmethod
        def reconnect(cls, capability: SolverLaunchCapability) -> object:
            nonlocal captured
            captured = capability
            return object()

    monkeypatch.setattr(headless_module, "SolverSupervisor", FakeSupervisor)
    with manager:
        authority = manager.issue_authority()
        session = headless_module.recover_headless_febio(
            reopened_store,
            reopened_intent,
            runtime,
            "attempt-a",
            official_fbs_runtime=official_runtime,
            fbs_adapter=authority,
        )

        assert captured is not None
        assert _require_issued_launch_capability(captured).fbs_adapter is authority
        assert session.execution.record_sha256 == execution.record_sha256


def test_recover_headless_uses_only_recorded_execution_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt, intent, input_path = _authority_context(tmp_path, case_id="case-a")
    runtime = _issued_runtime(tmp_path, monkeypatch)
    execution = issue_execution_authority(
        attempt,
        intent,
        runtime,
        input_path,
        requested_fields=("displacement",),
        expected_steps=4,
        expected_final_time=2.5,
        timeout_seconds=17.0,
    )
    store = object.__getattribute__(intent, "_store")
    reopened_store = EvidenceStore.open(store.case_workspace)
    reopened_intent = reopened_store.issue_intent_snapshot()
    captured: SolverLaunchCapability | None = None

    class FakeSupervisor:
        @classmethod
        def reconnect(cls, capability: SolverLaunchCapability) -> object:
            nonlocal captured
            captured = capability
            return object()

    monkeypatch.setattr(headless_module, "SolverSupervisor", FakeSupervisor)
    recover = getattr(headless_module, "recover_headless_febio", None)
    assert callable(recover), "recorded headless recovery API is missing"

    session = recover(reopened_store, reopened_intent, runtime, "attempt-a")

    assert captured is not None
    assert captured.spec.attempt_root == attempt.root
    assert captured.spec.input_path == execution.input_path
    assert captured.spec.expected_steps == 4
    assert captured.spec.expected_final_time == 2.5
    assert captured.spec.timeout_seconds == 17.0
    assert captured.spec.requested_fields == ("displacement",)
    assert session.execution.record_sha256 == execution.record_sha256
    assert session.attempt.attempt_id == "attempt-a"
    assert session.supervisor is not None


def test_recover_headless_rejects_unrecorded_attempt_before_reconnect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt, intent, _ = _authority_context(tmp_path, case_id="case-a")
    runtime = _issued_runtime(tmp_path, monkeypatch)
    store = object.__getattribute__(intent, "_store")
    recover = getattr(headless_module, "recover_headless_febio", None)
    assert callable(recover), "recorded headless recovery API is missing"

    with pytest.raises(HeadlessConfigurationError, match="recorded"):
        recover(store, intent, runtime, "attempt-missing")

    assert not (attempt.root.parent / "attempt-missing").exists()


def _capture_capability(
    attempt: AttemptWorkspace,
    intent: IntentSnapshotAuthority,
    runtime: FebioRuntimeDiagnostic,
    input_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> SolverLaunchCapability:
    captured: SolverLaunchCapability | None = None

    class CapturingSupervisor:
        def __init__(self, capability: SolverLaunchCapability) -> None:
            nonlocal captured
            captured = capability

        def run(self) -> SolverRunResult:
            assert captured is not None
            outputs = captured.spec.expected_outputs
            return SolverRunResult(
                state=SolverState.NORMAL_EXIT,
                classification=SolverClassification.FBS_UNVERIFIED,
                return_code=0,
                pid=123,
                command=captured.spec.command,
                log_path=outputs.log_path,
                xplt_path=outputs.xplt_path,
            )

    monkeypatch.setattr(headless_module, "SolverSupervisor", CapturingSupervisor)
    run_headless_febio(attempt, intent, runtime, input_path)
    assert captured is not None
    return captured


def test_launch_capability_cannot_be_copied_or_reused_as_a_forgery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt, intent, input_path = _authority_context(tmp_path, case_id="case-a")
    runtime = _issued_runtime(tmp_path, monkeypatch)
    capability = _capture_capability(attempt, intent, runtime, input_path, monkeypatch)

    for operation in (copy, deepcopy):
        with pytest.raises(TypeError):
            operation(capability)

    object.__setattr__(capability, "_runtime_diagnostic", object())
    with pytest.raises(SolverConfigurationError, match="binding|state|issued"):
        SolverSupervisor(capability)


@pytest.mark.parametrize("authority", ("attempt", "intent", "runtime", "input"))
def test_stale_or_mutated_launch_authority_is_rejected_before_file_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authority: str,
) -> None:
    attempt, intent, input_path = _authority_context(tmp_path, case_id="case-a")
    runtime = _issued_runtime(tmp_path, monkeypatch)
    capability = _capture_capability(attempt, intent, runtime, input_path, monkeypatch)
    root = attempt.root

    if authority == "attempt":
        object.__setattr__(attempt, "root", root.parent / "attempt-b")
    elif authority == "intent":
        store = object.__getattribute__(intent, "_store")
        store.record_attempt("attempt-b")
    elif authority == "runtime":
        object.__setattr__(runtime, "version", "4.12.1")
    else:
        input_path.write_text("mutated synthetic input", encoding="utf-8")

    def unexpected_popen(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("stale launch authority reached Popen")

    monkeypatch.setattr("febio_cae_harness.solver.supervisor.subprocess.Popen", unexpected_popen)
    with pytest.raises(SolverConfigurationError, match="authority|binding|stale|live"):
        SolverSupervisor(capability)

    assert not (root / "process.json").exists()
    assert not (root / "model.log").exists()
    assert not (root / "model.xplt").exists()
