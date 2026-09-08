"""Bounded Windows ownership probes; no native solver or PID-based cleanup."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from febio_cae.adapters.febio import runner as runner_module
from febio_cae.domain import Quantity, RunState

from .test_r1_remediation import _compiled, _owner, _Ownership


def _job_type() -> Any:
    assert hasattr(runner_module, "WindowsJobProcess"), (
        "runner must own a pre-execution Windows Job, not rediscover descendant PIDs"
    )
    return getattr(runner_module, "WindowsJobProcess")


def _until(predicate: Any, seconds: float = 5) -> None:
    deadline = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < deadline, "bounded fixture observation expired"
        time.sleep(0.02)


@pytest.fixture(autouse=True)
def _no_pid_operations(monkeypatch: pytest.MonkeyPatch) -> None:
    if os.name != "nt":
        pytest.skip("Windows Job Object integration")

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("PID-only observation/termination is forbidden")

    monkeypatch.setattr(os, "kill", forbidden)
    original_run = subprocess.run

    def guarded_run(args: Any, *rest: Any, **kwargs: Any) -> Any:
        assert "taskkill" not in str(args).lower()
        return original_run(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "run", guarded_run)


def _start(tmp_path: Path, code: str, budget_seconds: float = 8) -> tuple[Any, Any]:
    _job_type()  # Fail safely before executing the old uncontained implementation.
    revision, _, _, bundle, _ = _compiled(tmp_path)
    runner = runner_module.RunnerAdapter(ownership=_Ownership(), root=tmp_path / "runs")
    budget = replace(revision.spec.budget, max_elapsed=Quantity(budget_seconds, "s"))
    return runner, runner.start(replace(bundle, argv=(sys.executable, "-c", code)), _owner(), budget)


def _cleanup(runner: Any, attempt: Any) -> None:
    if runner._managed:
        managed = runner._managed[attempt.attempt_id]
        managed.process.terminate_tree()
        _until(lambda: managed.process.active_processes() == 0)
        managed.process.close()
        managed.stdout.close()
        managed.stderr.close()


def test_root_exit_keeps_descendant_writer_owned_until_natural_drain(tmp_path: Path) -> None:
    child = "from pathlib import Path; import time; p=Path('output/writer'); " + (
        "[(p.open('ab').write(b'x'),time.sleep(.05)) for _ in range(50)]"
    )
    code = f"import subprocess,sys; subprocess.Popen([sys.executable,'-c',{child!r}])"
    runner, attempt = _start(tmp_path, code)
    managed = runner._managed[attempt.attempt_id]
    process = managed.process
    try:
        _until(lambda: process.poll() == 0)
        writer = managed.attempt_root / "output/writer"
        _until(writer.exists)
        current = runner.poll(attempt, _owner()).attempt
        assert current.state is RunState.DRAINING
        assert process.active_processes() == 1
        before = writer.stat().st_size
        _until(lambda: writer.stat().st_size > before)
        _until(lambda: process.active_processes() == 0)
        result = runner.reconcile(current, _owner()).attempt
        assert result.state is RunState.VALIDATING
        assert process.closed and not runner._managed
        print("natural drain: root exit=0, job active=1 with writer growth, then active=0; handles closed")
    finally:
        _cleanup(runner, attempt)


def test_cancel_owned_tree_leaves_unrelated_sentinel_alive(tmp_path: Path) -> None:
    _job_type()
    sentinel = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(12)"])
    code = "import subprocess,sys,time;subprocess.Popen([sys.executable,'-c','import time;time.sleep(8)']);time.sleep(8)"
    runner = attempt = None
    try:
        runner, attempt = _start(tmp_path, code)
        process = runner._managed[attempt.attempt_id].process
        _until(lambda: process.active_processes() == 2)
        result = runner.cancel(attempt, _owner()).attempt
        assert result.state is RunState.CANCELLED
        assert process.closed and not runner._managed
        assert sentinel.poll() is None
        print("cancel: job active=2 -> confirmed zero/closed; unrelated held sentinel remains alive")
    finally:
        if runner is not None:
            _cleanup(runner, attempt)
        sentinel.terminate()  # Popen retains the actual Windows process HANDLE.
        sentinel.wait(timeout=3)


def test_accounting_failure_never_validates_then_recovers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    job_type = _job_type()
    runner, attempt = _start(tmp_path, "pass")
    process = runner._managed[attempt.attempt_id].process
    try:
        _until(lambda: process.poll() == 0)
        with monkeypatch.context() as patch:
            def unavailable(self: Any) -> int:
                raise OSError("injected accounting failure")
            patch.setattr(job_type, "active_processes", unavailable)
            current = runner.poll(attempt, _owner()).attempt
            assert current.state is RunState.DRAINING
            assert runner.reconcile(current, _owner()).attempt.state is RunState.DRAINING
        assert runner.poll(current, _owner()).attempt.state is RunState.VALIDATING
        assert process.closed
    finally:
        _cleanup(runner, attempt)


def test_budget_applies_after_root_exit_and_uses_legal_failed_transition(tmp_path: Path) -> None:
    code = "import subprocess,sys;subprocess.Popen([sys.executable,'-c','import time;time.sleep(8)'])"
    runner, attempt = _start(tmp_path, code, .4)
    process = runner._managed[attempt.attempt_id].process
    try:
        _until(lambda: process.poll() == 0)
        current = runner.poll(attempt, _owner()).attempt
        assert current.state is RunState.DRAINING
        time.sleep(.45)
        result = runner.poll(current, _owner())
        assert result.attempt.state is RunState.FAILED
        assert "budget" in " ".join(result.diagnostics)
        assert process.closed and not runner._managed
    finally:
        _cleanup(runner, attempt)


def test_assignment_failure_never_executes_and_releases_handles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    job_type = _job_type()
    helper = importlib.import_module(job_type.__module__)
    held: list[Any] = []
    original = job_type._assign

    def rejected(self: Any) -> None:
        held.append(self)
        raise OSError("injected job assignment failure")

    monkeypatch.setattr(job_type, "_assign", rejected)
    marker = tmp_path / "must-not-execute"
    from febio_cae.domain import PortError
    with pytest.raises(PortError, match="assignment"):
        _start(tmp_path, f"from pathlib import Path;Path({str(marker)!r}).touch()")
    assert len(held) == 1 and held[0].closed
    assert held[0].returncode is not None
    assert not marker.exists()
    assert helper is not None and original is not None
    print("assignment failure: suspended root terminated by retained handle before resume; handles closed")
