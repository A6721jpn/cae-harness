"""Synthetic durability tests for the owned solver supervisor."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from febio_cae_harness.contracts import IntentContract
from febio_cae_harness.evidence import EvidenceStore
from febio_cae_harness.solver import headless as headless_module
from febio_cae_harness.solver.runtime import FebioRuntimeDiagnostic, probe_febio
from febio_cae_harness.solver.supervisor import SolverSupervisor
from febio_cae_harness.solver.types import (
    SolverConfigurationError,
    SolverLaunchCapability,
    SolverLaunchSpec,
    SolverState,
)
from febio_cae_harness.workspace import AttemptWorkspace, ValidatedCaseWorkspace


def _spec(tmp_path: Path) -> SolverLaunchSpec:
    input_path = tmp_path / "input.feb"
    input_path.write_text("synthetic", encoding="utf-8")
    return SolverLaunchSpec(
        executable=Path(sys.executable),
        input_path=input_path,
        attempt_root=tmp_path / "attempt",
        arguments=("-c", "import time; time.sleep(30)"),
    )


def _issued_runtime(monkeypatch: pytest.MonkeyPatch) -> FebioRuntimeDiagnostic:
    class ProbeProcess:
        returncode = 0

        def communicate(self, input: bytes, timeout: float) -> tuple[bytes, bytes]:
            del timeout
            assert input == b"quit\n"
            return b"version 4.12.0\n", b""

    with monkeypatch.context() as probe_patch:
        probe_patch.setattr(
            "febio_cae_harness.solver.runtime.subprocess.Popen",
            lambda command, **kwargs: ProbeProcess(),
        )
        return probe_febio(Path(sys.executable))


def _capability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    code: str,
) -> SolverLaunchCapability:
    manager = ValidatedCaseWorkspace(tmp_path / "tool", tmp_path / "cae")
    case = manager.create_case("case-a")
    store = EvidenceStore(case, IntentContract())
    store.record_attempt("attempt-a")
    attempt = AttemptWorkspace._from_manager(
        case,
        "attempt-a",
        case.temporary_root / "attempts" / "attempt-a",
    )
    intent = store.issue_intent_snapshot()
    input_path = attempt.write_text("input.feb", code)
    runtime = _issued_runtime(monkeypatch)
    return headless_module._issue_launch_capability(
        attempt,
        intent,
        runtime,
        input_path,
        expected_steps=None,
        expected_final_time=None,
        timeout_seconds=None,
    )


def test_start_persists_attempt_owned_process_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capability = _capability(tmp_path, monkeypatch, code="import time; time.sleep(30)")
    supervisor = SolverSupervisor(capability)
    spec = capability.spec
    intent = object.__getattribute__(capability, "_intent_snapshot")

    supervisor.start()
    try:
        record_path = supervisor.process_record_path
        assert record_path == spec.attempt_root / "process.json"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        assert record["case_id"] == "case-a"
        assert record["intent_id"] == intent.intent_sha256
        assert record["attempt_id"] == "attempt-a"
        assert record["executable_path"] == str(spec.executable)
        assert record["pid"] == supervisor.pid
        assert record["process_creation_identity"]
        assert record["start_time"]
        assert record["owned_output_paths"] == {
            "log": str(spec.expected_outputs.log_path),
            "xplt": str(spec.expected_outputs.xplt_path),
        }
    finally:
        supervisor.cancel()

    assert supervisor.state is SolverState.CANCELLED


def test_raw_launch_spec_is_rejected_before_popen_or_attempt_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path = tmp_path / "model.feb"
    attempt_root = tmp_path / "caller-selected-attempt"
    input_path.write_text("synthetic", encoding="utf-8")
    spec = SolverLaunchSpec(
        executable=Path(sys.executable),
        input_path=input_path,
        attempt_root=attempt_root,
    )

    def unexpected_popen(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("raw launch spec reached Popen")

    monkeypatch.setattr(subprocess, "Popen", unexpected_popen)
    with pytest.raises(SolverConfigurationError, match="capability"):
        SolverSupervisor(spec)  # type: ignore[arg-type]

    assert not attempt_root.exists()
    assert not (attempt_root / "process.json").exists()


def test_forged_fully_populated_launch_capability_is_rejected_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path = tmp_path / "model.feb"
    attempt_root = tmp_path / "forged-attempt"
    input_path.write_text("synthetic", encoding="utf-8")
    spec = SolverLaunchSpec(
        executable=Path(sys.executable),
        input_path=input_path,
        attempt_root=attempt_root,
    )
    forged = object.__new__(SolverLaunchCapability)
    object.__setattr__(forged, "_spec", spec)
    object.__setattr__(forged, "_attempt_workspace", object())
    object.__setattr__(forged, "_intent_snapshot", object())
    object.__setattr__(forged, "_runtime_diagnostic", object())

    def unexpected_popen(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("forged launch capability reached Popen")

    monkeypatch.setattr(subprocess, "Popen", unexpected_popen)
    with pytest.raises(SolverConfigurationError, match="issued|capability"):
        SolverSupervisor(forged)

    assert not attempt_root.exists()
    assert not (attempt_root / "process.json").exists()
