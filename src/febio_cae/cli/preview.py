"""Finite single-process observation protocol over a redirected stdin pipe."""

from __future__ import annotations

import ctypes
import json
import math
import os
import sys
import time
from ctypes import wintypes
from typing import Any


def _stdin_fd() -> int:
    return sys.stdin.fileno()


def _available(fd: int) -> int:
    if os.name != "nt":
        raise OSError("preview stdin protocol currently requires Windows pipes")
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetFileType.argtypes = (wintypes.HANDLE,)
    kernel.GetFileType.restype = wintypes.DWORD
    kernel.PeekNamedPipe.argtypes = (
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
    )
    kernel.PeekNamedPipe.restype = wintypes.BOOL
    handle = msvcrt.get_osfhandle(fd)
    if kernel.GetFileType(handle) != 3:
        raise OSError("preview observation requires redirected stdin, not a terminal or file")
    count = wintypes.DWORD()
    if not kernel.PeekNamedPipe(handle, None, 0, None, ctypes.byref(count), None):
        if ctypes.get_last_error() in {109, 232}:
            raise ValueError("preview observation input closed before a complete record")
        raise ctypes.WinError(ctypes.get_last_error())
    return int(count.value)


def read_observation(fd: int, timeout: float) -> dict[str, Any]:
    from .case import _reject_duplicate_pairs

    if not math.isfinite(timeout) or timeout <= 0:
        raise TimeoutError("preview observation deadline expired")
    deadline = time.monotonic() + timeout
    content = bytearray()
    while time.monotonic() < deadline:
        available = _available(fd)
        if available:
            content.extend(os.read(fd, min(available, 65537 - len(content))))
            if len(content) > 65536:
                raise ValueError("preview observation exceeds the 64KiB protocol limit")
            if b"\n" in content:
                line, rest = bytes(content).split(b"\n", 1)
                if rest.strip():
                    raise ValueError("exactly one observation record is accepted")
                record = json.loads(line.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs)
                if not isinstance(record, dict):
                    raise ValueError("preview observation must be an object")
                return record
        time.sleep(min(0.02, max(0.0, deadline - time.monotonic())))
    raise TimeoutError("preview observation deadline expired")


def capture_observation(issue: dict[str, Any], remaining: float) -> dict[str, Any]:
    print(
        json.dumps(
            {"status": "PREVIEW_REQUESTED", "request": issue, "remaining_seconds": remaining},
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )
    return read_observation(_stdin_fd(), remaining)
