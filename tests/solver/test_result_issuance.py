"""Synthetic tests for the solver result issuance boundary."""

from __future__ import annotations

import copy
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from febio_cae_harness.contracts import IntentContract
from febio_cae_harness.evidence import EvidenceStore
from febio_cae_harness.solver import headless as headless_module
from febio_cae_harness.solver.runtime import FebioRuntimeDiagnostic, probe_febio
from febio_cae_harness.solver.supervisor import SolverSupervisor
from febio_cae_harness.solver.types import (
    SolverClassification,
    SolverOwnershipError,
    SolverRunResult,
)
from febio_cae_harness.workspace import AttemptWorkspace, ValidatedCaseWorkspace

_NORMAL_LOG = """
FEBio run
===== time step 1 =====
time = 0.5
===== time step 2 =====
time = 1.0
N O R M A L   T E R M I N A T I O N
"""


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


def _supervisor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    attempt: str = "attempt-a",
) -> SolverSupervisor:
    del attempt
    manager = ValidatedCaseWorkspace(tmp_path / "tool", tmp_path / "cae")
    case = manager.create_case("case-a")
    store = EvidenceStore(case, IntentContract())
    store.record_attempt("attempt-a")
    attempt_workspace = AttemptWorkspace._from_manager(
        case,
        "attempt-a",
        case.temporary_root / "attempts" / "attempt-a",
    )
    intent = store.issue_intent_snapshot()
    code = (
        "import os; from pathlib import Path; "
        f"Path(os.environ['FEBIO_CAE_HARNESS_LOG']).write_text({_NORMAL_LOG!r}); "
        "Path(os.environ['FEBIO_CAE_HARNESS_XPLT']).write_bytes(b'synthetic-xplt')"
    )
    input_path = attempt_workspace.write_text("model.feb", code)
    runtime = _issued_runtime(monkeypatch)
    capability = headless_module._issue_launch_capability(
        attempt_workspace,
        intent,
        runtime,
        input_path,
        expected_steps=2,
        expected_final_time=1.0,
        timeout_seconds=None,
    )
    return SolverSupervisor(capability)


def test_terminal_result_is_exactly_issued_and_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor = _supervisor(tmp_path, monkeypatch)

    result = supervisor.run()

    assert supervisor.wait() is result
    assert supervisor.result is result
    assert supervisor._validate_result(result) is result
    assert result.classification is SolverClassification.FBS_UNVERIFIED
    assert not result.success


def test_caller_results_and_foreign_bindings_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor = _supervisor(tmp_path, monkeypatch)
    result = supervisor.run()

    class ResultChild(SolverRunResult):
        pass

    forged = [
        SolverRunResult(**{field: getattr(result, field) for field in result.__dataclass_fields__}),
        replace(result),
        copy.copy(result),
        copy.deepcopy(result),
        object.__new__(SolverRunResult),
        ResultChild(**{field: getattr(result, field) for field in result.__dataclass_fields__}),
    ]
    for candidate in forged:
        with pytest.raises(SolverOwnershipError):
            supervisor._validate_result(candidate)

    foreign = _supervisor(tmp_path / "foreign", monkeypatch, attempt="attempt-b")
    with pytest.raises(SolverOwnershipError):
        foreign._validate_result(result)


def test_tampered_supervisor_binding_and_result_fields_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor = _supervisor(tmp_path, monkeypatch)
    result = supervisor.run()

    object.__setattr__(supervisor, "_result", replace(result))
    with pytest.raises(SolverOwnershipError):
        supervisor._validate_result(result)
    object.__setattr__(supervisor, "_result", result)

    object.__setattr__(supervisor, "_case_id", "tampered")
    with pytest.raises(SolverOwnershipError):
        supervisor._validate_result(result)
    object.__setattr__(supervisor, "_case_id", "case-a")

    object.__setattr__(result, "finished_at", result.started_at)
    with pytest.raises(SolverOwnershipError):
        supervisor._validate_result(result)
