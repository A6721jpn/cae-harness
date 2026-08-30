"""Owned, headless FEBio process supervision."""

from __future__ import annotations

import contextlib
import ctypes
import json
import math
import os
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
    _authority_record,
    _invalid_validation,
    _unregister_validation,
    validate_requested_fields,
)
from .log import LogValidation, LogValidator, validate_log
from .process_authority import ProcessAuthority, ProcessAuthorityError
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
_WINDOWS_OPEN_EXISTING = 3
_WINDOWS_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_WINDOWS_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_WINDOWS_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


@dataclass(frozen=True, slots=True)
class _FilesystemIdentity:
    path: Path
    device: int
    inode: int


@dataclass(slots=True)
class _WindowsDirectoryHandle:
    value: int
    volume_serial: int
    file_index: int


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
    value = raw_value_int
    try:
        information = _windows_directory_information(value)
    except BaseException:
        with contextlib.suppress(BaseException):
            kernel32.CloseHandle(value)
        raise
    file_index = (int(information.nFileIndexHigh) << 32) | int(information.nFileIndexLow)
    return _WindowsDirectoryHandle(
        value=value,
        volume_serial=int(information.dwVolumeSerialNumber),
        file_index=file_index,
    )


def _windows_close_directory(handle: _WindowsDirectoryHandle) -> None:
    kernel32 = _windows_kernel32()
    if not kernel32.CloseHandle(handle.value):
        error = ctypes.get_last_error()
        raise OSError(error, "unable to close filesystem authority handle")


def _windows_verify_directory(handle: _WindowsDirectoryHandle) -> None:
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


class _FilesystemAuthority:
    """Keep the attempt path and every ancestor bound to stable identities."""

    def __init__(self, root: Path) -> None:
        self.root = Path(os.path.abspath(os.fspath(root)))
        self._entries: tuple[
            tuple[_FilesystemIdentity, int | _WindowsDirectoryHandle | None], ...
        ] = ()
        self._root_fd: int | None = None
        self._closed = False
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
                            with contextlib.suppress(BaseException):
                                _windows_close_directory(held)
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
            with contextlib.suppress(BaseException):
                self.close()
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
        if self._closed:
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
        self._closed = True
        failures: list[BaseException] = []
        for _identity, held in self._entries:
            if os.name == "posix" and isinstance(held, int):
                try:
                    os.close(held)
                except BaseException as error:
                    failures.append(error)
            elif os.name == "nt" and isinstance(held, _WindowsDirectoryHandle):
                try:
                    _windows_close_directory(held)
                except BaseException as error:
                    failures.append(error)
        self._root_fd = None
        if failures:
            raise SolverOwnershipError("filesystem authority could not be closed") from failures[0]


@dataclass(frozen=True, slots=True)
class _ProcessRecordClaim:
    path: Path
    content: bytes
    device: int
    inode: int
    state: str | None


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


_RESULT_REGISTRY: dict[int, _ResultIssuance] = {}
_RESULT_REGISTRY_LOCK = threading.RLock()


@dataclass(frozen=True, slots=True)
class _ProcessMetadata:
    executable_path: str
    creation_identity: str
    alive: bool
    return_code: int | None
    started_at: datetime | None = None


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
    try:
        stat_line = (proc_root / "stat").read_text(encoding="utf-8")
        stat_fields = stat_line.rpartition(")")[2].split()
        executable_path = os.readlink(os.fspath(proc_root / "exe"))
    except OSError as error:
        raise ProcessLookupError(pid) from error
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
    return _ProcessMetadata(
        executable_path=executable_path,
        creation_identity=f"posix:{stat_fields[19]}",
        alive=state not in {"Z", "X"},
        return_code=None if state not in {"Z", "X"} else 0,
        started_at=started_at,
    )


def _process_metadata(pid: int) -> _ProcessMetadata:
    if os.name == "nt":
        return _windows_process_metadata(pid)
    if os.name == "posix":
        return _posix_process_metadata(pid)
    raise OSError("process identity is unsupported on this platform")


def _process_action(process: subprocess.Popen[bytes] | _ReconnectedProcess, action: str) -> None:
    getattr(process, action)()


