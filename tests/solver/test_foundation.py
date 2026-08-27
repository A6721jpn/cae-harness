from __future__ import annotations

import math
import sys
import time
from collections.abc import Sequence
from pathlib import Path

import pytest

from febio_cae_harness.contracts import IntentContract
from febio_cae_harness.evidence import EvidenceStore
from febio_cae_harness.solver import (
    FbsAdapterManager,
    OutputFreshnessError,
    SolverClassification,
    SolverLaunchCapability,
    SolverLaunchSpec,
    SolverState,
    SolverSupervisor,
    validate_log,
    validate_requested_fields,
)
from febio_cae_harness.solver import headless as headless_module
from febio_cae_harness.solver.runtime import FebioRuntimeDiagnostic, probe_febio
from febio_cae_harness.workspace import AttemptWorkspace, ValidatedCaseWorkspace

NORMAL_LOG = """
FEBio run
===== time step 1 =====
time = 0.5
===== time step 2 =====
time = 1.0
N O R M A L   T E R M I N A T I O N
"""


def make_spec(
    tmp_path: Path,
    *,
    code: str,
    timeout_seconds: float | None = None,
    requested_fields: Sequence[str] = (),
) -> SolverLaunchSpec:
    tmp_path.mkdir(parents=True, exist_ok=True)
    input_path = tmp_path / "model.feb"
    input_path.write_text("synthetic FEB input", encoding="utf-8")
    return SolverLaunchSpec(
        executable=Path(sys.executable),
        input_path=input_path,
        attempt_root=tmp_path,
        log_path=tmp_path / "attempt.log",
        xplt_path=tmp_path / "attempt.xplt",
        arguments=("-c", code),
        timeout_seconds=timeout_seconds,
        expected_steps=2,
        expected_final_time=1.0,
        requested_fields=tuple(requested_fields),
    )


class SyntheticVectorFixtureAdapter:
    def read_fields(self, xplt_path: Path, fields: Sequence[str]) -> dict[str, object]:
        assert xplt_path.is_file()
        return {field: [1.0, -2.0] for field in fields}


class SyntheticFixtureAdapter:
    def read_fields(self, xplt_path: Path, fields: Sequence[str]) -> dict[str, object]:
        assert xplt_path.is_file()
        return {field: 1.0 for field in fields}


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
    expected_steps: int | None = None,
    expected_final_time: float | None = None,
    timeout_seconds: float | None = None,
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
        expected_steps=expected_steps,
        expected_final_time=expected_final_time,
        timeout_seconds=timeout_seconds,
    )


def test_launch_spec_owns_command_and_fresh_attempt_outputs(tmp_path: Path) -> None:
    spec = make_spec(tmp_path, code="pass")

    assert spec.command == (sys.executable, "-c", "pass")
    assert spec.expected_outputs.log_path == tmp_path / "attempt.log"
    assert spec.expected_outputs.xplt_path == tmp_path / "attempt.xplt"
    spec.prepare_outputs()

    (tmp_path / "attempt.log").write_text("stale", encoding="utf-8")
    with pytest.raises(OutputFreshnessError):
        spec.prepare_outputs()


@pytest.mark.parametrize(
    ("text", "classification"),
    [
        ("initialization only\ninitialization complete", SolverClassification.INIT_ONLY),
        ("FATAL ERROR: cannot continue", SolverClassification.FATAL),
        ("Negative Jacobian determinant at element 4", SolverClassification.NEGATIVE_JACOBIAN),
    ],
)
def test_log_validation_classifies_non_success_logs(
    tmp_path: Path,
    text: str,
    classification: SolverClassification,
) -> None:
    path = tmp_path / "solver.log"
    path.write_text(text, encoding="utf-8")

    result = validate_log(path)

    assert result.classification is classification
    assert not result.valid


def test_supervisor_reports_missing_outputs_after_normal_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capability = _capability(tmp_path, monkeypatch, code="pass")

    result = SolverSupervisor(capability).run()

    assert result.state is SolverState.NORMAL_EXIT
    assert result.return_code == 0
    assert result.classification is SolverClassification.MISSING_OUTPUT
    assert not result.success


