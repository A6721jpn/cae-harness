"""Owned, headless FEBio process supervision."""

from __future__ import annotations

import atexit
import contextlib
import ctypes
import errno
import hashlib
import io
import json
import math
import os
import re
import stat
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from ..evidence import IntentSnapshotAuthority
from .fbs import (
    FbsAdapterAuthority,
    FbsValidation,
    _adopt_validation,
    _authority_record,
    _invalid_validation,
    _unregister_validation,
    validate_requested_fields,
)
from .log import LogValidation, LogValidator, validate_log
from .process_authority import (
    ProcessAuthority,
    ProcessAuthorityError,
    _attempt_binding,
)
from .runtime import (
    RuntimeProbeError,
    _acquire_runtime_launch_claim,
    _drain_runtime_claims,
    _FileSnapshot,
    _RuntimeLaunchClaim,
    _snapshot_from_fd,
    _windows_close_native_handle,
    _windows_close_owned_fd,
    _windows_compare_object_handles,
    _windows_convert_raw_handle,
    _windows_duplicate_fd_handle,  # noqa: F401
    _windows_duplicate_native_handle,
    _windows_fd_identity_matches,
)
from .types import (
    OutputFreshnessError,
    SolverClassification,
    SolverConfigurationError,
    SolverLaunchCapability,
    SolverLaunchError,
    SolverLaunchSpec,
    SolverOwnershipError,
    SolverRunResult,
    SolverState,
    _json_digest,
    _validate_launch_capability,
)

__all__ = ["SolverSupervisor"]

_PROCESS_RECORD_NAME = "process.json"
_CREATE_SUSPENDED = 0x00000004
_RESULT_FIELDS = (
    "state",
    "classification",
    "return_code",
    "pid",
    "command",
    "log_path",
    "xplt_path",
    "started_at",
    "finished_at",
    "log_validation",
    "fbs_validation",
    "error",
)
_NATIVE_WINDOWS = sys.platform == "win32"
_WINDOWS_FILE_SHARE_READ = 0x00000001
_WINDOWS_FILE_SHARE_WRITE = 0x00000002
_WINDOWS_FILE_READ_ATTRIBUTES = 0x00000080
_WINDOWS_FILE_LIST_DIRECTORY = 0x00000001
_WINDOWS_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_WINDOWS_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_WINDOWS_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_WINDOWS_GENERIC_READ = 0x80000000
_WINDOWS_GENERIC_WRITE = 0x40000000
_WINDOWS_DELETE = 0x00010000
_WINDOWS_FILE_SHARE_DELETE = 0x00000004
_WINDOWS_CREATE_NEW = 1
_WINDOWS_OPEN_EXISTING = 3
_WINDOWS_FILE_ATTRIBUTE_NORMAL = 0x00000080
_WINDOWS_FILE_DISPOSITION_INFO = 4
_WINDOWS_RESUME_TRANSACTION_NAME = ".process.json.resume"
_WINDOWS_RESUME_TRANSACTION_VERSION = 1
_WINDOWS_WRITE_DAC = 0x00040000
_WINDOWS_WRITE_OWNER = 0x00080000
_WINDOWS_EVENT_MODIFY_STATE = 0x0002
_WINDOWS_EVENT_QUERY_STATE = 0x0001
_WINDOWS_EVENT_RECONNECT_ACCESS = _WINDOWS_EVENT_QUERY_STATE | 0x00100000
_WINDOWS_EVENT_DENIED_ACCESS = (
    _WINDOWS_DELETE | _WINDOWS_WRITE_DAC | _WINDOWS_WRITE_OWNER | _WINDOWS_EVENT_MODIFY_STATE
)
_WINDOWS_EVENT_DACL_SDDL = (
    f"D:P(D;;0x{_WINDOWS_EVENT_DENIED_ACCESS:08X};;;OW)"
    f"(A;;0x{_WINDOWS_EVENT_RECONNECT_ACCESS:08X};;;OW)"
)
_WINDOWS_WAIT_OBJECT_0 = 0x00000000
_WINDOWS_WAIT_TIMEOUT = 0x00000102
_WINDOWS_RESUME_EVENT_RE = re.compile(
    r"^Local\\febio-cae-resume-"
    r"(?P<attempt>[0-9a-f]{64})-"
    r"(?P<context>[0-9a-f]{64})-"
    r"(?P<device>[0-9a-f]+)-"
    r"(?P<inode>[0-9a-f]+)-"
    r"(?P<token>[0-9a-f]{32})$"
)
_POSIX_AT_EMPTY_PATH = 0x1000
_POSIX_RENAME_EXCHANGE = 0x2


class _WindowsRecordClaimEntry(tuple[int, int, int]):
    """Tuple-compatible registry entry carrying a stable native identity."""

    native_handle: int | None
    owner: object | None

    def __new__(
        cls,
        fd: int,
        device: int,
        inode: int,
        native_handle: int | None = None,
        owner: object | None = None,
    ) -> _WindowsRecordClaimEntry:
        entry = tuple.__new__(cls, (fd, device, inode))
        entry.native_handle = native_handle
        entry.owner = owner
        return entry


class _WindowsProbeFd(int):
    """Int-compatible read-only probe retaining an independent native handle."""

    handle: int | None
    native_handle: int | None
    path: Path

    def __new__(
        cls,
        fd: int,
        path: Path,
        native_handle: int | None,
    ) -> _WindowsProbeFd:
        probe = int.__new__(cls, fd)
        probe.handle = fd
        probe.native_handle = native_handle
        probe.path = path
        return probe


_WINDOWS_RECORD_CLAIMS: dict[str, list[_WindowsRecordClaimEntry]] = {}
_WINDOWS_RECORD_CLAIMS_LOCK = threading.RLock()
_WINDOWS_PROBE_CLAIMS: list[_WindowsProbeFd] = []
_WINDOWS_PROBE_CLAIMS_LOCK = threading.RLock()


def _same_file_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return int(left.st_dev) == int(right.st_dev) and int(left.st_ino) == int(right.st_ino)


class _WindowsFileDispositionInfo(ctypes.Structure):
    _fields_ = [("delete_file", ctypes.c_int)]


def _windows_open_process_record_handle(
    path: Path,
    *,
    create: bool,
    desired_access: int,
    share_mode: int,
    descriptor_flags: int,
) -> int:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    kernel32.CreateFileW.restype = ctypes.c_void_p
    disposition = _WINDOWS_CREATE_NEW if create else _WINDOWS_OPEN_EXISTING
    ctypes.set_last_error(0)
    raw_handle = kernel32.CreateFileW(
        os.fspath(path),
        desired_access,
        share_mode,
        None,
        disposition,
        _WINDOWS_FILE_ATTRIBUTE_NORMAL,
        None,
    )
    raw_value = raw_handle.value if isinstance(raw_handle, ctypes.c_void_p) else raw_handle
    try:
        value = 0 if raw_value is None else int(cast(int, raw_value))
    except (TypeError, ValueError, OverflowError):
        value = 0
    if not value or value in {-1, _WINDOWS_INVALID_HANDLE_VALUE}:
        error = ctypes.get_last_error()
        if error in {80, 183}:  # ERROR_FILE_EXISTS / ERROR_ALREADY_EXISTS
            raise FileExistsError(error, "process record already exists", os.fspath(path))
        if error in {2, 3}:  # ERROR_FILE_NOT_FOUND / ERROR_PATH_NOT_FOUND
            raise FileNotFoundError(error, "process record does not exist", os.fspath(path))
        raise OSError(error, f"unable to open process record: {path}")
    return _windows_convert_raw_handle(
        value,
        descriptor_flags,
        duplicate_native_handle=_windows_duplicate_native_handle,
        close_native_handle=_windows_close_native_handle,
    )


def _windows_open_process_record(
    path: Path,
    *,
    create: bool,
    delete_access: bool = True,
) -> int:
    """Open an authoritative record handle with exclusive mutation rights.

    The ``delete_access`` argument is retained for private-call compatibility, but
    authoritative handles always include DELETE so rollback never needs to reopen
    the record by path.
    """

    del delete_access
    if not create:
        duplicate = _windows_duplicate_record_claim(path)
        if duplicate is not None:
            return duplicate
    return _windows_open_process_record_handle(
        path,
        create=create,
        desired_access=_WINDOWS_GENERIC_READ | _WINDOWS_GENERIC_WRITE | _WINDOWS_DELETE,
        share_mode=_WINDOWS_FILE_SHARE_READ,
        descriptor_flags=os.O_RDWR,
    )


def _windows_record_claim_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _windows_register_record_claim(
    path: Path,
    fd: int,
    device: int,
    inode: int,
    *,
    native_handle: int | None = None,
    owner: object | None = None,
) -> None:
    key = _windows_record_claim_key(path)
    with _WINDOWS_RECORD_CLAIMS_LOCK:
        entries = _WINDOWS_RECORD_CLAIMS.setdefault(key, [])
        entry = _WindowsRecordClaimEntry(fd, device, inode, native_handle, owner)
        if entry not in entries:
            entries.append(entry)


def _windows_unregister_record_claim(
    path: Path,
    fd: int,
    *,
    device: int | None = None,
    inode: int | None = None,
    native_handle: int | None = None,
    owner: object | None = None,
) -> None:
    key = _windows_record_claim_key(path)
    with _WINDOWS_RECORD_CLAIMS_LOCK:
        entries = _WINDOWS_RECORD_CLAIMS.get(key)
        if not entries:
            return
        if owner is not None:
            retained = [entry for entry in entries if entry.owner is not owner]
        elif native_handle is not None:
            retained = [entry for entry in entries if entry.native_handle != native_handle]
        elif device is None or inode is None:
            retained = [entry for entry in entries if entry[0] != fd]
        else:
            target = (fd, device, inode)
            removed = False
            retained = []
            for entry in entries:
                if not removed and entry == target:
                    removed = True
                else:
                    retained.append(entry)
        if retained:
            _WINDOWS_RECORD_CLAIMS[key] = retained
        else:
            _WINDOWS_RECORD_CLAIMS.pop(key, None)


def _windows_duplicate_record_claim(path: Path) -> int | None:
    """Duplicate an in-process claim without losing its owning claim object."""

    key = _windows_record_claim_key(path)
    with _WINDOWS_RECORD_CLAIMS_LOCK:
        entries = _WINDOWS_RECORD_CLAIMS.get(key)
        if not entries:
            return None
        retained: list[_WindowsRecordClaimEntry] = []
        for index, entry in enumerate(entries):
            fd, device, inode = entry
            native_handle = entry.native_handle
            owner = entry.owner
            if owner is None:
                raise SolverOwnershipError("process record registry owner is unavailable")
            owner_fd = getattr(owner, "handle", None)
            owner_native = getattr(owner, "native_handle", None)
            if (
                owner_fd is None
                or owner_native is None
                or owner_fd != fd
                or native_handle is None
                or owner_native != native_handle
            ):
                raise SolverOwnershipError("process record registry claim ownership is uncertain")
            entry_matches = _windows_fd_identity_matches(owner_fd, owner_native)
            if entry_matches is not True:
                raise SolverOwnershipError("process record registry claim identity is uncertain")
            probe_fd: _WindowsProbeFd | None = None
            duplicate_owner: _ProcessRecordClaim | None = None
            duplicate_owner_adopted = False
            try:
                probe_fd = _windows_probe_process_record(path)
                probe_metadata = os.fstat(probe_fd)
                if int(probe_metadata.st_dev) != device or int(probe_metadata.st_ino) != inode:
                    raise SolverOwnershipError("process record registry path identity changed")
                raw_duplicate = _windows_duplicate_native_handle(owner_native)
                duplicate = _windows_convert_raw_handle(
                    raw_duplicate,
                    os.O_RDWR,
                    duplicate_native_handle=_windows_duplicate_native_handle,
                    close_native_handle=_windows_close_native_handle,
                )
                duplicate_native = getattr(duplicate, "native_handle", None)
                if duplicate_native is None:
                    raise SolverOwnershipError(
                        "duplicated process record has no exact native guard"
                    )
                duplicate_owner = _ProcessRecordClaim(
                    path=path,
                    content=b"",
                    device=device,
                    inode=inode,
                    state=None,
                    handle=int(duplicate),
                    name=path.name,
                    native_handle=duplicate_native,
                )
                retained.append(entry)
                retained.extend(entries[index + 1 :])
                _WINDOWS_RECORD_CLAIMS[key] = retained
                return duplicate
            except FileNotFoundError as error:
                if duplicate_owner is not None:
                    _DURABLE_CLAIM_CLEANUP.adopt(None, None, (duplicate_owner,))
                    duplicate_owner_adopted = True
                raise SolverOwnershipError("process record registry path is unavailable") from error
            except BaseException:
                if duplicate_owner is not None:
                    _DURABLE_CLAIM_CLEANUP.adopt(None, None, (duplicate_owner,))
                    duplicate_owner_adopted = True
                _WINDOWS_RECORD_CLAIMS[key] = retained + entries[index:]
                raise
            finally:
                if probe_fd is not None:
                    try:
                        _close_windows_probe(probe_fd)
                    except BaseException:
                        if duplicate_owner is not None and not duplicate_owner_adopted:
                            _DURABLE_CLAIM_CLEANUP.adopt(None, None, (duplicate_owner,))
                        raise
        _WINDOWS_RECORD_CLAIMS[key] = retained
        return None


def _windows_probe_process_record(path: Path) -> _WindowsProbeFd:
    """Open a read-only path probe without acquiring record mutation authority."""

    fd = _windows_open_process_record_handle(
        path,
        create=False,
        desired_access=_WINDOWS_GENERIC_READ,
        share_mode=(
            _WINDOWS_FILE_SHARE_READ | _WINDOWS_FILE_SHARE_WRITE | _WINDOWS_FILE_SHARE_DELETE
        ),
        descriptor_flags=os.O_RDONLY,
    )
    native_handle = getattr(fd, "native_handle", None)
    if native_handle is None:
        with contextlib.suppress(BaseException):
            os.close(int(fd))
        raise SolverOwnershipError("read-only process-record probe has no exact native guard")
    return _WindowsProbeFd(int(fd), path, native_handle)


def _retain_windows_probe(probe: _WindowsProbeFd) -> None:
    with _WINDOWS_PROBE_CLAIMS_LOCK:
        if (probe.handle is not None or probe.native_handle is not None) and not any(
            held is probe for held in _WINDOWS_PROBE_CLAIMS
        ):
            _WINDOWS_PROBE_CLAIMS.append(probe)


def _discard_windows_probe(probe: _WindowsProbeFd) -> None:
    with _WINDOWS_PROBE_CLAIMS_LOCK:
        _WINDOWS_PROBE_CLAIMS[:] = [held for held in _WINDOWS_PROBE_CLAIMS if held is not probe]


def _close_windows_probe(probe: int | _WindowsProbeFd) -> None:
    if not isinstance(probe, _WindowsProbeFd):
        os.close(probe)
        return
    try:
        _windows_close_owned_fd(probe, "handle", "native_handle")
    except BaseException:
        _retain_windows_probe(probe)
        raise
    _discard_windows_probe(probe)


def _drain_windows_probe_claims() -> None:
    with _WINDOWS_PROBE_CLAIMS_LOCK:
        for probe in tuple(_WINDOWS_PROBE_CLAIMS):
            try:
                _close_windows_probe(probe)
            except BaseException:
                continue


def _write_record_fd(fd: int, content: bytes) -> None:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        offset = 0
        while offset < len(content):
            written = os.write(fd, content[offset:])
            if written <= 0:
                raise OSError("unable to write process record")
            offset += written
        os.fsync(fd)
    except OSError as error:
        raise OSError("unable to publish process record") from error


def _read_record_fd(fd: int) -> bytes:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    except OSError as error:
        raise SolverOwnershipError("unable to read the owned process record") from error


def _windows_delete_process_record(fd: int) -> None:
    import msvcrt

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.SetFileInformationByHandle.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    kernel32.SetFileInformationByHandle.restype = ctypes.c_int
    handle = msvcrt.get_osfhandle(fd)
    if handle == -1:
        raise OSError(errno.EBADF, "process record handle is unavailable")
    disposition = _WindowsFileDispositionInfo(1)
    ctypes.set_last_error(0)
    if not kernel32.SetFileInformationByHandle(
        handle,
        _WINDOWS_FILE_DISPOSITION_INFO,
        ctypes.byref(disposition),
        ctypes.sizeof(disposition),
    ):
        error = ctypes.get_last_error()
        raise OSError(error, "unable to remove exact process record handle")


class _WindowsEventSecurityAttributes(ctypes.Structure):
    _fields_ = [
        ("nLength", ctypes.c_uint32),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", ctypes.c_int),
    ]


def _windows_create_resume_event(name: str) -> int:
    """Create a new restricted event while retaining creator signal authority."""

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_uint32),
    ]
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = ctypes.c_int

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateEventW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_wchar_p,
    ]
    kernel32.CreateEventW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    # LocalFree is exported by kernel32 for the descriptor returned by the
    # ConvertStringSecurityDescriptorToSecurityDescriptorW contract.
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    descriptor = ctypes.c_void_p()
    raw_handle: int | None = None
    completed = False
    ctypes.set_last_error(0)
    try:
        if (
            not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                _WINDOWS_EVENT_DACL_SDDL,
                1,
                ctypes.byref(descriptor),
                None,
            )
            or not descriptor.value
        ):
            raise SolverOwnershipError(
                "unable to create restricted resume event security descriptor"
            )
        security = _WindowsEventSecurityAttributes(
            ctypes.sizeof(_WindowsEventSecurityAttributes),
            descriptor,
            0,
        )
        created = kernel32.CreateEventW(ctypes.byref(security), True, False, name)
        raw_value = created.value if isinstance(created, ctypes.c_void_p) else created
        if raw_value:
            raw_handle = int(raw_value)
        create_error = ctypes.get_last_error()
        if raw_handle is None:
            raise SolverOwnershipError("unable to create resume event")
        if create_error == 183:  # ERROR_ALREADY_EXISTS
            raise SolverOwnershipError("resume event claim already exists")
        completed = True
        return raw_handle
    finally:
        released = kernel32.LocalFree(descriptor)
        if released:
            if raw_handle is not None:
                with contextlib.suppress(BaseException):
                    kernel32.CloseHandle(ctypes.c_void_p(raw_handle))
            raise SolverOwnershipError("unable to release resume event security descriptor")
        if not completed and raw_handle is not None:
            with contextlib.suppress(BaseException):
                kernel32.CloseHandle(ctypes.c_void_p(raw_handle))


def _windows_duplicate_resume_event_handle(handle: int) -> int:
    """Duplicate only synchronize/query rights for the suspended child."""

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.DuplicateHandle.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint32,
    ]
    kernel32.DuplicateHandle.restype = ctypes.c_int
    current = kernel32.GetCurrentProcess()
    duplicate = ctypes.c_void_p()
    if (
        not kernel32.DuplicateHandle(
            current,
            ctypes.c_void_p(handle),
            current,
            ctypes.byref(duplicate),
            _WINDOWS_EVENT_RECONNECT_ACCESS,
            True,
            0,
        )
        or not duplicate.value
    ):
        raise SolverOwnershipError("unable to create inheritable resume event lifetime handle")
    return int(duplicate.value)


def _windows_open_resume_event(name: str) -> int:
    """Open exactly one event with query/synchronize rights and require signaled state."""

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenEventW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.OpenEventW.restype = ctypes.c_void_p
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    ctypes.set_last_error(0)
    handle = kernel32.OpenEventW(_WINDOWS_EVENT_RECONNECT_ACCESS, False, name)
    raw_value = handle.value if isinstance(handle, ctypes.c_void_p) else handle
    if not raw_value:
        raise SolverOwnershipError("resume event authority is unavailable")
    raw_handle = int(raw_value)
    wait_result = kernel32.WaitForSingleObject(ctypes.c_void_p(raw_handle), 0)
    if wait_result != _WINDOWS_WAIT_OBJECT_0:
        with contextlib.suppress(BaseException):
            kernel32.CloseHandle(ctypes.c_void_p(raw_handle))
        if wait_result == _WINDOWS_WAIT_TIMEOUT:
            raise SolverOwnershipError("resume event is not signaled")
        raise SolverOwnershipError("unable to query resume event state")
    return raw_handle


def _windows_signal_resume_event(handle: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.SetEvent.argtypes = [ctypes.c_void_p]
    kernel32.SetEvent.restype = ctypes.c_int
    if not kernel32.SetEvent(ctypes.c_void_p(handle)):
        raise SolverOwnershipError("unable to signal resume event")


def _windows_resume_event_name(
    attempt_root: Path,
    context_digest: str,
    device: int,
    inode: int,
    token: str,
) -> str:
    return (
        f"Local\\febio-cae-resume-{_attempt_binding(attempt_root)}-"
        f"{context_digest}-{device:x}-{inode:x}-{token}"
    )


def _windows_resume_event_binding(
    name: object,
    attempt_root: Path,
    context_digest: str,
    device: int,
    inode: int,
) -> bool:
    return (
        _windows_resume_event_parts(name, attempt_root, context_digest, device, inode) is not None
    )


def _windows_resume_event_parts(
    name: object,
    attempt_root: Path,
    context_digest: str,
    device: int,
    inode: int,
) -> tuple[int, int, str] | None:
    """Parse an event name only after checking every immutable binding."""

    if not isinstance(name, str):
        return None
    match = _WINDOWS_RESUME_EVENT_RE.fullmatch(name)
    if match is None:
        return None
    parsed_device = int(match.group("device"), 16)
    parsed_inode = int(match.group("inode"), 16)
    token = match.group("token")
    if (
        match.group("attempt") != _attempt_binding(attempt_root)
        or match.group("context") != context_digest
        or parsed_device != device
        or parsed_inode != inode
    ):
        return None
    if name != _windows_resume_event_name(
        attempt_root,
        context_digest,
        parsed_device,
        parsed_inode,
        token,
    ):
        return None
    return parsed_device, parsed_inode, token


def _windows_close_resume_event_handles(
    claim: _WindowsResumeTransactionClaim,
) -> tuple[BaseException, ...]:
    failures: list[BaseException] = []
    for attribute in ("child_handle", "event_handle"):
        handle = getattr(claim, attribute)
        if handle is None:
            continue
        try:
            _windows_close_native_handle(handle)
        except BaseException as error:
            failures.append(error)
        else:
            setattr(claim, attribute, None)
    return tuple(failures)


@contextlib.contextmanager
def _posix_record_lock(parent_fd: int) -> Iterator[None]:
    import fcntl

    try:
        fcntl.flock(parent_fd, fcntl.LOCK_EX)  # type: ignore[attr-defined]
    except OSError as error:
        raise SolverOwnershipError("unable to acquire process record transaction lock") from error
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(parent_fd, fcntl.LOCK_UN)  # type: ignore[attr-defined]


def _posix_libc_function(name: str) -> Any:
    libc = ctypes.CDLL(None, use_errno=True)
    function = getattr(libc, name, None)
    if function is None:
        raise OSError(f"POSIX {name} is unavailable")
    return function


def _posix_link_fd(source_fd: int, parent_fd: int, name: str) -> None:
    function = _posix_libc_function("linkat")
    function.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
    ]
    function.restype = ctypes.c_int
    result = function(source_fd, b"", parent_fd, os.fsencode(name), _POSIX_AT_EMPTY_PATH)
    if result != 0:
        error = ctypes.get_errno()
        if error not in {errno.ENOSYS, errno.EPERM, errno.EINVAL}:
            raise OSError(error, "unable to hold exact process record link")
        alias = Path("/proc/self/fd") / str(source_fd)
        try:
            os.link(
                os.fspath(alias),
                name,
                dst_dir_fd=parent_fd,
                follow_symlinks=True,
            )
        except OSError as fallback_error:
            raise OSError(
                fallback_error.errno,
                "unable to hold exact process record link",
            ) from fallback_error


