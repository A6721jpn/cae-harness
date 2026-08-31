"""Read-only, headless FEBio executable diagnostics."""

from __future__ import annotations

import atexit
import ctypes
import errno
import hashlib
import math
import os
import re
import stat
import subprocess
import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, cast

__all__ = [
    "FebioRuntimeDiagnostic",
    "RuntimeDiagnostic",
    "RuntimeIdentity",
    "RuntimeProbeDiagnostic",
    "RuntimeProbeError",
    "probe_febio",
    "probe_runtime",
    "validate_runtime_diagnostic",
]

_DEFAULT_TIMEOUT_SECONDS: Final[float] = 5.0
_REPARSE_POINT: Final[int] = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_VERSION_BANNER: Final[re.Pattern[str]] = re.compile(r"version (?P<version>[0-9]+\.[0-9]+\.[0-9]+)")
_WINDOWS_GENERIC_READ: Final[int] = 0x80000000
_WINDOWS_FILE_SHARE_READ: Final[int] = 0x00000001
_WINDOWS_OPEN_EXISTING: Final[int] = 3
_WINDOWS_FILE_ATTRIBUTE_NORMAL: Final[int] = 0x00000080
_WINDOWS_INVALID_HANDLE_VALUE: Final[int | None] = ctypes.c_void_p(-1).value
_WINDOWS_DUPLICATE_SAME_ACCESS: Final[int] = 0x00000002
_WINDOWS_ERROR_INVALID_HANDLE: Final[int] = 6
_WINDOWS_ERROR_NOT_SAME_OBJECT: Final[int] = 1656
_WINDOWS_FD_CLOSE_LOCK = threading.RLock()


class RuntimeProbeError(RuntimeError):
    """Raised when a FEBio runtime cannot be safely identified."""


@dataclass(frozen=True, slots=True)
class FebioRuntimeDiagnostic:
    """Immutable identity evidence; launch accepts only probe-issued instances."""

    path: Path
    sha256: str
    size: int
    version: str

    def __post_init__(self) -> None:
        path = Path(self.path)
        if not path.is_absolute():
            raise ValueError("runtime diagnostic path must be absolute")
        if not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise ValueError("runtime diagnostic SHA256 is invalid")
        if isinstance(self.size, bool) or not isinstance(self.size, int) or self.size < 0:
            raise ValueError("runtime diagnostic size is invalid")
        if _VERSION_BANNER.fullmatch(f"version {self.version}") is None:
            raise ValueError("runtime diagnostic version is invalid")
        object.__setattr__(self, "path", path)

    def to_dict(self) -> dict[str, object]:
        return {
            "path": os.fspath(self.path),
            "sha256": self.sha256,
            "size": self.size,
            "version": self.version,
        }


RuntimeDiagnostic = FebioRuntimeDiagnostic
RuntimeIdentity = FebioRuntimeDiagnostic
RuntimeProbeDiagnostic = FebioRuntimeDiagnostic


@dataclass(frozen=True, slots=True)
class _FileSnapshot:
    device: int
    inode: int
    nlink: int
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class _RuntimeIssuance:
    diagnostic: FebioRuntimeDiagnostic
    path: Path
    sha256: str
    size: int
    version: str
    before: _FileSnapshot
    after: _FileSnapshot


@dataclass(slots=True)
class _RuntimeLaunchClaim:
    """A short-lived Windows claim held across suspended process creation."""

    path: Path
    snapshot: _FileSnapshot
    handle: int | None
    native_handle: int | None = None
    raw_handle: int | None = None

    def authenticate(self, executable_path: str | Path) -> None:
        if _normalise_path(executable_path) != _normalise_path(self.path):
            raise RuntimeProbeError("launched process executable image does not match runtime")
        if self.handle is None:
            return
        current = _snapshot_from_handle(self.path, self.handle)
        if current != self.snapshot:
            raise RuntimeProbeError("runtime executable digest or identity changed before resume")

    def close(self) -> None:
        if self.raw_handle is not None:
            raw_handle = self.raw_handle
            _windows_close_native_handle(raw_handle)
            if self.raw_handle == raw_handle:
                self.raw_handle = None
        if self.handle is None and self.native_handle is None:
            return
        if os.name == "nt" and self.native_handle is not None:
            try:
                _windows_close_owned_fd(self, "handle", "native_handle")
            except BaseException as error:
                raise RuntimeProbeError("runtime executable claim could not be closed") from error
            return
        handle = self.handle
        if handle is None:
            return
        try:
            os.close(handle)
        except OSError as error:
            raise RuntimeProbeError("runtime executable claim could not be closed") from error
        self.handle = None


