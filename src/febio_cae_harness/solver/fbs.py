"""Synthetic, authority-bound FBS result validation.

This module deliberately contains no FEBio Studio runtime.  A manager binds a
single adapter to a non-empty runtime identity and issues an opaque authority.
Only that authority can be used to validate an attempt XPLT artifact; all
issued validations remain synthetic and unverified provenance.
"""

from __future__ import annotations

import atexit
import contextlib
import ctypes
import errno
import hashlib
import math
import os
import stat
import threading
import weakref
from collections.abc import Callable, Iterable, Mapping, Sequence
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol, SupportsIndex, cast

__all__ = [
    "FbsAdapterAuthority",
    "FbsAdapterManager",
    "FbsAdapterProtocol",
    "FbsValidation",
    "validate_requested_fields",
]

_AdapterReader = Callable[[Path, Sequence[str]], object]
_PROVENANCE = "synthetic-unverified"
_OFFICIAL_PROVENANCE = "official"
_AUTHORITY_TOKEN = object()


_WINDOWS_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_WINDOWS_FILE_LIST_DIRECTORY = 0x00000001
_WINDOWS_FILE_READ_ATTRIBUTES = 0x00000080
_WINDOWS_READ_CONTROL = 0x00020000
_WINDOWS_SYNCHRONIZE = 0x00100000
_WINDOWS_GENERIC_READ = 0x80000000
_WINDOWS_FILE_SHARE_READ = 0x00000001
_WINDOWS_FILE_SHARE_WRITE = 0x00000002
_WINDOWS_OPEN_EXISTING = 3
_WINDOWS_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_WINDOWS_FILE_DIRECTORY_FILE = 0x00000001
_WINDOWS_FILE_NON_DIRECTORY_FILE = 0x00000040
_WINDOWS_FILE_SYNCHRONOUS_IO_NONALERT = 0x00000020
_WINDOWS_FILE_BEGIN = 0
_WINDOWS_DUPLICATE_SAME_ACCESS = 0x00000002
_WINDOWS_OBJ_CASE_INSENSITIVE = 0x00000040
_WINDOWS_ERROR_INVALID_HANDLE = 6
_WINDOWS_HANDLE_FLAG_PROTECT_FROM_CLOSE = 0x00000002


class _WindowsFileTime(ctypes.Structure):
    _fields_ = [
        ("low", wintypes.DWORD),
        ("high", wintypes.DWORD),
    ]


class _WindowsFileInformation(ctypes.Structure):
    _fields_ = [
        ("attributes", wintypes.DWORD),
        ("creation_time", _WindowsFileTime),
        ("last_access_time", _WindowsFileTime),
        ("last_write_time", _WindowsFileTime),
        ("volume_serial_number", wintypes.DWORD),
        ("file_size_high", wintypes.DWORD),
        ("file_size_low", wintypes.DWORD),
        ("number_of_links", wintypes.DWORD),
        ("file_index_high", wintypes.DWORD),
        ("file_index_low", wintypes.DWORD),
    ]


class _WindowsUnicodeString(ctypes.Structure):
    _fields_ = [
        ("length", wintypes.USHORT),
        ("maximum_length", wintypes.USHORT),
        ("buffer", wintypes.LPWSTR),
    ]


class _WindowsObjectAttributes(ctypes.Structure):
    _fields_ = [
        ("length", wintypes.ULONG),
        ("root_directory", wintypes.HANDLE),
        ("object_name", ctypes.POINTER(_WindowsUnicodeString)),
        ("attributes", wintypes.ULONG),
        ("security_descriptor", wintypes.LPVOID),
        ("security_quality_of_service", wintypes.LPVOID),
    ]


class _WindowsIoStatusBlock(ctypes.Structure):
    _fields_ = [
        ("status", ctypes.c_long),
        ("information", ctypes.c_size_t),
    ]


class FbsAdapterProtocol(Protocol):
    """Minimal adapter contract; no FBS implementation is bundled."""

    def read_fields(self, xplt_path: Path, fields: Sequence[str]) -> Mapping[str, object]:
        """Read requested result fields from ``xplt_path``."""


