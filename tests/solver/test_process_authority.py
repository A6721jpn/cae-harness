from __future__ import annotations

import contextlib
import ctypes
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

import febio_cae_harness.solver.process_authority as authority_module
import febio_cae_harness.solver.supervisor as supervisor_module
from febio_cae_harness.contracts import IntentContract
from febio_cae_harness.evidence import EvidenceStore
from febio_cae_harness.solver import headless as headless_module
from febio_cae_harness.solver.process_authority import ProcessAuthority, ProcessAuthorityError
from febio_cae_harness.solver.runtime import FebioRuntimeDiagnostic, probe_febio
from febio_cae_harness.solver.supervisor import SolverSupervisor, _ProcessMetadata
from febio_cae_harness.solver.types import (
    SolverLaunchCapability,
    SolverLaunchError,
    SolverOwnershipError,
)
from febio_cae_harness.workspace import AttemptWorkspace, ValidatedCaseWorkspace

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows process ordering")


class _FakeProcess:
    pid = 4242

    def __init__(self, events: list[str]) -> None:
        self._events = events
        self._return_code: int | None = None

    def poll(self) -> int | None:
        return self._return_code

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self._return_code = self._return_code if self._return_code is not None else -15
        return self._return_code

    def kill(self) -> None:
        self._events.append("process-kill")
        self._return_code = -9


class _FakeWindowsAuthority:
    def __init__(self, events: list[str]) -> None:
        self._events = events
        self._process: _FakeProcess | None = None
        self._context_digest = "f" * 64
        self.close = Mock(side_effect=lambda: self._events.append("close"))

    @property
    def claim(self) -> dict[str, object]:
        return {
            "kind": "windows-job",
            "attempt_binding": "test",
            "name": "test-job",
            "context_digest": self._context_digest,
            "root_pid": None if self._process is None else self._process.pid,
            "root_creation_identity": "windows:test",
        }

    def child_environment(self) -> dict[str, str]:
        return {}

    def child_handle(self) -> int:
        return 9876

    def bind(self, pid: int, expected_creation_identity: str | None = None) -> None:
        assert pid == _FakeProcess.pid
        assert expected_creation_identity == "windows:test"
        self._events.append("bind")

    def resume(self, pid: int) -> None:
        assert pid == _FakeProcess.pid
        self._events.append("resume")

    def terminate(self, pid: int, *, force: bool = False) -> None:
        assert pid == _FakeProcess.pid
        self._events.append("terminate")
        if self._process is not None:
            self._process._return_code = -15

    def drain(self) -> None:
        self._events.append("drain")


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
    code: str = "pass",
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


def _fake_popen(
    authority: _FakeWindowsAuthority, events: list[str], kwargs: dict[str, Any]
) -> _FakeProcess:
    creation_flags = int(kwargs["creationflags"])
    events.append("created-suspended" if creation_flags & 0x00000004 else "child-code-before-bind")
    process = _FakeProcess(events)
    authority._process = process
    return process


