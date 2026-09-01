"""Synthetic durability tests for the owned solver supervisor."""

from __future__ import annotations

import contextlib
import gc
import hashlib
import inspect
import json
import os
import stat
import subprocess
import sys
import weakref
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock

import pytest

import febio_cae_harness.solver.runtime as runtime_module
import febio_cae_harness.solver.supervisor as supervisor_module
from febio_cae_harness.contracts import IntentContract
from febio_cae_harness.evidence import EvidenceStore
from febio_cae_harness.solver import headless as headless_module
from febio_cae_harness.solver.log import LogValidation, LogValidator
from febio_cae_harness.solver.process_authority import (
    ProcessAuthority,
    ProcessAuthorityError,
    _WindowsProcessAuthority,
)
from febio_cae_harness.solver.runtime import (
    FebioRuntimeDiagnostic,
    _acquire_runtime_launch_claim,
    _windows_close_native_handle,
    _windows_close_owned_fd,
    _windows_convert_raw_handle,
    probe_febio,
)
from febio_cae_harness.solver.supervisor import SolverSupervisor
from febio_cae_harness.solver.types import (
    SolverConfigurationError,
    SolverLaunchCapability,
    SolverLaunchError,
    SolverLaunchSpec,
    SolverOwnershipError,
    SolverState,
    _json_digest,
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


def _issued_runtime(
    monkeypatch: pytest.MonkeyPatch, executable: Path | None = None
) -> FebioRuntimeDiagnostic:
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
        return probe_febio(Path(sys.executable) if executable is None else executable)


def _capability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    code: str,
    executable: Path | None = None,
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
    runtime = _issued_runtime(monkeypatch, executable)
    return headless_module._issue_launch_capability(
        attempt,
        intent,
        runtime,
        input_path,
        expected_steps=None,
        expected_final_time=None,
        timeout_seconds=None,
    )


def _runtime_copy(tmp_path: Path) -> Path:
    path = tmp_path / "runtime-copy"
    source = Path(sys.executable)
    path.write_bytes(source.read_bytes())
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _windows_process_handle_count() -> int:
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetProcessHandleCount.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    kernel32.GetProcessHandleCount.restype = ctypes.c_int
    count = ctypes.c_uint32()
    if not kernel32.GetProcessHandleCount(kernel32.GetCurrentProcess(), ctypes.byref(count)):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(count.value)


def _windows_event_wait_result(name: str) -> int:
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenEventW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.OpenEventW.restype = ctypes.c_void_p
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    handle = kernel32.OpenEventW(
        supervisor_module._WINDOWS_EVENT_RECONNECT_ACCESS,
        False,
        name,
    )
    assert handle
    try:
        return int(kernel32.WaitForSingleObject(handle, 0))
    finally:
        assert kernel32.CloseHandle(handle)


def _assert_windows_record_mutation_blocked(record_path: Path) -> Path:
    try:
        foreign_fd = os.open(os.fspath(record_path), os.O_WRONLY)
    except OSError:
        pass
    else:
        os.close(foreign_fd)
        pytest.fail("foreign write opened the owned process record")

    foreign_path = record_path.with_name(f"{record_path.name}.foreign")
    foreign_path.write_bytes(b'{"foreign": true}')
    try:
        os.replace(os.fspath(foreign_path), os.fspath(record_path))
    except OSError:
        pass
    else:
        pytest.fail("foreign replacement changed the owned process record")
    try:
        os.unlink(os.fspath(record_path))
    except OSError:
        pass
    else:
        pytest.fail("foreign deletion removed the owned process record")
    return foreign_path


def _suspend_windows_primary_thread(authority: ProcessAuthority, pid: int) -> None:
    thread_id = authority.claim.get("root_thread_id")
    assert isinstance(thread_id, int) and thread_id > 0
    kernel32 = _WindowsProcessAuthority._kernel32()
    thread = kernel32.OpenThread(
        _WindowsProcessAuthority._THREAD_SUSPEND_RESUME
        | _WindowsProcessAuthority._THREAD_QUERY_LIMITED_INFORMATION,
        False,
        thread_id,
    )
    assert thread
    try:
        previous_count = kernel32.SuspendThread(thread)
        assert previous_count != _WindowsProcessAuthority._STILL_SUSPENDED
    finally:
        kernel32.CloseHandle(thread)


def _prepare_windows_resume_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    resume_before_crash: bool,
) -> tuple[
    SolverLaunchCapability,
    SolverSupervisor,
    subprocess.Popen[bytes],
    ProcessAuthority,
    dict[str, object],
    dict[str, object],
    Path,
]:
    if os.name != "nt":
        pytest.fail("required Windows resume recovery test executed on a non-Windows host")
    capability = _capability(tmp_path, monkeypatch, code="import time; time.sleep(30)")
    supervisor = SolverSupervisor(capability)
    original_resume = _WindowsProcessAuthority.resume
    monkeypatch.setattr(supervisor, "_delete_resume_transaction", lambda: ())
    if not resume_before_crash:
        monkeypatch.setattr(_WindowsProcessAuthority, "resume", lambda _instance, _pid: None)
    supervisor.start()
    if not resume_before_crash:
        monkeypatch.setattr(_WindowsProcessAuthority, "resume", original_resume)
    process = cast(subprocess.Popen[bytes], supervisor._process)
    authority = supervisor._process_authority
    assert authority is not None
    record_path = Path(os.fspath(supervisor.process_record_path))
    record_claim = supervisor._process_record_claim
    assert record_claim is not None and record_claim.handle is not None
    transaction_claim = supervisor._resume_transaction_claim
    assert transaction_claim is not None and transaction_claim.handle is not None
    running_record = json.loads(
        supervisor_module._read_record_fd(record_claim.handle).decode("utf-8")
    )
    transaction = json.loads(
        supervisor_module._read_record_fd(transaction_claim.handle).decode("utf-8")
    )
    assert isinstance(running_record, dict)
    assert isinstance(transaction, dict)
    assert running_record["state"] == SolverState.RUNNING.value
    bound_record = transaction.get("bound_record")
    transaction_running_record = transaction.get("running_record")
    assert isinstance(bound_record, dict)
    assert isinstance(transaction_running_record, dict)
    assert transaction_running_record == running_record
    if not resume_before_crash:
        supervisor._write_process_record(bound_record)
        assert bound_record["state"] == "BOUND_SUSPENDED"

    # Detach every coordinator-owned filesystem and process-authority handle;
    # the Popen object is retained only so the test can prove the child survives.
    resume_claim = supervisor._resume_transaction_claim
    if resume_claim is not None:
        supervisor._test_resume_transaction_claim = resume_claim  # type: ignore[attr-defined]
    supervisor.__del__()
    return (
        capability,
        supervisor,
        process,
        authority,
        cast(dict[str, object], bound_record),
        cast(dict[str, object], transaction_running_record),
        record_path,
    )


def _cleanup_windows_resume_recovery(
    capability: SolverLaunchCapability,
    supervisor: SolverSupervisor,
    process: subprocess.Popen[bytes],
    bound_record: dict[str, object],
    record_path: Path,
) -> None:
    if process.poll() is None:
        cleanup_authority: ProcessAuthority | None = None
        try:
            cleanup_authority = ProcessAuthority.from_claim(
                capability.spec.attempt_root,
                bound_record["process_authority"],
                supervisor._launch_context_digest,
            )
            cleanup_authority.terminate(process.pid)
        except BaseException:
            with contextlib.suppress(BaseException):
                process.kill()
        with contextlib.suppress(BaseException):
            process.wait(timeout=5.0)
        if cleanup_authority is not None:
            with contextlib.suppress(BaseException):
                cleanup_authority.close()
    for path in (record_path, record_path.with_name(".process.json.resume")):
        with contextlib.suppress(OSError):
            path.unlink()
    resume_claim = getattr(supervisor, "_test_resume_transaction_claim", None)
    if resume_claim is not None:
        with contextlib.suppress(BaseException):
            supervisor_module._windows_close_resume_event_handles(resume_claim)
        if resume_claim.handle is not None or resume_claim.native_handle is not None:
            with contextlib.suppress(BaseException):
                _windows_close_owned_fd(
                    resume_claim,
                    "handle",
                    "native_handle",
                )
    supervisor_module._drain_durable_cleanup()


