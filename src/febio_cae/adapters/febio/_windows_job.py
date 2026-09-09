"""Private Windows process ownership, assigned while the root is suspended.

No PID lookup, breakaway, or uncontained fallback. Windows 8+ nested jobs are
required when the hosting process is itself in a job. CPython's Windows API
wrapper constructs STARTUPINFOEX with an explicit inherited-handle allowlist.
"""

from __future__ import annotations

import ctypes
import importlib
import os
import subprocess
from ctypes import wintypes
from pathlib import Path
from typing import Any, BinaryIO


class _Limits(ctypes.Structure):
    _fields_ = [
        ("per_process_time", ctypes.c_longlong),
        ("per_job_time", ctypes.c_longlong),
        ("flags", wintypes.DWORD),
        ("minimum_working_set", ctypes.c_size_t),
        ("maximum_working_set", ctypes.c_size_t),
        ("active_process_limit", wintypes.DWORD),
        ("affinity", ctypes.c_size_t),
        ("priority", wintypes.DWORD),
        ("scheduling", wintypes.DWORD),
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("basic", _Limits),
        ("io_counters", ctypes.c_ulonglong * 6),
        ("process_memory", ctypes.c_size_t),
        ("job_memory", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t),
        ("peak_job_memory", ctypes.c_size_t),
    ]


class _Accounting(ctypes.Structure):
    _fields_ = [
        ("total_user", ctypes.c_longlong),
        ("total_kernel", ctypes.c_longlong),
        ("period_user", ctypes.c_longlong),
        ("period_kernel", ctypes.c_longlong),
        ("page_faults", wintypes.DWORD),
        ("total_processes", wintypes.DWORD),
        ("active_processes", wintypes.DWORD),
        ("terminated_processes", wintypes.DWORD),
    ]


def _kernel() -> Any:
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
        "SetInformationJobObject": (
            [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD],
            wintypes.BOOL,
        ),
        "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
        "QueryInformationJobObject": (
            [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p],
            wintypes.BOOL,
        ),
        "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        "ResumeThread": ([wintypes.HANDLE], wintypes.DWORD),
        "GetProcessTimes": ([wintypes.HANDLE] + [ctypes.c_void_p] * 4, wintypes.BOOL),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(api, name)
        function.argtypes = arguments
        function.restype = result
    return api


class LaunchCleanupPending(OSError):
    """Failed launch with an outstanding, retained-handle cleanup obligation."""

    def __init__(self, process: WindowsJobProcess) -> None:
        super().__init__("failed launch cleanup did not confirm exit")
        self.process = process