_RUNTIME_REGISTRY: dict[int, _RuntimeIssuance] = {}


class _WindowsOwnedFd(int):
    """An int-compatible CRT descriptor paired with its exact native guard."""

    handle: int
    native_handle: int

    def __new__(cls, handle: int, native_handle: int) -> _WindowsOwnedFd:
        owned = int.__new__(cls, handle)
        owned.handle = handle
        owned.native_handle = native_handle
        return owned


@dataclass(slots=True)
class _WindowsHandleOwner:
    """Durable owner while a raw handle is being converted to a CRT descriptor."""

    raw_handle: int | None = None
    handle: int | None = None
    native_handle: int | None = None
    close_native_handle: Callable[[int], None] | None = None

    def close(self) -> None:
        close_native = self.close_native_handle or _windows_close_native_handle
        if self.raw_handle is not None:
            raw_handle = self.raw_handle
            close_native(raw_handle)
            if self.raw_handle == raw_handle:
                self.raw_handle = None
        if self.handle is not None or self.native_handle is not None:
            _windows_close_owned_fd(
                self,
                "handle",
                "native_handle",
                close_native_handle=close_native,
            )


_RUNTIME_DURABLE_CLAIMS: list[object] = []


def _claim_has_owned_handles(claim: object) -> bool:
    return any(
        getattr(claim, attribute, None) is not None
        for attribute in ("raw_handle", "handle", "native_handle")
    )


def _retain_runtime_claim(claim: object) -> None:
    if _claim_has_owned_handles(claim) and not any(
        held is claim for held in _RUNTIME_DURABLE_CLAIMS
    ):
        _RUNTIME_DURABLE_CLAIMS.append(claim)


def _discard_runtime_claim(claim: object) -> None:
    _RUNTIME_DURABLE_CLAIMS[:] = [held for held in _RUNTIME_DURABLE_CLAIMS if held is not claim]


def _windows_raw_handle(value: object) -> int:
    raw_value = value.value if isinstance(value, ctypes.c_void_p) else value
    try:
        handle = 0 if raw_value is None else int(cast(Any, raw_value))
    except (TypeError, ValueError, OverflowError):
        handle = 0
    if not handle or handle in {-1, _WINDOWS_INVALID_HANDLE_VALUE}:
        raise OSError(errno.EBADF, "Windows handle is unavailable")
    return handle


def _windows_native_handle_for_fd(fd: int) -> int:
    import msvcrt

    try:
        return _windows_raw_handle(msvcrt.get_osfhandle(fd))
    except OSError:
        raise
    except BaseException as error:
        raise OSError("Windows CRT descriptor is unavailable") from error


def _windows_duplicate_native_handle(native_handle: int) -> int:
    """Duplicate one native handle into an independently owned native handle."""

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
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
    source_process = kernel32.GetCurrentProcess()
    duplicate = ctypes.c_void_p()
    ctypes.set_last_error(0)
    if not kernel32.DuplicateHandle(
        source_process,
        ctypes.c_void_p(native_handle),
        source_process,
        ctypes.byref(duplicate),
        0,
        0,
        _WINDOWS_DUPLICATE_SAME_ACCESS,
    ):
        error = ctypes.get_last_error()
        raise OSError(error, "unable to duplicate Windows file handle")
    try:
        return _windows_raw_handle(duplicate)
    except BaseException:
        with suppress(BaseException):
            kernel32.CloseHandle(duplicate)
        raise


def _windows_duplicate_fd_handle(fd: int) -> int:
    """Duplicate a CRT descriptor into an independently owned native handle."""

    return _windows_duplicate_native_handle(_windows_native_handle_for_fd(fd))


