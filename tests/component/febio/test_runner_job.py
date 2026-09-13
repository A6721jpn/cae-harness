"""Bounded Windows ownership probes; no native solver or PID-based cleanup."""

from __future__ import annotations

import ctypes
import io
import json
import os
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from febio_cae.adapters.febio import _windows_job as windows_job_module
from febio_cae.adapters.febio import runner as runner_module
from febio_cae.domain import PortError, Quantity, RunState

from .runner_fixture import _compiled, _owner, _Ownership


def _job_type() -> Any:
    assert hasattr(runner_module, "WindowsJobProcess"), (
        "runner must own a pre-execution Windows Job, not rediscover descendant PIDs"
    )
    return runner_module.WindowsJobProcess


def _until(predicate: Any, seconds: float = 5) -> None:
    deadline = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < deadline, "bounded fixture observation expired"
        time.sleep(0.02)


def _current_affinity() -> int:
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.GetProcessAffinityMask.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_size_t),
        ctypes.POINTER(ctypes.c_size_t),
    ]
    api.GetProcessAffinityMask.restype = ctypes.c_int
    process_mask, system_mask = ctypes.c_size_t(), ctypes.c_size_t()
    assert api.GetProcessAffinityMask(
        ctypes.c_void_p(-1), ctypes.byref(process_mask), ctypes.byref(system_mask)
    )
    return int(process_mask.value)


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
    return runner, runner.start(
        replace(bundle, argv=(sys.executable, "-c", code)), _owner(), budget
    )


def _cleanup(runner: Any, attempt: Any) -> None:
    if runner._managed:
        managed = next(iter(runner._managed.values()))
        managed.process.terminate_tree()
        _until(
            lambda: managed.process.active_processes() == 0 and managed.process.poll() is not None
        )
        managed.process.close()
        managed.stdout.close()
        managed.stderr.close()


def test_root_exit_keeps_descendant_writer_owned_until_natural_drain(tmp_path: Path) -> None:
    child = (
        "from pathlib import Path; import time,os; Path('output/child.pid').write_text(str(os.getpid())); p=Path('output/writer'); "
        + ("[(p.open('ab').write(b'x'),time.sleep(.05)) for _ in range(50)]")
    )
    code = f"import subprocess,sys; subprocess.Popen([sys.executable,'-c',{child!r}])"
    runner, attempt = _start(tmp_path, code)
    managed = next(iter(runner._managed.values()))
    process = managed.process
    child_handle = None
    try:
        _until(lambda: process.poll() == 0)
        writer = managed.attempt_root / "output/writer"
        _until(writer.exists)
        child_handle = _hold_child(process, managed.attempt_root / "output/child.pid")
        current = runner.poll(attempt, _owner()).attempt
        assert current.state is RunState.DRAINING
        assert process.active_processes() >= 1
        before = writer.stat().st_size
        _until(lambda: writer.stat().st_size > before)
        _until(lambda: process.active_processes() == 0)
        assert process._win.WaitForSingleObject(child_handle, 0) == 0
        result = runner.reconcile(current, _owner()).attempt
        assert result.state is RunState.VALIDATING
        assert process.closed and not runner._managed
        print(
            "natural drain: root exit=0, owned child HANDLE live with writer growth, then child HANDLE signaled and job active=0; handles closed"
        )
    finally:
        _cleanup(runner, attempt)
        if child_handle is not None:
            process._win.CloseHandle(child_handle)


def _hold_child(process: Any, pid_file: Path) -> int:
    _until(lambda: pid_file.exists() and bool(pid_file.read_text()))
    handle = int(process._win.OpenProcess(0x100000 | 0x1000, False, int(pid_file.read_text())))
    # Bind the opened handle to the retained job before trusting it; never kill by PID.
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.IsProcessInJob.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    api.IsProcessInJob.restype = ctypes.c_int
    member = ctypes.c_int()
    try:
        assert api.IsProcessInJob(handle, process._job, ctypes.byref(member)) and member.value
        times = [ctypes.c_ulonglong() for _ in range(4)]
        assert process._api.GetProcessTimes(handle, *(ctypes.byref(t) for t in times))
        assert times[0].value >= process.creation_time > 0
        assert process._win.WaitForSingleObject(handle, 0) == 258
        print(f"held child creation FILETIME={times[0].value}; verified retained-job membership")
        return handle
    except BaseException:
        process._win.CloseHandle(handle)
        raise


def test_cancel_owned_tree_leaves_unrelated_sentinel_alive(tmp_path: Path) -> None:
    _job_type()
    sentinel = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(12)"])
    child = "from pathlib import Path;import time,os;Path('output/child.pid').write_text(str(os.getpid()));time.sleep(8)"
    code = f"import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',{child!r}]);time.sleep(8)"
    runner = attempt = None
    child_handle = None
    try:
        runner, attempt = _start(tmp_path, code)
        managed = next(iter(runner._managed.values()))
        process = managed.process
        child_handle = _hold_child(process, managed.attempt_root / "output/child.pid")
        assert process.active_processes() >= 2
        result = runner.cancel(attempt, _owner()).attempt
        assert result.state is RunState.CANCELLED
        assert process.closed and not runner._managed
        assert sentinel.poll() is None
        assert process._win.WaitForSingleObject(child_handle, 0) == 0
        print(
            "cancel: child HANDLE signaled, job confirmed zero/closed; unrelated held sentinel remains alive"
        )
    finally:
        if runner is not None:
            _cleanup(runner, attempt)
        if child_handle is not None:
            process._win.CloseHandle(child_handle)
        sentinel.terminate()  # Popen retains the actual Windows process HANDLE.
        sentinel.wait(timeout=3)


