"""Synthetic tests for the solver result issuance boundary."""

from __future__ import annotations

import copy
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from febio_cae_harness.solver.supervisor import SolverSupervisor
from febio_cae_harness.solver.types import (
    SolverClassification,
    SolverLaunchSpec,
    SolverOwnershipError,
    SolverRunResult,
)

_NORMAL_LOG = """
FEBio run
===== time step 1 =====
time = 0.5
===== time step 2 =====
time = 1.0
N O R M A L   T E R M I N A T I O N
"""


def _supervisor(tmp_path: Path, *, attempt: str = "attempt-a") -> SolverSupervisor:
    tmp_path.mkdir(parents=True, exist_ok=True)
    input_path = tmp_path / "model.feb"
    log_path = tmp_path / "result.log"
    xplt_path = tmp_path / "result.xplt"
    input_path.write_text("synthetic", encoding="utf-8")
    code = (
        "from pathlib import Path; "
        f"Path({str(log_path)!r}).write_text({_NORMAL_LOG!r}); "
        f"Path({str(xplt_path)!r}).write_bytes(b'synthetic-xplt')"
    )
    spec = SolverLaunchSpec(
        executable=Path(sys.executable),
        input_path=input_path,
        attempt_root=tmp_path,
        log_path=log_path,
        xplt_path=xplt_path,
        arguments=("-c", code),
        expected_steps=2,
        expected_final_time=1.0,
    )
    return SolverSupervisor(
        spec,
        case_id="case-a",
        intent_id="intent-a",
        attempt_id=attempt,
    )


def test_terminal_result_is_exactly_issued_and_reused(tmp_path: Path) -> None:
    supervisor = _supervisor(tmp_path)

    result = supervisor.run()

    assert supervisor.wait() is result
    assert supervisor.result is result
    assert supervisor._validate_result(result) is result
    assert result.classification is SolverClassification.FBS_UNVERIFIED
    assert not result.success


def test_caller_results_and_foreign_bindings_are_rejected(tmp_path: Path) -> None:
    supervisor = _supervisor(tmp_path)
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

    foreign = _supervisor(tmp_path / "foreign", attempt="attempt-b")
    with pytest.raises(SolverOwnershipError):
        foreign._validate_result(result)


def test_tampered_supervisor_binding_and_result_fields_are_rejected(tmp_path: Path) -> None:
    supervisor = _supervisor(tmp_path)
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