def _patch_fake_windows(
    monkeypatch: pytest.MonkeyPatch, authority: _FakeWindowsAuthority, events: list[str]
) -> None:
    monkeypatch.setattr(os, "name", "nt")

    def fake_create(
        attempt_root: Path,
        context_digest: str,
        *,
        root_pid: int | None = None,
        root_creation_identity: str | None = None,
    ) -> _FakeWindowsAuthority:
        del attempt_root
        del root_pid, root_creation_identity
        authority._context_digest = context_digest
        return authority

    monkeypatch.setattr(ProcessAuthority, "create", fake_create)
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *args, **kwargs: _fake_popen(authority, events, kwargs),
    )
    monkeypatch.setattr(
        supervisor_module,
        "_process_metadata",
        lambda pid: _ProcessMetadata(
            str(Path(sys.executable)),
            "windows:test",
            True,
            None,
            datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )


def test_windows_primary_process_is_bound_before_any_child_code_or_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    authority = _FakeWindowsAuthority(events)

    _patch_fake_windows(monkeypatch, authority, events)

    capability = _capability(tmp_path, monkeypatch)
    supervisor = SolverSupervisor(capability)
    supervisor.start()
    assert events[:3] == ["created-suspended", "bind", "resume"]


def test_windows_immediate_descendant_runs_only_after_primary_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = tmp_path / "descendant-ran.txt"
    descendant_code = (
        "from pathlib import Path; "
        f"Path({str(sentinel)!r}).write_text('ran', encoding='utf-8'); "
        "import time; time.sleep(30)"
    )
    child_code = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {descendant_code!r}]); "
        "time.sleep(30)"
    )
    capability = _capability(tmp_path, monkeypatch, child_code)
    binding_observations: list[bool] = []
    native_bind = authority_module._WindowsProcessAuthority.bind

    def observe_bind(
        authority: object, pid: int, expected_creation_identity: str | None = None
    ) -> None:
        binding_observations.append(sentinel.exists())
        native_bind(authority, pid, expected_creation_identity)  # type: ignore[arg-type]
        binding_observations.append(sentinel.exists())

    monkeypatch.setattr(authority_module._WindowsProcessAuthority, "bind", observe_bind)
    supervisor = SolverSupervisor(capability)
    supervisor.start()
    try:
        assert binding_observations == [False, False]
        deadline = time.monotonic() + 5.0
        content = ""
        while time.monotonic() < deadline:
            with contextlib.suppress(FileNotFoundError):
                content = sentinel.read_text(encoding="utf-8")
            if content == "ran":
                break
            time.sleep(0.02)
        assert content == "ran"
    finally:
        assert supervisor.cancel().cancelled


@pytest.mark.parametrize("failure", ("bind", "resume"))
def test_windows_native_failure_preserves_only_durable_authority_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    events: list[str] = []
    authority = _FakeWindowsAuthority(events)
    _patch_fake_windows(monkeypatch, authority, events)
    monkeypatch.setattr(
        authority,
        failure,
        Mock(side_effect=ProcessAuthorityError("bind failed")),
    )

    capability = _capability(tmp_path, monkeypatch)
    supervisor = SolverSupervisor(capability)
    with pytest.raises(SolverLaunchError):
        supervisor.start()

    assert ("process-kill" if failure == "bind" else "terminate") in events
    authority.close.assert_called_once_with()
    if failure == "bind":
        assert not supervisor.process_record_path.exists()
    else:
        assert not supervisor.process_record_path.exists()
        assert events.count("terminate") == 1
        with pytest.raises(SolverOwnershipError, match="invalid|missing|reconnectable|RUNNING"):
            SolverSupervisor.reconnect(capability)
    assert supervisor.result is None


class _ClaimKernel:
    def __init__(self) -> None:
        self.opened_names: list[str] = []
        self.opened_accesses: list[int] = []
        self.opened_pids: list[int] = []
        self.terminated: list[int] = []

    def OpenJobObjectW(self, access: int, inherit: bool, name: str) -> int:
        del inherit
        self.opened_names.append(name)
        self.opened_accesses.append(access)
        return 700

    def OpenProcess(self, access: int, inherit: bool, pid: int) -> int:
        del access, inherit
        self.opened_pids.append(pid)
        return pid + 1000

    def IsProcessInJob(self, process: int, job: int, result: Any) -> int:
        del process, job
        result._obj.value = 1
        return 1

    def TerminateJobObject(self, job: int, code: int) -> int:
        del job
        self.terminated.append(code)
        return 1

    def CloseHandle(self, handle: int) -> None:
        del handle


class _ChildHandleKernel:
    def __init__(
        self,
        *,
        duplicate_succeeds: bool,
        descriptor_succeeds: bool = True,
        free_succeeds: bool = True,
        create_succeeds: bool = True,
    ) -> None:
        self.duplicate_succeeds = duplicate_succeeds
        self.descriptor_succeeds = descriptor_succeeds
        self.free_succeeds = free_succeeds
        self.create_succeeds = create_succeeds
        self.closed: list[int] = []
        self.freed: list[int] = []
        self.security_sddl: list[str] = []
        self.create_security: list[tuple[int | None, int]] = []
        self.duplicate_calls: list[tuple[int, int, int, int, bool, int]] = []

    def ConvertStringSecurityDescriptorToSecurityDescriptorW(
        self,
        sddl: str,
        revision: int,
        descriptor: Any,
        size: object,
    ) -> int:
        del revision, size
        self.security_sddl.append(sddl)
        if self.descriptor_succeeds:
            descriptor._obj.value = 600
        return int(self.descriptor_succeeds)

    def CreateJobObjectW(self, security: Any, name: str) -> int:
        del name
        self.create_security.append(
            (security._obj.lpSecurityDescriptor, security._obj.bInheritHandle)
        )
        return 700 if self.create_succeeds else 0

    def GetCurrentProcess(self) -> int:
        return -1

    def DuplicateHandle(
        self,
        source_process: int,
        source_handle: int,
        target_process: int,
        target_handle: Any,
        access: int,
        inherit: bool,
        options: int,
    ) -> int:
        self.duplicate_calls.append(
            (source_process, source_handle, target_process, access, inherit, options)
        )
        if self.duplicate_succeeds:
            target_handle._obj.value = 701
        return int(self.duplicate_succeeds)

    def CloseHandle(self, handle: int) -> None:
        self.closed.append(handle)

    def LocalFree(self, descriptor: int) -> int | None:
        self.freed.append(descriptor)
        return None if self.free_succeeds else descriptor