class FbsAdapterAuthority:
    """Opaque capability issued by :class:`FbsAdapterManager`."""

    __slots__ = ("_record", "__weakref__")

    def __new__(cls, token: object | None = None) -> FbsAdapterAuthority:
        if cls is not FbsAdapterAuthority or token is not _AUTHORITY_TOKEN:
            raise TypeError("FbsAdapterAuthority instances are manager-issued")
        return super().__new__(cls)

    def __init_subclass__(cls, **kwargs: object) -> None:
        del kwargs
        raise TypeError("FbsAdapterAuthority cannot be subclassed")

    def __copy__(self) -> FbsAdapterAuthority:
        raise TypeError("FbsAdapterAuthority cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> FbsAdapterAuthority:
        del memo
        raise TypeError("FbsAdapterAuthority cannot be copied")

    def __reduce__(self) -> str | tuple[Any, ...]:
        raise TypeError("FbsAdapterAuthority cannot be serialized")

    def __reduce_ex__(self, protocol: SupportsIndex) -> str | tuple[Any, ...]:
        del protocol
        raise TypeError("FbsAdapterAuthority cannot be serialized")


@dataclass(slots=True)
class _ManagerRecord:
    manager: FbsAdapterManager
    adapter: object
    reader: _AdapterReader
    runtime_identity: str
    official: bool
    profile: str | None
    provenance: str
    attempt_root: Path | None
    root_binding: _RootBinding | None
    xplt_owners: list[_OwnedHandle] = field(default_factory=list)
    authority: FbsAdapterAuthority | None = None
    closed: bool = False


@dataclass(slots=True)
class _AuthorityRecord:
    authority: FbsAdapterAuthority
    manager: FbsAdapterManager
    adapter: object
    reader: _AdapterReader
    runtime_identity: str
    official: bool
    profile: str | None
    provenance: str
    attempt_root: Path | None
    root_binding: _RootBinding | None


@dataclass(slots=True)
class _ValidationRecord:
    validation: FbsValidation
    authority: FbsAdapterAuthority
    runtime_identity: str
    xplt_path: Path
    requested_fields: tuple[str, ...]
    available_fields: tuple[str, ...]
    values: Mapping[str, object]
    missing_fields: tuple[str, ...]
    non_finite_fields: tuple[str, ...]
    valid: bool
    official: bool
    provenance: str
    issues: tuple[str, ...]
    digest_before: str
    digest_after: str
    xplt_owner: _OwnedHandle | None = None
    owner: object | None = None
    result: object | None = None
    official_result: object | None = None
    revoked: bool = False


@dataclass(slots=True)
class _RootBinding:
    path: Path
    fd: _OwnedHandle | None
    device: int
    inode: int
    path_identities: tuple[tuple[Path, int, int], ...] = ()
    closed: bool = False


@dataclass(slots=True)
class _OwnedHandle:
    """A native descriptor/handle together with its immutable object identity."""

    value: int | None
    device: int
    inode: int
    path: Path
    windows: bool
    attributes: int = 0
    final_path: str = ""
    closed: bool = False
    identity_known: bool = True
    windows_protected: bool = False
    windows_unprotected_for_close: bool = False

    generation: object = field(default_factory=object, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.closed or self.value is None:
            return
        with _HANDLE_LOCK:
            _HANDLE_REGISTRY[(self.windows, self.value)] = (self, self.generation)


_PENDING_CLEANUP: list[_OwnedHandle] = []
_HANDLE_LOCK = threading.RLock()
_HANDLE_REGISTRY: dict[tuple[bool, int], tuple[_OwnedHandle, object]] = {}


def _is_current_owned_handle(owner: _OwnedHandle, value: int) -> bool:
    current = _HANDLE_REGISTRY.get((owner.windows, value))
    return current is not None and current[0] is owner and current[1] is owner.generation


def _retain_cleanup_owner(owner: _OwnedHandle) -> None:
    with _HANDLE_LOCK:
        if owner.closed or owner.value is None:
            return
        if not any(existing is owner for existing in _PENDING_CLEANUP):
            _PENDING_CLEANUP.append(owner)


def _forget_cleanup_owner(owner: _OwnedHandle) -> None:
    with _HANDLE_LOCK:
        _PENDING_CLEANUP[:] = [existing for existing in _PENDING_CLEANUP if existing is not owner]


def _retire_owned_handle(owner: _OwnedHandle) -> None:
    with _HANDLE_LOCK:
        value = owner.value
        owner.closed = True
        owner.value = None
        owner.windows_protected = False
        owner.windows_unprotected_for_close = False
        if value is not None:
            current = _HANDLE_REGISTRY.get((owner.windows, value))
            if current is not None and current[0] is owner and current[1] is owner.generation:
                del _HANDLE_REGISTRY[(owner.windows, value)]
        _forget_cleanup_owner(owner)


def _owned_value(owner: _OwnedHandle) -> int:
    if owner.closed or owner.value is None:
        raise ValueError("owned filesystem handle is closed")
    with _HANDLE_LOCK:
        value = owner.value
        if value is None or owner.closed:
            raise ValueError("owned filesystem handle is closed")
        if not _is_current_owned_handle(owner, value):
            raise ValueError("owned filesystem handle generation is stale")
        return value


def _windows_error(message: str, code: int | None = None) -> OSError:
    error_code = ctypes.get_last_error() if code is None else code
    return OSError(error_code, f"{message} (WinError {error_code})")


def _windows_kernel32() -> Any:
    if os.name != "nt":
        raise OSError("Windows native filesystem APIs are unavailable")
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _windows_ntdll() -> Any:
    if os.name != "nt":
        raise OSError("Windows native filesystem APIs are unavailable")
    return ctypes.WinDLL("ntdll", use_last_error=True)


def _windows_handle_int(value: object) -> int:
    raw = getattr(value, "value", value)
    if raw is None:
        return 0
    if isinstance(raw, int):
        return raw
    return int(cast(SupportsIndex, raw))


def _windows_close_raw(value: int) -> None:
    kernel32 = _windows_kernel32()
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    if not close_handle(wintypes.HANDLE(value)):
        raise _windows_error("unable to close native filesystem handle")


def _windows_set_close_protection(value: int, protected: bool) -> None:
    kernel32 = _windows_kernel32()
    set_handle_information = kernel32.SetHandleInformation
    set_handle_information.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    set_handle_information.restype = wintypes.BOOL
    ctypes.set_last_error(0)
    flags = _WINDOWS_HANDLE_FLAG_PROTECT_FROM_CLOSE if protected else 0
    if not set_handle_information(
        wintypes.HANDLE(value),
        wintypes.DWORD(_WINDOWS_HANDLE_FLAG_PROTECT_FROM_CLOSE),
        wintypes.DWORD(flags),
    ):
        raise _windows_error("unable to change native filesystem handle close protection")


def _windows_handle_flags(value: int) -> int:
    kernel32 = _windows_kernel32()
    get_handle_information = kernel32.GetHandleInformation
    get_handle_information.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    get_handle_information.restype = wintypes.BOOL
    flags = wintypes.DWORD()
    ctypes.set_last_error(0)
    if not get_handle_information(wintypes.HANDLE(value), ctypes.byref(flags)):
        raise _windows_error("unable to inspect native filesystem handle flags")
    return int(flags.value)


def _windows_file_info(value: int) -> tuple[int, int, int, int]:
    kernel32 = _windows_kernel32()
    get_info = kernel32.GetFileInformationByHandle
    get_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(_WindowsFileInformation)]
    get_info.restype = wintypes.BOOL
    information = _WindowsFileInformation()
    if not get_info(wintypes.HANDLE(value), ctypes.byref(information)):
        raise _windows_error("unable to inspect native filesystem handle")
    file_index = (int(information.file_index_high) << 32) | int(information.file_index_low)
    return (
        int(information.attributes),
        int(information.volume_serial_number),
        file_index,
        int(information.number_of_links),
    )


def _windows_final_path(value: int) -> str:
    kernel32 = _windows_kernel32()
    get_final_path = kernel32.GetFinalPathNameByHandleW
    get_final_path.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    get_final_path.restype = wintypes.DWORD
    size = 1024
    while size <= 32768:
        buffer = ctypes.create_unicode_buffer(size)
        length = int(
            get_final_path(wintypes.HANDLE(value), buffer, wintypes.DWORD(size), wintypes.DWORD(0))
        )
        if length == 0:
            raise _windows_error("unable to inspect native filesystem path")
        if length < size:
            return buffer.value
        size = min(32768, max(size * 2, length + 1))
    raise OSError("native filesystem path is too long")


def _windows_normalise_final(value: str) -> str:
    normalised = os.path.normpath(value)
    if normalised.startswith("\\\\?\\UNC\\"):
        normalised = "\\\\" + normalised[8:]
    elif normalised.startswith("\\\\?\\"):
        normalised = normalised[4:]
    return os.path.normcase(normalised)


def _windows_expected_path(path: Path) -> str:
    return _windows_normalise_final(os.path.realpath(os.fspath(path)))


def _windows_path_inside(child: str, parent: str) -> bool:
    child_value = _windows_normalise_final(child)
    parent_value = _windows_normalise_final(parent).rstrip("\\/")
    return child_value == parent_value or child_value.startswith(parent_value + "\\")


def _windows_open_absolute(
    path: Path,
    desired_access: int,
    share_mode: int,
    flags: int,
) -> _OwnedHandle:
    with _HANDLE_LOCK:
        return _windows_open_absolute_locked(path, desired_access, share_mode, flags)


def _windows_open_absolute_locked(
    path: Path,
    desired_access: int,
    share_mode: int,
    flags: int,
) -> _OwnedHandle:
    kernel32 = _windows_kernel32()
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    raw = create_file(
        os.fspath(path),
        wintypes.DWORD(desired_access),
        wintypes.DWORD(share_mode),
        None,
        wintypes.DWORD(_WINDOWS_OPEN_EXISTING),
        wintypes.DWORD(flags),
        wintypes.HANDLE(0),
    )
    value = _windows_handle_int(raw)
    invalid_values = {0, -1, (1 << (ctypes.sizeof(ctypes.c_void_p) * 8)) - 1}
    if value in invalid_values:
        raise _windows_error("unable to open native filesystem object")
    owner = _OwnedHandle(value, 0, 0, path, True, identity_known=False)
    try:
        _windows_set_close_protection(value, True)
        owner.windows_protected = True
        attributes, device, inode, _ = _windows_file_info(value)
        owner.attributes = attributes
        owner.device = device
        owner.inode = inode
        owner.final_path = _windows_final_path(value)
        owner.identity_known = True
        return owner
    except BaseException:
        _best_effort_close(owner)
        raise


def _windows_open_relative(
    parent: _OwnedHandle,
    name: str,
    desired_access: int,
    share_mode: int,
    create_options: int,
    path: Path,
    *,
    create_disposition: int = 1,
) -> _OwnedHandle:
    with _HANDLE_LOCK:
        return _windows_open_relative_locked(
            parent,
            name,
            desired_access,
            share_mode,
            create_options,
            path,
            create_disposition=create_disposition,
        )


def _windows_open_relative_locked(
    parent: _OwnedHandle,
    name: str,
    desired_access: int,
    share_mode: int,
    create_options: int,
    path: Path,
    *,
    create_disposition: int = 1,
) -> _OwnedHandle:
    parent_value = _owned_value(parent)
    name_buffer = ctypes.create_unicode_buffer(name)
    unicode_name = _WindowsUnicodeString(
        length=len(name) * ctypes.sizeof(ctypes.c_wchar),
        maximum_length=(len(name) + 1) * ctypes.sizeof(ctypes.c_wchar),
        buffer=ctypes.cast(name_buffer, wintypes.LPWSTR),
    )
    attributes = _WindowsObjectAttributes(
        length=ctypes.sizeof(_WindowsObjectAttributes),
        root_directory=wintypes.HANDLE(parent_value),
        object_name=ctypes.pointer(unicode_name),
        attributes=_WINDOWS_OBJ_CASE_INSENSITIVE,
        security_descriptor=None,
        security_quality_of_service=None,
    )
    io_status = _WindowsIoStatusBlock()
    raw = wintypes.HANDLE()
    ntdll = _windows_ntdll()
    create_file = ntdll.NtCreateFile
    create_file.argtypes = [
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.ULONG,
        ctypes.POINTER(_WindowsObjectAttributes),
        ctypes.POINTER(_WindowsIoStatusBlock),
        ctypes.c_void_p,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        ctypes.c_void_p,
        wintypes.ULONG,
    ]
    create_file.restype = ctypes.c_long
    status = int(
        create_file(
            ctypes.byref(raw),
            wintypes.ULONG(desired_access),
            ctypes.byref(attributes),
            ctypes.byref(io_status),
            None,
            wintypes.ULONG(0),
            wintypes.ULONG(share_mode),
            wintypes.ULONG(create_disposition),
            wintypes.ULONG(create_options),
            None,
            wintypes.ULONG(0),
        )
    )
    if status < 0:
        raise OSError(f"NtCreateFile failed with NTSTATUS 0x{status & 0xFFFFFFFF:08x}")
    value = _windows_handle_int(raw)
    if value == 0:
        raise OSError("NtCreateFile returned an invalid handle")
    owner = _OwnedHandle(value, 0, 0, path, True, identity_known=False)
    try:
        _windows_set_close_protection(value, True)
        owner.windows_protected = True
        file_attributes, device, inode, _ = _windows_file_info(value)
        owner.attributes = file_attributes
        owner.device = device
        owner.inode = inode
        owner.final_path = _windows_final_path(value)
        owner.identity_known = True
        return owner
    except BaseException:
        _best_effort_close(owner)
        raise


def _windows_duplicate(owner: _OwnedHandle) -> _OwnedHandle:
    with _HANDLE_LOCK:
        return _windows_duplicate_locked(owner)


def _windows_duplicate_locked(owner: _OwnedHandle) -> _OwnedHandle:
    value = _owned_value(owner)
    kernel32 = _windows_kernel32()
    get_current_process = kernel32.GetCurrentProcess
    get_current_process.argtypes = []
    get_current_process.restype = wintypes.HANDLE
    duplicate_handle = kernel32.DuplicateHandle
    duplicate_handle.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    duplicate_handle.restype = wintypes.BOOL
    process = get_current_process()
    duplicate = wintypes.HANDLE()
    if not duplicate_handle(
        process,
        wintypes.HANDLE(value),
        process,
        ctypes.byref(duplicate),
        wintypes.DWORD(0),
        wintypes.BOOL(False),
        wintypes.DWORD(_WINDOWS_DUPLICATE_SAME_ACCESS),
    ):
        raise _windows_error("unable to duplicate native filesystem handle")
    duplicate_value = _windows_handle_int(duplicate)
    result = _OwnedHandle(
        duplicate_value,
        owner.device,
        owner.inode,
        owner.path,
        True,
        attributes=owner.attributes,
        final_path=owner.final_path,
        identity_known=False,
    )
    try:
        _windows_set_close_protection(duplicate_value, True)
        result.windows_protected = True
        result.identity_known = True
    except BaseException:
        _best_effort_close(result)
        raise
    return result


def _windows_verify_owner(
    owner: _OwnedHandle,
    *,
    directory: bool,
    expected_path: Path | None = None,
) -> None:
    value = _owned_value(owner)
    attributes, device, inode, links = _windows_file_info(value)
    if (
        device != owner.device
        or inode != owner.inode
        or bool(attributes & _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT)
        or bool(attributes & _WINDOWS_FILE_ATTRIBUTE_DIRECTORY) != directory
        or (not directory and links != 1)
    ):
        raise ValueError("native filesystem object identity or type changed")
    final_path = _windows_final_path(value)
    if owner.final_path and _windows_normalise_final(final_path) != _windows_normalise_final(
        owner.final_path
    ):
        raise ValueError("native filesystem object path binding changed")
    if expected_path is not None and _windows_normalise_final(final_path) != _windows_expected_path(
        expected_path
    ):
        raise ValueError("native filesystem path binding changed")


def _close_owned_handle(owner: _OwnedHandle) -> None:
    with _HANDLE_LOCK:
        value = owner.value
        if owner.closed or value is None:
            return
        if not _is_current_owned_handle(owner, value):
            _retire_owned_handle(owner)
            raise ValueError("native filesystem handle generation is stale; refusing close")
        _close_owned_handle_locked(owner)


def _close_owned_handle_locked(owner: _OwnedHandle) -> None:
    value = owner.value
    if owner.closed or value is None:
        return
    if owner.windows:
        if not owner.windows_protected:
            if owner.windows_unprotected_for_close:
                try:
                    _windows_set_close_protection(value, True)
                    flags = _windows_handle_flags(value)
                except OSError as error:
                    if getattr(error, "winerror", error.errno) == _WINDOWS_ERROR_INVALID_HANDLE:
                        _retire_owned_handle(owner)
                        return
                    raise
                if not flags & _WINDOWS_HANDLE_FLAG_PROTECT_FROM_CLOSE:
                    raise ValueError(
                        "native filesystem handle close protection is unavailable; retaining owner"
                    )
                owner.windows_protected = True
                owner.windows_unprotected_for_close = False
            else:
                if owner.identity_known:
                    _retire_owned_handle(owner)
                    raise ValueError("native filesystem handle is not protected; refusing close")
                try:
                    _windows_close_raw(value)
                except OSError as error:
                    if getattr(error, "winerror", error.errno) == _WINDOWS_ERROR_INVALID_HANDLE:
                        _retire_owned_handle(owner)
                        return
                    raise
                _retire_owned_handle(owner)
                return
        try:
            flags = _windows_handle_flags(value)
        except OSError as error:
            if getattr(error, "winerror", error.errno) == _WINDOWS_ERROR_INVALID_HANDLE:
                _retire_owned_handle(owner)
                return
            raise
        if not flags & _WINDOWS_HANDLE_FLAG_PROTECT_FROM_CLOSE:
            _retire_owned_handle(owner)
            raise ValueError("native filesystem handle is not protected; refusing close")
        if owner.identity_known:
            try:
                _, device, inode, _ = _windows_file_info(value)
            except OSError as error:
                if getattr(error, "winerror", error.errno) == _WINDOWS_ERROR_INVALID_HANDLE:
                    _retire_owned_handle(owner)
                    return
                raise
            if device != owner.device or inode != owner.inode:
                _retire_owned_handle(owner)
                raise ValueError("native filesystem handle identity changed; refusing close")
        try:
            _windows_set_close_protection(value, False)
            owner.windows_protected = False
            owner.windows_unprotected_for_close = True
            _windows_close_raw(value)
        except OSError as error:
            if getattr(error, "winerror", error.errno) == _WINDOWS_ERROR_INVALID_HANDLE:
                _retire_owned_handle(owner)
                return
            try:
                _windows_set_close_protection(value, True)
            except BaseException:
                owner.windows_protected = False
            else:
                owner.windows_protected = True
                owner.windows_unprotected_for_close = False
            raise
        _retire_owned_handle(owner)
        return
    if not owner.identity_known:
        try:
            os.close(value)
        except OSError as error:
            if error.errno == errno.EBADF:
                _retire_owned_handle(owner)
                return
            raise
        _retire_owned_handle(owner)
        return
    try:
        metadata = os.fstat(value)
    except OSError as error:
        if error.errno == errno.EBADF:
            _retire_owned_handle(owner)
            return
        raise
    if int(metadata.st_dev) != owner.device or int(metadata.st_ino) != owner.inode:
        _retire_owned_handle(owner)
        raise ValueError("descriptor identity changed; refusing close")
    try:
        os.close(value)
    except OSError as error:
        if error.errno == errno.EBADF:
            _retire_owned_handle(owner)
            return
        raise
    _retire_owned_handle(owner)


def _best_effort_close(owner: _OwnedHandle) -> None:
    try:
        _close_owned_handle(owner)
    except BaseException:
        _retain_cleanup_owner(owner)


def _release_root_binding(binding: _RootBinding | None) -> None:
    if binding is None:
        return
    owner = binding.fd
    if owner is None:
        binding.closed = True
        return
    try:
        _close_owned_handle(owner)
    except BaseException:
        _retain_cleanup_owner(owner)
        if owner.closed:
            binding.fd = None
            binding.closed = True
        raise
    binding.fd = None
    binding.closed = True


def _release_owned_handles(owners: list[_OwnedHandle]) -> list[BaseException]:
    failures: list[BaseException] = []
    for owner in tuple(owners):
        try:
            _close_owned_handle(owner)
        except BaseException as error:
            _retain_cleanup_owner(owner)
            failures.append(error)
        if owner.closed:
            owners[:] = [candidate for candidate in owners if candidate is not owner]
    return failures


def _finalize_manager_filesystem_owners(
    binding: _RootBinding | None,
    xplt_owners: list[_OwnedHandle],
) -> None:
    for owner in tuple(xplt_owners):
        _best_effort_close(owner)
        if owner.closed:
            xplt_owners[:] = [candidate for candidate in xplt_owners if candidate is not owner]
    _finalize_root_binding(binding)


def _finalize_root_binding(binding: _RootBinding | None) -> None:
    if binding is None or binding.fd is None:
        return
    try:
        _close_owned_handle(binding.fd)
    except BaseException:
        _retain_cleanup_owner(binding.fd)
        return
    binding.fd = None
    binding.closed = True


def _drain_pending_cleanup() -> None:
    with _HANDLE_LOCK:
        pending = tuple(_PENDING_CLEANUP)
        _PENDING_CLEANUP.clear()
    for owner in pending:
        try:
            _close_owned_handle(owner)
        except BaseException:
            _retain_cleanup_owner(owner)


atexit.register(_drain_pending_cleanup)


def _capture_posix_path_binding(path: Path) -> tuple[tuple[Path, int, int], ...]:
    identities: list[tuple[Path, int, int]] = []
    current = path
    while True:
        metadata = os.stat(os.fspath(current), follow_symlinks=False)
        if not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("attempt root pathname contains a non-directory")
        identities.append((current, int(metadata.st_dev), int(metadata.st_ino)))
        parent = current.parent
        if parent == current:
            break
        current = parent
    identities.reverse()
    return tuple(identities)


def _hold_root(root: Path) -> _RootBinding:
    with _HANDLE_LOCK:
        return _hold_root_locked(root)


def _hold_root_locked(root: Path) -> _RootBinding:
    """Hold the issued attempt root used for descriptor-relative reads."""

    path = _normalise_root(root)
    if os.path.normcase(os.path.realpath(os.fspath(path))) != os.path.normcase(os.fspath(path)):
        raise ValueError("attempt root must not be an alias")
    if os.name == "posix":
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        posix_owner: _OwnedHandle | None = None
        path_identities: tuple[tuple[Path, int, int], ...] = ()
        try:
            fd = os.open(os.fspath(path), flags)
            metadata = os.fstat(fd)
            posix_owner = _OwnedHandle(
                fd,
                int(metadata.st_dev),
                int(metadata.st_ino),
                path,
                False,
            )
            path_identities = _capture_posix_path_binding(path)
        except (OSError, ValueError) as error:
            if posix_owner is not None:
                _best_effort_close(posix_owner)
            elif "fd" in locals():
                with contextlib.suppress(OSError):
                    os.close(fd)
            raise ValueError("attempt root is not a held directory") from error
        if not stat.S_ISDIR(metadata.st_mode):
            if posix_owner is not None:
                _best_effort_close(posix_owner)
            raise ValueError("attempt root is not a directory")
        if posix_owner is None:  # pragma: no cover - defensive state guard
            raise ValueError("attempt root is not a held directory")
        if not path_identities or path_identities[-1][1:] != (
            posix_owner.device,
            posix_owner.inode,
        ):
            _best_effort_close(posix_owner)
            raise ValueError("attempt root pathname binding changed")
        return _RootBinding(
            path,
            posix_owner,
            posix_owner.device,
            posix_owner.inode,
            path_identities=path_identities,
        )

    windows_owner: _OwnedHandle | None = None
    try:
        windows_owner = _windows_open_absolute(
            path,
            _WINDOWS_FILE_LIST_DIRECTORY
            | _WINDOWS_FILE_READ_ATTRIBUTES
            | _WINDOWS_READ_CONTROL
            | _WINDOWS_SYNCHRONIZE,
            _WINDOWS_FILE_SHARE_READ | _WINDOWS_FILE_SHARE_WRITE,
            _WINDOWS_FILE_FLAG_BACKUP_SEMANTICS | _WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT,
        )
        _windows_verify_owner(windows_owner, directory=True, expected_path=path)
    except OSError as error:
        if windows_owner is not None:
            _best_effort_close(windows_owner)
        raise ValueError("attempt root is unavailable") from error
    except ValueError as error:
        if windows_owner is not None:
            _best_effort_close(windows_owner)
        raise ValueError("attempt root is not a directory") from error
    if windows_owner is None:  # pragma: no cover - defensive state guard
        raise ValueError("attempt root is unavailable")
    try:
        metadata = os.stat(os.fspath(path), follow_symlinks=False)
    except OSError as error:
        _best_effort_close(windows_owner)
        raise ValueError("attempt root is unavailable") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        _best_effort_close(windows_owner)
        raise ValueError("attempt root is not a directory")
    return _RootBinding(path, windows_owner, int(metadata.st_dev), int(metadata.st_ino))


def _verify_root(binding: _RootBinding | None) -> None:
    with _HANDLE_LOCK:
        _verify_root_locked(binding)


def _verify_posix_path_binding(binding: _RootBinding) -> None:
    identities = binding.path_identities
    if not identities:
        identities = ((binding.path, binding.device, binding.inode),)
    for path, device, inode in identities:
        try:
            metadata = os.stat(os.fspath(path), follow_symlinks=False)
        except OSError as error:
            raise TypeError("issued attempt root pathname binding changed") from error
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or int(metadata.st_dev) != device
            or int(metadata.st_ino) != inode
        ):
            raise TypeError("issued attempt root pathname binding changed")


