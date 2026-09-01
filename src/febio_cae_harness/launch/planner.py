"""Headless command planning for the fixed development deployment."""

from __future__ import annotations

import atexit
import ctypes
import json
import os
import subprocess
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, nullcontext
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any

from .deployment import (
    BuildIdentity,
    DeploymentError,
    DeploymentLayout,
    deployment_lock,
    verify_payload_identity,
)


class LaunchError(DeploymentError):
    """Raised when a planned launcher cannot be executed."""


_GENERIC_READ = 0x80000000
_FILE_READ_ATTRIBUTES = 0x00000080
_SYNCHRONIZE = 0x00100000
_FILE_SHARE_READ = 0x00000001
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_NORMAL = 0x00000080
_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_HANDLE_FLAG_PROTECT_FROM_CLOSE = 0x00000002
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_ERROR_INVALID_HANDLE = 6


class _WindowsFileTime(ctypes.Structure):
    _fields_ = (("low", wintypes.DWORD), ("high", wintypes.DWORD))


class _WindowsFileInformation(ctypes.Structure):
    _fields_ = (
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
    )


@dataclass(slots=True)
class _LauncherClaim:
    path: Path
    value: int | None
    identity: tuple[int, int] | None
    final_path: str | None
    protected: bool = False
    last_native_value: int | None = None
    indeterminate: bool = False


_PENDING_LAUNCHER_CLAIMS: list[_LauncherClaim] = []
_INDETERMINATE_LAUNCHER_CLAIMS: list[_LauncherClaim] = []
_LAUNCHER_CLAIM_LOCK = RLock()


def _windows_kernel32() -> Any:
    if os.name != "nt":
        raise LaunchError("exact launcher authority is available only on Windows")
    try:
        return ctypes.WinDLL("kernel32", use_last_error=True)
    except (AttributeError, OSError) as error:
        raise LaunchError("Windows launcher authority is unavailable") from error


def _normalise_final_path(value: str) -> str:
    normalised = os.path.normpath(value)
    if normalised.startswith("\\\\?\\UNC\\"):
        normalised = "\\\\" + normalised[8:]
    elif normalised.startswith("\\\\?\\"):
        normalised = normalised[4:]
    return os.path.normcase(normalised)


def _launcher_handle_info(value: int) -> tuple[int, int, int, int]:
    kernel32 = _windows_kernel32()
    get_info = kernel32.GetFileInformationByHandle
    get_info.argtypes = (wintypes.HANDLE, ctypes.POINTER(_WindowsFileInformation))
    get_info.restype = wintypes.BOOL
    information = _WindowsFileInformation()
    ctypes.set_last_error(0)
    if not get_info(wintypes.HANDLE(value), ctypes.byref(information)):
        error = ctypes.get_last_error()
        raise _LauncherHandleInspectionError(error)
    attributes = int(information.attributes)
    file_index = (int(information.file_index_high) << 32) | int(information.file_index_low)
    return (
        int(information.volume_serial_number),
        file_index,
        attributes,
        int(information.number_of_links),
    )


class _LauncherHandleInspectionError(LaunchError):
    def __init__(self, error_code: int) -> None:
        super().__init__("cannot inspect exact launcher handle")
        self.error_code = error_code


def _windows_launcher_protected(value: int) -> bool:
    kernel32 = _windows_kernel32()
    get_handle_information = kernel32.GetHandleInformation
    get_handle_information.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    get_handle_information.restype = wintypes.BOOL
    flags = wintypes.DWORD()
    ctypes.set_last_error(0)
    if not get_handle_information(wintypes.HANDLE(value), ctypes.byref(flags)):
        raise _LauncherHandleInspectionError(ctypes.get_last_error())
    return bool(int(flags.value) & _HANDLE_FLAG_PROTECT_FROM_CLOSE)


