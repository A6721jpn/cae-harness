"""Runner authority regressions with bounded, retained-handle Windows processes."""

from __future__ import annotations

import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from febio_cae.adapters.febio.runner import RunnerAdapter
from febio_cae.domain import ExecutionSetting, PortError, Quantity, RunState

from .runner_fixture import _compiled, _owner, _Ownership
from .test_runner_job import _job_type, _no_pid_operations, _until

__all__ = ["_no_pid_operations"]


def _fixture(tmp_path: Path) -> tuple[Any, Any, Any, Any]:
    revision, _, _, bundle, store = _compiled(tmp_path)
    ownership = _Ownership()
    runner = RunnerAdapter(ownership=ownership, root=tmp_path / "runs", bundle_store=store)
    return (
        runner,
        ownership,
        replace(bundle, argv=(sys.executable, "-c", "import time;time.sleep(8)")),
        revision.spec.budget,
    )


def _cleanup_process(process: Any) -> None:
    if not process.closed:
        process.terminate_tree()
        _until(lambda: process.active_processes() == 0 and process.poll() is not None)
        process.close()


def _finish(runner: Any, attempt: Any, owner: Any) -> Any:
    deadline = time.monotonic() + 4
    while attempt.state in {RunState.RUNNING, RunState.DRAINING}:
        assert time.monotonic() < deadline
        attempt = runner.poll(attempt, owner).attempt
        time.sleep(0.02)
    return attempt


def test_two_runs_sharing_attempt_are_isolated_and_duplicate_launch_is_rejected(
    tmp_path: Path,
) -> None:
    runner, _, bundle, budget = _fixture(tmp_path)
    a = runner.start(bundle, _owner("run-a"), budget)
    first = next(iter(runner._managed.values()))
    b = runner.start(bundle, _owner("run-b"), budget)
    second = list(runner._managed.values())[-1]
    try:
        assert len(runner._managed) == 2, "full run scope must retain both jobs"
        with pytest.raises(PortError):
            runner.start(bundle, _owner("run-a"), budget)
        assert len(runner._managed) == 2
        assert runner.cancel(a, _owner("run-a")).attempt.state is RunState.CANCELLED
        assert first.process.closed
        assert second.process.poll() is None
        assert runner.poll(b, _owner("run-b")).attempt.state is RunState.RUNNING
        assert runner.cancel(b, _owner("run-b")).attempt.state is RunState.CANCELLED
    finally:
        for managed in (first, second):
            _cleanup_process(managed.process)
            managed.stdout.close()
            managed.stderr.close()


def test_forged_same_scope_identity_does_not_cancel_valid_job(tmp_path: Path) -> None:
    runner, _, bundle, budget = _fixture(tmp_path)
    attempt = runner.start(bundle, _owner(), budget)
    managed = next(iter(runner._managed.values()))
    try:
        assert attempt.process is not None
        forged = replace(attempt, process=replace(attempt.process, start_marker="foreign"))
        with pytest.raises(PortError):
            runner.cancel(forged, _owner())
        assert managed.process.poll() is None
        assert runner.poll(attempt, _owner()).attempt.state is RunState.RUNNING
    finally:
        _cleanup_process(managed.process)
        managed.stdout.close()
        managed.stderr.close()


def test_real_authority_revocation_drains_retained_job_without_publication(tmp_path: Path) -> None:
    runner, ownership, bundle, budget = _fixture(tmp_path)
    attempt = runner.start(bundle, _owner(), budget)
    managed = next(iter(runner._managed.values()))
    try:
        ownership.revoked = True
        result = _finish(runner, attempt, _owner())
        assert result.state is RunState.FAILED
        assert managed.process.closed and not runner._managed
    finally:
        _cleanup_process(managed.process)
        managed.stdout.close()
        managed.stderr.close()


def test_conflicting_bundle_budget_cannot_extend_authorized_deadline(tmp_path: Path) -> None:
    runner, _, bundle, budget = _fixture(tmp_path)
    bundle = replace(
        bundle, settings=(*bundle.settings, ExecutionSetting("max_elapsed_seconds", 30))
    )
    budget = replace(budget, max_elapsed=Quantity(0.15, "s"))
    attempt = runner.start(bundle, _owner(), budget)
    managed = next(iter(runner._managed.values()))
    try:
        # Reserved diagnostic is emitted once from Budget, never trusted as authority.
        values = [s.value for s in attempt.settings if s.name == "max_elapsed_seconds"]
        assert values == [0.15]
        time.sleep(0.2)
        assert _finish(runner, attempt, _owner()).state is RunState.FAILED
        assert managed.process.closed
    finally:
        _cleanup_process(managed.process)
        managed.stdout.close()
        managed.stderr.close()


def test_assignment_and_termination_failure_retains_recoverable_suspended_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_type = _job_type()
    held: list[Any] = []
    runner, _, bundle, budget = _fixture(tmp_path)
    marker = tmp_path / "must-not-execute"
    bundle = replace(
        bundle,
        argv=(sys.executable, "-c", f"from pathlib import Path;Path({str(marker)!r}).touch()"),
    )
    import _winapi

    original_terminate = _winapi.TerminateProcess
    cleanup_handles: list[int] = []

    def reject(self: Any) -> None:
        held.append(self)
        current = _winapi.GetCurrentProcess()
        cleanup_handles.append(
            _winapi.DuplicateHandle(
                current, self._process, current, 0, False, _winapi.DUPLICATE_SAME_ACCESS
            )
        )
        raise OSError("assignment injected")

    def fail_terminate(*args: Any) -> None:
        raise OSError("termination injected")

    monkeypatch.setattr(job_type, "_assign", reject)
    monkeypatch.setattr(_winapi, "TerminateProcess", fail_terminate)
    try:
        with pytest.raises(PortError):
            runner.start(bundle, _owner(), budget)
        assert len(held) == 1
        process = held[0]
        assert not process.closed and process._process is not None
        assert process.poll() is None and not marker.exists()
        assert runner._failed_launches, "runner must retain cleanup even after start raises"
        monkeypatch.setattr(_winapi, "TerminateProcess", original_terminate)
        assert runner.retry_failed_launch_cleanup() == 0
        assert process.closed and process.returncode is not None and not marker.exists()
    finally:
        monkeypatch.setattr(_winapi, "TerminateProcess", original_terminate)
        # Test-owned retained handle only. Old RED implementation drops it: capture
        # a duplicate before injection below to guarantee safe RED cleanup as well.
        for process in held:
            if process._process is not None:
                original_terminate(process._process, 1)
                _winapi.WaitForSingleObject(process._process, 3000)
                process.close()
        for handle in cleanup_handles:
            if _winapi.WaitForSingleObject(handle, 0) != 0:
                original_terminate(handle, 1)
            assert _winapi.WaitForSingleObject(handle, 3000) == 0
            _winapi.CloseHandle(handle)