def test_windows_resume_transaction_replacement_after_creator_close_rejects_before_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A byte-identical replacement journal is not crash-recovery authority."""

    if os.name != "nt":
        pytest.fail("required Windows resume replacement test executed on a non-Windows host")

    (
        capability,
        supervisor,
        process,
        authority,
        bound_record,
        _running_record,
        record_path,
    ) = _prepare_windows_resume_recovery(tmp_path, monkeypatch, resume_before_crash=False)
    transaction_path = record_path.with_name(".process.json.resume")
    replacement_path = transaction_path.with_name("resume-replacement")
    resume_calls: list[int] = []
    original_resume = _WindowsProcessAuthority.resume

    def track_resume(instance: _WindowsProcessAuthority, pid: int) -> None:
        resume_calls.append(pid)
        original_resume(instance, pid)

    monkeypatch.setattr(_WindowsProcessAuthority, "resume", track_resume)
    try:
        replacement_path.write_bytes(transaction_path.read_bytes())
        os.replace(os.fspath(replacement_path), os.fspath(transaction_path))

        with pytest.raises(SolverOwnershipError, match="transaction|identity|authority|event"):
            SolverSupervisor.reconnect(capability)

        assert resume_calls == []
        assert process.poll() is None
        assert process.returncode is None
        assert json.loads(record_path.read_text(encoding="utf-8"))["state"] == "BOUND_SUSPENDED"
    finally:
        _cleanup_windows_resume_recovery(capability, supervisor, process, bound_record, record_path)


def test_windows_reconnect_rejects_self_consistent_rewritten_journal_and_fabricated_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A replacement journal and attacker-signaled event cannot authorize resume."""

    if os.name != "nt":
        pytest.fail("required Windows resume authority test executed on a non-Windows host")

    (
        capability,
        supervisor,
        process,
        _authority,
        bound_record,
        _running_record,
        record_path,
    ) = _prepare_windows_resume_recovery(tmp_path, monkeypatch, resume_before_crash=False)
    transaction_path = record_path.with_name(".process.json.resume")
    replacement_path = transaction_path.with_name("resume-replacement")
    helper: subprocess.Popen[str] | None = None
    resume_calls: list[int] = []
    original_resume = _WindowsProcessAuthority.resume

    def track_resume(instance: _WindowsProcessAuthority, pid: int) -> None:
        resume_calls.append(pid)
        original_resume(instance, pid)

    monkeypatch.setattr(_WindowsProcessAuthority, "resume", track_resume)
    helper_code = "\n".join(
        (
            "import ctypes, sys, time",
            "k = ctypes.WinDLL('kernel32', use_last_error=True)",
            "k.CreateEventW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, "
            "ctypes.c_wchar_p]",
            "k.CreateEventW.restype = ctypes.c_void_p",
            "k.SetEvent.argtypes = [ctypes.c_void_p]",
            "k.SetEvent.restype = ctypes.c_int",
            "k.CloseHandle.argtypes = [ctypes.c_void_p]",
            "k.CloseHandle.restype = ctypes.c_int",
            "event = k.CreateEventW(None, 0, 1, sys.argv[1])",
            "print('event', int(bool(event)), flush=True)",
            "print('set', int(bool(event and k.SetEvent(event))), flush=True)",
            "time.sleep(30)",
        )
    )
    try:
        transaction = json.loads(transaction_path.read_text(encoding="utf-8"))
        assert isinstance(transaction, dict)
        replacement_path.write_bytes(transaction_path.read_bytes())
        replacement_identity = replacement_path.stat()
        replacement_name = supervisor_module._windows_resume_event_name(
            capability.spec.attempt_root,
            supervisor._launch_context_digest,
            int(replacement_identity.st_dev),
            int(replacement_identity.st_ino),
            "d" * 32,
        )
        transaction["journal_identity"] = {
            "device": int(replacement_identity.st_dev),
            "inode": int(replacement_identity.st_ino),
        }
        transaction["event_name"] = replacement_name
        replacement_path.write_bytes(
            json.dumps(transaction, indent=2, sort_keys=True).encode("utf-8")
        )
        os.replace(os.fspath(replacement_path), os.fspath(transaction_path))

        helper = subprocess.Popen(
            [sys.executable, "-c", helper_code, replacement_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert helper.stdout is not None
        assert helper.stdout.readline().strip() == "event 1"
        assert helper.stdout.readline().strip() == "set 1"

        with pytest.raises(SolverOwnershipError, match="transaction|identity|authority|event"):
            SolverSupervisor.reconnect(capability)

        assert resume_calls == []
        assert process.poll() is None
        assert process.returncode is None
        assert json.loads(record_path.read_text(encoding="utf-8"))["state"] == "BOUND_SUSPENDED"
    finally:
        if helper is not None:
            try:
                with contextlib.suppress(BaseException):
                    if helper.poll() is None:
                        helper.kill()
                with contextlib.suppress(BaseException):
                    helper.communicate(timeout=5.0)
            finally:
                for stream in (helper.stdout, helper.stderr):
                    if stream is not None:
                        with contextlib.suppress(BaseException):
                            stream.close()
        _cleanup_windows_resume_recovery(capability, supervisor, process, bound_record, record_path)


def test_windows_reconnect_rejects_forged_inherited_event_after_supervisor_loss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A signaled replacement Event cannot stand in for the child-inherited Event."""

    if os.name != "nt":
        pytest.fail(
            "required Windows inherited-event authority test executed on a non-Windows host"
        )

    marker = tmp_path / "child-started"
    capability = _capability(
        tmp_path,
        monkeypatch,
        code=(
            f"from pathlib import Path; Path({str(marker)!r}).write_text('started'); "
            "import time; time.sleep(30)"
        ),
    )
    supervisor = SolverSupervisor(capability)
    original_resume = _WindowsProcessAuthority.resume
    original_clear_child_handle = supervisor._clear_resume_transaction_child_handle
    inherited_child_handles: list[int] = []

    def capture_child_handle() -> None:
        claim = supervisor._resume_transaction_claim
        if claim is not None and claim.child_handle is not None:
            inherited_child_handles.append(claim.child_handle)
        original_clear_child_handle()

    monkeypatch.setattr(supervisor, "_signal_windows_resume_transaction", lambda: None)
    monkeypatch.setattr(supervisor, "_delete_resume_transaction", lambda: ())
    monkeypatch.setattr(supervisor, "_clear_resume_transaction_child_handle", capture_child_handle)
    monkeypatch.setattr(_WindowsProcessAuthority, "resume", lambda _instance, _pid: None)

    process: subprocess.Popen[bytes] | None = None
    reconnected: SolverSupervisor | None = None
    alternate_handle: int | None = None
    original_record: dict[str, object] = {}
    record_path = Path(os.fspath(supervisor.process_record_path))
    transaction_path = record_path.with_name(".process.json.resume")
    try:
        supervisor.start()
        process = cast(subprocess.Popen[bytes], supervisor._process)
        assert process.poll() is None
        assert not marker.exists()
        assert len(inherited_child_handles) == 1
        inherited_child_handle = inherited_child_handles[0]
        transaction_claim = supervisor._resume_transaction_claim
        assert transaction_claim is not None
        original_event_name = transaction_claim.event_name
        assert original_event_name
        record_claim = supervisor._process_record_claim
        assert record_claim is not None and record_claim.handle is not None
        assert transaction_claim.handle is not None
        original_record = json.loads(
            supervisor_module._read_record_fd(record_claim.handle).decode("utf-8")
        )
        original_transaction = json.loads(
            supervisor_module._read_record_fd(transaction_claim.handle).decode("utf-8")
        )
        assert isinstance(original_record, dict)
        assert isinstance(original_transaction, dict)

        monkeypatch.setattr(_WindowsProcessAuthority, "resume", original_resume)
        supervisor.__del__()
        assert process.poll() is None
        assert not marker.exists()
        assert (
            _windows_event_wait_result(original_event_name)
            == supervisor_module._WINDOWS_WAIT_TIMEOUT
        )

        replacement_record_path = record_path.with_name("process-replacement")
        replacement_journal_path = transaction_path.with_name("resume-replacement")
        replacement_record_path.write_bytes(b"placeholder")
        replacement_record_identity = replacement_record_path.stat()
        replacement_journal_path.write_bytes(b"placeholder")
        replacement_journal_identity = replacement_journal_path.stat()
        forged_token = "d" * 32
        forged_event_name = supervisor_module._windows_resume_event_name(
            capability.spec.attempt_root,
            supervisor._launch_context_digest,
            int(replacement_journal_identity.st_dev),
            int(replacement_journal_identity.st_ino),
            forged_token,
        )
        alternate_handle = supervisor_module._windows_create_resume_event(forged_event_name)
        supervisor_module._windows_signal_resume_event(alternate_handle)
        forged_binding = {
            "journal_identity": {
                "device": int(replacement_journal_identity.st_dev),
                "inode": int(replacement_journal_identity.st_ino),
            },
            "event_name": forged_event_name,
            "event_token": forged_token,
            "child_handle": inherited_child_handle,
        }
        forged_record = dict(original_record)
        forged_record["resume_transaction"] = forged_binding
        forged_bound_record = dict(cast(dict[str, object], original_transaction["bound_record"]))
        forged_bound_record["resume_transaction"] = forged_binding
        forged_running_record = dict(
            cast(dict[str, object], original_transaction["running_record"])
        )
        forged_running_record["resume_transaction"] = forged_binding
        forged_transaction = dict(original_transaction)
        forged_transaction.update(
            {
                "event_name": forged_event_name,
                "event_token": forged_token,
                "journal_identity": forged_binding["journal_identity"],
                "record_identity": {
                    "device": int(replacement_record_identity.st_dev),
                    "inode": int(replacement_record_identity.st_ino),
                },
                "child_handle": inherited_child_handle,
                "bound_record": forged_bound_record,
                "running_record": forged_running_record,
                "bound_digest": _json_digest(forged_bound_record),
                "running_digest": _json_digest(forged_running_record),
            }
        )
        replacement_record_path.write_bytes(
            json.dumps(forged_record, indent=2, sort_keys=True).encode("utf-8")
        )
        replacement_journal_path.write_bytes(
            json.dumps(forged_transaction, indent=2, sort_keys=True).encode("utf-8")
        )
        os.replace(os.fspath(replacement_record_path), os.fspath(record_path))
        os.replace(os.fspath(replacement_journal_path), os.fspath(transaction_path))

        handles_before_reconnect = _windows_process_handle_count()
        with pytest.raises(SolverOwnershipError, match="event|authority|identity|transaction"):
            reconnected = SolverSupervisor.reconnect(capability)
        assert _windows_process_handle_count() == handles_before_reconnect

        assert process.poll() is None
        assert process.returncode is None
        assert not marker.exists()
    finally:
        if reconnected is not None:
            with contextlib.suppress(BaseException):
                reconnected.cancel()
        if alternate_handle is not None:
            with contextlib.suppress(BaseException):
                _windows_close_native_handle(alternate_handle)
        if process is not None and process.poll() is None:
            with contextlib.suppress(BaseException):
                cleanup_authority = ProcessAuthority.from_claim(
                    capability.spec.attempt_root,
                    original_record["process_authority"],
                    supervisor._launch_context_digest,
                )
                cleanup_authority.terminate(process.pid)
                cleanup_authority.close()
            with contextlib.suppress(BaseException):
                process.wait(timeout=5.0)
        for path in (
            record_path,
            transaction_path,
            record_path.with_name("process-replacement"),
            transaction_path.with_name("resume-replacement"),
        ):
            with contextlib.suppress(OSError):
                path.unlink()
        supervisor_module._drain_durable_cleanup()


def test_windows_resume_event_dacl_blocks_external_mutation_and_setevent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An external process can query/wait on the event but cannot mutate it."""

    if os.name != "nt":
        pytest.fail("required Windows resume-event DACL test executed on a non-Windows host")

    (
        capability,
        supervisor,
        process,
        _authority,
        bound_record,
        _running_record,
        record_path,
    ) = _prepare_windows_resume_recovery(tmp_path, monkeypatch, resume_before_crash=False)
    helper: subprocess.Popen[str] | None = None
    resume_claim = cast(Any, supervisor._test_resume_transaction_claim)  # type: ignore[attr-defined]
    event_name = resume_claim.event_name
    helper_code = "\n".join(
        (
            "import ctypes, sys, time",
            "k = ctypes.WinDLL('kernel32', use_last_error=True)",
            "k.OpenEventW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]",
            "k.OpenEventW.restype = ctypes.c_void_p",
            "k.SetEvent.argtypes = [ctypes.c_void_p]",
            "k.SetEvent.restype = ctypes.c_int",
            "k.CloseHandle.argtypes = [ctypes.c_void_p]",
            "k.CloseHandle.restype = ctypes.c_int",
            "rights = (0x0002, 0x00040000, 0x00080000, 0x00010000, 0x001F0003)",
            "opened = []",
            "set_results = []",
            "for access in rights:",
            "    handle = k.OpenEventW(access, 0, sys.argv[1])",
            "    opened.append(int(bool(handle)))",
            "    set_results.append(int(bool(handle and k.SetEvent(handle))))",
            "    if handle: k.CloseHandle(handle)",
            "print('rights', *opened, flush=True)",
            "print('set', *set_results, flush=True)",
            "time.sleep(30)",
        )
    )
    try:
        helper = subprocess.Popen(
            [sys.executable, "-c", helper_code, event_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert helper.stdout is not None
        assert helper.stdout.readline().strip() == "rights 0 0 0 0 0"
        assert helper.stdout.readline().strip() == "set 0 0 0 0 0"
        assert supervisor_module._WINDOWS_EVENT_DACL_SDDL.count("0x") >= 2
        denied = (
            _WindowsProcessAuthority._DELETE
            | _WindowsProcessAuthority._WRITE_DAC
            | _WindowsProcessAuthority._WRITE_OWNER
            | supervisor_module._WINDOWS_EVENT_MODIFY_STATE
        )
        assert f"0x{denied:08X}" in supervisor_module._WINDOWS_EVENT_DACL_SDDL
    finally:
        if helper is not None:
            try:
                with contextlib.suppress(BaseException):
                    if helper.poll() is None:
                        helper.kill()
                with contextlib.suppress(BaseException):
                    helper.communicate(timeout=5.0)
            finally:
                for stream in (helper.stdout, helper.stderr):
                    if stream is not None:
                        with contextlib.suppress(BaseException):
                            stream.close()
        _cleanup_windows_resume_recovery(capability, supervisor, process, bound_record, record_path)


def test_windows_external_wait_preserves_resume_event_for_reconnect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A permitted external wait must not consume crash-recovery authority."""

    if os.name != "nt":
        pytest.fail("required Windows resume-event durability test executed on a non-Windows host")

    (
        capability,
        supervisor,
        process,
        _authority,
        bound_record,
        _running_record,
        record_path,
    ) = _prepare_windows_resume_recovery(tmp_path, monkeypatch, resume_before_crash=False)
    transaction_path = record_path.with_name(".process.json.resume")
    resume_claim = cast(Any, supervisor._test_resume_transaction_claim)  # type: ignore[attr-defined]
    event_name = resume_claim.event_name
    record_before = record_path.read_bytes()
    transaction_before = transaction_path.read_bytes()
    helper_code = "\n".join(
        (
            "import ctypes, sys",
            "k = ctypes.WinDLL('kernel32', use_last_error=True)",
            "k.OpenEventW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]",
            "k.OpenEventW.restype = ctypes.c_void_p",
            "k.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]",
            "k.WaitForSingleObject.restype = ctypes.c_uint32",
            "k.CloseHandle.argtypes = [ctypes.c_void_p]",
            "k.CloseHandle.restype = ctypes.c_int",
            f"access = {supervisor_module._WINDOWS_EVENT_RECONNECT_ACCESS}",
            "print('access', hex(access), flush=True)",
            "handle = k.OpenEventW(access, 0, sys.argv[1])",
            "print('opened', int(bool(handle)), flush=True)",
            "if not handle: raise SystemExit(2)",
            "wait_result = k.WaitForSingleObject(handle, 0)",
            "print('wait', hex(int(wait_result)), flush=True)",
            "if wait_result != 0: raise SystemExit(3)",
            "if not k.CloseHandle(handle): raise SystemExit(4)",
        )
    )
    reconnected: SolverSupervisor | None = None
    try:
        helper = subprocess.run(
            [sys.executable, "-c", helper_code, event_name],
            capture_output=True,
            text=True,
            timeout=10.0,
            check=False,
        )
        assert helper.returncode == 0, helper.stderr
        assert helper.stdout.splitlines() == [
            f"access {supervisor_module._WINDOWS_EVENT_RECONNECT_ACCESS:#x}",
            "opened 1",
            "wait 0x0",
        ]
        assert record_path.read_bytes() == record_before
        assert transaction_path.read_bytes() == transaction_before
        assert process.poll() is None
        assert process.returncode is None

        reconnected = SolverSupervisor.reconnect(capability)
        assert reconnected.state is SolverState.RUNNING
        assert reconnected.pid == process.pid
        assert reconnected.poll() is None
        assert process.returncode is None
        reconnected_claim = reconnected._process_record_claim
        assert reconnected_claim is not None and reconnected_claim.handle is not None
        assert (
            json.loads(supervisor_module._read_record_fd(reconnected_claim.handle).decode("utf-8"))[
                "state"
            ]
            == SolverState.RUNNING.value
        )
    finally:
        if reconnected is not None:
            with contextlib.suppress(BaseException):
                reconnected.cancel()
        _cleanup_windows_resume_recovery(
            capability,
            supervisor,
            process,
            bound_record,
            record_path,
        )


def test_windows_active_finalizer_closes_only_own_process_authority_and_reconnects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finalization releases this supervisor while preserving reconnect authority."""

    if os.name != "nt":
        pytest.fail("required Windows active finalizer test executed on a non-Windows host")

    capability = _capability(tmp_path, monkeypatch, code="import time; time.sleep(30)")
    supervisor = SolverSupervisor(capability)
    supervisor.start()
    process = cast(subprocess.Popen[bytes], supervisor._process)
    authority = supervisor._process_authority
    claim = supervisor._process_record_claim
    assert authority is not None
    assert isinstance(authority, _WindowsProcessAuthority)
    assert claim is not None and claim.handle is not None
    record_fd = claim.handle
    record_path = Path(os.fspath(claim.path))
    record_content = supervisor_module._read_record_fd(record_fd)
    close_calls: list[str] = []
    drain_calls: list[str] = []
    terminate_calls: list[int] = []
    reap_calls: list[str] = []
    original_close = authority.close

    def track_close() -> None:
        close_calls.append("close")
        original_close()

    def forbidden_drain() -> None:
        drain_calls.append("drain")
        raise AssertionError("finalization drained the live solver")

    def forbidden_terminate(pid: int, *, force: bool = False) -> None:
        del force
        terminate_calls.append(pid)
        raise AssertionError("finalization terminated the live solver")

    def forbidden_reap(*args: object, **kwargs: object) -> object:
        del args, kwargs
        reap_calls.append("wait")
        raise AssertionError("finalization reaped the live solver")

    monkeypatch.setattr(authority, "close", track_close)
    monkeypatch.setattr(authority, "drain", forbidden_drain)
    monkeypatch.setattr(authority, "terminate", forbidden_terminate)
    monkeypatch.setattr(process, "wait", forbidden_reap)
    reconnected: SolverSupervisor | None = None
    try:
        assert supervisor.state is SolverState.RUNNING
        supervisor.__del__()
        assert supervisor.state is SolverState.RUNNING
        assert close_calls == ["close"]
        assert drain_calls == []
        assert terminate_calls == []
        assert reap_calls == []
        assert process.poll() is None
        assert process.returncode is None
        assert authority._handle is None
        assert authority._child_handle is None
        assert claim.handle is None
        with pytest.raises(OSError):
            os.fstat(record_fd)
        assert supervisor._filesystem_authority is None
        assert supervisor._process_record_claim is None
        assert supervisor_module._windows_record_claim_key(record_path) not in (
            supervisor_module._WINDOWS_RECORD_CLAIMS
        )
        assert record_path.read_bytes() == record_content

        supervisor_ref = weakref.ref(supervisor)
        del supervisor
        for _ in range(3):
            gc.collect()
            if supervisor_ref() is None:
                break
        assert supervisor_ref() is None

        reconnected = SolverSupervisor.reconnect(capability)
        assert reconnected.state is SolverState.RUNNING
        assert reconnected.process_id == process.pid
        reconnected_claim = reconnected._process_record_claim
        assert reconnected_claim is not None and reconnected_claim.handle is not None
        assert supervisor_module._read_record_fd(reconnected_claim.handle) == record_content
        assert process.poll() is None
    finally:
        if reconnected is not None:
            with contextlib.suppress(BaseException):
                reconnected.cancel()
        elif process.poll() is None:
            with contextlib.suppress(BaseException):
                process.kill()
            with contextlib.suppress(BaseException):
                process.wait(timeout=5.0)
        with contextlib.suppress(BaseException):
            authority.close()
        with contextlib.suppress(OSError):
            record_path.unlink()


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
        context = record["launch_context"]
        assert isinstance(context, dict)
        assert context["input_path"] == os.path.normcase(
            os.path.realpath(os.path.abspath(os.fspath(spec.input_path)))
        )
        assert context["input_sha256"] == hashlib.sha256(spec.input_path.read_bytes()).hexdigest()
        assert context["runtime_path"] == os.path.normcase(
            os.path.realpath(os.path.abspath(os.fspath(spec.executable)))
        )
        runtime = object.__getattribute__(capability, "_runtime_diagnostic")
        assert context["runtime_sha256"] == runtime.sha256
        assert context["runtime_version"] == runtime.version
        assert context["argv"] == list(spec.command)
        assert context["cwd"] == os.path.normcase(
            os.path.realpath(os.path.abspath(os.fspath(spec.cwd)))
        )
        assert context["output_paths"] == {
            "log": os.path.normcase(
                os.path.realpath(os.path.abspath(os.fspath(spec.expected_outputs.log_path)))
            ),
            "xplt": os.path.normcase(
                os.path.realpath(os.path.abspath(os.fspath(spec.expected_outputs.xplt_path)))
            ),
        }
        assert context["timeout_seconds"] is None
        assert context["expected_steps"] is None
        assert context["expected_final_time"] is None
        assert context["requested_fields"] == []
        assert isinstance(context["environment_digest"], str)
        assert record["launch_context_digest"]
        process_authority = record["process_authority"]
        assert isinstance(process_authority, dict)
        assert process_authority["context_digest"] == record["launch_context_digest"]
        assert process_authority["root_pid"] == record["pid"]
        assert process_authority["root_creation_identity"] == record["process_creation_identity"]
        assert "FEBIO_CAE_HARNESS_AUTHORITY_CONTEXT" not in record_path.read_text(encoding="utf-8")
    finally:
        supervisor.cancel()

    assert supervisor.state is SolverState.CANCELLED


def test_posix_input_lease_uses_held_exact_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "posix":
        pytest.skip("POSIX-only exact-input descriptor lease")

    capability = _capability(tmp_path, monkeypatch, code="exact input bytes")
    supervisor = SolverSupervisor(capability)
    try:
        supervisor._acquire_filesystem_authority()
        supervisor._acquire_input_lease()
        lease = supervisor._input_lease
        assert lease is not None
        held_path = lease.child_path
        assert held_path != capability.spec.input_path
        assert held_path.read_bytes() == b"exact input bytes"
        assert os.fspath(held_path) in supervisor._child_command()

        replacement = capability.spec.input_path.with_name("replacement.feb")
        replacement.write_bytes(b"replacement bytes")
        os.replace(os.fspath(replacement), os.fspath(capability.spec.input_path))

        assert held_path.read_bytes() == b"exact input bytes"
    finally:
        with contextlib.suppress(BaseException):
            supervisor._close_filesystem_authority()


def test_posix_input_lease_uses_immutable_snapshot_against_preexisting_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "posix":
        pytest.skip("POSIX-only immutable-input snapshot")

    original = b"stable solver input bytes\n"
    capability = _capability(tmp_path, monkeypatch, code=original.decode("ascii"))
    supervisor = SolverSupervisor(capability)
    source_fd = os.open(os.fspath(capability.spec.input_path), os.O_RDWR)
    sealed_fd: int | None = None
    try:
        # The writer existed before the lease was acquired and remains usable.
        supervisor._acquire_filesystem_authority()
        supervisor._acquire_input_lease()
        lease = supervisor._input_lease
        assert lease is not None
        assert lease.child_path != capability.spec.input_path

        os.lseek(source_fd, 0, os.SEEK_SET)
        tampered = bytes(value ^ 0xFF for value in original)
        assert os.write(source_fd, tampered) == len(tampered)
        assert lease.child_path.read_bytes() == original

        sealed_fd = os.open(os.fspath(lease.child_path), os.O_RDWR)
        with pytest.raises(OSError):
            os.write(sealed_fd, b"x")
        with pytest.raises(OSError):
            os.ftruncate(sealed_fd, len(original) + 1)
        with pytest.raises(OSError):
            os.ftruncate(sealed_fd, len(original) - 1)
    finally:
        if sealed_fd is not None:
            with contextlib.suppress(OSError):
                os.close(sealed_fd)
        with contextlib.suppress(OSError):
            os.lseek(source_fd, 0, os.SEEK_SET)
            os.ftruncate(source_fd, 0)
            os.write(source_fd, original)
        with contextlib.suppress(OSError):
            os.close(source_fd)
        with contextlib.suppress(BaseException):
            supervisor._close_filesystem_authority()


def test_posix_sealing_fails_closed_when_fcntl_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seal = getattr(supervisor_module, "_posix_seal_fd", None)
    assert callable(seal)

    def unavailable() -> object:
        raise ImportError("synthetic fcntl unavailable")

    monkeypatch.setattr(supervisor_module, "_posix_fcntl", unavailable)
    with pytest.raises(SolverOwnershipError, match="seal"):
        seal(17)


def test_posix_sealing_fails_closed_when_add_seals_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seal = getattr(supervisor_module, "_posix_seal_fd", None)
    assert callable(seal)

    class Fcntl:
        F_ADD_SEALS = 1
        F_GET_SEALS = 2
        F_SEAL_WRITE = 4
        F_SEAL_GROW = 8
        F_SEAL_SHRINK = 16
        F_SEAL_SEAL = 32

        @staticmethod
        def fcntl(fd: int, command: int, argument: int = 0) -> int:
            del fd, command, argument
            raise OSError("synthetic F_ADD_SEALS failure")

    monkeypatch.setattr(supervisor_module, "_posix_fcntl", lambda: Fcntl())
    with pytest.raises(SolverOwnershipError, match="seal"):
        seal(17)


def test_posix_sealing_fails_closed_when_seals_do_not_verify(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seal = getattr(supervisor_module, "_posix_seal_fd", None)
    assert callable(seal)

    class Fcntl:
        F_ADD_SEALS = 1
        F_GET_SEALS = 2
        F_SEAL_WRITE = 4
        F_SEAL_GROW = 8
        F_SEAL_SHRINK = 16
        F_SEAL_SEAL = 32

        @staticmethod
        def fcntl(fd: int, command: int, argument: int = 0) -> int:
            del fd, argument
            if command == Fcntl.F_ADD_SEALS:
                return 0
            return 0

    monkeypatch.setattr(supervisor_module, "_posix_fcntl", lambda: Fcntl())
    with pytest.raises(SolverOwnershipError, match="seal"):
        seal(17)


def test_posix_start_persists_sealed_child_fd_attestation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "posix":
        pytest.skip("POSIX-only sealed-child process record")

    capability = _capability(tmp_path, monkeypatch, code="import time; time.sleep(30)")
    supervisor = SolverSupervisor(capability)
    supervisor.start()
    try:
        lease = supervisor._input_lease
        assert lease is not None and lease.handle is not None
        record = json.loads(supervisor.process_record_path.read_text(encoding="utf-8"))
        input_record = record["input_lease"]
        assert isinstance(input_record, dict)
        assert input_record["fd"] == lease.handle
        assert (
            input_record["sha256"]
            == hashlib.sha256(capability.spec.input_path.read_bytes()).hexdigest()
        )
        assert "path" not in input_record
        identity = input_record["identity"]
        assert isinstance(identity, dict)
        metadata = os.fstat(lease.handle)
        assert identity == {
            "device": int(metadata.st_dev),
            "inode": int(metadata.st_ino),
            "nlink": int(metadata.st_nlink),
            "size": int(metadata.st_size),
            "mtime_ns": int(metadata.st_mtime_ns),
        }
    finally:
        supervisor.cancel()


def test_posix_reconnect_uses_recorded_sealed_child_object_after_source_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "posix":
        pytest.skip("POSIX-only sealed-child reconnect")

    original_bytes = b"import time; time.sleep(30)\n"
    capability = _capability(tmp_path, monkeypatch, code=original_bytes.decode("ascii"))
    supervisor = SolverSupervisor(capability)
    reconnected: SolverSupervisor | None = None
    supervisor.start()
    process = cast(subprocess.Popen[bytes], supervisor._process)
    old_record = json.loads(supervisor.process_record_path.read_text(encoding="utf-8"))
    old_fd = old_record["input_lease"]["fd"]
    supervisor._close_filesystem_authority()
    replacement = capability.spec.input_path.with_name("replacement.feb")
    replacement.write_bytes(b"replaceable-current-path\n")
    os.replace(os.fspath(replacement), os.fspath(capability.spec.input_path))
    try:
        reconnected = SolverSupervisor.reconnect(capability)
        lease = reconnected._input_lease
        assert lease is not None
        assert lease.child_path.read_bytes() == original_bytes
        assert reconnected.pid == process.pid
        reconnect_record = reconnected._process_record
        assert reconnect_record is not None
        reconnect_input = reconnect_record["input_lease"]
        assert isinstance(reconnect_input, dict)
        assert reconnect_input["fd"] == old_fd
    finally:
        if reconnected is not None:
            with contextlib.suppress(BaseException):
                reconnected.cancel()
        elif process.poll() is None:
            with contextlib.suppress(BaseException):
                process.kill()
            with contextlib.suppress(BaseException):
                process.wait(timeout=5.0)
        with contextlib.suppress(BaseException):
            if supervisor._process_authority is not None:
                supervisor._process_authority.close()
        with contextlib.suppress(OSError):
            supervisor.process_record_path.unlink()


def test_posix_reconnect_rejects_unavailable_recorded_input_fd_without_touching_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "posix":
        pytest.skip("POSIX-only sealed-child reconnect validation")

    capability = _capability(tmp_path, monkeypatch, code="import time; time.sleep(30)")
    supervisor = SolverSupervisor(capability)
    supervisor.start()
    process = cast(subprocess.Popen[bytes], supervisor._process)
    supervisor._close_filesystem_authority()
    record_path = supervisor.process_record_path
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["input_lease"]["fd"] = max(4096, int(record["input_lease"]["fd"]) + 10000)
    tampered_content = json.dumps(record, indent=2, sort_keys=True).encode("utf-8")
    record_path.write_bytes(tampered_content)
    try:
        with pytest.raises(SolverOwnershipError, match="input|sealed|descriptor|fd"):
            SolverSupervisor.reconnect(capability)
        assert process.poll() is None
        assert record_path.read_bytes() == tampered_content
    finally:
        if process.poll() is None:
            with contextlib.suppress(BaseException):
                process.kill()
            with contextlib.suppress(BaseException):
                process.wait(timeout=5.0)
        with contextlib.suppress(BaseException):
            if supervisor._process_authority is not None:
                supervisor._process_authority.close()
        with contextlib.suppress(OSError):
            record_path.unlink()


def test_posix_launch_executes_held_runtime_image_after_path_replace_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "posix":
        pytest.skip("POSIX-only exact executable image launch")

    executable = _runtime_copy(tmp_path)
    marker = tmp_path / "executed-marker.txt"
    code = (
        "from pathlib import Path; "
        f"Path({str(marker)!r}).write_text('held-image', encoding='utf-8')"
    )
    capability = _capability(tmp_path, monkeypatch, code=code, executable=executable)
    supervisor = SolverSupervisor(capability)
    original_popen = cast(Callable[..., subprocess.Popen[bytes]], subprocess.Popen)
    observed: dict[str, object] = {}

    def racing_popen(command: object, **kwargs: object) -> subprocess.Popen[bytes]:
        observed["command"] = command
        assert isinstance(command, tuple)
        assert str(command[0]).startswith("/proc/self/fd/")
        replacement = executable.with_name("runtime-replacement")
        displaced = executable.with_name("runtime-displaced")
        replacement.write_bytes(b"replacement image")
        os.replace(os.fspath(executable), os.fspath(displaced))
        os.replace(os.fspath(replacement), os.fspath(executable))
        try:
            return original_popen(command, **kwargs)
        finally:
            os.replace(os.fspath(executable), os.fspath(replacement))
            os.replace(os.fspath(displaced), os.fspath(executable))
            replacement.unlink()

    monkeypatch.setattr(subprocess, "Popen", racing_popen)
    result = supervisor.run()

    assert result.state is SolverState.NORMAL_EXIT
    assert marker.read_text(encoding="utf-8") == "held-image"
    assert observed["command"]


def test_posix_process_record_binds_executed_image_and_reconnects_by_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "posix":
        pytest.skip("POSIX-only executed-image reconnect binding")

    executable = _runtime_copy(tmp_path)
    capability = _capability(
        tmp_path,
        monkeypatch,
        code="import time; time.sleep(30)",
        executable=executable,
    )
    supervisor = SolverSupervisor(capability)
    reconnected: SolverSupervisor | None = None
    supervisor.start()
    process = cast(subprocess.Popen[bytes], supervisor._process)
    record_path = Path(os.fspath(supervisor.process_record_path))
    record = json.loads(record_path.read_text(encoding="utf-8"))
    image = record["executable_image"]
    assert isinstance(image, dict)
    assert image["sha256"] == hashlib.sha256(executable.read_bytes()).hexdigest()
    image_identity = image["identity"]
    assert isinstance(image_identity, dict)
    assert image_identity["device"] >= 0
    assert image_identity["inode"] >= 0
    assert image_identity["nlink"] == 0
    assert image_identity["size"] == executable.stat().st_size

    supervisor._close_filesystem_authority()
    try:
        # The path is replaced and restored while reconnect authority is detached;
        # the executed image remains the held object recorded above.
        replacement = executable.with_name("runtime-replacement")
        displaced = executable.with_name("runtime-displaced")
        replacement.write_bytes(b"replacement image")
        os.replace(os.fspath(executable), os.fspath(displaced))
        os.replace(os.fspath(replacement), os.fspath(executable))
        os.replace(os.fspath(executable), os.fspath(replacement))
        os.replace(os.fspath(displaced), os.fspath(executable))
        replacement.unlink()

        tampered = json.loads(json.dumps(record))
        tampered["executable_image"]["sha256"] = "0" * 64
        record_path.write_bytes(json.dumps(tampered, indent=2, sort_keys=True).encode("utf-8"))
        with pytest.raises(SolverOwnershipError, match="executable|image|digest"):
            SolverSupervisor.reconnect(capability)
        assert process.poll() is None

        record_path.write_bytes(json.dumps(record, indent=2, sort_keys=True).encode("utf-8"))
        reconnected = SolverSupervisor.reconnect(capability)
        assert reconnected.pid == process.pid
    finally:
        if reconnected is not None:
            with contextlib.suppress(BaseException):
                reconnected.cancel()
        elif process.poll() is None:
            with contextlib.suppress(BaseException):
                process.kill()
            with contextlib.suppress(BaseException):
                process.wait(timeout=5.0)
        with contextlib.suppress(BaseException):
            if supervisor._process_authority is not None:
                supervisor._process_authority.close()
        with contextlib.suppress(OSError):
            record_path.unlink()


def test_process_record_parser_accepts_zero_link_anonymous_input_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capability = _capability(tmp_path, monkeypatch, code="pass")
    supervisor = SolverSupervisor(capability)
    expected_sha256, expected_identity = supervisor._recorded_input_binding()
    identity = {
        "device": 11,
        "inode": 22,
        "nlink": 0,
        "size": 33,
        "mtime_ns": 44,
    }
    record: dict[str, object] = {
        "input_lease": {
            "fd": 7,
            "sha256": expected_sha256,
            "identity": identity,
        }
    }

    parsed_fd, parsed_sha256, parsed_identity = supervisor._parse_posix_input_lease_record(record)

    assert parsed_fd == 7
    assert parsed_sha256 == expected_sha256
    assert parsed_identity == (
        identity["device"],
        identity["inode"],
        identity["nlink"],
        identity["size"],
        identity["mtime_ns"],
    )
    assert expected_identity[2] == 1


@pytest.mark.parametrize("nlink", [-1, 1, 2])
def test_process_record_parser_rejects_nonanonymous_input_link_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, nlink: int
) -> None:
    capability = _capability(tmp_path, monkeypatch, code="pass")
    supervisor = SolverSupervisor(capability)
    expected_sha256, _expected_identity = supervisor._recorded_input_binding()
    record: dict[str, object] = {
        "input_lease": {
            "fd": 7,
            "sha256": expected_sha256,
            "identity": {
                "device": 11,
                "inode": 22,
                "nlink": nlink,
                "size": 33,
                "mtime_ns": 44,
            },
        }
    }

    with pytest.raises(SolverOwnershipError, match="identity"):
        supervisor._parse_posix_input_lease_record(record)


@pytest.mark.parametrize("field", ["device", "inode", "size", "mtime_ns"])
def test_process_record_parser_rejects_negative_anonymous_input_identity_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    capability = _capability(tmp_path, monkeypatch, code="pass")
    supervisor = SolverSupervisor(capability)
    expected_sha256, _expected_identity = supervisor._recorded_input_binding()
    identity = {
        "device": 11,
        "inode": 22,
        "nlink": 0,
        "size": 33,
        "mtime_ns": 44,
    }
    identity[field] = -1
    record: dict[str, object] = {
        "input_lease": {
            "fd": 7,
            "sha256": expected_sha256,
            "identity": identity,
        }
    }

    with pytest.raises(SolverOwnershipError, match="identity"):
        supervisor._parse_posix_input_lease_record(record)


def test_windows_input_lease_blocks_leaf_write_and_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "nt":
        pytest.fail("required Windows exact-input lease test executed on a non-Windows host")

    capability = _capability(tmp_path, monkeypatch, code="exact input bytes")
    supervisor = SolverSupervisor(capability)
    replacement = capability.spec.input_path.with_name("replacement.feb")
    try:
        supervisor._acquire_filesystem_authority()
        supervisor._acquire_input_lease()
        lease = supervisor._input_lease
        assert lease is not None
        assert lease.child_path == capability.spec.input_path
        assert lease.native_handle is not None

        with pytest.raises(OSError):
            os.open(os.fspath(capability.spec.input_path), os.O_WRONLY)

        replacement.write_bytes(b"replacement bytes")
        with pytest.raises(OSError):
            os.replace(os.fspath(replacement), os.fspath(capability.spec.input_path))
    finally:
        with contextlib.suppress(BaseException):
            supervisor._close_filesystem_authority()
        with contextlib.suppress(OSError):
            replacement.unlink()


def test_windows_directory_open_identity_failure_retains_exact_raw_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "nt":
        pytest.fail("required Windows directory-open cleanup test executed on a non-Windows host")

    import ctypes

    captured: list[supervisor_module._WindowsDirectoryHandle] = []

    def capture_directory(self: object, handle: supervisor_module._WindowsDirectoryHandle) -> None:
        del self
        captured.append(handle)

    class FakeKernel32:
        def CreateFileW(self, *args: object) -> ctypes.c_void_p:
            del args
            return ctypes.c_void_p(0x1234)

        def CloseHandle(self, handle: object) -> int:
            del handle
            return 0

    monkeypatch.setattr(
        supervisor_module._DurableClaimCleanup,
        "adopt_directory_handle",
        capture_directory,
        raising=False,
    )
    monkeypatch.setattr(supervisor_module, "_windows_kernel32", lambda: FakeKernel32())
    monkeypatch.setattr(
        supervisor_module,
        "_windows_directory_information",
        lambda handle: (_ for _ in ()).throw(OSError("synthetic identity failure")),
    )
    monkeypatch.setattr(
        supervisor_module, "_windows_duplicate_native_handle", lambda handle: 0x5678
    )
    monkeypatch.setattr(supervisor_module, "_windows_close_native_handle", lambda handle: None)
    monkeypatch.setattr(
        supervisor_module,
        "_windows_compare_object_handles",
        lambda first, second: True,
        raising=False,
    )

    with pytest.raises(OSError, match="synthetic identity failure"):
        supervisor_module._windows_open_directory(tmp_path)

    assert len(captured) == 1
    assert captured[0].value == 0x1234


def test_windows_partial_filesystem_authority_is_durably_adopted_after_close_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "nt":
        pytest.fail(
            "required Windows partial-authority cleanup test executed on a non-Windows host"
        )

    root = tmp_path / "attempt"
    root.mkdir()
    captured: list[supervisor_module._FilesystemAuthority] = []
    handles: list[supervisor_module._WindowsDirectoryHandle] = []

    def fake_open(path: Path) -> supervisor_module._WindowsDirectoryHandle:
        del path
        handle = supervisor_module._WindowsDirectoryHandle(
            value=len(handles) + 1,
            volume_serial=1,
            file_index=len(handles) + 1,
        )
        handles.append(handle)
        return handle

    def failing_close(handle: supervisor_module._WindowsDirectoryHandle) -> None:
        del handle
        raise OSError("synthetic directory close failure")

    def capture_authority(
        self: object,
        runtime_claim: object,
        process_record_claim: object,
        candidates: object,
        *,
        filesystem_authority: supervisor_module._FilesystemAuthority | None = None,
        input_lease: object = None,
    ) -> None:
        del self, runtime_claim, process_record_claim, candidates, input_lease
        if filesystem_authority is not None:
            captured.append(filesystem_authority)

    original_lstat = os.lstat
    lstat_calls = 0

    def flaky_lstat(path: str | os.PathLike[str]) -> os.stat_result:
        nonlocal lstat_calls
        lstat_calls += 1
        if lstat_calls == 2:
            raise OSError("synthetic directory identity failure")
        return original_lstat(path)

    monkeypatch.setattr(supervisor_module, "_windows_open_directory", fake_open)
    monkeypatch.setattr(supervisor_module, "_windows_close_directory", failing_close)
    monkeypatch.setattr(supervisor_module._DurableClaimCleanup, "adopt", capture_authority)
    monkeypatch.setattr(os, "lstat", flaky_lstat)

    with pytest.raises(OSError, match="synthetic directory identity failure"):
        supervisor_module._FilesystemAuthority(root)

    assert len(handles) >= 2
    assert len(captured) == 1
    assert captured[0]._entries


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


def test_start_revalidates_after_prepare_outputs_before_popen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability = _capability(tmp_path, monkeypatch, code="pass")
    attempt = object.__getattribute__(capability, "_attempt_workspace")
    assert type(attempt) is AttemptWorkspace
    original_prepare_outputs = SolverSupervisor._prepare_outputs

    def racing_prepare_outputs(supervisor: SolverSupervisor) -> None:
        original_prepare_outputs(supervisor)
        object.__setattr__(attempt, "attempt_id", "attempt-b")

    monkeypatch.setattr(SolverSupervisor, "_prepare_outputs", racing_prepare_outputs)
    popen_reached = False

    def unexpected_popen(*args: object, **kwargs: object) -> None:
        nonlocal popen_reached
        del args, kwargs
        popen_reached = True
        raise AssertionError("raced launch reached Popen")

    monkeypatch.setattr("febio_cae_harness.solver.supervisor.subprocess.Popen", unexpected_popen)
    supervisor = SolverSupervisor(capability)
    with pytest.raises(SolverConfigurationError, match="authority|binding|live"):
        supervisor.start()

    assert not popen_reached


def test_posix_launch_anchors_preparation_and_child_paths_across_attempt_root_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "posix":
        pytest.skip("POSIX-only launch path anchoring")

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
    attempt_root = Path(os.fspath(attempt))
    code = (
        "from pathlib import Path; import os; "
        "Path(os.environ['FEBIO_CAE_HARNESS_LOG']).write_text('replacement', encoding='utf-8'); "
        "Path(os.environ['FEBIO_CAE_HARNESS_XPLT']).write_bytes(b'replacement')"
    )
    input_path = attempt.write_text("input.feb", code)
    runtime = _issued_runtime(monkeypatch)
    capability = headless_module._issue_launch_capability(
        attempt,
        intent,
        runtime,
        input_path,
        expected_steps=None,
        expected_final_time=None,
        timeout_seconds=None,
    )
    supervisor = SolverSupervisor(capability)
    moved_root = attempt_root.with_name(attempt_root.name + "-moved")
    attempt_root.rename(moved_root)
    replacement_root = attempt_root
    replacement_root.mkdir()

    result = supervisor.run()
    assert result.state is SolverState.NORMAL_EXIT

    assert not (replacement_root / "input.log").exists()
    assert (moved_root / "input.log").read_text(encoding="utf-8") == "replacement"
    assert not (replacement_root / "process.json").exists()
    assert (moved_root / "process.json").is_file()


def test_rollback_process_record_has_no_check_then_path_unlink_window() -> None:
    source = inspect.getsource(SolverSupervisor._rollback_process_record)
    assert "_record_claim_matches" not in source
    assert "claim.path.unlink" not in source


def test_posix_rollback_preserves_replacement_at_former_unlink_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name != "posix":
        pytest.skip("POSIX atomic record transaction")

    capability = _capability(tmp_path, monkeypatch, code="pass")
    supervisor = SolverSupervisor(capability)
    supervisor.start()
    claim = supervisor._process_record_claim
    assert claim is not None
    replaced = False

    original_exchange = supervisor_module._posix_rename_exchange

    def racing_exchange(parent_fd: int, left: str, right: str) -> None:
        nonlocal replaced
        original_exchange(parent_fd, left, right)
        if not replaced and left == claim.name:
            replacement = supervisor.process_record_path.with_name("foreign.json")
            replacement.write_text('{"foreign": true}', encoding="utf-8")
            os.replace(os.fspath(replacement), os.fspath(supervisor.process_record_path))
            replaced = True

    monkeypatch.setattr(supervisor_module, "_posix_rename_exchange", racing_exchange)
    try:
        assert supervisor._rollback_process_record() == ()
        assert replaced
        assert supervisor.process_record_path.read_text(encoding="utf-8") == '{"foreign": true}'
    finally:
        supervisor.cancel()


def test_posix_rollback_uncertainty_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name != "posix":
        pytest.skip("POSIX atomic record transaction")

    capability = _capability(tmp_path, monkeypatch, code="pass")
    supervisor = SolverSupervisor(capability)
    supervisor.start()
    try:
        with monkeypatch.context() as transaction_patch:
            transaction_patch.setattr(
                supervisor_module,
                "_posix_rename_exchange",
                Mock(side_effect=OSError("synthetic exchange uncertainty")),
            )
            failures = supervisor._rollback_process_record()
        assert failures
        assert supervisor.process_record_path.is_file()
    finally:
        supervisor.cancel()
    assert not supervisor.process_record_path.exists()


def test_wait_replays_only_supervisor_latched_result_and_ignores_registry_injection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability = _capability(tmp_path, monkeypatch, code="pass")
    supervisor = SolverSupervisor(capability)
    result = supervisor.run()
    forged = replace(result)
    object.__setattr__(supervisor, "_result", forged)
    monkeypatch.setattr(
        supervisor_module,
        "_RESULT_REGISTRY",
        {id(forged): forged},
        raising=False,
    )
    assert supervisor.wait() is result
    assert supervisor.result is result


def test_windows_directory_authority_blocks_root_and_ancestor_rename(tmp_path: Path) -> None:
    if os.name != "nt":
        pytest.skip("Windows-only directory-handle authority")

    root = tmp_path / "attempt"
    root.mkdir()
    authority = supervisor_module._FilesystemAuthority(root)
    try:
        for target in (root, root.parent):
            with pytest.raises(OSError):
                target.rename(target.with_name(target.name + "-renamed"))
    finally:
        authority.close()

    moved = root.with_name(root.name + "-moved")
    root.rename(moved)
    assert moved.is_dir()


def test_windows_active_supervisor_finalizer_releases_process_record_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "nt":
        pytest.fail("required Windows finalizer transaction test executed on a non-Windows host")

    capability = _capability(tmp_path, monkeypatch, code="import time; time.sleep(30)")
    supervisor = SolverSupervisor(capability)
    process: subprocess.Popen[bytes] | None = None
    process_authority: ProcessAuthority | None = None
    filesystem_authority: supervisor_module._FilesystemAuthority | None = None
    record_fd: int | None = None
    record_path = capability.spec.attempt_root / "process.json"
    record_key = supervisor_module._windows_record_claim_key(record_path)
    record_claim_entry: tuple[int, int, int] | None = None
    record_content = b""
    supervisor_ref: weakref.ReferenceType[SolverSupervisor] | None = None
    original_open = supervisor_module._windows_open_process_record
    original_delete = supervisor_module._windows_delete_process_record
    original_complete = SolverSupervisor._complete
    original_rollback = SolverSupervisor._rollback_process_record
    reopen_calls: list[Path] = []
    terminal_actions: list[str] = []
    delete_calls: list[int] = []
    close_failures = True
    close_attempts: list[int] = []
    original_close = os.close

    def track_open(path: Path, *, create: bool, delete_access: bool = True) -> int:
        reopen_calls.append(Path(os.fspath(path)))
        return original_open(path, create=create, delete_access=delete_access)

    def track_delete(fd: int) -> None:
        delete_calls.append(fd)
        original_delete(fd)

    def unexpected_complete(
        instance: SolverSupervisor, state: SolverState, return_code: int | None
    ) -> object:
        terminal_actions.append("complete")
        return original_complete(instance, state, return_code)

    def unexpected_rollback(instance: SolverSupervisor) -> tuple[BaseException, ...]:
        terminal_actions.append("rollback")
        return original_rollback(instance)

    def flaky_close(fd: int) -> None:
        if record_fd is not None and fd == record_fd and close_failures:
            close_attempts.append(fd)
            raise OSError("synthetic persistent process-record close failure")
        original_close(fd)

    try:
        supervisor.start()
        assert supervisor._process is not None
        process = cast(subprocess.Popen[bytes], supervisor._process)
        process_authority = supervisor._process_authority
        filesystem_authority = supervisor._filesystem_authority
        claim = supervisor._process_record_claim
        assert process_authority is not None
        assert filesystem_authority is not None
        assert claim is not None and claim.handle is not None
        record_fd = claim.handle
        record_claim_entry = (record_fd, claim.device, claim.inode)
        record_path = Path(os.fspath(claim.path))
        record_key = supervisor_module._windows_record_claim_key(record_path)
        record_content = supervisor_module._read_record_fd(record_fd)
        record = json.loads(record_content.decode("utf-8"))
        assert record["state"] == SolverState.RUNNING.value
        assert process.poll() is None
        assert process.returncode is None
        assert supervisor_module._WINDOWS_RECORD_CLAIMS.get(record_key) == [record_claim_entry]

        supervisor_ref = weakref.ref(supervisor)
        monkeypatch.setattr(supervisor_module, "_windows_open_process_record", track_open)
        monkeypatch.setattr(supervisor_module, "_windows_delete_process_record", track_delete)
        monkeypatch.setattr(SolverSupervisor, "_complete", unexpected_complete)
        monkeypatch.setattr(SolverSupervisor, "_rollback_process_record", unexpected_rollback)
        monkeypatch.setattr(os, "close", flaky_close)
        del supervisor
        for _ in range(3):
            gc.collect()
            if supervisor_ref() is None:
                break

        assert supervisor_ref() is None
        assert process.poll() is None
        assert process.returncode is None
        assert isinstance(process_authority, _WindowsProcessAuthority)
        assert process_authority._handle is None
        assert process_authority._child_handle is None
        with pytest.raises(SolverOwnershipError, match="closed"):
            filesystem_authority.verify()
        assert close_attempts == [record_fd]
        os.fstat(record_fd)
        durable_owner = getattr(supervisor_module, "_DURABLE_CLAIM_CLEANUP", None)
        assert durable_owner is not None
        assert claim is not None
        assert claim.handle == record_fd
        assert sum(held is claim for held in durable_owner._process_record_claims) == 1
        assert supervisor_module._WINDOWS_RECORD_CLAIMS.get(record_key) == [record_claim_entry]
        assert record_path.is_file()
        assert supervisor_module._read_record_fd(record_fd) == record_content
        assert json.loads(record_content.decode("utf-8"))["state"] == SolverState.RUNNING.value
        assert delete_calls == []
        assert terminal_actions == []
        assert reopen_calls == []

        close_failures = False
        supervisor_module._drain_durable_cleanup()
        assert claim.handle is None
        with pytest.raises(OSError):
            os.fstat(record_fd)
        assert record_key not in supervisor_module._WINDOWS_RECORD_CLAIMS
        assert not any(held is claim for held in durable_owner._process_record_claims)
        assert record_path.read_bytes() == record_content
        assert process.poll() is None
        assert process.returncode is None

        reconnect_fd = supervisor_module._windows_open_process_record(record_path, create=False)
        try:
            assert reopen_calls == [record_path]
            assert supervisor_module._read_record_fd(reconnect_fd) == record_content
            supervisor_module._windows_delete_process_record(reconnect_fd)
        finally:
            supervisor_module._windows_close_owned_fd(
                reconnect_fd,
                "handle",
                "native_handle",
            )
        assert not record_path.exists()
    finally:
        close_failures = False
        with contextlib.suppress(BaseException):
            supervisor_module._drain_durable_cleanup()
        if process is not None:
            try:
                if process.poll() is None:
                    try:
                        if process_authority is not None:
                            process_authority.terminate(process.pid)
                        else:
                            process.kill()
                    except BaseException:
                        process.kill()
                process.wait(timeout=5.0)
            finally:
                if process_authority is not None:
                    process_authority.close()

        if record_fd is not None:
            try:
                os.fstat(record_fd)
            except OSError:
                pass
            else:
                if record_path.exists():
                    original_delete(record_fd)
                supervisor_module._windows_unregister_record_claim(record_path, record_fd)
                original_close(record_fd)
                if claim is not None:
                    claim.handle = None
        with contextlib.suppress(BaseException):
            supervisor_module._drain_durable_cleanup()
        if record_path.exists():
            cleanup_fd = original_open(record_path, create=False)
            try:
                original_delete(cleanup_fd)
            finally:
                original_close(cleanup_fd)
        if filesystem_authority is not None:
            filesystem_authority.close()


def test_windows_active_record_read_validation_failure_retains_exact_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "nt":
        pytest.fail("required Windows active-record validation test executed on a non-Windows host")

    capability = _capability(tmp_path, monkeypatch, code="import time; time.sleep(30)")
    supervisor = SolverSupervisor(capability)
    supervisor.start()
    process = cast(subprocess.Popen[bytes], supervisor._process)
    claim = supervisor._process_record_claim
    assert claim is not None and claim.handle is not None
    original_claim = claim
    original_fd = claim.handle
    original_content = supervisor_module._read_record_fd(original_fd)
    record_path = Path(os.fspath(claim.path))
    record_key = supervisor_module._windows_record_claim_key(record_path)
    record_claim_entry = (original_fd, claim.device, claim.inode)
    assert supervisor_module._WINDOWS_RECORD_CLAIMS.get(record_key) == [record_claim_entry]

    probe_paths: list[Path] = []
    path_reopens: list[Path] = []
    lifecycle_actions: list[str] = []
    original_probe = supervisor_module._windows_probe_process_record
    original_open = supervisor_module._windows_open_process_record

    def fail_second_probe(path: Path) -> int:
        probe_paths.append(Path(os.fspath(path)))
        if len(probe_paths) == 2:
            raise OSError("synthetic second process-record read probe failure")
        return original_probe(path)

    def track_path_open(path: Path, *, create: bool, delete_access: bool = True) -> int:
        path_reopens.append(Path(os.fspath(path)))
        return original_open(path, create=create, delete_access=delete_access)

    def forbidden_complete(
        instance: SolverSupervisor, state: SolverState, return_code: int | None
    ) -> object:
        del instance, state, return_code
        lifecycle_actions.append("complete")
        raise AssertionError("active record read completed the supervisor")

    def forbidden_rollback(instance: SolverSupervisor) -> tuple[BaseException, ...]:
        del instance
        lifecycle_actions.append("rollback")
        raise AssertionError("active record read rolled back the supervisor")

    def forbidden_delete(fd: int) -> None:
        del fd
        lifecycle_actions.append("delete")
        raise AssertionError("active record read deleted the process record")

    def forbidden_terminate(
        instance: SolverSupervisor,
        owned_process: subprocess.Popen[bytes] | supervisor_module._ReconnectedProcess,
    ) -> None:
        del instance, owned_process
        lifecycle_actions.append("terminate")
        raise AssertionError("active record read terminated the solver")

    foreign_path: Path | None = None
    try:
        with monkeypatch.context() as read_patch:
            read_patch.setattr(
                supervisor_module,
                "_windows_probe_process_record",
                fail_second_probe,
            )
            read_patch.setattr(
                supervisor_module,
                "_windows_open_process_record",
                track_path_open,
            )
            read_patch.setattr(SolverSupervisor, "_complete", forbidden_complete)
            read_patch.setattr(
                SolverSupervisor,
                "_rollback_process_record",
                forbidden_rollback,
            )
            read_patch.setattr(
                supervisor_module,
                "_windows_delete_process_record",
                forbidden_delete,
            )
            read_patch.setattr(
                SolverSupervisor,
                "_terminate_owned_process",
                forbidden_terminate,
            )
            with pytest.raises(SolverOwnershipError):
                supervisor._read_process_record()

        assert probe_paths == [record_path, record_path]
        assert path_reopens == []
        assert lifecycle_actions == []
        assert supervisor.state is SolverState.RUNNING
        assert process.poll() is None
        assert process.returncode is None
        assert supervisor.result is None
        retained_claim = supervisor._process_record_claim
        assert retained_claim is original_claim or (
            retained_claim is not None
            and retained_claim.handle == original_fd
            and retained_claim.path == original_claim.path
            and retained_claim.device == original_claim.device
            and retained_claim.inode == original_claim.inode
        )
        assert os.fstat(original_fd).st_size == len(original_content)
        assert supervisor_module._read_record_fd(original_fd) == original_content
        assert supervisor_module._WINDOWS_RECORD_CLAIMS.get(record_key) == [record_claim_entry]
        foreign_path = _assert_windows_record_mutation_blocked(record_path)
    finally:
        try:
            if supervisor.process_id is not None:
                supervisor.cancel()
        finally:
            for path in (foreign_path, record_path):
                if path is not None and path.exists():
                    path.unlink()


def test_windows_active_record_read_reuses_exact_claim_without_rotation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "nt":
        pytest.fail("required Windows active-record reuse test executed on a non-Windows host")

    capability = _capability(tmp_path, monkeypatch, code="import time; time.sleep(30)")
    supervisor = SolverSupervisor(capability)
    supervisor.start()
    process = cast(subprocess.Popen[bytes], supervisor._process)
    claim = supervisor._process_record_claim
    assert claim is not None and claim.handle is not None
    original_claim = claim
    original_fd = claim.handle
    original_content = supervisor_module._read_record_fd(original_fd)
    original_record = json.loads(original_content.decode("utf-8"))
    record_path = Path(os.fspath(claim.path))
    record_key = supervisor_module._windows_record_claim_key(record_path)
    record_claim_entry = (original_fd, claim.device, claim.inode)
    assert supervisor_module._WINDOWS_RECORD_CLAIMS.get(record_key) == [record_claim_entry]
    handle_count_before = _windows_process_handle_count()

    release_fds: list[int] = []
    closed_fds: list[int] = []
    path_reopens: list[Path] = []
    probe_paths: list[Path] = []
    original_release = SolverSupervisor._release_process_record_claim
    original_open = supervisor_module._windows_open_process_record
    original_probe = supervisor_module._windows_probe_process_record
    original_close = os.close

    def track_release(
        instance: SolverSupervisor,
        candidate: supervisor_module._ProcessRecordClaim | None,
    ) -> tuple[BaseException, ...]:
        if candidate is not None and candidate.handle == original_fd:
            release_fds.append(original_fd)
        return original_release(instance, candidate)

    def track_close(fd: int) -> None:
        if fd == original_fd:
            closed_fds.append(fd)
        original_close(fd)

    def track_path_open(path: Path, *, create: bool, delete_access: bool = True) -> int:
        path_reopens.append(Path(os.fspath(path)))
        return original_open(path, create=create, delete_access=delete_access)

    def track_probe(path: Path) -> int:
        probe_paths.append(Path(os.fspath(path)))
        return original_probe(path)

    try:
        with monkeypatch.context() as read_patch:
            read_patch.setattr(SolverSupervisor, "_release_process_record_claim", track_release)
            read_patch.setattr(os, "close", track_close)
            read_patch.setattr(
                supervisor_module,
                "_windows_open_process_record",
                track_path_open,
            )
            read_patch.setattr(supervisor_module, "_windows_probe_process_record", track_probe)
            records = [supervisor._read_process_record() for _ in range(5)]

        assert records == [original_record] * 5
        assert len(probe_paths) == 10
        assert path_reopens == []
        assert release_fds == []
        assert closed_fds == []
        assert _windows_process_handle_count() == handle_count_before
        assert supervisor.state is SolverState.RUNNING
        assert process.poll() is None
        assert process.returncode is None
        assert supervisor.result is None
        retained_claim = supervisor._process_record_claim
        assert retained_claim is original_claim or (
            retained_claim is not None
            and retained_claim.handle == original_fd
            and retained_claim.path == original_claim.path
            and retained_claim.device == original_claim.device
            and retained_claim.inode == original_claim.inode
        )
        assert os.fstat(original_fd).st_size == len(original_content)
        assert supervisor_module._read_record_fd(original_fd) == original_content
        assert supervisor_module._WINDOWS_RECORD_CLAIMS.get(record_key) == [record_claim_entry]
    finally:
        if supervisor.process_id is not None:
            supervisor.cancel()


def test_windows_reconnect_validation_failure_preserves_existing_record_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "nt":
        pytest.fail("required Windows reconnect validation test executed on a non-Windows host")

    capability = _capability(tmp_path, monkeypatch, code="import time; time.sleep(30)")
    original_supervisor = SolverSupervisor(capability)
    original_supervisor.start()
    original_process = cast(subprocess.Popen[bytes], original_supervisor._process)
    original_claim = original_supervisor._process_record_claim
    assert original_claim is not None and original_claim.handle is not None
    original_fd = original_claim.handle
    original_content = supervisor_module._read_record_fd(original_fd)
    record_path = Path(os.fspath(original_claim.path))
    record_key = supervisor_module._windows_record_claim_key(record_path)
    record_claim_entry = (original_fd, original_claim.device, original_claim.inode)
    assert supervisor_module._WINDOWS_RECORD_CLAIMS.get(record_key) == [record_claim_entry]

    candidate_fds: list[int] = []
    original_duplicate = supervisor_module._windows_duplicate_record_claim
    original_identity = SolverSupervisor._record_claim_identity_matches

    def track_duplicate(path: Path) -> int | None:
        duplicate = original_duplicate(path)
        if duplicate is not None:
            candidate_fds.append(duplicate)
        return duplicate

    def fail_candidate_identity(
        instance: SolverSupervisor,
        candidate: supervisor_module._ProcessRecordClaim,
    ) -> bool:
        if instance is not original_supervisor:
            assert candidate.handle in candidate_fds
            return False
        return original_identity(instance, candidate)

    foreign_path: Path | None = None
    try:
        with monkeypatch.context() as reconnect_patch:
            reconnect_patch.setattr(
                supervisor_module,
                "_windows_duplicate_record_claim",
                track_duplicate,
            )
            reconnect_patch.setattr(
                SolverSupervisor,
                "_record_claim_identity_matches",
                fail_candidate_identity,
            )
            with pytest.raises(SolverOwnershipError):
                SolverSupervisor.reconnect(capability)

        assert candidate_fds
        assert len(candidate_fds) == len(set(candidate_fds))
        for candidate_fd in candidate_fds:
            with pytest.raises(OSError):
                os.fstat(candidate_fd)
        assert original_supervisor.state is SolverState.RUNNING
        assert original_process.poll() is None
        assert original_process.returncode is None
        assert original_supervisor.result is None
        assert original_supervisor._process_record_claim is original_claim
        assert os.fstat(original_fd).st_size == len(original_content)
        assert supervisor_module._read_record_fd(original_fd) == original_content
        assert supervisor_module._WINDOWS_RECORD_CLAIMS.get(record_key) == [record_claim_entry]
        foreign_path = _assert_windows_record_mutation_blocked(record_path)
    finally:
        try:
            if original_supervisor.process_id is not None:
                original_supervisor.cancel()
        finally:
            for path in (foreign_path, record_path):
                if path is not None and path.exists():
                    path.unlink()


def test_windows_process_record_lease_blocks_replacement_through_running_transition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "nt":
        pytest.fail("required Windows transaction test executed on a non-Windows host")

    capability = _capability(tmp_path, monkeypatch, code="import time; time.sleep(30)")
    handle_number = 0

    def permissive_directory_open(path: Path) -> supervisor_module._WindowsDirectoryHandle:
        nonlocal handle_number
        handle_number += 1
        return supervisor_module._WindowsDirectoryHandle(0, 0, handle_number)

    monkeypatch.setattr(supervisor_module, "_windows_open_directory", permissive_directory_open)
    monkeypatch.setattr(supervisor_module, "_windows_verify_directory", lambda handle: None)
    monkeypatch.setattr(supervisor_module, "_windows_close_directory", lambda handle: None)
    supervisor = SolverSupervisor(capability)
    original_write = SolverSupervisor._write_process_record
    foreign_write_errors: list[OSError] = []
    foreign_write_succeeded = False
    replacement_errors: list[OSError] = []
    replacement_succeeded = False
    delete_errors: list[OSError] = []
    delete_succeeded = False

    def write_record(instance: SolverSupervisor, record: dict[str, object]) -> None:
        nonlocal delete_succeeded, foreign_write_succeeded, replacement_succeeded
        original_write(instance, record)
        if record.get("state") != "BOUND_SUSPENDED":
            return
        try:
            foreign_fd = os.open(os.fspath(instance.process_record_path), os.O_WRONLY)
        except OSError as error:
            foreign_write_errors.append(error)
        else:
            foreign_write_succeeded = True
            os.close(foreign_fd)
        foreign_path = instance.process_record_path.with_name("foreign.json")
        foreign_path.write_text('{"foreign": true}', encoding="utf-8")
        try:
            os.replace(os.fspath(foreign_path), os.fspath(instance.process_record_path))
        except OSError as error:
            replacement_errors.append(error)
        else:
            replacement_succeeded = True
        try:
            os.unlink(os.fspath(instance.process_record_path))
        except OSError as error:
            delete_errors.append(error)
        else:
            delete_succeeded = True

    monkeypatch.setattr(SolverSupervisor, "_write_process_record", write_record)
    try:
        supervisor.start()
        assert foreign_write_errors
        assert not foreign_write_succeeded
        assert replacement_errors
        assert not replacement_succeeded
        assert delete_errors
        assert not delete_succeeded
        claim = supervisor._process_record_claim
        assert claim is not None and claim.handle is not None
        record = json.loads(supervisor_module._read_record_fd(claim.handle).decode("utf-8"))
        assert record["state"] == SolverState.RUNNING.value
        assert record.get("foreign") is None
    finally:
        if supervisor.process_id is not None:
            supervisor.cancel()


def test_windows_running_record_transition_rejects_lost_directory_entry_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "nt":
        pytest.fail("required Windows transaction test executed on a non-Windows host")

    capability = _capability(tmp_path, monkeypatch, code="import time; time.sleep(30)")
    supervisor = SolverSupervisor(capability)
    original_identity_check = SolverSupervisor._record_claim_identity_matches
    identity_checks = 0
    persisted_states: list[object] = []

    def lost_directory_entry(
        instance: SolverSupervisor, claim: supervisor_module._ProcessRecordClaim
    ) -> bool:
        nonlocal identity_checks
        if claim.state == "BOUND_SUSPENDED":
            identity_checks += 1
            return False
        return original_identity_check(instance, claim)

    original_write = SolverSupervisor._write_process_record

    def track_write(instance: SolverSupervisor, record: dict[str, object]) -> None:
        persisted_states.append(record.get("state"))
        original_write(instance, record)

    monkeypatch.setattr(SolverSupervisor, "_record_claim_identity_matches", lost_directory_entry)
    monkeypatch.setattr(SolverSupervisor, "_write_process_record", track_write)
    try:
        with pytest.raises(SolverOwnershipError, match="record|directory|replaced"):
            supervisor.start()
        assert identity_checks >= 1
        assert SolverState.RUNNING.value not in persisted_states
        assert supervisor.state is SolverState.FAILED
        assert supervisor.process_id is None
        assert not supervisor.process_record_path.exists()
    finally:
        if supervisor.process_id is not None:
            supervisor.cancel()


def test_windows_reconnect_recovers_crash_before_native_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        capability,
        supervisor,
        process,
        _authority,
        bound_record,
        _running_record,
        record_path,
    ) = _prepare_windows_resume_recovery(
        tmp_path,
        monkeypatch,
        resume_before_crash=False,
    )
    reconnected: SolverSupervisor | None = None
    transaction_path = record_path.with_name(".process.json.resume")
    try:
        assert transaction_path.is_file()
        resume_claim = cast(Any, supervisor._test_resume_transaction_claim)  # type: ignore[attr-defined]
        resume_binding = cast(dict[str, object], bound_record["resume_transaction"])
        assert resume_binding["child_handle"] == resume_claim.inherited_child_handle
        assert isinstance(resume_binding["child_handle"], int)
        reconnected = SolverSupervisor.reconnect(capability)
        assert reconnected.state is SolverState.RUNNING
        assert reconnected.pid == process.pid
        assert reconnected.poll() is None
        reconnected_claim = reconnected._process_record_claim
        assert reconnected_claim is not None and reconnected_claim.handle is not None
        assert (
            json.loads(supervisor_module._read_record_fd(reconnected_claim.handle).decode("utf-8"))[
                "state"
            ]
            == SolverState.RUNNING.value
        )
        assert not transaction_path.exists()
    finally:
        if reconnected is not None:
            with contextlib.suppress(BaseException):
                reconnected.cancel()
        _cleanup_windows_resume_recovery(
            capability,
            supervisor,
            process,
            bound_record,
            record_path,
        )


def test_windows_reconnect_recovers_crash_after_native_resume_before_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        capability,
        supervisor,
        process,
        _authority,
        bound_record,
        _running_record,
        record_path,
    ) = _prepare_windows_resume_recovery(
        tmp_path,
        monkeypatch,
        resume_before_crash=True,
    )
    reconnected: SolverSupervisor | None = None
    transaction_path = record_path.with_name(".process.json.resume")
    try:
        assert transaction_path.is_file()
        reconnected = SolverSupervisor.reconnect(capability)
        assert reconnected.state is SolverState.RUNNING
        assert reconnected.pid == process.pid
        assert reconnected.poll() is None
        reconnected_claim = reconnected._process_record_claim
        assert reconnected_claim is not None and reconnected_claim.handle is not None
        assert (
            json.loads(supervisor_module._read_record_fd(reconnected_claim.handle).decode("utf-8"))[
                "state"
            ]
            == SolverState.RUNNING.value
        )
        assert not transaction_path.exists()
    finally:
        if reconnected is not None:
            with contextlib.suppress(BaseException):
                reconnected.cancel()
        _cleanup_windows_resume_recovery(
            capability,
            supervisor,
            process,
            bound_record,
            record_path,
        )


def test_windows_reconnect_rejects_torn_resume_transaction_without_touching_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        capability,
        supervisor,
        process,
        _authority,
        bound_record,
        _running_record,
        record_path,
    ) = _prepare_windows_resume_recovery(
        tmp_path,
        monkeypatch,
        resume_before_crash=False,
    )
    transaction_path = record_path.with_name(".process.json.resume")
    try:
        transaction_path.write_bytes(b'{"version": 1, "kind":')
        with pytest.raises(SolverOwnershipError, match="transaction|invalid|torn"):
            SolverSupervisor.reconnect(capability)
        assert process.poll() is None
        assert transaction_path.read_bytes() == b'{"version": 1, "kind":'
    finally:
        _cleanup_windows_resume_recovery(
            capability,
            supervisor,
            process,
            bound_record,
            record_path,
        )


def test_windows_reconnect_rejects_parseable_intermediate_resume_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        capability,
        supervisor,
        process,
        _authority,
        bound_record,
        _running_record,
        record_path,
    ) = _prepare_windows_resume_recovery(
        tmp_path,
        monkeypatch,
        resume_before_crash=False,
    )
    transaction_path = record_path.with_name(".process.json.resume")
    try:
        transaction = json.loads(transaction_path.read_text(encoding="utf-8"))
        assert isinstance(transaction, dict)
        transaction["running_record"] = dict(bound_record)
        transaction_path.write_bytes(
            json.dumps(transaction, indent=2, sort_keys=True).encode("utf-8")
        )
        with pytest.raises(SolverOwnershipError, match="transaction|state|digest"):
            SolverSupervisor.reconnect(capability)
        assert process.poll() is None
    finally:
        _cleanup_windows_resume_recovery(
            capability,
            supervisor,
            process,
            bound_record,
            record_path,
        )