def test_supervisor_validates_log_xplt_and_synthetic_fbs_fields(tmp_path: Path) -> None:
    code = (
        "import os; from pathlib import Path; "
        f"Path(os.environ['FEBIO_CAE_HARNESS_LOG']).write_text({NORMAL_LOG!r}); "
        "Path(os.environ['FEBIO_CAE_HARNESS_XPLT']).write_bytes(b'xplt')"
    )
    monkeypatch = pytest.MonkeyPatch()
    capability = _capability(
        tmp_path,
        monkeypatch,
        code=code,
        expected_steps=2,
        expected_final_time=1.0,
    )
    authority = FbsAdapterManager(
        SyntheticVectorFixtureAdapter(), "synthetic-fixture", capability.spec.attempt_root
    ).issue_authority()

    result = SolverSupervisor(
        capability,
        fbs_adapter=authority,
        requested_fields=("displacement",),
    ).run()

    assert result.state is SolverState.NORMAL_EXIT
    assert result.classification is SolverClassification.FBS_UNVERIFIED
    assert not result.success
    assert result.fbs_validation is not None
    assert result.fbs_validation.provenance == "synthetic-unverified"
    assert not result.fbs_validation.missing_fields
    assert not result.fbs_validation.non_finite_fields


def test_synthetic_fbs_adapter_is_not_reported_as_official(tmp_path: Path) -> None:
    code = (
        "import os; from pathlib import Path; "
        f"Path(os.environ['FEBIO_CAE_HARNESS_LOG']).write_text({NORMAL_LOG!r}); "
        "Path(os.environ['FEBIO_CAE_HARNESS_XPLT']).write_bytes(b'xplt')"
    )
    monkeypatch = pytest.MonkeyPatch()
    capability = _capability(
        tmp_path,
        monkeypatch,
        code=code,
        expected_steps=2,
        expected_final_time=1.0,
    )
    authority = FbsAdapterManager(
        SyntheticFixtureAdapter(), "synthetic-fixture", capability.spec.attempt_root
    ).issue_authority()

    result = SolverSupervisor(
        capability,
        fbs_adapter=authority,
        requested_fields=("displacement",),
    ).run()

    assert result.fbs_validation is not None
    assert result.fbs_validation.provenance == "synthetic-unverified"
    assert result.classification is SolverClassification.FBS_UNVERIFIED
    assert not result.success


def test_fbs_boundary_rejects_non_finite_requested_field(tmp_path: Path) -> None:
    xplt_path = tmp_path / "result.xplt"
    xplt_path.write_bytes(b"synthetic xplt")

    class NonFiniteAdapter:
        def read_fields(self, path: Path, fields: Sequence[str]) -> dict[str, object]:
            del path
            return {fields[0]: math.nan}

    authority = FbsAdapterManager(
        NonFiniteAdapter(), "synthetic-fixture", tmp_path
    ).issue_authority()
    result = validate_requested_fields(
        authority,
        xplt_path,
        ("stress",),
    )

    assert not result.valid
    assert result.non_finite_fields == ("stress",)


def test_supervisor_timeout_and_cancel_are_owned_states(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capability = _capability(
        tmp_path,
        monkeypatch,
        code="import time; time.sleep(30)",
        timeout_seconds=0.05,
    )

    result = SolverSupervisor(capability).run()

    assert result.state is SolverState.TIMED_OUT
    assert result.classification is SolverClassification.TIMEOUT
    assert not result.success

    capability2 = _capability(
        tmp_path / "cancel",
        monkeypatch,
        code="import time; time.sleep(30)",
    )
    supervisor = SolverSupervisor(capability2)
    supervisor.start()
    time.sleep(0.05)
    cancelled = supervisor.cancel()

    assert cancelled.state is SolverState.CANCELLED
    assert cancelled.classification is SolverClassification.CANCELLED
    assert not cancelled.success
