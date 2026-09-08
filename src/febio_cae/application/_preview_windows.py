"""Passive Windows process/window identity queries; no input or process launch."""

from __future__ import annotations

import ctypes
import hashlib
import os
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from febio_cae.adapters.preview.studio import ExistingStudioSession
from febio_cae.domain import ToolIdentity
from febio_cae.storage._ownership import pinned_read


@dataclass(frozen=True)
class ProcessSnapshot:
    process_id: int
    start_marker: str
    executable: Path
    version: str
    executable_digest: str


class _FixedVersion(ctypes.Structure):
    _fields_ = [
        (name, wintypes.DWORD)
        for name in (
            "signature",
            "structure_version",
            "file_ms",
            "file_ls",
            "product_ms",
            "product_ls",
            "flags_mask",
            "flags",
            "os",
            "type",
            "subtype",
            "date_ms",
            "date_ls",
        )
    ]


def _kernel() -> Any:
    if os.name != "nt":
        raise OSError("existing Studio observation requires Windows process queries")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.GetProcessTimes.argtypes = (wintypes.HANDLE, *(ctypes.POINTER(wintypes.FILETIME),) * 4)
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    )
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    return kernel


def _file_version(path: Path) -> str:
    library = ctypes.WinDLL("version", use_last_error=True)
    library.GetFileVersionInfoSizeW.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD))
    library.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    library.GetFileVersionInfoW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
    )
    library.GetFileVersionInfoW.restype = wintypes.BOOL
    library.VerQueryValueW.argtypes = (
        wintypes.LPCVOID,
        wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.UINT),
    )
    library.VerQueryValueW.restype = wintypes.BOOL
    unused = wintypes.DWORD()
    size = library.GetFileVersionInfoSizeW(str(path), ctypes.byref(unused))
    if not size or size > 4 * 1024 * 1024:
        raise OSError("executable file-version resource is unavailable")
    content = ctypes.create_string_buffer(size)
    if not library.GetFileVersionInfoW(str(path), 0, size, content):
        raise ctypes.WinError(ctypes.get_last_error())
    pointer, length = ctypes.c_void_p(), wintypes.UINT()
    if not library.VerQueryValueW(content, "\\", ctypes.byref(pointer), ctypes.byref(length)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not pointer.value or length.value < ctypes.sizeof(_FixedVersion):
        raise OSError("executable version resource is malformed")
    fixed = ctypes.cast(pointer, ctypes.POINTER(_FixedVersion)).contents
    if fixed.signature != 0xFEEF04BD:
        raise OSError("executable version resource signature differs")
    parts = [
        fixed.file_ms >> 16,
        fixed.file_ms & 0xFFFF,
        fixed.file_ls >> 16,
        fixed.file_ls & 0xFFFF,
    ]
    if parts[-1] == 0:
        parts.pop()
    return ".".join(str(part) for part in parts)


def process_snapshot(process_id: int) -> ProcessSnapshot:
    if type(process_id) is not int or process_id <= 0:
        raise ValueError("an explicit positive process ID is required")
    kernel = _kernel()
    handle = kernel.OpenProcess(0x1000 | 0x100000, False, process_id)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        created, ended, system, user = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(ended),
            ctypes.byref(system),
            ctypes.byref(user),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        path_buffer = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(path_buffer))
        if not kernel.QueryFullProcessImageNameW(handle, 0, path_buffer, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        path = Path(path_buffer.value).absolute()
        with pinned_read(path) as stream:
            content_hash = hashlib.sha256()
            while block := stream.read(1024 * 1024):
                content_hash.update(block)
            digest = content_hash.hexdigest()
            version = _file_version(path)
        if kernel.WaitForSingleObject(handle, 0) != 0x102:
            raise OSError("observed process has ended or its lifetime is unavailable")
        marker = (created.dwHighDateTime << 32) | created.dwLowDateTime
        return ProcessSnapshot(process_id, f"windows-filetime:{marker}", path, version, digest)
    finally:
        kernel.CloseHandle(handle)


def _window_pid(window_id: int) -> int:
    if os.name != "nt" or type(window_id) is not int or window_id <= 0:
        raise OSError("an existing Windows window identity is required")
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.IsWindow.argtypes = (wintypes.HWND,)
    user.IsWindow.restype = wintypes.BOOL
    user.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user.GetWindowThreadProcessId.restype = wintypes.DWORD
    process = wintypes.DWORD()
    if not user.IsWindow(window_id) or not user.GetWindowThreadProcessId(
        window_id, ctypes.byref(process)
    ):
        raise OSError("the existing Studio window is unavailable")
    return int(process.value)


class WindowsStudioProbe:
    def __init__(self, executable: Path | str) -> None:
        self.executable = Path(executable).absolute()
        if self.executable.name.casefold() != "febiostudio.exe":
            raise ValueError("the existing-window probe requires the FEBioStudio executable")

    def identify(self, window_id: int) -> ExistingStudioSession:
        process_id = _window_pid(window_id)
        measured = process_snapshot(process_id)
        if measured.executable != self.executable:
            raise ValueError("window process image is not the specified Studio executable")
        if _window_pid(window_id) != measured.process_id:
            raise OSError("window process identity changed during observation")
        return ExistingStudioSession(
            ToolIdentity("FEBio Studio", measured.version, measured.executable_digest),
            measured.process_id,
            measured.start_marker,
            window_id,
        )

    def __call__(self, expected: ExistingStudioSession) -> ExistingStudioSession | None:
        try:
            return self.identify(expected.window_id)
        except (OSError, ValueError):
            return None