def _windows_close_native_handle(native_handle: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    ctypes.set_last_error(0)
    if not kernel32.CloseHandle(ctypes.c_void_p(native_handle)):
        error = ctypes.get_last_error()
        raise OSError(error, "unable to close Windows file handle")


def _windows_compare_object_handles(first_handle: int, second_handle: int) -> bool | None:
    """Compare native handles as kernel objects, failing closed if unavailable."""

    compare = None
    for library_name in ("kernel32", "kernelbase"):
        try:
            library = ctypes.WinDLL(library_name, use_last_error=True)
            compare = library.CompareObjectHandles
        except (AttributeError, OSError):
            continue
        break
    if compare is None:
        return None
    try:
        compare.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        compare.restype = ctypes.c_int
        ctypes.set_last_error(0)
        result = compare(
            ctypes.c_void_p(first_handle),
            ctypes.c_void_p(second_handle),
        )
        if result:
            return True
        error = ctypes.get_last_error()
        if error in {_WINDOWS_ERROR_NOT_SAME_OBJECT, _WINDOWS_ERROR_INVALID_HANDLE}:
            return False
        return None
    except BaseException:
        return None


def _windows_fd_identity_matches(fd: int, native_handle: int) -> bool | None:
    """Return false for a retired/reused CRT slot and None for uncertainty."""

    try:
        current_native = _windows_native_handle_for_fd(fd)
    except OSError as error:
        if getattr(error, "winerror", None) == 6 or getattr(error, "errno", None) in {9}:
            return False
        return None
    try:
        return _windows_compare_object_handles(current_native, native_handle)
    except BaseException:
        return None


def _windows_close_owned_fd(
    owner: object,
    fd_attribute: str,
    native_attribute: str,
    *,
    close_native_handle: Callable[[int], None] | None = None,
) -> None:
    """Close an owned CRT slot only while its native object identity is proven."""

    close_native = close_native_handle or _windows_close_native_handle
    with _WINDOWS_FD_CLOSE_LOCK:
        fd = getattr(owner, fd_attribute)
        native_handle = getattr(owner, native_attribute)
        if fd is None and native_handle is None:
            return
        if native_handle is None:
            if os.name == "nt":
                raise OSError("Windows CRT descriptor has no exact native guard")
            if fd is None:
                return
            os.close(fd)
            setattr(owner, fd_attribute, None)
            return
        if fd is None:
            close_native(native_handle)
            setattr(owner, native_attribute, None)
            return

        identity_matches = _windows_fd_identity_matches(fd, native_handle)
        if identity_matches is None:
            raise OSError("unable to verify Windows CRT descriptor identity")
        if not identity_matches:
            setattr(owner, fd_attribute, None)
            close_native(native_handle)
            setattr(owner, native_attribute, None)
            return

        try:
            os.close(fd)
        except BaseException:
            after_close = _windows_fd_identity_matches(fd, native_handle)
            if after_close is not False:
                raise
            setattr(owner, fd_attribute, None)
            try:
                close_native(native_handle)
            except BaseException:
                raise
            setattr(owner, native_attribute, None)
            return

        setattr(owner, fd_attribute, None)
        close_native(native_handle)
        setattr(owner, native_attribute, None)


def _windows_convert_raw_handle(
    raw_handle: int,
    descriptor_flags: int,
    *,
    duplicate_native_handle: Callable[[int], int] | None = None,
    close_native_handle: Callable[[int], None] | None = None,
) -> _WindowsOwnedFd:
    """Guard raw H1 as H2 before transferring H1 into the CRT descriptor table."""

    owner = _WindowsHandleOwner(
        raw_handle=raw_handle,
        close_native_handle=close_native_handle,
    )
    _retain_runtime_claim(owner)
    duplicate_native = duplicate_native_handle or _windows_duplicate_native_handle
    try:
        guard = duplicate_native(raw_handle)
        owner.native_handle = guard
        import msvcrt

        opened = msvcrt.open_osfhandle(
            raw_handle,
            descriptor_flags | getattr(os, "O_BINARY", 0),
        )
        owner.handle = int(opened)
        owner.raw_handle = None
        result = _WindowsOwnedFd(owner.handle, guard)
    except BaseException:
        try:
            owner.close()
        except BaseException:
            _retain_runtime_claim(owner)
        else:
            _discard_runtime_claim(owner)
        raise
    _discard_runtime_claim(owner)
    return result


def _drain_runtime_claims(
    claims: list[object] = _RUNTIME_DURABLE_CLAIMS,
) -> None:
    """Retry only exact module-owned conversion and validation claims."""

    for claim in tuple(claims):
        if not _claim_has_owned_handles(claim):
            _discard_runtime_claim(claim)
            continue
        try:
            close = cast(Any, claim).close
            close()
        except BaseException:
            continue
        if not _claim_has_owned_handles(claim):
            _discard_runtime_claim(claim)


atexit.register(_drain_runtime_claims)


def _absolute_path(value: str | Path) -> Path:
    return Path(os.path.abspath(os.fspath(Path(value).expanduser())))


def _normalise_path(value: str | Path) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(value))))


def _reject_alias(path: Path) -> None:
    try:
        for ancestor in (path, *path.parents):
            if not os.path.lexists(os.fspath(ancestor)):
                continue
            metadata = ancestor.lstat()
            if stat.S_ISLNK(metadata.st_mode) or bool(
                getattr(metadata, "st_file_attributes", 0) & _REPARSE_POINT
            ):
                raise RuntimeProbeError(f"FEBio executable path contains an alias: {path}")
    except OSError as error:
        raise RuntimeProbeError(f"unable to inspect FEBio executable: {path}") from error