def _windows_set_launcher_protection(value: int, protected: bool) -> None:
    kernel32 = _windows_kernel32()
    set_handle_information = kernel32.SetHandleInformation
    set_handle_information.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
    )
    set_handle_information.restype = wintypes.BOOL
    flags = _HANDLE_FLAG_PROTECT_FROM_CLOSE if protected else 0
    ctypes.set_last_error(0)
    if not set_handle_information(
        wintypes.HANDLE(value),
        wintypes.DWORD(_HANDLE_FLAG_PROTECT_FROM_CLOSE),
        wintypes.DWORD(flags),
    ):
        raise LaunchError("cannot change exact launcher handle protection")


def _launcher_final_path(value: int) -> str:
    kernel32 = _windows_kernel32()
    get_final_path = kernel32.GetFinalPathNameByHandleW
    get_final_path.argtypes = (wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD)
    get_final_path.restype = wintypes.DWORD
    size = 1024
    while size <= 32768:
        buffer = ctypes.create_unicode_buffer(size)
        ctypes.set_last_error(0)
        length = int(
            get_final_path(
                wintypes.HANDLE(value),
                buffer,
                wintypes.DWORD(size),
                wintypes.DWORD(0),
            )
        )
        if length == 0:
            raise LaunchError("cannot inspect exact launcher path")
        if length < size:
            return _normalise_final_path(buffer.value)
        size = max(size * 2, length + 1)
    raise LaunchError("exact launcher path is too long")


def _open_launcher_claim(path: Path) -> _LauncherClaim:
    _drain_launcher_claims()
    kernel32 = _windows_kernel32()
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    expected_path = _normalise_final_path(os.path.abspath(os.fspath(path)))
    ctypes.set_last_error(0)
    raw = create_file(
        os.fspath(path),
        _GENERIC_READ | _FILE_READ_ATTRIBUTES | _SYNCHRONIZE,
        _FILE_SHARE_READ,
        None,
        _OPEN_EXISTING,
        _FILE_ATTRIBUTE_NORMAL,
        None,
    )
    value = ctypes.cast(raw, ctypes.c_void_p).value
    if value is None or value == _INVALID_HANDLE_VALUE:
        raise LaunchError(f"cannot hold exact launcher: {path}")
    handle = int(value)
    claim = _LauncherClaim(path, handle, None, None)
    try:
        _windows_set_launcher_protection(handle, True)
        claim.protected = True
        volume, file_index, _attributes, _links = _launcher_handle_info(handle)
        claim.identity = (volume, file_index)
        final_path = _launcher_final_path(handle)
        if final_path != expected_path:
            raise LaunchError("launcher handle resolved to a different path")
        claim.final_path = final_path
        _validate_launcher_claim(claim)
        return claim
    except BaseException:
        _close_launcher_claim(claim, required=False)
        raise


def _validate_launcher_claim(claim: _LauncherClaim) -> None:
    if claim.value is None or claim.identity is None or claim.final_path is None:
        raise LaunchError("exact launcher claim is closed")
    if not claim.protected or not _windows_launcher_protected(claim.value):
        raise LaunchError("exact launcher handle is not protected")
    volume, file_index, attributes, links = _launcher_handle_info(claim.value)
    if (volume, file_index) != claim.identity:
        raise LaunchError("exact launcher identity changed")
    if _launcher_final_path(claim.value) != claim.final_path:
        raise LaunchError("exact launcher path changed")
    if attributes & (_FILE_ATTRIBUTE_DIRECTORY | _FILE_ATTRIBUTE_REPARSE_POINT):
        raise LaunchError("launcher handle has an unsafe file type")
    if links != 1:
        raise LaunchError("launcher must not be hard-linked")


def _close_launcher_claim(claim: _LauncherClaim, *, required: bool) -> None:
    with _LAUNCHER_CLAIM_LOCK:
        _close_launcher_claim_locked(claim, required=required)


def _windows_close_launcher(value: int) -> bool:
    kernel32 = _windows_kernel32()
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    ctypes.set_last_error(0)
    return bool(close_handle(wintypes.HANDLE(value)))


def _forget_pending_claim(claim: _LauncherClaim) -> None:
    _PENDING_LAUNCHER_CLAIMS[:] = [
        existing for existing in _PENDING_LAUNCHER_CLAIMS if existing is not claim
    ]