def test_windows_reconnect_rejects_resume_transaction_after_record_binding_loss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        capability,
        supervisor,
        process,
        _authority,
        bound_record,
        _running_record,
        record_path,
    ) = _prepare_windows_resume_recovery(
        tmp_path,
        monkeypatch,
        resume_before_crash=False,
    )
    transaction_path = record_path.with_name(".process.json.resume")
    try:
        foreign_path = record_path.with_name("process.foreign.json")
        foreign_path.write_bytes(json.dumps(bound_record, indent=2, sort_keys=True).encode("utf-8"))
        os.replace(os.fspath(foreign_path), os.fspath(record_path))
        with pytest.raises(SolverOwnershipError, match="record|binding|identity"):
            SolverSupervisor.reconnect(capability)
        assert process.poll() is None
        assert transaction_path.is_file()
    finally:
        _cleanup_windows_resume_recovery(
            capability,
            supervisor,
            process,
            bound_record,
            record_path,
        )


def test_windows_owned_record_rollback_uses_held_handle_without_path_reopen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "nt":
        pytest.fail("required Windows transaction test executed on a non-Windows host")

    capability = _capability(tmp_path, monkeypatch, code="import time; time.sleep(30)")
    supervisor = SolverSupervisor(capability)
    supervisor.start()
    claim = supervisor._process_record_claim
    assert claim is not None
    original_open = supervisor_module._windows_open_process_record
    reopen_attempts: list[Path] = []

    def reject_path_reopen(path: Path, *, create: bool, delete_access: bool = False) -> int:
        if not create:
            reopen_attempts.append(path)
            raise AssertionError("rollback reopened process record by path")
        return original_open(path, create=create, delete_access=delete_access)

    try:
        with monkeypatch.context() as rollback_patch:
            rollback_patch.setattr(
                supervisor_module,
                "_windows_open_process_record",
                reject_path_reopen,
            )
            failures = supervisor._rollback_process_record()
        assert failures == ()
        assert not reopen_attempts
    finally:
        process = supervisor._process
        if process is not None:
            supervisor._terminate_owned_process(process)
        supervisor._release_process_authority()
        supervisor._process = None
        supervisor._process_record = None
        supervisor._close_filesystem_authority()
    assert not supervisor.process_record_path.exists()