class WindowsJobProcess:
    """Retain actual kernel handles until root exit AND job accounting is zero."""

    def __init__(
        self,
        argv: tuple[str, ...],
        cwd: Path,
        stdout: BinaryIO,
        stderr: BinaryIO,
        *,
        environment: dict[str, str] | None = None,
        memory_limit_bytes: int | None = None,
    ) -> None:
        if memory_limit_bytes is not None and (
            type(memory_limit_bytes) is not int or memory_limit_bytes <= 0
        ):
            raise ValueError("memory_limit_bytes must be a positive integer")
        self._api = _kernel()
        self._win = importlib.import_module("_winapi")
        self._job: int | None = None
        self._process: int | None = None
        self.pid = 0
        self.creation_time = 0
        self.returncode: int | None = None
        self.closed = False
        self._assigned = False
        thread: int | None = None
        inherited: list[int] = []
        try:
            self._job = self._api.CreateJobObjectW(None, None)
            self._check(bool(self._job))
            limits = _ExtendedLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE only.
            if memory_limit_bytes is not None:
                limits.basic.flags |= 0x100 | 0x200  # PROCESS_MEMORY | JOB_MEMORY
                limits.process_memory = memory_limit_bytes
                limits.job_memory = memory_limit_bytes
            self._check(
                self._api.SetInformationJobObject(
                    self._job, 9, ctypes.byref(limits), ctypes.sizeof(limits)
                )
            )
            msvcrt = importlib.import_module("msvcrt")
            current = self._win.GetCurrentProcess()
            with open(os.devnull, "rb") as stdin:
                for stream in (stdin, stdout, stderr):
                    inherited.append(
                        self._win.DuplicateHandle(
                            current,
                            msvcrt.get_osfhandle(stream.fileno()),
                            current,
                            0,
                            True,
                            self._win.DUPLICATE_SAME_ACCESS,
                        )
                    )
                info = subprocess.STARTUPINFO()
                info.dwFlags = subprocess.STARTF_USESTDHANDLES | subprocess.STARTF_USESHOWWINDOW
                info.wShowWindow = subprocess.SW_HIDE
                info.hStdInput, info.hStdOutput, info.hStdError = inherited
                info.lpAttributeList = {"handle_list": inherited}
                # CPython adds EXTENDED_STARTUPINFO_PRESENT for lpAttributeList.
                self._process, thread, self.pid, _ = self._win.CreateProcess(
                    argv[0],
                    subprocess.list2cmdline(argv),
                    None,
                    None,
                    True,
                    0x4 | 0x08000000,  # CREATE_SUSPENDED | CREATE_NO_WINDOW
                    environment,
                    str(cwd),
                    info,
                )
            self._assign()
            self._assigned = True
            times = [ctypes.c_ulonglong() for _ in range(4)]
            self._check(
                self._api.GetProcessTimes(self._process, *(ctypes.byref(item) for item in times))
            )
            self.creation_time = times[0].value
            if self._api.ResumeThread(thread) != 1:
                raise OSError("root thread could not resume from its initial suspension")
        except BaseException as error:
            # Even failed assignment leaves a suspended process that we created.
            # It is addressed only by its retained handle, never its numeric PID.
            if not self.retry_launch_cleanup():
                raise LaunchCleanupPending(self) from error
            raise
        finally:
            if thread is not None:
                self._win.CloseHandle(thread)
            for handle in inherited:
                self._win.CloseHandle(handle)

    @staticmethod
    def _check(result: Any) -> None:
        if not result:
            raise ctypes.WinError(ctypes.get_last_error())

    def _assign(self) -> None:
        self._check(self._api.AssignProcessToJobObject(self._job, self._process))

    def poll(self) -> int | None:
        if self.returncode is not None:
            return self.returncode
        if self._process is None:
            raise OSError("root handle is unavailable")
        status = self._win.WaitForSingleObject(self._process, 0)
        if status == 0:
            self.returncode = int(self._win.GetExitCodeProcess(self._process))
        elif status != 258:
            raise OSError("root handle observation failed")
        return self.returncode

    def active_processes(self) -> int:
        if self._job is None:
            raise OSError("job handle is unavailable")
        accounting = _Accounting()
        self._check(
            self._api.QueryInformationJobObject(
                self._job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None
            )
        )
        return int(accounting.active_processes)

    def terminate_tree(self) -> None:
        if self._job is None:
            raise OSError("job handle is unavailable")
        self._check(self._api.TerminateJobObject(self._job, 1))

    def retry_launch_cleanup(self) -> bool:
        """Never lose the last handle if termination or exit observation fails."""
        if self.closed:
            return True
        if self._process is not None:
            try:
                if self._assigned:
                    self.terminate_tree()
                else:
                    self._win.TerminateProcess(self._process, 1)
            except OSError:
                pass  # Termination can fail after exit; observe the retained handle.
            try:
                if self._win.WaitForSingleObject(self._process, 100) != 0:
                    return False
                self.returncode = int(self._win.GetExitCodeProcess(self._process))
                if self._assigned and self.active_processes() != 0:
                    return False
            except OSError:
                return False
        self.close()
        return True

    def close(self) -> None:
        """Release only after observed exit, retaining uncertain cleanup capability."""
        if self._process is not None and (
            self.poll() is None or (self._assigned and self.active_processes() != 0)
        ):
            raise OSError("cannot discard live or uncertain owned process handles")
        if self._job is not None:
            self._win.CloseHandle(self._job)
            self._job = None
        if self._process is not None:
            self._win.CloseHandle(self._process)
            self._process = None
        self.closed = True