def _retain_pending_claim(claim: _LauncherClaim) -> None:
    if not any(existing is claim for existing in _PENDING_LAUNCHER_CLAIMS):
        _PENDING_LAUNCHER_CLAIMS.append(claim)


def _mark_claim_indeterminate(claim: _LauncherClaim, value: int) -> None:
    _forget_pending_claim(claim)
    claim.last_native_value = value
    claim.value = None
    claim.protected = False
    claim.indeterminate = True
    if not any(existing is claim for existing in _INDETERMINATE_LAUNCHER_CLAIMS):
        _INDETERMINATE_LAUNCHER_CLAIMS.append(claim)


def _retain_after_uncertain_close(
    claim: _LauncherClaim,
    value: int,
    failure: BaseException,
    *,
    required: bool,
) -> None:
    _mark_claim_indeterminate(claim, value)
    if required:
        raise failure


def _close_launcher_claim_locked(claim: _LauncherClaim, *, required: bool) -> None:
    value = claim.value
    if value is None:
        _forget_pending_claim(claim)
        return
    try:
        volume, file_index, _attributes, _links = _launcher_handle_info(value)
    except _LauncherHandleInspectionError as error:
        if error.error_code != _ERROR_INVALID_HANDLE:
            _retain_pending_claim(claim)
            if required:
                raise
            return
        claim.value = None
        claim.protected = False
        _forget_pending_claim(claim)
        if required:
            raise
        return
    except BaseException:
        _retain_pending_claim(claim)
        if required:
            raise
        return
    if claim.identity is None:
        claim.identity = (volume, file_index)
    elif (volume, file_index) != claim.identity:
        claim.value = None
        claim.protected = False
        _forget_pending_claim(claim)
        if required:
            raise LaunchError("exact launcher identity changed before close")
        return
    if claim.protected:
        try:
            if not _windows_launcher_protected(value):
                raise LaunchError("exact launcher handle lost close protection")
        except BaseException:
            _retain_pending_claim(claim)
            if required:
                raise
            return
        try:
            _windows_set_launcher_protection(value, False)
        except BaseException:
            _mark_claim_indeterminate(claim, value)
            if required:
                raise
            return
        claim.protected = False
    try:
        closed = _windows_close_launcher(value)
    except BaseException as close_error:
        _retain_after_uncertain_close(
            claim,
            value,
            close_error,
            required=required,
        )
        return
    if not closed:
        _retain_after_uncertain_close(
            claim,
            value,
            LaunchError("cannot close exact launcher handle"),
            required=required,
        )
        return
    claim.value = None
    _forget_pending_claim(claim)


def _drain_launcher_claims() -> None:
    with _LAUNCHER_CLAIM_LOCK:
        for claim in tuple(_PENDING_LAUNCHER_CLAIMS):
            try:
                _close_launcher_claim_locked(claim, required=False)
            except BaseException:
                continue


atexit.register(_drain_launcher_claims)


@contextmanager
def _hold_exact_launcher(path: Path) -> Iterator[_LauncherClaim]:
    claim = _open_launcher_claim(path)
    try:
        yield claim
    except BaseException as primary_error:
        try:
            _close_launcher_claim(claim, required=True)
        except BaseException as cleanup_error:
            primary_error.add_note(f"launcher cleanup failed: {cleanup_error}")
        raise
    else:
        _close_launcher_claim(claim, required=True)


@dataclass(frozen=True, slots=True)
class LaunchPlan:
    """A shell-free command that always runs the fixed latest launcher."""

    layout: DeploymentLayout
    argv: tuple[str, ...]
    cwd: Path
    build_identity: BuildIdentity | None = None

    @property
    def command(self) -> tuple[str, ...]:
        return self.argv

    @property
    def launcher(self) -> Path:
        return self.layout.launcher

    def to_dict(self) -> dict[str, object]:
        identity: dict[str, str | None] | None = None
        if self.build_identity is not None:
            identity = self.build_identity.to_dict()
        return {
            "argv": list(self.argv),
            "build_identity": identity,
            "cwd": os_fspath(self.cwd),
            "launcher": os_fspath(self.launcher),
            "latest_development": os_fspath(self.layout.latest),
        }