def _verify_root_locked(binding: _RootBinding | None) -> None:
    if binding is None or binding.closed or binding.fd is None:
        raise TypeError("issued attempt root authority is unavailable")
    if os.name == "nt":
        try:
            _windows_verify_owner(binding.fd, directory=True, expected_path=binding.path)
        except (OSError, ValueError) as error:
            raise TypeError("issued attempt root authority changed") from error
        try:
            metadata = os.stat(os.fspath(binding.path), follow_symlinks=False)
        except OSError as error:
            raise TypeError("issued attempt root authority changed") from error
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or int(metadata.st_dev) != binding.device
            or int(metadata.st_ino) != binding.inode
        ):
            raise TypeError("issued attempt root authority changed")
        return
    try:
        metadata = os.fstat(_owned_value(binding.fd))
    except (OSError, ValueError) as error:
        raise TypeError("issued attempt root authority is unavailable") from error
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or int(metadata.st_dev) != binding.device
        or int(metadata.st_ino) != binding.inode
    ):
        raise TypeError("issued attempt root authority changed")
    _verify_posix_path_binding(binding)


def _manager_record(value: object) -> _ManagerRecord:
    if type(value) is not FbsAdapterManager:
        raise TypeError("value is not an exact FbsAdapterManager instance")
    try:
        record = object.__getattribute__(value, "_record")
    except AttributeError as error:
        raise TypeError("FbsAdapterManager is not manager-issued") from error
    if not isinstance(record, _ManagerRecord) or record.manager is not value:
        raise TypeError("FbsAdapterManager binding is invalid")
    return record