class _OrderingProcess:
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


class _OrderingAuthority:
    def __init__(self, events: list[str]) -> None:
        self._events = events
        self._process: _OrderingProcess | None = None
        self._context_digest = "f" * 64

    @property
    def claim(self) -> dict[str, object]:
        return {
            "kind": "windows-job",
            "attempt_binding": "test",
            "name": "test-job",
            "context_digest": self._context_digest,
            "root_pid": None if self._process is None else self._process.pid,
            "root_creation_identity": "windows:test",
            "root_thread_id": 4243,
            "root_thread_creation_identity": "windows:4243",
        }

    def child_environment(self) -> dict[str, str]:
        return {}

    def child_handle(self) -> int:
        return 9876

    def bind(self, pid: int, expected_creation_identity: str | None = None) -> None:
        assert pid == _OrderingProcess.pid
        assert expected_creation_identity == "windows:test"
        self._events.append("bind")

    def resume(self, pid: int) -> None:
        assert pid == _OrderingProcess.pid
        self._events.append("resume")

    def terminate(self, pid: int, *, force: bool = False) -> None:
        del force
        assert pid == _OrderingProcess.pid
        self._events.append("terminate")
        if self._process is not None:
            self._process._return_code = -15

    def close(self) -> None:
        self._events.append("close")

    def drain(self) -> None:
        self._events.append("drain")