class _ReconnectedProcess:
    """Small process handle for a process that is not this client's child."""

    def __init__(
        self,
        pid: int,
        executable_path: str,
        creation_identity: str,
        authority: ProcessAuthority,
    ) -> None:
        self.pid = pid
        self._executable_path = executable_path
        self._creation_identity = creation_identity
        self._authority = authority

    def poll(self) -> int | None:
        try:
            metadata = _process_metadata(self.pid)
        except ProcessLookupError:
            return 1
        if not metadata.alive:
            return metadata.return_code
        if metadata.alive and (
            _normalise_executable(metadata.executable_path)
            != _normalise_executable(self._executable_path)
            or metadata.creation_identity != self._creation_identity
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
        self.spec = _capability_record.spec
        self._case_id = case_id
        self._intent_id = intent_id
        self._attempt_id = attempt_id
        self._launch_context = launch_context
        self._launch_context_digest = launch_context_digest
        if fbs_adapter is not None:
            try:
                _authority_record(fbs_adapter)
            except TypeError as error:
                raise SolverConfigurationError(
                    "fbs_adapter must be issued by FbsAdapterManager"
                ) from error
        self._fbs_adapter = fbs_adapter
        self._requested_fields = (
            tuple(requested_fields) if requested_fields is not None else self.spec.requested_fields
        )
        self._log_validator = log_validator
        self._owner_token = uuid.uuid4().hex
        self._lock = threading.RLock()
        self._state = SolverState.NOT_STARTED
        self._process: subprocess.Popen[bytes] | _ReconnectedProcess | None = None
        self._process_authority: ProcessAuthority | None = None
        self._started_at: datetime | None = None
        self._result: SolverRunResult | None = None
        self._process_record: dict[str, object] | None = None
        self._process_record_claim: _ProcessRecordClaim | None = None
        self._record_owner_token = self._owner_token
        filesystem_authority = _FilesystemAuthority(self.spec.attempt_root)
        try:
            self._filesystem_authority: _FilesystemAuthority | None = filesystem_authority
            self._revalidate_launch_binding()
        except BaseException:
            filesystem_authority.close()
            raise

    def __del__(self) -> None:
        authority = getattr(self, "_filesystem_authority", None)
        if authority is not None:
            with contextlib.suppress(BaseException):
                authority.close()

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

        return self.spec.attempt_root / _PROCESS_RECORD_NAME

    @property
    def result(self) -> SolverRunResult | None:
        with self._lock:
            return self._result

    def _acquire_filesystem_authority(self) -> None:
        if self._filesystem_authority is None:
            self._filesystem_authority = _FilesystemAuthority(self.spec.attempt_root)
        self._filesystem_authority.verify()

    def _verify_filesystem_authority(self) -> None:
        authority = self._filesystem_authority
        if authority is None:
            raise SolverOwnershipError("filesystem authority is unavailable")
        authority.verify()

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

    def _close_filesystem_authority(self) -> None:
        authority = self._filesystem_authority
        self._filesystem_authority = None
        if authority is not None:
            authority.close()

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
        command: list[str] = []
        for value in self.spec.command:
            try:
                candidate = Path(value)
            except (TypeError, ValueError):
                command.append(value)
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

    def _record_claim_matches(self, claim: _ProcessRecordClaim) -> bool:
        try:
            metadata = os.lstat(os.fspath(claim.path))
            if (
                stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISREG(metadata.st_mode)
                or int(metadata.st_dev) != claim.device
                or int(metadata.st_ino) != claim.inode
            ):
                return False
            return self._read_path_bytes(claim.path) == claim.content
        except FileNotFoundError:
            return False
        except OSError as error:
            raise SolverOwnershipError("unable to verify the owned process record") from error

    def _rollback_process_record(self) -> tuple[BaseException, ...]:
        """Remove only the exact record inode and bytes this owner published."""

        claim = self._process_record_claim
        if claim is None:
            return ()
        try:
            if not self._record_claim_matches(claim):
                return ()
            if os.name == "posix":
                # The claim path is an anchored /proc/self/fd path, so unlink
                # cannot be redirected by a renamed lexical attempt root.
                os.unlink(os.fspath(claim.path))
            else:
                claim.path.unlink()
        except FileNotFoundError:
            return ()
        except BaseException as error:
            failure = SolverOwnershipError("unable to roll back the owned process record")
            failure.__cause__ = error
            return (failure,)
        return ()

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
            bound = False
            try:
                self._acquire_filesystem_authority()
                self._verify_filesystem_authority()
                self._prepare_outputs()
                self._verify_filesystem_authority()
                self._revalidate_launch_binding()
                record_path = self._path_for_io(self.process_record_path)
                if os.path.lexists(os.fspath(record_path)):
                    raise SolverLaunchError(
                        f"process record already exists: {self.process_record_path}"
                    )
                if not self._input_is_regular():
                    raise FileNotFoundError(f"solver input does not exist: {self.spec.input_path}")
                authority = ProcessAuthority.create(
                    self.spec.attempt_root, self._launch_context_digest
                )
                self._verify_filesystem_authority()
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
                    startup_info = subprocess.STARTUPINFO()
                    startup_info.lpAttributeList = {"handle_list": [child_handle]}
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
                else:
                    filesystem_authority = self._filesystem_authority
                    if filesystem_authority is None:  # pragma: no cover - state guard
                        raise SolverOwnershipError("filesystem authority is unavailable")
                    pass_fds = tuple(
                        dict.fromkeys(
                            authority.child_pass_fds() + filesystem_authority.child_pass_fds
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
                metadata = bound_metadata
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
                    authority.resume(process.pid)
                    self._revalidate_launch_binding()
                    self._verify_filesystem_authority()
                running_record = self._make_process_record(
                    process.pid, metadata, started_at, authority, state=SolverState.RUNNING.value
                )
                self._write_process_record(running_record)
                self._process_record = running_record
                # A final binding check occurs only after RUNNING is durable.
                # Failure here must roll back that exact record before the
                # process and filesystem authorities are released.
                self._revalidate_launch_binding()
                self._verify_filesystem_authority()
            except BaseException as error:
                cleanup_failures = list(self._cleanup_failed_start(process, authority, bound))
                cleanup_failures.extend(self._rollback_process_record())
                self._process = None
                self._started_at = None
                self._process_authority = None
                self._process_record = None
                self._process_record_claim = None
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
            record = supervisor._read_process_record()
            supervisor._revalidate_launch_binding()
            metadata, started_at, authority = supervisor._validate_process_record(record)
            supervisor._revalidate_launch_binding()
            supervisor._verify_filesystem_authority()
            owner_token = record.get("owner_token")
            if isinstance(owner_token, str) and owner_token:
                supervisor._record_owner_token = owner_token
            supervisor._process_record = record
            pid = cast(int, record["pid"])
            supervisor._process = _ReconnectedProcess(
                pid, metadata.executable_path, metadata.creation_identity, authority
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
            if self._result is not None:
                return self._result
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
            return self._complete(SolverState.TIMED_OUT, process.poll())
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
            if self._result is not None:
                return self._result
            if self._state is SolverState.NOT_STARTED:
                return self._complete(SolverState.CANCELLED, None)
            process = self._process
        if process is None:  # pragma: no cover - defensive state guard
            raise SolverLaunchError("solver process ownership was lost")
        try:
            self._terminate_owned_process(process)
        except BaseException as error:
            self._fail_terminal_operation(error)
        return self._complete(SolverState.CANCELLED, process.poll())

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
        return {
            "case_id": self._case_id,
            "intent_id": self._intent_id,
            "attempt_id": self._attempt_id,
            "state": state,
            "owner_token": self._record_owner_token,
            "executable_path": str(self.spec.executable),
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

    @staticmethod
    def _read_path_bytes(path: Path) -> bytes:
        if os.name != "posix":
            return path.read_bytes()
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(os.fspath(path), flags)
        try:
            with os.fdopen(fd, "rb") as stream:
                fd = -1
                return stream.read()
        finally:
            if fd >= 0:
                with contextlib.suppress(OSError):
                    os.close(fd)

    def _claim_process_record(self, path: Path, content: bytes, record: dict[str, object]) -> None:
        metadata = os.stat(os.fspath(path), follow_symlinks=False)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise OSError("process record is not a regular file")
        record_state = record.get("state")
        self._process_record_claim = _ProcessRecordClaim(
            path=path,
            content=content,
            device=int(metadata.st_dev),
            inode=int(metadata.st_ino),
            state=record_state if isinstance(record_state, str) else None,
        )

    def _write_process_record(self, record: dict[str, object]) -> None:
        self._verify_filesystem_authority()
        path = self._path_for_io(self.process_record_path)
        temporary = path.with_name(f".{path.name}.{self._owner_token}.tmp")
        try:
            content = json.dumps(record, indent=2, sort_keys=True).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise OSError(f"unable to persist process record: {error}") from error
        try:
            if os.name == "posix":
                authority = self._filesystem_authority
                if authority is None:
                    raise SolverOwnershipError("filesystem authority is unavailable")
                with authority.open_directory(
                    self.process_record_path.parent, create=True
                ) as parent_fd:
                    flags = (
                        os.O_WRONLY
                        | os.O_CREAT
                        | os.O_EXCL
                        | getattr(os, "O_CLOEXEC", 0)
                        | getattr(os, "O_NOFOLLOW", 0)
                    )
                    temp_fd = os.open(temporary.name, flags, 0o600, dir_fd=parent_fd)
                    try:
                        with os.fdopen(temp_fd, "wb") as stream:
                            temp_fd = -1
                            stream.write(content)
                            stream.flush()
                            os.fsync(stream.fileno())
                    finally:
                        if temp_fd >= 0:
                            with contextlib.suppress(OSError):
                                os.close(temp_fd)
                    # A create-new hard link publishes the record without
                    # replacing a foreign file that won the name race.
                    os.link(
                        temporary.name,
                        path.name,
                        src_dir_fd=parent_fd,
                        dst_dir_fd=parent_fd,
                        follow_symlinks=False,
                    )
                    os.unlink(temporary.name, dir_fd=parent_fd)
                    self._claim_process_record(path, content, record)
                    os.fsync(parent_fd)
            else:
                with temporary.open("x", encoding="utf-8", newline="") as stream:
                    stream.write(content.decode("utf-8"))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(os.fspath(temporary), os.fspath(path))
            if os.name != "posix":
                self._claim_process_record(path, content, record)
            if self._read_path_bytes(path) != content:
                raise OSError("process record changed during publication")
        except (OSError, SolverOwnershipError, TypeError, ValueError) as error:
            if os.name == "posix":
                with contextlib.suppress(OSError):
                    os.unlink(os.fspath(temporary))
            else:
                with contextlib.suppress(OSError):
                    temporary.unlink(missing_ok=True)
            if isinstance(error, SolverOwnershipError):
                raise
            raise OSError(f"unable to persist process record: {error}") from error

    def _read_process_record(self) -> dict[str, object]:
        self._verify_filesystem_authority()
        path = self._path_for_io(self.process_record_path)
        try:
            metadata = os.stat(os.fspath(path), follow_symlinks=False)
        except OSError as error:
            raise SolverOwnershipError(f"missing process record: {path}") from error
        if not stat.S_ISREG(metadata.st_mode):
            raise SolverOwnershipError(f"missing process record: {path}")
        try:
            content = self._read_path_bytes(path)
            record = json.loads(content.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise SolverOwnershipError(f"invalid process record: {path}") from error
        if not isinstance(record, dict):
            raise SolverOwnershipError("process record must be a JSON object")
        self._verify_filesystem_authority()
        metadata = os.stat(os.fspath(path), follow_symlinks=False)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise SolverOwnershipError("process record is not a regular file")
        if self._read_path_bytes(path) != content:
            raise SolverOwnershipError("process record changed during read")
        record_state = record.get("state")
        self._process_record_claim = _ProcessRecordClaim(
            path=path,
            content=content,
            device=int(metadata.st_dev),
            inode=int(metadata.st_ino),
            state=record_state if isinstance(record_state, str) else None,
        )
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
        if _normalise_executable(metadata.executable_path) != _normalise_executable(
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
        self._process_record_claim = None
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
        with _RESULT_REGISTRY_LOCK:
            existing = _RESULT_REGISTRY.get(id(result))
            if existing is not None and existing.result is not result:
                raise SolverOwnershipError("solver result registry collision")
            _RESULT_REGISTRY[id(result)] = issuance

    def _unregister_result(self, result: object) -> bool:
        if type(result) is not SolverRunResult:
            return False
        with _RESULT_REGISTRY_LOCK:
            issuance = _RESULT_REGISTRY.get(id(result))
            if issuance is None or issuance.result is not result:
                return False
            del _RESULT_REGISTRY[id(result)]
            return True

    def _validate_result(self, candidate: object) -> SolverRunResult:
        if type(self) is not SolverSupervisor:
            raise SolverOwnershipError("solver supervisor must be an exact instance")
        if type(candidate) is not SolverRunResult:
            raise SolverOwnershipError("solver result must be an exact SolverRunResult instance")
        with self._lock:
            with _RESULT_REGISTRY_LOCK:
                issuance = _RESULT_REGISTRY.get(id(candidate))
            if issuance is None or issuance.result is not candidate:
                raise SolverOwnershipError("solver result was not issued by a supervisor")
            if issuance.supervisor is not self:
                raise SolverOwnershipError("solver result belongs to another supervisor")
            if self._result is not candidate:
                raise SolverOwnershipError("supervisor result binding is invalid")
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
            if self._result is not None:
                return self._result

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
                                attempt_root=self.spec.attempt_root,
                                physical_path=self._path_for_io(outputs.xplt_path),
                                physical_root=self._path_for_io(self.spec.attempt_root),
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
                self._process_record_claim = None
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