def _native_handle_api() -> Any:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.GetHandleInformation.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    kernel.GetHandleInformation.restype = ctypes.c_int
    kernel.DuplicateHandle.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint32,
    ]
    kernel.DuplicateHandle.restype = ctypes.c_int
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.c_int
    kernel.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel.AssignProcessToJobObject.restype = ctypes.c_int
    kernel.QueryInformationJobObject.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    kernel.QueryInformationJobObject.restype = ctypes.c_int
    kernel.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.TerminateJobObject.restype = ctypes.c_int
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.OpenJobObjectW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
    kernel.OpenJobObjectW.restype = ctypes.c_void_p
    return kernel


def test_windows_child_job_handle_is_restricted_and_retains_job_lifetime(
    tmp_path: Path,
) -> None:
    digest = "e" * 64
    authority = authority_module._WindowsProcessAuthority.create(tmp_path, digest)
    kernel = _native_handle_api()
    full_handle = int(authority._handle)
    child_handle = authority.child_handle()
    assert child_handle is not None
    assert child_handle != full_handle

    inherit_flag = 0x00000001
    full_information = ctypes.c_uint32()
    child_information = ctypes.c_uint32()
    assert kernel.GetHandleInformation(ctypes.c_void_p(full_handle), ctypes.byref(full_information))
    assert kernel.GetHandleInformation(
        ctypes.c_void_p(child_handle), ctypes.byref(child_information)
    )
    assert not full_information.value & inherit_flag
    assert child_information.value & inherit_flag

    child_code = "\n".join(
        (
            "import ctypes, sys, time",
            "k = ctypes.WinDLL('kernel32', use_last_error=True)",
            "k.GetCurrentProcess.argtypes = []",
            "k.GetCurrentProcess.restype = ctypes.c_void_p",
            "k.DuplicateHandle.argtypes = [ctypes.c_void_p, ctypes.c_void_p, "
            "ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint32, "
            "ctypes.c_int, ctypes.c_uint32]",
            "k.DuplicateHandle.restype = ctypes.c_int",
            "k.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]",
            "k.AssignProcessToJobObject.restype = ctypes.c_int",
            "k.QueryInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, "
            "ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]",
            "k.QueryInformationJobObject.restype = ctypes.c_int",
            "k.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]",
            "k.TerminateJobObject.restype = ctypes.c_int",
            "k.OpenJobObjectW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]",
            "k.OpenJobObjectW.restype = ctypes.c_void_p",
            "k.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]",
            "k.OpenProcess.restype = ctypes.c_void_p",
            "k.CloseHandle.argtypes = [ctypes.c_void_p]",
            "k.CloseHandle.restype = ctypes.c_int",
            "current = k.GetCurrentProcess()",
            "handle = ctypes.c_void_p(int(sys.argv[1]))",
            "target = k.OpenProcess(0x101, 0, int(sys.argv[2]))",
            "assigned = k.AssignProcessToJobObject(handle, target)",
            "query_buffer = ctypes.create_string_buffer(64)",
            "queried = k.QueryInformationJobObject(handle, 1, query_buffer, 64, None)",
            "terminated = k.TerminateJobObject(handle, 1)",
            "duplicate_results = []",
            "for access in (0x1, 0x2, 0x4, 0x8, 0x10, 0x20, 0x10000, 0x40000, 0x80000, 0x100000):",
            "    duplicate = ctypes.c_void_p()",
            "    ok = k.DuplicateHandle(current, handle, current, "
            "ctypes.byref(duplicate), access, 0, 0)",
            "    duplicate_results.append(int(bool(ok)))",
            "    if duplicate: k.CloseHandle(duplicate)",
            "open_results = []",
            "for access in (0x1, 0x2, 0x4, 0x8, 0x10, 0x20, 0x10000, 0x40000, 0x80000, 0x100000):",
            "    opened = k.OpenJobObjectW(access, 0, sys.argv[3])",
            "    open_results.append(int(bool(opened)))",
            "    if opened: k.CloseHandle(opened)",
            "print('direct', int(bool(assigned)), int(bool(queried)), "
            "int(bool(terminated)), flush=True)",
            "print('duplicate', *duplicate_results, flush=True)",
            "print('open', *open_results, flush=True)",
            "if target: k.CloseHandle(target)",
            "time.sleep(30)",
        )
    )
    target = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    startup_info = subprocess.STARTUPINFO()
    startup_info.lpAttributeList = {"handle_list": [child_handle]}
    name = authority_module._windows_job_name(authority_module._attempt_binding(tmp_path), digest)
    helper = subprocess.Popen(
        [
            sys.executable,
            "-c",
            child_code,
            str(child_handle),
            str(target.pid),
            name,
        ],
        close_fds=True,
        startupinfo=startup_info,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    reopened: int | None = None
    try:
        assert helper.stdout is not None
        assert helper.stdout.readline().strip() == "direct 0 0 0"
        assert helper.stdout.readline().strip() == "duplicate 0 0 1 0 0 0 0 0 0 1"
        assert helper.stdout.readline().strip() == "open 0 0 1 0 0 0 0 0 0 1"
        assert target.poll() is None
        authority.close()
        authority.close()
        reopened = kernel.OpenJobObjectW(
            authority_module._WindowsProcessAuthority._JOB_OBJECT_QUERY
            | authority_module._WindowsProcessAuthority._SYNCHRONIZE,
            False,
            name,
        )
        assert reopened
    finally:
        if reopened:
            kernel.CloseHandle(reopened)
        helper.kill()
        helper.wait(timeout=5)
        if target.poll() is None:
            target.kill()
        target.wait(timeout=5)
        authority.close()


def test_windows_full_job_handle_is_closed_before_adversarial_child_resumes(
    tmp_path: Path,
) -> None:
    digest = "5" * 64
    authority = authority_module._WindowsProcessAuthority.create(tmp_path, digest)
    full_handle = int(authority._handle)
    child_handle = authority.child_handle()
    assert child_handle is not None
    child_code = "\n".join(
        (
            "import ctypes, sys, time",
            "k = ctypes.WinDLL('kernel32', use_last_error=True)",
            "k.GetCurrentProcess.argtypes = []",
            "k.GetCurrentProcess.restype = ctypes.c_void_p",
            "k.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]",
            "k.OpenProcess.restype = ctypes.c_void_p",
            "k.DuplicateHandle.argtypes = [ctypes.c_void_p, ctypes.c_void_p, "
            "ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint32, "
            "ctypes.c_int, ctypes.c_uint32]",
            "k.DuplicateHandle.restype = ctypes.c_int",
            "k.CloseHandle.argtypes = [ctypes.c_void_p]",
            "k.CloseHandle.restype = ctypes.c_int",
            "source = k.OpenProcess(0x40, 0, int(sys.argv[1]))",
            "current = k.GetCurrentProcess()",
            "results = []",
            "for access in (0x1, 0x2, 0x8, 0x10, 0x20, 0x10000, 0x40000, 0x80000):",
            "    duplicate = ctypes.c_void_p()",
            "    ok = source and k.DuplicateHandle(source, ctypes.c_void_p(int(sys.argv[2])), "
            "current, ctypes.byref(duplicate), access, 0, 0)",
            "    results.append(int(bool(ok)))",
            "    if duplicate: k.CloseHandle(duplicate)",
            "print('source', int(bool(source)), flush=True)",
            "print('rights', *results, flush=True)",
            "if source: k.CloseHandle(source)",
            "time.sleep(30)",
        )
    )
    startup_info = subprocess.STARTUPINFO()
    startup_info.lpAttributeList = {"handle_list": [child_handle]}
    root = subprocess.Popen(
        [sys.executable, "-c", child_code, str(os.getpid()), str(full_handle)],
        close_fds=True,
        creationflags=supervisor_module._CREATE_SUSPENDED,
        startupinfo=startup_info,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        identity = supervisor_module._process_metadata(root.pid).creation_identity
        authority.bind(root.pid, identity)
        authority.resume(root.pid)

        assert root.stdout is not None
        assert root.stdout.readline().strip() == "source 1"
        assert root.stdout.readline().strip() == "rights 0 0 0 0 0 0 0 0"
        assert authority._can_terminate_job is False
        assert unrelated.poll() is None
    finally:
        if root.poll() is None:
            with contextlib.suppress(ProcessAuthorityError):
                authority.terminate(root.pid)
        with contextlib.suppress(subprocess.TimeoutExpired):
            root.wait(timeout=5)
        if root.poll() is None:
            root.kill()
            root.wait(timeout=5)
        if unrelated.poll() is None:
            unrelated.kill()
        unrelated.wait(timeout=5)
        authority.close()


def test_windows_child_job_handle_duplicate_failure_closes_full_handle_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kernel = _ChildHandleKernel(duplicate_succeeds=False)
    monkeypatch.setattr(
        authority_module._WindowsProcessAuthority,
        "_kernel32",
        staticmethod(lambda: kernel),
    )
    monkeypatch.setattr(
        authority_module._WindowsProcessAuthority,
        "_advapi32",
        staticmethod(lambda: kernel),
    )

    with pytest.raises(ProcessAuthorityError, match="child|retention|duplicate"):
        authority_module._WindowsProcessAuthority.create(tmp_path, "f" * 64)

    assert kernel.closed == [700]
    assert kernel.freed == [600]
    assert kernel.security_sddl == [authority_module._WindowsProcessAuthority._JOB_DACL_SDDL]
    assert kernel.create_security == [(600, 0)]
    assert len(kernel.duplicate_calls) == 1
    assert kernel.duplicate_calls[0][4] is True


def test_windows_child_job_handle_close_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kernel = _ChildHandleKernel(duplicate_succeeds=True)
    monkeypatch.setattr(
        authority_module._WindowsProcessAuthority,
        "_kernel32",
        staticmethod(lambda: kernel),
    )
    monkeypatch.setattr(
        authority_module._WindowsProcessAuthority,
        "_advapi32",
        staticmethod(lambda: kernel),
    )

    authority = authority_module._WindowsProcessAuthority.create(tmp_path, "a" * 64)
    authority.close()
    authority.close()

    assert kernel.closed == [701, 700]
    assert kernel.freed == [600]
    assert kernel.create_security == [(600, 0)]
    assert len(kernel.duplicate_calls) == 1
    assert kernel.duplicate_calls[0][3] == authority_module._WindowsProcessAuthority._SYNCHRONIZE
    assert kernel.duplicate_calls[0][4] is True


@pytest.mark.parametrize(
    ("kernel", "message", "closed", "freed"),
    (
        (
            _ChildHandleKernel(
                duplicate_succeeds=True,
                descriptor_succeeds=False,
            ),
            "security descriptor",
            [],
            [],
        ),
        (
            _ChildHandleKernel(
                duplicate_succeeds=True,
                create_succeeds=False,
            ),
            "create process job",
            [],
            [600],
        ),
        (
            _ChildHandleKernel(
                duplicate_succeeds=True,
                free_succeeds=False,
            ),
            "release restricted job security descriptor",
            [700],
            [600],
        ),
    ),
)
def test_windows_child_job_handle_security_setup_failure_closes_every_owned_handle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kernel: _ChildHandleKernel,
    message: str,
    closed: list[int],
    freed: list[int],
) -> None:
    monkeypatch.setattr(
        authority_module._WindowsProcessAuthority,
        "_kernel32",
        staticmethod(lambda: kernel),
    )
    monkeypatch.setattr(
        authority_module._WindowsProcessAuthority,
        "_advapi32",
        staticmethod(lambda: kernel),
    )

    with pytest.raises(ProcessAuthorityError, match=message):
        authority_module._WindowsProcessAuthority.create(tmp_path, "6" * 64)

    assert kernel.closed == closed
    assert kernel.freed == freed
    assert kernel.duplicate_calls == []


def _windows_claim(root: Path, digest: str, name: str) -> dict[str, object]:
    return {
        "kind": "windows-job",
        "attempt_binding": authority_module._attempt_binding(root),
        "name": name,
        "context_digest": digest,
        "root_pid": 101,
        "root_creation_identity": "windows:1001",
    }


def test_windows_reconnect_rejects_unrelated_same_prefix_job_without_opening_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    digest = "a" * 64
    binding = authority_module._attempt_binding(tmp_path)
    prefix = f"Local\\febio-cae-{binding[:32]}-{digest[:32]}-"
    kernel = _ClaimKernel()
    monkeypatch.setattr(
        authority_module._WindowsProcessAuthority,
        "_kernel32",
        staticmethod(lambda: kernel),
    )

    with pytest.raises(ProcessAuthorityError, match="job name|claim"):
        ProcessAuthority.from_claim(
            tmp_path,
            _windows_claim(tmp_path, digest, prefix + "attacker-created"),
            digest,
        )

    assert kernel.opened_names == []
    assert kernel.terminated == []


def test_windows_reconnect_rejects_same_job_descendant_without_terminating_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    digest = "b" * 64
    kernel = _ClaimKernel()
    monkeypatch.setattr(
        authority_module._WindowsProcessAuthority,
        "_kernel32",
        staticmethod(lambda: kernel),
    )
    binding = authority_module._attempt_binding(tmp_path)
    name = f"Local\\febio-cae-{binding}-{digest}"
    claim = _windows_claim(tmp_path, digest, name)
    authority = ProcessAuthority.from_claim(tmp_path, claim, digest)

    with pytest.raises(ProcessAuthorityError, match="root|identity|PID"):
        authority.terminate(202)

    assert kernel.opened_pids == []
    assert kernel.terminated == []


def test_windows_reconnect_limited_authority_opens_query_and_synchronize_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    digest = "9" * 64
    kernel = _ClaimKernel()
    monkeypatch.setattr(
        authority_module._WindowsProcessAuthority,
        "_kernel32",
        staticmethod(lambda: kernel),
    )
    binding = authority_module._attempt_binding(tmp_path)
    name = f"Local\\febio-cae-{binding}-{digest}"

    authority = ProcessAuthority.from_claim(
        tmp_path,
        _windows_claim(tmp_path, digest, name),
        digest,
    )
    authority.close()

    assert kernel.opened_accesses == [
        authority_module._WindowsProcessAuthority._JOB_OBJECT_QUERY
        | authority_module._WindowsProcessAuthority._SYNCHRONIZE
    ]


def test_windows_reconnect_limited_authority_never_uses_job_wide_terminate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    digest = "8" * 64
    kernel = _ClaimKernel()
    authority = authority_module._WindowsProcessAuthority(
        tmp_path,
        name="Local\\febio-cae-limited-termination",
        handle=700,
        context_digest=digest,
        bound_pid=101,
        bound_creation_identity="windows:1001",
        can_terminate_job=False,
    )
    batches = iter(
        (
            ((101, "windows:1001"), (202, "windows:2002")),
            (),
        )
    )
    terminated: list[tuple[int, str]] = []
    monkeypatch.setattr(
        authority_module._WindowsProcessAuthority,
        "_kernel32",
        staticmethod(lambda: kernel),
    )
    monkeypatch.setattr(
        authority, "verify", lambda pid: setattr(authority, "_claim_verified", True)
    )
    monkeypatch.setattr(authority, "_job_members", lambda: next(batches))
    monkeypatch.setattr(
        authority,
        "_terminate_member",
        lambda pid, identity: terminated.append((pid, identity)),
    )

    authority.terminate(101)

    assert terminated == [(101, "windows:1001"), (202, "windows:2002")]
    assert kernel.terminated == []


def test_windows_reconnect_limited_authority_terminates_only_its_native_job_member(
    tmp_path: Path,
) -> None:
    digest = "7" * 64
    creator = authority_module._WindowsProcessAuthority.create(tmp_path, digest)
    child_handle = creator.child_handle()
    assert child_handle is not None
    startup_info = subprocess.STARTUPINFO()
    startup_info.lpAttributeList = {"handle_list": [child_handle]}
    root = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        close_fds=True,
        startupinfo=startup_info,
    )
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    limited: ProcessAuthority | None = None
    try:
        identity = supervisor_module._process_metadata(root.pid).creation_identity
        creator.bind(root.pid, identity)
        claim = creator.claim
        creator.close()
        limited = ProcessAuthority.from_claim(tmp_path, claim, digest)
        limited.verify(root.pid)

        limited.terminate(root.pid)

        root.wait(timeout=5)
        assert root.poll() is not None
        assert unrelated.poll() is None
    finally:
        if limited is not None:
            limited.close()
        creator.close()
        if root.poll() is None:
            root.kill()
        root.wait(timeout=5)
        if unrelated.poll() is None:
            unrelated.kill()
        unrelated.wait(timeout=5)


