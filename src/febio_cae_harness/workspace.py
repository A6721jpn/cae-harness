"""Validated case-owned filesystem boundaries.

This module owns case directory creation and path validation only.  It does
not inspect or infer the physical meaning of any input, model, or result.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import shutil
import stat
from collections import OrderedDict
from collections.abc import Callable, Iterable, Iterator
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import FrozenInstanceError, dataclass
from pathlib import Path
from typing import Any, BinaryIO, Literal, SupportsIndex, cast

__all__ = [
    "AttemptWorkspace",
    "CaseWorkspace",
    "ImmutableInputError",
    "ValidatedCaseWorkspace",
    "WorkspaceBoundaryError",
]


_TEMPORARY_ROOT = "90_Temporary"
_PERMANENT_ROOTS = frozenset({"02_Model", "03_Result", "04_Report", "05_Verification"})
_CONTROL_FILES = frozenset({"CASE_MANIFEST.json", "intent.json"})
_EVENTS_FILE = f"{_TEMPORARY_ROOT}/events.jsonl"
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


type _IdentityStamp = tuple[int, int]
type _FileState = tuple[int, int, int, int, int, int, int]


@dataclass(frozen=True, slots=True)
class _OriginalInputSource:
    path: Path
    state: _FileState


_MANAGER_REGISTRY: dict[int, tuple[Any, ...]] = {}
_CASE_REGISTRY: dict[int, tuple[Any, ...]] = {}
_ATTEMPT_REGISTRY: dict[int, tuple[Any, ...]] = {}
_ATTEMPT_ROOT_STAMP_LIMIT = 128
_ATTEMPT_ROOT_STAMPS: OrderedDict[
    tuple[Path, _IdentityStamp, str], tuple[Path, _IdentityStamp, Any]
] = OrderedDict()


class WorkspaceBoundaryError(PermissionError):
    """Raised when an operation leaves the owned case workspace."""


class ImmutableInputError(WorkspaceBoundaryError):
    """Raised when an operation would modify a preserved original input."""


def _validate_segment(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or value in {".", ".."}:
        raise ValueError(f"{label} must be a non-empty path segment")
    if value != value.strip() or "\x00" in value or "/" in value or "\\" in value:
        raise ValueError(f"{label} must be a single path segment")
    return value


def _lexical_path(value: str | Path) -> Path:
    """Return an absolute path without resolving filesystem aliases."""

    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return Path(os.path.abspath(os.fspath(path)))


def _reject_reparse_alias(path: str | Path, label: str = "path") -> Path:
    """Reject symlinks, junctions, and all other reparse-point ancestors."""

    _reject_parent_segments(Path(path), label)
    absolute = _lexical_path(path)
    ancestors: list[Path] = []
    current = absolute
    while True:
        ancestors.append(current)
        if current == current.parent:
            break
        current = current.parent
    for ancestor in reversed(ancestors):
        try:
            exists = os.path.lexists(os.fspath(ancestor))
        except OSError as error:
            raise WorkspaceBoundaryError(f"cannot inspect {label}: {absolute}") from error
        if not exists:
            continue
        try:
            metadata = ancestor.lstat()
        except OSError as error:
            raise WorkspaceBoundaryError(f"cannot inspect {label}: {absolute}") from error
        if stat.S_ISLNK(metadata.st_mode) or bool(
            getattr(metadata, "st_file_attributes", 0) & _REPARSE_POINT
        ):
            raise WorkspaceBoundaryError(f"{label} cannot contain a reparse-point alias")
    return absolute


def _identity_stamp(
    path: str | Path, label: str, expected: _IdentityStamp | None = None
) -> _IdentityStamp:
    absolute = _reject_reparse_alias(path, label)
    try:
        metadata = os.lstat(os.fspath(absolute))
    except OSError as error:
        raise WorkspaceBoundaryError(f"cannot inspect {label}: {absolute}") from error
    if not stat.S_ISDIR(metadata.st_mode):
        raise WorkspaceBoundaryError(f"{label} must be a directory")
    stamp = (int(metadata.st_dev), int(metadata.st_ino))
    if expected is not None and stamp != expected:
        raise WorkspaceBoundaryError(f"{label} was replaced or renamed")
    return stamp


def _file_state(metadata: os.stat_result) -> _FileState:
    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(metadata.st_mode),
        int(metadata.st_nlink),
        int(metadata.st_size),
        int(metadata.st_mtime_ns),
        int(metadata.st_ctime_ns),
    )


def _file_state_identity(state: _FileState) -> _IdentityStamp:
    return (state[0], state[1])


def _source_path_state(path: Path) -> _FileState:
    try:
        checked = _reject_reparse_alias(path, "original input")
        metadata = os.lstat(os.fspath(checked))
    except (OSError, WorkspaceBoundaryError) as error:
        raise WorkspaceBoundaryError("original input changed during case creation") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise WorkspaceBoundaryError("original input changed during case creation")
    return _file_state(metadata)


def _source_stream_state(stream: BinaryIO) -> _FileState:
    try:
        metadata = os.fstat(stream.fileno())
    except OSError as error:
        raise WorkspaceBoundaryError("original input changed during case creation") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise WorkspaceBoundaryError("original input changed during case creation")
    return _file_state(metadata)


def _stream_sha256(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _windows_create(
    path: Path, access: int, share: int, disposition: int, flags: int, label: str
) -> int:
    import ctypes

    create_file = ctypes.WinDLL("kernel32", use_last_error=True).CreateFileW
    create_file.restype = ctypes.c_void_p
    handle = create_file(
        ctypes.c_wchar_p(os.fspath(path)), access, share, None, disposition, flags, None
    )
    handle_value = getattr(handle, "value", handle)
    if handle_value in {None, ctypes.c_void_p(-1).value}:
        raise WorkspaceBoundaryError(f"cannot open {label}: {path} ({ctypes.get_last_error()})")
    return int(handle_value)


def _close_handle(handle: int) -> None:
    if os.name == "nt":
        import ctypes

        close_handle = ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle
        close_handle.argtypes = [ctypes.c_void_p]
        close_handle.restype = ctypes.c_int
        if not close_handle(ctypes.c_void_p(handle)):
            error = ctypes.get_last_error()
            raise OSError(error, f"CloseHandle failed ({error})")
    else:
        os.close(handle)


def _windows_descriptor_handle(descriptor: int) -> int:
    import msvcrt

    try:
        return int(msvcrt.get_osfhandle(descriptor))
    except OSError as error:
        raise WorkspaceBoundaryError("cannot resolve exact file handle") from error


def _set_open_file_mode(descriptor: int, mode: int, label: str) -> None:
    """Apply mode metadata through the continuously held exact file."""

    if os.name != "nt":
        fchmod = getattr(os, "fchmod", None)
        if fchmod is None:  # pragma: no cover - POSIX supplies fchmod
            raise WorkspaceBoundaryError(f"exact mode binding is unavailable for {label}")
        try:
            fchmod(descriptor, mode)
        except OSError as error:
            raise WorkspaceBoundaryError(f"cannot set {label} mode") from error
        return

    import ctypes

    class FileBasicInfo(ctypes.Structure):
        _fields_ = [
            ("CreationTime", ctypes.c_int64),
            ("LastAccessTime", ctypes.c_int64),
            ("LastWriteTime", ctypes.c_int64),
            ("ChangeTime", ctypes.c_int64),
            ("FileAttributes", ctypes.c_uint32),
        ]

    native = _windows_descriptor_handle(descriptor)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_information = kernel32.GetFileInformationByHandleEx
    get_information.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    get_information.restype = ctypes.c_int
    set_information = kernel32.SetFileInformationByHandle
    set_information.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    set_information.restype = ctypes.c_int
    information = FileBasicInfo()
    if not get_information(
        ctypes.c_void_p(native),
        0,
        ctypes.byref(information),
        ctypes.sizeof(information),
    ):
        last_error = ctypes.get_last_error()
        raise WorkspaceBoundaryError(f"cannot inspect {label} mode ({last_error})")
    if mode & stat.S_IWRITE:
        information.FileAttributes &= ~0x00000001
    else:
        information.FileAttributes |= 0x00000001
    if not information.FileAttributes:
        information.FileAttributes = 0x00000080
    if not set_information(
        ctypes.c_void_p(native),
        0,
        ctypes.byref(information),
        ctypes.sizeof(information),
    ):
        last_error = ctypes.get_last_error()
        raise WorkspaceBoundaryError(f"cannot set {label} mode ({last_error})")


def _windows_rename_open_file(
    descriptor: int,
    parent_handle: int,
    parent_path: Path,
    target_name: str,
    *,
    replace: bool,
    label: str,
) -> None:
    """Rename the exact open file relative to the exact held parent."""

    if os.name != "nt":
        raise WorkspaceBoundaryError(f"Windows exact rename is unavailable for {label}")

    import ctypes

    if not target_name:
        raise WorkspaceBoundaryError(f"cannot rename {label} to an empty name")
    target_path = target_name

    class FileRenameInfo(ctypes.Structure):
        _fields_ = [
            ("ReplaceIfExists", ctypes.c_ubyte),
            ("RootDirectory", ctypes.c_void_p),
            ("FileNameLength", ctypes.c_uint32),
            ("FileName", ctypes.c_wchar * (len(target_path) + 1)),
        ]

    class IoStatusBlock(ctypes.Structure):
        _fields_ = [("Status", ctypes.c_void_p), ("Information", ctypes.c_size_t)]

    native = _windows_descriptor_handle(descriptor)
    information = FileRenameInfo()
    information.ReplaceIfExists = int(replace)
    information.RootDirectory = ctypes.c_void_p(parent_handle)
    information.FileNameLength = len(target_path.encode("utf-16-le"))
    information.FileName = target_path
    status_block = IoStatusBlock()
    ntdll = ctypes.WinDLL("ntdll")
    set_information = ntdll.NtSetInformationFile
    set_information.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(IoStatusBlock),
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_int,
    ]
    set_information.restype = ctypes.c_long
    status = set_information(
        ctypes.c_void_p(native),
        ctypes.byref(status_block),
        ctypes.byref(information),
        ctypes.sizeof(information),
        10,
    )
    if status < 0:
        rtl_error = ntdll.RtlNtStatusToDosError
        rtl_error.argtypes = [ctypes.c_long]
        rtl_error.restype = ctypes.c_ulong
        error = int(rtl_error(status))
        raise WorkspaceBoundaryError(
            f"cannot rename {label} in exact parent {parent_path} by handle ({error})"
        )


def _delete_open_handle(handle: int, label: str) -> None:
    """Mark an open Windows filesystem handle for deletion."""

    if os.name != "nt":
        raise WorkspaceBoundaryError(f"handle-bound deletion is unavailable for {label}")

    import ctypes

    class FileDispositionInfo(ctypes.Structure):
        _fields_ = [("DeleteFile", ctypes.c_ubyte)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    set_file_information = kernel32.SetFileInformationByHandle
    set_file_information.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    set_file_information.restype = ctypes.c_int
    disposition = FileDispositionInfo(1)
    if not set_file_information(
        ctypes.c_void_p(handle),
        4,
        ctypes.byref(disposition),
        ctypes.sizeof(disposition),
    ):
        error = ctypes.get_last_error()
        raise WorkspaceBoundaryError(f"cannot remove {label} by handle ({error})")


def _delete_open_directory(handle: int, label: str) -> None:
    """Mark an open Windows directory handle for deletion."""

    _delete_open_handle(handle, label)


def _delete_open_file(handle: int, label: str) -> None:
    """Mark an open Windows regular-file handle for deletion."""

    _delete_open_handle(handle, label)


def _open_directory(path: Path, label: str) -> int:
    if os.name != "nt":
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            return os.open(os.fspath(path), flags)
        except OSError as error:
            raise WorkspaceBoundaryError(f"cannot open {label}: {path}") from error
    return _windows_create(
        path, 0x0001 | 0x0080, 0x0001 | 0x0002, 3, 0x02000000 | 0x00200000, label
    )


def _open_cleanup_directory(path: Path, label: str) -> int:
    """Open a Windows directory handle that can delete its exact object."""

    if os.name != "nt":
        raise WorkspaceBoundaryError(f"handle-bound directory deletion is unavailable for {label}")
    return _windows_create(
        path,
        0x0001 | 0x0080 | 0x00010000,
        0x0001 | 0x0002,
        3,
        0x02000000 | 0x00200000,
        label,
    )


def _open_cleanup_file(path: Path, label: str) -> int:
    """Open a Windows regular file for cleanup without sharing deletion."""

    if os.name != "nt":
        raise WorkspaceBoundaryError(f"handle-bound file deletion is unavailable for {label}")
    return _windows_create(
        path,
        0x00010000 | 0x00000080,
        0x0001 | 0x0002,
        3,
        0x00200000,
        label,
    )


def _open_append_file(path: Path, label: str) -> int:
    if os.name != "nt":
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        try:
            return os.open(os.fspath(path), flags, 0o666)
        except OSError as error:
            raise WorkspaceBoundaryError(f"cannot open {label}: {path}") from error

    import msvcrt

    handle_value = _windows_create(path, 0x0004 | 0x0080, 0x0003, 4, 0x00200000, label)
    try:
        return msvcrt.open_osfhandle(handle_value, os.O_WRONLY | os.O_APPEND | os.O_BINARY)
    except OSError as error:
        _close_handle(handle_value)
        raise WorkspaceBoundaryError(f"cannot open {label}: {path}") from error


def _windows_file_identity(handle: int, label: str) -> tuple[_IdentityStamp, bool, int]:
    """Read identity, type, and link count from an open Windows file handle."""

    if os.name != "nt":
        raise WorkspaceBoundaryError(f"Windows file identity is unavailable for {label}")

    import ctypes

    class FileTime(ctypes.Structure):
        _fields_ = [
            ("dwLowDateTime", ctypes.c_uint32),
            ("dwHighDateTime", ctypes.c_uint32),
        ]

    class ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", ctypes.c_uint32),
            ("ftCreationTime", FileTime),
            ("ftLastAccessTime", FileTime),
            ("ftLastWriteTime", FileTime),
            ("dwVolumeSerialNumber", ctypes.c_uint32),
            ("nFileSizeHigh", ctypes.c_uint32),
            ("nFileSizeLow", ctypes.c_uint32),
            ("nNumberOfLinks", ctypes.c_uint32),
            ("nFileIndexHigh", ctypes.c_uint32),
            ("nFileIndexLow", ctypes.c_uint32),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_file_information = kernel32.GetFileInformationByHandle
    get_file_information.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ByHandleFileInformation),
    ]
    get_file_information.restype = ctypes.c_int
    information = ByHandleFileInformation()
    if not get_file_information(ctypes.c_void_p(handle), ctypes.byref(information)):
        error = ctypes.get_last_error()
        raise WorkspaceBoundaryError(f"cannot inspect {label} handle ({error})")
    file_index = (int(information.nFileIndexHigh) << 32) | int(information.nFileIndexLow)
    stamp = (int(information.dwVolumeSerialNumber), file_index)
    return (
        stamp,
        bool(information.dwFileAttributes & 0x10),
        int(information.nNumberOfLinks),
    )


def _delete_checked_file(path: Path, metadata: os.stat_result, label: str) -> None:
    """Delete the exact checked regular file through a Windows handle."""

    if os.name != "nt":
        raise WorkspaceBoundaryError(
            f"cannot remove {label}: exact-object cleanup is unavailable on this platform"
        )

    expected_stamp = (int(metadata.st_dev) & 0xFFFFFFFF, int(metadata.st_ino))
    handle: int | None = None
    try:
        handle = _open_cleanup_file(path, label)
        actual_stamp, is_directory, link_count = _windows_file_identity(handle, label)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or int(metadata.st_nlink) != 1
            or is_directory
            or actual_stamp != expected_stamp
            or link_count != int(metadata.st_nlink)
        ):
            raise WorkspaceBoundaryError(f"cannot remove unowned {label}: {path}")
        _delete_open_file(handle, label)
    except WorkspaceBoundaryError:
        raise
    except OSError as error:
        raise WorkspaceBoundaryError(f"cannot remove {label}: {path}") from error
    finally:
        if handle is not None:
            _close_handle(handle)


@contextmanager
def _directory_guard(
    path: Path,
    expected: _IdentityStamp,
    label: str,
) -> Iterator[None]:
    checked = _reject_reparse_alias(path, label)
    handle = _open_directory(checked, label)
    try:
        _identity_stamp(checked, label, expected)
        yield
        _identity_stamp(checked, label, expected)
    finally:
        _close_handle(handle)


def _native_directory_identity(handle: int) -> _IdentityStamp:
    if os.name == "nt":
        stamp, is_directory, _ = _windows_file_identity(handle, "exact directory")
        if not is_directory:
            raise WorkspaceBoundaryError("exact directory owner is not a directory")
        return stamp
    try:
        metadata = os.fstat(handle)
    except OSError as error:
        raise WorkspaceBoundaryError("cannot inspect exact directory owner") from error
    if not stat.S_ISDIR(metadata.st_mode):
        raise WorkspaceBoundaryError("exact directory owner is not a directory")
    return (int(metadata.st_dev), int(metadata.st_ino))


def _expected_native_directory_identity(stamp: _IdentityStamp) -> _IdentityStamp:
    return (stamp[0] & 0xFFFFFFFF, stamp[1]) if os.name == "nt" else stamp


def _stable_file_state(metadata: os.stat_result) -> tuple[int, ...]:
    state = _file_state(metadata)
    return state[:6] if os.name == "nt" else state


def _open_exact_file_descriptor(
    parent_handle: int,
    parent_path: Path,
    name: str,
    *,
    flags: int,
    access: int,
    share: int,
    disposition: int,
    mode: int = 0o666,
) -> int:
    if os.name != "nt":
        return os.open(
            name,
            flags | getattr(os, "O_NOFOLLOW", 0),
            mode,
            dir_fd=parent_handle,
        )
    import ctypes
    import msvcrt

    class UnicodeString(ctypes.Structure):
        _fields_ = [
            ("Length", ctypes.c_ushort),
            ("MaximumLength", ctypes.c_ushort),
            ("Buffer", ctypes.c_wchar_p),
        ]

    class ObjectAttributes(ctypes.Structure):
        _fields_ = [
            ("Length", ctypes.c_ulong),
            ("RootDirectory", ctypes.c_void_p),
            ("ObjectName", ctypes.POINTER(UnicodeString)),
            ("Attributes", ctypes.c_ulong),
            ("SecurityDescriptor", ctypes.c_void_p),
            ("SecurityQualityOfService", ctypes.c_void_p),
        ]

    class IoStatusBlock(ctypes.Structure):
        _fields_ = [("Status", ctypes.c_void_p), ("Information", ctypes.c_size_t)]

    nt_disposition = {1: 2, 3: 1}.get(disposition)
    if nt_disposition is None:
        raise WorkspaceBoundaryError("unsupported exact file open disposition")
    name_buffer = ctypes.create_unicode_buffer(name)
    encoded_length = len(name.encode("utf-16-le"))
    unicode_name = UnicodeString(
        encoded_length,
        encoded_length + ctypes.sizeof(ctypes.c_wchar),
        ctypes.cast(name_buffer, ctypes.c_wchar_p),
    )
    attributes = ObjectAttributes(
        ctypes.sizeof(ObjectAttributes),
        ctypes.c_void_p(parent_handle),
        ctypes.pointer(unicode_name),
        0x00000040,
        None,
        None,
    )
    result = ctypes.c_void_p()
    status_block = IoStatusBlock()
    ntdll = ctypes.WinDLL("ntdll")
    create_file = ntdll.NtCreateFile
    create_file.argtypes = [
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_ulong,
        ctypes.POINTER(ObjectAttributes),
        ctypes.POINTER(IoStatusBlock),
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.c_ulong,
    ]
    create_file.restype = ctypes.c_long
    status = create_file(
        ctypes.byref(result),
        access | 0x00100000,
        ctypes.byref(attributes),
        ctypes.byref(status_block),
        None,
        0x00000080,
        share,
        nt_disposition,
        0x00000020 | 0x00000040 | 0x00200000,
        None,
        0,
    )
    if status < 0 or result.value is None:
        rtl_error = ntdll.RtlNtStatusToDosError
        rtl_error.argtypes = [ctypes.c_long]
        rtl_error.restype = ctypes.c_ulong
        error = int(rtl_error(status))
        raise ctypes.WinError(error, f"cannot open exact file: {parent_path / name}")
    native = int(result.value)
    try:
        return msvcrt.open_osfhandle(native, flags | os.O_BINARY)
    except OSError:
        _close_handle(native)
        raise


def _create_exact_directory(
    parent_handle: int,
    parent_path: Path,
    name: str,
    label: str,
) -> int:
    """Create and return a child directory relative to one held exact parent."""

    if os.name != "nt":
        try:
            os.mkdir(name, dir_fd=parent_handle)
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
            return os.open(name, flags, dir_fd=parent_handle)
        except OSError:
            raise

    import ctypes

    class UnicodeString(ctypes.Structure):
        _fields_ = [
            ("Length", ctypes.c_ushort),
            ("MaximumLength", ctypes.c_ushort),
            ("Buffer", ctypes.c_wchar_p),
        ]

    class ObjectAttributes(ctypes.Structure):
        _fields_ = [
            ("Length", ctypes.c_ulong),
            ("RootDirectory", ctypes.c_void_p),
            ("ObjectName", ctypes.POINTER(UnicodeString)),
            ("Attributes", ctypes.c_ulong),
            ("SecurityDescriptor", ctypes.c_void_p),
            ("SecurityQualityOfService", ctypes.c_void_p),
        ]

    class IoStatusBlock(ctypes.Structure):
        _fields_ = [("Status", ctypes.c_void_p), ("Information", ctypes.c_size_t)]

    name_buffer = ctypes.create_unicode_buffer(name)
    encoded_length = len(name.encode("utf-16-le"))
    unicode_name = UnicodeString(
        encoded_length,
        encoded_length + ctypes.sizeof(ctypes.c_wchar),
        ctypes.cast(name_buffer, ctypes.c_wchar_p),
    )
    attributes = ObjectAttributes(
        ctypes.sizeof(ObjectAttributes),
        ctypes.c_void_p(parent_handle),
        ctypes.pointer(unicode_name),
        0x00000040,
        None,
        None,
    )
    result = ctypes.c_void_p()
    status_block = IoStatusBlock()
    ntdll = ctypes.WinDLL("ntdll")
    create_file = ntdll.NtCreateFile
    create_file.argtypes = [
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_ulong,
        ctypes.POINTER(ObjectAttributes),
        ctypes.POINTER(IoStatusBlock),
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.c_ulong,
    ]
    create_file.restype = ctypes.c_long
    status = create_file(
        ctypes.byref(result),
        0x0001 | 0x0080 | 0x00100000,
        ctypes.byref(attributes),
        ctypes.byref(status_block),
        None,
        0x00000010,
        0x0001 | 0x0002,
        2,
        0x00000001 | 0x00000020 | 0x00200000,
        None,
        0,
    )
    if status < 0 or result.value is None:
        rtl_error = ntdll.RtlNtStatusToDosError
        rtl_error.argtypes = [ctypes.c_long]
        rtl_error.restype = ctypes.c_ulong
        error = int(rtl_error(status))
        raise ctypes.WinError(error, f"cannot create {label}: {parent_path / name}")
    return int(result.value)


def _open_exact_cleanup_directory(
    parent_handle: int,
    parent_path: Path,
    name: str,
    label: str,
) -> int:
    """Open one exact child directory for deletion through its held parent."""

    if os.name != "nt":
        raise WorkspaceBoundaryError(f"exact directory cleanup is unavailable for {label}")

    import ctypes

    class UnicodeString(ctypes.Structure):
        _fields_ = [
            ("Length", ctypes.c_ushort),
            ("MaximumLength", ctypes.c_ushort),
            ("Buffer", ctypes.c_wchar_p),
        ]

    class ObjectAttributes(ctypes.Structure):
        _fields_ = [
            ("Length", ctypes.c_ulong),
            ("RootDirectory", ctypes.c_void_p),
            ("ObjectName", ctypes.POINTER(UnicodeString)),
            ("Attributes", ctypes.c_ulong),
            ("SecurityDescriptor", ctypes.c_void_p),
            ("SecurityQualityOfService", ctypes.c_void_p),
        ]

    class IoStatusBlock(ctypes.Structure):
        _fields_ = [("Status", ctypes.c_void_p), ("Information", ctypes.c_size_t)]

    name_buffer = ctypes.create_unicode_buffer(name)
    encoded_length = len(name.encode("utf-16-le"))
    unicode_name = UnicodeString(
        encoded_length,
        encoded_length + ctypes.sizeof(ctypes.c_wchar),
        ctypes.cast(name_buffer, ctypes.c_wchar_p),
    )
    attributes = ObjectAttributes(
        ctypes.sizeof(ObjectAttributes),
        ctypes.c_void_p(parent_handle),
        ctypes.pointer(unicode_name),
        0x00000040,
        None,
        None,
    )
    result = ctypes.c_void_p()
    status_block = IoStatusBlock()
    ntdll = ctypes.WinDLL("ntdll")
    open_file = ntdll.NtOpenFile
    open_file.argtypes = [
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_ulong,
        ctypes.POINTER(ObjectAttributes),
        ctypes.POINTER(IoStatusBlock),
        ctypes.c_ulong,
        ctypes.c_ulong,
    ]
    open_file.restype = ctypes.c_long
    status = open_file(
        ctypes.byref(result),
        0x0001 | 0x0080 | 0x00010000 | 0x00100000,
        ctypes.byref(attributes),
        ctypes.byref(status_block),
        0x0001 | 0x0002,
        0x00000001 | 0x00000020 | 0x00200000,
    )
    if status < 0 or result.value is None:
        rtl_error = ntdll.RtlNtStatusToDosError
        rtl_error.argtypes = [ctypes.c_long]
        rtl_error.restype = ctypes.c_ulong
        error = int(rtl_error(status))
        raise ctypes.WinError(error, f"cannot open {label}: {parent_path / name}")
    return int(result.value)


@dataclass(slots=True)
class _ExactOwner:
    """One close-once owner that cannot later close a reused foreign value."""

    handle: int
    expected: tuple[int, ...]
    identity_of: Callable[[int], tuple[int, ...]]
    close_handle: Callable[[int], None]
    label: str
    released: bool = False
    indeterminate: bool = False

    def _probe(self) -> tuple[int, ...] | BaseException:
        try:
            return self.identity_of(self.handle)
        except BaseException as error:
            return error

    def validate(self) -> None:
        if self.released:
            raise WorkspaceBoundaryError(f"{self.label} is already released")
        current = self._probe()
        if isinstance(current, BaseException):
            raise WorkspaceBoundaryError(f"cannot inspect {self.label}") from current
        if current != self.expected:
            raise WorkspaceBoundaryError(f"{self.label} identity changed")

    def close(self) -> None:
        if self.released:
            return
        if self.indeterminate:
            current = self._probe()
            if not isinstance(current, BaseException) and current != self.expected:
                self.released = True
                raise WorkspaceBoundaryError(
                    f"{self.label} cleanup ownership changed; foreign value retained"
                )
            cause = current if isinstance(current, BaseException) else None
            raise WorkspaceBoundaryError(
                f"{self.label} cleanup remains indeterminate; numeric value retained"
            ) from cause
        try:
            self.close_handle(self.handle)
        except BaseException as error:
            current = self._probe()
            if not isinstance(current, BaseException) and current != self.expected:
                self.released = True
                detail = "ownership changed; foreign value retained"
            else:
                self.indeterminate = True
                detail = "is indeterminate; numeric value retained"
            raise WorkspaceBoundaryError(f"{self.label} cleanup {detail}") from error
        self.released = True


type _AttemptRootKey = tuple[Path, _IdentityStamp, str]
type _AttemptRootClaim = tuple[Path, _IdentityStamp, _ExactOwner]


def _release_attempt_root_claim(
    key: _AttemptRootKey,
    claim: _AttemptRootClaim,
) -> None:
    owner = claim[2]
    owner.close()
    if _ATTEMPT_ROOT_STAMPS.get(key) is claim:
        del _ATTEMPT_ROOT_STAMPS[key]


def _register_attempt_root_claim(
    key: _AttemptRootKey,
    path: Path,
    created_stamp: _IdentityStamp,
    created_owner: _ExactOwner,
) -> None:
    existing = cast(_AttemptRootClaim | None, _ATTEMPT_ROOT_STAMPS.get(key))
    if existing is not None:
        _release_attempt_root_claim(key, existing)
    while len(_ATTEMPT_ROOT_STAMPS) >= _ATTEMPT_ROOT_STAMP_LIMIT:
        oldest_key = next(iter(_ATTEMPT_ROOT_STAMPS))
        oldest = cast(_AttemptRootClaim, _ATTEMPT_ROOT_STAMPS[oldest_key])
        _release_attempt_root_claim(oldest_key, oldest)

    created_owner.validate()
    handle = _open_directory(path, "attempt creation identity")
    claim = _ExactOwner(
        handle,
        created_owner.expected,
        _native_directory_identity,
        lambda value: _close_handle(value),
        "attempt creation identity",
    )
    try:
        claim.validate()
    except BaseException as primary:
        try:
            claim.close()
        except BaseException as cleanup:
            _ATTEMPT_ROOT_STAMPS[key] = (path, created_stamp, claim)
            primary.add_note(f"attempt creation claim cleanup failed: {cleanup}")
        raise
    _ATTEMPT_ROOT_STAMPS[key] = (path, created_stamp, claim)


class _ExactCaseTransaction:
    """Hold and operate below one exact registered case for a full transaction."""

    __slots__ = (
        "case",
        "root",
        "_root_stamp",
        "_directories",
        "_files",
        "_owners",
        "_entered",
    )

    def __init__(self, case: CaseWorkspace) -> None:
        case = _require_registered_case(case)
        self.case = case
        self.root = case.case_root
        self._root_stamp = _registered_case_stamp(case)
        self._directories: dict[tuple[str, ...], _ExactOwner] = {}
        self._files: dict[str, _ExactOwner] = {}
        self._owners: list[_ExactOwner] = []
        self._entered = False

    @property
    def root_handle(self) -> int:
        self._validate_root()
        return self._directories[()].handle

    def __enter__(self) -> _ExactCaseTransaction:
        if self._entered:
            raise WorkspaceBoundaryError("exact case transaction cannot be re-entered")
        self._entered = True
        handle: int | None = None
        try:
            handle = _open_directory(self.root, "exact case root")
            owner = _ExactOwner(
                handle,
                _expected_native_directory_identity(self._root_stamp),
                _native_directory_identity,
                lambda value: _close_handle(value),
                "exact case root",
            )
            self._owners.append(owner)
            handle = None
            owner.validate()
            self._directories[()] = owner
            self._validate_root()
            return self
        except BaseException as primary:
            if handle is not None:
                try:
                    _close_handle(handle)
                except BaseException as cleanup:
                    primary.add_note(f"exact case enter cleanup failed: {cleanup}")
            self._close_preserving(primary)
            raise

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> Literal[False]:
        del exc_type, traceback
        primary = exc_value
        if primary is None:
            try:
                self.validate()
            except BaseException as error:
                primary = error
        self._close_preserving(primary)
        if exc_value is not None:
            return False
        if primary is not None:
            raise primary
        return False

    def close(self) -> None:
        self._close_preserving(None)

    def _close_preserving(self, primary: BaseException | None) -> None:
        errors: list[BaseException] = []
        for owner in reversed(self._owners):
            try:
                owner.close()
            except BaseException as error:
                errors.append(error)
        if not errors:
            return
        detail = "; ".join(str(error) for error in errors)
        if primary is not None:
            primary.add_note(f"exact transaction cleanup failed: {detail}")
            return
        raise WorkspaceBoundaryError(f"exact transaction cleanup failed: {detail}") from errors[0]

    @staticmethod
    def _parts(relative_path: str | Path, label: str) -> tuple[str, ...]:
        relative = Path(relative_path)
        _reject_parent_segments(relative, label)
        if relative.is_absolute() or relative.anchor or not relative.parts:
            raise WorkspaceBoundaryError(f"{label} must be a non-empty relative path")
        for segment in relative.parts:
            _validate_segment(segment, label)
        return tuple(relative.parts)

    def _validate_root(self) -> None:
        root_owner = self._directories.get(())
        if root_owner is None:
            raise WorkspaceBoundaryError("exact case transaction is not entered")
        root_owner.validate()
        registration = _CASE_REGISTRY.get(id(self.case))
        if (
            registration is None
            or registration[0] is not self.case
            or registration[3] != self.root
            or registration[4] != self._root_stamp
        ):
            raise WorkspaceBoundaryError("exact case authority binding changed")
        _identity_stamp(self.root, "exact case root", self._root_stamp)

    def _entry_state(
        self,
        parent_parts: tuple[str, ...],
        name: str,
        label: str,
    ) -> os.stat_result:
        parent = self._directory(parent_parts)
        parent_path = self.root.joinpath(*parent_parts)
        try:
            if os.name == "nt":
                metadata = os.lstat(os.fspath(parent_path / name))
            else:
                metadata = os.stat(
                    name,
                    dir_fd=parent.handle,
                    follow_symlinks=False,
                )
        except OSError as error:
            raise WorkspaceBoundaryError(f"cannot inspect {label}") from error
        if stat.S_ISLNK(metadata.st_mode) or bool(
            getattr(metadata, "st_file_attributes", 0) & _REPARSE_POINT
        ):
            raise WorkspaceBoundaryError(f"{label} cannot be a reparse-point alias")
        return metadata

    def _verify_file(
        self, parts: tuple[str, ...], owner: _ExactOwner, label: str
    ) -> tuple[int, ...]:
        current = _stable_file_state(self._entry_state(parts[:-1], parts[-1], label))
        if current != owner.expected:
            raise WorkspaceBoundaryError(f"{label} identity changed")
        return current

    def _directory(self, parts: tuple[str, ...]) -> _ExactOwner:
        self._validate_root()
        current_parts: tuple[str, ...] = ()
        for segment in parts:
            parent_parts = current_parts
            current_parts = (*current_parts, segment)
            existing = self._directories.get(current_parts)
            if existing is not None:
                existing.validate()
                if os.name == "nt":
                    metadata = self._entry_state(parent_parts, segment, "exact directory")
                    identity = (int(metadata.st_dev) & 0xFFFFFFFF, int(metadata.st_ino))
                    if identity != existing.expected:
                        raise WorkspaceBoundaryError("exact directory path was replaced")
                continue
            parent = self._directories[parent_parts]
            parent_path = self.root.joinpath(*parent_parts)
            metadata = self._entry_state(parent_parts, segment, "exact directory")
            if not stat.S_ISDIR(metadata.st_mode):
                raise WorkspaceBoundaryError("exact directory path is not a directory")
            path = parent_path / segment
            try:
                if os.name == "nt":
                    handle = _open_directory(path, "exact directory")
                else:
                    flags = (
                        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
                    )
                    handle = os.open(segment, flags, dir_fd=parent.handle)
            except OSError as error:
                raise WorkspaceBoundaryError("cannot open exact directory") from error
            owner = _ExactOwner(
                handle,
                _expected_native_directory_identity((int(metadata.st_dev), int(metadata.st_ino))),
                _native_directory_identity,
                lambda value: _close_handle(value),
                "exact directory",
            )
            self._owners.append(owner)
            owner.validate()
            if os.name == "nt":
                current = self._entry_state(parent_parts, segment, "exact directory")
                if (int(current.st_dev) & 0xFFFFFFFF, int(current.st_ino)) != owner.expected:
                    raise WorkspaceBoundaryError("exact directory changed while opening")
            self._directories[current_parts] = owner
        return self._directories[parts]

    def _own_file(self, descriptor: int, label: str) -> _ExactOwner:
        owner = _ExactOwner(
            descriptor,
            _stable_file_state(os.fstat(descriptor)),
            lambda value: _stable_file_state(os.fstat(value)),
            lambda value: os.close(value),
            label,
        )
        self._owners.append(owner)
        owner.validate()
        return owner

    def _new_file(self, parts: tuple[str, ...], label: str, mode: int = 0o666) -> _ExactOwner:
        parent = self._directory(parts[:-1])
        parent_path = self.root.joinpath(*parts[:-1])
        try:
            descriptor = _open_exact_file_descriptor(
                parent.handle,
                parent_path,
                parts[-1],
                flags=os.O_RDWR | os.O_CREAT | os.O_EXCL,
                access=0xC0010000,
                share=0x0001 | 0x0002 | 0x0004,
                disposition=1,
                mode=mode,
            )
        except OSError as error:
            raise WorkspaceBoundaryError(f"cannot create {label}") from error
        return self._own_file(descriptor, label)

    @staticmethod
    def _write(owner: _ExactOwner, data: bytes) -> None:
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(owner.handle, view) :]
            os.fsync(owner.handle)
        except OSError as error:
            raise WorkspaceBoundaryError(f"cannot write {owner.label}") from error
        owner.expected = _stable_file_state(os.fstat(owner.handle))
        owner.validate()

    def _discard(
        self,
        parts: tuple[str, ...],
        owner: _ExactOwner,
        primary: BaseException,
    ) -> None:
        try:
            if os.name != "nt":
                owner.validate()
                raise WorkspaceBoundaryError("exact-object cleanup is unavailable on this platform")
            self._directory(parts[:-1])
            owner.validate()
            if owner.expected[3] != 1:
                raise WorkspaceBoundaryError(f"{owner.label} cleanup object is not singly linked")
            _delete_open_file(_windows_descriptor_handle(owner.handle), owner.label)
        except BaseException as cleanup:
            primary.add_note(f"{owner.label} cleanup failed: {cleanup}")

    def _open_file(self, parts: tuple[str, ...], *, append: bool = False) -> _ExactOwner:
        key = Path(*parts).as_posix()
        existing = self._files.get(key)
        if existing is not None and not existing.released:
            existing.validate()
            self._verify_file(parts, existing, "exact file")
            if not append:
                return existing

        parent = self._directory(parts[:-1])
        parent_path = self.root.joinpath(*parts[:-1])
        metadata = self._entry_state(parts[:-1], parts[-1], "exact file")
        initial = _stable_file_state(metadata)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise WorkspaceBoundaryError("exact file must be singly-linked and regular")
        try:
            flags = os.O_RDWR | os.O_APPEND if append else os.O_RDONLY
            handle = _open_exact_file_descriptor(
                parent.handle,
                parent_path,
                parts[-1],
                flags=flags,
                access=0x80000000 | (0x0004 if append else 0),
                share=0x0001 | 0x0002,
                disposition=3,
            )
        except OSError as error:
            raise WorkspaceBoundaryError("cannot open exact file") from error
        owner = self._own_file(handle, "exact file")
        if owner.expected != initial:
            raise WorkspaceBoundaryError("exact file changed while opening")
        self._verify_file(parts, owner, "exact file")
        self._files[key] = owner
        return owner

    def _windows_replacement_target(
        self,
        parts: tuple[str, ...],
        existing: _ExactOwner,
    ) -> _ExactOwner:
        """Conditionally reacquire the validated target with rename authority."""

        expected = existing.expected
        existing.close()
        parent = self._directory(parts[:-1])
        parent_path = self.root.joinpath(*parts[:-1])
        try:
            descriptor = _open_exact_file_descriptor(
                parent.handle,
                parent_path,
                parts[-1],
                flags=os.O_RDONLY,
                access=0x80000000 | 0x00010000,
                share=0x0001 | 0x0002,
                disposition=3,
            )
        except OSError as error:
            raise WorkspaceBoundaryError("cannot reacquire exact replacement target") from error
        owner = _ExactOwner(
            descriptor,
            expected,
            lambda value: _stable_file_state(os.fstat(value)),
            lambda value: os.close(value),
            "exact replacement target",
        )
        self._owners.append(owner)
        owner.validate()
        self._verify_file(parts, owner, "exact replacement target")
        self._files[Path(*parts).as_posix()] = owner
        return owner

    def _windows_committed_target(
        self,
        parts: tuple[str, ...],
        committed: _ExactOwner,
    ) -> _ExactOwner:
        """Conditionally guard the committed object without sharing deletion."""

        expected = committed.expected
        committed.close()
        parent = self._directory(parts[:-1])
        parent_path = self.root.joinpath(*parts[:-1])
        try:
            descriptor = _open_exact_file_descriptor(
                parent.handle,
                parent_path,
                parts[-1],
                flags=os.O_RDONLY,
                access=0x80000000,
                share=0x0001 | 0x0002,
                disposition=3,
            )
        except OSError as error:
            raise WorkspaceBoundaryError("cannot guard exact committed target") from error
        owner = _ExactOwner(
            descriptor,
            expected,
            lambda value: _stable_file_state(os.fstat(value)),
            lambda value: os.close(value),
            "exact committed target",
        )
        self._owners.append(owner)
        owner.validate()
        self._verify_file(parts, owner, "exact committed target")
        self._files[Path(*parts).as_posix()] = owner
        return owner

    def read_bytes(self, relative_path: str | Path) -> bytes:
        parts = self._parts(relative_path, "exact read")
        owner = self._open_file(parts)
        try:
            os.lseek(owner.handle, 0, os.SEEK_SET)
            chunks: list[bytes] = []
            while chunk := os.read(owner.handle, 1024 * 1024):
                chunks.append(chunk)
        except OSError as error:
            raise WorkspaceBoundaryError("cannot read exact file") from error
        owner.validate()
        self._verify_file(parts, owner, "exact file read")
        return b"".join(chunks)

    def digest(self, relative_path: str | Path) -> str:
        return hashlib.sha256(self.read_bytes(relative_path)).hexdigest()

    def identity(self, relative_path: str | Path) -> tuple[int, int, int, int]:
        parts = self._parts(relative_path, "exact identity")
        owner = self._open_file(parts)
        state = owner.expected
        self._verify_file(parts, owner, "exact file")
        return (state[0], state[1], state[4], state[5])

    def append_bytes(self, relative_path: str | Path, data: bytes) -> None:
        parts = self._parts(relative_path, "exact append")
        owner = self._open_file(parts, append=True)
        self._write(owner, data)
        for held in self._owners:
            if not held.released and held.expected[:2] == owner.expected[:2]:
                held.expected = owner.expected
        self._verify_file(parts, owner, "exact append target")
        key = Path(*parts).as_posix()
        self._files[key] = owner

    def list_directory(self, relative_path: str | Path) -> tuple[str, ...]:
        parts = self._parts(relative_path, "exact directory listing")
        owner = self._directory(parts)
        path = self.root.joinpath(*parts)
        try:
            names = os.listdir(path) if os.name == "nt" else os.listdir(owner.handle)
        except OSError as error:
            raise WorkspaceBoundaryError("cannot list exact directory") from error
        owner.validate()
        return tuple(sorted(names))

    def make_directory(self, relative_path: str | Path) -> _IdentityStamp:
        parts = self._parts(relative_path, "exact directory creation")
        parent = self._directory(parts[:-1])
        parent_path = self.root.joinpath(*parts[:-1])
        handle: int | None = None
        try:
            parent.validate()
            handle = _create_exact_directory(
                parent.handle,
                parent_path,
                parts[-1],
                "exact directory",
            )
        except FileExistsError:
            raise
        except OSError as error:
            raise WorkspaceBoundaryError("cannot create exact directory") from error
        try:
            owner = _ExactOwner(
                handle,
                _native_directory_identity(handle),
                _native_directory_identity,
                lambda value: _close_handle(value),
                "exact directory",
            )
            self._owners.append(owner)
            handle = None
            owner.validate()
            parent.validate()
            self._directories[parts] = owner
        except BaseException as primary:
            if handle is not None:
                try:
                    _close_handle(handle)
                except BaseException as cleanup:
                    primary.add_note(f"exact directory cleanup failed: {cleanup}")
            raise
        metadata = self._entry_state(parts[:-1], parts[-1], "created exact directory")
        created_stamp = (int(metadata.st_dev), int(metadata.st_ino))
        owner = self._directory(parts)
        owner.validate()
        if owner.expected != _expected_native_directory_identity(created_stamp):
            raise WorkspaceBoundaryError("created exact directory was substituted")
        self._validate_root()
        if len(parts) == 3 and parts[:2] == (_TEMPORARY_ROOT, "attempts"):
            created_path = self.root.joinpath(*parts)
            _register_attempt_root_claim(
                (self.root, self._root_stamp, parts[2]),
                created_path,
                created_stamp,
                owner,
            )
        return created_stamp

    def remove_empty_directory(self, relative_path: str | Path) -> None:
        """Remove one exact empty directory without a pathname deletion fallback."""

        parts = self._parts(relative_path, "exact empty directory cleanup")
        if os.name != "nt":
            raise WorkspaceBoundaryError(
                "exact empty directory deletion is unavailable on this platform"
            )
        parent = self._directory(parts[:-1])
        current = self._directory(parts)
        expected = current.expected
        if len(parts) == 3 and parts[:2] == (_TEMPORARY_ROOT, "attempts"):
            claim_key = (self.root, self._root_stamp, parts[2])
            claim = cast(_AttemptRootClaim | None, _ATTEMPT_ROOT_STAMPS.get(claim_key))
            if claim is None:
                raise WorkspaceBoundaryError("pending attempt cleanup authority is unavailable")
            if (
                claim[0] != self.root.joinpath(*parts)
                or _expected_native_directory_identity(claim[1]) != expected
                or claim[2].expected != expected
            ):
                raise WorkspaceBoundaryError("pending attempt cleanup authority changed")
            claim[2].validate()
            _release_attempt_root_claim(claim_key, claim)

        current.close()
        self._directories.pop(parts, None)
        parent_path = self.root.joinpath(*parts[:-1])
        try:
            handle = _open_exact_cleanup_directory(
                parent.handle,
                parent_path,
                parts[-1],
                "exact empty directory",
            )
        except OSError as error:
            raise WorkspaceBoundaryError("cannot open exact empty directory") from error
        owner = _ExactOwner(
            handle,
            expected,
            _native_directory_identity,
            lambda value: _close_handle(value),
            "exact empty directory",
        )
        self._owners.append(owner)
        owner.validate()
        metadata = self._entry_state(parts[:-1], parts[-1], "exact empty directory")
        actual = (int(metadata.st_dev) & 0xFFFFFFFF, int(metadata.st_ino))
        if actual != expected:
            raise WorkspaceBoundaryError("exact empty directory identity changed")
        _delete_open_directory(handle, "exact empty directory")
        owner.close()

    def ensure_directories(self, relative_path: str | Path) -> None:
        parts = self._parts(relative_path, "exact directory parents")
        for index in range(1, len(parts) + 1):
            current = parts[:index]
            try:
                self._directory(current)
            except WorkspaceBoundaryError as error:
                if not isinstance(error.__cause__, FileNotFoundError):
                    raise
                self.make_directory(Path(*current))

    def exists(self, relative_path: str | Path) -> bool:
        parts = self._parts(relative_path, "exact existence check")
        try:
            self._entry_state(parts[:-1], parts[-1], "exact existence check")
        except WorkspaceBoundaryError as error:
            if isinstance(error.__cause__, FileNotFoundError):
                return False
            raise
        return True

    def ensure_file(self, relative_path: str | Path) -> None:
        parts = self._parts(relative_path, "exact file marker")
        try:
            self._open_file(parts)
            return
        except WorkspaceBoundaryError as error:
            if not isinstance(error.__cause__, FileNotFoundError):
                raise
        try:
            owner = self._new_file(parts, "exact file marker")
        except WorkspaceBoundaryError as error:
            if isinstance(error.__cause__, FileExistsError):
                self._open_file(parts)
                return
            raise
        key = Path(*parts).as_posix()
        self._files[key] = owner
        self._verify_file(parts, owner, "exact file marker")

    def copy_create_new(
        self,
        source: str | Path,
        destination: str | Path,
        expected_sha256: str,
    ) -> Path:
        source_data = self.read_bytes(source)
        if hashlib.sha256(source_data).hexdigest() != expected_sha256:
            raise WorkspaceBoundaryError("exact copy source digest changed")
        parts = self._parts(destination, "exact copy destination")
        if self.exists(destination):
            raise FileExistsError(self.root.joinpath(*parts))
        parent_path = self.root.joinpath(*parts[:-1])
        owner = self._new_file(parts, "exact copy destination")
        key = Path(*parts).as_posix()
        self._files[key] = owner
        try:
            self._write(owner, source_data)
            self._verify_file(parts, owner, "exact copy destination")
            if hashlib.sha256(self.read_bytes(destination)).hexdigest() != expected_sha256:
                raise WorkspaceBoundaryError("exact copy digest changed")
            return parent_path / parts[-1]
        except BaseException as primary:
            self._discard(parts, owner, primary)
            raise

    def replace_bytes(self, relative_path: str | Path, data: bytes) -> None:
        parts = self._parts(relative_path, "exact replacement")
        if os.name != "nt":
            raise WorkspaceBoundaryError(
                "exact namespace replacement is unavailable on this platform"
            )
        parent = self._directory(parts[:-1])
        parent_path = self.root.joinpath(*parts[:-1])
        key = Path(*parts).as_posix()
        existing: _ExactOwner | None
        try:
            existing = self._open_file(parts)
            mode = stat.S_IMODE(
                self._entry_state(parts[:-1], parts[-1], "exact replacement").st_mode
            )
        except WorkspaceBoundaryError as error:
            if not isinstance(error.__cause__, FileNotFoundError):
                raise
            existing = None
            mode = 0o666
        if os.name == "nt" and existing is not None:
            existing = self._windows_replacement_target(parts, existing)

        temporary_name = f".{parts[-1]}.{secrets.token_hex(16)}"
        temporary = self._new_file(
            (*parts[:-1], temporary_name), "exact replacement temporary", mode
        )
        descriptor = temporary.handle
        replacement = _ExactReplacementState()
        try:
            self._write(temporary, data)
            _set_open_file_mode(descriptor, mode, "exact replacement temporary")
            temporary.expected = _stable_file_state(os.fstat(descriptor))
            temporary.validate()
            temporary_parts = (*parts[:-1], temporary_name)
            self._verify_file(temporary_parts, temporary, "exact replacement temporary")
            if existing is not None:
                existing.validate()
                self._verify_file(parts, existing, "exact replacement target")
            self._validate_root()
            _replace_exact_entry(
                parent=parent,
                parent_path=parent_path,
                temporary_name=temporary_name,
                target_name=parts[-1],
                temporary=temporary,
                existing=existing,
                target_existed=existing is not None,
                state=replacement,
            )
            temporary.expected = _stable_file_state(os.fstat(descriptor))
            self._verify_file(parts, temporary, "exact replacement result")
            committed = temporary
            if os.name == "nt":
                committed = self._windows_committed_target(parts, temporary)
            self._files[key] = committed
            if replacement.previous_name is not None and existing is not None:
                existing.validate()
                _delete_open_file(
                    _windows_descriptor_handle(existing.handle),
                    "exact replacement prior target",
                )
                replacement.previous_delete_pending = True
                existing.expected = _stable_file_state(os.fstat(existing.handle))
                existing.validate()
                existing.close()
                replacement.previous_name = None
        except BaseException as error:
            primary = (
                WorkspaceBoundaryError("cannot replace exact file")
                if isinstance(error, OSError)
                else error
            )
            discard_temporary = not replacement.new_committed
            if discard_temporary and replacement.previous_name is not None and existing is not None:
                discard_temporary = _restore_windows_previous(
                    parent=parent,
                    parent_path=parent_path,
                    target_name=parts[-1],
                    existing=existing,
                    state=replacement,
                    primary=primary,
                )
            if discard_temporary:
                self._discard((*parts[:-1], temporary_name), temporary, primary)
            elif replacement.new_committed:
                primary.add_note("exact replacement committed target retained after failure")
            else:
                primary.add_note(
                    "exact replacement temporary retained because prior target restoration failed"
                )
            if primary is error:
                raise
            raise primary from error

    def validate(self) -> None:
        self._validate_root()
        for owner in self._owners:
            if not owner.released:
                owner.validate()
        for key, owner in self._files.items():
            if owner.released or self._files.get(key) is not owner:
                continue
            parts = tuple(Path(key).parts)
            self._verify_file(parts, owner, "exact file")


@dataclass(slots=True)
class _ExactReplacementState:
    previous_name: str | None = None
    new_committed: bool = False
    previous_delete_pending: bool = False


def _replace_exact_entry(
    *,
    parent: _ExactOwner,
    parent_path: Path,
    temporary_name: str,
    target_name: str,
    temporary: _ExactOwner,
    existing: _ExactOwner | None,
    target_existed: bool,
    state: _ExactReplacementState,
) -> None:
    """Replace through the held parent while retaining the temporary owner."""

    parent.validate()
    temporary.validate()
    if os.name != "nt":
        raise WorkspaceBoundaryError("exact namespace replacement is unavailable on this platform")
    del temporary_name
    if existing is None:
        _windows_rename_open_file(
            temporary.handle,
            parent.handle,
            parent_path,
            target_name,
            replace=False,
            label="exact replacement temporary",
        )
        state.new_committed = True
        return
    previous_name = f".{target_name}.previous.{secrets.token_hex(16)}"
    existing.validate()
    _windows_rename_open_file(
        existing.handle,
        parent.handle,
        parent_path,
        previous_name,
        replace=False,
        label="exact replacement prior target",
    )
    state.previous_name = previous_name
    try:
        _windows_rename_open_file(
            temporary.handle,
            parent.handle,
            parent_path,
            target_name,
            replace=False,
            label="exact replacement temporary",
        )
        state.new_committed = True
    except BaseException as primary:
        _restore_windows_previous(
            parent=parent,
            parent_path=parent_path,
            target_name=target_name,
            existing=existing,
            state=state,
            primary=primary,
        )
        raise


def _restore_windows_previous(
    *,
    parent: _ExactOwner,
    parent_path: Path,
    target_name: str,
    existing: _ExactOwner,
    state: _ExactReplacementState,
    primary: BaseException,
) -> bool:
    if state.previous_name is None:
        return True
    try:
        parent.validate()
        existing.validate()
        _windows_rename_open_file(
            existing.handle,
            parent.handle,
            parent_path,
            target_name,
            replace=False,
            label="exact replacement prior target recovery",
        )
    except BaseException as cleanup:
        primary.add_note(f"exact replacement prior target recovery failed: {cleanup}")
        return False
    state.previous_name = None
    return True


@contextmanager
def _parent_guard(
    root: Path,
    root_stamp: _IdentityStamp,
    parent: Path,
    label: str,
    cleanup: Callable[[_IdentityStamp], None] | None = None,
) -> Iterator[None]:
    if not parent.is_relative_to(root):
        raise WorkspaceBoundaryError(f"{label} parent is outside the owned root")

    with ExitStack() as stack:
        stack.enter_context(_directory_guard(root, root_stamp, "owned root"))
        current = root
        for segment in parent.relative_to(root).parts:
            current /= segment
            with suppress(FileExistsError):
                current.mkdir()
            current_stamp = _identity_stamp(current, f"{label} parent")
            stack.enter_context(_directory_guard(current, current_stamp, "parent"))
        if cleanup is not None:
            stack.callback(lambda: cleanup(current_stamp))
        yield


def _remove_created_tree_contents(
    path: Path,
    expected: _IdentityStamp,
    label: str,
    *,
    already_guarded: bool = False,
) -> None:
    """Remove only the guarded contents of a newly-created directory tree."""

    if os.name != "nt":
        raise WorkspaceBoundaryError(
            f"cannot remove {label}: exact-object cleanup is unavailable on this platform"
        )

    checked = _reject_reparse_alias(path, label)
    handle: int | None = None
    if not already_guarded:
        handle = _open_directory(checked, label)
    try:
        _identity_stamp(checked, label, expected)
        try:
            entries = tuple(os.scandir(os.fspath(checked)))
        except OSError as error:
            raise WorkspaceBoundaryError(f"cannot inspect {label}: {checked}") from error

        for entry in entries:
            _identity_stamp(checked, label, expected)
            child = Path(entry.path)
            child_label = f"{label} child"
            _reject_reparse_alias(child, child_label)
            try:
                metadata = os.lstat(os.fspath(child))
            except OSError as error:
                raise WorkspaceBoundaryError(f"cannot inspect {child_label}: {child}") from error

            if stat.S_ISDIR(metadata.st_mode):
                child_stamp = (int(metadata.st_dev), int(metadata.st_ino))
                child_handle = _open_cleanup_directory(child, child_label)
                try:
                    _remove_created_tree_contents(
                        child,
                        child_stamp,
                        child_label,
                        already_guarded=True,
                    )
                    _identity_stamp(child, child_label, child_stamp)
                    _delete_open_directory(child_handle, child_label)
                finally:
                    _close_handle(child_handle)
                continue

            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise WorkspaceBoundaryError(f"cannot remove unowned {child_label}: {child}")
            _delete_checked_file(child, metadata, child_label)

        _identity_stamp(checked, label, expected)
    finally:
        if handle is not None:
            _close_handle(handle)


@contextmanager
def _case_creation_guard(
    manager: ValidatedCaseWorkspace, case_path: Path, case_stamp: _IdentityStamp
) -> Iterator[None]:
    checked = _reject_reparse_alias(case_path, "case root")
    handle = _open_directory(checked, "case root")
    try:
        _identity_stamp(checked, "case root", case_stamp)
        try:
            yield
        except BaseException:
            _require_registered_manager(manager)
            _identity_stamp(checked, "case root", case_stamp)
            _remove_created_tree_contents(
                checked,
                case_stamp,
                "case root",
                already_guarded=True,
            )
            _identity_stamp(checked, "case root", case_stamp)
            if os.name == "nt":
                _close_handle(handle)
                handle = -1
                cleanup_handle = _open_cleanup_directory(checked, "case root")
                try:
                    _identity_stamp(checked, "case root", case_stamp)
                    _delete_open_directory(cleanup_handle, "case root")
                finally:
                    _close_handle(cleanup_handle)
            else:
                raise WorkspaceBoundaryError(
                    "cannot remove case root: exact-object cleanup is unavailable on this platform"
                ) from None
            raise
        else:
            _identity_stamp(checked, "case root", case_stamp)
    finally:
        if handle != -1:
            _close_handle(handle)


def _registered_case_stamp(case: CaseWorkspace) -> _IdentityStamp:
    return cast(_IdentityStamp, _CASE_REGISTRY[id(case)][4])


def _registered_attempt_stamp(attempt: AttemptWorkspace) -> _IdentityStamp:
    return cast(_IdentityStamp, _ATTEMPT_REGISTRY[id(attempt)][5])


def _reject_parent_segments(path: Path, label: str) -> None:
    if ".." in path.parts:
        raise WorkspaceBoundaryError(f"{label} cannot contain parent traversal")


def _resolve_owned_target(
    root: Path,
    relative_path: str | Path,
    *,
    allow_absolute: bool = False,
) -> Path:
    relative = Path(relative_path)
    _reject_parent_segments(relative, "write target")
    root = _reject_reparse_alias(root, "owned root")
    if relative.is_absolute() or relative.anchor:
        if not allow_absolute:
            raise WorkspaceBoundaryError("write target must be relative to the case workspace")
        target = _lexical_path(relative)
    else:
        target = _lexical_path(root / relative)
    target = _reject_reparse_alias(target, "owned target")
    if target == root or not target.is_relative_to(root):
        raise WorkspaceBoundaryError("write target is outside the owned case workspace")
    return target


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    path = _reject_reparse_alias(path, "file")
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise WorkspaceBoundaryError(f"cannot read promotion source: {path}") from error
    return digest.hexdigest()


def _validate_sha256(value: str) -> str:
    if not isinstance(value, str) or len(value) != hashlib.sha256().digest_size * 2:
        raise ValueError("expected_sha256 must be a SHA-256 digest")
    if any(character not in "0123456789abcdefABCDEF" for character in value):
        raise ValueError("expected_sha256 must be a SHA-256 digest")
    return value.casefold()


def _append_bytes(
    target: Path,
    data: bytes,
    label: str,
    *,
    root: Path,
    root_stamp: _IdentityStamp,
) -> Path:
    """Append bytes only to a file with one directory entry.

    Append-only evidence must retain physical append semantics.  Rejecting a
    multiply-linked inode before writing prevents an append through a case
    name from changing an outside hard-link name.
    """

    parent = _reject_reparse_alias(target.parent, f"{label} parent")
    guard = _parent_guard(root, root_stamp, parent, label)
    guard.__enter__()
    descriptor: int | None = None
    try:
        _reject_reparse_alias(target, label)
        descriptor = _open_append_file(target, label)
        with os.fdopen(descriptor, "ab") as stream:
            descriptor = None
            _identity_stamp(root, "owned root", root_stamp)
            metadata = os.fstat(stream.fileno())
            if metadata.st_nlink != 1:
                raise WorkspaceBoundaryError(f"{label} cannot be a hard link")
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except WorkspaceBoundaryError:
        raise
    except OSError as error:
        raise WorkspaceBoundaryError(f"cannot append {label}: {target}") from error
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
        guard.__exit__(None, None, None)
    return target


def _require_registered_manager(value: object) -> ValidatedCaseWorkspace:
    if type(value) is not ValidatedCaseWorkspace:
        raise WorkspaceBoundaryError("workspace authority is not a registered manager")
    registration = _MANAGER_REGISTRY.get(id(value))
    if registration is None or registration[0] is not value:
        raise WorkspaceBoundaryError("workspace authority is not a registered manager")
    _, registered_tool_root, registered_cae_root, tool_stamp, cae_stamp = registration
    manager = value
    try:
        tool_root = manager.tool_root
        cae_root = manager.cae_root
        unchanged = (
            type(tool_root) is type(registered_tool_root)
            and type(cae_root) is type(registered_cae_root)
            and tool_root == registered_tool_root
            and cae_root == registered_cae_root
        )
    except Exception as error:
        raise WorkspaceBoundaryError("workspace manager binding was changed") from error
    if not unchanged:
        raise WorkspaceBoundaryError("workspace manager binding was changed")
    _identity_stamp(registered_tool_root, "tool root", tool_stamp)
    _identity_stamp(registered_cae_root, "cae root", cae_stamp)
    return cast(ValidatedCaseWorkspace, manager)


def _require_registered_case(value: object) -> CaseWorkspace:
    if type(value) is not CaseWorkspace:
        raise WorkspaceBoundaryError("case workspace is not a registered handle")
    registration = _CASE_REGISTRY.get(id(value))
    if registration is None or registration[0] is not value:
        raise WorkspaceBoundaryError("case workspace is not a registered handle")
    _, manager_value, case_id, case_root, case_stamp, original_inputs, source_inputs = registration
    case = value
    try:
        unchanged = (
            case._manager is manager_value
            and case.case_id == case_id
            and type(case.case_root) is type(case_root)
            and case.case_root == case_root
            and type(case.original_inputs) is tuple
            and case.original_inputs == original_inputs
            and type(case.source_inputs) is tuple
            and case.source_inputs == source_inputs
        )
    except Exception as error:
        raise WorkspaceBoundaryError("case workspace binding was changed") from error
    if not unchanged:
        raise WorkspaceBoundaryError("case workspace binding was changed")
    _require_registered_manager(manager_value)
    _identity_stamp(case_root, "case root", case_stamp)
    return cast(CaseWorkspace, case)


def _require_registered_attempt(value: object) -> AttemptWorkspace:
    if type(value) is not AttemptWorkspace:
        raise WorkspaceBoundaryError("attempt workspace is not a registered handle")
    registration = _ATTEMPT_REGISTRY.get(id(value))
    if registration is None or registration[0] is not value:
        raise WorkspaceBoundaryError("attempt workspace is not a registered handle")
    _, case_value, case_id, attempt_id, root, root_stamp = registration
    attempt = value
    try:
        unchanged = (
            attempt.case_id == case_id
            and attempt.attempt_id == attempt_id
            and type(attempt.root) is type(root)
            and attempt.root == root
        )
    except Exception as error:
        raise WorkspaceBoundaryError("attempt workspace binding was changed") from error
    if not unchanged:
        raise WorkspaceBoundaryError("attempt workspace binding was changed")
    case = _require_registered_case(case_value)
    if case.case_id != case_id:
        raise WorkspaceBoundaryError("attempt workspace case binding was changed")
    _identity_stamp(root, "attempt root", root_stamp)
    return cast(AttemptWorkspace, attempt)


@dataclass(frozen=True, slots=True, init=False)
class AttemptWorkspace:
    """A bounded writer for one case-owned temporary attempt directory."""

    case_id: str
    attempt_id: str
    root: Path

    @classmethod
    def _from_manager(
        cls,
        case_workspace: CaseWorkspace,
        attempt_id: str,
        root: Path,
        expected_root_stamp: _IdentityStamp | None = None,
    ) -> AttemptWorkspace:
        """Construct a handle only for the manager-owned attempt path."""

        if cls is not AttemptWorkspace:
            raise TypeError("attempt workspace handles cannot be subclassed")
        if type(case_workspace) is not CaseWorkspace:
            raise TypeError("attempt workspace requires a case workspace authority")
        case_workspace = _require_registered_case(case_workspace)
        _validate_segment(case_workspace.case_id, "case_id")
        _validate_segment(attempt_id, "attempt_id")
        expected_root = _reject_reparse_alias(
            case_workspace.temporary_root / "attempts" / attempt_id,
            "attempt root",
        )
        actual_root = _reject_reparse_alias(root, "attempt root")
        if actual_root != expected_root:
            raise WorkspaceBoundaryError("attempt root is not owned by the case workspace")
        case_stamp = _registered_case_stamp(case_workspace)
        creation_key = (case_workspace.case_root, case_stamp, attempt_id)
        creation = cast(_AttemptRootClaim | None, _ATTEMPT_ROOT_STAMPS.get(creation_key))
        if expected_root_stamp is None:
            if creation is None or creation[0] != actual_root:
                raise WorkspaceBoundaryError("attempt creation identity is required")
            expected_root_stamp = creation[1]
        if creation is not None:
            if creation[0] != actual_root or creation[1] != expected_root_stamp:
                raise WorkspaceBoundaryError("attempt creation identity changed")
            creation[2].validate()
            if creation[2].expected != _expected_native_directory_identity(expected_root_stamp):
                raise WorkspaceBoundaryError("attempt creation identity changed")
        root_stamp = _identity_stamp(actual_root, "attempt root", expected_root_stamp)

        instance = object.__new__(cls)
        object.__setattr__(instance, "case_id", case_workspace.case_id)
        object.__setattr__(instance, "attempt_id", attempt_id)
        object.__setattr__(instance, "root", actual_root)
        _ATTEMPT_REGISTRY[id(instance)] = (
            instance,
            case_workspace,
            case_workspace.case_id,
            attempt_id,
            actual_root,
            root_stamp,
        )
        if creation is not None:
            try:
                _release_attempt_root_claim(creation_key, creation)
            except BaseException:
                del _ATTEMPT_REGISTRY[id(instance)]
                raise
        return instance

    def __fspath__(self) -> str:
        attempt = _require_registered_attempt(self)
        return os.fspath(attempt.root)

    def __copy__(self) -> AttemptWorkspace:
        raise TypeError("attempt workspace handles cannot be copied")

    def __deepcopy__(self, memo: object) -> AttemptWorkspace:
        del memo
        raise TypeError("attempt workspace handles cannot be copied")

    def __reduce__(self) -> str | tuple[Any, ...]:
        raise TypeError("attempt workspace handles cannot be serialized")

    def __reduce_ex__(self, protocol: SupportsIndex) -> str | tuple[Any, ...]:
        del protocol
        raise TypeError("attempt workspace handles cannot be serialized")

    def __getstate__(self) -> object:
        raise TypeError("attempt workspace state is not transferable")

    def __setstate__(self, state: object) -> None:
        del state
        raise TypeError("attempt workspace state is not transferable")

    def _atomic_owned(self, target: Path, data: bytes, label: str) -> Path:
        del label
        attempt = _require_registered_attempt(self)
        registration = _ATTEMPT_REGISTRY[id(attempt)]
        case = cast(CaseWorkspace, registration[1])
        attempt_stamp = _registered_attempt_stamp(attempt)
        attempt_relative = attempt.root.relative_to(case.case_root)
        target_relative = target.relative_to(case.case_root)
        with case._exact_transaction() as exact:
            attempt_owner = exact._directory(tuple(attempt_relative.parts))
            if attempt_owner.expected != _expected_native_directory_identity(attempt_stamp):
                raise WorkspaceBoundaryError("attempt root changed before exact write")
            exact.ensure_directories(target_relative.parent)
            exact.replace_bytes(target_relative, data)
        return target

    def write_bytes(self, relative_path: str | Path, data: bytes) -> Path:
        attempt = _require_registered_attempt(self)
        target = _resolve_owned_target(attempt.root, relative_path)
        return attempt._atomic_owned(target, data, "attempt target")

    def write_text(
        self,
        relative_path: str | Path,
        text: str,
        *,
        encoding: str = "utf-8",
    ) -> Path:
        attempt = _require_registered_attempt(self)
        target = _resolve_owned_target(attempt.root, relative_path)
        return attempt._atomic_owned(target, text.encode(encoding), "attempt target")


@dataclass(frozen=True, slots=True, init=False)
class CaseWorkspace:
    """A manager-issued handle with temporary-only normal writes."""

    _manager: ValidatedCaseWorkspace
    case_id: str
    case_root: Path
    original_inputs: tuple[Path, ...] = ()
    source_inputs: tuple[Path, ...] = ()

    @classmethod
    def _from_manager(
        cls,
        manager: ValidatedCaseWorkspace,
        case_id: str,
        case_root: Path,
        original_inputs: Iterable[str | Path] = (),
        source_inputs: Iterable[str | Path] = (),
    ) -> CaseWorkspace:
        """Construct a handle only for a path issued by its manager.

        The public dataclass constructor is disabled.  In addition to making
        accidental construction fail, this factory checks that the supplied
        root is exactly the manager's case path and that original inputs remain
        inside ``01_Input``.
        """

        if cls is not CaseWorkspace:
            raise TypeError("case workspace handles cannot be subclassed")
        if type(manager) is not ValidatedCaseWorkspace:
            raise TypeError("case workspace requires a validated workspace authority")
        manager = _require_registered_manager(manager)
        _validate_segment(case_id, "case_id")
        expected_root = _reject_reparse_alias(
            manager.cae_root / case_id,
            "case root",
        )
        actual_root = _reject_reparse_alias(case_root, "case root")
        if actual_root != expected_root:
            raise WorkspaceBoundaryError("case root is not owned by the validated workspace")
        case_stamp = _identity_stamp(actual_root, "case root")

        input_root = _reject_reparse_alias(actual_root / "01_Input", "input root")
        normalised_originals: list[Path] = []
        for path_value in original_inputs:
            path = _reject_reparse_alias(path_value, "original input")
            if not path.is_file() or not path.is_relative_to(input_root):
                raise WorkspaceBoundaryError("original input is outside the owned 01_Input")
            normalised_originals.append(path)

        instance = object.__new__(cls)
        object.__setattr__(instance, "_manager", manager)
        object.__setattr__(instance, "case_id", case_id)
        object.__setattr__(instance, "case_root", actual_root)
        object.__setattr__(instance, "original_inputs", tuple(normalised_originals))
        object.__setattr__(
            instance,
            "source_inputs",
            tuple(_reject_reparse_alias(path, "source input") for path in source_inputs),
        )
        _CASE_REGISTRY[id(instance)] = (
            instance,
            manager,
            case_id,
            actual_root,
            case_stamp,
            tuple(normalised_originals),
            instance.source_inputs,
        )
        return instance

    @property
    def root(self) -> Path:
        return _require_registered_case(self).case_root

    @property
    def tool_root(self) -> Path:
        case = _require_registered_case(self)
        return _require_registered_manager(case._manager).tool_root

    @property
    def cae_root(self) -> Path:
        case = _require_registered_case(self)
        return _require_registered_manager(case._manager).cae_root

    @property
    def input_root(self) -> Path:
        return _require_registered_case(self).case_root / "01_Input"

    @property
    def model_root(self) -> Path:
        return _require_registered_case(self).case_root / "02_Model"

    @property
    def result_root(self) -> Path:
        return _require_registered_case(self).case_root / "03_Result"

    @property
    def report_root(self) -> Path:
        return _require_registered_case(self).case_root / "04_Report"

    @property
    def verification_root(self) -> Path:
        return _require_registered_case(self).case_root / "05_Verification"

    @property
    def temporary_root(self) -> Path:
        return _require_registered_case(self).case_root / _TEMPORARY_ROOT

    def __copy__(self) -> CaseWorkspace:
        raise TypeError("case workspace handles cannot be copied")

    def __deepcopy__(self, memo: object) -> CaseWorkspace:
        del memo
        raise TypeError("case workspace handles cannot be copied")

    def __reduce__(self) -> str | tuple[Any, ...]:
        raise TypeError("case workspace handles cannot be serialized")

    def __reduce_ex__(self, protocol: SupportsIndex) -> str | tuple[Any, ...]:
        del protocol
        raise TypeError("case workspace handles cannot be serialized")

    def __getstate__(self) -> object:
        raise TypeError("case workspace state is not transferable")

    def __setstate__(self, state: object) -> None:
        del state
        raise TypeError("case workspace state is not transferable")

    def _atomic_owned(self, target: Path, data: bytes, label: str) -> Path:
        del label
        case = _require_registered_case(self)
        relative = target.relative_to(case.case_root)
        with case._exact_transaction() as exact:
            exact.replace_bytes(relative, data)
        return target

    def _append_owned(self, target: Path, data: bytes) -> Path:
        return _append_bytes(
            target,
            data,
            "append target",
            root=self.case_root,
            root_stamp=_registered_case_stamp(self),
        )

    def _exact_transaction(self) -> _ExactCaseTransaction:
        """Issue one exact-object transaction for the internal evidence store."""

        return _ExactCaseTransaction(_require_registered_case(self))

    def _temporary_write_target(
        self,
        relative_path: str | Path,
        *,
        allow_event_append: bool = False,
    ) -> Path:
        case = _require_registered_case(self)
        target = _resolve_owned_target(case.case_root, relative_path)
        temporary_root = _reject_reparse_alias(case.temporary_root, "temporary root")
        if target == temporary_root or not target.is_relative_to(temporary_root):
            input_root = _reject_reparse_alias(case.input_root, "input root")
            if target == input_root or target.is_relative_to(input_root):
                raise ImmutableInputError("01_Input is immutable after case creation")
            raise WorkspaceBoundaryError("normal writes are restricted to 90_Temporary")

        relative = target.relative_to(self.case_root).as_posix()
        if relative == _EVENTS_FILE and not allow_event_append:
            raise WorkspaceBoundaryError("events.jsonl is append-only")
        return target

    def _control_write_target(self, relative_path: str | Path) -> Path:
        case = _require_registered_case(self)
        target = _resolve_owned_target(case.case_root, relative_path)
        relative = target.relative_to(case.case_root).as_posix()
        if relative not in _CONTROL_FILES:
            raise WorkspaceBoundaryError("only evidence control files may use an internal write")
        return target

    def write_bytes(self, relative_path: str | Path, data: bytes) -> Path:
        target = self._temporary_write_target(relative_path)
        return self._atomic_owned(target, data, "case target")

    def write_text(
        self,
        relative_path: str | Path,
        text: str,
        *,
        encoding: str = "utf-8",
    ) -> Path:
        target = self._temporary_write_target(relative_path)
        return self._atomic_owned(target, text.encode(encoding), "case target")

    def _write_control_text(
        self,
        relative_path: str | Path,
        text: str,
        *,
        encoding: str = "utf-8",
    ) -> Path:
        """Write a root-level evidence control file for ``EvidenceStore``."""

        target = self._control_write_target(relative_path)
        return self._atomic_owned(target, text.encode(encoding), "control target")

    def append_bytes(self, relative_path: str | Path, data: bytes) -> Path:
        """Durably append bytes to a file in the temporary area."""

        target = self._temporary_write_target(relative_path, allow_event_append=True)
        return self._append_owned(target, data)

    def append_text(
        self,
        relative_path: str | Path,
        text: str,
        *,
        encoding: str = "utf-8",
    ) -> Path:
        """Durably append text to a file in the temporary area.

        The file is opened with append semantics, flushed, and fsynced before
        returning.  ``events.jsonl`` is intentionally excluded from normal
        ``write_text`` so callers cannot accidentally replace the chain.
        """

        return self.append_bytes(relative_path, text.encode(encoding))

    def _copy_create_new(
        self,
        source: str | Path,
        destination: str | Path,
        *,
        expected_sha256: str,
    ) -> Path:
        """Copy an EvidenceStore-authorized artifact into a permanent area once.

        This is an internal hook for ``EvidenceStore``.  Public callers must
        obtain a persisted verification record from that store; a digest alone
        is not a promotion authority.
        """

        case = _require_registered_case(self)
        case_stamp = _registered_case_stamp(case)
        expected = _validate_sha256(expected_sha256)
        source_path = _resolve_owned_target(case.case_root, source, allow_absolute=True)
        temporary_root = _reject_reparse_alias(case.temporary_root, "temporary root")
        if source_path == temporary_root or not source_path.is_relative_to(temporary_root):
            raise WorkspaceBoundaryError("promotion source must be inside 90_Temporary")
        if not source_path.is_file():
            raise WorkspaceBoundaryError("promotion source must be a regular file")

        destination_path = _resolve_owned_target(
            case.case_root,
            destination,
            allow_absolute=True,
        )
        relative_destination = destination_path.relative_to(case.case_root)
        if (
            len(relative_destination.parts) < 2
            or relative_destination.parts[0] not in _PERMANENT_ROOTS
        ):
            raise WorkspaceBoundaryError(
                "promotion destination must be inside a permanent case directory"
            )
        created_destination: int | None = None

        def cleanup(parent_stamp: _IdentityStamp) -> None:
            if created_destination is None:
                return
            _require_registered_case(case)
            _identity_stamp(destination_path.parent, "promotion destination parent", parent_stamp)
            metadata = os.lstat(os.fspath(destination_path))
            if metadata.st_ino == created_destination:
                destination_path.unlink()

        with (
            _parent_guard(
                case.case_root,
                case_stamp,
                source_path.parent,
                "promotion source",
            ),
            _parent_guard(
                case.case_root,
                case_stamp,
                destination_path.parent,
                "promotion destination",
                cleanup=cleanup,
            ),
        ):
            if destination_path.exists() or destination_path.is_symlink():
                raise FileExistsError(destination_path)
            if _sha256_file(source_path) != expected:
                raise ValueError("promotion source sha256 does not match expected_sha256")

            _identity_stamp(case.case_root, "case root", case_stamp)
            copied_digest = hashlib.sha256()
            with (
                source_path.open("rb") as source_stream,
                destination_path.open("xb") as target,
            ):
                created_destination = int(os.fstat(target.fileno()).st_ino)
                for chunk in iter(lambda: source_stream.read(1024 * 1024), b""):
                    target.write(chunk)
                    copied_digest.update(chunk)
                target.flush()
                os.fsync(target.fileno())
            _identity_stamp(case.case_root, "case root", case_stamp)
            if copied_digest.hexdigest() != expected:
                raise ValueError("promotion source changed during copy")
            created_destination = None
        return destination_path

    def promote_verified(
        self,
        source: str | Path,
        destination: str | Path,
        *,
        expected_sha256: str,
    ) -> Path:
        """Reject the former raw-digest promotion API.

        Promotion authority belongs to ``EvidenceStore`` and is represented by
        a persisted, opaque verification receipt.  Keeping this guard makes
        accidental calls fail explicitly without exposing a bypass.
        """

        _require_registered_case(self)
        del source, destination, expected_sha256
        raise TypeError("promotion requires EvidenceStore.promote_verified receipt")

    def promote_artifact(
        self,
        source: str | Path,
        destination: str | Path,
        *,
        expected_sha256: str,
    ) -> Path:
        """Reject the legacy raw-digest promotion alias."""

        _require_registered_case(self)
        del source, destination, expected_sha256
        raise TypeError("promotion requires EvidenceStore.promote_verified receipt")

    def allocate_attempt(self, attempt_id: str) -> AttemptWorkspace:
        case = _require_registered_case(self)
        _validate_segment(attempt_id, "attempt_id")
        _reject_reparse_alias(case.case_root, "case root")
        _reject_reparse_alias(case.temporary_root, "temporary root")
        _reject_reparse_alias(
            case.temporary_root / "attempts",
            "attempts root",
        )
        attempt_root = case.temporary_root / "attempts" / attempt_id
        _reject_reparse_alias(attempt_root, "attempt root")
        relative = attempt_root.relative_to(case.case_root)
        with case._exact_transaction() as exact:
            root_stamp = exact.make_directory(relative)
            return AttemptWorkspace._from_manager(
                case,
                attempt_id,
                attempt_root,
                root_stamp,
            )


class ValidatedCaseWorkspace:
    """Manager for isolated, case-owned workspaces.

    ``tool_root`` is the repository/tool tree and ``cae_root`` is the external
    ``02_CAE`` root.  Neither root itself is a writable case target.
    """

    __slots__ = ("tool_root", "cae_root")

    tool_root: Path
    cae_root: Path

    def __init__(self, tool_root: Path, cae_root: Path) -> None:
        if type(self) is not ValidatedCaseWorkspace:
            raise TypeError("workspace managers cannot be subclassed")
        if id(self) in _MANAGER_REGISTRY:
            raise TypeError("workspace manager cannot be reinitialised")
        tool_root = _reject_reparse_alias(tool_root, "tool root")
        cae_root = _reject_reparse_alias(cae_root, "cae root")
        if tool_root == cae_root:
            raise ValueError("tool_root and cae_root must be different roots")
        if tool_root.is_relative_to(cae_root) or cae_root.is_relative_to(tool_root):
            raise ValueError("tool_root and cae_root must not overlap")
        try:
            tool_root.mkdir(parents=True, exist_ok=True)
            cae_root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise WorkspaceBoundaryError("cannot create workspace roots") from error
        tool_stamp = _identity_stamp(tool_root, "tool root")
        cae_stamp = _identity_stamp(cae_root, "cae root")
        object.__setattr__(self, "tool_root", tool_root)
        object.__setattr__(self, "cae_root", cae_root)
        _MANAGER_REGISTRY[id(self)] = (self, tool_root, cae_root, tool_stamp, cae_stamp)

    def __setattr__(self, name: str, value: object) -> None:
        if name in self.__slots__ and hasattr(self, name):
            raise FrozenInstanceError(f"cannot assign to field '{name}'")
        object.__setattr__(self, name, value)

    def __delattr__(self, name: str) -> None:
        if name in self.__slots__ and hasattr(self, name):
            raise FrozenInstanceError(f"cannot delete field '{name}'")
        object.__delattr__(self, name)

    def __copy__(self) -> ValidatedCaseWorkspace:
        raise TypeError("workspace managers cannot be copied")

    def __deepcopy__(self, memo: object) -> ValidatedCaseWorkspace:
        del memo
        raise TypeError("workspace managers cannot be copied")

    def __reduce__(self) -> str | tuple[Any, ...]:
        raise TypeError("workspace managers cannot be serialized")

    def __reduce_ex__(self, protocol: SupportsIndex) -> str | tuple[Any, ...]:
        del protocol
        raise TypeError("workspace managers cannot be serialized")

    def __getstate__(self) -> object:
        raise TypeError("workspace manager state is not transferable")

    def __setstate__(self, state: object) -> None:
        del state
        raise TypeError("workspace manager state is not transferable")

    def _case_path(self, case_id: str) -> Path:
        manager = _require_registered_manager(self)
        _validate_segment(case_id, "case_id")
        return manager.cae_root / case_id

    @staticmethod
    def _normalise_inputs(
        original_inputs: Iterable[str | Path] | str | Path,
    ) -> tuple[_OriginalInputSource, ...]:
        candidates: tuple[str | Path, ...]
        if isinstance(original_inputs, (str, Path)):
            candidates = (original_inputs,)
        else:
            candidates = tuple(original_inputs)

        sources: list[_OriginalInputSource] = []
        names: set[str] = set()
        for candidate in candidates:
            source = _reject_reparse_alias(candidate, "original input")
            try:
                metadata = os.lstat(os.fspath(source))
            except OSError as error:
                raise ValueError(f"original input is not a file: {source}") from error
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError(f"original input is not a file: {source}")
            name_key = source.name.casefold()
            if name_key in names:
                raise ValueError(f"duplicate original input name: {source.name}")
            names.add(name_key)
            sources.append(_OriginalInputSource(source, _file_state(metadata)))
        return tuple(sources)

    def create_case(
        self,
        case_id: str,
        original_inputs: Iterable[str | Path] | str | Path = (),
    ) -> CaseWorkspace:
        """Create a new case and copy source inputs without modifying them."""

        manager = _require_registered_manager(self)
        case_path = _reject_reparse_alias(manager._case_path(case_id), "case root")
        sources = self._normalise_inputs(original_inputs)
        manager_registration = _MANAGER_REGISTRY[id(manager)]
        cae_stamp = manager_registration[4]
        with _directory_guard(manager.cae_root, cae_stamp, "cae root"):
            case_path.mkdir(parents=False, exist_ok=False)
            case_stamp = _identity_stamp(case_path, "case root")
            with _case_creation_guard(manager, case_path, case_stamp):
                for directory_name in (
                    "01_Input",
                    "02_Model",
                    "03_Result",
                    "04_Report",
                    "05_Verification",
                    "90_Temporary/attempts",
                ):
                    directory = case_path / directory_name
                    with _parent_guard(case_path, case_stamp, directory.parent, "dir"):
                        directory.mkdir(parents=False, exist_ok=False)

                with ExitStack() as source_stack:
                    opened_sources: list[
                        tuple[_OriginalInputSource, BinaryIO, _FileState, str]
                    ] = []
                    for source in sources:
                        try:
                            source_stream = cast(
                                BinaryIO,
                                source_stack.enter_context(source.path.open("rb")),
                            )
                        except OSError as error:
                            raise WorkspaceBoundaryError(
                                "original input changed during case creation"
                            ) from error
                        stream_state = _source_stream_state(source_stream)
                        if (
                            _file_state_identity(stream_state) != _file_state_identity(source.state)
                            or _source_path_state(source.path) != source.state
                        ):
                            raise WorkspaceBoundaryError(
                                "original input changed during case creation"
                            )
                        initial_digest = _stream_sha256(source_stream)
                        if (
                            _source_stream_state(source_stream) != stream_state
                            or _source_path_state(source.path) != source.state
                        ):
                            raise WorkspaceBoundaryError(
                                "original input changed during case creation"
                            )
                        source_stream.seek(0)
                        opened_sources.append((source, source_stream, stream_state, initial_digest))

                    copied_inputs: list[Path] = []
                    for source, source_stream, stream_state, initial_digest in opened_sources:
                        destination = case_path / "01_Input" / source.path.name
                        with _parent_guard(
                            case_path,
                            case_stamp,
                            destination.parent,
                            "original input",
                        ):
                            _identity_stamp(case_path, "case root", case_stamp)
                            with destination.open("x+b") as target:
                                shutil.copyfileobj(source_stream, target)
                                target.flush()
                                os.fsync(target.fileno())
                                target.seek(0)
                                copied_digest = _stream_sha256(target)

                        if (
                            _source_stream_state(source_stream) != stream_state
                            or _source_path_state(source.path) != source.state
                        ):
                            raise WorkspaceBoundaryError(
                                "original input changed during case creation"
                            )
                        source_stream.seek(0)
                        final_digest = _stream_sha256(source_stream)
                        if (
                            final_digest != initial_digest
                            or copied_digest != initial_digest
                            or _source_stream_state(source_stream) != stream_state
                            or _source_path_state(source.path) != source.state
                        ):
                            raise WorkspaceBoundaryError(
                                "original input changed during case creation"
                            )
                        copied_inputs.append(_lexical_path(destination))

                    for source, source_stream, stream_state, initial_digest in opened_sources:
                        if (
                            _source_stream_state(source_stream) != stream_state
                            or _source_path_state(source.path) != source.state
                        ):
                            raise WorkspaceBoundaryError(
                                "original input changed during case creation"
                            )
                        source_stream.seek(0)
                        final_digest = _stream_sha256(source_stream)
                        if (
                            final_digest != initial_digest
                            or _source_stream_state(source_stream) != stream_state
                            or _source_path_state(source.path) != source.state
                        ):
                            raise WorkspaceBoundaryError(
                                "original input changed during case creation"
                            )

                    return CaseWorkspace._from_manager(
                        manager,
                        case_id,
                        case_path,
                        copied_inputs,
                        (source.path for source in sources),
                    )

    def open_case(self, case_id: str) -> CaseWorkspace:
        """Open an existing case without granting access outside its root."""

        manager = _require_registered_manager(self)
        _reject_reparse_alias(manager.tool_root, "tool root")
        _reject_reparse_alias(manager.cae_root, "cae root")
        case_path = _reject_reparse_alias(manager._case_path(case_id), "case root")
        manager_registration = _MANAGER_REGISTRY[id(manager)]
        cae_stamp = manager_registration[4]
        with _directory_guard(manager.cae_root, cae_stamp, "cae root"):
            if not case_path.is_dir() or not case_path.is_relative_to(manager.cae_root):
                raise FileNotFoundError(f"case does not exist: {case_id}")
            case_stamp = _identity_stamp(case_path, "case root")
            with _directory_guard(case_path, case_stamp, "case root"):
                input_root = _reject_reparse_alias(case_path / "01_Input", "input root")
                original_inputs: tuple[Path, ...]
                if input_root.is_dir():
                    entries = tuple(input_root.iterdir())
                    normalised_entries: list[Path] = []
                    for entry in entries:
                        try:
                            normalised_entry = _reject_reparse_alias(entry, "original input")
                        except WorkspaceBoundaryError as error:
                            raise WorkspaceBoundaryError(
                                "original input directory contains an invalid entry"
                            ) from error
                        if not normalised_entry.is_file():
                            raise WorkspaceBoundaryError(
                                "original input directory contains an invalid entry"
                            )
                        normalised_entries.append(normalised_entry)
                    original_inputs = tuple(sorted(normalised_entries, key=str))
                else:
                    original_inputs = ()
                return CaseWorkspace._from_manager(manager, case_id, case_path, original_inputs)

    def write_bytes(self, case_id: str, relative_path: str | Path, data: bytes) -> Path:
        return self.open_case(case_id).write_bytes(relative_path, data)

    def write_text(
        self,
        case_id: str,
        relative_path: str | Path,
        text: str,
        *,
        encoding: str = "utf-8",
    ) -> Path:
        return self.open_case(case_id).write_text(relative_path, text, encoding=encoding)