def _posix_rename_exchange(parent_fd: int, left: str, right: str) -> None:
    function = _posix_libc_function("renameat2")
    function.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    function.restype = ctypes.c_int
    result = function(
        parent_fd,
        os.fsencode(left),
        parent_fd,
        os.fsencode(right),
        _POSIX_RENAME_EXCHANGE,
    )
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, "unable to atomically exchange process record entries")


def _posix_unlink(parent_fd: int, name: str) -> None:
    function = _posix_libc_function("unlinkat")
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    function.restype = ctypes.c_int
    result = function(parent_fd, os.fsencode(name), 0)
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, "unable to unlink process record entry")


def _input_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(metadata.st_nlink),
        int(metadata.st_size),
        int(metadata.st_mtime_ns),
    )


def _posix_fcntl() -> Any:
    try:
        import fcntl
    except (ImportError, OSError) as error:
        raise SolverOwnershipError("POSIX input sealing is unavailable") from error
    return fcntl


def _posix_seal_configuration() -> tuple[Any, int]:
    try:
        fcntl = _posix_fcntl()
    except (ImportError, OSError) as error:
        raise SolverOwnershipError("POSIX input sealing is unavailable") from error
    try:
        add_seals = fcntl.F_ADD_SEALS
        get_seals = fcntl.F_GET_SEALS
        required = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
    except (AttributeError, TypeError, ValueError) as error:
        raise SolverOwnershipError("POSIX input sealing is unavailable") from error
    if (
        isinstance(add_seals, bool)
        or not isinstance(add_seals, int)
        or isinstance(get_seals, bool)
        or not isinstance(get_seals, int)
        or isinstance(required, bool)
        or not isinstance(required, int)
        or required <= 0
    ):
        raise SolverOwnershipError("POSIX input sealing is unavailable")
    return (fcntl, required)


def _posix_verify_seals(fd: int) -> None:
    fcntl, required = _posix_seal_configuration()
    try:
        observed = fcntl.fcntl(fd, fcntl.F_GET_SEALS)
    except (AttributeError, OSError, TypeError, ValueError) as error:
        raise SolverOwnershipError("unable to verify POSIX input seals") from error
    if (
        isinstance(observed, bool)
        or not isinstance(observed, int)
        or observed & required != required
    ):
        raise SolverOwnershipError("POSIX input seals could not be verified")


def _posix_seal_fd(fd: int) -> None:
    fcntl, required = _posix_seal_configuration()
    try:
        fcntl.fcntl(fd, fcntl.F_ADD_SEALS, required)
    except (AttributeError, OSError, TypeError, ValueError) as error:
        raise SolverOwnershipError("unable to apply POSIX input seals") from error
    _posix_verify_seals(fd)


def _posix_create_input_snapshot(
    source_fd: int,
    expected_sha256: str,
    expected_identity: tuple[int, int, int, int, int],
) -> tuple[int, tuple[int, int, int, int, int]]:
    """Copy one source descriptor into an anonymous, sealed child object."""

    if not Path("/proc/self/fd").is_dir():
        raise SolverOwnershipError("POSIX /proc descriptor path is unavailable")
    try:
        source_initial = os.fstat(source_fd)
    except OSError as error:
        raise SolverOwnershipError("solver input source descriptor is unavailable") from error
    source_initial_identity = _input_identity(source_initial)
    if not stat.S_ISREG(source_initial.st_mode):
        raise SolverOwnershipError("solver input source is not a regular file")
    if source_initial_identity != expected_identity:
        raise SolverOwnershipError("solver input source identity changed")

    memfd_create = getattr(os, "memfd_create", None)
    sealing_flag = getattr(os, "MFD_ALLOW_SEALING", None)
    if (
        not callable(memfd_create)
        or isinstance(sealing_flag, bool)
        or not isinstance(sealing_flag, int)
    ):
        raise SolverOwnershipError("POSIX anonymous input sealing is unavailable")
    cloexec_flag = getattr(os, "MFD_CLOEXEC", 0)
    if isinstance(cloexec_flag, bool) or not isinstance(cloexec_flag, int):
        raise SolverOwnershipError("POSIX anonymous input descriptor is unavailable")
    snapshot_flags = sealing_flag | cloexec_flag

    snapshot_fd: int | None = None
    completed = False
    try:
        try:
            created = memfd_create("febio-cae-input", snapshot_flags)
        except (OSError, TypeError, ValueError) as error:
            raise SolverOwnershipError("unable to create POSIX anonymous input snapshot") from error
        if isinstance(created, bool) or not isinstance(created, int) or created < 0:
            raise SolverOwnershipError("POSIX anonymous input snapshot descriptor is invalid")
        snapshot_fd = created
        try:
            os.set_inheritable(snapshot_fd, False)
        except (AttributeError, OSError, TypeError, ValueError) as error:
            raise SolverOwnershipError("POSIX anonymous input descriptor is unavailable") from error

        try:
            os.lseek(source_fd, 0, os.SEEK_SET)
            digest_state = hashlib.sha256()
            while chunk := os.read(source_fd, 1024 * 1024):
                digest_state.update(chunk)
                offset = 0
                while offset < len(chunk):
                    written = os.write(snapshot_fd, chunk[offset:])
                    if written <= 0:
                        raise OSError("unable to write POSIX input snapshot")
                    offset += written
            source_finished = os.fstat(source_fd)
        except OSError as error:
            raise SolverOwnershipError("unable to copy POSIX solver input") from error
        if _input_identity(source_finished) != expected_identity:
            raise SolverOwnershipError("solver input source changed while copying")
        if digest_state.hexdigest() != expected_sha256:
            raise SolverOwnershipError("solver input source bytes changed while copying")

        try:
            snapshot_metadata = os.fstat(snapshot_fd)
        except OSError as error:
            raise SolverOwnershipError("POSIX input snapshot is unavailable") from error
        if not stat.S_ISREG(snapshot_metadata.st_mode):
            raise SolverOwnershipError("POSIX input snapshot is not a regular file")
        if int(snapshot_metadata.st_size) != expected_identity[3]:
            raise SolverOwnershipError("POSIX input snapshot size changed")
        _posix_seal_fd(snapshot_fd)
        _posix_verify_seals(snapshot_fd)
        try:
            sealed_metadata = os.fstat(snapshot_fd)
        except OSError as error:
            raise SolverOwnershipError("sealed POSIX input snapshot is unavailable") from error
        if not stat.S_ISREG(sealed_metadata.st_mode):
            raise SolverOwnershipError("sealed POSIX input snapshot is not a regular file")
        if int(sealed_metadata.st_size) != expected_identity[3]:
            raise SolverOwnershipError("sealed POSIX input snapshot size changed")
        sealed_identity = _input_identity(sealed_metadata)
        completed = True
        return snapshot_fd, sealed_identity
    except SolverOwnershipError:
        raise
    except (OSError, TypeError, ValueError) as error:
        raise SolverOwnershipError("unable to create sealed POSIX input snapshot") from error
    finally:
        if snapshot_fd is not None and not completed:
            try:
                os.close(snapshot_fd)
            except BaseException:
                orphan = _InputLease(
                    path=Path("."),
                    expected_sha256=expected_sha256,
                    expected_identity=expected_identity,
                    handle=snapshot_fd,
                )
                _DURABLE_CLAIM_CLEANUP.adopt(None, None, (), input_lease=orphan)


@dataclass(frozen=True, slots=True)
class _FilesystemIdentity:
    path: Path
    device: int
    inode: int


@dataclass(slots=True)
class _InputLease:
    """Hold the exact launch input object until its child has consumed it."""

    path: Path
    expected_sha256: str
    expected_identity: tuple[int, int, int, int, int]
    handle: int | None
    native_handle: int | None = None
    source_handle: int | None = None
    object_identity: tuple[int, int, int, int, int] | None = None

    @property
    def child_path(self) -> Path:
        if os.name == "posix":
            if self.handle is None:
                raise SolverOwnershipError("solver input lease is unavailable")
            proc_fd = Path("/proc/self/fd")
            if not proc_fd.is_dir():
                raise SolverOwnershipError("POSIX /proc descriptor path is unavailable")
            return proc_fd / str(self.handle)
        return self.path

    @property
    def child_pass_fds(self) -> tuple[int, ...]:
        if os.name != "posix" or self.handle is None:
            return ()
        return (self.handle,)

    @property
    def child_handle(self) -> int | None:
        return self.native_handle if _NATIVE_WINDOWS else None

    def prepare_child_handle(self) -> int | None:
        child_handle = self.child_handle
        if child_handle is None:
            return None
        _windows_set_handle_inheritable(child_handle, True)
        return child_handle

    def clear_child_handle(self) -> None:
        child_handle = self.child_handle
        if child_handle is not None:
            _windows_set_handle_inheritable(child_handle, False)

    def verify(self, *, digest: bool = False) -> None:
        if self.handle is None:
            raise SolverOwnershipError("solver input lease is unavailable")
        try:
            metadata = os.fstat(self.handle)
        except OSError as error:
            raise SolverOwnershipError("solver input lease is unavailable") from error
        if not stat.S_ISREG(metadata.st_mode):
            raise SolverOwnershipError("solver input lease is not a regular file")
        current_identity = _input_identity(metadata)
        if os.name == "posix":
            if self.object_identity is None:
                raise SolverOwnershipError("sealed POSIX input identity is unavailable")
            if current_identity != self.object_identity:
                raise SolverOwnershipError("sealed POSIX input identity changed")
            if current_identity[3] != self.expected_identity[3]:
                raise SolverOwnershipError("sealed POSIX input size changed")
            _posix_verify_seals(self.handle)
        elif current_identity != self.expected_identity:
            raise SolverOwnershipError("solver input lease identity changed")
        if not digest:
            return
        try:
            os.lseek(self.handle, 0, os.SEEK_SET)
            digest_state = hashlib.sha256()
            while chunk := os.read(self.handle, 1024 * 1024):
                digest_state.update(chunk)
            finished = os.fstat(self.handle)
            finished_identity = _input_identity(finished)
            if os.name == "posix":
                if self.object_identity is None or finished_identity != self.object_identity:
                    raise SolverOwnershipError("sealed POSIX input changed while reading")
            elif finished_identity != self.expected_identity:
                raise SolverOwnershipError("solver input lease changed while reading")
            if digest_state.hexdigest() != self.expected_sha256:
                raise SolverOwnershipError("solver input lease bytes changed")
            os.lseek(self.handle, 0, os.SEEK_SET)
        except SolverOwnershipError:
            raise
        except OSError as error:
            raise SolverOwnershipError("unable to verify solver input lease bytes") from error

    def close(self) -> None:
        if self.handle is None and self.native_handle is None and self.source_handle is None:
            return
        if _NATIVE_WINDOWS:
            _windows_close_owned_fd(self, "handle", "native_handle")
            return
        failures: list[BaseException] = []
        for attribute in ("handle", "source_handle"):
            handle = getattr(self, attribute)
            if handle is None:
                continue
            try:
                os.close(handle)
            except BaseException as error:
                failures.append(error)
            else:
                if getattr(self, attribute) == handle:
                    setattr(self, attribute, None)
        if failures:
            raise SolverOwnershipError(
                "solver input lease handles could not be closed"
            ) from failures[0]

    def close_source(self) -> None:
        handle = self.source_handle
        if handle is None:
            return
        try:
            os.close(handle)
        except OSError as error:
            raise SolverOwnershipError("solver input source handle could not be closed") from error
        if self.source_handle == handle:
            self.source_handle = None


@dataclass(slots=True)
class _WindowsDirectoryHandle:
    value: int
    volume_serial: int
    file_index: int
    native_handle: int | None = None
    closed: bool = False


class _WindowsFileInformation(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", ctypes.c_uint32),
        ("ftCreationTime", ctypes.c_uint32 * 2),
        ("ftLastAccessTime", ctypes.c_uint32 * 2),
        ("ftLastWriteTime", ctypes.c_uint32 * 2),
        ("dwVolumeSerialNumber", ctypes.c_uint32),
        ("nFileSizeHigh", ctypes.c_uint32),
        ("nFileSizeLow", ctypes.c_uint32),
        ("nNumberOfLinks", ctypes.c_uint32),
        ("nFileIndexHigh", ctypes.c_uint32),
        ("nFileIndexLow", ctypes.c_uint32),
    ]


def _windows_kernel32() -> Any:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.GetFileInformationByHandle.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_WindowsFileInformation),
    ]
    kernel32.GetFileInformationByHandle.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    return kernel32


def _windows_directory_information(handle: int) -> _WindowsFileInformation:
    kernel32 = _windows_kernel32()
    information = _WindowsFileInformation()
    if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(information)):
        error = ctypes.get_last_error()
        raise OSError(error, "unable to query directory handle identity")
    if not information.dwFileAttributes & _WINDOWS_FILE_ATTRIBUTE_DIRECTORY:
        raise SolverOwnershipError("filesystem authority handle is not a directory")
    if information.dwFileAttributes & _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT:
        raise SolverOwnershipError("filesystem authority directory is a reparse point")
    return information


def _windows_open_directory(path: Path) -> _WindowsDirectoryHandle:
    kernel32 = _windows_kernel32()
    desired_access = _WINDOWS_FILE_READ_ATTRIBUTES | _WINDOWS_FILE_LIST_DIRECTORY
    share_mode = _WINDOWS_FILE_SHARE_READ | _WINDOWS_FILE_SHARE_WRITE
    flags = _WINDOWS_FILE_FLAG_BACKUP_SEMANTICS | _WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT
    ctypes.set_last_error(0)
    raw_handle = kernel32.CreateFileW(
        os.fspath(path),
        desired_access,
        share_mode,
        None,
        _WINDOWS_OPEN_EXISTING,
        flags,
        None,
    )
    raw_value = raw_handle.value if isinstance(raw_handle, ctypes.c_void_p) else raw_handle
    if raw_value is None:
        raw_value_int = 0
    else:
        try:
            raw_value_int = int(raw_value)
        except (TypeError, ValueError, OverflowError):
            raw_value_int = 0
    if not raw_value_int or raw_value_int in {-1, _WINDOWS_INVALID_HANDLE_VALUE}:
        error = ctypes.get_last_error()
        raise OSError(error, f"unable to hold directory authority: {path}")
    held = _WindowsDirectoryHandle(
        value=raw_value_int,
        volume_serial=0,
        file_index=0,
    )
    _DURABLE_CLAIM_CLEANUP.adopt_directory_handle(held)
    try:
        held.native_handle = _windows_duplicate_native_handle(raw_value_int)
        information = _windows_directory_information(raw_value_int)
        held.volume_serial = int(information.dwVolumeSerialNumber)
        held.file_index = (int(information.nFileIndexHigh) << 32) | int(information.nFileIndexLow)
    except BaseException:
        try:
            _windows_close_directory(held)
        except BaseException:
            pass
        else:
            _DURABLE_CLAIM_CLEANUP.discard_directory_handle(held)
        raise
    _DURABLE_CLAIM_CLEANUP.discard_directory_handle(held)
    return held


def _windows_close_directory(handle: _WindowsDirectoryHandle) -> None:
    value = handle.value
    native_handle = handle.native_handle
    if handle.closed:
        if native_handle is None:
            return
        _windows_close_native_handle(native_handle)
        handle.native_handle = None
        return
    if native_handle is None:
        raise OSError("filesystem authority handle has no exact native guard")
    identity_matches = _windows_compare_object_handles(value, native_handle)
    if identity_matches is None:
        raise OSError("unable to verify filesystem authority handle identity")
    if not identity_matches:
        handle.closed = True
        _windows_close_native_handle(native_handle)
        handle.native_handle = None
        return

    kernel32 = _windows_kernel32()
    ctypes.set_last_error(0)
    if not kernel32.CloseHandle(ctypes.c_void_p(value)):
        error = ctypes.get_last_error()
        after_close = _windows_compare_object_handles(value, native_handle)
        if after_close is False:
            handle.closed = True
            _windows_close_native_handle(native_handle)
            handle.native_handle = None
            return
        raise OSError(error, "unable to close filesystem authority handle")
    handle.closed = True
    _windows_close_native_handle(native_handle)
    handle.native_handle = None


def _windows_verify_directory(handle: _WindowsDirectoryHandle) -> None:
    if handle.closed:
        raise SolverOwnershipError("filesystem authority handle is unavailable")
    try:
        information = _windows_directory_information(handle.value)
    except SolverOwnershipError:
        raise
    except OSError as error:
        raise SolverOwnershipError("filesystem authority handle is unavailable") from error
    file_index = (int(information.nFileIndexHigh) << 32) | int(information.nFileIndexLow)
    if (
        int(information.dwVolumeSerialNumber) != handle.volume_serial
        or file_index != handle.file_index
    ):
        raise SolverOwnershipError("filesystem authority handle identity changed")


def _windows_set_handle_inheritable(native_handle: int, inheritable: bool) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.SetHandleInformation.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32]
    kernel32.SetHandleInformation.restype = ctypes.c_int
    ctypes.set_last_error(0)
    if not kernel32.SetHandleInformation(
        ctypes.c_void_p(native_handle),
        ctypes.c_uint32(0x00000001),
        ctypes.c_uint32(0x00000001 if inheritable else 0),
    ):
        error = ctypes.get_last_error()
        raise OSError(error, "unable to configure solver input handle inheritance")