def os_fspath(path: Path) -> str:
    """Return a string path while keeping ``LaunchPlan`` JSON-serializable."""

    return str(path)


def read_build_identity(layout: DeploymentLayout) -> BuildIdentity:
    """Read and validate the identity persisted in the current deployment."""

    with deployment_lock(layout):
        return _read_build_identity_unlocked(layout)


def _read_build_identity_unlocked(layout: DeploymentLayout) -> BuildIdentity:
    identity_path = layout.identity_path
    try:
        payload = json.loads(identity_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LaunchError(f"cannot read build identity: {identity_path}") from error
    if not isinstance(payload, dict):
        raise LaunchError(f"build identity is not an object: {identity_path}")
    try:
        identity = BuildIdentity.from_mapping(payload)
        return verify_payload_identity(layout.latest, identity)
    except (TypeError, ValueError) as error:
        raise LaunchError(f"invalid build identity: {identity_path}") from error
    except DeploymentError as error:
        raise LaunchError(str(error)) from error


def _plan_cli_launch_unlocked(
    resolved_layout: DeploymentLayout,
    normalised_arguments: tuple[str, ...],
    *,
    require_published: bool,
) -> LaunchPlan:
    identity: BuildIdentity | None = None
    if require_published:
        if not resolved_layout.latest.is_dir() or not resolved_layout.launcher.is_file():
            raise LaunchError("latest-development launcher is not installed")
        identity = _read_build_identity_unlocked(resolved_layout)
    elif resolved_layout.identity_path.is_file():
        identity = _read_build_identity_unlocked(resolved_layout)

    return LaunchPlan(
        layout=resolved_layout,
        argv=(str(resolved_layout.launcher), *normalised_arguments),
        cwd=resolved_layout.latest,
        build_identity=identity,
    )


def plan_cli_launch(
    layout: DeploymentLayout | str | Path,
    arguments: Sequence[str] = (),
    *,
    require_published: bool = False,
) -> LaunchPlan:
    """Create a shell-free launch plan for ``latest-development``.

    Versioned directories and update/network checks are intentionally absent from
    this function.  ``require_published`` is useful for a real launch request;
    callers that only need to display a plan can leave it false.
    """

    resolved_layout = (
        layout
        if isinstance(layout, DeploymentLayout)
        else DeploymentLayout.from_local_app_data(layout)
    )
    normalised_arguments = tuple(arguments)
    if any(not isinstance(argument, str) for argument in normalised_arguments):
        raise TypeError("launch arguments must be strings")
    if any("\x00" in argument for argument in normalised_arguments):
        raise ValueError("launch arguments cannot contain NUL")

    needs_lock = require_published or resolved_layout.identity_path.is_file()
    with deployment_lock(resolved_layout) if needs_lock else nullcontext():
        return _plan_cli_launch_unlocked(
            resolved_layout,
            normalised_arguments,
            require_published=require_published,
        )


def launch_cli(
    arguments: Sequence[str] = (),
    *,
    layout: DeploymentLayout | None = None,
    local_app_data: str | Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Execute the fixed launcher without a shell or version selection."""

    if layout is not None and local_app_data is not None:
        raise TypeError("pass either layout or local_app_data, not both")
    resolved_layout = layout
    if resolved_layout is None:
        resolved_layout = (
            DeploymentLayout.from_environment()
            if local_app_data is None
            else DeploymentLayout.from_local_app_data(local_app_data)
        )
    with (
        deployment_lock(resolved_layout),
        _hold_exact_launcher(resolved_layout.launcher) as launcher_claim,
    ):
        plan = _plan_cli_launch_unlocked(
            resolved_layout,
            tuple(arguments),
            require_published=True,
        )
        _validate_launcher_claim(launcher_claim)
        try:
            return subprocess.run(
                plan.argv,
                cwd=os_fspath(plan.cwd),
                check=False,
                shell=False,
                text=True,
                capture_output=False,
            )
        except OSError as error:
            raise LaunchError(f"cannot execute launcher: {plan.launcher}") from error


__all__ = [
    "LaunchError",
    "LaunchPlan",
    "launch_cli",
    "plan_cli_launch",
    "read_build_identity",
]