def _authority_record(value: object) -> _AuthorityRecord:
    if type(value) is not FbsAdapterAuthority:
        raise TypeError("value is not an exact FbsAdapterAuthority instance")
    try:
        record = object.__getattribute__(value, "_record")
    except AttributeError as error:
        raise TypeError("FbsAdapterAuthority is not manager-issued") from error
    if not isinstance(record, _AuthorityRecord) or record.authority is not value:
        raise TypeError("FbsAdapterAuthority binding is invalid")
    manager_record = _manager_record(record.manager)
    if manager_record.closed:
        raise TypeError("FbsAdapterManager authority is closed")
    if manager_record.authority is not value:
        raise TypeError("FbsAdapterAuthority manager binding is invalid")
    if manager_record.root_binding is not record.root_binding:
        raise TypeError("FbsAdapterAuthority root binding is invalid")
    if record.root_binding is not None:
        _verify_root(record.root_binding)
    return record


def _adapter_reader(adapter: object) -> _AdapterReader:
    candidate = getattr(adapter, "read_fields", None)
    if callable(candidate):
        return cast(_AdapterReader, candidate)
    if callable(adapter):
        return cast(_AdapterReader, adapter)
    raise TypeError("FBS adapter must provide read_fields(path, fields) or be callable")


def _normalise_root(root: str | Path) -> Path:
    return Path(os.path.abspath(os.fspath(root)))