def test_windows_binding_rejects_creation_identity_changed_before_assignment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    digest = "c" * 64
    kernel = _ClaimKernel()
    monkeypatch.setattr(
        authority_module._WindowsProcessAuthority,
        "_kernel32",
        staticmethod(lambda: kernel),
    )
    authority = authority_module._WindowsProcessAuthority(
        tmp_path,
        name="Local\\febio-cae-binding-race",
        handle=700,
        context_digest=digest,
    )
    monkeypatch.setattr(
        authority,
        "_live_creation_identity",
        lambda pid, handle=None: "windows:replacement",
        raising=False,
    )

    with pytest.raises(ProcessAuthorityError, match="creation identity|identity"):
        authority.bind(101, "windows:original")

    assert kernel.opened_pids == [101]
    assert kernel.terminated == []


class _FakeProcFile:
    def __init__(self, pid: int, name: str) -> None:
        self.pid = pid
        self.name = name

    def read_bytes(self) -> bytes:
        if self.name == "environ":
            return b"FEBIO_CAE_HARNESS_AUTHORITY_CONTEXT=" + b"d" * 64 + b"\0"
        raise AssertionError(self.name)

    def read_text(self, encoding: str = "utf-8") -> str:
        del encoding
        if self.name == "stat":
            return f"({self.pid}) S " + " ".join("0" for _ in range(19))
        raise AssertionError(self.name)