class _CleanupFailureAuthority(_OrderingAuthority):
    def terminate(self, pid: int, *, force: bool = False) -> None:
        assert pid == _OrderingProcess.pid
        self._events.append("terminate-force" if force else "terminate")
        raise ProcessAuthorityError("synthetic termination failure")

    def drain(self) -> None:
        self._events.append("drain")
        raise ProcessAuthorityError("synthetic drain failure")

    def close(self) -> None:
        self._events.append("close")
        raise ProcessAuthorityError("synthetic close failure")


def test_normal_exit_drain_failure_precedes_validation_and_cannot_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    validator = LogValidator()
    original_validate = validator.validate

    def track_validation(path: str | Path) -> LogValidation:
        events.append("validate")
        return original_validate(path)

    monkeypatch.setattr(validator, "validate", track_validation)
    supervisor = SolverSupervisor(
        _capability(tmp_path, monkeypatch, code="pass"),
        log_validator=validator,
    ).start()
    authority = supervisor._process_authority
    assert authority is not None
    original_drain = authority.drain
    drain_attempts = 0

    def fail_first_drain() -> None:
        nonlocal drain_attempts
        drain_attempts += 1
        events.append(f"drain:{drain_attempts}")
        if drain_attempts == 1:
            raise ProcessAuthorityError("synthetic early drain failure")
        original_drain()

    monkeypatch.setattr(authority, "drain", fail_first_drain)

    with pytest.raises(SolverOwnershipError, match="drain|terminal cleanup"):
        supervisor.wait(timeout_seconds=5)

    assert events == ["drain:1", "drain:2"]
    assert supervisor.state is SolverState.FAILED
    assert supervisor._result is None
    assert supervisor._result_latch is None
    assert supervisor._process is None
    assert supervisor._process_authority is None


def test_timeout_drain_failure_precedes_completion_and_retries_same_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    supervisor = SolverSupervisor(
        _capability(tmp_path, monkeypatch, code="import time; time.sleep(30)")
    ).start()
    authority = supervisor._process_authority
    assert authority is not None
    original_drain = authority.drain
    drain_attempts = 0

    def fail_first_drain() -> None:
        nonlocal drain_attempts
        drain_attempts += 1
        events.append(f"drain:{drain_attempts}")
        if drain_attempts == 1:
            raise ProcessAuthorityError("synthetic timeout drain failure")
        original_drain()

    monkeypatch.setattr(authority, "drain", fail_first_drain)
    monkeypatch.setattr(
        supervisor,
        "_complete",
        lambda *_args: events.append("complete"),
    )

    with pytest.raises(SolverOwnershipError, match="drain|terminal cleanup"):
        supervisor.wait(timeout_seconds=0.01)

    assert events == ["drain:1", "drain:2"]
    assert supervisor.state is SolverState.FAILED
    assert supervisor._result is None
    assert supervisor._result_latch is None
    assert supervisor._process is None
    assert supervisor._process_authority is None


def test_cancel_drains_after_root_already_exited_before_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancellation must drain the held authority even when terminate is a no-op."""
    events: list[str] = []
    supervisor = SolverSupervisor(_capability(tmp_path, monkeypatch, code="pass")).start()
    process = supervisor._process
    authority = supervisor._process_authority
    assert process is not None and authority is not None
    assert process.wait(timeout=5.0) == 0
    original_drain = authority.drain

    def tracked_drain() -> None:
        events.append("drain")
        original_drain()

    monkeypatch.setattr(authority, "drain", tracked_drain)

    def fake_complete(state: SolverState, return_code: int | None) -> object:
        del state, return_code
        events.append("validate")
        return object()

    monkeypatch.setattr(supervisor, "_complete", fake_complete)
    supervisor.cancel()
    assert events == ["drain", "validate"]


def test_cancel_drain_failure_blocks_completion_and_retries_same_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    supervisor = SolverSupervisor(_capability(tmp_path, monkeypatch, code="pass")).start()
    process = supervisor._process
    authority = supervisor._process_authority
    assert process is not None and authority is not None
    assert process.wait(timeout=5.0) == 0
    attempts = 0

    def fail_once_then_succeed() -> None:
        nonlocal attempts
        attempts += 1
        events.append(f"drain:{attempts}")
        if attempts == 1:
            raise ProcessAuthorityError("synthetic cancel drain failure")

    monkeypatch.setattr(authority, "drain", fail_once_then_succeed)
    monkeypatch.setattr(supervisor, "_complete", lambda *_args: events.append("validate"))
    with pytest.raises(SolverOwnershipError, match="drain|terminal cleanup"):
        supervisor.cancel()
    assert events == ["drain:1", "drain:2"]
    assert supervisor._process_authority is None


def test_windows_persists_bound_record_before_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ordering probe must fail on the old resume-before-record sequence."""

    capability = _capability(tmp_path, monkeypatch, code="pass")
    events: list[str] = []
    authority = _OrderingAuthority(events)

    class StartupInfo:
        lpAttributeList: object | None = None

    def fake_popen(*args: object, **kwargs: object) -> _OrderingProcess:
        del args
        creation_flags = kwargs["creationflags"]
        assert isinstance(creation_flags, int)
        assert creation_flags & 0x00000004
        events.append("created-suspended")
        process = _OrderingProcess(events)
        authority._process = process
        return process

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(subprocess, "STARTUPINFO", StartupInfo, raising=False)

    def fake_create(
        attempt_root: Path,
        context_digest: str | None = None,
        *,
        root_pid: int | None = None,
        root_creation_identity: str | None = None,
    ) -> _OrderingAuthority:
        del attempt_root
        del root_pid, root_creation_identity
        if context_digest is not None:
            authority._context_digest = context_digest
        return authority

    monkeypatch.setattr(ProcessAuthority, "create", fake_create)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        supervisor_module,
        "_process_metadata",
        lambda pid: supervisor_module._ProcessMetadata(
            str(Path(sys.executable)),
            "windows:test",
            True,
            None,
            datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )
    original_write = SolverSupervisor._write_process_record

    def record_write(supervisor: SolverSupervisor, record: dict[str, object]) -> None:
        events.append(f"persist:{record.get('state')}")
        original_write(supervisor, record)

    monkeypatch.setattr(SolverSupervisor, "_write_process_record", record_write)
    supervisor = SolverSupervisor(capability)
    try:
        supervisor.start()
        first_persist = next(
            index for index, event in enumerate(events) if event.startswith("persist:")
        )
        resume = events.index("resume")
        assert first_persist < resume
        assert events[first_persist] == "persist:BOUND_SUSPENDED"
        assert events.index("persist:RUNNING") > resume
    finally:
        supervisor.cancel()


def test_windows_process_image_replacement_fails_before_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A suspended child with a replaced image must never become RUNNING."""

    capability = _capability(tmp_path, monkeypatch, code="pass")
    events: list[str] = []
    authority = _OrderingAuthority(events)

    class StartupInfo:
        lpAttributeList: object | None = None

    def fake_popen(*args: object, **kwargs: object) -> _OrderingProcess:
        del args
        creation_flags = kwargs["creationflags"]
        assert isinstance(creation_flags, int)
        assert creation_flags & 0x00000004
        events.append("created-suspended")
        process = _OrderingProcess(events)
        authority._process = process
        return process

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(subprocess, "STARTUPINFO", StartupInfo, raising=False)

    def fake_create(
        attempt_root: Path,
        context_digest: str | None = None,
        *,
        root_pid: int | None = None,
        root_creation_identity: str | None = None,
    ) -> _OrderingAuthority:
        del attempt_root
        del root_pid, root_creation_identity
        if context_digest is not None:
            authority._context_digest = context_digest
        return authority

    replacement = tmp_path / "replacement-febio.exe"
    replacement.write_bytes(b"replacement image")
    metadata_calls = 0

    def process_metadata(pid: int) -> supervisor_module._ProcessMetadata:
        nonlocal metadata_calls
        assert pid == _OrderingProcess.pid
        metadata_calls += 1
        executable = Path(sys.executable) if metadata_calls == 1 else replacement
        return supervisor_module._ProcessMetadata(
            str(executable),
            "windows:test",
            True,
            None,
            datetime(2026, 1, 1, tzinfo=UTC),
        )

    monkeypatch.setattr(ProcessAuthority, "create", fake_create)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(supervisor_module, "_process_metadata", process_metadata)
    original_write = SolverSupervisor._write_process_record

    def record_write(supervisor: SolverSupervisor, record: dict[str, object]) -> None:
        events.append(f"persist:{record.get('state')}")
        original_write(supervisor, record)

    monkeypatch.setattr(SolverSupervisor, "_write_process_record", record_write)
    supervisor = SolverSupervisor(capability)

    with pytest.raises(SolverLaunchError, match="executable|image|runtime"):
        supervisor.start()

    assert "resume" not in events
    assert "persist:RUNNING" not in events
    assert events.count("terminate") == 1
    assert events.count("close") == 1
    assert supervisor.state is SolverState.FAILED
    assert supervisor.process_id is None
    assert not supervisor.process_record_path.exists()


def test_windows_runtime_claim_close_failure_after_resume_retains_exact_retry_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A post-resume runtime claim close failure keeps its exact handle reachable."""

    if os.name != "nt":
        pytest.fail("required Windows runtime claim test executed on a non-Windows host")

    capability = _capability(tmp_path, monkeypatch, code="import time; time.sleep(30)")
    captured_claims: list[runtime_module._RuntimeLaunchClaim] = []
    original_acquire = _acquire_runtime_launch_claim

    def capture_claim(value: object) -> runtime_module._RuntimeLaunchClaim:
        claim = original_acquire(value)
        captured_claims.append(claim)
        return claim

    monkeypatch.setattr(supervisor_module, "_acquire_runtime_launch_claim", capture_claim)
    launched: list[subprocess.Popen[bytes]] = []
    original_cleanup = SolverSupervisor._cleanup_failed_start

    def capture_cleanup(
        instance: SolverSupervisor,
        process: subprocess.Popen[bytes] | None,
        authority: ProcessAuthority | None,
        bound: bool,
    ) -> tuple[BaseException, ...]:
        if process is not None:
            launched.append(process)
        return original_cleanup(instance, process, authority, bound)

    monkeypatch.setattr(SolverSupervisor, "_cleanup_failed_start", capture_cleanup)
    close_failures = True
    close_attempts: list[int] = []
    original_claim_close = runtime_module._RuntimeLaunchClaim.close

    def flaky_close(claim: runtime_module._RuntimeLaunchClaim) -> None:
        handle = claim.handle
        assert handle is not None
        close_attempts.append(handle)
        if close_failures:
            raise runtime_module.RuntimeProbeError("synthetic persistent runtime close failure")
        original_claim_close(claim)

    monkeypatch.setattr(runtime_module._RuntimeLaunchClaim, "close", flaky_close)
    supervisor = SolverSupervisor(capability)
    try:
        with pytest.raises(SolverOwnershipError, match="cleanup|closed|authority"):
            supervisor.start()

        assert captured_claims
        claim = captured_claims[0]
        assert claim.handle is not None
        runtime_fd = claim.handle
        assert close_attempts
        assert close_attempts == [runtime_fd] * len(close_attempts)
        assert supervisor._runtime_launch_claim is claim
        assert supervisor.state is SolverState.FAILED
        assert supervisor._process is None
        assert launched
        child = launched[0]
        assert child.poll() is not None
        assert child.wait(timeout=0) == child.returncode

        close_failures = False
        supervisor._close_filesystem_authority()
        assert supervisor._runtime_launch_claim is None
        with pytest.raises(OSError):
            os.fstat(runtime_fd)
    finally:
        close_failures = False
        if captured_claims and captured_claims[0].handle is not None:
            original_claim_close(captured_claims[0])
        for child in launched:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5.0)


def test_windows_runtime_claim_close_failure_before_bind_retains_exact_retry_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed pre-bind start keeps a runtime claim for exact later cleanup."""

    if os.name != "nt":
        pytest.fail("required Windows runtime claim test executed on a non-Windows host")

    capability = _capability(tmp_path, monkeypatch, code="import time; time.sleep(30)")
    captured_claims: list[runtime_module._RuntimeLaunchClaim] = []
    original_acquire = _acquire_runtime_launch_claim

    def capture_claim(value: object) -> runtime_module._RuntimeLaunchClaim:
        claim = original_acquire(value)
        captured_claims.append(claim)
        return claim

    monkeypatch.setattr(supervisor_module, "_acquire_runtime_launch_claim", capture_claim)
    launched: list[subprocess.Popen[bytes]] = []
    original_cleanup = SolverSupervisor._cleanup_failed_start

    def capture_cleanup(
        instance: SolverSupervisor,
        process: subprocess.Popen[bytes] | None,
        authority: ProcessAuthority | None,
        bound: bool,
    ) -> tuple[BaseException, ...]:
        if process is not None:
            launched.append(process)
        return original_cleanup(instance, process, authority, bound)

    monkeypatch.setattr(SolverSupervisor, "_cleanup_failed_start", capture_cleanup)
    metadata_calls = 0
    original_metadata = supervisor_module._process_metadata

    def fail_before_bind(pid: int) -> supervisor_module._ProcessMetadata:
        nonlocal metadata_calls
        metadata_calls += 1
        if metadata_calls == 1:
            raise OSError("synthetic pre-bind metadata failure")
        return original_metadata(pid)

    monkeypatch.setattr(supervisor_module, "_process_metadata", fail_before_bind)
    close_failures = True
    close_attempts: list[int] = []
    original_claim_close = runtime_module._RuntimeLaunchClaim.close

    def flaky_close(claim: runtime_module._RuntimeLaunchClaim) -> None:
        handle = claim.handle
        assert handle is not None
        close_attempts.append(handle)
        if close_failures:
            raise runtime_module.RuntimeProbeError("synthetic persistent runtime close failure")
        original_claim_close(claim)

    monkeypatch.setattr(runtime_module._RuntimeLaunchClaim, "close", flaky_close)
    supervisor = SolverSupervisor(capability)
    try:
        with pytest.raises(SolverOwnershipError, match="cleanup|closed|authority"):
            supervisor.start()

        assert captured_claims
        claim = captured_claims[0]
        assert claim.handle is not None
        runtime_fd = claim.handle
        assert close_attempts
        assert close_attempts == [runtime_fd] * len(close_attempts)
        assert supervisor._runtime_launch_claim is claim
        assert supervisor.state is SolverState.FAILED
        assert supervisor._process is None
        assert launched
        child = launched[0]
        assert child.poll() is not None
        assert child.wait(timeout=0) == child.returncode
        assert supervisor._process_record is None

        close_failures = False
        supervisor._close_filesystem_authority()
        assert supervisor._runtime_launch_claim is None
        with pytest.raises(OSError):
            os.fstat(runtime_fd)
    finally:
        close_failures = False
        if captured_claims and captured_claims[0].handle is not None:
            original_claim_close(captured_claims[0])
        for child in launched:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5.0)


def test_windows_invalid_process_record_candidate_close_failure_retains_exact_retry_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Malformed record candidates remain owned when their close keeps failing."""

    if os.name != "nt":
        pytest.fail("required Windows process-record test executed on a non-Windows host")

    capability = _capability(tmp_path, monkeypatch, code="pass")
    supervisor = SolverSupervisor(capability)
    record_path = Path(os.fspath(supervisor.process_record_path))
    record_path.write_bytes(b"{")
    candidate_fds: list[int] = []
    original_open = supervisor_module._windows_open_process_record

    def capture_open(path: Path, *, create: bool, delete_access: bool = True) -> int:
        fd = original_open(path, create=create, delete_access=delete_access)
        candidate_fds.append(fd)
        return fd

    monkeypatch.setattr(supervisor_module, "_windows_open_process_record", capture_open)
    close_failures = True
    close_attempts: list[int] = []
    original_close = os.close

    def fail_candidate_close(fd: int) -> None:
        if fd in candidate_fds and close_failures:
            close_attempts.append(fd)
            raise OSError("synthetic persistent process-record close failure")
        original_close(fd)

    try:
        with monkeypatch.context() as read_patch:
            read_patch.setattr(os, "close", fail_candidate_close)
            with pytest.raises(SolverOwnershipError, match="invalid|closed"):
                supervisor._read_process_record()

        assert candidate_fds
        candidate_fd = candidate_fds[0]
        assert close_attempts == [candidate_fd]
        owned_candidates = getattr(supervisor, "_process_record_candidates", ())
        assert any(candidate.handle == candidate_fd for candidate in owned_candidates)

        close_failures = False
        supervisor._close_filesystem_authority()
        assert not getattr(supervisor, "_process_record_candidates", ())
        with pytest.raises(OSError):
            os.fstat(candidate_fd)
    finally:
        close_failures = False
        for candidate_fd in candidate_fds:
            try:
                os.fstat(candidate_fd)
            except OSError:
                continue
            else:
                original_close(candidate_fd)
        if record_path.exists():
            record_path.unlink()