def _validate_target(path: Path) -> os.stat_result:
    _reject_alias(path)
    try:
        metadata = path.lstat()
    except OSError as error:
        raise RuntimeProbeError(f"FEBio executable does not exist: {path}") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise RuntimeProbeError(f"FEBio executable is not a regular file: {path}")
    if metadata.st_nlink != 1:
        raise RuntimeProbeError(f"FEBio executable must not be a hardlink: {path}")
    if os.name != "nt" and not metadata.st_mode & 0o111:
        raise RuntimeProbeError(f"FEBio executable is not executable: {path}")
    return metadata


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        (left.st_dev, left.st_ino, left.st_nlink, left.st_size)
        == (right.st_dev, right.st_ino, right.st_nlink, right.st_size)
        and stat.S_ISREG(left.st_mode)
        and stat.S_ISREG(right.st_mode)
    )


def _snapshot(path: Path) -> _FileSnapshot:
    metadata = _validate_target(path)
    try:
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if not _same_file(metadata, opened):
                raise RuntimeProbeError(f"FEBio executable changed while opening: {path}")
            hasher = hashlib.sha256()
            while chunk := stream.read(1024 * 1024):
                hasher.update(chunk)
            finished = os.fstat(stream.fileno())
    except OSError as error:
        raise RuntimeProbeError(f"unable to read FEBio executable: {path}") from error
    if not _same_file(metadata, finished):
        raise RuntimeProbeError(f"FEBio executable changed while hashing: {path}")
    return _FileSnapshot(
        metadata.st_dev, metadata.st_ino, metadata.st_nlink, metadata.st_size, hasher.hexdigest()
    )


def _snapshot_from_handle(path: Path, handle: int) -> _FileSnapshot:
    metadata = _validate_target(path)
    try:
        opened = os.fstat(handle)
        if not _same_file(metadata, opened):
            raise RuntimeProbeError(
                f"FEBio executable changed while acquiring launch claim: {path}"
            )
        os.lseek(handle, 0, os.SEEK_SET)
        hasher = hashlib.sha256()
        while chunk := os.read(handle, 1024 * 1024):
            hasher.update(chunk)
        finished = os.fstat(handle)
    except RuntimeProbeError:
        raise
    except OSError as error:
        raise RuntimeProbeError(f"unable to read FEBio executable launch claim: {path}") from error
    if not _same_file(metadata, finished):
        raise RuntimeProbeError(f"FEBio executable changed while acquiring launch claim: {path}")
    return _FileSnapshot(
        metadata.st_dev, metadata.st_ino, metadata.st_nlink, metadata.st_size, hasher.hexdigest()
    )


def _windows_open_runtime_claim(path: Path) -> int:
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
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    ctypes.set_last_error(0)
    raw_handle = kernel32.CreateFileW(
        os.fspath(path),
        _WINDOWS_GENERIC_READ,
        _WINDOWS_FILE_SHARE_READ,
        None,
        _WINDOWS_OPEN_EXISTING,
        _WINDOWS_FILE_ATTRIBUTE_NORMAL,
        None,
    )
    raw_value = raw_handle.value if isinstance(raw_handle, ctypes.c_void_p) else raw_handle
    try:
        value = 0 if raw_value is None else int(raw_value)
    except (TypeError, ValueError, OverflowError):
        value = 0
    if not value or value in {-1, _WINDOWS_INVALID_HANDLE_VALUE}:
        error = ctypes.get_last_error()
        raise RuntimeProbeError(f"unable to hold FEBio executable identity: {path} ({error})")
    descriptor_flags = os.O_RDONLY | getattr(os, "O_NOINHERIT", 0)
    return _windows_convert_raw_handle(value, descriptor_flags)


def _validate_timeout(timeout_seconds: float) -> float:
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
        raise ValueError("runtime probe timeout must be a positive finite number")
    timeout = float(timeout_seconds)
    if timeout <= 0 or not math.isfinite(timeout):
        raise ValueError("runtime probe timeout must be a positive finite number")
    return timeout


def _parse_version(stdout: bytes, stderr: bytes, path: Path) -> str:
    combined = b"\n".join((stdout, stderr)).decode("utf-8", errors="replace")
    versions = [
        match.group("version")
        for line in combined.splitlines()
        if (match := _VERSION_BANNER.fullmatch(line.strip())) is not None
    ]
    if len(versions) != 1:
        raise RuntimeProbeError(f"FEBio version banner is missing or not exact: {path}")
    return versions[0]


