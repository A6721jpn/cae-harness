"""OS-backed attestations for one supervisor-owned solver launch."""

from __future__ import annotations

import contextlib
import ctypes
import hashlib
import os
import secrets
import signal
from pathlib import Path
from typing import Any


class ProcessAuthorityError(OSError):
    """Raised when the platform cannot attest the owned process."""


def _attempt_binding(attempt_root: Path) -> str:
    value = os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(attempt_root))))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class ProcessAuthority:
    @classmethod
    def create(cls, attempt_root: Path) -> ProcessAuthority:
        if os.name == "nt":
            return _WindowsProcessAuthority.create(attempt_root)
        if os.name == "posix":
            return _PosixProcessAuthority.create(attempt_root)
        raise ProcessAuthorityError("process attestation is unsupported on this platform")

    @classmethod
    def from_claim(cls, attempt_root: Path, claim: object) -> ProcessAuthority:
        if not isinstance(claim, dict):
            raise ProcessAuthorityError("process authority claim is invalid")
        kind = claim.get("kind")
        if os.name == "nt" and kind == "windows-job":
            return _WindowsProcessAuthority.from_claim(attempt_root, claim)
        if os.name == "posix" and kind == "posix-pipe":
            return _PosixProcessAuthority.from_claim(attempt_root, claim)
        raise ProcessAuthorityError("process authority kind is unsupported")

    def child_environment(self) -> dict[str, str]: ...  # type: ignore[empty-body]

    def child_pass_fds(self) -> tuple[int, ...]: ...  # type: ignore[empty-body]

    def child_handle(self) -> int | None: ...

    def bind(self, pid: int) -> None: ...

    def resume(self, pid: int) -> None: ...

    def verify(self, pid: int) -> None: ...

    def terminate(self, pid: int, *, force: bool = False) -> None: ...

    def close(self) -> None: ...

    @property
    def claim(self) -> dict[str, object]: ...  # type: ignore[empty-body]


class _PosixProcessAuthority(ProcessAuthority):
    def __init__(self, attempt_root: Path, *, inode: int, write_fd: int | None) -> None:
        if not Path("/proc").is_dir():
            raise ProcessAuthorityError("/proc is required for process attestation")
        self._binding = _attempt_binding(attempt_root)
        self._inode = inode
        self._write_fd = write_fd
        self._claim = {"kind": "posix-pipe", "attempt_binding": self._binding, "pipe_inode": inode}

    @classmethod
    def create(cls, attempt_root: Path) -> _PosixProcessAuthority:
        if not Path("/proc").is_dir():
            raise ProcessAuthorityError("/proc is required for process attestation")
        read_fd, write_fd = os.pipe()
        try:
            os.close(read_fd)
            os.set_inheritable(write_fd, True)
            inode = os.fstat(write_fd).st_ino
        except OSError as error:
            with contextlib.suppress(OSError):
                os.close(write_fd)
            raise ProcessAuthorityError(f"unable to create process attestation: {error}") from error
        return cls(attempt_root, inode=inode, write_fd=write_fd)

    @classmethod
    def from_claim(cls, attempt_root: Path, claim: object) -> _PosixProcessAuthority:
        if not isinstance(claim, dict) or claim.get("attempt_binding") != _attempt_binding(
            attempt_root
        ):
            raise ProcessAuthorityError("process authority attempt binding does not match")
        inode = claim.get("pipe_inode")
        if isinstance(inode, bool) or not isinstance(inode, int) or inode <= 0:
            raise ProcessAuthorityError("process authority pipe identity is invalid")
        return cls(attempt_root, inode=inode, write_fd=None)

    @property
    def claim(self) -> dict[str, object]:
        return dict(self._claim)

    def child_environment(self) -> dict[str, str]:
        return (
            {}
            if self._write_fd is None
            else {"FEBIO_CAE_HARNESS_AUTHORITY_FD": str(self._write_fd)}
        )

    def child_pass_fds(self) -> tuple[int, ...]:
        return () if self._write_fd is None else (self._write_fd,)

    def bind(self, pid: int) -> None:
        self.verify(pid)
        self.close()

    def verify(self, pid: int) -> None:
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise ProcessAuthorityError("process authority PID is invalid")
        getpgid = getattr(os, "getpgid", None)
        getsid = getattr(os, "getsid", None)
        if not callable(getpgid) or not callable(getsid):
            raise ProcessAuthorityError("process session attestation is unsupported")
        try:
            if getpgid(pid) != pid or getsid(pid) != pid:
                raise ProcessAuthorityError("process is not the attested session leader")
            expected = f"pipe:[{self._inode}]"
            with os.scandir(f"/proc/{pid}/fd") as entries:
                found = any(
                    os.readlink(entry.path) == expected
                    for entry in entries
                    if os.path.exists(entry.path)
                )
            if not found:
                raise ProcessAuthorityError("process does not hold the attestation capability")
        except OSError as error:
            raise ProcessAuthorityError("unable to verify process attestation") from error

    def terminate(self, pid: int, *, force: bool = False) -> None:
        self.verify(pid)
        getpgid = getattr(os, "getpgid", None)
        killpg = getattr(os, "killpg", None)
        if not callable(getpgid) or not callable(killpg):
            raise ProcessAuthorityError("process group termination is unsupported")
        try:
            killpg(getpgid(pid), getattr(signal, "SIGKILL" if force else "SIGTERM"))
        except OSError as error:
            raise ProcessAuthorityError("unable to terminate attested process tree") from error

    def close(self) -> None:
        if self._write_fd is not None:
            fd, self._write_fd = self._write_fd, None
            with contextlib.suppress(OSError):
                os.close(fd)