def _windows_open_input(path: Path) -> int:
    """Open the exact solver input with read-only, replacement-blocking sharing."""

    kernel32 = _windows_kernel32()
    ctypes.set_last_error(0)
    raw_handle = kernel32.CreateFileW(
        os.fspath(path),
        _WINDOWS_GENERIC_READ,
        _WINDOWS_FILE_SHARE_READ,
        None,
        _WINDOWS_OPEN_EXISTING,
        _WINDOWS_FILE_ATTRIBUTE_NORMAL | _WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    raw_value = raw_handle.value if isinstance(raw_handle, ctypes.c_void_p) else raw_handle
    try:
        value = 0 if raw_value is None else int(cast(int, raw_value))
    except (TypeError, ValueError, OverflowError):
        value = 0
    if not value or value in {-1, _WINDOWS_INVALID_HANDLE_VALUE}:
        error = ctypes.get_last_error()
        raise OSError(error, f"unable to hold solver input authority: {path}")
    return cast(
        int,
        _windows_convert_raw_handle(
            value,
            os.O_RDONLY,
            duplicate_native_handle=_windows_duplicate_native_handle,
            close_native_handle=_windows_close_native_handle,
        ),
    )


class _FilesystemAuthority:
    """Keep the attempt path and every ancestor bound to stable identities."""

    def __init__(self, root: Path) -> None:
        self.root = Path(os.path.abspath(os.fspath(root)))
        self._entries: tuple[
            tuple[_FilesystemIdentity, int | _WindowsDirectoryHandle | None], ...
        ] = ()
        self._root_fd: int | None = None
        self._closed = False
        self._close_pending = False
        entries: list[tuple[_FilesystemIdentity, int | _WindowsDirectoryHandle | None]] = []
        try:
            paths: list[Path] = []
            current = self.root
            while True:
                paths.append(current)
                if current == current.parent:
                    break
                current = current.parent

            parent_fd: int | None = None
            for path in reversed(paths):
                held: int | _WindowsDirectoryHandle | None = None
                if os.name == "posix":
                    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
                    try:
                        if parent_fd is None:
                            held = os.open(os.fspath(path), flags)
                        else:
                            held = os.open(path.name, flags, dir_fd=parent_fd)
                    except OSError as error:
                        raise SolverOwnershipError(
                            f"unable to hold directory authority: {path}"
                        ) from error
                    metadata = os.fstat(held)
                    if not stat.S_ISDIR(metadata.st_mode):
                        with contextlib.suppress(OSError):
                            os.close(held)
                        raise SolverOwnershipError(
                            f"attempt path is not a stable directory: {path}"
                        )
                    identity = _FilesystemIdentity(
                        path,
                        int(metadata.st_dev),
                        int(metadata.st_ino),
                    )
                    if path == self.root:
                        self._root_fd = held
                    parent_fd = held
                elif os.name == "nt" and _NATIVE_WINDOWS:
                    try:
                        held = _windows_open_directory(path)
                        metadata = os.lstat(os.fspath(path))
                        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                            raise SolverOwnershipError(
                                f"attempt path is not a stable directory: {path}"
                            )
                        identity = _FilesystemIdentity(
                            path,
                            int(metadata.st_dev),
                            int(metadata.st_ino),
                        )
                    except BaseException:
                        if isinstance(held, _WindowsDirectoryHandle):
                            try:
                                _windows_close_directory(held)
                            except BaseException:
                                _DURABLE_CLAIM_CLEANUP.adopt_directory_handle(held)
                        raise
                else:
                    metadata = os.lstat(os.fspath(path))
                    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                        raise SolverOwnershipError(
                            f"attempt path is not a stable directory: {path}"
                        )
                    identity = _FilesystemIdentity(
                        path,
                        int(metadata.st_dev),
                        int(metadata.st_ino),
                    )
                entries.append((identity, held))
            if parent_fd is not None and os.name == "posix":
                # ``parent_fd`` is the root descriptor and is retained in entries.
                parent_fd = None
            self._entries = tuple(entries)
            self.verify()
        except BaseException:
            self._entries = tuple(entries)
            try:
                self.close()
            except BaseException:
                _DURABLE_CLAIM_CLEANUP.adopt(
                    None,
                    None,
                    (),
                    filesystem_authority=self,
                )
            raise

    @property
    def child_pass_fds(self) -> tuple[int, ...]:
        return () if self._root_fd is None else (self._root_fd,)

    @property
    def root_fd(self) -> int | None:
        return self._root_fd

    @property
    def identities(self) -> tuple[_FilesystemIdentity, ...]:
        return tuple(identity for identity, _fd in self._entries)

    def verify(self) -> None:
        if self._closed or self._close_pending:
            raise SolverOwnershipError("filesystem authority is closed")
        for identity, held in self._entries:
            if os.name == "posix" and isinstance(held, int):
                try:
                    metadata = os.fstat(held)
                except OSError as error:
                    raise SolverOwnershipError(
                        f"attempt directory handle is unavailable: {identity.path}"
                    ) from error
                if (
                    not stat.S_ISDIR(metadata.st_mode)
                    or int(metadata.st_dev) != identity.device
                    or int(metadata.st_ino) != identity.inode
                ):
                    raise SolverOwnershipError(f"attempt directory handle changed: {identity.path}")
            elif os.name == "nt" and isinstance(held, _WindowsDirectoryHandle):
                _windows_verify_directory(held)
            else:
                try:
                    metadata = os.lstat(os.fspath(identity.path))
                except OSError as error:
                    raise SolverOwnershipError(
                        f"attempt path authority is unavailable: {identity.path}"
                    ) from error
                if (
                    stat.S_ISLNK(metadata.st_mode)
                    or not stat.S_ISDIR(metadata.st_mode)
                    or int(metadata.st_dev) != identity.device
                    or int(metadata.st_ino) != identity.inode
                ):
                    raise SolverOwnershipError(f"attempt path authority changed: {identity.path}")

    def anchor(self, path: Path) -> Path:
        """Return a child path rooted at the held attempt directory on POSIX."""

        path = Path(os.path.abspath(os.fspath(path)))
        try:
            relative = path.relative_to(self.root)
        except ValueError as error:
            raise SolverOwnershipError(f"path escapes the attempt root: {path}") from error
        if os.name != "posix":
            return path
        if self._root_fd is None:
            raise SolverOwnershipError("POSIX attempt directory handle is unavailable")
        return Path("/proc/self/fd") / str(self._root_fd) / relative

    @contextlib.contextmanager
    def open_directory(self, path: Path, *, create: bool = False) -> Iterator[int]:
        """Open a directory relative to the held POSIX root descriptor."""

        if os.name != "posix":
            raise SolverOwnershipError("descriptor-relative directories require POSIX")
        self.verify()
        root_fd = self._root_fd
        if root_fd is None:
            raise SolverOwnershipError("POSIX attempt directory handle is unavailable")
        path = Path(os.path.abspath(os.fspath(path)))
        try:
            relative = path.relative_to(self.root)
        except ValueError as error:
            raise SolverOwnershipError(f"path escapes the attempt root: {path}") from error
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        current_fd = os.dup(root_fd)
        try:
            for component in relative.parts:
                try:
                    next_fd = os.open(component, flags, dir_fd=current_fd)
                except FileNotFoundError:
                    if not create:
                        raise
                    with contextlib.suppress(FileExistsError):
                        os.mkdir(component, dir_fd=current_fd)
                    next_fd = os.open(component, flags, dir_fd=current_fd)
                previous_fd = current_fd
                current_fd = next_fd
                try:
                    os.close(previous_fd)
                except OSError:
                    with contextlib.suppress(OSError):
                        os.close(current_fd)
                    current_fd = -1
                    raise
            yield current_fd
        except OSError as error:
            raise SolverOwnershipError(f"unable to open directory authority: {path}") from error
        finally:
            if current_fd >= 0:
                with contextlib.suppress(OSError):
                    os.close(current_fd)

    def close(self) -> None:
        if self._closed:
            return
        failures: list[BaseException] = []
        remaining: list[tuple[_FilesystemIdentity, int | _WindowsDirectoryHandle | None]] = []
        for identity, held in self._entries:
            if os.name == "posix" and isinstance(held, int):
                try:
                    os.close(held)
                except BaseException as error:
                    failures.append(error)
                    remaining.append((identity, held))
            elif os.name == "nt" and isinstance(held, _WindowsDirectoryHandle):
                try:
                    _windows_close_directory(held)
                except BaseException as error:
                    failures.append(error)
                    remaining.append((identity, held))
        self._entries = tuple(remaining)
        self._root_fd = next(
            (
                held
                for identity, held in remaining
                if identity.path == self.root and isinstance(held, int)
            ),
            None,
        )
        if failures:
            self._close_pending = True
            raise SolverOwnershipError("filesystem authority could not be closed") from failures[0]
        self._root_fd = None
        self._close_pending = False
        self._closed = True


@dataclass(slots=True)
class _ProcessRecordClaim:
    path: Path
    content: bytes
    device: int
    inode: int
    state: str | None
    handle: int | None = None
    parent_fd: int | None = None
    name: str = _PROCESS_RECORD_NAME
    native_handle: int | None = None
    parent_native_handle: int | None = None


@dataclass(slots=True)
class _WindowsResumeTransactionClaim:
    """Held authority for the crash-recovery transition journal."""

    path: Path
    content: bytes
    device: int
    inode: int
    handle: int | None
    native_handle: int | None
    event_name: str = ""
    event_token: str = ""
    event_handle: int | None = None
    child_handle: int | None = None
    inherited_child_handle: int | None = None


class _DurableClaimCleanup:
    """Retain only exact claim objects whose descriptors could not be closed."""

    __slots__ = (
        "_lock",
        "_directory_handles",
        "_filesystem_authorities",
        "_input_leases",
        "_runtime_claims",
        "_process_authorities",
        "_process_record_claims",
        "_resume_transaction_claims",
    )

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._directory_handles: list[_WindowsDirectoryHandle] = []
        self._filesystem_authorities: list[_FilesystemAuthority] = []
        self._input_leases: list[_InputLease] = []
        self._runtime_claims: list[_RuntimeLaunchClaim] = []
        self._process_authorities: list[ProcessAuthority] = []
        self._process_record_claims: list[_ProcessRecordClaim] = []
        self._resume_transaction_claims: list[_WindowsResumeTransactionClaim] = []

    @staticmethod
    def _discard_identity(entries: list[Any], target: Any) -> None:
        entries[:] = [entry for entry in entries if entry is not target]

    def adopt_directory_handle(self, handle: _WindowsDirectoryHandle) -> None:
        """Retain one exact raw directory handle until it can be closed."""

        with self._lock:
            if (not handle.closed or handle.native_handle is not None) and not any(
                held is handle for held in self._directory_handles
            ):
                self._directory_handles.append(handle)

    def discard_directory_handle(self, handle: _WindowsDirectoryHandle) -> None:
        with self._lock:
            self._discard_identity(self._directory_handles, handle)

    def adopt(
        self,
        runtime_claim: _RuntimeLaunchClaim | None,
        process_record_claim: _ProcessRecordClaim | None,
        candidates: Iterable[_ProcessRecordClaim],
        *,
        process_authority: ProcessAuthority | None = None,
        filesystem_authority: _FilesystemAuthority | None = None,
        input_lease: _InputLease | None = None,
        resume_transaction: _WindowsResumeTransactionClaim | None = None,
    ) -> None:
        """Adopt still-live claims without retaining their supervisor owner."""

        with self._lock:
            if (
                filesystem_authority is not None
                and not filesystem_authority._closed
                and not any(held is filesystem_authority for held in self._filesystem_authorities)
            ):
                self._filesystem_authorities.append(filesystem_authority)
            if (
                input_lease is not None
                and (
                    input_lease.handle is not None
                    or input_lease.native_handle is not None
                    or input_lease.source_handle is not None
                )
                and not any(held is input_lease for held in self._input_leases)
            ):
                self._input_leases.append(input_lease)
            if (
                runtime_claim is not None
                and (
                    runtime_claim.handle is not None
                    or runtime_claim.native_handle is not None
                    or runtime_claim.source_handle is not None
                )
                and not any(held is runtime_claim for held in self._runtime_claims)
            ):
                self._runtime_claims.append(runtime_claim)
            if process_authority is not None and not any(
                held is process_authority for held in self._process_authorities
            ):
                self._process_authorities.append(process_authority)
            for candidate in (
                process_record_claim,
                *tuple(candidates),
            ):
                if candidate is None:
                    continue
                if (
                    candidate.handle is None
                    and candidate.parent_fd is None
                    and candidate.native_handle is None
                    and candidate.parent_native_handle is None
                ):
                    continue
                if not any(held is candidate for held in self._process_record_claims):
                    self._process_record_claims.append(candidate)
            if (
                resume_transaction is not None
                and (
                    resume_transaction.handle is not None
                    or resume_transaction.native_handle is not None
                    or resume_transaction.event_handle is not None
                    or resume_transaction.child_handle is not None
                )
                and not any(held is resume_transaction for held in self._resume_transaction_claims)
            ):
                self._resume_transaction_claims.append(resume_transaction)

    def _drain_runtime_claims(self) -> None:
        for claim in tuple(self._runtime_claims):
            if claim.handle is None and claim.native_handle is None and claim.source_handle is None:
                self._discard_identity(self._runtime_claims, claim)
                continue
            try:
                claim.close()
            except BaseException:
                continue
            if claim.handle is None and claim.native_handle is None and claim.source_handle is None:
                self._discard_identity(self._runtime_claims, claim)

    def _drain_process_authorities(self) -> None:
        for authority in tuple(self._process_authorities):
            try:
                authority.close()
            except BaseException:
                continue
            self._discard_identity(self._process_authorities, authority)

    @staticmethod
    def _close_record_claim_descriptors(claim: _ProcessRecordClaim) -> None:
        for attribute, native_attribute in (
            ("handle", "native_handle"),
            ("parent_fd", "parent_native_handle"),
        ):
            fd = getattr(claim, attribute)
            native_handle = getattr(claim, native_attribute)
            if fd is None and native_handle is None:
                continue
            if _NATIVE_WINDOWS:
                try:
                    if attribute == "handle":
                        with _WINDOWS_RECORD_CLAIMS_LOCK:
                            old_fd = claim.handle
                            old_native = claim.native_handle
                            _windows_close_owned_fd(claim, attribute, native_attribute)
                            _windows_unregister_record_claim(
                                claim.path,
                                old_fd if old_fd is not None else -1,
                                device=claim.device,
                                inode=claim.inode,
                                native_handle=old_native,
                                owner=claim,
                            )
                    else:
                        _windows_close_owned_fd(claim, attribute, native_attribute)
                except BaseException:
                    continue
                continue
            try:
                os.close(fd)
                setattr(claim, attribute, None)
            except BaseException:
                continue

    def _drain_process_record_claims(self) -> None:
        for claim in tuple(self._process_record_claims):
            if (
                claim.handle is None
                and claim.parent_fd is None
                and claim.native_handle is None
                and claim.parent_native_handle is None
            ):
                self._discard_identity(self._process_record_claims, claim)
                continue
            self._close_record_claim_descriptors(claim)
            if (
                claim.handle is None
                and claim.parent_fd is None
                and claim.native_handle is None
                and claim.parent_native_handle is None
            ):
                self._discard_identity(self._process_record_claims, claim)

    def _drain_resume_transaction_claims(self) -> None:
        for claim in tuple(self._resume_transaction_claims):
            if (
                claim.handle is None
                and claim.native_handle is None
                and claim.event_handle is None
                and claim.child_handle is None
            ):
                self._discard_identity(self._resume_transaction_claims, claim)
                continue
            _windows_close_resume_event_handles(claim)
            try:
                if claim.handle is not None or claim.native_handle is not None:
                    _windows_close_owned_fd(claim, "handle", "native_handle")
            except BaseException:
                continue
            if (
                claim.handle is None
                and claim.native_handle is None
                and claim.event_handle is None
                and claim.child_handle is None
            ):
                self._discard_identity(self._resume_transaction_claims, claim)

    def _drain_filesystem_authorities(self) -> None:
        for authority in tuple(self._filesystem_authorities):
            try:
                authority.close()
            except BaseException:
                continue
            self._discard_identity(self._filesystem_authorities, authority)

    def _drain_directory_handles(self) -> None:
        for handle in tuple(self._directory_handles):
            try:
                _windows_close_directory(handle)
            except BaseException:
                continue
            if handle.closed and handle.native_handle is None:
                self._discard_identity(self._directory_handles, handle)

    def _drain_input_leases(self) -> None:
        for lease in tuple(self._input_leases):
            try:
                lease.close()
            except BaseException:
                continue
            if lease.handle is None and lease.native_handle is None and lease.source_handle is None:
                self._discard_identity(self._input_leases, lease)

    def drain(self) -> None:
        """Retry exact descriptor closes; persistent failures remain owned."""

        with self._lock:
            self._drain_directory_handles()
            self._drain_filesystem_authorities()
            self._drain_input_leases()
            _drain_runtime_claims()
            self._drain_runtime_claims()
            self._drain_process_authorities()
            self._drain_process_record_claims()
            self._drain_resume_transaction_claims()
            _drain_windows_probe_claims()


_DURABLE_CLAIM_CLEANUP = _DurableClaimCleanup()


def _drain_durable_cleanup(
    owner: _DurableClaimCleanup = _DURABLE_CLAIM_CLEANUP,
) -> None:
    """Opportunistically retry durable claim cleanup without raising."""

    with contextlib.suppress(BaseException):
        owner.drain()


atexit.register(_drain_durable_cleanup)


class _ProcessRecordPath(type(Path())):  # type: ignore[misc]
    """Path view that routes owner-local record observations through safe probes."""

    __slots__ = ("_record_owner",)

    def __new__(cls, path: str | Path, owner: SolverSupervisor | None = None) -> _ProcessRecordPath:
        instance = super().__new__(cls, path)
        return cast(_ProcessRecordPath, instance)

    def __init__(self, path: str | Path, owner: SolverSupervisor | None = None) -> None:
        super().__init__(path)
        self._record_owner = owner

    def read_text(
        self,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> str:
        owner = cast(SolverSupervisor | None, getattr(self, "_record_owner", None))
        if owner is not None and self.name == _PROCESS_RECORD_NAME and _NATIVE_WINDOWS:
            return owner._read_process_record_text(encoding=encoding, errors=errors)
        return cast(str, super().read_text(encoding=encoding, errors=errors, newline=newline))

    def write_text(
        self,
        data: str,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> int:
        owner = cast(SolverSupervisor | None, getattr(self, "_record_owner", None))
        if owner is not None and self.name == _PROCESS_RECORD_NAME and _NATIVE_WINDOWS:
            return owner._write_process_record_text(
                data,
                encoding=encoding,
                errors=errors,
            )
        return cast(
            int,
            super().write_text(
                data,
                encoding=encoding,
                errors=errors,
                newline=newline,
            ),
        )

    def unlink(self, missing_ok: bool = False) -> None:
        owner = cast(SolverSupervisor | None, getattr(self, "_record_owner", None))
        if owner is not None and self.name == _PROCESS_RECORD_NAME and _NATIVE_WINDOWS:
            owner._unlink_process_record_path(missing_ok=missing_ok)
            return
        super().unlink(missing_ok=missing_ok)


@dataclass(frozen=True, slots=True)
class _ResultIssuance:
    result: SolverRunResult
    supervisor: SolverSupervisor
    spec: SolverLaunchSpec
    process: subprocess.Popen[bytes] | _ReconnectedProcess | None
    process_record: dict[str, object] | None
    case_id: str
    intent_id: str
    attempt_id: str
    executable_path: str
    process_identity: str | None
    started_at: datetime | None
    log_path: Path
    xplt_path: Path
    finished_at: datetime | None
    filesystem_identities: tuple[_FilesystemIdentity, ...]
    snapshot: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class _ProcessMetadata:
    executable_path: str
    creation_identity: str
    alive: bool
    return_code: int | None
    started_at: datetime | None = None
    executable_snapshot: _FileSnapshot | None = None


def _normalise_executable(path: str | Path) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(path))))