def _run_process(path: Path, timeout: float) -> tuple[bytes, bytes]:
    command = [os.fspath(path)]
    try:
        process = subprocess.Popen(
            command,
            shell=False,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as error:
        raise RuntimeProbeError(f"unable to launch FEBio executable: {path}") from error

    try:
        stdout, stderr = process.communicate(input=b"quit\n", timeout=timeout)
    except subprocess.TimeoutExpired as error:
        with suppress(OSError):
            process.kill()
        with suppress(subprocess.TimeoutExpired):
            process.communicate(timeout=0.1)
        raise RuntimeProbeError(f"FEBio runtime probe timed out: {path}") from error
    return_code = process.returncode
    if return_code != 0:
        raise RuntimeProbeError(f"FEBio runtime did not have a clean exit: {path} ({return_code})")
    return stdout, stderr


def probe_febio(
    executable: str | Path,
    *,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> FebioRuntimeDiagnostic:
    """Probe one exact executable without shell or filesystem writes."""

    path = _absolute_path(executable)
    timeout = _validate_timeout(timeout_seconds)
    before = _snapshot(path)
    run_error: RuntimeProbeError | None = None
    stdout = stderr = b""
    try:
        stdout, stderr = _run_process(path, timeout)
    except RuntimeProbeError as error:
        run_error = error
    after = _snapshot(path)
    if after != before:
        raise RuntimeProbeError(f"FEBio executable changed during probe: {path}")
    if run_error is not None:
        raise run_error
    version = _parse_version(stdout, stderr, path)
    diagnostic = FebioRuntimeDiagnostic(
        path,
        after.sha256,
        after.size,
        version,
    )
    _RUNTIME_REGISTRY[id(diagnostic)] = _RuntimeIssuance(
        diagnostic,
        path,
        after.sha256,
        after.size,
        version,
        before,
        after,
    )
    return diagnostic


def validate_runtime_diagnostic(value: object) -> FebioRuntimeDiagnostic:
    """Fail closed unless ``value`` is the live result of an unchanged probe."""

    if type(value) is not FebioRuntimeDiagnostic:
        raise RuntimeProbeError("runtime diagnostic must be an exact probe-issued capability")
    issuance = _RUNTIME_REGISTRY.get(id(value))
    if issuance is None or issuance.diagnostic is not value:
        raise RuntimeProbeError("runtime diagnostic was not issued by probe_febio")

    diagnostic = value
    try:
        fields_match = (
            diagnostic.path == issuance.path
            and diagnostic.sha256 == issuance.sha256
            and diagnostic.size == issuance.size
            and diagnostic.version == issuance.version
        )
    except Exception as error:
        raise RuntimeProbeError("runtime diagnostic state is invalid") from error
    if not fields_match:
        raise RuntimeProbeError("runtime diagnostic state was modified")
    if issuance.before != issuance.after:
        raise RuntimeProbeError("runtime executable changed during probe")
    try:
        current = _snapshot(issuance.path)
    except RuntimeProbeError:
        raise
    except Exception as error:
        raise RuntimeProbeError("runtime executable identity is invalid") from error
    if current != issuance.after:
        raise RuntimeProbeError("runtime executable changed after probe")
    return diagnostic


def _acquire_runtime_launch_claim(value: object) -> _RuntimeLaunchClaim:
    """Hold the probed Windows image identity through suspended process setup."""

    diagnostic = validate_runtime_diagnostic(value)
    issuance = _RUNTIME_REGISTRY.get(id(diagnostic))
    if issuance is None or issuance.diagnostic is not diagnostic:  # pragma: no cover
        raise RuntimeProbeError("runtime diagnostic issuance is unavailable")
    if os.name != "nt" or not hasattr(ctypes, "WinDLL"):
        return _RuntimeLaunchClaim(issuance.path, issuance.after, None)

    opened = _windows_open_runtime_claim(issuance.path)
    handle = int(opened)
    native_handle = getattr(opened, "native_handle", None)
    if native_handle is None:
        raise RuntimeProbeError("runtime executable claim has no exact native guard")
    claim = _RuntimeLaunchClaim(issuance.path, issuance.after, handle, native_handle)
    try:
        current = _snapshot_from_handle(issuance.path, handle)
        if current != issuance.after:
            raise RuntimeProbeError("runtime executable changed before process creation")
    except BaseException:
        try:
            claim.close()
        except BaseException:
            _retain_runtime_claim(claim)
        else:
            _discard_runtime_claim(claim)
        raise
    return claim


probe_runtime = probe_febio