def test_windows_runtime_claim_finalizer_persistent_close_transfers_exact_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A collected supervisor cannot strand a runtime claim after close failure."""

    if os.name != "nt":
        pytest.fail("required Windows runtime finalizer test executed on a non-Windows host")

    capability = _capability(tmp_path, monkeypatch, code="import time; time.sleep(30)")
    captured_claims: list[runtime_module._RuntimeLaunchClaim] = []
    original_acquire = _acquire_runtime_launch_claim

    def capture_claim(value: object) -> runtime_module._RuntimeLaunchClaim:
        claim = original_acquire(value)
        captured_claims.append(claim)
        return claim

    monkeypatch.setattr(supervisor_module, "_acquire_runtime_launch_claim", capture_claim)
    launched: list[subprocess.Popen[bytes]] = []
    original_cleanup = SolverSupervisor._cleanup_failed_start

    def capture_cleanup(
        instance: SolverSupervisor,
        process: subprocess.Popen[bytes] | None,
        authority: ProcessAuthority | None,
        bound: bool,
    ) -> tuple[BaseException, ...]:
        if process is not None:
            launched.append(process)
        return original_cleanup(instance, process, authority, bound)

    monkeypatch.setattr(SolverSupervisor, "_cleanup_failed_start", capture_cleanup)
    close_failures = True
    close_attempts: list[int] = []
    original_claim_close = runtime_module._RuntimeLaunchClaim.close

    def flaky_close(claim: runtime_module._RuntimeLaunchClaim) -> None:
        handle = claim.handle
        assert handle is not None
        close_attempts.append(handle)
        if close_failures:
            raise runtime_module.RuntimeProbeError("synthetic persistent runtime close failure")
        original_claim_close(claim)

    monkeypatch.setattr(runtime_module._RuntimeLaunchClaim, "close", flaky_close)
    supervisor: SolverSupervisor | None = SolverSupervisor(capability)
    supervisor_ref = weakref.ref(supervisor)
    claim: runtime_module._RuntimeLaunchClaim | None = None
    try:
        assert supervisor is not None
        with pytest.raises(SolverOwnershipError, match="cleanup|closed|authority"):
            supervisor.start()

        assert captured_claims
        claim = captured_claims[0]
        assert claim.handle is not None
        runtime_fd = claim.handle
        assert close_attempts
        assert close_attempts == [runtime_fd] * len(close_attempts)
        assert launched
        child = launched[0]
        assert child.poll() is not None
        assert child.wait(timeout=0) == child.returncode

        del supervisor
        supervisor = None
        for _ in range(3):
            gc.collect()
            if supervisor_ref() is None:
                break

        assert supervisor_ref() is None
        durable_owner = getattr(supervisor_module, "_DURABLE_CLAIM_CLEANUP", None)
        assert durable_owner is not None
        assert sum(held is claim for held in durable_owner._runtime_claims) == 1
        assert claim.handle == runtime_fd
        os.fstat(runtime_fd)

        close_failures = False
        supervisor_module._drain_durable_cleanup()
        assert claim.handle is None
        assert not any(held is claim for held in durable_owner._runtime_claims)
        with pytest.raises(OSError):
            os.fstat(runtime_fd)
    finally:
        close_failures = False
        if supervisor is not None:
            with contextlib.suppress(BaseException):
                supervisor._close_filesystem_authority()
        with contextlib.suppress(BaseException):
            supervisor_module._drain_durable_cleanup()
        if claim is not None and claim.handle is not None:
            with contextlib.suppress(BaseException):
                original_claim_close(claim)
        for child in launched:
            if child.poll() is None:
                with contextlib.suppress(BaseException):
                    child.kill()
                with contextlib.suppress(BaseException):
                    child.wait(timeout=5.0)


def test_windows_invalid_record_candidate_finalizer_persistent_close_transfers_exact_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A collected malformed-record supervisor keeps its exact candidate and registry lease."""

    if os.name != "nt":
        pytest.fail("required Windows record finalizer test executed on a non-Windows host")

    capability = _capability(tmp_path, monkeypatch, code="pass")
    supervisor: SolverSupervisor | None = SolverSupervisor(capability)
    assert supervisor is not None
    record_path = Path(os.fspath(supervisor.process_record_path))
    record_content = b"{"
    record_path.write_bytes(record_content)
    candidate_fds: list[int] = []
    original_open = supervisor_module._windows_open_process_record

    def capture_open(path: Path, *, create: bool, delete_access: bool = True) -> int:
        fd = original_open(path, create=create, delete_access=delete_access)
        candidate_fds.append(fd)
        return fd

    monkeypatch.setattr(supervisor_module, "_windows_open_process_record", capture_open)
    close_failures = True
    close_attempts: list[int] = []
    original_close = os.close

    def flaky_close(fd: int) -> None:
        if fd in candidate_fds and close_failures:
            close_attempts.append(fd)
            raise OSError("synthetic persistent process-record close failure")
        original_close(fd)

    supervisor_ref = weakref.ref(supervisor)
    candidate: supervisor_module._ProcessRecordClaim | None = None
    try:
        with monkeypatch.context() as close_patch:
            close_patch.setattr(os, "close", flaky_close)
            assert supervisor is not None
            with pytest.raises(SolverOwnershipError, match="invalid|closed"):
                supervisor._read_process_record()

            assert candidate_fds
            candidate_fd = candidate_fds[0]
            candidate = next(
                held
                for held in supervisor._process_record_candidates
                if held.handle == candidate_fd
            )
            assert close_attempts == [candidate_fd]
            del supervisor
            supervisor = None
            for _ in range(3):
                gc.collect()
                if supervisor_ref() is None:
                    break

            assert supervisor_ref() is None
            assert candidate.handle == candidate_fd
            record_key = supervisor_module._windows_record_claim_key(record_path)
            assert supervisor_module._WINDOWS_RECORD_CLAIMS.get(record_key) == [
                (candidate_fd, candidate.device, candidate.inode)
            ]
            durable_owner = getattr(supervisor_module, "_DURABLE_CLAIM_CLEANUP", None)
            assert durable_owner is not None
            assert sum(held is candidate for held in durable_owner._process_record_claims) == 1
            os.fstat(candidate_fd)

            close_failures = False
            supervisor_module._drain_durable_cleanup()
            assert candidate.handle is None
            assert not any(held is candidate for held in durable_owner._process_record_claims)
            with pytest.raises(OSError):
                os.fstat(candidate_fd)
            assert record_path.read_bytes() == record_content
            assert record_key not in supervisor_module._WINDOWS_RECORD_CLAIMS
    finally:
        close_failures = False
        if supervisor is not None:
            with contextlib.suppress(BaseException):
                supervisor._close_filesystem_authority()
        with contextlib.suppress(BaseException):
            supervisor_module._drain_durable_cleanup()
        if candidate is not None and candidate.handle is not None:
            with contextlib.suppress(BaseException):
                original_close(candidate.handle)
            candidate.handle = None
        if candidate is not None:
            supervisor_module._windows_unregister_record_claim(record_path, candidate_fds[0])
        if record_path.exists():
            record_path.unlink()


def test_windows_durable_claim_drain_retries_exact_record_identity_idempotently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repeated failed drains retain one exact record claim until one successful drain."""

    if os.name != "nt":
        pytest.fail("required Windows durable-drain test executed on a non-Windows host")

    capability = _capability(tmp_path, monkeypatch, code="pass")
    supervisor: SolverSupervisor | None = SolverSupervisor(capability)
    assert supervisor is not None
    record_path = Path(os.fspath(supervisor.process_record_path))
    record_path.write_bytes(b"{")
    candidate_fds: list[int] = []
    original_open = supervisor_module._windows_open_process_record

    def capture_open(path: Path, *, create: bool, delete_access: bool = True) -> int:
        fd = original_open(path, create=create, delete_access=delete_access)
        candidate_fds.append(fd)
        return fd

    monkeypatch.setattr(supervisor_module, "_windows_open_process_record", capture_open)
    close_failures = True
    close_attempts: list[int] = []
    original_close = os.close

    def flaky_close(fd: int) -> None:
        if fd in candidate_fds and close_failures:
            close_attempts.append(fd)
            raise OSError("synthetic persistent process-record close failure")
        original_close(fd)

    supervisor_ref = weakref.ref(supervisor)
    candidate: supervisor_module._ProcessRecordClaim | None = None
    try:
        with monkeypatch.context() as close_patch:
            close_patch.setattr(os, "close", flaky_close)
            assert supervisor is not None
            with pytest.raises(SolverOwnershipError, match="invalid|closed"):
                supervisor._read_process_record()
            candidate_fd = candidate_fds[0]
            candidate = next(
                held
                for held in supervisor._process_record_candidates
                if held.handle == candidate_fd
            )
            del supervisor
            supervisor = None
            for _ in range(3):
                gc.collect()
                if supervisor_ref() is None:
                    break
            assert supervisor_ref() is None
            durable_owner = getattr(supervisor_module, "_DURABLE_CLAIM_CLEANUP", None)
            assert durable_owner is not None
            record_key = supervisor_module._windows_record_claim_key(record_path)
            expected_entry = (candidate_fd, candidate.device, candidate.inode)

            for _ in range(2):
                supervisor_module._drain_durable_cleanup()
                assert candidate.handle == candidate_fd
                assert sum(held is candidate for held in durable_owner._process_record_claims) == 1
                assert supervisor_module._WINDOWS_RECORD_CLAIMS.get(record_key) == [expected_entry]
                os.fstat(candidate_fd)

            close_failures = False
            supervisor_module._drain_durable_cleanup()
            successful_attempts = len(close_attempts)
            assert candidate.handle is None
            assert record_key not in supervisor_module._WINDOWS_RECORD_CLAIMS
            assert not any(held is candidate for held in durable_owner._process_record_claims)
            with pytest.raises(OSError):
                os.fstat(candidate_fd)

            supervisor_module._drain_durable_cleanup()
            assert len(close_attempts) == successful_attempts
            assert record_key not in supervisor_module._WINDOWS_RECORD_CLAIMS
    finally:
        close_failures = False
        if supervisor is not None:
            with contextlib.suppress(BaseException):
                supervisor._close_filesystem_authority()
        with contextlib.suppress(BaseException):
            supervisor_module._drain_durable_cleanup()
        if candidate is not None and candidate.handle is not None:
            with contextlib.suppress(BaseException):
                original_close(candidate.handle)
            candidate.handle = None
        if candidate is not None and candidate_fds:
            supervisor_module._windows_unregister_record_claim(record_path, candidate_fds[0])
        if record_path.exists():
            record_path.unlink()


def test_windows_process_record_candidate_validation_and_close_failure_preserves_prior_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A replacement candidate cannot displace a live prior claim before validation."""

    if os.name != "nt":
        pytest.fail("required Windows process-record test executed on a non-Windows host")

    capability = _capability(tmp_path, monkeypatch, code="import time; time.sleep(30)")
    supervisor = SolverSupervisor(capability)
    supervisor.start()
    prior_claim = supervisor._process_record_claim
    assert prior_claim is not None and prior_claim.handle is not None
    prior_fd = prior_claim.handle
    record_path = Path(os.fspath(prior_claim.path))
    record_key = supervisor_module._windows_record_claim_key(record_path)
    prior_entry = (prior_fd, prior_claim.device, prior_claim.inode)
    original_open = supervisor_module._windows_open_process_record
    candidate_fds: list[int] = []

    def capture_open(path: Path, *, create: bool, delete_access: bool = True) -> int:
        fd = original_open(path, create=create, delete_access=delete_access)
        candidate_fds.append(fd)
        return fd

    supervisor._process_record_claim = None
    candidate_from_installed: supervisor_module._ProcessRecordClaim | None = None
    try:
        with monkeypatch.context() as read_patch:
            read_patch.setattr(supervisor_module, "_windows_open_process_record", capture_open)
            record = supervisor._read_process_record()
        candidate_from_installed = supervisor._process_record_claim
        supervisor._process_record_claim = prior_claim

        assert candidate_fds
        candidate_fd = candidate_fds[0]
        owned_candidates = getattr(supervisor, "_process_record_candidates", ())
        assert any(candidate.handle == candidate_fd for candidate in owned_candidates)
        candidate = next(
            candidate for candidate in owned_candidates if candidate.handle == candidate_fd
        )
        assert supervisor._process_record_claim is prior_claim
        invalid_record = dict(record)
        invalid_record["state"] = "NOT_RUNNING"
        with pytest.raises(SolverOwnershipError, match="reconnectable|RUNNING"):
            supervisor._validate_process_record(invalid_record)
        assert supervisor._process_record_claim is prior_claim
        assert os.fstat(prior_fd).st_size > 0
        assert supervisor_module._WINDOWS_RECORD_CLAIMS.get(record_key) == [
            prior_entry,
            (candidate_fd, candidate.device, candidate.inode),
        ]

        close_failures = True
        close_attempts: list[int] = []
        original_close = os.close

        def fail_candidate_close(fd: int) -> None:
            if fd == candidate_fd:
                close_attempts.append(fd)
                if close_failures:
                    raise OSError("synthetic persistent candidate close failure")
            original_close(fd)

        with monkeypatch.context() as close_patch:
            close_patch.setattr(os, "close", fail_candidate_close)
            failures = supervisor._release_process_record_claim(candidate)
            assert failures
            assert candidate.handle == candidate_fd
            assert supervisor._process_record_claim is prior_claim
            assert os.fstat(prior_fd).st_size > 0
            assert supervisor_module._WINDOWS_RECORD_CLAIMS.get(record_key) == [
                prior_entry,
                (candidate_fd, candidate.device, candidate.inode),
            ]
            close_failures = False
            assert supervisor._release_process_record_claim(candidate) == ()
            assert supervisor._release_process_record_claim(candidate) == ()

        assert close_attempts == [candidate_fd, candidate_fd]
        assert supervisor._process_record_claim is prior_claim
        assert supervisor_module._WINDOWS_RECORD_CLAIMS.get(record_key) == [prior_entry]
    finally:
        if candidate_from_installed is not None and candidate_from_installed.handle is not None:
            with contextlib.suppress(OSError):
                os.close(candidate_from_installed.handle)
        supervisor._process_record_claim = prior_claim
        if supervisor.process_id is not None:
            supervisor.cancel()