def test_accounting_failure_never_validates_then_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_type = _job_type()
    runner, attempt = _start(tmp_path, "pass")
    process = next(iter(runner._managed.values())).process
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
    code = (
        "import subprocess,sys;subprocess.Popen([sys.executable,'-c','import time;time.sleep(8)'])"
    )
    runner, attempt = _start(tmp_path, code, 0.4)
    process = next(iter(runner._managed.values())).process
    try:
        _until(lambda: process.poll() == 0)
        current = runner.poll(attempt, _owner()).attempt
        assert current.state is RunState.DRAINING
        time.sleep(0.45)
        result = runner.poll(current, _owner())
        assert result.attempt.state is RunState.FAILED
        assert "budget" in " ".join(result.diagnostics)
        assert process.closed and not runner._managed
    finally:
        _cleanup(runner, attempt)


def test_assignment_failure_never_executes_and_releases_handles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_type = _job_type()
    held: list[Any] = []

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
    print(
        "assignment failure: suspended root terminated by retained handle before resume; handles closed"
    )


def test_cpu_affinity_bounds_root_and_descendant_and_drains(tmp_path: Path) -> None:
    allowed = _current_affinity()
    if allowed.bit_count() < 2:
        pytest.skip("host process is limited to one CPU; subset bound is not observable")
    child = (
        "import ctypes,json,os,time;from pathlib import Path;"
        "api=ctypes.WinDLL('kernel32',use_last_error=True);"
        "api.GetProcessAffinityMask.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_size_t),"
        "ctypes.POINTER(ctypes.c_size_t)];api.GetProcessAffinityMask.restype=ctypes.c_int;"
        "p=ctypes.c_size_t();s=ctypes.c_size_t();"
        "assert api.GetProcessAffinityMask(ctypes.c_void_p(-1),ctypes.byref(p),ctypes.byref(s));"
        "report=Path('output/descendant-affinity.json');temporary=report.with_suffix('.tmp');"
        "temporary.write_text(json.dumps({'mask':p.value}));temporary.replace(report);\n"
        "deadline=time.monotonic()+15\n"
        "while not Path('output/release-descendant').exists():\n"
        "    if time.monotonic() >= deadline: raise TimeoutError('descendant release expired')\n"
        "    time.sleep(.01)\n"
    )
    code = (
        "import ctypes,json,subprocess,sys,time;from pathlib import Path;"
        "api=ctypes.WinDLL('kernel32',use_last_error=True);"
        "api.GetProcessAffinityMask.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_size_t),"
        "ctypes.POINTER(ctypes.c_size_t)];api.GetProcessAffinityMask.restype=ctypes.c_int;"
        "p=ctypes.c_size_t();s=ctypes.c_size_t();"
        "assert api.GetProcessAffinityMask(ctypes.c_void_p(-1),ctypes.byref(p),ctypes.byref(s));"
        "report=Path('output/root-affinity.json');temporary=report.with_suffix('.tmp');"
        "temporary.write_text(json.dumps({'mask':p.value}));temporary.replace(report);"
        f"subprocess.Popen([sys.executable,'-c',{child!r}])"
    )
    runner, attempt = _start(tmp_path, code, budget_seconds=20)
    managed = next(iter(runner._managed.values()))
    process = managed.process
    try:
        root_file = managed.attempt_root / "output/root-affinity.json"
        child_file = managed.attempt_root / "output/descendant-affinity.json"
        _until(lambda: root_file.exists() and child_file.exists())
        root_mask = json.loads(root_file.read_text())["mask"]
        child_mask = json.loads(child_file.read_text())["mask"]
        for observed in (root_mask, child_mask):
            assert observed and observed & ~allowed == 0 and observed.bit_count() <= 1
        _until(lambda: process.poll() == 0)
        draining = runner.poll(attempt, _owner()).attempt
        assert draining.state is RunState.DRAINING
        assert process.active_processes() >= 1
        (managed.attempt_root / "output/release-descendant").touch(exist_ok=False)
        _until(lambda: process.active_processes() == 0)
        assert runner.reconcile(draining, _owner()).attempt.state is RunState.VALIDATING
        assert process.closed and not runner._managed
        print(
            f"CPU bound: parent=0x{allowed:x}, root=0x{root_mask:x}, "
            f"descendant=0x{child_mask:x}; root exited, descendant drained, job closed"
        )
    finally:
        _cleanup(runner, attempt)


def test_unavailable_cpu_affinity_refuses_before_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queried: list[bool] = []
    original_kernel = windows_job_module._kernel

    def unavailable_kernel() -> Any:
        api = original_kernel()

        def unavailable(*args: Any) -> int:
            del args
            queried.append(True)
            return 0

        api.GetProcessAffinityMask = unavailable
        return api

    monkeypatch.setattr(windows_job_module, "_kernel", unavailable_kernel)
    marker = tmp_path / "must-not-execute"
    runner = None
    attempt = None
    try:
        with pytest.raises(PortError):
            runner, attempt = _start(
                tmp_path, f"from pathlib import Path;Path({str(marker)!r}).touch()"
            )
        assert queried == [True]
        assert not marker.exists()
    finally:
        if runner is not None:
            _cleanup(runner, attempt)


@pytest.mark.parametrize("value", [True, 0, -1, 1.5])
def test_cpu_workers_validation_precedes_windows_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: Any
) -> None:
    monkeypatch.setattr(
        windows_job_module,
        "_kernel",
        lambda: pytest.fail("invalid CPU allocation reached Windows setup"),
    )
    with pytest.raises(ValueError, match="cpu_workers"):
        windows_job_module.WindowsJobProcess(
            (sys.executable,), tmp_path, io.BytesIO(), io.BytesIO(), cpu_workers=value
        )