class FbsAdapterManager:
    """Bind one adapter and issue one opaque validation authority."""

    __slots__ = ("_record", "_finalizer", "__weakref__")

    def __init__(
        self,
        adapter: object,
        runtime_identity: str,
        attempt_root: str | Path | None = None,
    ) -> None:
        try:
            object.__getattribute__(self, "_record")
        except AttributeError:
            pass
        else:
            raise TypeError("FbsAdapterManager is already initialized")
        if not isinstance(runtime_identity, str) or not runtime_identity.strip():
            raise ValueError("runtime_identity must be a non-empty string")
        reader = _adapter_reader(adapter)
        _drain_pending_cleanup()
        root = _normalise_root(attempt_root) if attempt_root is not None else None
        from .official_fbs import _official_adapter_identity

        official_profile = _official_adapter_identity(adapter, runtime_identity.strip(), root)
        official = official_profile is not None
        provenance = _OFFICIAL_PROVENANCE if official else _PROVENANCE
        root_binding = _hold_root(root) if root is not None else None
        object.__setattr__(self, "_finalizer", None)
        manager_record = _ManagerRecord(
            manager=self,
            adapter=adapter,
            reader=reader,
            runtime_identity=runtime_identity.strip(),
            official=official,
            profile=official_profile,
            provenance=provenance,
            attempt_root=root,
            root_binding=root_binding,
        )
        object.__setattr__(self, "_record", manager_record)
        if root_binding is not None:
            object.__setattr__(
                self,
                "_finalizer",
                weakref.finalize(
                    self,
                    _finalize_manager_filesystem_owners,
                    root_binding,
                    manager_record.xplt_owners,
                ),
            )

    def __del__(self) -> None:
        """Release only this manager's filesystem owner during finalization."""

        try:
            record = object.__getattribute__(self, "_record")
            binding = getattr(record, "root_binding", None)
            owners = getattr(record, "xplt_owners", [])
            _finalize_manager_filesystem_owners(binding, owners)
        except BaseException:
            return

    def __init_subclass__(cls, **kwargs: object) -> None:
        del kwargs
        raise TypeError("FbsAdapterManager cannot be subclassed")

    def __copy__(self) -> FbsAdapterManager:
        raise TypeError("FbsAdapterManager cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> FbsAdapterManager:
        del memo
        raise TypeError("FbsAdapterManager cannot be copied")

    def __reduce__(self) -> str | tuple[Any, ...]:
        raise TypeError("FbsAdapterManager cannot be serialized")

    def __reduce_ex__(self, protocol: SupportsIndex) -> str | tuple[Any, ...]:
        del protocol
        raise TypeError("FbsAdapterManager cannot be serialized")

    def __enter__(self) -> FbsAdapterManager:
        _manager_record(self)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: Any,
    ) -> None:
        del exc_type, exc_value, traceback
        self.close()

    def close(self) -> None:
        """Revoke authority and close this manager's exact filesystem owners."""

        record = _manager_record(self)
        record.closed = True
        failures = _release_owned_handles(record.xplt_owners)
        try:
            _release_root_binding(record.root_binding)
        except BaseException as error:
            failures.append(error)
        finalizer = object.__getattribute__(self, "_finalizer")
        root_closed = record.root_binding is None or record.root_binding.closed
        if isinstance(finalizer, weakref.finalize) and not record.xplt_owners and root_closed:
            finalizer.detach()
            object.__setattr__(self, "_finalizer", None)
        if len(failures) == 1:
            raise failures[0]
        if failures:
            raise BaseExceptionGroup("FBS filesystem owner cleanup failed", failures)

    def issue_authority(self) -> FbsAdapterAuthority:
        """Issue the manager's exact-instance authority."""

        record = _manager_record(self)
        if record.closed:
            raise TypeError("FbsAdapterManager is closed")
        if record.authority is None:
            authority = FbsAdapterAuthority(_AUTHORITY_TOKEN)
            authority_record = _AuthorityRecord(
                authority=authority,
                manager=self,
                adapter=record.adapter,
                reader=record.reader,
                runtime_identity=record.runtime_identity,
                official=record.official,
                profile=record.profile,
                provenance=record.provenance,
                attempt_root=record.attempt_root,
                root_binding=record.root_binding,
            )
            object.__setattr__(authority, "_record", authority_record)
            record.authority = authority
        authority = record.authority
        if authority is None:  # pragma: no cover - defensive state guard
            raise TypeError("FbsAdapterManager authority is unavailable")
        _authority_record(authority)
        return authority


def _normalise_fields(fields: Iterable[str]) -> tuple[str, ...]:
    if isinstance(fields, (str, bytes, bytearray)):
        raise ValueError("requested fields must be an iterable of strings")
    try:
        normalised = tuple(fields)
    except TypeError as error:
        raise ValueError("requested fields must be an iterable of strings") from error
    if not normalised:
        raise ValueError("at least one requested field is required")
    if any(not isinstance(field, str) or not field.strip() for field in normalised):
        raise ValueError("requested fields must contain non-empty strings")
    if len(set(normalised)) != len(normalised):
        raise ValueError("requested fields must not contain duplicates")
    return normalised


def _path(path: str | Path) -> Path:
    try:
        return Path(os.path.abspath(os.fspath(path)))
    except TypeError as error:
        raise ValueError("XPLT path must be a filesystem path") from error


@dataclass(slots=True)
class _OpenedXplt:
    fd: _OwnedHandle
    parent_fd: _OwnedHandle
    name: str
    device: int
    inode: int
    path: Path
    parent_path: Path
    root_binding: _RootBinding


def _fd_alias_parts(path: Path) -> tuple[int, tuple[str, ...]] | None:
    parts = path.parts
    if len(parts) < 5 or parts[:4] != (os.sep, "proc", "self", "fd"):
        return None
    fd_text = parts[4]
    if not fd_text.isdigit():
        return None
    return int(fd_text), tuple(parts[5:])


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    try:
        Path(os.path.realpath(path)).relative_to(Path(os.path.realpath(root)))
    except ValueError:
        return False
    return True


def _require_xplt(
    path: Path,
    root: Path | None,
    *,
    require_exists: bool = True,
) -> None:
    if path.suffix.lower() != ".xplt":
        raise ValueError("validation requires an XPLT path")
    if _fd_alias_parts(path) is not None:
        raise ValueError("descriptor XPLT paths require a bound physical root")
    if root is not None and not _inside(path, root):
        raise ValueError("XPLT path is outside the solver attempt outputs")
    if path.is_symlink():
        raise ValueError("XPLT path must not be a symlink")
    if not require_exists:
        return
    try:
        if not stat.S_ISREG(path.stat(follow_symlinks=False).st_mode):
            raise ValueError("XPLT path must be a regular file")
    except FileNotFoundError as error:
        raise FileNotFoundError(f"XPLT file does not exist: {path}") from error


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _windows_seek(value: int) -> None:
    kernel32 = _windows_kernel32()
    set_pointer = kernel32.SetFilePointerEx
    set_pointer.argtypes = [
        wintypes.HANDLE,
        ctypes.c_longlong,
        ctypes.POINTER(ctypes.c_longlong),
        wintypes.DWORD,
    ]
    set_pointer.restype = wintypes.BOOL
    new_position = ctypes.c_longlong()
    if not set_pointer(
        wintypes.HANDLE(value),
        ctypes.c_longlong(0),
        ctypes.byref(new_position),
        wintypes.DWORD(_WINDOWS_FILE_BEGIN),
    ):
        raise _windows_error("unable to seek held XPLT handle")


def _digest_windows(owner: _OwnedHandle) -> str:
    value = _owned_value(owner)
    kernel32 = _windows_kernel32()
    read_file = kernel32.ReadFile
    read_file.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    read_file.restype = wintypes.BOOL
    _windows_seek(value)
    digest = hashlib.sha256()
    buffer = ctypes.create_string_buffer(1024 * 1024)
    try:
        while True:
            count = wintypes.DWORD()
            if not read_file(
                wintypes.HANDLE(value),
                buffer,
                wintypes.DWORD(len(buffer)),
                ctypes.byref(count),
                None,
            ):
                raise _windows_error("unable to hash held XPLT handle")
            if count.value == 0:
                break
            digest.update(buffer.raw[: count.value])
    finally:
        _windows_seek(value)
    return digest.hexdigest()


