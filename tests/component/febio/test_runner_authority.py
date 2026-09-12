"""Runner authority regressions with bounded, retained-handle Windows processes."""

from __future__ import annotations

import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from febio_cae.adapters.febio.runner import RunnerAdapter
from febio_cae.domain import ExecutionSetting, PortError, PortErrorCategory, Quantity, RunState

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


@pytest.mark.parametrize("invalid", ["path", "executable"])
def test_inherited_prelaunch_rejections_never_execute(tmp_path: Path, invalid: str) -> None:
    runner, _, bundle, budget = _fixture(tmp_path)
    marker = tmp_path / "must-not-run"
    bundle = replace(
        bundle,
        argv=(sys.executable, "-c", f"from pathlib import Path;Path({str(marker)!r}).touch()"),
    )
    owner = _owner()
    if invalid == "path":
        owner = _owner("../escaped")
    else:
        bundle = replace(bundle, tool=replace(bundle.tool, executable_digest="0" * 64))
    with pytest.raises(PortError):
        runner.start(bundle, owner, budget)
    assert not runner._managed and not marker.exists()


def test_cancel_with_uncertain_drain_retains_job_and_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, _, bundle, budget = _fixture(tmp_path)
    attempt = runner.start(bundle, _owner(), budget)
    managed = next(iter(runner._managed.values()))
    try:
        with monkeypatch.context() as patch:
            patch.setattr(runner, "_wait_for_drain", lambda *args, **kwargs: False)
            current = runner.cancel(attempt, _owner()).attempt
            assert current.state is RunState.DRAINING
            assert not managed.process.closed and runner._managed
        assert _finish(runner, current, _owner()).state is RunState.CANCELLED
        assert managed.process.closed
    finally:
        _cleanup_process(managed.process)
        managed.stdout.close()
        managed.stderr.close()


def test_compiled_input_is_staged_and_success_requires_observed_exit(tmp_path: Path) -> None:
    runner, _, bundle, budget = _fixture(tmp_path)
    bundle = replace(
        bundle,
        argv=(
            sys.executable,
            "-c",
            "from pathlib import Path;assert b'febio_spec' in Path('input/case.feb').read_bytes()",
        ),
    )
    attempt = runner.start(bundle, _owner(), budget)
    managed = next(iter(runner._managed.values()))
    try:
        assert _finish(runner, attempt, _owner()).state is RunState.VALIDATING
        assert managed.process.returncode == 0 and managed.process.closed
    finally:
        _cleanup_process(managed.process)
        managed.stdout.close()
        managed.stderr.close()


@pytest.mark.parametrize("failure", ["missing", "tampered"])
def test_qualified_runtime_failure_is_rejected_before_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    import hashlib

    from febio_cae.adapters.febio import _native_qualification
    from febio_cae.domain import FileEntry, ToolIdentity
    from febio_cae.domain.canonical import canonical_bytes

    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    executable = runtime_root / "febio4.exe"
    executable_bytes = b"synthetic qualified executable"
    executable.write_bytes(executable_bytes)
    dll_bytes = {
        "dependency-a.dll": b"dependency-a",
        "dependency-b.dll": b"dependency-b",
    }
    if failure == "missing":
        (runtime_root / "dependency-a.dll").write_bytes(dll_bytes["dependency-a.dll"])
    else:
        (runtime_root / "dependency-a.dll").write_bytes(b"tampered")
        (runtime_root / "dependency-b.dll").write_bytes(dll_bytes["dependency-b.dll"])

    known_tool = ToolIdentity(
        "synthetic-qualified",
        "1",
        hashlib.sha256(executable_bytes).hexdigest(),
    )
    expected_runtime_files = tuple(
        _native_qualification._RuntimeFile(name, hashlib.sha256(content).hexdigest(), len(content))
        for name, content in (("febio4.exe", executable_bytes), *dll_bytes.items())
    )
    expected_files = [
        {"name": item.name, "digest": item.digest, "size_bytes": item.size_bytes}
        for item in expected_runtime_files
    ]
    descriptor = canonical_bytes(
        {
            "schema_version": "1",
            "kind": "febio-runtime-qualification",
            "tool": known_tool.to_dict(),
            "executable": expected_files[0],
            "dlls": expected_files[1:],
        }
    )
    monkeypatch.setattr(_native_qualification, "_QUALIFIED_TOOL", known_tool)
    monkeypatch.setattr(_native_qualification, "_QUALIFIED_DESCRIPTOR", descriptor)
    monkeypatch.setattr(_native_qualification, "_QUALIFIED_FILES", expected_runtime_files)

    runner, _, bundle, budget = _fixture(tmp_path)
    runtime_entry = FileEntry(
        "input/native-runtime.json",
        hashlib.sha256(descriptor).hexdigest(),
        len(descriptor),
        "native-runtime",
    )
    bundle = replace(
        bundle,
        tool=known_tool,
        files=(*bundle.files, runtime_entry),
        argv=(str(executable), *bundle.argv[1:]),
    )
    runner.bundle_store.stage(bundle.bundle_id, runtime_entry.logical_path, descriptor)

    with pytest.raises(PortError) as failure_record:
        runner.start(bundle, _owner(), budget)
    assert failure_record.value.category is PortErrorCategory.INTEGRITY
