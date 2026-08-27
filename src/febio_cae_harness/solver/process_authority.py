"""OS-backed attestations for one supervisor-owned solver launch."""

from __future__ import annotations

import contextlib
import ctypes
import hashlib
import os
import re
import signal
import time
from pathlib import Path
from typing import Any


class ProcessAuthorityError(OSError):
    """Raised when the platform cannot attest the owned process."""


_CONTEXT_ENVIRONMENT_KEY = "FEBIO_CAE_HARNESS_AUTHORITY_CONTEXT"


def _attempt_binding(attempt_root: Path) -> str:
    value = os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(attempt_root))))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validate_context_digest(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ProcessAuthorityError("process authority context binding is invalid")
    return value


def _validate_pid(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ProcessAuthorityError("process authority PID is invalid")
    return value


def _validate_creation_identity(value: object) -> str:
    identity = value.split(":", 1)[1] if isinstance(value, str) else ""
    if (
        not isinstance(value, str)
        or re.fullmatch(r"(?:windows|posix):[0-9]+", value) is None
        or not identity.lstrip("0")
    ):
        raise ProcessAuthorityError("process authority creation identity is invalid")
    return value


def _posix_creation_identity(pid: int) -> str:
    pid = _validate_pid(pid)
    proc_root = Path("/proc") / str(pid)
    try:
        stat_line = (proc_root / "stat").read_text(encoding="utf-8")
        stat_fields = stat_line.rpartition(")")[2].split()
    except OSError as error:
        raise ProcessAuthorityError("unable to query process creation identity") from error
    if len(stat_fields) < 20 or not stat_fields[19].isdigit():
        raise ProcessAuthorityError("unable to query process creation identity")
    return f"posix:{stat_fields[19]}"


def _windows_job_name(binding: str, context_digest: str) -> str:
    """Return the sole job claim identifier for one exact launch context."""

    return f"Local\\febio-cae-{binding}-{context_digest}"


class ProcessAuthority:
    @classmethod
    def create(
        cls,
        attempt_root: Path,
        context_digest: str,
    ) -> ProcessAuthority:
        context_digest = _validate_context_digest(context_digest)
        if os.name == "nt":
            return _WindowsProcessAuthority.create(attempt_root, context_digest)
        if os.name == "posix":
            return _PosixProcessAuthority.create(attempt_root, context_digest)
        raise ProcessAuthorityError("process attestation is unsupported on this platform")

    @classmethod
    def from_claim(cls, attempt_root: Path, claim: object, context_digest: str) -> ProcessAuthority:
        context_digest = _validate_context_digest(context_digest)
        if not isinstance(claim, dict):
            raise ProcessAuthorityError("process authority claim is invalid")
        kind = claim.get("kind")
        if os.name == "nt" and kind == "windows-job":
            return _WindowsProcessAuthority.from_claim(attempt_root, claim, context_digest)
        if os.name == "posix" and kind == "posix-pipe":
            return _PosixProcessAuthority.from_claim(attempt_root, claim, context_digest)
        raise ProcessAuthorityError("process authority kind is unsupported")

    def child_environment(self) -> dict[str, str]: ...  # type: ignore[empty-body]

    def child_pass_fds(self) -> tuple[int, ...]: ...  # type: ignore[empty-body]

    def child_handle(self) -> int | None: ...

    def bind(self, pid: int, expected_creation_identity: str | None = None) -> None: ...

    def resume(self, pid: int) -> None: ...

    def verify(self, pid: int) -> None: ...

    def terminate(self, pid: int, *, force: bool = False) -> None: ...

    def drain(self) -> None: ...

    def close(self) -> None: ...

    @property
    def claim(self) -> dict[str, object]: ...  # type: ignore[empty-body]


class _PosixProcessAuthority(ProcessAuthority):
    def __init__(
        self,
        attempt_root: Path,
        *,
        inode: int,
        write_fd: int | None,
        context_digest: str,
        bound_pid: int | None = None,
        bound_creation_identity: str | None = None,
    ) -> None:
        if not Path("/proc").is_dir():
            raise ProcessAuthorityError("/proc is required for process attestation")
        self._binding = _attempt_binding(attempt_root)
        self._context_digest = _validate_context_digest(context_digest)
        self._inode = inode
        self._write_fd = write_fd
        self._bound_pid = bound_pid
        self._bound_creation_identity = bound_creation_identity
        self._claim_verified = False
        self._claim = {
            "kind": "posix-pipe",
            "attempt_binding": self._binding,
            "pipe_inode": inode,
            "context_digest": self._context_digest,
        }

    @classmethod
    def create(
        cls,
        attempt_root: Path,
        context_digest: str,
    ) -> _PosixProcessAuthority:
        context_digest = _validate_context_digest(context_digest)
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
        return cls(
            attempt_root,
            inode=inode,
            write_fd=write_fd,
            context_digest=context_digest,
        )

    @classmethod
    def from_claim(
        cls, attempt_root: Path, claim: object, context_digest: str
    ) -> _PosixProcessAuthority:
        context_digest = _validate_context_digest(context_digest)
        if not isinstance(claim, dict) or claim.get("attempt_binding") != _attempt_binding(
            attempt_root
        ):
            raise ProcessAuthorityError("process authority attempt binding does not match")
        if claim.get("context_digest") != context_digest:
            raise ProcessAuthorityError("process authority context binding does not match")
        inode = claim.get("pipe_inode")
        if isinstance(inode, bool) or not isinstance(inode, int) or inode <= 0:
            raise ProcessAuthorityError("process authority pipe identity is invalid")
        bound_pid = _validate_pid(claim.get("root_pid"))
        bound_creation_identity = _validate_creation_identity(claim.get("root_creation_identity"))
        return cls(
            attempt_root,
            inode=inode,
            write_fd=None,
            context_digest=context_digest,
            bound_pid=bound_pid,
            bound_creation_identity=bound_creation_identity,
        )

    @property
    def claim(self) -> dict[str, object]:
        if self._bound_pid is None or self._bound_creation_identity is None:
            raise ProcessAuthorityError("process authority root process is not bound")
        return {
            **self._claim,
            "root_pid": self._bound_pid,
            "root_creation_identity": self._bound_creation_identity,
        }

    def child_environment(self) -> dict[str, str]:
        if self._write_fd is None:
            return {}
        return {
            "FEBIO_CAE_HARNESS_AUTHORITY_FD": str(self._write_fd),
            _CONTEXT_ENVIRONMENT_KEY: self._context_digest,
        }

    def child_pass_fds(self) -> tuple[int, ...]:
        return () if self._write_fd is None else (self._write_fd,)

    def bind(self, pid: int, expected_creation_identity: str | None = None) -> None:
        pid = _validate_pid(pid)
        live_identity = _posix_creation_identity(pid)
        if (
            expected_creation_identity is not None
            and _validate_creation_identity(expected_creation_identity) != live_identity
        ):
            raise ProcessAuthorityError("process creation identity changed before assignment")
        self._bound_pid = pid
        self._bound_creation_identity = live_identity
        try:
            self.verify(pid)
        except ProcessAuthorityError:
            self._bound_pid = None
            self._bound_creation_identity = None
            raise
        self._close_write_fd()

    def verify(self, pid: int) -> None:
        pid = _validate_pid(pid)
        if self._bound_pid is None or self._bound_creation_identity is None:
            raise ProcessAuthorityError("process authority root process is not bound")
        if pid != self._bound_pid:
            raise ProcessAuthorityError("process is not the attested root process")
        if _posix_creation_identity(pid) != self._bound_creation_identity:
            raise ProcessAuthorityError("process creation identity no longer matches")
        proc_root = Path("/proc") / str(pid)
        getpgid = getattr(os, "getpgid", None)
        getsid = getattr(os, "getsid", None)
        if not callable(getpgid) or not callable(getsid):
            raise ProcessAuthorityError("process session attestation is unsupported")
        try:
            if getpgid(pid) != pid or getsid(pid) != pid:
                raise ProcessAuthorityError("process is not the attested session leader")
            expected = f"pipe:[{self._inode}]"
            expected_context = f"{_CONTEXT_ENVIRONMENT_KEY}={self._context_digest}".encode()
            if expected_context not in (proc_root / "environ").read_bytes().split(b"\0"):
                raise ProcessAuthorityError("process context attestation does not match")
            with os.scandir(f"/proc/{pid}/fd") as entries:
                found = any(
                    os.readlink(entry.path) == expected
                    for entry in entries
                    if os.path.exists(entry.path)
                )
            if not found:
                raise ProcessAuthorityError("process does not hold the attestation capability")
            self._claim_verified = True
        except ProcessAuthorityError:
            raise
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

    def _close_write_fd(self) -> None:
        if self._write_fd is not None:
            fd, self._write_fd = self._write_fd, None
            with contextlib.suppress(OSError):
                os.close(fd)

    def _pid_holds_attestation(self, pid: int) -> bool:
        proc_root = Path("/proc") / str(pid)
        expected_context = f"{_CONTEXT_ENVIRONMENT_KEY}={self._context_digest}".encode()
        expected_pipe = f"pipe:[{self._inode}]"
        try:
            if expected_context not in (proc_root / "environ").read_bytes().split(b"\0"):
                return False
            with os.scandir(os.fspath(proc_root / "fd")) as entries:
                for entry in entries:
                    try:
                        if os.readlink(entry.path) == expected_pipe:
                            return True
                    except OSError:
                        continue
        except OSError:
            return False
        return False

    def _owned_pids(self) -> set[int]:
        try:
            entries = tuple(Path("/proc").iterdir())
        except OSError as error:
            raise ProcessAuthorityError("unable to enumerate attested process tree") from error
        owned: set[int] = set()
        for entry in entries:
            if not entry.name.isdigit():
                continue
            pid = int(entry.name)
            if self._pid_holds_attestation(pid):
                owned.add(pid)
        return owned

    def _owned_process_group_pids(self) -> set[int]:
        if self._bound_pid is None:
            return set()
        try:
            entries = tuple(Path("/proc").iterdir())
        except OSError as error:
            raise ProcessAuthorityError("unable to enumerate attested process tree") from error
        owned: set[int] = set()
        for entry in entries:
            if not entry.name.isdigit():
                continue
            try:
                stat_fields = (
                    (entry / "stat").read_text(encoding="utf-8").rpartition(")")[2].split()
                )
                if len(stat_fields) < 4 or stat_fields[0] in {"Z", "X"}:
                    continue
                if (
                    int(stat_fields[2]) == self._bound_pid
                    and int(stat_fields[3]) == self._bound_pid
                ):
                    owned.add(int(entry.name))
            except (OSError, ValueError):
                continue
        return owned

    def drain(self) -> None:
        """Terminate every live process still holding this exact capability."""

        if not self._claim_verified:
            return
        remaining: set[int] = set()
        kill_signal = getattr(signal, "SIGKILL", signal.SIGTERM)
        for sig in (signal.SIGTERM, kill_signal):
            marker_pids = self._owned_pids()
            group_pids = self._owned_process_group_pids()
            remaining = marker_pids | group_pids
            if not remaining:
                return
            for pid in marker_pids:
                try:
                    os.kill(pid, sig)
                except ProcessLookupError:
                    continue
                except OSError as error:
                    raise ProcessAuthorityError("unable to drain attested process tree") from error
            if group_pids:
                killpg = getattr(os, "killpg", None)
                if not callable(killpg):
                    raise ProcessAuthorityError("process group termination is unsupported")
                try:
                    killpg(self._bound_pid, sig)
                except ProcessLookupError:
                    pass
                except OSError as error:
                    raise ProcessAuthorityError("unable to drain attested process tree") from error
            time.sleep(0.05)
        if self._owned_pids() or self._owned_process_group_pids():
            raise ProcessAuthorityError("attested process descendants remain live")

    def close(self) -> None:
        self._close_write_fd()
        self._claim_verified = False


class _WindowsProcessAuthority(ProcessAuthority):
    _JOB_ACCESS = 0x000C
    _PROCESS_ACCESS = 0x1101
    _PROCESS_QUERY = 0x1000
    _THREAD_SUSPEND_RESUME = 0x0002
    _THREAD_QUERY_LIMITED_INFORMATION = 0x0800
    _TH32CS_SNAPPROCESS = 0x00000002
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

    class _ProcessEntry32(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_uint32),
            ("cntUsage", ctypes.c_uint32),
            ("th32ProcessID", ctypes.c_uint32),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", ctypes.c_uint32),
            ("cntThreads", ctypes.c_uint32),
            ("th32ParentProcessID", ctypes.c_uint32),
            ("pcPriClassBase", ctypes.c_int32),
            ("dwFlags", ctypes.c_uint32),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    def __init__(
        self,
        attempt_root: Path,
        *,
        name: str,
        handle: Any,
        context_digest: str,
        bound_pid: int | None = None,
        bound_creation_identity: str | None = None,
    ) -> None:
        self._binding = _attempt_binding(attempt_root)
        self._context_digest = _validate_context_digest(context_digest)
        self._handle = handle
        self._bound_pid = bound_pid
        self._bound_creation_identity = bound_creation_identity
        self._claim_verified = False
        self._root_checked = False
        self._claim = {
            "kind": "windows-job",
            "attempt_binding": self._binding,
            "name": name,
            "context_digest": self._context_digest,
        }

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
        k.Process32FirstW.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_WindowsProcessAuthority._ProcessEntry32),
        ]
        k.Process32FirstW.restype = ctypes.c_int
        k.Process32NextW.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_WindowsProcessAuthority._ProcessEntry32),
        ]
        k.Process32NextW.restype = ctypes.c_int
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
    def create(
        cls,
        attempt_root: Path,
        context_digest: str,
    ) -> _WindowsProcessAuthority:
        context_digest = _validate_context_digest(context_digest)
        binding = _attempt_binding(attempt_root)
        name = _windows_job_name(binding, context_digest)
        k = cls._kernel32()
        security = cls._SecurityAttributes(ctypes.sizeof(cls._SecurityAttributes), None, 1)
        ctypes.set_last_error(0)
        handle = k.CreateJobObjectW(ctypes.byref(security), name)
        if not handle:
            raise ProcessAuthorityError("unable to create process job attestation")
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            k.CloseHandle(handle)
            raise ProcessAuthorityError("process authority job claim already exists")
        return cls(
            attempt_root,
            name=name,
            handle=handle,
            context_digest=context_digest,
        )

    @classmethod
    def from_claim(
        cls, attempt_root: Path, claim: object, context_digest: str
    ) -> _WindowsProcessAuthority:
        context_digest = _validate_context_digest(context_digest)
        binding = _attempt_binding(attempt_root)
        name = claim.get("name") if isinstance(claim, dict) else None
        if not isinstance(claim, dict) or claim.get("attempt_binding") != binding:
            raise ProcessAuthorityError("process authority attempt binding does not match")
        if claim.get("context_digest") != context_digest:
            raise ProcessAuthorityError("process authority context binding does not match")
        bound_pid = _validate_pid(claim.get("root_pid"))
        bound_creation_identity = _validate_creation_identity(claim.get("root_creation_identity"))
        expected_name = _windows_job_name(binding, context_digest)
        if not isinstance(name, str) or name != expected_name:
            raise ProcessAuthorityError("process authority job name is invalid")
        handle = cls._kernel32().OpenJobObjectW(cls._JOB_ACCESS, False, name)
        if not handle:
            raise ProcessAuthorityError("live process job attestation is unavailable")
        return cls(
            attempt_root,
            name=name,
            handle=handle,
            context_digest=context_digest,
            bound_pid=bound_pid,
            bound_creation_identity=bound_creation_identity,
        )

    @property
    def claim(self) -> dict[str, object]:
        if self._bound_pid is None or self._bound_creation_identity is None:
            raise ProcessAuthorityError("process authority root process is not bound")
        return {
            **self._claim,
            "root_pid": self._bound_pid,
            "root_creation_identity": self._bound_creation_identity,
        }

    def child_environment(self) -> dict[str, str]:
        return {}

    def child_handle(self) -> int | None:
        return None if self._handle is None else int(self._handle)

    @staticmethod
    def _creation_identity_from_handle(handle: Any) -> str:
        from ctypes import wintypes

        k = _WindowsProcessAuthority._kernel32()
        times = [wintypes.FILETIME() for _ in range(4)]
        if not k.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
            raise ProcessAuthorityError("unable to query process creation identity")
        creation_value = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        return f"windows:{creation_value}"

    def _live_creation_identity(self, pid: int, handle: Any | None = None) -> str:
        pid = _validate_pid(pid)
        k = self._kernel32()
        opened = handle is None
        process = handle if handle is not None else k.OpenProcess(self._PROCESS_QUERY, False, pid)
        if not process:
            raise ProcessAuthorityError("attested process is not live")
        try:
            return self._creation_identity_from_handle(process)
        finally:
            if opened:
                k.CloseHandle(process)

    def _job_members(self) -> tuple[tuple[int, str], ...]:
        k = self._kernel32()
        snapshot = k.CreateToolhelp32Snapshot(self._TH32CS_SNAPPROCESS, 0)
        invalid_handle = ctypes.c_void_p(-1).value
        if not snapshot or snapshot == invalid_handle:
            raise ProcessAuthorityError("unable to enumerate attested job processes")
        members: list[tuple[int, str]] = []
        try:
            entry = self._ProcessEntry32()
            entry.dwSize = ctypes.sizeof(entry)
            first = k.Process32FirstW(snapshot, ctypes.byref(entry))
            while first:
                pid = int(entry.th32ProcessID)
                if pid > 0:
                    process = k.OpenProcess(self._PROCESS_QUERY, False, pid)
                    if process:
                        try:
                            in_job = ctypes.c_int()
                            if (
                                k.IsProcessInJob(
                                    process,
                                    self._handle,
                                    ctypes.byref(in_job),
                                )
                                and in_job.value
                            ):
                                try:
                                    identity = self._live_creation_identity(pid, process)
                                except ProcessAuthorityError:
                                    pass
                                else:
                                    members.append((pid, identity))
                        finally:
                            k.CloseHandle(process)
                entry.dwSize = ctypes.sizeof(entry)
                if not k.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
                first = 1
        finally:
            k.CloseHandle(snapshot)
        return tuple(members)

    def _verify_root_process(self, pid: int, identity: str) -> None:
        if self._root_checked:
            return
        try:
            identity_value = int(identity.removeprefix("windows:"))
        except ValueError as error:
            raise ProcessAuthorityError("process creation identity is invalid") from error
        members = self._job_members()
        if not members:
            raise ProcessAuthorityError("attested root process is not in the job")
        try:
            earliest = min(
                members,
                key=lambda member: int(member[1].removeprefix("windows:")),
            )
        except ValueError as error:
            raise ProcessAuthorityError("attested job process identity is invalid") from error
        if earliest != (pid, identity) or identity_value != int(
            earliest[1].removeprefix("windows:")
        ):
            raise ProcessAuthorityError("process is not the attested root process")
        self._root_checked = True

    def bind(self, pid: int, expected_creation_identity: str | None = None) -> None:
        pid = _validate_pid(pid)
        if self._handle is None:
            raise ProcessAuthorityError("process authority handle is unavailable")
        k = self._kernel32()
        process = k.OpenProcess(self._PROCESS_ACCESS, False, pid)
        if not process:
            raise ProcessAuthorityError("unable to open launched process for attestation")
        assigned = False
        try:
            live_identity = self._live_creation_identity(pid, process)
            if (
                expected_creation_identity is not None
                and _validate_creation_identity(expected_creation_identity) != live_identity
            ):
                raise ProcessAuthorityError("process creation identity changed before assignment")
            if not k.AssignProcessToJobObject(self._handle, process):
                raise ProcessAuthorityError("unable to bind launched process to attestation")
            self._bound_pid = pid
            self._bound_creation_identity = live_identity
            assigned = True
            self.verify(pid)
        except ProcessAuthorityError:
            if assigned:
                with contextlib.suppress(ProcessAuthorityError):
                    self._terminate_job()
            raise
        finally:
            k.CloseHandle(process)

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
        pid = _validate_pid(pid)
        if self._bound_pid is None or self._bound_creation_identity is None:
            raise ProcessAuthorityError("process authority root process is not bound")
        if pid != self._bound_pid:
            raise ProcessAuthorityError("process is not the attested root process")
        k = self._kernel32()
        process = k.OpenProcess(self._PROCESS_QUERY, False, pid)
        if not process:
            raise ProcessAuthorityError("attested process is not live")
        try:
            if self._live_creation_identity(pid, process) != self._bound_creation_identity:
                raise ProcessAuthorityError("process creation identity no longer matches")
            in_job = ctypes.c_int()
            if not k.IsProcessInJob(process, self._handle, ctypes.byref(in_job)):
                raise ProcessAuthorityError("unable to verify process job attestation")
            if not in_job.value:
                raise ProcessAuthorityError("process is not in the attested job")
            self._verify_root_process(pid, self._bound_creation_identity)
            self._claim_verified = True
        except ProcessAuthorityError:
            raise
        finally:
            k.CloseHandle(process)

    def terminate(self, pid: int, *, force: bool = False) -> None:
        self.verify(pid)
        self._terminate_job()

    def drain(self) -> None:
        """Terminate descendants only after this exact root claim was verified."""

        if self._claim_verified:
            self._terminate_job()

    def close(self) -> None:
        if self._handle:
            handle, self._handle = self._handle, None
            self._claim_verified = False
            self._kernel32().CloseHandle(handle)


__all__ = ["ProcessAuthority", "ProcessAuthorityError"]
