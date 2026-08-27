from __future__ import annotations

import sys
from pathlib import Path

import pytest

from febio_cae_harness.solver.headless import HeadlessRunDiagnostic, run_headless_febio
from febio_cae_harness.solver.runtime import FebioRuntimeDiagnostic, RuntimeProbeError
from febio_cae_harness.solver.types import SolverClassification, SolverState


def _context(tmp_path: Path) -> tuple[Path, Path]:
    attempt_root = tmp_path / "attempt"
    attempt_root.mkdir()
    input_path = attempt_root / "model.feb"
    input_path.write_text("synthetic completed FEB", encoding="utf-8")
    return attempt_root, input_path


def test_missing_runtime_command_is_a_probe_failure(tmp_path: Path) -> None:
    attempt_root, input_path = _context(tmp_path)

    with pytest.raises(RuntimeProbeError):
        run_headless_febio(
            tmp_path / "missing-febio.exe",
            input_path,
            attempt_root,
            case_id="case",
            intent_id="intent",
            attempt_id="attempt",
        )


def test_input_must_be_physically_inside_attempt_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempt_root, _ = _context(tmp_path)
    outside_input = tmp_path / "outside.feb"
    outside_input.write_text("synthetic completed FEB", encoding="utf-8")

    monkeypatch.setattr(
        "febio_cae_harness.solver.headless.probe_febio",
        lambda executable: FebioRuntimeDiagnostic(
            Path("C:/synthetic/febio.exe"), "a" * 64, 1, "4.12.0"
        ),
    )

    with pytest.raises(ValueError, match="inside the attempt root"):
        run_headless_febio(
            Path("C:/synthetic/febio.exe"),
            outside_input,
            attempt_root,
            case_id="case",
            intent_id="intent",
            attempt_id="attempt",
        )


def _synthetic_probe_script(tmp_path: Path) -> Path:
    script = tmp_path / "probe_runtime.py"
    script.write_text(
        "import sys\n"
        "print('version 4.12.0', flush=True)\n"
        "assert sys.stdin.readline().strip() == 'quit'\n",
        encoding="utf-8",
    )
    return script


def _synthetic_run(
    tmp_path: Path, *, solver_code: str, timeout_seconds: float | None = 5.0
) -> HeadlessRunDiagnostic:
    attempt_root, input_path = _context(tmp_path)
    probe_script = _synthetic_probe_script(tmp_path)
    return run_headless_febio(
        sys.executable,
        input_path,
        attempt_root,
        case_id="case-a",
        intent_id="intent-a",
        attempt_id="attempt-a",
        expected_steps=1,
        expected_final_time=1.0,
        timeout_seconds=timeout_seconds,
        arguments=("-c", solver_code),
        probe_arguments=(str(probe_script),),
    )


def test_synthetic_normal_engine_path_is_fbs_unverified_and_immutable(tmp_path: Path) -> None:
    log = "time step 1\ntime = 1.0\nnormal termination\n"
    code = (
        "import os; from pathlib import Path; "
        "Path(os.environ['FEBIO_CAE_HARNESS_LOG']).write_text(" + repr(log) + "); "
        "Path(os.environ['FEBIO_CAE_HARNESS_XPLT']).write_bytes(b'synthetic xplt')"
    )
    diagnostic = _synthetic_run(tmp_path, solver_code=code)

    assert diagnostic.state is SolverState.NORMAL_EXIT
    assert diagnostic.classification is SolverClassification.FBS_UNVERIFIED
    assert diagnostic.return_code == 0
    assert diagnostic.pid is not None
    assert diagnostic.runtime_identity.version == "4.12.0"
    assert diagnostic.official_fbs is False
    assert diagnostic.success is False
    assert diagnostic.to_dict() == {
        "classification": "FBS_UNVERIFIED",
        "log": str(tmp_path / "attempt" / "model.log"),
        "official_fbs": False,
        "pid": diagnostic.pid,
        "return_code": 0,
        "runtime": {
            "path": str(Path(sys.executable).absolute()),
            "sha256": diagnostic.runtime_identity.sha256,
            "size": diagnostic.runtime_identity.size,
            "version": "4.12.0",
        },
        "state": "NORMAL_EXIT",
        "success": False,
        "xplt": str(tmp_path / "attempt" / "model.xplt"),
    }
    with pytest.raises(AttributeError):
        diagnostic.success = True  # type: ignore[misc]
    with pytest.raises(AttributeError):
        diagnostic.runtime_identity.version = "4.13.0"  # type: ignore[misc]


def test_nonzero_synthetic_engine_exit_is_a_terminal_failure(tmp_path: Path) -> None:
    diagnostic = _synthetic_run(tmp_path, solver_code="raise SystemExit(7)")

    assert diagnostic.state is SolverState.FAILED
    assert diagnostic.classification is SolverClassification.MISSING_OUTPUT
    assert diagnostic.return_code == 7
    assert diagnostic.success is False


def test_synthetic_timeout_is_a_terminal_failure(tmp_path: Path) -> None:
    diagnostic = _synthetic_run(
        tmp_path,
        solver_code="import time; time.sleep(30)",
        timeout_seconds=0.05,
    )

    assert diagnostic.state is SolverState.TIMED_OUT
    assert diagnostic.classification is SolverClassification.TIMEOUT
    assert diagnostic.success is False