def test_windows_record_claim_close_failure_retains_exact_retry_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed record close keeps the exact claim for a later close retry."""

    if os.name != "nt":
        pytest.fail("required Windows record claim test executed on a non-Windows host")

    capability = _capability(tmp_path, monkeypatch, code="import time; time.sleep(30)")
    supervisor = SolverSupervisor(capability)
    supervisor.start()
    process = supervisor._process
    claim = supervisor._process_record_claim
    assert process is not None
    assert claim is not None and claim.handle is not None
    record_fd = claim.handle
    record_path = Path(os.fspath(claim.path))
    record_key = supervisor_module._windows_record_claim_key(record_path)
    record_entry = (record_fd, claim.device, claim.inode)
    process_record = supervisor_module._read_record_fd(record_fd)
    close_attempts = 0
    original_close = os.close

    def fail_once(fd: int) -> None:
        nonlocal close_attempts
        if fd == record_fd and close_attempts == 0:
            close_attempts += 1
            raise OSError("synthetic process record close failure")
        original_close(fd)

    try:
        with monkeypatch.context() as close_patch:
            close_patch.setattr(os, "close", fail_once)
            with pytest.raises(SolverOwnershipError, match="handles|closed"):
                supervisor._close_filesystem_authority()

        assert supervisor.state is SolverState.RUNNING
        assert supervisor._process is process
        assert supervisor._process_record_claim is claim
        assert claim.handle == record_fd
        assert supervisor_module._WINDOWS_RECORD_CLAIMS.get(record_key) == [record_entry]
        assert supervisor_module._read_record_fd(record_fd) == process_record
        assert process.poll() is None

        supervisor._close_filesystem_authority()
        assert supervisor._process_record_claim is None
        assert record_key not in supervisor_module._WINDOWS_RECORD_CLAIMS
        with pytest.raises(OSError):
            os.fstat(record_fd)
        assert process.poll() is None
        assert json.loads(record_path.read_text(encoding="utf-8"))["state"] == "RUNNING"
    finally:
        active_process = supervisor._process
        if active_process is not None and active_process.poll() is None:
            supervisor._terminate_owned_process(active_process)
        supervisor._release_process_authority()
        supervisor._process = None
        supervisor._process_record = None
        if record_path.exists():
            record_path.unlink()


def test_windows_late_failure_preserves_replaced_process_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capability = _capability(tmp_path, monkeypatch, code="pass")
    events: list[str] = []
    authority = _OrderingAuthority(events)

    class StartupInfo:
        lpAttributeList: object | None = None

    def fake_popen(*args: object, **kwargs: object) -> _OrderingProcess:
        del args
        creation_flags = kwargs["creationflags"]
        assert isinstance(creation_flags, int)
        assert creation_flags & 0x00000004
        events.append("created-suspended")
        process = _OrderingProcess(events)
        authority._process = process
        return process

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(subprocess, "STARTUPINFO", StartupInfo, raising=False)

    def fake_create(
        attempt_root: Path,
        context_digest: str | None = None,
        *,
        root_pid: int | None = None,
        root_creation_identity: str | None = None,
    ) -> _OrderingAuthority:
        del attempt_root
        del root_pid, root_creation_identity
        if context_digest is not None:
            authority._context_digest = context_digest
        return authority

    def fail_resume(pid: int) -> None:
        assert pid == _OrderingProcess.pid
        events.append("resume-failure")
        raise OSError("synthetic late resume failure")

    monkeypatch.setattr(ProcessAuthority, "create", fake_create)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        supervisor_module,
        "_process_metadata",
        lambda pid: supervisor_module._ProcessMetadata(
            str(Path(sys.executable)),
            "windows:test",
            True,
            None,
            datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )
    monkeypatch.setattr(authority, "resume", fail_resume)
    original_write = SolverSupervisor._write_process_record
    replacement_installed = False

    def record_write(supervisor: SolverSupervisor, record: dict[str, object]) -> None:
        nonlocal replacement_installed
        original_write(supervisor, record)
        if record.get("state") == "BOUND_SUSPENDED":
            replacement = dict(record)
            replacement["replacement_marker"] = "foreign-regular-file"
            replacement_path = supervisor.process_record_path.with_name("foreign.json")
            replacement_path.write_text(json.dumps(replacement, sort_keys=True), encoding="utf-8")
            try:
                os.replace(os.fspath(replacement_path), os.fspath(supervisor.process_record_path))
            except PermissionError:
                events.append("foreign-replacement-blocked")
            else:
                replacement_installed = True
                events.append("replaced-process-record")

    monkeypatch.setattr(SolverSupervisor, "_write_process_record", record_write)
    supervisor = SolverSupervisor(capability)

    with pytest.raises(SolverLaunchError, match="late resume failure"):
        supervisor.start()

    record_path = supervisor.process_record_path
    if replacement_installed:
        replacement = json.loads(record_path.read_text(encoding="utf-8"))
        assert record_path.is_file()
        assert replacement["replacement_marker"] == "foreign-regular-file"
        assert replacement["state"] == "BOUND_SUSPENDED"
    else:
        assert not record_path.exists()
    assert supervisor.state is SolverState.FAILED
    replacement_event = (
        "replaced-process-record" if replacement_installed else "foreign-replacement-blocked"
    )
    assert events.index(replacement_event) < events.index("resume-failure")
    assert events.count("terminate") == 1
    assert events.count("close") == 1
    assert events.index("terminate") < events.index("close")

    with pytest.raises(SolverOwnershipError, match="invalid|reconnectable|RUNNING"):
        SolverSupervisor.reconnect(capability)


@pytest.mark.parametrize("replace_with_foreign", [False, True])
def test_windows_final_guard_rolls_back_owned_record_without_foreign_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replace_with_foreign: bool,
) -> None:
    capability = _capability(tmp_path, monkeypatch, code="pass")
    events: list[str] = []
    authority = _OrderingAuthority(events)

    class StartupInfo:
        lpAttributeList: object | None = None

    def fake_popen(*args: object, **kwargs: object) -> _OrderingProcess:
        del args
        creation_flags = kwargs["creationflags"]
        assert isinstance(creation_flags, int)
        assert creation_flags & 0x00000004
        events.append("created-suspended")
        process = _OrderingProcess(events)
        authority._process = process
        return process

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(subprocess, "STARTUPINFO", StartupInfo, raising=False)

    def fake_create(
        attempt_root: Path,
        context_digest: str | None = None,
        *,
        root_pid: int | None = None,
        root_creation_identity: str | None = None,
    ) -> _OrderingAuthority:
        del attempt_root
        del root_pid, root_creation_identity
        if context_digest is not None:
            authority._context_digest = context_digest
        return authority

    monkeypatch.setattr(ProcessAuthority, "create", fake_create)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        supervisor_module,
        "_process_metadata",
        lambda pid: supervisor_module._ProcessMetadata(
            str(Path(sys.executable)),
            "windows:test",
            True,
            None,
            datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )
    original_write = SolverSupervisor._write_process_record
    replacement_installed = False

    def record_write(supervisor: SolverSupervisor, record: dict[str, object]) -> None:
        nonlocal replacement_installed
        original_write(supervisor, record)
        if replace_with_foreign and record.get("state") == "RUNNING":
            replacement = dict(record)
            replacement["replacement_marker"] = "foreign-final-guard"
            replacement_path = supervisor.process_record_path.with_name("foreign.json")
            replacement_path.write_text(json.dumps(replacement, sort_keys=True), encoding="utf-8")
            try:
                os.replace(os.fspath(replacement_path), os.fspath(supervisor.process_record_path))
            except PermissionError:
                return
            replacement_installed = True

    monkeypatch.setattr(SolverSupervisor, "_write_process_record", record_write)
    original_revalidate = SolverSupervisor._revalidate_launch_binding
    guard_failed = False

    def final_guard(supervisor: SolverSupervisor) -> None:
        nonlocal guard_failed
        original_revalidate(supervisor)
        if any(event == "persist:RUNNING" for event in events) and not guard_failed:
            guard_failed = True
            raise SolverConfigurationError("synthetic final guard failure")

    def record_events(supervisor: SolverSupervisor, record: dict[str, object]) -> None:
        events.append(f"persist:{record.get('state')}")
        record_write(supervisor, record)

    monkeypatch.setattr(SolverSupervisor, "_revalidate_launch_binding", final_guard)
    monkeypatch.setattr(SolverSupervisor, "_write_process_record", record_events)
    supervisor = SolverSupervisor(capability)

    with pytest.raises(SolverConfigurationError, match="final guard"):
        supervisor.start()

    assert supervisor.state is SolverState.FAILED
    assert supervisor._process is None
    assert supervisor._process_authority is None
    record_path = supervisor.process_record_path
    if replacement_installed:
        replacement = json.loads(record_path.read_text(encoding="utf-8"))
        assert replacement["replacement_marker"] == "foreign-final-guard"
    else:
        assert not record_path.exists()


@pytest.mark.parametrize("replace_with_foreign", [False, True])
def test_windows_bound_suspended_rollback_removes_owned_record_without_foreign_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replace_with_foreign: bool,
) -> None:
    capability = _capability(tmp_path, monkeypatch, code="pass")
    events: list[str] = []
    authority = _OrderingAuthority(events)

    class StartupInfo:
        lpAttributeList: object | None = None

    def fake_popen(*args: object, **kwargs: object) -> _OrderingProcess:
        del args
        creation_flags = kwargs["creationflags"]
        assert isinstance(creation_flags, int)
        assert creation_flags & 0x00000004
        events.append("created-suspended")
        process = _OrderingProcess(events)
        authority._process = process
        return process

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(subprocess, "STARTUPINFO", StartupInfo, raising=False)

    def fake_create(
        attempt_root: Path,
        context_digest: str | None = None,
        *,
        root_pid: int | None = None,
        root_creation_identity: str | None = None,
    ) -> _OrderingAuthority:
        del attempt_root
        del root_pid, root_creation_identity
        if context_digest is not None:
            authority._context_digest = context_digest
        return authority

    monkeypatch.setattr(ProcessAuthority, "create", fake_create)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        supervisor_module,
        "_process_metadata",
        lambda pid: supervisor_module._ProcessMetadata(
            str(Path(sys.executable)),
            "windows:test",
            True,
            None,
            datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )
    original_write = SolverSupervisor._write_process_record
    replacement_installed = False

    def record_write(supervisor: SolverSupervisor, record: dict[str, object]) -> None:
        nonlocal replacement_installed
        original_write(supervisor, record)
        if replace_with_foreign and record.get("state") == "BOUND_SUSPENDED":
            replacement = dict(record)
            replacement["replacement_marker"] = "foreign-bound-record"
            replacement_path = supervisor.process_record_path.with_name("foreign.json")
            replacement_path.write_text(json.dumps(replacement, sort_keys=True), encoding="utf-8")
            try:
                os.replace(os.fspath(replacement_path), os.fspath(supervisor.process_record_path))
            except PermissionError:
                return
            replacement_installed = True

    def fail_resume(pid: int) -> None:
        assert pid == _OrderingProcess.pid
        events.append("resume-failure")
        raise OSError("synthetic late resume failure")

    monkeypatch.setattr(authority, "resume", fail_resume)

    def record_events(supervisor: SolverSupervisor, record: dict[str, object]) -> None:
        events.append(f"persist:{record.get('state')}")
        record_write(supervisor, record)

    monkeypatch.setattr(SolverSupervisor, "_write_process_record", record_events)
    supervisor = SolverSupervisor(capability)

    with pytest.raises(SolverLaunchError, match="late resume failure"):
        supervisor.start()

    assert supervisor.state is SolverState.FAILED
    assert supervisor._process is None
    assert supervisor._process_authority is None
    record_path = supervisor.process_record_path
    if replacement_installed:
        replacement = json.loads(record_path.read_text(encoding="utf-8"))
        assert replacement["replacement_marker"] == "foreign-bound-record"
    else:
        assert not record_path.exists()


def test_failed_start_cleanup_errors_propagate_and_roll_back_owned_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capability = _capability(tmp_path, monkeypatch, code="pass")
    events: list[str] = []
    authority = _CleanupFailureAuthority(events)

    class StartupInfo:
        lpAttributeList: object | None = None

    def fake_popen(*args: object, **kwargs: object) -> _OrderingProcess:
        del args
        creation_flags = kwargs["creationflags"]
        assert isinstance(creation_flags, int)
        assert creation_flags & supervisor_module._CREATE_SUSPENDED
        events.append("created-suspended")
        process = _OrderingProcess(events)
        authority._process = process
        return process

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(subprocess, "STARTUPINFO", StartupInfo, raising=False)

    def fake_create(
        attempt_root: Path,
        context_digest: str | None = None,
        *,
        root_pid: int | None = None,
        root_creation_identity: str | None = None,
    ) -> _CleanupFailureAuthority:
        del attempt_root, root_pid, root_creation_identity
        if context_digest is not None:
            authority._context_digest = context_digest
        return authority

    def fail_resume(pid: int) -> None:
        assert pid == _OrderingProcess.pid
        events.append("resume-failure")
        raise OSError("synthetic late resume failure")

    monkeypatch.setattr(ProcessAuthority, "create", fake_create)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        supervisor_module,
        "_process_metadata",
        lambda pid: supervisor_module._ProcessMetadata(
            str(Path(sys.executable)),
            "windows:test",
            True,
            None,
            datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )
    monkeypatch.setattr(authority, "resume", fail_resume)

    supervisor = SolverSupervisor(capability)
    with pytest.raises(SolverOwnershipError, match="cleanup|termination|drain|close"):
        supervisor.start()

    assert supervisor.state is SolverState.FAILED
    assert supervisor._process is None
    assert supervisor._process_authority is None
    assert not supervisor.process_record_path.exists()
    assert events.index("terminate") < events.index("terminate-force")
    assert events.index("terminate-force") < events.index("drain")
    assert events.index("drain") < events.index("close")


def test_windows_current_record_partial_close_retires_reused_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A partial close of the current claim cannot close a reused CRT descriptor."""

    if os.name != "nt":
        pytest.fail("required Windows record claim test executed on a non-Windows host")

    capability = _capability(tmp_path, monkeypatch, code="import time; time.sleep(30)")
    supervisor = SolverSupervisor(capability)
    supervisor.start()
    process = supervisor._process
    claim = supervisor._process_record_claim
    assert process is not None
    assert claim is not None and claim.handle is not None
    record_path = Path(os.fspath(supervisor.process_record_path))
    record_content = supervisor_module._read_record_fd(claim.handle)
    record_key = supervisor_module._windows_record_claim_key(record_path)
    target_fd = claim.handle
    original_close = os.close
    original_open = os.open
    unrelated_path = tmp_path / "unrelated-current.bin"
    unrelated_fd: int | None = None

    def partial_close(fd: int) -> None:
        nonlocal unrelated_fd
        if fd == target_fd and unrelated_fd is None:
            original_close(fd)
            unrelated_fd = original_open(os.fspath(unrelated_path), os.O_RDWR | os.O_CREAT, 0o600)
            if unrelated_fd != target_fd:
                duplicated = os.dup2(unrelated_fd, target_fd)
                original_close(unrelated_fd)
                unrelated_fd = duplicated
            assert unrelated_fd == target_fd
            raise OSError("synthetic close reported failure after retiring the CRT descriptor")
        original_close(fd)

    try:
        with monkeypatch.context() as close_patch:
            close_patch.setattr(os, "close", partial_close)
            with contextlib.suppress(SolverOwnershipError):
                supervisor._close_filesystem_authority()

        assert claim.handle is None
        assert supervisor._process_record_claim is None
        assert record_key not in supervisor_module._WINDOWS_RECORD_CLAIMS
        assert record_path.read_bytes() == record_content
        assert unrelated_fd is not None

        supervisor._close_filesystem_authority()
        supervisor_module._drain_durable_cleanup()
        os.write(unrelated_fd, b"still-owned-by-test")
        assert process.poll() is None
    finally:
        if unrelated_fd is not None:
            with contextlib.suppress(OSError):
                original_close(unrelated_fd)
        if process.poll() is None:
            with contextlib.suppress(BaseException):
                cast(subprocess.Popen[bytes], process).kill()
            with contextlib.suppress(BaseException):
                process.wait(timeout=5.0)
        with contextlib.suppress(BaseException):
            supervisor._close_filesystem_authority()


def test_windows_malformed_record_partial_close_retires_reused_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Malformed candidate cleanup retires a partially closed descriptor and its registry entry."""

    if os.name != "nt":
        pytest.fail("required Windows record candidate test executed on a non-Windows host")

    capability = _capability(tmp_path, monkeypatch, code="pass")
    supervisor = SolverSupervisor(capability)
    record_path = Path(os.fspath(supervisor.process_record_path))
    record_content = b"{"
    record_path.write_bytes(record_content)
    opened: list[int] = []
    captured_candidates: list[supervisor_module._ProcessRecordClaim] = []
    original_open_record = supervisor_module._windows_open_process_record
    original_own_candidate = supervisor._own_process_record_candidate
    original_close = os.close
    original_open = os.open
    unrelated_path = tmp_path / "unrelated-malformed.bin"
    unrelated_fd: int | None = None

    def capture_open(path: Path, *, create: bool, delete_access: bool = True) -> int:
        fd = original_open_record(path, create=create, delete_access=delete_access)
        opened.append(fd)
        return fd

    def capture_candidate(
        path: Path, *, handle: int, parent_fd: int | None
    ) -> supervisor_module._ProcessRecordClaim:
        candidate = original_own_candidate(path, handle=handle, parent_fd=parent_fd)
        captured_candidates.append(candidate)
        return candidate

    def partial_close(fd: int) -> None:
        nonlocal unrelated_fd
        if opened and fd == opened[0] and unrelated_fd is None:
            original_close(fd)
            unrelated_fd = original_open(os.fspath(unrelated_path), os.O_RDWR | os.O_CREAT, 0o600)
            if unrelated_fd != opened[0]:
                duplicated = os.dup2(unrelated_fd, opened[0])
                original_close(unrelated_fd)
                unrelated_fd = duplicated
            assert unrelated_fd == opened[0]
            raise OSError("synthetic close reported failure after retiring the CRT descriptor")
        original_close(fd)

    monkeypatch.setattr(supervisor_module, "_windows_open_process_record", capture_open)
    monkeypatch.setattr(supervisor, "_own_process_record_candidate", capture_candidate)
    candidate: supervisor_module._ProcessRecordClaim | None = None
    try:
        with monkeypatch.context() as close_patch:
            close_patch.setattr(os, "close", partial_close)
            with contextlib.suppress(SolverOwnershipError):
                supervisor._read_process_record()

        assert opened
        assert captured_candidates
        candidate = captured_candidates[0]
        assert candidate.handle is None
        assert supervisor_module._windows_record_claim_key(record_path) not in (
            supervisor_module._WINDOWS_RECORD_CLAIMS
        )
        assert record_path.read_bytes() == record_content
        assert unrelated_fd is not None

        supervisor._close_filesystem_authority()
        supervisor_module._drain_durable_cleanup()
        os.write(unrelated_fd, b"still-owned-by-test")
    finally:
        if unrelated_fd is not None:
            with contextlib.suppress(OSError):
                original_close(unrelated_fd)
        if candidate is not None and candidate.handle is not None:
            with contextlib.suppress(OSError):
                original_close(candidate.handle)
            candidate.handle = None
        with contextlib.suppress(BaseException):
            supervisor._close_filesystem_authority()
        if record_path.exists():
            with contextlib.suppress(OSError):
                record_path.unlink()


def test_windows_pending_record_partial_close_retires_reused_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pending claim cleanup has the same partial-close guarantee as current claims."""

    if os.name != "nt":
        pytest.fail("required Windows pending claim test executed on a non-Windows host")

    capability = _capability(tmp_path, monkeypatch, code="pass")
    supervisor = SolverSupervisor(capability)
    record_path = Path(os.fspath(supervisor.process_record_path))
    record_content = b"{}"
    record_path.write_bytes(record_content)
    supervisor._read_process_record()
    candidate = supervisor._pending_process_record_claim
    assert candidate is not None and candidate.handle is not None
    target_fd = candidate.handle
    record_key = supervisor_module._windows_record_claim_key(record_path)
    original_close = os.close
    original_open = os.open
    unrelated_path = tmp_path / "unrelated-pending.bin"
    unrelated_fd: int | None = None

    def partial_close(fd: int) -> None:
        nonlocal unrelated_fd
        if fd == target_fd and unrelated_fd is None:
            original_close(fd)
            unrelated_fd = original_open(os.fspath(unrelated_path), os.O_RDWR | os.O_CREAT, 0o600)
            if unrelated_fd != target_fd:
                duplicated = os.dup2(unrelated_fd, target_fd)
                original_close(unrelated_fd)
                unrelated_fd = duplicated
            assert unrelated_fd == target_fd
            raise OSError("synthetic close reported failure after retiring the CRT descriptor")
        original_close(fd)

    try:
        with monkeypatch.context() as close_patch:
            close_patch.setattr(os, "close", partial_close)
            failures = supervisor._release_process_record_claim(candidate)
        assert not failures
        assert candidate.handle is None
        assert supervisor._pending_process_record_claim is None
        assert record_key not in supervisor_module._WINDOWS_RECORD_CLAIMS
        assert unrelated_fd is not None
        supervisor._close_filesystem_authority()
        supervisor_module._drain_durable_cleanup()
        os.write(unrelated_fd, b"still-owned-by-test")
    finally:
        if unrelated_fd is not None:
            with contextlib.suppress(OSError):
                original_close(unrelated_fd)
        with contextlib.suppress(BaseException):
            supervisor._close_filesystem_authority()
        if record_path.exists():
            with contextlib.suppress(OSError):
                record_path.unlink()


def test_windows_replacement_record_partial_close_retires_only_candidate_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A replacement candidate cannot unregister or close the current claim's object."""

    if os.name != "nt":
        pytest.fail("required Windows replacement claim test executed on a non-Windows host")

    capability = _capability(tmp_path, monkeypatch, code="pass")
    supervisor = SolverSupervisor(capability)
    record_path = Path(os.fspath(supervisor.process_record_path))
    record_content = b"{}"
    record_path.write_bytes(record_content)
    supervisor._read_process_record()
    supervisor._install_pending_process_record_claim()
    prior = supervisor._process_record_claim
    assert prior is not None and prior.handle is not None
    candidate_fd = supervisor_module._windows_open_process_record(record_path, create=False)
    candidate = supervisor._own_process_record_candidate(
        record_path,
        handle=candidate_fd,
        parent_fd=None,
    )
    candidate.content = record_content
    candidate.state = None
    record_key = supervisor_module._windows_record_claim_key(record_path)
    target_fd = candidate.handle
    assert target_fd is not None and target_fd != prior.handle
    original_close = os.close
    original_open = os.open
    unrelated_path = tmp_path / "unrelated-replacement.bin"
    unrelated_fd: int | None = None

    def partial_close(fd: int) -> None:
        nonlocal unrelated_fd
        if fd == target_fd and unrelated_fd is None:
            original_close(fd)
            unrelated_fd = original_open(os.fspath(unrelated_path), os.O_RDWR | os.O_CREAT, 0o600)
            if unrelated_fd != target_fd:
                duplicated = os.dup2(unrelated_fd, target_fd)
                original_close(unrelated_fd)
                unrelated_fd = duplicated
            assert unrelated_fd == target_fd
            raise OSError("synthetic close reported failure after retiring the CRT descriptor")
        original_close(fd)

    try:
        with monkeypatch.context() as close_patch:
            close_patch.setattr(os, "close", partial_close)
            failures = supervisor._release_process_record_claim(candidate)
        assert not failures
        assert candidate.handle is None
        assert supervisor._process_record_claim is prior
        assert prior.handle is not None
        assert record_key in supervisor_module._WINDOWS_RECORD_CLAIMS
        assert unrelated_fd is not None
        assert supervisor_module._read_record_fd(prior.handle) == record_content
        supervisor_module._drain_durable_cleanup()
        os.write(unrelated_fd, b"still-owned-by-test")
    finally:
        if unrelated_fd is not None:
            with contextlib.suppress(OSError):
                original_close(unrelated_fd)
        with contextlib.suppress(BaseException):
            supervisor._close_filesystem_authority()
        if record_path.exists():
            with contextlib.suppress(OSError):
                record_path.unlink()


@pytest.mark.parametrize("failure_mode", ["before", "partial"])
def test_windows_read_only_probe_cleanup_retains_or_retires_exact_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_mode: str
) -> None:
    """Read-only probe failures must not leak or retry a reused descriptor."""

    if os.name != "nt":
        pytest.fail("required Windows read-only probe test executed on a non-Windows host")

    capability = _capability(tmp_path, monkeypatch, code="pass")
    supervisor = SolverSupervisor(capability)
    supervisor.start()
    claim = supervisor._process_record_claim
    assert claim is not None and claim.handle is not None
    process = supervisor._process
    assert process is not None
    captured: list[supervisor_module._WindowsProbeFd] = []
    original_probe = supervisor_module._windows_probe_process_record
    original_close = os.close
    original_open = os.open
    unrelated_path = tmp_path / f"unrelated-probe-{failure_mode}.bin"
    unrelated_fd: int | None = None

    def capture_probe(path: Path) -> supervisor_module._WindowsProbeFd:
        probe = original_probe(path)
        captured.append(probe)
        return probe

    monkeypatch.setattr(supervisor_module, "_windows_probe_process_record", capture_probe)

    try:
        with monkeypatch.context() as probe_patch:
            probe_patch.setattr(supervisor_module, "_windows_probe_process_record", capture_probe)
            with probe_patch.context() as close_patch:

                def failing_close(fd: int) -> None:
                    nonlocal unrelated_fd
                    target_fd = int(captured[0])
                    if fd == target_fd and failure_mode == "before":
                        raise OSError("synthetic probe close failed before closing")
                    if fd == target_fd and unrelated_fd is None:
                        original_close(fd)
                        unrelated_fd = original_open(
                            os.fspath(unrelated_path), os.O_RDWR | os.O_CREAT, 0o600
                        )
                        assert unrelated_fd == target_fd
                        raise OSError(
                            "synthetic probe close reported failure after retiring "
                            "the CRT descriptor"
                        )
                    original_close(fd)

                close_patch.setattr(os, "close", failing_close)
                with contextlib.suppress(SolverOwnershipError):
                    supervisor._record_claim_identity_matches(claim)

        assert captured
        if failure_mode == "before":
            os.fstat(int(captured[0]))
            supervisor_module._drain_durable_cleanup()
            with pytest.raises(OSError):
                os.fstat(int(captured[0]))
        else:
            assert getattr(captured[0], "handle", None) is None
            supervisor_module._drain_durable_cleanup()
            assert unrelated_fd is not None
            os.write(unrelated_fd, b"still-owned-by-test")
        assert not getattr(supervisor_module, "_WINDOWS_PROBE_CLAIMS", [captured[0]])
    finally:
        if unrelated_fd is not None:
            with contextlib.suppress(OSError):
                original_close(unrelated_fd)
        with contextlib.suppress(BaseException):
            supervisor._close_filesystem_authority()
        if process.poll() is None:
            with contextlib.suppress(BaseException):
                cast(subprocess.Popen[bytes], process).kill()
            with contextlib.suppress(BaseException):
                process.wait(timeout=5.0)


