"""Windows filesystem identity pins and process-owned publication exclusion.

Directory handles deny rename/delete for the duration of I/O. Byte locks are
released by the kernel on process exit, so recovery never guesses death from age.
"""

from __future__ import annotations

import ctypes
import json
import os
import stat
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import BinaryIO


def identity(path: Path) -> tuple[int, int]:
    info = path.stat(follow_symlinks=False)
    return info.st_dev, info.st_ino


def _open(path: Path, *, directory: bool = False, writable: bool = False) -> int:
    if os.name != "nt":
        raise OSError("registered storage requires Windows handle identity protection")
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create.restype = wintypes.HANDLE
    access = 0x80000000 | (0x40000000 if writable else 0)
    share = 3 if directory or writable else 1  # never FILE_SHARE_DELETE
    flags = 0x00200000 | (0x02000000 if directory else 0)  # OPEN_REPARSE_POINT
    handle = create(str(path), access, share, None, 4 if writable else 3, flags, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise OSError(f"reparse point is outside registered storage: {path}")
        return msvcrt.open_osfhandle(handle, os.O_BINARY | (os.O_RDWR if writable else os.O_RDONLY))
    except BaseException:
        close = kernel.CloseHandle
        close.argtypes = [wintypes.HANDLE]
        close(handle)
        raise


@contextmanager
def pin_directories(path: Path, *, create: bool = False) -> Iterator[None]:
    """Pin each component from the volume down, before accessing the next one."""
    path = path.absolute()
    with ExitStack() as stack:
        for component in (*reversed(path.parents), path):
            if create and not component.exists():
                component.mkdir()
            descriptor = _open(component, directory=True)
            stack.callback(os.close, descriptor)
        yield


@contextmanager
def pinned_read(path: Path) -> Iterator[BinaryIO]:
    with pin_directories(path.parent), os.fdopen(_open(path), "rb") as stream:
        if os.fstat(stream.fileno()).st_nlink != 1:
            raise OSError("registered file has multiple hard links")
        yield stream


class _LocalOwner:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.depth = 0


_owners: dict[str, _LocalOwner] = {}
_owners_lock = threading.Lock()


@contextmanager
def lease(root: Path, *, blocking: bool = True, recovery: bool = False) -> Iterator[bool]:
    """One reentrant thread and one kernel-owned lock per registered root."""
    import msvcrt

    with _owners_lock:
        local = _owners.setdefault(str(root).casefold(), _LocalOwner())
    acquired = local.lock.acquire(blocking=blocking)
    if not acquired:
        yield False
        return
    try:
        if recovery and local.depth:
            yield False
            return
        if local.depth:
            local.depth += 1
            try:
                yield True
            finally:
                local.depth -= 1
            return
        with pin_directories(root):
            descriptor = _open(root / ".publication.lock", writable=True)
            try:
                if os.fstat(descriptor).st_nlink != 1:
                    raise OSError("publication lock has multiple hard links")
                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"\0")
                deadline = time.monotonic() + 30
                while True:
                    try:
                        os.lseek(descriptor, 0, os.SEEK_SET)
                        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if not blocking:
                            yield False
                            return
                        if time.monotonic() >= deadline:
                            raise TimeoutError(
                                "registered publication owner is still busy"
                            ) from None
                        time.sleep(0.02)
                token = uuid.uuid4().hex
                local.depth = 1
                try:
                    _record(descriptor, token, "acquired")
                    yield True
                finally:
                    local.depth = 0
                    _record(descriptor, token, "released")
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            finally:
                os.close(descriptor)
    finally:
        local.lock.release()


def _record(descriptor: int, token: str, event: str) -> None:
    os.lseek(descriptor, 0, os.SEEK_END)
    os.write(
        descriptor,
        (
            json.dumps(
                {
                    "pid": os.getpid(),
                    "thread": threading.get_ident(),
                    "token": token,
                    "event": event,
                    "time_ns": time.time_ns(),
                }
            )
            + "\n"
        ).encode(),
    )
    os.fsync(descriptor)