class _WindowsProcessAuthority(ProcessAuthority):
    _JOB_ACCESS = 0x000C
    _PROCESS_ACCESS = 0x1101
    _PROCESS_QUERY = 0x1000
    _THREAD_SUSPEND_RESUME = 0x0002
    _THREAD_QUERY_LIMITED_INFORMATION = 0x0800
    _TH32CS_SNAPTHREAD = 0x00000004
    _STILL_SUSPENDED = 0xFFFFFFFF

    class _SecurityAttributes(ctypes.Structure):
        _fields_ = [
            ("nLength", ctypes.c_uint32),
            ("lpSecurityDescriptor", ctypes.c_void_p),
            ("bInheritHandle", ctypes.c_int),
        ]

    class _ThreadEntry32(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_uint32),
            ("cntUsage", ctypes.c_uint32),
            ("th32ThreadID", ctypes.c_uint32),
            ("th32OwnerProcessID", ctypes.c_uint32),
            ("tpBasePri", ctypes.c_int32),
            ("tpDeltaPri", ctypes.c_int32),
            ("dwFlags", ctypes.c_uint32),
        ]

    def __init__(self, attempt_root: Path, *, name: str, handle: Any) -> None:
        self._binding = _attempt_binding(attempt_root)
        self._handle = handle
        self._bound_pid: int | None = None
        self._claim = {"kind": "windows-job", "attempt_binding": self._binding, "name": name}

    @staticmethod
    def _kernel32() -> Any:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateJobObjectW.argtypes = [
            ctypes.POINTER(_WindowsProcessAuthority._SecurityAttributes),
            ctypes.c_wchar_p,
        ]
        k.CreateJobObjectW.restype = ctypes.c_void_p
        k.OpenJobObjectW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
        k.OpenJobObjectW.restype = ctypes.c_void_p
        k.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        k.IsProcessInJob.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
        k.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        k.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        k.OpenProcess.restype = ctypes.c_void_p
        k.CreateToolhelp32Snapshot.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
        k.Thread32First.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_WindowsProcessAuthority._ThreadEntry32),
        ]
        k.Thread32First.restype = ctypes.c_int
        k.Thread32Next.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_WindowsProcessAuthority._ThreadEntry32),
        ]
        k.Thread32Next.restype = ctypes.c_int
        k.OpenThread.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        k.OpenThread.restype = ctypes.c_void_p
        k.GetProcessIdOfThread.argtypes = [ctypes.c_void_p]
        k.GetProcessIdOfThread.restype = ctypes.c_uint32
        k.ResumeThread.argtypes = [ctypes.c_void_p]
        k.ResumeThread.restype = ctypes.c_uint32
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        return k

    @classmethod
    def create(cls, attempt_root: Path) -> _WindowsProcessAuthority:
        binding = _attempt_binding(attempt_root)
        name = f"Local\\febio-cae-{binding[:32]}-{secrets.token_hex(16)}"
        k = cls._kernel32()
        security = cls._SecurityAttributes(ctypes.sizeof(cls._SecurityAttributes), None, 1)
        handle = k.CreateJobObjectW(ctypes.byref(security), name)
        if not handle:
            raise ProcessAuthorityError("unable to create process job attestation")
        return cls(attempt_root, name=name, handle=handle)

    @classmethod
    def from_claim(cls, attempt_root: Path, claim: object) -> _WindowsProcessAuthority:
        binding = _attempt_binding(attempt_root)
        name = claim.get("name") if isinstance(claim, dict) else None
        prefix = f"Local\\febio-cae-{binding[:32]}-"
        if not isinstance(claim, dict) or claim.get("attempt_binding") != binding:
            raise ProcessAuthorityError("process authority attempt binding does not match")
        if not isinstance(name, str) or not name.startswith(prefix):
            raise ProcessAuthorityError("process authority job name is invalid")
        handle = cls._kernel32().OpenJobObjectW(cls._JOB_ACCESS, False, name)
        if not handle:
            raise ProcessAuthorityError("live process job attestation is unavailable")
        return cls(attempt_root, name=name, handle=handle)

    @property
    def claim(self) -> dict[str, object]:
        return dict(self._claim)

    def child_environment(self) -> dict[str, str]:
        return {}

    def child_handle(self) -> int | None:
        return None if self._handle is None else int(self._handle)

    def bind(self, pid: int) -> None:
        k = self._kernel32()
        process = k.OpenProcess(self._PROCESS_ACCESS, False, pid)
        if not process:
            raise ProcessAuthorityError("unable to open launched process for attestation")
        try:
            if not k.AssignProcessToJobObject(self._handle, process):
                raise ProcessAuthorityError("unable to bind launched process to attestation")
            self._bound_pid = pid
        finally:
            k.CloseHandle(process)
        try:
            self.verify(pid)
        except ProcessAuthorityError:
            with contextlib.suppress(ProcessAuthorityError):
                self._terminate_job()
            raise

    def resume(self, pid: int) -> None:
        """Resume only threads belonging to the already-attested process."""

        try:
            self.verify(pid)
        except ProcessAuthorityError:
            self._fail_closed(pid, "unable to verify attested process before resume")
        k = self._kernel32()
        snapshot = k.CreateToolhelp32Snapshot(self._TH32CS_SNAPTHREAD, 0)
        invalid_handle = ctypes.c_void_p(-1).value
        if not snapshot or snapshot == invalid_handle:
            self._fail_closed(pid, "unable to enumerate attested process threads")
        thread_ids: list[int] = []
        try:
            entry = self._ThreadEntry32()
            entry.dwSize = ctypes.sizeof(entry)
            if k.Thread32First(snapshot, ctypes.byref(entry)):
                while True:
                    if entry.th32OwnerProcessID == pid:
                        thread_ids.append(entry.th32ThreadID)
                    entry.dwSize = ctypes.sizeof(entry)
                    if not k.Thread32Next(snapshot, ctypes.byref(entry)):
                        break
        finally:
            k.CloseHandle(snapshot)
        if not thread_ids:
            self._fail_closed(pid, "attested process has no resumable primary thread")

        try:
            for thread_id in thread_ids:
                thread = k.OpenThread(
                    self._THREAD_SUSPEND_RESUME | self._THREAD_QUERY_LIMITED_INFORMATION,
                    False,
                    thread_id,
                )
                if not thread:
                    raise ProcessAuthorityError("unable to open attested process primary thread")
                try:
                    if k.GetProcessIdOfThread(thread) != pid:
                        raise ProcessAuthorityError(
                            "attested process primary thread identity changed"
                        )
                    previous_count = k.ResumeThread(thread)
                    if previous_count == self._STILL_SUSPENDED or previous_count != 1:
                        raise ProcessAuthorityError(
                            "attested process primary thread was not suspended exactly once"
                        )
                finally:
                    k.CloseHandle(thread)
        except ProcessAuthorityError:
            self._fail_closed(pid, "unable to resume attested process primary thread")

    def _fail_closed(self, pid: int, message: str) -> None:
        try:
            self._terminate_job()
        except ProcessAuthorityError as error:
            raise ProcessAuthorityError(f"{message}; unable to terminate attested job") from error
        raise ProcessAuthorityError(message)

    def _terminate_job(self) -> None:
        if not self._handle or not self._kernel32().TerminateJobObject(self._handle, 1):
            raise ProcessAuthorityError("unable to terminate attested process tree")

    def verify(self, pid: int) -> None:
        k = self._kernel32()
        process = k.OpenProcess(self._PROCESS_QUERY, False, pid)
        if not process:
            raise ProcessAuthorityError("attested process is not live")
        try:
            in_job = ctypes.c_int()
            if not k.IsProcessInJob(process, self._handle, ctypes.byref(in_job)):
                raise ProcessAuthorityError("unable to verify process job attestation")
            if not in_job.value:
                raise ProcessAuthorityError("process is not in the attested job")
        finally:
            k.CloseHandle(process)

    def terminate(self, pid: int, *, force: bool = False) -> None:
        self.verify(pid)
        self._terminate_job()

    def close(self) -> None:
        if self._handle:
            handle, self._handle = self._handle, None
            self._bound_pid = None
            self._kernel32().CloseHandle(handle)


__all__ = ["ProcessAuthority", "ProcessAuthorityError"]