def _digest_fd(fd: _OwnedHandle) -> str:
    if os.name == "nt":
        return _digest_windows(fd)
    value = _owned_value(fd)
    digest = hashlib.sha256()
    try:
        os.lseek(value, 0, os.SEEK_SET)
        while True:
            chunk = os.read(value, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        os.lseek(value, 0, os.SEEK_SET)
    except OSError as error:
        raise ValueError("unable to hash the held XPLT descriptor") from error
    return digest.hexdigest()


def _same_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return int(left.st_dev) == int(right.st_dev) and int(left.st_ino) == int(right.st_ino)


def _relative_to_root(path: Path, root: Path) -> tuple[str, ...]:
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise ValueError("XPLT path is outside the issued attempt root") from error
    parts = relative.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("XPLT path is not a direct relative artifact")
    return parts


def _open_bound_xplt(record: _AuthorityRecord, reported_path: Path) -> _OpenedXplt:
    with _HANDLE_LOCK:
        return _open_bound_xplt_locked(record, reported_path)


def _open_bound_xplt_locked(record: _AuthorityRecord, reported_path: Path) -> _OpenedXplt:
    root = record.attempt_root
    binding = record.root_binding
    if root is None or binding is None or binding.fd is None:
        raise ValueError("FBS validation requires a held issued attempt root")
    _verify_root(binding)
    parts = _relative_to_root(reported_path, root)
    if os.name == "nt":
        current = _windows_duplicate(binding.fd)
        try:
            if len(parts) == 1:
                parent_path = root
            else:
                parent_path = root.joinpath(*parts[:-1])
                for index, component in enumerate(parts[:-1]):
                    next_path = root.joinpath(*parts[: index + 1])
                    next_owner = _windows_open_relative(
                        current,
                        component,
                        _WINDOWS_FILE_LIST_DIRECTORY
                        | _WINDOWS_FILE_READ_ATTRIBUTES
                        | _WINDOWS_READ_CONTROL
                        | _WINDOWS_SYNCHRONIZE,
                        _WINDOWS_FILE_SHARE_READ | _WINDOWS_FILE_SHARE_WRITE,
                        _WINDOWS_FILE_DIRECTORY_FILE
                        | _WINDOWS_FILE_SYNCHRONOUS_IO_NONALERT
                        | _WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT,
                        next_path,
                    )
                    try:
                        _windows_verify_owner(next_owner, directory=True, expected_path=next_path)
                    except BaseException:
                        _best_effort_close(next_owner)
                        raise
                    try:
                        _close_owned_handle(current)
                    except BaseException:
                        _retain_cleanup_owner(next_owner)
                        raise
                    current = next_owner
            file_owner = _windows_open_relative(
                current,
                parts[-1],
                _WINDOWS_GENERIC_READ | _WINDOWS_FILE_READ_ATTRIBUTES | _WINDOWS_SYNCHRONIZE,
                _WINDOWS_FILE_SHARE_READ,
                _WINDOWS_FILE_NON_DIRECTORY_FILE
                | _WINDOWS_FILE_SYNCHRONOUS_IO_NONALERT
                | _WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT,
                reported_path,
            )
            try:
                _windows_verify_owner(file_owner, directory=False, expected_path=reported_path)
                _windows_verify_owner(current, directory=True, expected_path=parent_path)
                if not _windows_path_inside(file_owner.final_path, binding.fd.final_path):
                    raise ValueError("XPLT path is outside the held attempt root")
                return _OpenedXplt(
                    fd=file_owner,
                    parent_fd=current,
                    name=parts[-1],
                    device=file_owner.device,
                    inode=file_owner.inode,
                    path=reported_path,
                    parent_path=parent_path,
                    root_binding=binding,
                )
            except BaseException:
                _best_effort_close(file_owner)
                raise
        except BaseException:
            _best_effort_close(current)
            raise

    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    file_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    root_value = _owned_value(binding.fd)
    current_fd = os.dup(root_value)
    current = _OwnedHandle(current_fd, binding.device, binding.inode, root, False)
    try:
        for index, component in enumerate(parts[:-1]):
            next_fd = os.open(component, directory_flags, dir_fd=_owned_value(current))
            next_metadata = os.fstat(next_fd)
            next_owner = _OwnedHandle(
                next_fd,
                int(next_metadata.st_dev),
                int(next_metadata.st_ino),
                root.joinpath(*parts[: index + 1]),
                False,
            )
            if not stat.S_ISDIR(next_metadata.st_mode):
                _best_effort_close(next_owner)
                raise ValueError("XPLT parent is not a directory")
            try:
                _close_owned_handle(current)
            except BaseException:
                _retain_cleanup_owner(next_owner)
                raise
            current = next_owner
        file_fd = os.open(parts[-1], file_flags, dir_fd=_owned_value(current))
        file_owner = _OwnedHandle(
            file_fd,
            0,
            0,
            reported_path,
            False,
            identity_known=False,
        )
        try:
            metadata = os.fstat(file_fd)
            file_owner.device = int(metadata.st_dev)
            file_owner.inode = int(metadata.st_ino)
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("XPLT path must be a regular file")
            if int(metadata.st_nlink) != 1:
                raise ValueError("XPLT path must not be a hard link")
            return _OpenedXplt(
                fd=file_owner,
                parent_fd=current,
                name=parts[-1],
                device=int(metadata.st_dev),
                inode=int(metadata.st_ino),
                path=reported_path,
                parent_path=reported_path.parent,
                root_binding=binding,
            )
        except BaseException:
            _best_effort_close(file_owner)
            raise
    except BaseException:
        _best_effort_close(current)
        raise


def _verify_opened_xplt(opened: _OpenedXplt) -> None:
    if os.name == "nt":
        try:
            _verify_root(opened.root_binding)
            _windows_verify_owner(
                opened.parent_fd, directory=True, expected_path=opened.parent_path
            )
            _windows_verify_owner(opened.fd, directory=False, expected_path=opened.path)
            root_owner = opened.root_binding.fd
            if root_owner is None or not _windows_path_inside(
                opened.fd.final_path, root_owner.final_path
            ):
                raise ValueError("XPLT path is outside the held attempt root")
        except (OSError, ValueError, TypeError) as error:
            raise ValueError("XPLT changed or became unavailable") from error
        return
    try:
        _verify_root(opened.root_binding)
        path_metadata = os.stat(
            opened.name, dir_fd=_owned_value(opened.parent_fd), follow_symlinks=False
        )
        descriptor_metadata = os.fstat(_owned_value(opened.fd))
    except (OSError, TypeError, ValueError) as error:
        raise ValueError("XPLT changed or became unavailable") from error
    if (
        not stat.S_ISREG(path_metadata.st_mode)
        or int(path_metadata.st_nlink) != 1
        or not _same_identity(path_metadata, descriptor_metadata)
        or int(descriptor_metadata.st_dev) != opened.device
        or int(descriptor_metadata.st_ino) != opened.inode
    ):
        raise ValueError("XPLT changed or became unavailable")


def _close_opened_xplt(opened: _OpenedXplt) -> None:
    failures: list[BaseException] = []
    for owner in (opened.fd, opened.parent_fd):
        try:
            _close_owned_handle(owner)
        except BaseException as error:
            _retain_cleanup_owner(owner)
            failures.append(error)
    if failures:
        raise ValueError("unable to close held XPLT handles") from failures[0]


def _duplicate_xplt_owner(owner: _OwnedHandle, path: Path) -> _OwnedHandle:
    if type(owner) is not _OwnedHandle:
        raise TypeError("XPLT owner is invalid")
    if os.name == "nt":
        duplicate = _windows_duplicate(owner)
        try:
            _windows_verify_owner(duplicate, directory=False, expected_path=path)
        except BaseException:
            _best_effort_close(duplicate)
            raise
        return duplicate
    value = os.dup(_owned_value(owner))
    duplicate = _OwnedHandle(value, 0, 0, path, False, identity_known=False)
    try:
        metadata = os.fstat(value)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or int(metadata.st_nlink) != 1
            or int(metadata.st_dev) != owner.device
            or int(metadata.st_ino) != owner.inode
        ):
            raise ValueError("XPLT object identity changed before retention")
        duplicate.device = int(metadata.st_dev)
        duplicate.inode = int(metadata.st_ino)
        duplicate.identity_known = True
        return duplicate
    except BaseException:
        _best_effort_close(duplicate)
        raise