def _windows_process_metadata(pid: int) -> _ProcessMetadata:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        raise ProcessLookupError(pid)
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            raise OSError(ctypes.get_last_error(), "unable to query process exit code")
        code = exit_code.value
        if code != 259:  # STILL_ACTIVE
            return _ProcessMetadata("", "", False, code)
        buffer = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buffer))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            raise OSError(ctypes.get_last_error(), "unable to query process executable")
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel32.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
            raise OSError(ctypes.get_last_error(), "unable to query process creation time")
        creation_value = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        started_at = datetime(1601, 1, 1, tzinfo=UTC) + timedelta(microseconds=creation_value // 10)
        return _ProcessMetadata(
            executable_path=buffer.value,
            creation_identity=f"windows:{creation_value}",
            alive=True,
            return_code=None,
            started_at=started_at,
        )
    finally:
        kernel32.CloseHandle(handle)


def _posix_process_metadata(pid: int) -> _ProcessMetadata:
    proc_root = Path("/proc") / str(pid)
    if not Path("/proc").is_dir() or not proc_root.is_dir():
        raise ProcessLookupError(pid)
    try:
        stat_line = (proc_root / "stat").read_text(encoding="utf-8")
        stat_fields = stat_line.rpartition(")")[2].split()
        executable_path = os.readlink(os.fspath(proc_root / "exe"))
    except (OSError, TypeError, ValueError) as error:
        raise ProcessLookupError(pid) from error
    if not isinstance(executable_path, str) or not executable_path:
        raise ProcessLookupError(pid)
    if len(stat_fields) < 20:
        raise OSError("unable to read process start identity")
    state = stat_fields[0]
    try:
        boot_time = next(
            int(line.split()[1])
            for line in Path("/proc/stat").read_text(encoding="utf-8").splitlines()
            if line.startswith("btime ")
        )
        sysconf = getattr(os, "sysconf", None)
        if not callable(sysconf):
            raise OSError("process clock tick query is unsupported")
        clock_ticks = int(sysconf("SC_CLK_TCK"))
        if clock_ticks <= 0 or not stat_fields[19].isdigit():
            raise ValueError
        started_at = datetime.fromtimestamp(
            boot_time + int(stat_fields[19]) / clock_ticks,
            UTC,
        )
    except (OSError, StopIteration, ValueError, TypeError, OverflowError) as error:
        raise OSError("unable to read process start identity") from error
    executable_fd: int | None = None
    try:
        close_on_exec = getattr(os, "O_CLOEXEC", None)
        if (
            isinstance(close_on_exec, bool)
            or not isinstance(close_on_exec, int)
            or close_on_exec <= 0
        ):
            raise OSError("process executable descriptor primitive is unavailable")
        executable_fd = os.open(
            os.fspath(proc_root / "exe"),
            os.O_RDONLY | close_on_exec,
        )
        executable_snapshot = _snapshot_from_fd(executable_fd)
    except (AttributeError, OSError, RuntimeProbeError, TypeError, ValueError) as error:
        raise OSError("unable to read process executable image") from error
    finally:
        if executable_fd is not None:
            try:
                os.close(executable_fd)
            except (AttributeError, OSError, TypeError, ValueError) as error:
                raise OSError("unable to close process executable descriptor") from error
    return _ProcessMetadata(
        executable_path=executable_path,
        creation_identity=f"posix:{stat_fields[19]}",
        alive=state not in {"Z", "X"},
        return_code=None if state not in {"Z", "X"} else 0,
        started_at=started_at,
        executable_snapshot=executable_snapshot,
    )


def _process_metadata(pid: int) -> _ProcessMetadata:
    if os.name == "nt":
        return _windows_process_metadata(pid)
    if os.name == "posix":
        return _posix_process_metadata(pid)
    raise OSError("process identity is unsupported on this platform")


def _process_action(process: subprocess.Popen[bytes] | _ReconnectedProcess, action: str) -> None:
    getattr(process, action)()


def _detach_windows_process_for_finalizer(process: object) -> None:
    """Release one live Popen wrapper without inspecting or acting on its child."""

    if not _NATIVE_WINDOWS or not isinstance(process, subprocess.Popen):
        return
    if getattr(process, "_child_created", False) and getattr(process, "returncode", None) is None:
        # Popen.__del__ otherwise emits a warning and polls the child.  The
        # supervisor finalizer is only allowed to release this object's own
        # runtime handle; reconnect retains the durable process record and
        # process authority for a later owner.
        cast(Any, process)._child_created = False


class _ReconnectedProcess:
    """Small process handle for a process that is not this client's child."""

    def __init__(
        self,
        pid: int,
        executable_path: str,
        creation_identity: str,
        authority: ProcessAuthority,
        executable_snapshot: _FileSnapshot | None = None,
    ) -> None:
        self.pid = pid
        self._executable_path = executable_path
        self._creation_identity = creation_identity
        self._authority = authority
        self._executable_snapshot = executable_snapshot

    def poll(self) -> int | None:
        try:
            metadata = _process_metadata(self.pid)
        except ProcessLookupError:
            return 1
        if not metadata.alive:
            return metadata.return_code
        if metadata.alive and metadata.creation_identity != self._creation_identity:
            raise SolverOwnershipError("reconnected process identity no longer matches")
        if metadata.alive:
            if os.name == "posix":
                if (
                    self._executable_snapshot is None
                    or metadata.executable_snapshot != self._executable_snapshot
                ):
                    raise SolverOwnershipError("reconnected process image no longer matches")
            elif _normalise_executable(metadata.executable_path) != _normalise_executable(
                self._executable_path
            ):
                raise SolverOwnershipError("reconnected process identity no longer matches")
        try:
            self._authority.verify(self.pid)
        except ProcessAuthorityError as error:
            raise SolverOwnershipError(
                "reconnected process authority is no longer valid"
            ) from error
        return None

    def wait(self, timeout: float | None = None) -> int | None:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            return_code = self.poll()
            if return_code is not None:
                return return_code
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(str(self.pid), timeout or 0.0)
            time.sleep(0.05)


class SolverSupervisor:
    """Launch and monitor one solver process with attempt-owned outputs.

    The process is started without a shell and, where supported, in its own
    process group.  ``cancel`` and timeout handling operate on that owned
    group; callers never need to supply a raw PID.
    """

    def __init__(
        self,
        launch_capability: SolverLaunchCapability,
        *,
        fbs_adapter: FbsAdapterAuthority | None = None,
        requested_fields: Iterable[str] | None = None,
        log_validator: LogValidator | None = None,
    ) -> None:
        (
            _capability_record,
            case_id,
            intent_id,
            attempt_id,
            _attempt_root,
            launch_context,
            launch_context_digest,
        ) = _validate_launch_capability(launch_capability)
        self._launch_capability = launch_capability
        self._runtime_diagnostic = _capability_record.runtime_diagnostic
        self.spec = _capability_record.spec
        self._case_id = case_id
        self._intent_id = intent_id
        self._attempt_id = attempt_id
        self._launch_context = launch_context
        self._launch_context_digest = launch_context_digest
        bound_fbs_adapter = _capability_record.fbs_adapter
        if bound_fbs_adapter is not None:
            if fbs_adapter is not None and fbs_adapter is not bound_fbs_adapter:
                raise SolverConfigurationError("fbs adapter does not match launch capability")
            fbs_adapter = cast(FbsAdapterAuthority, bound_fbs_adapter)
        if fbs_adapter is not None:
            try:
                fbs_record = _authority_record(fbs_adapter)
            except TypeError as error:
                raise SolverConfigurationError(
                    "fbs_adapter must be issued by FbsAdapterManager"
                ) from error
            if bound_fbs_adapter is None and fbs_record.official:
                raise SolverConfigurationError(
                    "official fbs adapter must be bound into the launch capability"
                )
        self._fbs_adapter = fbs_adapter
        if requested_fields is not None and tuple(requested_fields) != self.spec.requested_fields:
            raise SolverConfigurationError("requested fields do not match launch capability")
        self._requested_fields = self.spec.requested_fields
        self._log_validator = log_validator
        self._owner_token = uuid.uuid4().hex
        self._lock = threading.RLock()
        self._state = SolverState.NOT_STARTED
        self._process: subprocess.Popen[bytes] | _ReconnectedProcess | None = None
        self._process_authority: ProcessAuthority | None = None
        self._started_at: datetime | None = None
        self._result_latch: SolverRunResult | None = None
        self._result_issuance: _ResultIssuance | None = None
        self._result: SolverRunResult | None = None
        self._process_record: dict[str, object] | None = None
        self._process_record_claim: _ProcessRecordClaim | None = None
        self._process_record_candidates: list[_ProcessRecordClaim] = []
        self._pending_process_record_claim: _ProcessRecordClaim | None = None
        self._resume_transaction_claim: _WindowsResumeTransactionClaim | None = None
        self._runtime_launch_claim: _RuntimeLaunchClaim | None = None
        self._runtime_image_snapshot: _FileSnapshot | None = None
        self._input_lease: _InputLease | None = None
        self._record_owner_token = self._owner_token
        _drain_durable_cleanup()
        filesystem_authority = _FilesystemAuthority(self.spec.attempt_root)
        try:
            self._filesystem_authority: _FilesystemAuthority | None = filesystem_authority
            if fbs_adapter is not None:
                fbs_record = _authority_record(fbs_adapter)
                root_binding = fbs_record.root_binding
                filesystem_root_identity = next(
                    (
                        identity
                        for identity in self._filesystem_authority.identities
                        if identity.path == self.spec.attempt_root
                    ),
                    None,
                )
                if (
                    fbs_record.attempt_root != self.spec.attempt_root
                    or root_binding is None
                    or filesystem_root_identity is None
                    or (
                        root_binding.device,
                        root_binding.inode,
                    )
                    != (
                        filesystem_root_identity.device,
                        filesystem_root_identity.inode,
                    )
                ):
                    raise SolverConfigurationError("fbs adapter root authority does not match")
            self._revalidate_launch_binding()
        except BaseException:
            try:
                filesystem_authority.close()
            except BaseException:
                _DURABLE_CLAIM_CLEANUP.adopt(
                    None,
                    None,
                    (),
                    filesystem_authority=filesystem_authority,
                )
            raise

    def __del__(self) -> None:
        process = getattr(self, "_process", None)
        try:
            with contextlib.suppress(BaseException):
                # Finalization releases this supervisor's filesystem, record,
                # runtime, input, and resume claims.  A failed close leaves
                # only the exact residual claims for durable cleanup.
                self._close_filesystem_authority()
            process_authority = getattr(self, "_process_authority", None)
            if process_authority is not None:
                try:
                    # Closing this supervisor's authority only releases its
                    # own handles; it must never drain, terminate, reap, or
                    # mutate the live child or its reconnect record.
                    process_authority.close()
                except BaseException:
                    _DURABLE_CLAIM_CLEANUP.adopt(
                        None,
                        None,
                        (),
                        process_authority=process_authority,
                    )
                self._process_authority = None
            pending = getattr(self, "_pending_process_record_claim", None)
            candidate_values = tuple(getattr(self, "_process_record_candidates", ()))
            if pending is not None:
                candidate_values += (pending,)
            # Any failed close above remains attached to its exact claim object;
            # this finalizer never reopens authority by path.
            _DURABLE_CLAIM_CLEANUP.adopt(
                getattr(self, "_runtime_launch_claim", None),
                getattr(self, "_process_record_claim", None),
                candidate_values,
                filesystem_authority=getattr(self, "_filesystem_authority", None),
                input_lease=getattr(self, "_input_lease", None),
                resume_transaction=getattr(self, "_resume_transaction_claim", None),
            )
        except BaseException:
            # Finalization must not surface an unraisable exception. Claims
            # already adopted above remain owned by the process-lifetime owner.
            pass
        finally:
            with contextlib.suppress(BaseException):
                _detach_windows_process_for_finalizer(process)

    @property
    def state(self) -> SolverState:
        with self._lock:
            return self._state

    @property
    def process_id(self) -> int | None:
        with self._lock:
            return self._process.pid if self._process is not None else None

    @property
    def pid(self) -> int | None:
        """Read-only diagnostic PID; cancellation remains supervisor-owned."""

        return self.process_id

    @property
    def owner_token(self) -> str:
        return self._owner_token

    @property
    def process_record_path(self) -> Path:
        """The only persisted authority used to reconnect this attempt."""

        path = self.spec.attempt_root / _PROCESS_RECORD_NAME
        return _ProcessRecordPath(path, self) if _NATIVE_WINDOWS else path

    @property
    def result(self) -> SolverRunResult | None:
        with self._lock:
            latch = self._result_latch
            issuance = self._result_issuance
            if latch is None or issuance is None or issuance.result is not latch:
                return None
            return latch

    def _acquire_filesystem_authority(self) -> None:
        _drain_durable_cleanup()
        if self._filesystem_authority is None:
            self._filesystem_authority = _FilesystemAuthority(self.spec.attempt_root)
        self._filesystem_authority.verify()

    def _verify_filesystem_authority(self) -> None:
        authority = self._filesystem_authority
        if authority is None:
            raise SolverOwnershipError("filesystem authority is unavailable")
        authority.verify()
        self._verify_input_lease()

    def _recorded_input_binding(self) -> tuple[str, tuple[int, int, int, int, int]]:
        expected_path = self._launch_context.get("input_path")
        expected_sha256 = self._launch_context.get("input_sha256")
        identity_value = self._launch_context.get("input_identity")
        if not isinstance(expected_path, str) or not expected_path:
            raise SolverOwnershipError("solver input path binding is invalid")
        canonical_path = os.path.normcase(
            os.path.realpath(os.path.abspath(os.fspath(self.spec.input_path)))
        )
        if expected_path != canonical_path:
            raise SolverOwnershipError("solver input path binding is invalid")
        if not isinstance(expected_sha256, str) or not expected_sha256:
            raise SolverOwnershipError("solver input SHA256 binding is invalid")
        if not isinstance(identity_value, dict):
            raise SolverOwnershipError("solver input identity binding is invalid")
        identity: list[int] = []
        for name in ("device", "inode", "nlink", "size", "mtime_ns"):
            value = identity_value.get(name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise SolverOwnershipError("solver input identity binding is invalid")
            identity.append(value)
        return expected_sha256, cast(tuple[int, int, int, int, int], tuple(identity))

    def _acquire_input_lease(self) -> None:
        if self._input_lease is not None:
            self._input_lease.verify()
            return
        expected_sha256, expected_identity = self._recorded_input_binding()
        input_path = self.spec.input_path
        handle: int
        native_handle: int | None = None
        object_identity: tuple[int, int, int, int, int] | None = None
        source_fd: int | None = None
        if os.name == "posix":
            authority = self._filesystem_authority
            if authority is None:
                raise SolverOwnershipError("filesystem authority is unavailable")
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            try:
                with authority.open_directory(input_path.parent) as parent_fd:
                    source_fd = os.open(input_path.name, flags, dir_fd=parent_fd)
            except OSError as error:
                raise SolverOwnershipError("unable to hold exact solver input") from error
            try:
                handle, object_identity = _posix_create_input_snapshot(
                    source_fd,
                    expected_sha256,
                    expected_identity,
                )
            except BaseException:
                try:
                    os.close(source_fd)
                except BaseException:
                    orphan = _InputLease(
                        path=input_path,
                        expected_sha256=expected_sha256,
                        expected_identity=expected_identity,
                        handle=None,
                        source_handle=source_fd,
                    )
                    _DURABLE_CLAIM_CLEANUP.adopt(None, None, (), input_lease=orphan)
                raise
        elif os.name == "nt" and _NATIVE_WINDOWS:
            try:
                opened = _windows_open_input(input_path)
            except OSError as error:
                raise SolverOwnershipError("unable to hold exact solver input") from error
            handle = int(opened)
            native_handle = getattr(opened, "native_handle", None)
            if not isinstance(native_handle, int) or isinstance(native_handle, bool):
                with contextlib.suppress(BaseException):
                    os.close(handle)
                raise SolverOwnershipError("exact solver input has no native handle")
        else:
            raise SolverOwnershipError("exact solver input leases are unsupported")

        lease = _InputLease(
            path=input_path,
            expected_sha256=expected_sha256,
            expected_identity=expected_identity,
            handle=handle,
            native_handle=native_handle,
            source_handle=source_fd,
            object_identity=object_identity,
        )
        source_fd = None
        try:
            lease.close_source()
            lease.verify(digest=True)
        except BaseException:
            try:
                lease.close()
            except BaseException:
                _DURABLE_CLAIM_CLEANUP.adopt(None, None, (), input_lease=lease)
            raise
        self._input_lease = lease

    def _verify_input_lease(self, *, digest: bool = False) -> None:
        lease = self._input_lease
        if lease is not None:
            lease.verify(digest=digest)

    def _release_input_lease(self) -> tuple[BaseException, ...]:
        lease = self._input_lease
        if lease is None:
            return ()
        try:
            lease.close()
        except BaseException as error:
            return (error,)
        self._input_lease = None
        return ()

    def _filesystem_identities(self) -> tuple[_FilesystemIdentity, ...]:
        authority = self._filesystem_authority
        if authority is None:
            raise SolverOwnershipError("filesystem authority is unavailable")
        return authority.identities

    @staticmethod
    def _validate_filesystem_identities(
        identities: tuple[_FilesystemIdentity, ...],
    ) -> None:
        for identity in identities:
            try:
                metadata = os.lstat(os.fspath(identity.path))
            except OSError as error:
                raise SolverOwnershipError(
                    f"filesystem authority is unavailable: {identity.path}"
                ) from error
            if (
                stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISDIR(metadata.st_mode)
                or int(metadata.st_dev) != identity.device
                or int(metadata.st_ino) != identity.inode
            ):
                raise SolverOwnershipError(f"filesystem authority changed: {identity.path}")

    def _record_filesystem_identities(self) -> list[dict[str, object]]:
        return [
            {
                "path": os.fspath(identity.path),
                "device": identity.device,
                "inode": identity.inode,
            }
            for identity in self._filesystem_identities()
        ]

    def _parse_record_filesystem_identities(self, value: object) -> tuple[_FilesystemIdentity, ...]:
        if not isinstance(value, list):
            raise SolverOwnershipError("process record filesystem authority is invalid")
        identities: list[_FilesystemIdentity] = []
        for item in value:
            if not isinstance(item, dict):
                raise SolverOwnershipError("process record filesystem authority is invalid")
            path_value = item.get("path")
            device = item.get("device")
            inode = item.get("inode")
            if (
                not isinstance(path_value, str)
                or not Path(path_value).is_absolute()
                or isinstance(device, bool)
                or not isinstance(device, int)
                or isinstance(inode, bool)
                or not isinstance(inode, int)
            ):
                raise SolverOwnershipError("process record filesystem authority is invalid")
            identities.append(
                _FilesystemIdentity(
                    Path(os.path.abspath(path_value)),
                    device,
                    inode,
                )
            )
        return tuple(identities)

    def _release_process_record_claim(
        self, claim: _ProcessRecordClaim | None
    ) -> tuple[BaseException, ...]:
        failures: list[BaseException] = []
        if claim is not None:
            for attribute, native_attribute in (
                ("handle", "native_handle"),
                ("parent_fd", "parent_native_handle"),
            ):
                fd = getattr(claim, attribute)
                native_handle = getattr(claim, native_attribute)
                if fd is not None or native_handle is not None:
                    try:
                        if _NATIVE_WINDOWS:
                            if attribute == "handle":
                                with _WINDOWS_RECORD_CLAIMS_LOCK:
                                    old_fd = claim.handle
                                    old_native = claim.native_handle
                                    _windows_close_owned_fd(
                                        claim,
                                        attribute,
                                        native_attribute,
                                    )
                                    _windows_unregister_record_claim(
                                        claim.path,
                                        old_fd if old_fd is not None else -1,
                                        device=claim.device,
                                        inode=claim.inode,
                                        native_handle=old_native,
                                        owner=claim,
                                    )
                            else:
                                _windows_close_owned_fd(
                                    claim,
                                    attribute,
                                    native_attribute,
                                )
                        else:
                            os.close(fd)
                    except BaseException as error:
                        failures.append(error)
                    else:
                        if getattr(claim, attribute) == fd:
                            setattr(claim, attribute, None)
            if (
                claim.handle is None
                and claim.parent_fd is None
                and claim.native_handle is None
                and claim.parent_native_handle is None
            ):
                if _NATIVE_WINDOWS:
                    _windows_unregister_record_claim(
                        claim.path,
                        -1,
                        owner=claim,
                    )
                self._process_record_candidates[:] = [
                    candidate
                    for candidate in self._process_record_candidates
                    if candidate is not claim
                ]
                if self._pending_process_record_claim is claim:
                    self._pending_process_record_claim = None
        return tuple(failures)

    def _release_runtime_launch_claim(self) -> tuple[BaseException, ...]:
        claim = self._runtime_launch_claim
        if claim is None:
            return ()
        try:
            claim.close()
        except BaseException as error:
            return (error,)
        self._runtime_launch_claim = None
        return ()

    def _release_resume_transaction_claim(self) -> tuple[BaseException, ...]:
        claim = self._resume_transaction_claim
        if claim is None:
            return ()
        failures = list(_windows_close_resume_event_handles(claim))
        if claim.handle is not None or claim.native_handle is not None:
            try:
                _windows_close_owned_fd(claim, "handle", "native_handle")
            except BaseException as error:
                failures.append(error)
        if (
            claim.handle is None
            and claim.native_handle is None
            and claim.event_handle is None
            and claim.child_handle is None
        ):
            self._resume_transaction_claim = None
        return tuple(failures)

    def _delete_resume_transaction(self) -> tuple[BaseException, ...]:
        """Delete only the exact held transition journal, then release it."""

        claim = self._resume_transaction_claim
        if claim is None:
            return ()
        failures: list[BaseException] = []
        if claim.handle is None:
            failures.append(SolverOwnershipError("resume transaction handle is unavailable"))
        else:
            try:
                _windows_delete_process_record(claim.handle)
            except BaseException as error:
                failures.append(error)
        failures.extend(self._release_resume_transaction_claim())
        return tuple(failures)

    def _close_filesystem_authority(self) -> None:
        authority = self._filesystem_authority
        failures: list[BaseException] = []
        if authority is not None:
            try:
                authority.close()
            except BaseException as error:
                failures.append(error)
            else:
                self._filesystem_authority = None
        claim = self._process_record_claim
        failures.extend(self._release_process_record_claim(claim))
        if claim is None or (
            claim.handle is None
            and claim.parent_fd is None
            and claim.native_handle is None
            and claim.parent_native_handle is None
        ):
            self._process_record_claim = None
        for candidate in tuple(self._process_record_candidates):
            failures.extend(self._release_process_record_claim(candidate))
        failures.extend(self._release_resume_transaction_claim())
        failures.extend(self._release_input_lease())
        failures.extend(self._release_runtime_launch_claim())
        _drain_runtime_claims()
        if failures:
            raise SolverOwnershipError("authority handles could not be closed") from failures[0]

    def _path_for_io(self, path: Path) -> Path:
        authority = self._filesystem_authority
        return path if authority is None else authority.anchor(path)

    def _prepare_outputs(self) -> None:
        """Prepare outputs without leaving the held attempt authority."""

        authority = self._filesystem_authority
        if authority is None:
            raise SolverOwnershipError("filesystem authority is unavailable")
        if os.name != "posix":
            self.spec.prepare_outputs()
            return

        outputs = self.spec.expected_outputs
        for path, label in ((outputs.log_path, "LOG"), (outputs.xplt_path, "XPLT")):
            try:
                with authority.open_directory(path.parent, create=True) as parent_fd:
                    try:
                        os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    raise OutputFreshnessError(f"{label} path already exists: {path}")
            except SolverOwnershipError:
                raise
            except OSError as error:
                raise SolverOwnershipError(f"unable to prepare {label} output: {path}") from error

    def _input_is_regular(self) -> bool:
        input_path = self.spec.input_path
        authority = self._filesystem_authority
        if authority is None:
            raise SolverOwnershipError("filesystem authority is unavailable")
        if os.name == "posix":
            try:
                with authority.open_directory(input_path.parent) as parent_fd:
                    metadata = os.stat(input_path.name, dir_fd=parent_fd, follow_symlinks=False)
            except SolverOwnershipError as error:
                if isinstance(error.__cause__, FileNotFoundError):
                    return False
                raise
            except OSError:
                return False
            return stat.S_ISREG(metadata.st_mode)
        path = self._path_for_io(input_path)
        try:
            metadata = os.stat(os.fspath(path), follow_symlinks=False)
        except OSError:
            return False
        return stat.S_ISREG(metadata.st_mode)

    def _child_command(self) -> tuple[str, ...]:
        root = self.spec.attempt_root
        input_lease = self._input_lease
        command: list[str] = []
        for value in self.spec.command:
            try:
                candidate = Path(value)
            except (TypeError, ValueError):
                command.append(value)
                continue
            runtime_claim = self._runtime_launch_claim
            if runtime_claim is not None and candidate == self.spec.executable:
                command.append(os.fspath(runtime_claim.child_path))
                continue
            if input_lease is not None and candidate == self.spec.input_path:
                command.append(os.fspath(input_lease.child_path))
                continue
            if candidate.is_absolute() and candidate.is_relative_to(root):
                command.append(os.fspath(self._path_for_io(candidate)))
            else:
                command.append(value)
        return tuple(command)

    def _child_environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        environment.update(self.spec.environment)
        root = self.spec.attempt_root
        for key, value in tuple(environment.items()):
            try:
                candidate = Path(value)
            except (TypeError, ValueError):
                continue
            if candidate.is_absolute() and candidate.is_relative_to(root):
                environment[key] = os.fspath(self._path_for_io(candidate))

        outputs = self.spec.expected_outputs
        environment["FEBIO_CAE_HARNESS_OWNER"] = self._owner_token
        environment["FEBIO_CAE_HARNESS_ATTEMPT_ROOT"] = os.fspath(self._path_for_io(root))
        environment["FEBIO_CAE_HARNESS_LOG"] = os.fspath(self._path_for_io(outputs.log_path))
        environment["FEBIO_CAE_HARNESS_XPLT"] = os.fspath(self._path_for_io(outputs.xplt_path))
        return environment

    def _cleanup_failed_start(
        self,
        process: subprocess.Popen[bytes] | None,
        authority: ProcessAuthority | None,
        bound: bool,
    ) -> tuple[BaseException, ...]:
        """Attempt every failed-start cleanup action and retain all failures."""

        failures: list[BaseException] = []
        if process is not None:
            if authority is not None and bound:
                termination_failed = False
                try:
                    if self._process_authority is authority:
                        self._terminate_owned_process(process)
                    else:
                        authority.terminate(process.pid)
                except BaseException as error:
                    failures.append(error)
                    termination_failed = True
                if termination_failed:
                    try:
                        authority.terminate(process.pid, force=True)
                    except BaseException as error:
                        failures.append(error)
            elif not bound:
                try:
                    process.kill()
                except BaseException as error:
                    failures.append(error)
            try:
                process.wait(timeout=2.0)
            except BaseException as error:
                failures.append(error)
        if authority is not None:
            try:
                authority.drain()
            except BaseException as error:
                failures.append(error)
            try:
                authority.close()
            except BaseException as error:
                failures.append(error)
        return tuple(failures)

    def _record_claim_matches(
        self,
        claim: _ProcessRecordClaim,
        *,
        content: bytes | None = None,
    ) -> bool:
        """Check the held record object and directory entry without mutating either."""

        if claim.handle is None:
            return False
        try:
            if not self._record_claim_identity_matches(claim):
                return False
            expected_content = claim.content if content is None else content
            return _read_record_fd(claim.handle) == expected_content
        except FileNotFoundError:
            return False
        except OSError as error:
            raise SolverOwnershipError("unable to verify the owned process record") from error

    def _record_claim_identity_matches(self, claim: _ProcessRecordClaim) -> bool:
        if claim.handle is None:
            return False
        try:
            if _NATIVE_WINDOWS:
                if claim.native_handle is None:
                    raise SolverOwnershipError("owned process record has no exact native guard")
                exact_match = _windows_fd_identity_matches(
                    claim.handle,
                    claim.native_handle,
                )
                if exact_match is None:
                    raise SolverOwnershipError("unable to verify Windows CRT descriptor identity")
                if not exact_match:
                    claim.handle = None
                    return False
            handle_metadata = os.fstat(claim.handle)
            if (
                not stat.S_ISREG(handle_metadata.st_mode)
                or int(handle_metadata.st_dev) != claim.device
                or int(handle_metadata.st_ino) != claim.inode
            ):
                return False
            if claim.parent_fd is not None:
                entry_metadata = os.stat(
                    claim.name,
                    dir_fd=claim.parent_fd,
                    follow_symlinks=False,
                )
                return _same_file_identity(entry_metadata, handle_metadata)
            if _NATIVE_WINDOWS:
                try:
                    path_fd = _windows_probe_process_record(claim.path)
                except FileNotFoundError:
                    return False
                try:
                    return _same_file_identity(handle_metadata, os.fstat(path_fd))
                finally:
                    with contextlib.suppress(BaseException):
                        _close_windows_probe(path_fd)
            return True
        except FileNotFoundError:
            return False
        except OSError as error:
            raise SolverOwnershipError("unable to verify the owned process record") from error

    def _rollback_process_record(self) -> tuple[BaseException, ...]:
        """Remove only the exact record object this owner published."""

        failures: list[BaseException] = []
        if self._resume_transaction_claim is not None:
            failures.extend(self._delete_resume_transaction())
        claim = self._process_record_claim
        if claim is None or claim.handle is None:
            claim = next(
                (
                    candidate
                    for candidate in reversed(self._process_record_candidates)
                    if candidate.handle is not None
                    and candidate.state in {"BOUND_SUSPENDED", SolverState.RUNNING.value}
                ),
                None,
            )
        if claim is None or claim.handle is None:
            return tuple(failures)
        try:
            if _NATIVE_WINDOWS:
                _windows_delete_process_record(claim.handle)
            else:
                parent_fd = claim.parent_fd
                if parent_fd is None:
                    raise SolverOwnershipError("process record transaction parent is unavailable")
                self._rollback_posix_process_record(claim)
        except FileNotFoundError as error:
            if not _NATIVE_WINDOWS:
                return tuple(failures)
            failure = SolverOwnershipError("unable to roll back the owned process record")
            failure.__cause__ = error
            failures.append(failure)
            return tuple(failures)
        except BaseException as error:
            failure = SolverOwnershipError("unable to roll back the owned process record")
            failure.__cause__ = error
            failures.append(failure)
            return tuple(failures)
        return tuple(failures)

    def _rollback_posix_process_record(self, claim: _ProcessRecordClaim) -> None:
        """CAS-remove a held record, preserving any name replacement."""

        parent_fd = claim.parent_fd
        handle = claim.handle
        if parent_fd is None or handle is None:
            raise SolverOwnershipError("process record transaction handle is unavailable")
        tombstone = f".{claim.name}.{self._owner_token}.rollback"
        tombstone_is_owner = True
        with _posix_record_lock(parent_fd):
            try:
                _posix_link_fd(handle, parent_fd, tombstone)
            except OSError as error:
                raise SolverOwnershipError(
                    "process record cleanup cannot establish an exact transaction"
                ) from error
            try:
                try:
                    _posix_rename_exchange(parent_fd, claim.name, tombstone)
                except FileNotFoundError:
                    _posix_unlink(parent_fd, tombstone)
                    return
                except OSError as error:
                    with contextlib.suppress(OSError):
                        _posix_unlink(parent_fd, tombstone)
                    raise SolverOwnershipError(
                        "process record cleanup cannot exchange the owned entry"
                    ) from error

                entry_metadata = os.stat(
                    tombstone,
                    dir_fd=parent_fd,
                    follow_symlinks=False,
                )
                owner_metadata = os.fstat(handle)
                tombstone_is_owner = _same_file_identity(entry_metadata, owner_metadata)
                if tombstone_is_owner:
                    current_metadata = os.stat(
                        claim.name,
                        dir_fd=parent_fd,
                        follow_symlinks=False,
                    )
                    if _same_file_identity(current_metadata, owner_metadata):
                        _posix_unlink(parent_fd, claim.name)
                    _posix_unlink(parent_fd, tombstone)
                    return

                # A foreign entry occupied the name at exchange time.  Put it
                # back only when the owner entry is still the exchange result;
                # otherwise leave every foreign entry untouched and fail closed.
                current_metadata = os.stat(
                    claim.name,
                    dir_fd=parent_fd,
                    follow_symlinks=False,
                )
                if not _same_file_identity(current_metadata, owner_metadata):
                    raise SolverOwnershipError(
                        "process record cleanup encountered an uncertain replacement"
                    )
                _posix_rename_exchange(parent_fd, claim.name, tombstone)
                tombstone_is_owner = True
                _posix_unlink(parent_fd, tombstone)
            except BaseException:
                # The tombstone is an exact held-object link.  Removing it
                # cannot remove a foreign replacement at ``process.json``.
                if tombstone_is_owner:
                    with contextlib.suppress(OSError):
                        _posix_unlink(parent_fd, tombstone)
                raise

    def _revalidate_launch_binding(self) -> None:
        """Require the supervisor's capability and authority binding to remain exact."""

        (
            capability_record,
            case_id,
            intent_id,
            attempt_id,
            _attempt_root,
            launch_context,
            launch_context_digest,
        ) = _validate_launch_capability(self._launch_capability)
        if capability_record.spec is not self.spec:
            raise SolverConfigurationError("supervisor launch binding is invalid")
        if (case_id, intent_id, attempt_id) != (
            self._case_id,
            self._intent_id,
            self._attempt_id,
        ):
            raise SolverConfigurationError("supervisor authority binding is invalid")
        if (
            launch_context != self._launch_context
            or launch_context_digest != self._launch_context_digest
        ):
            raise SolverConfigurationError("supervisor launch context binding is invalid")

    def _validated_retry_correlation(self) -> tuple[object, ...]:
        """Return the live case/intent correlation bound to this supervisor."""

        try:
            (
                capability_record,
                case_id,
                intent_sha256,
                attempt_id,
                attempt_root,
                _launch_context,
                _launch_context_digest,
            ) = _validate_launch_capability(self._launch_capability)
            if capability_record.spec is not self.spec:
                raise SolverConfigurationError("supervisor launch binding is invalid")
            if (case_id, intent_sha256, attempt_id) != (
                self._case_id,
                self._intent_id,
                self._attempt_id,
            ):
                raise SolverConfigurationError("supervisor authority binding is invalid")

            launch_snapshot = capability_record.intent_snapshot
            if type(launch_snapshot) is not IntentSnapshotAuthority:
                raise SolverConfigurationError("supervisor launch snapshot is invalid")
            attempt_workspace = capability_record.attempt_workspace
            case_workspace = object.__getattribute__(launch_snapshot, "_case_workspace")
            case_sha256 = launch_snapshot.case_sha256
            case_root = case_workspace.root
            if not attempt_root.is_relative_to(case_root):
                raise SolverConfigurationError("supervisor attempt is outside its case")

            # Revalidate after reading the snapshot projection so a late
            # capability or snapshot mutation cannot be returned as authority.
            (
                final_record,
                final_case_id,
                final_intent_sha256,
                final_attempt_id,
                final_attempt_root,
                _final_launch_context,
                _final_launch_context_digest,
            ) = _validate_launch_capability(self._launch_capability)
            final_snapshot = final_record.intent_snapshot
            if type(final_snapshot) is not IntentSnapshotAuthority:
                raise SolverConfigurationError("supervisor launch snapshot is invalid")
            final_case_workspace = object.__getattribute__(final_snapshot, "_case_workspace")
            final_case_sha256 = final_snapshot.case_sha256
            final_case_root = final_case_workspace.root
            if (
                final_record is not capability_record
                or final_record.spec is not self.spec
                or final_case_id != case_id
                or final_intent_sha256 != intent_sha256
                or final_attempt_id != attempt_id
                or final_attempt_root != attempt_root
                or final_snapshot is not launch_snapshot
                or final_case_workspace is not case_workspace
                or final_case_sha256 != case_sha256
                or final_case_root != case_root
            ):
                raise SolverConfigurationError(
                    "supervisor retry correlation changed during validation"
                )
        except SolverConfigurationError:
            raise
        except Exception as error:
            raise SolverConfigurationError("supervisor retry correlation is not live") from error

        return (
            self._launch_capability,
            capability_record.spec,
            attempt_workspace,
            launch_snapshot,
            case_workspace,
            case_root,
            case_id,
            case_sha256,
            intent_sha256,
            attempt_id,
            attempt_root,
        )

    def start(self) -> SolverSupervisor:
        """Prepare fresh outputs and launch the owned process."""

        with self._lock:
            if self._state is not SolverState.NOT_STARTED:
                raise RuntimeError(f"solver cannot start from state {self._state}")

            # Revalidate every authority immediately before acquiring the
            # filesystem handles used to anchor all later launch paths.
            self._revalidate_launch_binding()

            authority: ProcessAuthority | None = None
            process: subprocess.Popen[bytes] | None = None
            runtime_claim: _RuntimeLaunchClaim | None = None
            bound = False
            try:
                self._acquire_filesystem_authority()
                self._verify_filesystem_authority()
                self._prepare_outputs()
                self._verify_filesystem_authority()
                self._revalidate_launch_binding()
                if not self._input_is_regular():
                    raise FileNotFoundError(f"solver input does not exist: {self.spec.input_path}")
                self._acquire_input_lease()
                self._verify_input_lease(digest=True)
                authority = ProcessAuthority.create(
                    self.spec.attempt_root, self._launch_context_digest
                )
                self._verify_filesystem_authority()
                try:
                    runtime_claim = _acquire_runtime_launch_claim(self._runtime_diagnostic)
                except RuntimeProbeError as error:
                    raise SolverLaunchError(
                        "unable to hold the probed runtime executable identity"
                    ) from error
                self._runtime_launch_claim = runtime_claim
                if os.name == "nt":
                    self._reserve_windows_resume_transaction()
                environment = self._child_environment()
                environment.update(authority.child_environment())
                command = self._child_command()
                cwd = os.fspath(self._path_for_io(self.spec.cwd))

                if os.name == "nt":
                    creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | (
                        _CREATE_SUSPENDED
                    )
                    child_handle = authority.child_handle()
                    if child_handle is None:
                        raise ProcessAuthorityError("process attestation handle is unavailable")
                    input_lease = self._input_lease
                    if input_lease is None:
                        raise SolverOwnershipError("solver input lease is unavailable")
                    input_child_handle = input_lease.prepare_child_handle()
                    if input_child_handle is None:
                        raise SolverOwnershipError("solver input child handle is unavailable")
                    transaction_claim = self._resume_transaction_claim
                    if transaction_claim is None or transaction_claim.child_handle is None:
                        raise SolverOwnershipError("resume transaction child handle is unavailable")
                    startup_info = subprocess.STARTUPINFO()
                    startup_info.lpAttributeList = {
                        "handle_list": [
                            child_handle,
                            input_child_handle,
                            transaction_claim.child_handle,
                        ]
                    }
                    try:
                        self._revalidate_launch_binding()
                        self._verify_filesystem_authority()
                        process = subprocess.Popen(
                            command,
                            cwd=cwd,
                            env=environment,
                            shell=False,
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            creationflags=creation_flags,
                            close_fds=True,
                            startupinfo=startup_info,
                        )
                    finally:
                        input_lease.clear_child_handle()
                        self._clear_resume_transaction_child_handle()
                else:
                    filesystem_authority = self._filesystem_authority
                    if filesystem_authority is None:  # pragma: no cover - state guard
                        raise SolverOwnershipError("filesystem authority is unavailable")
                    input_lease = self._input_lease
                    if input_lease is None:
                        raise SolverOwnershipError("solver input lease is unavailable")
                    pass_fds = tuple(
                        dict.fromkeys(
                            authority.child_pass_fds()
                            + filesystem_authority.child_pass_fds
                            + (() if runtime_claim is None else runtime_claim.child_pass_fds)
                            + input_lease.child_pass_fds
                        )
                    )
                    self._revalidate_launch_binding()
                    self._verify_filesystem_authority()
                    process = subprocess.Popen(
                        command,
                        cwd=cwd,
                        env=environment,
                        shell=False,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                        pass_fds=pass_fds,
                    )
                self._verify_filesystem_authority()
                metadata = _process_metadata(process.pid)
                if runtime_claim is not None:
                    runtime_claim.authenticate(
                        metadata.executable_path,
                        image_snapshot=metadata.executable_snapshot,
                    )
                authority.bind(process.pid, metadata.creation_identity)
                bound = True
                self._verify_filesystem_authority()
                bound_metadata = _process_metadata(process.pid)
                if (
                    not bound_metadata.alive
                    or bound_metadata.creation_identity != metadata.creation_identity
                ):
                    raise ProcessAuthorityError(
                        "process creation identity changed during assignment"
                    )
                if runtime_claim is not None:
                    runtime_claim.authenticate(
                        bound_metadata.executable_path,
                        image_snapshot=bound_metadata.executable_snapshot,
                    )
                metadata = bound_metadata
                if runtime_claim is not None:
                    if os.name == "posix":
                        if (
                            metadata.executable_snapshot is None
                            or metadata.executable_snapshot != runtime_claim.snapshot
                            or metadata.executable_snapshot.nlink != 0
                        ):
                            raise ProcessAuthorityError(
                                "executed POSIX runtime image attestation is unavailable"
                            )
                        self._runtime_image_snapshot = metadata.executable_snapshot
                    else:
                        self._runtime_image_snapshot = runtime_claim.snapshot
                if metadata.started_at is None:
                    raise ProcessAuthorityError("process start identity is unavailable")
                started_at = metadata.started_at
                self._revalidate_launch_binding()
                self._verify_filesystem_authority()
                self._process = process
                self._started_at = started_at
                self._process_authority = authority
                if os.name == "nt":
                    bound_record = self._make_process_record(
                        process.pid,
                        metadata,
                        started_at,
                        authority,
                        state="BOUND_SUSPENDED",
                    )
                    self._write_process_record(bound_record)
                    self._verify_filesystem_authority()
                    bound_claim = self._process_record_claim
                    if bound_claim is None or not self._record_claim_matches(bound_claim):
                        raise SolverOwnershipError(
                            "BOUND_SUSPENDED process record binding changed before resume"
                        )
                    running_record = self._make_process_record(
                        process.pid,
                        metadata,
                        started_at,
                        authority,
                        state=SolverState.RUNNING.value,
                    )
                    self._prepare_windows_resume_transaction(bound_record, running_record)
                    resume_metadata = _process_metadata(process.pid)
                    if (
                        not resume_metadata.alive
                        or resume_metadata.creation_identity != metadata.creation_identity
                    ):
                        raise ProcessAuthorityError(
                            "process creation identity changed before resume"
                        )
                    if runtime_claim is not None:
                        runtime_claim.authenticate(resume_metadata.executable_path)
                    self._signal_windows_resume_transaction()
                    authority.resume(process.pid)
                    self._revalidate_launch_binding()
                    self._verify_filesystem_authority()
                    if runtime_claim is not None:
                        release_failures = self._release_runtime_launch_claim()
                        if release_failures:
                            raise SolverOwnershipError(
                                "runtime executable claim could not be closed"
                            ) from release_failures[0]
                        runtime_claim = None
                elif runtime_claim is not None:
                    release_failures = self._release_runtime_launch_claim()
                    if release_failures:
                        raise SolverOwnershipError(
                            "runtime executable claim could not be closed"
                        ) from release_failures[0]
                    runtime_claim = None
                running_record = self._make_process_record(
                    process.pid, metadata, started_at, authority, state=SolverState.RUNNING.value
                )
                self._write_process_record(running_record)
                self._process_record = running_record
                running_claim = self._process_record_claim
                if running_claim is None or not self._record_claim_matches(running_claim):
                    raise SolverOwnershipError("RUNNING process record binding changed")
                # A final binding check occurs only after RUNNING is durable.
                # Failure here must roll back that exact record before the
                # process and filesystem authorities are released.
                self._revalidate_launch_binding()
                self._verify_filesystem_authority()
                if os.name == "nt":
                    transaction_failures = self._delete_resume_transaction()
                    if transaction_failures:
                        raise SolverOwnershipError(
                            "resume transaction could not be finalized"
                        ) from transaction_failures[0]
            except BaseException as error:
                cleanup_failures: list[BaseException] = []
                cleanup_failures.extend(self._release_runtime_launch_claim())
                cleanup_failures.extend(self._cleanup_failed_start(process, authority, bound))
                cleanup_failures.extend(self._rollback_process_record())
                self._process = None
                self._started_at = None
                self._process_authority = None
                self._process_record = None
                try:
                    self._close_filesystem_authority()
                except BaseException as close_error:
                    cleanup_failures.append(close_error)
                self._state = SolverState.FAILED
                if cleanup_failures:
                    details = "; ".join(str(failure) for failure in cleanup_failures)
                    cleanup_error = SolverOwnershipError(f"solver launch cleanup failed: {details}")
                    raise cleanup_error from error
                if isinstance(
                    error,
                    (SolverConfigurationError, SolverLaunchError, SolverOwnershipError),
                ):
                    raise
                raise SolverLaunchError(f"unable to launch solver: {error}") from error

            self._state = SolverState.RUNNING
            return self

    launch = start

    def poll(self) -> int | None:
        """Return the process return code, if it has exited."""

        with self._lock:
            process = self._process
        if process is None:
            return None
        self._verify_filesystem_authority()
        return_code = process.poll()
        self._verify_filesystem_authority()
        return return_code

    @classmethod
    def reconnect(
        cls,
        launch_capability: SolverLaunchCapability,
        *,
        fbs_adapter: FbsAdapterAuthority | None = None,
        requested_fields: Iterable[str] | None = None,
        log_validator: LogValidator | None = None,
    ) -> SolverSupervisor:
        """Rebuild a supervisor only after validating its owned record."""

        supervisor = cls(
            launch_capability,
            fbs_adapter=fbs_adapter,
            requested_fields=requested_fields,
            log_validator=log_validator,
        )
        authority: ProcessAuthority | None = None
        try:
            supervisor._acquire_filesystem_authority()
            if os.name != "posix":
                supervisor._acquire_input_lease()
            record = supervisor._read_process_record()
            supervisor._revalidate_launch_binding()
            transition: tuple[dict[str, object], dict[str, object]] | None = None
            recovering_transition = False
            if _NATIVE_WINDOWS:
                pending_claim = supervisor._pending_process_record_claim
                if pending_claim is None:
                    raise SolverOwnershipError("process record candidate is unavailable")
                if record.get("state") == "BOUND_SUSPENDED":
                    if not supervisor._resume_transaction_path().is_file():
                        raise SolverOwnershipError(
                            "BOUND_SUSPENDED process record is not a reconnectable RUNNING state "
                            "and has no recoverable transaction"
                        )
                    # Authenticate the live process, deterministic Job, and
                    # exact current record binding before opening the journal
                    # or trusting its event name.
                    authenticated_record = dict(record)
                    authenticated_record["state"] = SolverState.RUNNING.value
                    metadata, started_at, authority = supervisor._validate_process_record(
                        authenticated_record
                    )
                    expected_binding = supervisor._parse_resume_transaction_record_binding(
                        authenticated_record
                    )
                    try:
                        transition = supervisor._read_windows_resume_transaction(
                            pending_claim,
                            expected_binding,
                        )
                    except FileNotFoundError as error:
                        raise SolverOwnershipError(
                            "BOUND_SUSPENDED process record is not a reconnectable RUNNING state "
                            "and has no recoverable transaction"
                        ) from error
                    supervisor._validate_windows_resume_transaction(
                        transition[0], transition[1], record
                    )
                    if authority is None:  # pragma: no cover - state guard
                        raise SolverOwnershipError("recorded process authority is unavailable")
                    supervisor._validate_windows_resume_event(
                        authority,
                        cast(int, authenticated_record["pid"]),
                        expected_binding,
                    )
                    recovering_transition = True
                    record = transition[1]
                elif record.get("state") == SolverState.RUNNING.value:
                    metadata, started_at, authority = supervisor._validate_process_record(record)
                    expected_binding = supervisor._parse_resume_transaction_record_binding(record)
                    try:
                        transition = supervisor._read_windows_resume_transaction(
                            pending_claim,
                            expected_binding,
                        )
                    except FileNotFoundError:
                        transition = None
                    else:
                        supervisor._validate_windows_resume_transaction(
                            transition[0], transition[1], record
                        )
                        if authority is None:  # pragma: no cover - state guard
                            raise SolverOwnershipError("recorded process authority is unavailable")
                        supervisor._validate_windows_resume_event(
                            authority,
                            cast(int, record["pid"]),
                            expected_binding,
                        )
                        recovering_transition = True
                else:
                    metadata, started_at, authority = supervisor._validate_process_record(record)
            else:
                metadata, started_at, authority = supervisor._validate_process_record(record)
            if os.name == "posix":
                supervisor._acquire_reconnected_posix_input_lease(record, metadata, authority)
            supervisor._revalidate_launch_binding()
            supervisor._verify_filesystem_authority()
            if recovering_transition:
                authority.resume(cast(int, record["pid"]))
                candidate = supervisor._pending_process_record_claim
                if candidate is None:
                    raise SolverOwnershipError("process record candidate is unavailable")
                supervisor._repair_process_record_from_resume_transaction(record, candidate)
            supervisor._install_pending_process_record_claim()
            if transition is not None:
                transaction_failures = supervisor._delete_resume_transaction()
                if transaction_failures:
                    raise SolverOwnershipError(
                        "resume transaction could not be finalized"
                    ) from transaction_failures[0]
            owner_token = record.get("owner_token")
            if isinstance(owner_token, str) and owner_token:
                supervisor._record_owner_token = owner_token
            supervisor._process_record = record
            pid = cast(int, record["pid"])
            supervisor._process = _ReconnectedProcess(
                pid,
                metadata.executable_path,
                metadata.creation_identity,
                authority,
                metadata.executable_snapshot,
            )
            supervisor._process_authority = authority
            supervisor._started_at = started_at
            supervisor._state = SolverState.RUNNING
            return supervisor
        except BaseException as error:
            cleanup_failures: list[BaseException] = []
            if authority is not None:
                try:
                    authority.close()
                except BaseException as close_error:
                    cleanup_failures.append(close_error)
            supervisor._process_authority = None
            try:
                supervisor._close_filesystem_authority()
            except BaseException as close_error:
                cleanup_failures.append(close_error)
            if cleanup_failures:
                details = "; ".join(str(failure) for failure in cleanup_failures)
                raise SolverOwnershipError(f"reconnect cleanup failed: {details}") from error
            raise

    def wait(self, timeout_seconds: float | None = None) -> SolverRunResult:
        """Wait for completion, enforcing the configured timeout if present."""

        with self._lock:
            if self._result_latch is not None:
                issuance = self._result_issuance
                if issuance is None or issuance.result is not self._result_latch:
                    raise SolverOwnershipError("supervisor result latch is invalid")
                return self._result_latch
            if self._state is SolverState.NOT_STARTED:
                raise RuntimeError("solver has not been started")
            process = self._process
            configured_timeout = self.spec.timeout_seconds
        if process is None:  # pragma: no cover - defensive state guard
            raise SolverLaunchError("solver process ownership was lost")

        effective_timeout = (
            self.spec.timeout_seconds if timeout_seconds is None else timeout_seconds
        )
        if effective_timeout is not None:
            if isinstance(effective_timeout, bool) or not isinstance(
                effective_timeout, (int, float)
            ):
                raise ValueError("timeout_seconds must be a positive finite number")
            if effective_timeout <= 0 or not math.isfinite(float(effective_timeout)):
                raise ValueError("timeout_seconds must be a positive finite number")
        elif configured_timeout is not None:
            effective_timeout = configured_timeout

        try:
            return_code = process.wait(timeout=effective_timeout)
        except subprocess.TimeoutExpired:
            try:
                self._terminate_owned_process(process)
            except BaseException as error:
                self._fail_terminal_operation(error)
            try:
                self._drain_owned_descendants_after_root_exit(process)
            except BaseException as error:
                self._fail_terminal_operation(error)
            return self._complete(SolverState.TIMED_OUT, process.poll())
        try:
            self._drain_owned_descendants_after_root_exit(process)
        except BaseException as error:
            self._fail_terminal_operation(error)
        return self._complete(
            SolverState.NORMAL_EXIT if return_code == 0 else SolverState.FAILED,
            return_code,
        )

    wait_for_completion = wait

    def run(self, timeout_seconds: float | None = None) -> SolverRunResult:
        """Start once and wait for the owned process."""

        with self._lock:
            started = self._state is not SolverState.NOT_STARTED
        if not started:
            self.start()
        return self.wait(timeout_seconds)

    def cancel(self) -> SolverRunResult:
        """Cancel the owned process tree and return a cancelled result."""

        with self._lock:
            if self._result_latch is not None:
                issuance = self._result_issuance
                if issuance is None or issuance.result is not self._result_latch:
                    raise SolverOwnershipError("supervisor result latch is invalid")
                return self._result_latch
            if self._state is SolverState.NOT_STARTED:
                return self._complete(SolverState.CANCELLED, None)
            process = self._process
        if process is None:  # pragma: no cover - defensive state guard
            raise SolverLaunchError("solver process ownership was lost")
        try:
            self._terminate_owned_process(process)
        except BaseException as error:
            self._fail_terminal_operation(error)
        try:
            # Termination is a no-op when the root already exited; in every
            # case drain the exact held authority before completion validates
            # outputs or publishes a result.
            self._drain_owned_descendants_after_root_exit(process)
        except BaseException as error:
            self._fail_terminal_operation(error)
        return self._complete(SolverState.CANCELLED, process.poll())

    def _make_input_lease_record(self) -> dict[str, object]:
        lease = self._input_lease
        if lease is None or lease.handle is None or lease.object_identity is None:
            raise SolverOwnershipError("sealed POSIX input lease is unavailable")
        lease.verify(digest=True)
        device, inode, nlink, size, mtime_ns = lease.object_identity
        return {
            "fd": lease.handle,
            "sha256": lease.expected_sha256,
            "identity": {
                "device": device,
                "inode": inode,
                "nlink": nlink,
                "size": size,
                "mtime_ns": mtime_ns,
            },
        }

    def _parse_posix_input_lease_record(
        self, record: dict[str, object]
    ) -> tuple[int, str, tuple[int, int, int, int, int]]:
        value = record.get("input_lease")
        if not isinstance(value, dict):
            raise SolverOwnershipError("process record sealed input lease is invalid")
        fd = value.get("fd")
        sha256 = value.get("sha256")
        identity_value = value.get("identity")
        if (
            isinstance(fd, bool)
            or not isinstance(fd, int)
            or fd < 0
            or not isinstance(sha256, str)
            or len(sha256) != 64
            or any(character not in "0123456789abcdef" for character in sha256)
            or not isinstance(identity_value, dict)
        ):
            raise SolverOwnershipError("process record sealed input lease is invalid")
        expected_sha256, _expected_identity = self._recorded_input_binding()
        if sha256 != expected_sha256:
            raise SolverOwnershipError("process record sealed input digest does not match")
        identity: list[int] = []
        for name in ("device", "inode", "nlink", "size", "mtime_ns"):
            item = identity_value.get(name)
            if isinstance(item, bool) or not isinstance(item, int):
                raise SolverOwnershipError("process record sealed input identity is invalid")
            identity.append(item)
        if any(value < 0 for value in identity) or identity[2] != 0:
            raise SolverOwnershipError("process record sealed input identity is invalid")
        return fd, sha256, cast(tuple[int, int, int, int, int], tuple(identity))

    def _resume_transaction_path(self) -> Path:
        return self._path_for_io(self.spec.attempt_root / _WINDOWS_RESUME_TRANSACTION_NAME)

    def _resume_transaction_record_binding(self) -> dict[str, object]:
        """Return the pre-authenticated journal/event binding for a new record."""

        claim = self._resume_transaction_claim
        if (
            claim is None
            or claim.handle is None
            or claim.native_handle is None
            or not claim.event_name
            or not claim.event_token
            or claim.inherited_child_handle is None
        ):
            raise SolverOwnershipError("resume transaction authority is unavailable")
        parts = _windows_resume_event_parts(
            claim.event_name,
            self.spec.attempt_root,
            self._launch_context_digest,
            claim.device,
            claim.inode,
        )
        if parts != (claim.device, claim.inode, claim.event_token):
            raise SolverOwnershipError("resume event binding is invalid")
        return {
            "journal_identity": {
                "device": claim.device,
                "inode": claim.inode,
            },
            "event_name": claim.event_name,
            "event_token": claim.event_token,
            "child_handle": claim.inherited_child_handle,
        }

    def _parse_resume_transaction_record_binding(
        self, record: dict[str, object]
    ) -> tuple[int, int, str, str, int]:
        """Validate the journal/event binding carried by an authenticated record."""

        value = record.get("resume_transaction")
        if not isinstance(value, dict):
            raise SolverOwnershipError("process record resume transaction binding is invalid")
        identity = value.get("journal_identity")
        if not isinstance(identity, dict):
            raise SolverOwnershipError("process record journal identity is invalid")
        device = identity.get("device")
        inode = identity.get("inode")
        if (
            isinstance(device, bool)
            or not isinstance(device, int)
            or device < 0
            or isinstance(inode, bool)
            or not isinstance(inode, int)
            or inode < 0
        ):
            raise SolverOwnershipError("process record journal identity is invalid")
        event_name = value.get("event_name")
        event_token = value.get("event_token")
        child_handle = value.get("child_handle")
        if (
            not isinstance(event_name, str)
            or not isinstance(event_token, str)
            or re.fullmatch(r"[0-9a-f]{32}", event_token) is None
            or isinstance(child_handle, bool)
            or not isinstance(child_handle, int)
            or child_handle <= 0
        ):
            raise SolverOwnershipError("process record resume event binding is invalid")
        parts = _windows_resume_event_parts(
            event_name,
            self.spec.attempt_root,
            self._launch_context_digest,
            device,
            inode,
        )
        if parts != (device, inode, event_token):
            raise SolverOwnershipError("process record resume event binding is invalid")
        return device, inode, event_name, event_token, child_handle

    @staticmethod
    def _record_content(record: dict[str, object]) -> bytes:
        try:
            return json.dumps(record, indent=2, sort_keys=True).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise SolverOwnershipError("process record content is not serializable") from error

    def _own_resume_transaction_claim(
        self, handle: int, path: Path, content: bytes
    ) -> _WindowsResumeTransactionClaim:
        native_handle = getattr(handle, "native_handle", None)
        if _NATIVE_WINDOWS and native_handle is None:
            raise SolverOwnershipError("resume transaction has no exact native guard")
        metadata = os.fstat(handle)
        if not stat.S_ISREG(metadata.st_mode):
            raise SolverOwnershipError("resume transaction is not a regular file")
        return _WindowsResumeTransactionClaim(
            path=path,
            content=content,
            device=int(metadata.st_dev),
            inode=int(metadata.st_ino),
            handle=int(handle),
            native_handle=native_handle,
        )

    def _reserve_windows_resume_transaction(self) -> None:
        """Pre-create the exact journal and its crash-surviving resume event."""

        if not _NATIVE_WINDOWS:
            return
        if self._resume_transaction_claim is not None:
            raise SolverOwnershipError("resume transaction is already owned")
        path = self._resume_transaction_path()
        handle: int | None = None
        try:
            handle = _windows_open_process_record_handle(
                path,
                create=True,
                desired_access=_WINDOWS_GENERIC_READ | _WINDOWS_GENERIC_WRITE | _WINDOWS_DELETE,
                share_mode=_WINDOWS_FILE_SHARE_READ,
                descriptor_flags=os.O_RDWR,
            )
            claim = self._own_resume_transaction_claim(handle, path, b"")
            self._resume_transaction_claim = claim
            token = uuid.uuid4().hex
            claim.event_name = _windows_resume_event_name(
                self.spec.attempt_root,
                self._launch_context_digest,
                claim.device,
                claim.inode,
                token,
            )
            claim.event_token = token
            claim.event_handle = _windows_create_resume_event(claim.event_name)
            claim.child_handle = _windows_duplicate_resume_event_handle(claim.event_handle)
            claim.inherited_child_handle = claim.child_handle
        except BaseException:
            if self._resume_transaction_claim is None and handle is not None:
                temporary_claim = _WindowsResumeTransactionClaim(
                    path=path,
                    content=b"",
                    device=0,
                    inode=0,
                    handle=int(handle),
                    native_handle=getattr(handle, "native_handle", None),
                )
                with contextlib.suppress(BaseException):
                    _windows_close_owned_fd(temporary_claim, "handle", "native_handle")
            raise

    def _resume_transaction_identity_matches(self, claim: _WindowsResumeTransactionClaim) -> bool:
        """Verify both the held journal object and its current directory entry."""

        if claim.handle is None or claim.native_handle is None:
            return False
        exact_match = _windows_fd_identity_matches(claim.handle, claim.native_handle)
        if exact_match is not True:
            return False
        try:
            metadata = os.fstat(claim.handle)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or int(metadata.st_dev) != claim.device
                or int(metadata.st_ino) != claim.inode
            ):
                return False
            probe = _windows_probe_process_record(claim.path)
        except FileNotFoundError:
            return False
        try:
            return _same_file_identity(metadata, os.fstat(probe))
        finally:
            with contextlib.suppress(BaseException):
                _close_windows_probe(probe)

    def _clear_resume_transaction_child_handle(self) -> None:
        claim = self._resume_transaction_claim
        if claim is None or claim.child_handle is None:
            return
        child_handle = claim.child_handle
        if claim.inherited_child_handle is None:
            claim.inherited_child_handle = child_handle
        try:
            _windows_close_native_handle(child_handle)
        except BaseException:
            raise
        claim.child_handle = None

    def _prepare_windows_resume_transaction(
        self,
        bound_record: dict[str, object],
        running_record: dict[str, object],
    ) -> None:
        """Durably journal the exact record to publish after a native resume."""

        if not _NATIVE_WINDOWS:
            return
        claim = self._resume_transaction_claim
        if claim is None:
            self._reserve_windows_resume_transaction()
            claim = self._resume_transaction_claim
        if (
            claim is None
            or claim.handle is None
            or claim.event_handle is None
            or not claim.event_name
            or not claim.event_token
            or claim.inherited_child_handle is None
        ):
            raise SolverOwnershipError("resume transaction authority is unavailable")
        process_claim = self._process_record_claim
        if process_claim is None or process_claim.handle is None:
            raise SolverOwnershipError("process record handle is unavailable")
        if not self._record_claim_matches(process_claim):
            raise SolverOwnershipError("owned process record was replaced")
        expected_binding = (
            claim.device,
            claim.inode,
            claim.event_name,
            claim.event_token,
            claim.inherited_child_handle,
        )
        if (
            self._parse_resume_transaction_record_binding(bound_record) != expected_binding
            or self._parse_resume_transaction_record_binding(running_record) != expected_binding
        ):
            raise SolverOwnershipError("resume transaction record binding is invalid")
        transaction: dict[str, object] = {
            "version": _WINDOWS_RESUME_TRANSACTION_VERSION,
            "kind": "windows-resume-transaction",
            "event_name": claim.event_name,
            "event_token": claim.event_token,
            "child_handle": claim.inherited_child_handle,
            "journal_identity": {
                "device": claim.device,
                "inode": claim.inode,
            },
            "record_path": os.fspath(self.process_record_path),
            "record_identity": {
                "device": process_claim.device,
                "inode": process_claim.inode,
            },
            "bound_digest": _json_digest(bound_record),
            "bound_record": bound_record,
            "running_digest": _json_digest(running_record),
            "running_record": running_record,
        }
        content = self._record_content(transaction)
        transaction_handle = claim.handle
        _write_record_fd(transaction_handle, content)
        if _read_record_fd(transaction_handle) != content:
            raise SolverOwnershipError("resume transaction changed during publication")
        claim.content = content
        if not self._resume_transaction_identity_matches(claim):
            raise SolverOwnershipError("resume transaction directory entry changed")
        if not self._record_claim_matches(process_claim):
            raise SolverOwnershipError("owned process record changed before resume")
        self._verify_filesystem_authority()

    def _signal_windows_resume_transaction(self) -> None:
        """Publish the crash transition only after both exact file claims pass."""

        if not _NATIVE_WINDOWS:
            return
        claim = self._resume_transaction_claim
        process_claim = self._process_record_claim
        if (
            claim is None
            or claim.event_handle is None
            or not claim.event_name
            or process_claim is None
        ):
            raise SolverOwnershipError("resume transaction authority is unavailable")
        if not self._resume_transaction_identity_matches(claim):
            raise SolverOwnershipError("resume transaction directory entry changed")
        if not _windows_resume_event_binding(
            claim.event_name,
            self.spec.attempt_root,
            self._launch_context_digest,
            claim.device,
            claim.inode,
        ):
            raise SolverOwnershipError("resume event binding is invalid")
        if not self._record_claim_matches(process_claim):
            raise SolverOwnershipError("owned process record changed before resume")
        process_handle = process_claim.handle
        if process_handle is None:  # pragma: no cover - state guard
            raise SolverOwnershipError("process record handle is unavailable")
        try:
            current_record = json.loads(_read_record_fd(process_handle).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError, ValueError) as error:
            raise SolverOwnershipError("owned process record is invalid before resume") from error
        if (
            not isinstance(current_record, dict)
            or current_record.get("state") != "BOUND_SUSPENDED"
            or self._parse_resume_transaction_record_binding(current_record)
            != (
                claim.device,
                claim.inode,
                claim.event_name,
                claim.event_token,
                claim.inherited_child_handle,
            )
        ):
            raise SolverOwnershipError("owned process record binding changed before resume")
        self._verify_filesystem_authority()
        _windows_signal_resume_event(claim.event_handle)

    def _parse_windows_resume_transaction(
        self,
        content: bytes,
        process_claim: _ProcessRecordClaim,
        expected_binding: tuple[int, int, str, str, int],
    ) -> tuple[dict[str, object], dict[str, object]]:
        try:
            value = json.loads(content.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError, ValueError) as error:
            raise SolverOwnershipError("resume transaction is torn or invalid") from error
        if not isinstance(value, dict):
            raise SolverOwnershipError("resume transaction is invalid")
        version = value.get("version")
        if (
            isinstance(version, bool)
            or not isinstance(version, int)
            or version != _WINDOWS_RESUME_TRANSACTION_VERSION
            or value.get("kind") != "windows-resume-transaction"
        ):
            raise SolverOwnershipError("resume transaction version is invalid")
        transaction_claim = self._resume_transaction_claim
        if transaction_claim is None:
            raise SolverOwnershipError("resume transaction authority is unavailable")
        (
            expected_device,
            expected_inode,
            expected_event_name,
            expected_event_token,
            expected_child_handle,
        ) = expected_binding
        if (transaction_claim.device, transaction_claim.inode) != (
            expected_device,
            expected_inode,
        ):
            raise SolverOwnershipError("resume transaction journal identity changed")
        event_name = value.get("event_name")
        event_token = value.get("event_token")
        if event_name != expected_event_name or event_token != expected_event_token:
            raise SolverOwnershipError("resume event binding does not match process record")
        if _windows_resume_event_parts(
            event_name,
            self.spec.attempt_root,
            self._launch_context_digest,
            expected_device,
            expected_inode,
        ) != expected_binding[:2] + (expected_event_token,):
            raise SolverOwnershipError("resume event binding is invalid")
        journal_identity = value.get("journal_identity")
        if not isinstance(journal_identity, dict):
            raise SolverOwnershipError("resume transaction journal identity is invalid")
        journal_device = journal_identity.get("device")
        journal_inode = journal_identity.get("inode")
        if (
            isinstance(journal_device, bool)
            or not isinstance(journal_device, int)
            or journal_device < 0
            or isinstance(journal_inode, bool)
            or not isinstance(journal_inode, int)
            or journal_inode < 0
            or (journal_device, journal_inode) != (expected_device, expected_inode)
        ):
            raise SolverOwnershipError("resume transaction journal identity changed")
        if not isinstance(event_name, str) or not isinstance(event_token, str):
            raise SolverOwnershipError("resume event binding is invalid")
        child_handle = value.get("child_handle")
        if (
            isinstance(child_handle, bool)
            or not isinstance(child_handle, int)
            or child_handle <= 0
            or child_handle != expected_child_handle
        ):
            raise SolverOwnershipError("resume transaction child handle binding is invalid")
        record_path = value.get("record_path")
        if (
            not isinstance(record_path, str)
            or not Path(record_path).is_absolute()
            or os.path.normcase(os.path.abspath(record_path))
            != os.path.normcase(os.path.abspath(os.fspath(self.process_record_path)))
        ):
            raise SolverOwnershipError("resume transaction record binding is invalid")
        record_identity = value.get("record_identity")
        if not isinstance(record_identity, dict):
            raise SolverOwnershipError("resume transaction record identity is invalid")
        device = record_identity.get("device")
        inode = record_identity.get("inode")
        if (
            isinstance(device, bool)
            or not isinstance(device, int)
            or device < 0
            or isinstance(inode, bool)
            or not isinstance(inode, int)
            or inode < 0
        ):
            raise SolverOwnershipError("resume transaction record identity is invalid")
        if device != process_claim.device or inode != process_claim.inode:
            raise SolverOwnershipError("resume transaction process record identity changed")
        bound_record = value.get("bound_record")
        running_record = value.get("running_record")
        if not isinstance(bound_record, dict) or not isinstance(running_record, dict):
            raise SolverOwnershipError("resume transaction records are invalid")
        if bound_record.get("state") != "BOUND_SUSPENDED":
            raise SolverOwnershipError("resume transaction bound state is invalid")
        if running_record.get("state") != SolverState.RUNNING.value:
            raise SolverOwnershipError("resume transaction running state is invalid")
        if (
            self._parse_resume_transaction_record_binding(bound_record) != expected_binding
            or self._parse_resume_transaction_record_binding(running_record) != expected_binding
        ):
            raise SolverOwnershipError("resume transaction record binding is invalid")
        try:
            bound_digest = _json_digest(bound_record)
            running_digest = _json_digest(running_record)
        except SolverConfigurationError as error:
            raise SolverOwnershipError("resume transaction records are invalid") from error
        if (
            value.get("bound_digest") != bound_digest
            or value.get("running_digest") != running_digest
        ):
            raise SolverOwnershipError("resume transaction digest is invalid")
        equivalent_bound = dict(running_record)
        equivalent_bound["state"] = "BOUND_SUSPENDED"
        if equivalent_bound != bound_record:
            raise SolverOwnershipError("resume transaction record transition is invalid")
        return bound_record, running_record

    def _validate_windows_resume_event(
        self,
        authority: ProcessAuthority,
        pid: int,
        expected_binding: tuple[int, int, str, str, int],
    ) -> None:
        claim = self._resume_transaction_claim
        if claim is None or claim.event_handle is None:
            raise SolverOwnershipError("resume event authority is unavailable")
        try:
            authority.validate_inherited_event(pid, expected_binding[4], claim.event_handle)
        except ProcessAuthorityError as error:
            raise SolverOwnershipError(
                "resume event does not match the attested child handle"
            ) from error

    def _read_windows_resume_transaction(
        self,
        process_claim: _ProcessRecordClaim,
        expected_binding: tuple[int, int, str, str, int],
    ) -> tuple[dict[str, object], dict[str, object]]:
        if not _NATIVE_WINDOWS:
            raise SolverOwnershipError("Windows resume transaction is unsupported")
        if self._resume_transaction_claim is not None:
            raise SolverOwnershipError("resume transaction is already owned")
        path = self._resume_transaction_path()
        handle: int | None = None
        try:
            handle = _windows_open_process_record_handle(
                path,
                create=False,
                # Reconnect must be able to delete only this exact journal
                # handle after recovery, without acquiring write authority.
                desired_access=_WINDOWS_GENERIC_READ | _WINDOWS_DELETE,
                share_mode=(
                    _WINDOWS_FILE_SHARE_READ
                    | _WINDOWS_FILE_SHARE_WRITE
                    | _WINDOWS_FILE_SHARE_DELETE
                ),
                descriptor_flags=os.O_RDONLY,
            )
            claim = self._own_resume_transaction_claim(handle, path, b"")
            self._resume_transaction_claim = claim
            if (claim.device, claim.inode) != expected_binding[:2]:
                raise SolverOwnershipError("resume transaction journal identity changed")
            if not self._record_claim_matches(process_claim):
                raise SolverOwnershipError("owned process record changed before recovery")
            if not self._resume_transaction_identity_matches(claim):
                raise SolverOwnershipError("resume transaction directory entry changed")
            transaction_handle = claim.handle
            if transaction_handle is None:  # pragma: no cover - state guard
                raise SolverOwnershipError("resume transaction handle is unavailable")
            content = _read_record_fd(transaction_handle)
            claim.content = content
            parsed = self._parse_windows_resume_transaction(
                content,
                process_claim,
                expected_binding,
            )
            if not self._resume_transaction_identity_matches(claim):
                raise SolverOwnershipError("resume transaction directory entry changed")
            if not self._record_claim_matches(process_claim):
                raise SolverOwnershipError("owned process record changed during recovery")
            claim.event_name = expected_binding[2]
            claim.event_token = expected_binding[3]
            claim.inherited_child_handle = expected_binding[4]
            claim.event_handle = _windows_open_resume_event(expected_binding[2])
            return parsed
        except BaseException:
            failures = self._release_resume_transaction_claim()
            if failures:
                raise SolverOwnershipError(
                    "resume transaction handle could not be closed"
                ) from failures[0]
            raise

    def _validate_windows_resume_transaction(
        self,
        bound_record: dict[str, object],
        running_record: dict[str, object],
        current_record: dict[str, object] | None,
    ) -> None:
        if current_record is None:
            return
        if current_record.get("state") == "BOUND_SUSPENDED":
            expected = bound_record
        elif current_record.get("state") == SolverState.RUNNING.value:
            expected = running_record
        else:
            raise SolverOwnershipError("process record transition state is invalid")
        if current_record != expected:
            raise SolverOwnershipError("process record transition does not match its journal")
        expected_binding = self._parse_resume_transaction_record_binding(current_record)
        if (
            self._parse_resume_transaction_record_binding(bound_record) != expected_binding
            or self._parse_resume_transaction_record_binding(running_record) != expected_binding
        ):
            raise SolverOwnershipError("process record transition binding does not match")

    def _repair_process_record_from_resume_transaction(
        self,
        running_record: dict[str, object],
        candidate: _ProcessRecordClaim,
    ) -> None:
        if candidate.handle is None or not self._record_claim_identity_matches(candidate):
            raise SolverOwnershipError("owned process record binding changed during recovery")
        record_handle = candidate.handle
        content = self._record_content(running_record)
        _write_record_fd(record_handle, content)
        if _read_record_fd(record_handle) != content:
            raise SolverOwnershipError("process record changed during recovery")
        candidate.content = content
        candidate.state = SolverState.RUNNING.value

    def _make_process_record(
        self,
        pid: int,
        metadata: _ProcessMetadata,
        started_at: datetime,
        authority: ProcessAuthority,
        *,
        state: str,
    ) -> dict[str, object]:
        outputs = self.spec.expected_outputs
        claim = authority.claim
        if claim.get("context_digest") != self._launch_context_digest:
            raise ProcessAuthorityError("process authority context binding is invalid")
        if claim.get("root_pid") != pid:
            raise ProcessAuthorityError("process authority root PID binding is invalid")
        if claim.get("root_creation_identity") != metadata.creation_identity:
            raise ProcessAuthorityError(
                "process authority root creation identity binding is invalid"
            )
        if os.name == "nt":
            thread_id = claim.get("root_thread_id")
            thread_identity = claim.get("root_thread_creation_identity")
            if (
                isinstance(thread_id, bool)
                or not isinstance(thread_id, int)
                or thread_id <= 0
                or not isinstance(thread_identity, str)
                or not thread_identity.startswith("windows:")
            ):
                raise ProcessAuthorityError("process authority primary thread binding is invalid")
        executable_snapshot = self._runtime_image_snapshot
        if executable_snapshot is None:
            raise ProcessAuthorityError("runtime executable image binding is unavailable")
        if executable_snapshot.sha256 != self._launch_context.get("runtime_sha256"):
            raise ProcessAuthorityError("runtime executable image digest binding is invalid")
        if os.name == "posix" and executable_snapshot.nlink != 0:
            raise ProcessAuthorityError("runtime executable image is not anonymous")
        record: dict[str, object] = {
            "case_id": self._case_id,
            "intent_id": self._intent_id,
            "attempt_id": self._attempt_id,
            "state": state,
            "owner_token": self._record_owner_token,
            "executable_path": str(self.spec.executable),
            "executable_image": {
                "sha256": executable_snapshot.sha256,
                "identity": {
                    "device": executable_snapshot.device,
                    "inode": executable_snapshot.inode,
                    "nlink": executable_snapshot.nlink,
                    "size": executable_snapshot.size,
                },
            },
            "pid": pid,
            "process_creation_identity": metadata.creation_identity,
            "start_time": started_at.isoformat(),
            "owned_output_paths": {
                "log": str(outputs.log_path),
                "xplt": str(outputs.xplt_path),
            },
            "filesystem_authority": self._record_filesystem_identities(),
            "launch_context": self._launch_context,
            "launch_context_digest": self._launch_context_digest,
            "process_authority": claim,
        }
        if _NATIVE_WINDOWS:
            # The record is the authenticated source of truth for the exact
            # journal object and event.  Reconnect must not learn either
            # identity from a path-opened replacement journal.
            record["resume_transaction"] = self._resume_transaction_record_binding()
        if os.name == "posix":
            record["input_lease"] = self._make_input_lease_record()
        return record

    def _own_process_record_candidate(
        self,
        path: Path,
        *,
        handle: int,
        parent_fd: int | None,
    ) -> _ProcessRecordClaim:
        """Retain a newly opened record descriptor before fallible use."""

        path = Path(os.fspath(path))
        handle_value = int(handle)
        native_handle = getattr(handle, "native_handle", None)
        parent_native_handle = getattr(parent_fd, "native_handle", None)
        candidate = _ProcessRecordClaim(
            path=path,
            content=b"",
            device=0,
            inode=0,
            state=None,
            handle=handle_value,
            parent_fd=None if parent_fd is None else int(parent_fd),
            name=self.process_record_path.name,
            native_handle=native_handle,
            parent_native_handle=parent_native_handle,
        )
        self._process_record_candidates.append(candidate)
        try:
            if _NATIVE_WINDOWS and native_handle is None:
                raise SolverOwnershipError("process record descriptor has no exact native guard")
            metadata = os.fstat(handle_value)
            if not stat.S_ISREG(metadata.st_mode):
                raise OSError("process record is not a regular file")
        except BaseException:
            failures = self._release_process_record_claim(candidate)
            if failures:
                raise SolverOwnershipError(
                    "process record acquisition cleanup failed"
                ) from failures[0]
            raise
        candidate.device = int(metadata.st_dev)
        candidate.inode = int(metadata.st_ino)
        if _NATIVE_WINDOWS:
            _windows_register_record_claim(
                path,
                handle_value,
                candidate.device,
                candidate.inode,
                native_handle=native_handle,
                owner=candidate,
            )
        return candidate

    @staticmethod
    def _update_process_record_candidate(
        candidate: _ProcessRecordClaim,
        content: bytes,
        record: dict[str, object],
    ) -> None:
        candidate.content = content
        record_state = record.get("state")
        candidate.state = record_state if isinstance(record_state, str) else None

    def _install_process_record_candidate(self, candidate: _ProcessRecordClaim) -> None:
        if not any(owned is candidate for owned in self._process_record_candidates):
            raise SolverOwnershipError("process record candidate is not owned")
        if candidate.handle is None or not self._record_claim_matches(candidate):
            raise SolverOwnershipError("process record candidate changed before install")
        previous_claim = self._process_record_claim
        if previous_claim is not None and previous_claim is not candidate:
            release_failures = self._release_process_record_claim(previous_claim)
            if (
                release_failures
                or previous_claim.handle is not None
                or previous_claim.parent_fd is not None
                or previous_claim.native_handle is not None
                or previous_claim.parent_native_handle is not None
            ):
                raise SolverOwnershipError(
                    "previous process record handle could not be closed"
                ) from (release_failures[0] if release_failures else None)
            self._process_record_claim = None
        self._process_record_claim = candidate
        self._process_record_candidates[:] = [
            owned for owned in self._process_record_candidates if owned is not candidate
        ]
        if self._pending_process_record_claim is candidate:
            self._pending_process_record_claim = None

    def _install_pending_process_record_claim(self) -> None:
        candidate = self._pending_process_record_claim
        if candidate is None:
            raise SolverOwnershipError("process record candidate is unavailable")
        self._install_process_record_candidate(candidate)

    def _set_process_record_claim(
        self,
        path: Path,
        content: bytes,
        record: dict[str, object],
        *,
        handle: int,
        parent_fd: int | None,
    ) -> None:
        candidate = self._own_process_record_candidate(
            path,
            handle=handle,
            parent_fd=parent_fd,
        )
        self._update_process_record_candidate(candidate, content, record)
        self._install_process_record_candidate(candidate)

    def _read_process_record_text(
        self,
        *,
        encoding: str | None,
        errors: str | None,
    ) -> str:
        selected_encoding = io.text_encoding(encoding)
        selected_errors = "strict" if errors is None else errors
        claim = self._process_record_claim
        if claim is None or claim.handle is None:
            return Path(self._path_for_io(self.process_record_path)).read_text(
                encoding=selected_encoding,
                errors=selected_errors,
            )
        probe_fd = _windows_probe_process_record(claim.path)
        try:
            if claim.native_handle is None:
                raise SolverOwnershipError("owned process record has no exact native guard")
            exact_match = _windows_fd_identity_matches(claim.handle, claim.native_handle)
            if exact_match is None:
                raise SolverOwnershipError("unable to verify Windows CRT descriptor identity")
            if not exact_match:
                claim.handle = None
                raise SolverOwnershipError("owned process record was replaced")
            if not _same_file_identity(
                os.fstat(claim.handle),
                os.fstat(probe_fd),
            ):
                raise SolverOwnershipError("owned process record was replaced")
            content = _read_record_fd(probe_fd)
        finally:
            _close_windows_probe(probe_fd)
        return content.decode(selected_encoding, selected_errors)

    def _write_process_record_text(
        self,
        data: str,
        *,
        encoding: str | None,
        errors: str | None,
    ) -> int:
        selected_encoding = io.text_encoding(encoding)
        selected_errors = "strict" if errors is None else errors
        content = data.encode(selected_encoding, selected_errors)
        claim = self._process_record_claim
        if claim is None or claim.handle is None:
            raise SolverOwnershipError("process record handle is unavailable")
        if not self._record_claim_matches(claim):
            raise SolverOwnershipError("owned process record was replaced")
        _write_record_fd(claim.handle, content)
        if not self._record_claim_matches(claim, content=content):
            raise SolverOwnershipError("owned process record changed during update")
        claim.content = content
        return len(data)

    def _unlink_process_record_path(self, *, missing_ok: bool) -> None:
        claim = self._process_record_claim
        if claim is None or claim.handle is None:
            if missing_ok and not self.process_record_path.exists():
                return
            raise SolverOwnershipError("process record handle is unavailable")
        try:
            _windows_delete_process_record(claim.handle)
        except FileNotFoundError:
            if not missing_ok:
                raise

    def _write_process_record(self, record: dict[str, object]) -> None:
        self._verify_filesystem_authority()
        path = self._path_for_io(self.process_record_path)
        temporary = path.with_name(f".{path.name}.{self._owner_token}.tmp")
        try:
            content = json.dumps(record, indent=2, sort_keys=True).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise OSError(f"unable to persist process record: {error}") from error
        claim = self._process_record_claim
        try:
            if claim is not None and claim.handle is not None:
                if _NATIVE_WINDOWS:
                    if not self._record_claim_matches(claim):
                        raise SolverOwnershipError("owned process record was replaced")
                    _write_record_fd(claim.handle, content)
                    if not self._record_claim_matches(claim, content=content):
                        raise SolverOwnershipError("owned process record changed during update")
                else:
                    parent_fd = claim.parent_fd
                    if parent_fd is None:
                        raise SolverOwnershipError(
                            "process record transaction parent is unavailable"
                        )
                    with _posix_record_lock(parent_fd):
                        if not self._record_claim_identity_matches(claim):
                            raise SolverOwnershipError("owned process record was replaced")
                        _write_record_fd(claim.handle, content)
                record_state = record.get("state")
                claim.content = content
                claim.state = record_state if isinstance(record_state, str) else None
                return
            if os.name == "posix":
                authority = self._filesystem_authority
                if authority is None:
                    raise SolverOwnershipError("filesystem authority is unavailable")
                with authority.open_directory(
                    self.process_record_path.parent, create=True
                ) as parent_fd:
                    temp_fd: int | None = None
                    candidate: _ProcessRecordClaim | None = None
                    flags = (
                        os.O_WRONLY
                        | os.O_CREAT
                        | os.O_EXCL
                        | getattr(os, "O_CLOEXEC", 0)
                        | getattr(os, "O_NOFOLLOW", 0)
                    )
                    with _posix_record_lock(parent_fd):
                        temp_fd = os.open(temporary.name, flags, 0o600, dir_fd=parent_fd)
                        candidate = self._own_process_record_candidate(
                            path,
                            handle=temp_fd,
                            parent_fd=None,
                        )
                        record_state = record.get("state")
                        candidate.state = record_state if isinstance(record_state, str) else None
                        _write_record_fd(temp_fd, content)
                        candidate.parent_fd = os.dup(parent_fd)
                        # A create-new hard link publishes the record without
                        # replacing a foreign file that won the name race.
                        _posix_link_fd(temp_fd, parent_fd, path.name)
                        _posix_unlink(parent_fd, temporary.name)
                        os.fsync(parent_fd)
                        self._update_process_record_candidate(candidate, content, record)
                        self._install_process_record_candidate(candidate)
            elif _NATIVE_WINDOWS:
                record_fd = _windows_open_process_record(path, create=True)
                candidate = self._own_process_record_candidate(
                    path,
                    handle=record_fd,
                    parent_fd=None,
                )
                record_state = record.get("state")
                candidate.state = record_state if isinstance(record_state, str) else None
                _write_record_fd(record_fd, content)
                self._update_process_record_candidate(candidate, content, record)
                self._install_process_record_candidate(candidate)
                claim = self._process_record_claim
                if claim is None or not self._record_claim_matches(claim):
                    raise SolverOwnershipError("owned process record changed during create")
            else:
                with temporary.open("x", encoding="utf-8", newline="") as stream:
                    stream.write(content.decode("utf-8"))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(os.fspath(temporary), os.fspath(path))
                record_fd = os.open(os.fspath(path), os.O_RDWR)
                candidate = self._own_process_record_candidate(
                    path,
                    handle=record_fd,
                    parent_fd=None,
                )
                self._update_process_record_candidate(candidate, content, record)
                self._install_process_record_candidate(candidate)
        except (OSError, SolverOwnershipError, TypeError, ValueError) as error:
            if os.name == "posix":
                with contextlib.suppress(OSError):
                    authority = self._filesystem_authority
                    if authority is not None:
                        with authority.open_directory(
                            self.process_record_path.parent, create=False
                        ) as parent_fd:
                            _posix_unlink(parent_fd, temporary.name)
            else:
                with contextlib.suppress(OSError):
                    temporary.unlink(missing_ok=True)
            if isinstance(error, SolverOwnershipError):
                raise
            raise OSError(f"unable to persist process record: {error}") from error

    def _read_process_record(self) -> dict[str, object]:
        self._verify_filesystem_authority()
        path = self._path_for_io(self.process_record_path)
        active_claim = self._process_record_claim
        if _NATIVE_WINDOWS and active_claim is not None and active_claim.handle is not None:
            if not self._record_claim_matches(active_claim):
                raise SolverOwnershipError("owned process record changed before read")
            content = _read_record_fd(active_claim.handle)
            try:
                record = json.loads(content.decode("utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
                raise SolverOwnershipError(f"invalid process record: {path}") from error
            if not isinstance(record, dict):
                raise SolverOwnershipError("process record must be a JSON object")
            if not self._record_claim_matches(active_claim, content=content):
                raise SolverOwnershipError("process record changed during read")
            self._verify_filesystem_authority()
            return record

        record_fd: int | None = None
        candidate: _ProcessRecordClaim | None = None
        try:
            if os.name == "posix":
                authority = self._filesystem_authority
                if authority is None:
                    raise SolverOwnershipError("filesystem authority is unavailable")
                with authority.open_directory(self.process_record_path.parent) as opened_parent:
                    record_fd = os.open(
                        path.name,
                        os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=opened_parent,
                    )
                    candidate = self._own_process_record_candidate(
                        path,
                        handle=record_fd,
                        parent_fd=None,
                    )
                    candidate.parent_fd = os.dup(opened_parent)
                    content = _read_record_fd(record_fd)
            elif _NATIVE_WINDOWS:
                record_fd = _windows_open_process_record(path, create=False)
                candidate = self._own_process_record_candidate(
                    path,
                    handle=record_fd,
                    parent_fd=None,
                )
                content = _read_record_fd(record_fd)
            else:
                record_fd = os.open(os.fspath(path), os.O_RDWR)
                candidate = self._own_process_record_candidate(
                    path,
                    handle=record_fd,
                    parent_fd=None,
                )
                content = _read_record_fd(record_fd)
            if candidate is None:  # pragma: no cover - defensive state guard
                raise SolverOwnershipError("process record candidate is unavailable")
            candidate.content = content
            record = json.loads(content.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
            release_failures = self._release_process_record_claim(candidate)
            if release_failures:
                raise SolverOwnershipError(
                    "process record candidate handles could not be closed"
                ) from release_failures[0]
            raise SolverOwnershipError(f"invalid process record: {path}") from error
        if not isinstance(record, dict):
            release_failures = self._release_process_record_claim(candidate)
            if release_failures:
                raise SolverOwnershipError(
                    "process record candidate handles could not be closed"
                ) from release_failures[0]
            raise SolverOwnershipError("process record must be a JSON object")
        try:
            self._verify_filesystem_authority()
            if candidate is None or candidate.handle is None:
                raise SolverOwnershipError("process record handle is unavailable")
            self._update_process_record_candidate(candidate, content, record)
            if not self._record_claim_matches(candidate, content=content):
                raise SolverOwnershipError("process record changed during read")
            self._pending_process_record_claim = candidate
        except BaseException:
            release_failures = self._release_process_record_claim(candidate)
            if release_failures:
                raise SolverOwnershipError(
                    "process record candidate handles could not be closed"
                ) from release_failures[0]
            raise
        return record

    def _validate_record_path(self, value: object, expected: Path, label: str) -> None:
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise SolverOwnershipError(f"{label} must be an absolute path")
        candidate = Path(os.path.abspath(value))
        root = self.spec.attempt_root
        if (
            candidate != expected
            or not candidate.is_relative_to(root)
            or not Path(os.path.realpath(candidate)).is_relative_to(root)
        ):
            raise SolverOwnershipError(f"{label} escapes the attempt root")

    def _parse_executable_image_record(self, record: dict[str, object]) -> _FileSnapshot:
        value = record.get("executable_image")
        if not isinstance(value, dict):
            raise SolverOwnershipError("process record executable image is invalid")
        sha256 = value.get("sha256")
        identity_value = value.get("identity")
        expected_sha256 = self._launch_context.get("runtime_sha256")
        if (
            not isinstance(sha256, str)
            or len(sha256) != 64
            or any(character not in "0123456789abcdef" for character in sha256)
            or sha256 != expected_sha256
            or not isinstance(identity_value, dict)
        ):
            raise SolverOwnershipError("process record executable image is invalid")
        identity: list[int] = []
        for name in ("device", "inode", "nlink", "size"):
            item = identity_value.get(name)
            if isinstance(item, bool) or not isinstance(item, int) or item < 0:
                raise SolverOwnershipError("process record executable image identity is invalid")
            identity.append(item)
        if os.name == "posix" and identity[2] != 0:
            raise SolverOwnershipError("process record executable image identity is not anonymous")
        return _FileSnapshot(
            device=identity[0],
            inode=identity[1],
            nlink=identity[2],
            size=identity[3],
            sha256=sha256,
        )

    def _acquire_reconnected_posix_input_lease(
        self,
        record: dict[str, object],
        metadata: _ProcessMetadata,
        authority: ProcessAuthority,
    ) -> None:
        recorded_fd, expected_sha256, object_identity = self._parse_posix_input_lease_record(record)
        pid = record.get("pid")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise SolverOwnershipError("process record PID is invalid")
        proc_root = Path("/proc") / str(pid)
        proc_fd_directory = proc_root / "fd"
        if not proc_fd_directory.is_dir():
            raise SolverOwnershipError("POSIX child descriptor path is unavailable")
        try:
            authority.verify(pid)
        except ProcessAuthorityError as error:
            raise SolverOwnershipError("reconnect process authority is no longer valid") from error

        child_fd_path = proc_fd_directory / str(recorded_fd)
        try:
            handle = os.open(
                os.fspath(child_fd_path),
                os.O_RDONLY | getattr(os, "O_CLOEXEC", 0),
            )
        except OSError as error:
            raise SolverOwnershipError(
                "recorded POSIX child input descriptor is unavailable"
            ) from error
        _expected_source_sha256, expected_source_identity = self._recorded_input_binding()
        lease = _InputLease(
            path=self.spec.input_path,
            expected_sha256=expected_sha256,
            expected_identity=expected_source_identity,
            handle=handle,
            object_identity=object_identity,
        )
        try:
            try:
                authority.verify(pid)
                current = _process_metadata(pid)
            except (OSError, ProcessAuthorityError) as error:
                raise SolverOwnershipError("reconnected process is no longer valid") from error
            if (
                not current.alive
                or current.creation_identity != metadata.creation_identity
                or current.started_at != metadata.started_at
                or (
                    current.executable_snapshot is None
                    or metadata.executable_snapshot is None
                    or current.executable_snapshot != metadata.executable_snapshot
                )
            ):
                raise SolverOwnershipError("reconnected process identity changed")
            lease.verify(digest=True)
            try:
                authority.verify(pid)
                current = _process_metadata(pid)
            except (OSError, ProcessAuthorityError) as error:
                raise SolverOwnershipError("reconnected process is no longer valid") from error
            if (
                not current.alive
                or current.creation_identity != metadata.creation_identity
                or current.started_at != metadata.started_at
                or (
                    current.executable_snapshot is None
                    or metadata.executable_snapshot is None
                    or current.executable_snapshot != metadata.executable_snapshot
                )
            ):
                raise SolverOwnershipError("reconnected process identity changed")
        except BaseException:
            try:
                lease.close()
            except BaseException:
                _DURABLE_CLAIM_CLEANUP.adopt(None, None, (), input_lease=lease)
            raise
        self._input_lease = lease

    def _validate_process_record(
        self, record: dict[str, object]
    ) -> tuple[_ProcessMetadata, datetime, ProcessAuthority]:
        self._verify_filesystem_authority()
        expected_ids = {
            "case_id": self._case_id,
            "intent_id": self._intent_id,
            "attempt_id": self._attempt_id,
        }
        if any(record.get(name) != expected for name, expected in expected_ids.items()):
            raise SolverOwnershipError("process record identity does not match")

        if record.get("state") != SolverState.RUNNING.value:
            raise SolverOwnershipError("process record is not a reconnectable RUNNING state")
        filesystem_identities = self._parse_record_filesystem_identities(
            record.get("filesystem_authority")
        )
        if filesystem_identities != self._filesystem_identities():
            raise SolverOwnershipError("process record filesystem authority does not match")
        self._validate_filesystem_identities(filesystem_identities)
        context = record.get("launch_context")
        if not isinstance(context, dict):
            raise SolverOwnershipError("process record launch context is invalid")
        context_digest = record.get("launch_context_digest")
        if not isinstance(context_digest, str):
            raise SolverOwnershipError("process record launch context digest is invalid")
        try:
            recomputed_digest = _json_digest(context)
        except SolverConfigurationError as error:
            raise SolverOwnershipError("process record launch context is invalid") from error
        if (
            context_digest != self._launch_context_digest
            or recomputed_digest != context_digest
            or context != self._launch_context
        ):
            raise SolverOwnershipError("process record launch context does not match")
        if _NATIVE_WINDOWS:
            # Keep the journal object identity and event token inside the
            # record validation boundary.  Reconnect may use them only after
            # the process authority below authenticates the live Job member.
            self._parse_resume_transaction_record_binding(record)
        if os.name == "posix":
            self._parse_posix_input_lease_record(record)

        authority_claim = record.get("process_authority")
        if (
            not isinstance(authority_claim, dict)
            or authority_claim.get("context_digest") != self._launch_context_digest
            or authority_claim.get("root_pid") != record.get("pid")
            or authority_claim.get("root_creation_identity")
            != record.get("process_creation_identity")
        ):
            raise SolverOwnershipError("process record authority context does not match")

        executable_value = record.get("executable_path")
        if not isinstance(executable_value, str) or not Path(executable_value).is_absolute():
            raise SolverOwnershipError("process record executable must be absolute")
        if _normalise_executable(executable_value) != _normalise_executable(self.spec.executable):
            raise SolverOwnershipError("process record executable does not match")
        executable_snapshot = self._parse_executable_image_record(record)

        pid = record.get("pid")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise SolverOwnershipError("process record PID is invalid")
        identity = record.get("process_creation_identity")
        if not isinstance(identity, str) or not identity:
            raise SolverOwnershipError("process record creation identity is invalid")

        started_value = record.get("start_time")
        if not isinstance(started_value, str):
            raise SolverOwnershipError("process record start time is invalid")
        try:
            started_at = datetime.fromisoformat(started_value)
        except ValueError as error:
            raise SolverOwnershipError("process record start time is invalid") from error
        if started_at.tzinfo is None or started_at.utcoffset() is None:
            raise SolverOwnershipError("process record start time is invalid")
        output_values = record.get("owned_output_paths")
        if not isinstance(output_values, dict):
            raise SolverOwnershipError("process record output paths are invalid")
        outputs = self.spec.expected_outputs
        self._validate_record_path(output_values.get("log"), outputs.log_path, "LOG path")
        self._validate_record_path(output_values.get("xplt"), outputs.xplt_path, "XPLT path")

        authority: ProcessAuthority | None = None
        try:
            authority = ProcessAuthority.from_claim(
                self.spec.attempt_root,
                authority_claim,
                self._launch_context_digest,
            )
            authority.verify(pid)
        except ProcessAuthorityError as error:
            if authority is not None:
                authority.close()
            raise SolverOwnershipError("recorded process authority is invalid") from error
        try:
            metadata = _process_metadata(pid)
        except OSError as error:
            authority.close()
            raise SolverOwnershipError("recorded process is not live") from error
        if not metadata.alive:
            authority.close()
            raise SolverOwnershipError("recorded process is not live")
        if os.name == "posix":
            current_snapshot = metadata.executable_snapshot
            if current_snapshot is None or current_snapshot != executable_snapshot:
                authority.close()
                raise SolverOwnershipError("current process executable image does not match")
        elif _normalise_executable(metadata.executable_path) != _normalise_executable(
            executable_value
        ):
            authority.close()
            raise SolverOwnershipError("current process executable does not match")
        if metadata.creation_identity != identity:
            authority.close()
            raise SolverOwnershipError("current process creation identity does not match")
        if metadata.started_at is None or metadata.started_at != started_at:
            authority.close()
            raise SolverOwnershipError("current process start time does not match")
        if authority is None:  # pragma: no cover - defensive type/state guard
            raise SolverOwnershipError("recorded process authority is unavailable")
        self._runtime_image_snapshot = executable_snapshot
        self._verify_filesystem_authority()
        return metadata, metadata.started_at, authority

    def _terminate_owned_process(
        self, process: subprocess.Popen[bytes] | _ReconnectedProcess
    ) -> None:
        """Terminate only the process group created by this supervisor."""

        if process.poll() is not None:
            return
        authority = self._process_authority
        if authority is None:
            raise SolverOwnershipError("process authority is unavailable")
        try:
            authority.terminate(process.pid)
        except ProcessAuthorityError as error:
            raise SolverOwnershipError("process authority could not be verified") from error

        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            try:
                authority.terminate(process.pid, force=True)
            except ProcessAuthorityError as error:
                raise SolverOwnershipError("process authority could not be verified") from error
            process.wait(timeout=2.0)

    def _drain_owned_descendants_after_root_exit(
        self,
        process: subprocess.Popen[bytes] | _ReconnectedProcess,
    ) -> None:
        """Drain the exact owned tree before validating root-exit outputs."""

        with self._lock:
            if self._result_latch is not None:
                issuance = self._result_issuance
                if issuance is None or issuance.result is not self._result_latch:
                    raise SolverOwnershipError("supervisor result latch is invalid")
                return
            if self._process is not process:
                raise SolverOwnershipError("observed root process is not owned by supervisor")
            authority = self._process_authority
            if authority is None:
                raise SolverOwnershipError("process authority is unavailable")
            try:
                authority.drain()
            except ProcessAuthorityError as error:
                raise SolverOwnershipError(
                    "owned process descendants could not be drained"
                ) from error

    def _release_process_authority(self) -> tuple[BaseException, ...]:
        authority = self._process_authority
        if authority is None:
            return ()
        self._process_authority = None
        failures: list[BaseException] = []
        try:
            authority.drain()
        except BaseException as error:
            failures.append(error)
        try:
            authority.close()
        except BaseException as error:
            failures.append(error)
        return tuple(failures)

    def _cleanup_terminal_failure(
        self,
        process: subprocess.Popen[bytes] | _ReconnectedProcess | None,
    ) -> tuple[BaseException, ...]:
        """Terminate, drain, and close an owned process after a late failure."""

        authority = self._process_authority
        if authority is None:
            return ()
        failures: list[BaseException] = []
        if process is not None:
            termination_failed = False
            try:
                self._terminate_owned_process(process)
            except BaseException as error:
                failures.append(error)
                termination_failed = True
            if termination_failed:
                try:
                    authority.terminate(process.pid, force=True)
                except BaseException as error:
                    failures.append(error)
            try:
                process.wait(timeout=2.0)
            except BaseException as error:
                failures.append(error)
        try:
            authority.drain()
        except BaseException as error:
            failures.append(error)
        try:
            authority.close()
        except BaseException as error:
            failures.append(error)
        self._process_authority = None
        return tuple(failures)

    def _fail_terminal_operation(self, error: BaseException) -> None:
        """Roll back a cancel/timeout failure without hiding cleanup errors."""

        cleanup_failures = [error]
        cleanup_failures.extend(self._cleanup_terminal_failure(self._process))
        cleanup_failures.extend(self._rollback_process_record())
        self._process = None
        self._started_at = None
        self._process_record = None
        self._result_latch = None
        self._result_issuance = None
        self._result = None
        self._state = SolverState.FAILED
        try:
            self._close_filesystem_authority()
        except BaseException as close_error:
            cleanup_failures.append(close_error)
        details = "; ".join(str(failure) for failure in cleanup_failures)
        cleanup_error = SolverOwnershipError(f"terminal cleanup failed: {details}")
        raise cleanup_error from error

    def _register_result(self, result: SolverRunResult) -> None:
        if type(result) is not SolverRunResult:
            raise SolverOwnershipError("solver result must be an exact SolverRunResult instance")
        if self._result_latch is not None or self._result_issuance is not None:
            raise SolverOwnershipError("supervisor already has an issued solver result")
        process_record = self._process_record
        identity = process_record.get("process_creation_identity") if process_record else None
        issuance = _ResultIssuance(
            result=result,
            supervisor=self,
            spec=self.spec,
            process=self._process,
            process_record=process_record,
            case_id=self._case_id,
            intent_id=self._intent_id,
            attempt_id=self._attempt_id,
            executable_path=str(self.spec.executable),
            process_identity=identity if isinstance(identity, str) else None,
            started_at=self._started_at,
            log_path=self.spec.expected_outputs.log_path,
            xplt_path=self.spec.expected_outputs.xplt_path,
            finished_at=result.finished_at,
            filesystem_identities=(
                self._filesystem_identities() if self._filesystem_authority is not None else ()
            ),
            snapshot=tuple(getattr(result, field) for field in _RESULT_FIELDS),
        )
        self._result_issuance = issuance

    def _unregister_result(self, result: object) -> bool:
        if type(result) is not SolverRunResult:
            return False
        issuance = self._result_issuance
        if issuance is None or issuance.result is not result:
            return False
        self._result_issuance = None
        if self._result_latch is result:
            self._result_latch = None
        if self._result is result:
            self._result = None
        return True

    def _validate_result(self, candidate: object) -> SolverRunResult:
        if type(self) is not SolverSupervisor:
            raise SolverOwnershipError("solver supervisor must be an exact instance")
        if type(candidate) is not SolverRunResult:
            raise SolverOwnershipError("solver result must be an exact SolverRunResult instance")
        with self._lock:
            issuance = self._result_issuance
            if issuance is None or issuance.result is not candidate:
                raise SolverOwnershipError("solver result was not issued by a supervisor")
            if issuance.supervisor is not self:
                raise SolverOwnershipError("solver result belongs to another supervisor")
            if self._result_latch is not candidate:
                raise SolverOwnershipError("supervisor result binding is invalid")
            if self._result is not candidate:
                raise SolverOwnershipError("supervisor result latch is invalid")
            if self.spec is not issuance.spec:
                raise SolverOwnershipError("supervisor launch binding is invalid")
            for value, expected, label in (
                (self._case_id, issuance.case_id, "case_id"),
                (self._intent_id, issuance.intent_id, "intent_id"),
                (self._attempt_id, issuance.attempt_id, "attempt_id"),
            ):
                if (
                    not isinstance(value, str)
                    or not value.strip()
                    or not isinstance(expected, str)
                    or not expected.strip()
                    or value != expected
                ):
                    raise SolverOwnershipError(f"solver result {label} binding is invalid")
            if self._process is not issuance.process or self._process is None:
                raise SolverOwnershipError("solver result process binding is invalid")
            self._validate_filesystem_identities(issuance.filesystem_identities)
            pid = getattr(self._process, "pid", None)
            if (
                isinstance(pid, bool)
                or not isinstance(pid, int)
                or pid <= 0
                or candidate.pid != pid
            ):
                raise SolverOwnershipError("solver result process identity is invalid")

            process_record = self._process_record
            if process_record is None or process_record is not issuance.process_record:
                raise SolverOwnershipError("solver result process record binding is invalid")
            if process_record.get("pid") != pid:
                raise SolverOwnershipError("solver result process record PID is invalid")
            executable = process_record.get("executable_path")
            if (
                not isinstance(executable, str)
                or not Path(executable).is_absolute()
                or executable != issuance.executable_path
                or _normalise_executable(executable) != _normalise_executable(self.spec.executable)
            ):
                raise SolverOwnershipError("solver result executable binding is invalid")
            identity = process_record.get("process_creation_identity")
            if (
                issuance.process_identity is None
                or not isinstance(identity, str)
                or not identity.strip()
                or identity != issuance.process_identity
            ):
                raise SolverOwnershipError("solver result process creation identity is invalid")

            started_at = self._started_at
            if (
                issuance.started_at is None
                or started_at is not issuance.started_at
                or not isinstance(started_at, datetime)
                or started_at.tzinfo is None
                or started_at.utcoffset() is None
                or candidate.started_at is not started_at
            ):
                raise SolverOwnershipError("solver result start time is invalid")
            start_value = process_record.get("start_time")
            if not isinstance(start_value, str):
                raise SolverOwnershipError("solver result process start time is invalid")
            try:
                record_started_at = datetime.fromisoformat(start_value)
            except ValueError as error:
                raise SolverOwnershipError("solver result process start time is invalid") from error
            if record_started_at != started_at:
                raise SolverOwnershipError("solver result process start time does not match")

            if (
                issuance.finished_at is None
                or not isinstance(candidate.finished_at, datetime)
                or candidate.finished_at is not issuance.finished_at
                or candidate.finished_at.tzinfo is None
                or candidate.finished_at.utcoffset() is None
                or candidate.finished_at < started_at
            ):
                raise SolverOwnershipError("solver result timestamps are invalid")
            output_values = process_record.get("owned_output_paths")
            if not isinstance(output_values, dict):
                raise SolverOwnershipError("solver result output paths are invalid")
            self._validate_record_path(output_values.get("log"), issuance.log_path, "LOG path")
            self._validate_record_path(output_values.get("xplt"), issuance.xplt_path, "XPLT path")
            for candidate_path, expected_path, path_label in (
                (candidate.log_path, issuance.log_path, "LOG path"),
                (candidate.xplt_path, issuance.xplt_path, "XPLT path"),
            ):
                if not isinstance(candidate_path, Path):
                    raise SolverOwnershipError(f"{path_label} must be a Path")
                self._validate_record_path(os.fspath(candidate_path), expected_path, path_label)
                if candidate_path != expected_path:
                    raise SolverOwnershipError(f"{path_label} binding is invalid")
            for field, expected_value in zip(_RESULT_FIELDS, issuance.snapshot, strict=True):
                try:
                    value = getattr(candidate, field)
                except AttributeError as error:
                    raise SolverOwnershipError("solver result fields are invalid") from error
                if value != expected_value:
                    raise SolverOwnershipError("solver result contents were modified")
            return candidate

    def _complete(self, state: SolverState, return_code: int | None) -> SolverRunResult:
        with self._lock:
            if self._result_latch is not None:
                issuance = self._result_issuance
                if issuance is None or issuance.result is not self._result_latch:
                    raise SolverOwnershipError("supervisor result latch is invalid")
                return self._result_latch
            if self._result_issuance is not None:
                raise SolverOwnershipError("supervisor has an unlatched solver result")

            # Cancellation before launch has no filesystem or process
            # publication to validate, but still receives an issued result.
            if self._process is None and state is SolverState.CANCELLED:
                cancelled_result = SolverRunResult(
                    state=state,
                    classification=SolverClassification.CANCELLED,
                    return_code=return_code,
                    pid=None,
                    command=self.spec.command,
                    log_path=self.spec.expected_outputs.log_path,
                    xplt_path=self.spec.expected_outputs.xplt_path,
                    started_at=None,
                    finished_at=datetime.now(UTC),
                )
                try:
                    self._register_result(cancelled_result)
                    self._state = state
                    self._result_latch = cancelled_result
                    self._result = cancelled_result
                    self._close_filesystem_authority()
                    return cancelled_result
                except BaseException:
                    self._unregister_result(cancelled_result)
                    self._state = SolverState.FAILED
                    self._close_filesystem_authority()
                    raise

            fbs_validation: FbsValidation | None = None
            result: SolverRunResult | None = None
            try:
                if state is not SolverState.CANCELLED:
                    self._revalidate_launch_binding()
                self._verify_filesystem_authority()
                self._verify_input_lease(digest=True)
                started_at = self._started_at
                pid = self._process.pid if self._process is not None else None
                outputs = self.spec.expected_outputs
                log_path = self._path_for_io(outputs.log_path)

                log_validation = (
                    self._log_validator.validate(log_path)
                    if self._log_validator is not None
                    else validate_log(
                        log_path,
                        expected_steps=self.spec.expected_steps,
                        expected_final_time=self.spec.expected_final_time,
                    )
                )
                self._verify_filesystem_authority()
                classification = self._classify_process_and_outputs(
                    state,
                    return_code,
                    log_validation,
                )
                self._verify_filesystem_authority()
                if classification is None:
                    if self._fbs_adapter is None:
                        classification = SolverClassification.FBS_UNVERIFIED
                    else:
                        try:
                            self._verify_filesystem_authority()
                            fbs_validation = validate_requested_fields(
                                self._fbs_adapter,
                                outputs.xplt_path,
                                self._requested_fields,
                            )
                            self._verify_filesystem_authority()
                        except Exception as error:
                            if fbs_validation is not None:
                                _unregister_validation(fbs_validation)
                                fbs_validation = None
                            fbs_validation = _invalid_validation(
                                self._fbs_adapter,
                                outputs.xplt_path,
                                self._requested_fields,
                                str(error),
                            )
                        if not fbs_validation.valid:
                            classification = SolverClassification.FBS_INVALID
                        elif (
                            fbs_validation.official is True
                            and fbs_validation.provenance == "official"
                        ):
                            classification = SolverClassification.SUCCESS
                        else:
                            classification = SolverClassification.FBS_UNVERIFIED

                finished_at = datetime.now(UTC)
                result = SolverRunResult(
                    state=state,
                    classification=classification,
                    return_code=return_code,
                    pid=pid,
                    command=self.spec.command,
                    log_path=outputs.log_path,
                    xplt_path=outputs.xplt_path,
                    started_at=started_at,
                    finished_at=finished_at,
                    log_validation=log_validation,
                    fbs_validation=fbs_validation,
                )
                if fbs_validation is not None:
                    _adopt_validation(fbs_validation, self, result)
                self._register_result(result)

                # Result/FBS issuance is provisional until the final live
                # binding and root checks pass and process authority release
                # completes successfully.
                if state is not SolverState.CANCELLED:
                    self._revalidate_launch_binding()
                self._verify_filesystem_authority()
                release_failures = self._release_process_authority()
                if release_failures:
                    details = "; ".join(str(failure) for failure in release_failures)
                    raise SolverOwnershipError(
                        f"owned process authority could not be released: {details}"
                    ) from release_failures[0]
                if state is not SolverState.CANCELLED:
                    self._revalidate_launch_binding()
                self._verify_filesystem_authority()

                self._state = state
                self._result_latch = result
                self._result = result
                self._close_filesystem_authority()
                return result
            except BaseException as error:
                if result is not None:
                    self._unregister_result(result)
                if fbs_validation is not None:
                    _unregister_validation(fbs_validation)
                cleanup_failures = list(self._cleanup_terminal_failure(self._process))
                cleanup_failures.extend(self._rollback_process_record())
                self._process = None
                self._started_at = None
                self._process_record = None
                self._result_latch = None
                self._result_issuance = None
                self._result = None
                self._state = SolverState.FAILED
                try:
                    self._close_filesystem_authority()
                except BaseException as close_error:
                    cleanup_failures.append(close_error)
                if cleanup_failures:
                    details = "; ".join(str(failure) for failure in cleanup_failures)
                    cleanup_error = SolverOwnershipError(f"terminal cleanup failed: {details}")
                    raise cleanup_error from error
                raise

    def _classify_process_and_outputs(
        self,
        state: SolverState,
        return_code: int | None,
        log_validation: LogValidation,
    ) -> SolverClassification | None:
        if state is SolverState.TIMED_OUT:
            return SolverClassification.TIMEOUT
        if state is SolverState.CANCELLED:
            return SolverClassification.CANCELLED
        if log_validation.classification in {
            SolverClassification.NEGATIVE_JACOBIAN,
            SolverClassification.NONLINEAR_CONVERGENCE,
            SolverClassification.FATAL,
            SolverClassification.INIT_ONLY,
        }:
            return log_validation.classification
        outputs = self.spec.expected_outputs
        if not (
            self._path_for_io(outputs.log_path).is_file()
            and self._path_for_io(outputs.xplt_path).is_file()
        ):
            return SolverClassification.MISSING_OUTPUT
        if return_code != 0:
            return SolverClassification.NONZERO_EXIT
        if log_validation.classification is not None:
            return log_validation.classification
        return None