class _FakeProcRoot:
    def is_dir(self) -> bool:
        return True

    def __truediv__(self, value: str) -> object:
        if value.isdigit():
            return _FakeProcRootForPid(int(value))
        raise AssertionError(value)


class _FakeProcRootForPid:
    def __init__(self, pid: int) -> None:
        self.pid = pid

    def __truediv__(self, value: str) -> _FakeProcFile:
        return _FakeProcFile(self.pid, value)


def test_posix_reconnect_rejects_simulated_session_substitution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    digest = "d" * 64
    real_path = authority_module.Path  # type: ignore[attr-defined]

    def fake_path(value: str | Path) -> object:
        return _FakeProcRoot() if str(value) == "/proc" else real_path(value)

    monkeypatch.setattr(authority_module.os, "name", "posix")  # type: ignore[attr-defined]
    monkeypatch.setattr(authority_module, "Path", fake_path)
    monkeypatch.setattr(authority_module.os, "getpgid", lambda pid: pid, raising=False)  # type: ignore[attr-defined]
    monkeypatch.setattr(authority_module.os, "getsid", lambda pid: pid, raising=False)  # type: ignore[attr-defined]
    monkeypatch.setattr(
        authority_module.os,  # type: ignore[attr-defined]
        "scandir",
        lambda path: iter((type("Entry", (), {"path": path + "/3"})(),)),
    )
    monkeypatch.setattr(authority_module.os.path, "exists", lambda path: True)  # type: ignore[attr-defined]
    monkeypatch.setattr(
        authority_module.os,  # type: ignore[attr-defined]
        "readlink",
        lambda path: "pipe:[123]",
    )

    claim = {
        "kind": "posix-pipe",
        "attempt_binding": authority_module._attempt_binding(tmp_path),
        "pipe_inode": 123,
        "context_digest": digest,
        "root_pid": 101,
        "root_creation_identity": "posix:1001",
    }
    authority = ProcessAuthority.from_claim(tmp_path, claim, digest)

    with pytest.raises(ProcessAuthorityError, match="root|identity|PID"):
        authority.verify(202)