def _verify_validation_xplt_owner(record: _ValidationRecord) -> _OwnedHandle:
    owner = record.xplt_owner
    try:
        authority_record = _authority_record(record.authority)
        manager_record = _manager_record(authority_record.manager)
        if (
            type(owner) is not _OwnedHandle
            or owner.path != record.xplt_path
            or not any(candidate is owner for candidate in manager_record.xplt_owners)
        ):
            raise ValueError("retained XPLT owner binding is invalid")
        if os.name == "nt":
            _windows_verify_owner(owner, directory=False, expected_path=record.xplt_path)
        else:
            descriptor_metadata = os.fstat(_owned_value(owner))
            path_metadata = os.stat(os.fspath(record.xplt_path), follow_symlinks=False)
            if (
                not stat.S_ISREG(descriptor_metadata.st_mode)
                or not stat.S_ISREG(path_metadata.st_mode)
                or int(descriptor_metadata.st_nlink) != 1
                or int(path_metadata.st_nlink) != 1
                or not _same_identity(descriptor_metadata, path_metadata)
                or int(descriptor_metadata.st_dev) != owner.device
                or int(descriptor_metadata.st_ino) != owner.inode
            ):
                raise ValueError("retained XPLT object identity changed")
        if record.digest_before != record.digest_after or _digest_fd(owner) != record.digest_before:
            raise ValueError("retained XPLT content changed")
    except (OSError, TypeError, ValueError) as error:
        raise TypeError("FbsValidation XPLT binding is unavailable") from error
    return owner


def _attach_validation_xplt_owner(
    validation: FbsValidation,
    owner: _OwnedHandle,
) -> None:
    record = _issued_validation_record(validation)
    authority_record = _authority_record(record.authority)
    manager_record = _manager_record(authority_record.manager)
    if (
        record.official is not True
        or record.provenance != _OFFICIAL_PROVENANCE
        or record.xplt_owner is not None
        or type(owner) is not _OwnedHandle
        or any(candidate is owner for candidate in manager_record.xplt_owners)
    ):
        raise TypeError("official FBS XPLT owner issuance binding is invalid")
    if owner.path != record.xplt_path or _digest_fd(owner) != record.digest_before:
        raise TypeError("official FBS XPLT owner context binding is invalid")
    manager_record.xplt_owners.append(owner)
    record.xplt_owner = owner
    _verify_validation_xplt_owner(record)


def _require_validation_xplt_owner(validation: FbsValidation) -> _OwnedHandle:
    """Return the exact retained XPLT owner for a live official validation."""

    return _verify_validation_xplt_owner(_issued_validation_record(validation))