def test_windows_duplicate_record_claim_returns_exact_guarded_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reconnect duplication returns a CRT descriptor paired with its own stable guard."""

    if os.name != "nt":
        pytest.fail("required Windows record duplication test executed on a non-Windows host")

    capability = _capability(tmp_path, monkeypatch, code="pass")
    supervisor = SolverSupervisor(capability)
    supervisor.start()
    duplicate: int | None = None
    try:
        record_path = Path(os.fspath(supervisor.process_record_path))
        duplicate = supervisor_module._windows_duplicate_record_claim(record_path)
        assert duplicate is not None
        native_handle = getattr(duplicate, "native_handle", None)
        assert native_handle is not None
        assert runtime_module._windows_fd_identity_matches(int(duplicate), native_handle) is True
    finally:
        if duplicate is not None:
            handle = duplicate
            native_handle = getattr(duplicate, "native_handle", None)
            if handle is not None:
                with contextlib.suppress(OSError):
                    os.close(handle)
            if native_handle is not None:
                with contextlib.suppress(OSError):
                    runtime_module._windows_close_native_handle(native_handle)
        with contextlib.suppress(BaseException):
            supervisor.cancel()


def test_windows_duplicate_record_claim_late_probe_close_failure_retains_exact_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A late probe-close failure durably owns the unreturned duplicate exactly."""

    if os.name != "nt":
        pytest.fail("required Windows duplicate cleanup test executed on a non-Windows host")

    capability = _capability(tmp_path, monkeypatch, code="import time; time.sleep(30)")
    supervisor = SolverSupervisor(capability)
    supervisor.start()
    original_claim = supervisor._process_record_claim
    assert original_claim is not None and original_claim.handle is not None
    record_path = Path(os.fspath(supervisor.process_record_path))
    record_key = supervisor_module._windows_record_claim_key(record_path)
    original_probe = supervisor_module._windows_probe_process_record
    original_convert = _windows_convert_raw_handle
    original_close = os.close
    original_open = os.open
    original_close_native = runtime_module._windows_close_native_handle
    captured_probes: list[supervisor_module._WindowsProbeFd] = []
    captured_duplicates: list[int] = []
    probe_foreign_fd: int | None = None
    duplicate_foreign_fd: int | None = None
    duplicate_owner_observed = False
    native_close_attempts: list[int] = []
    fail_probe_native_close = True
    duplicate: int | None = None

    def capture_probe(path: Path) -> supervisor_module._WindowsProbeFd:
        probe = original_probe(path)
        captured_probes.append(probe)
        return probe

    def capture_convert(
        raw_handle: int,
        descriptor_flags: int,
        *,
        duplicate_native_handle: Callable[[int], int] | None = None,
        close_native_handle: Callable[[int], None] | None = None,
    ) -> int:
        result = original_convert(
            raw_handle,
            descriptor_flags,
            duplicate_native_handle=duplicate_native_handle,
            close_native_handle=close_native_handle,
        )
        if descriptor_flags == os.O_RDWR:
            captured_duplicates.append(result)
        return result

    def failing_close_native(native_handle: int) -> None:
        native_close_attempts.append(native_handle)
        if (
            fail_probe_native_close
            and captured_probes
            and native_handle == captured_probes[0].native_handle
        ):
            raise OSError("synthetic probe native close failure")
        original_close_native(native_handle)

    def partially_close_probe(fd: int) -> None:
        nonlocal probe_foreign_fd
        assert captured_probes
        probe = captured_probes[0]
        if fd == int(probe) and probe_foreign_fd is None:
            original_close(fd)
            probe_foreign_fd = original_open(
                os.fspath(tmp_path / "late-probe-foreign.bin"),
                os.O_RDWR | os.O_CREAT,
                0o600,
            )
            if probe_foreign_fd != fd:
                duplicated = os.dup2(probe_foreign_fd, fd)
                original_close(probe_foreign_fd)
                probe_foreign_fd = duplicated
            assert probe_foreign_fd == fd
            raise OSError("synthetic probe CRT close failure after retirement")
        original_close(fd)

    try:
        monkeypatch.setattr(runtime_module, "_windows_close_native_handle", failing_close_native)
        with monkeypatch.context() as fault_patch:
            fault_patch.setattr(supervisor_module, "_windows_probe_process_record", capture_probe)
            fault_patch.setattr(supervisor_module, "_windows_convert_raw_handle", capture_convert)
            fault_patch.setattr(os, "close", partially_close_probe)
            with pytest.raises(OSError, match="synthetic probe native close failure"):
                duplicate = supervisor_module._windows_duplicate_record_claim(record_path)

        assert duplicate is None
        assert captured_probes
        assert captured_duplicates
        probe = captured_probes[0]
        duplicate = captured_duplicates[0]
        duplicate_native = getattr(duplicate, "native_handle", None)
        probe_native = probe.native_handle
        assert duplicate_native is not None
        assert probe.handle is None
        assert probe_native is not None
        assert any(held is probe for held in supervisor_module._WINDOWS_PROBE_CLAIMS)

        duplicate_owners = [
            owner
            for owner in supervisor_module._DURABLE_CLAIM_CLEANUP._process_record_claims
            if owner.handle == int(duplicate) and owner.native_handle == duplicate_native
        ]
        duplicate_owner_observed = bool(duplicate_owners)
        assert len(duplicate_owners) == 1
        duplicate_owner = duplicate_owners[0]
        assert duplicate_owner.handle == int(duplicate)
        assert duplicate_owner.native_handle == duplicate_native
        assert supervisor._process_record_claim is original_claim
        entries = supervisor_module._WINDOWS_RECORD_CLAIMS.get(record_key)
        assert entries is not None
        assert any(entry.owner is original_claim for entry in entries)
        assert probe_foreign_fd is not None
        os.write(probe_foreign_fd, b"probe-foreign")

        fail_probe_native_close = False
        os.fstat(int(duplicate))
        original_close(int(duplicate))
        duplicate.handle = None  # type: ignore[attr-defined]
        duplicate_foreign_fd = original_open(
            os.fspath(tmp_path / "late-duplicate-foreign.bin"),
            os.O_RDWR | os.O_CREAT,
            0o600,
        )
        if duplicate_foreign_fd != int(duplicate):
            duplicated = os.dup2(duplicate_foreign_fd, int(duplicate))
            original_close(duplicate_foreign_fd)
            duplicate_foreign_fd = duplicated
        assert duplicate_foreign_fd == int(duplicate)
        supervisor_module._drain_durable_cleanup()
        assert duplicate_owner.handle is None
        assert duplicate_owner.native_handle is None
        assert not any(
            owner is duplicate_owner
            for owner in supervisor_module._DURABLE_CLAIM_CLEANUP._process_record_claims
        )
        assert native_close_attempts.count(duplicate_native) == 1
        os.write(duplicate_foreign_fd, b"duplicate-foreign")
        assert probe.native_handle is None
        assert not any(held is probe for held in supervisor_module._WINDOWS_PROBE_CLAIMS)
        assert native_close_attempts.count(probe_native) == 2
        assert original_claim.handle is not None
        assert supervisor_module._read_record_fd(original_claim.handle)
    finally:
        fail_probe_native_close = False
        with contextlib.suppress(BaseException):
            supervisor_module._drain_durable_cleanup()
        if not duplicate_owner_observed and duplicate is not None:
            duplicate_handle = getattr(duplicate, "handle", None)
            if duplicate_handle is not None:
                with contextlib.suppress(OSError):
                    original_close(int(duplicate_handle))
                duplicate.handle = None  # type: ignore[attr-defined]
            duplicate_native = getattr(duplicate, "native_handle", None)
            if duplicate_native is not None:
                with contextlib.suppress(OSError):
                    original_close_native(duplicate_native)
                duplicate.native_handle = None  # type: ignore[attr-defined]
        if probe_foreign_fd is not None:
            with contextlib.suppress(OSError):
                original_close(probe_foreign_fd)
        if duplicate_foreign_fd is not None:
            with contextlib.suppress(OSError):
                original_close(duplicate_foreign_fd)
        with contextlib.suppress(BaseException):
            supervisor.cancel()


@pytest.mark.parametrize("failure", ["duplicate", "open_osfhandle"])
def test_windows_process_record_conversion_failure_retains_exact_raw_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Process-record H1/H2 conversion failures retain the exact owner until retry."""

    if os.name != "nt":
        pytest.fail(
            "required Windows process-record conversion test executed on a non-Windows host"
        )

    import msvcrt

    record_path = tmp_path / "process.json"
    record_path.write_bytes(b"{}")
    captured: list[int] = []
    original_duplicate = getattr(supervisor_module, "_windows_duplicate_native_handle", None)

    def fail_duplicate(raw_handle: int) -> int:
        captured.append(int(raw_handle))
        raise OSError("synthetic process-record DuplicateHandle failure")

    def fail_open(raw_handle: int, descriptor_flags: int) -> int:
        captured.append(int(raw_handle))
        raise OSError("synthetic process-record open_osfhandle failure")

    if failure == "duplicate":
        monkeypatch.setattr(
            supervisor_module,
            "_windows_duplicate_native_handle",
            fail_duplicate,
            raising=False,
        )
    else:
        assert original_duplicate is not None
        monkeypatch.setattr(
            supervisor_module,
            "_windows_duplicate_native_handle",
            original_duplicate,
            raising=False,
        )
        monkeypatch.setattr(msvcrt, "open_osfhandle", fail_open)

    def fail_close(native_handle: int) -> None:
        del native_handle
        raise OSError("synthetic process-record CloseHandle failure")

    monkeypatch.setattr(
        supervisor_module,
        "_windows_close_native_handle",
        fail_close,
        raising=False,
    )
    result: int | None = None
    try:
        with pytest.raises(OSError, match="failure"):
            result = supervisor_module._windows_open_process_record_handle(
                record_path,
                create=False,
                desired_access=supervisor_module._WINDOWS_GENERIC_READ,
                share_mode=(
                    supervisor_module._WINDOWS_FILE_SHARE_READ
                    | supervisor_module._WINDOWS_FILE_SHARE_WRITE
                    | supervisor_module._WINDOWS_FILE_SHARE_DELETE
                ),
                descriptor_flags=os.O_RDONLY,
            )
        assert captured
        durable = getattr(runtime_module, "_RUNTIME_DURABLE_CLAIMS", ())
        assert any(getattr(owner, "raw_handle", None) in captured for owner in durable)
    finally:
        if result is not None:
            with contextlib.suppress(OSError):
                os.close(result)
        with contextlib.suppress(BaseException):
            runtime_module._drain_runtime_claims()


def test_windows_read_only_probe_conversion_failure_retains_exact_raw_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read-only probe retains its raw owner when guard conversion cleanup fails."""

    if os.name != "nt":
        pytest.fail(
            "required Windows read-only probe acquisition test executed on a non-Windows host"
        )

    record_path = tmp_path / "process.json"
    record_path.write_bytes(b"{}")
    captured: list[int] = []

    def fail_duplicate(raw_handle: int) -> int:
        captured.append(int(raw_handle))
        raise OSError("synthetic probe DuplicateHandle failure")

    def fail_close(native_handle: int) -> None:
        del native_handle
        raise OSError("synthetic probe CloseHandle failure")

    monkeypatch.setattr(
        supervisor_module,
        "_windows_duplicate_native_handle",
        fail_duplicate,
        raising=False,
    )
    monkeypatch.setattr(
        supervisor_module,
        "_windows_close_native_handle",
        fail_close,
        raising=False,
    )
    try:
        with pytest.raises(OSError, match="failure"):
            supervisor_module._windows_probe_process_record(record_path)
        assert captured
        durable = getattr(runtime_module, "_RUNTIME_DURABLE_CLAIMS", ())
        assert any(getattr(owner, "raw_handle", None) in captured for owner in durable)
    finally:
        with contextlib.suppress(BaseException):
            runtime_module._drain_runtime_claims()


def test_windows_owned_process_record_candidate_does_not_duplicate_after_crt_transfer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Candidate adoption consumes the already-guarded descriptor without an acquisition gap."""

    if os.name != "nt":
        pytest.fail("required Windows process-record ownership test executed on a non-Windows host")

    capability = _capability(tmp_path, monkeypatch, code="pass")
    supervisor = SolverSupervisor(capability)
    record_path = Path(os.fspath(supervisor.process_record_path))
    record_path.write_bytes(b"{}")
    handle = supervisor_module._windows_open_process_record(record_path, create=False)

    def unexpected_duplicate(fd: int) -> int:
        del fd
        raise AssertionError("candidate adoption duplicated after CRT ownership")

    monkeypatch.setattr(supervisor_module, "_windows_duplicate_fd_handle", unexpected_duplicate)
    candidate: supervisor_module._ProcessRecordClaim | None = None
    try:
        candidate = supervisor._own_process_record_candidate(
            record_path,
            handle=handle,
            parent_fd=None,
        )
        assert candidate.native_handle is not None
    finally:
        if candidate is not None:
            with contextlib.suppress(BaseException):
                supervisor._release_process_record_claim(candidate)
        else:
            with contextlib.suppress(OSError):
                os.close(int(handle))
        with contextlib.suppress(BaseException):
            supervisor._close_filesystem_authority()
        if record_path.exists():
            with contextlib.suppress(OSError):
                record_path.unlink()


def test_windows_record_registry_reuse_retains_object_owner_after_partial_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reused CRT slot cannot make registry cleanup lose its claim owner."""

    if os.name != "nt":
        pytest.fail("required Windows record-registry test executed on a non-Windows host")

    capability = _capability(tmp_path, monkeypatch, code="pass")
    supervisor = SolverSupervisor(capability)
    record_path = Path(os.fspath(supervisor.process_record_path))
    record_content = b"{}"
    record_path.write_bytes(record_content)
    record_fd = supervisor_module._windows_open_process_record(record_path, create=False)
    claim = supervisor._own_process_record_candidate(
        record_path,
        handle=record_fd,
        parent_fd=None,
    )
    claim.content = record_content
    supervisor._process_record_claim = claim
    updated_record: dict[str, object] = {"marker": "in-place"}
    supervisor._write_process_record(updated_record)
    record_content = json.dumps(updated_record, indent=2, sort_keys=True).encode("utf-8")
    assert supervisor._process_record_claim is claim
    target_fd = claim.handle
    old_native = claim.native_handle
    assert target_fd is not None and old_native is not None
    record_key = supervisor_module._windows_record_claim_key(record_path)
    entries = supervisor_module._WINDOWS_RECORD_CLAIMS.get(record_key)
    assert entries is not None and len(entries) == 1
    assert getattr(entries[0], "owner", None) is claim

    original_close = os.close
    original_open = os.open
    unrelated_path = tmp_path / "registry-reuse.bin"
    unrelated_fd: int | None = None
    native_close_attempts: list[int] = []
    close_guard_failure = True
    original_close_native = runtime_module._windows_close_native_handle

    def close_native(native_handle: int) -> None:
        native_close_attempts.append(native_handle)
        if close_guard_failure and native_handle == old_native:
            raise OSError("synthetic transient native guard close failure")
        original_close_native(native_handle)

    def partial_close(fd: int) -> None:
        nonlocal unrelated_fd
        if fd == target_fd and unrelated_fd is None:
            original_close(fd)
            unrelated_fd = original_open(os.fspath(unrelated_path), os.O_RDWR | os.O_CREAT, 0o600)
            if unrelated_fd != fd:
                duplicated = os.dup2(unrelated_fd, fd)
                original_close(unrelated_fd)
                unrelated_fd = duplicated
            assert unrelated_fd == fd
            raise OSError("synthetic CRT close reported failure after retiring the slot")
        original_close(fd)

    monkeypatch.setattr(runtime_module, "_windows_close_native_handle", close_native)
    monkeypatch.setattr(supervisor_module, "_windows_close_native_handle", close_native)
    try:
        with monkeypatch.context() as close_patch:
            close_patch.setattr(os, "close", partial_close)
            failures = supervisor._release_process_record_claim(claim)
        assert failures
        assert claim.handle is None
        assert claim.native_handle == old_native
        assert native_close_attempts == [old_native]
        assert supervisor_module._WINDOWS_RECORD_CLAIMS.get(record_key) == entries

        path_opens: list[Path] = []

        def forbidden_path_open(path: Path, **kwargs: object) -> int:
            del kwargs
            path_opens.append(path)
            raise AssertionError("registry uncertainty reopened process record by path")

        with monkeypatch.context() as path_patch:
            path_patch.setattr(
                supervisor_module,
                "_windows_open_process_record_handle",
                forbidden_path_open,
            )
            with pytest.raises(SolverOwnershipError, match="claim|record|authority"):
                supervisor_module._windows_open_process_record(record_path, create=False)

        assert path_opens == []
        assert claim.handle is None
        assert claim.native_handle == old_native
        assert native_close_attempts == [old_native]
        assert supervisor_module._WINDOWS_RECORD_CLAIMS.get(record_key) == entries

        close_guard_failure = False
        assert supervisor._release_process_record_claim(claim) == ()
        assert claim.handle is None
        assert claim.native_handle is None
        assert record_key not in supervisor_module._WINDOWS_RECORD_CLAIMS
    finally:
        close_guard_failure = False
        if unrelated_fd is not None:
            with contextlib.suppress(OSError):
                original_close(unrelated_fd)
        with contextlib.suppress(BaseException):
            supervisor._close_filesystem_authority()
        if record_path.exists():
            with contextlib.suppress(OSError):
                record_path.unlink()


def test_windows_directory_close_failure_retries_only_original_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transient directory close failure preserves the exact handle for retry."""

    if os.name != "nt":
        pytest.fail("required Windows directory-close test executed on a non-Windows host")

    root = tmp_path / "attempt"
    root.mkdir()
    authority = supervisor_module._FilesystemAuthority(root)
    original_entries = authority._entries
    original_handles = {
        handle.value
        for _identity, handle in original_entries
        if isinstance(handle, supervisor_module._WindowsDirectoryHandle)
    }
    assert original_handles
    failed_handle = next(iter(original_handles))
    close_attempts: list[int] = []
    fail_once = True
    original_close = supervisor_module._windows_close_directory

    def flaky_close(handle: supervisor_module._WindowsDirectoryHandle) -> None:
        nonlocal fail_once
        close_attempts.append(handle.value)
        if handle.value == failed_handle and fail_once:
            fail_once = False
            raise OSError("synthetic transient directory close failure")
        original_close(handle)

    monkeypatch.setattr(supervisor_module, "_windows_close_directory", flaky_close)
    try:
        with pytest.raises(SolverOwnershipError, match="closed"):
            authority.close()

        assert not authority._closed
        retained_handles = {
            handle.value
            for _identity, handle in authority._entries
            if isinstance(handle, supervisor_module._WindowsDirectoryHandle)
        }
        assert retained_handles == {failed_handle}
        assert failed_handle in close_attempts
        assert set(close_attempts) == original_handles

        authority.close()
        assert authority._closed
        assert authority._entries == ()
        assert close_attempts.count(failed_handle) == 2
        assert set(close_attempts) == original_handles
    finally:
        with contextlib.suppress(BaseException):
            authority.close()