def _finite_value(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        try:
            return math.isfinite(float(value))
        except (OverflowError, ValueError):
            return False
    if isinstance(value, Mapping):
        return bool(value) and all(_finite_value(item) for item in value.values())
    if isinstance(value, (str, bytes, bytearray)):
        return False
    if isinstance(value, Sequence):
        return bool(value) and all(_finite_value(item) for item in value)
    return False


def _freeze_value(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {_freeze_value(key): _freeze_value(item) for key, item in value.items()}
        )
    if isinstance(value, (str, bytes, int, float, bool, type(None))):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_value(item) for item in value)
    if isinstance(value, Sequence):
        return tuple(_freeze_value(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class FbsValidation:
    """Immutable validation bound to one issued synthetic or official authority."""

    xplt_path: Path
    requested_fields: tuple[str, ...]
    available_fields: tuple[str, ...]
    values: Mapping[str, object]
    missing_fields: tuple[str, ...]
    non_finite_fields: tuple[str, ...]
    valid: bool
    official: bool = False
    provenance: str = _PROVENANCE
    issues: tuple[str, ...] = ()
    authority: FbsAdapterAuthority | None = None
    runtime_identity: str = ""
    digest_before: str = ""
    digest_after: str = ""
    _issuance: _ValidationRecord | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.authority is not None:
            record = _authority_record(self.authority)
            if self.runtime_identity != record.runtime_identity:
                raise ValueError("validation runtime identity does not match authority")
            if self.official is not record.official or self.provenance != record.provenance:
                raise ValueError("validation provenance does not match authority")
        values = dict(self.values)
        object.__setattr__(
            self,
            "values",
            MappingProxyType({key: _freeze_value(value) for key, value in values.items()}),
        )

    @property
    def all_requested_fields_finite(self) -> bool:
        return not self.missing_fields and not self.non_finite_fields


def _field_snapshot(fields: object) -> tuple[str, ...]:
    if isinstance(fields, (str, bytes, bytearray)):
        return ()
    try:
        values: tuple[object, ...] = tuple(cast(Iterable[object], fields))
    except TypeError:
        return ()
    return tuple(field for field in values if isinstance(field, str))


def _register_validation(
    validation: FbsValidation,
    authority: FbsAdapterAuthority,
    *,
    authority_record: _AuthorityRecord | None = None,
) -> FbsValidation:
    if type(validation) is not FbsValidation:
        raise TypeError("validation is not an exact FbsValidation instance")
    if authority_record is None:
        authority_record = _authority_record(authority)
    elif authority_record.authority is not authority:
        raise TypeError("validation authority binding is invalid")
    if validation.authority is not authority:
        raise TypeError("validation authority binding is invalid")
    try:
        existing = object.__getattribute__(validation, "_issuance")
    except AttributeError as error:  # pragma: no cover - exact dataclass guard
        raise TypeError("validation issuance state is unavailable") from error
    if existing is not None:
        raise TypeError("validation is already issued")
    record = _ValidationRecord(
        validation=validation,
        authority=authority,
        runtime_identity=authority_record.runtime_identity,
        xplt_path=validation.xplt_path,
        requested_fields=validation.requested_fields,
        available_fields=validation.available_fields,
        values=validation.values,
        missing_fields=validation.missing_fields,
        non_finite_fields=validation.non_finite_fields,
        valid=validation.valid,
        official=validation.official,
        provenance=validation.provenance,
        issues=validation.issues,
        digest_before=validation.digest_before,
        digest_after=validation.digest_after,
    )
    object.__setattr__(validation, "_issuance", record)
    return validation


def _unregister_validation(value: object) -> bool:
    """Revoke exactly one validation issued by this module.

    Rollback is deliberately identity based.  A caller-provided object or a
    replacement occupying the same integer key must never remove an issued
    validation belonging to another operation.
    """

    if type(value) is not FbsValidation:
        return False
    try:
        record = object.__getattribute__(value, "_issuance")
    except AttributeError:
        return False
    if not isinstance(record, _ValidationRecord) or record.validation is not value:
        return False
    if record.revoked:
        return False
    record.revoked = True
    xplt_owner = record.xplt_owner
    if xplt_owner is not None:
        manager_record: _ManagerRecord | None = None
        try:
            authority_record = object.__getattribute__(record.authority, "_record")
            if (
                isinstance(authority_record, _AuthorityRecord)
                and authority_record.authority is record.authority
            ):
                manager_record = _manager_record(authority_record.manager)
        except (AttributeError, TypeError):
            manager_record = None
        _best_effort_close(xplt_owner)
        if xplt_owner.closed:
            record.xplt_owner = None
            if manager_record is not None:
                manager_record.xplt_owners[:] = [
                    owner for owner in manager_record.xplt_owners if owner is not xplt_owner
                ]
    record.owner = None
    record.result = None
    record.official_result = None
    return True


def _adopt_validation(value: object, owner: object, result: object) -> FbsValidation:
    record = _issued_validation_record(value)
    if record.owner is not None or record.result is not None:
        raise TypeError("FbsValidation is already adopted")
    record.owner = owner
    record.result = result
    return cast(FbsValidation, value)


def _issued_validation_record(value: object) -> _ValidationRecord:
    if type(value) is not FbsValidation:
        raise TypeError("value is not an exact FbsValidation instance")
    try:
        record = object.__getattribute__(value, "_issuance")
    except AttributeError as error:
        raise TypeError("FbsValidation was not issued by validate_requested_fields") from error
    if not isinstance(record, _ValidationRecord) or record.validation is not value:
        raise TypeError("FbsValidation was not issued by validate_requested_fields")
    if record.revoked:
        raise TypeError("FbsValidation issuance has been revoked")
    try:
        authority_record = _authority_record(record.authority)
        matches = (
            authority_record.runtime_identity == record.runtime_identity
            and record.xplt_path.is_absolute()
            and value.authority is record.authority
            and value.runtime_identity == record.runtime_identity
            and value.xplt_path == record.xplt_path
            and value.requested_fields == record.requested_fields
            and value.available_fields == record.available_fields
            and value.values is record.values
            and value.missing_fields == record.missing_fields
            and value.non_finite_fields == record.non_finite_fields
            and value.valid is record.valid
            and value.official is record.official
            and value.provenance == record.provenance
            and value.issues == record.issues
            and value.digest_before == record.digest_before
            and value.digest_after == record.digest_after
        )
    except (AttributeError, TypeError, ValueError):
        matches = False
    if not matches:
        raise TypeError("FbsValidation issuance binding is invalid")
    return record


def _require_issued_validation(
    value: object,
    authority: FbsAdapterAuthority,
    xplt_path: str | Path,
    requested_fields: Iterable[str],
) -> FbsValidation:
    record = _issued_validation_record(value)
    owner = record.owner
    result = record.result
    try:
        latched = object.__getattribute__(owner, "_result_latch") if owner is not None else None
        result_validation = getattr(result, "fbs_validation", None)
    except (AttributeError, TypeError):
        latched = None
        result_validation = None
    if owner is None or result is None or latched is not result or result_validation is not value:
        raise TypeError("FbsValidation is not adopted by a supervisor transaction")
    expected_authority = _authority_record(authority)
    expected_path = _path(xplt_path)
    try:
        expected_fields = _normalise_fields(requested_fields)
    except (TypeError, ValueError) as error:
        raise TypeError("validation context is invalid") from error
    if (
        record.authority is not authority
        or expected_authority.runtime_identity != record.runtime_identity
        or expected_path != record.xplt_path
        or expected_fields != record.requested_fields
    ):
        raise TypeError("validation does not match the requested authority context")
    return cast(FbsValidation, value)


def _invalid_validation(
    authority: FbsAdapterAuthority,
    xplt_path: str | Path,
    requested_fields: object,
    issue: str,
    *,
    digest_before: str = "",
    digest_after: str = "",
    authority_record: _AuthorityRecord | None = None,
) -> FbsValidation:
    record = authority_record or _authority_record(authority)
    if record.authority is not authority:
        raise TypeError("validation authority binding is invalid")
    fields = _field_snapshot(requested_fields)
    validation = FbsValidation(
        authority=None,
        runtime_identity=record.runtime_identity,
        xplt_path=_path(xplt_path),
        requested_fields=fields,
        available_fields=(),
        values=MappingProxyType({}),
        missing_fields=fields,
        non_finite_fields=(),
        valid=False,
        official=record.official,
        provenance=record.provenance,
        digest_before=digest_before,
        digest_after=digest_after,
        issues=(issue,),
    )
    object.__setattr__(validation, "authority", authority)
    return _register_validation(validation, authority, authority_record=record)


def _build_validation(
    authority: FbsAdapterAuthority,
    record: _AuthorityRecord,
    reported_path: Path,
    fields: tuple[str, ...],
    values: Mapping[str, object],
    digest_before: str,
    digest_after: str,
) -> FbsValidation:
    available = tuple(values)
    missing = tuple(field for field in fields if field not in values)
    non_finite = tuple(
        field for field in fields if field in values and not _finite_value(values[field])
    )
    issues: list[str] = []
    if missing:
        issues.append("missing requested fields: " + ", ".join(missing))
    if non_finite:
        issues.append("non-finite requested fields: " + ", ".join(non_finite))
    validation = FbsValidation(
        authority=authority,
        runtime_identity=record.runtime_identity,
        xplt_path=reported_path,
        requested_fields=fields,
        available_fields=available,
        values=values,
        missing_fields=missing,
        non_finite_fields=non_finite,
        valid=not issues and digest_before == digest_after,
        official=record.official,
        provenance=record.provenance,
        digest_before=digest_before,
        digest_after=digest_after,
        issues=tuple(issues),
    )
    return _register_validation(validation, authority)


def validate_requested_fields(
    authority: FbsAdapterAuthority,
    xplt_path: str | Path,
    requested_fields: Iterable[str],
    *,
    attempt_root: str | Path | None = None,
    physical_path: str | Path | None = None,
    physical_root: str | Path | None = None,
) -> FbsValidation:
    """Read and validate requested fields through an issued authority."""

    record = _authority_record(authority)
    fields = _normalise_fields(requested_fields)
    if physical_path is not None or physical_root is not None:
        raise ValueError("caller-supplied physical paths cannot establish authority")
    if attempt_root is not None:
        supplied_root = _normalise_root(attempt_root)
        if record.attempt_root is None or supplied_root != record.attempt_root:
            raise ValueError("caller-supplied attempt root does not match authority")
    if record.attempt_root is None or record.root_binding is None:
        raise ValueError("FBS validation requires an issued attempt-root authority")
    reported_path = _path(xplt_path)
    _require_xplt(reported_path, record.attempt_root, require_exists=False)
    _require_xplt(reported_path, record.attempt_root)

    opened: _OpenedXplt | None = None
    path = reported_path
    digest_before = ""
    digest_after = ""
    raw_result: object = None
    retained_xplt_owner: _OwnedHandle | None = None
    try:
        if os.name == "posix":
            opened = _open_bound_xplt(record, reported_path)
            path = Path("/proc/self/fd") / str(_owned_value(opened.fd))
            digest_before = _digest_fd(opened.fd)
        else:
            opened = _open_bound_xplt(record, reported_path)
            _verify_opened_xplt(opened)
            digest_before = _digest_fd(opened.fd)
        try:
            raw_result = record.reader(path, fields)
        except Exception as error:
            try:
                if opened is not None:
                    _verify_opened_xplt(opened)
                    digest_after = _digest_fd(opened.fd)
                else:
                    _require_xplt(reported_path, record.attempt_root)
                    digest_after = _digest(path)
            except Exception:
                digest_after = ""
            return _invalid_validation(
                authority,
                reported_path,
                fields,
                f"adapter execution failed: {error}",
                digest_before=digest_before,
                digest_after=digest_after,
                authority_record=record,
            )

        try:
            if opened is not None:
                _verify_opened_xplt(opened)
                digest_after = _digest_fd(opened.fd)
                _verify_opened_xplt(opened)
                if record.official:
                    retained_xplt_owner = _duplicate_xplt_owner(opened.fd, reported_path)
            else:
                _require_xplt(reported_path, record.attempt_root)
                digest_after = _digest(path)
        except Exception as error:
            return _invalid_validation(
                authority,
                reported_path,
                fields,
                f"XPLT changed or became unavailable during adapter execution: {error}",
                digest_before=digest_before,
                authority_record=record,
            )
    finally:
        if opened is not None:
            try:
                _close_opened_xplt(opened)
            except BaseException:
                if retained_xplt_owner is not None:
                    _best_effort_close(retained_xplt_owner)
                raise

    try:
        if digest_before != digest_after:
            return _invalid_validation(
                authority,
                reported_path,
                fields,
                "adapter mutated the XPLT artifact",
                digest_before=digest_before,
                digest_after=digest_after,
                authority_record=record,
            )
        if not isinstance(raw_result, Mapping):
            return _invalid_validation(
                authority,
                reported_path,
                fields,
                "adapter must return a field mapping",
                digest_before=digest_before,
                digest_after=digest_after,
                authority_record=record,
            )
        try:
            values: dict[str, object] = dict(raw_result)
        except Exception as error:
            return _invalid_validation(
                authority,
                reported_path,
                fields,
                f"adapter returned an unreadable field mapping: {error}",
                digest_before=digest_before,
                digest_after=digest_after,
                authority_record=record,
            )
        if any(not isinstance(field, str) for field in values):
            return _invalid_validation(
                authority,
                reported_path,
                fields,
                "adapter field mapping keys must be strings",
                digest_before=digest_before,
                digest_after=digest_after,
                authority_record=record,
            )
        validation = _build_validation(
            authority,
            record,
            reported_path,
            fields,
            values,
            digest_before,
            digest_after,
        )
        if record.official:
            try:
                from .official_fbs import _adopt_official_fbs_result

                if retained_xplt_owner is None:  # pragma: no cover - official path guard
                    raise TypeError("official FBS XPLT owner is unavailable")
                _attach_validation_xplt_owner(validation, retained_xplt_owner)
                retained_xplt_owner = None
                _adopt_official_fbs_result(validation, raw_result, record.adapter)
            except Exception as error:
                _unregister_validation(validation)
                return _invalid_validation(
                    authority,
                    reported_path,
                    fields,
                    f"official FBS result receipt validation failed: {error}",
                    digest_before=digest_before,
                    digest_after=digest_after,
                    authority_record=record,
                )
        return validation
    finally:
        if retained_xplt_owner is not None:
            _best_effort_close(retained_xplt_owner)
