"""Owned, headless FEBio process supervision."""

from __future__ import annotations

import contextlib
import json
import math
import os
import signal
import subprocess
import threading
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from .fbs import (
    FbsAdapterAuthority,
    FbsValidation,
    _authority_record,
    _invalid_validation,
    validate_requested_fields,
)
from .log import LogValidation, LogValidator, validate_log
from .types import (
    OutputFreshnessError,
    SolverClassification,
    SolverConfigurationError,
    SolverLaunchError,
    SolverLaunchSpec,
    SolverOwnershipError,
    SolverRunResult,
    SolverState,
)

__all__ = ["SolverSupervisor"]

_PROCESS_RECORD_NAME = "process.json"
_RESULT_FIELDS = (
    "state",
    "classification",
    "return_code",
    "pid",
    "command",
    "log_path",
    "xplt_path",
    "started_at",
    "finished_at",
    "log_validation",
    "fbs_validation",
    "error",
)


@dataclass(frozen=True, slots=True)
class _ResultIssuance:
    result: SolverRunResult
    supervisor: SolverSupervisor
    spec: SolverLaunchSpec
    process: subprocess.Popen[bytes] | _ReconnectedProcess | None
    process_record: dict[str, object] | None
    case_id: str
    intent_id: str
    attempt_id: str
    executable_path: str
    process_identity: str | None
    started_at: datetime | None
    log_path: Path
    xplt_path: Path
    finished_at: datetime | None
    snapshot: tuple[object, ...]


_RESULT_REGISTRY: dict[int, _ResultIssuance] = {}
_RESULT_REGISTRY_LOCK = threading.RLock()


@dataclass(frozen=True, slots=True)
class _ProcessMetadata:
    executable_path: str
    creation_identity: str
    alive: bool
    return_code: int | None


def _normalise_executable(path: str | Path) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(path))))


def _windows_process_metadata(pid: int) -> _ProcessMetadata:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        raise ProcessLookupError(pid)
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            raise OSError(ctypes.get_last_error(), "unable to query process exit code")
        code = exit_code.value
        if code != 259:  # STILL_ACTIVE
            return _ProcessMetadata("", "", False, code)
        buffer = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buffer))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            raise OSError(ctypes.get_last_error(), "unable to query process executable")
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel32.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
            raise OSError(ctypes.get_last_error(), "unable to query process creation time")
        creation_value = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        return _ProcessMetadata(
            executable_path=buffer.value,
            creation_identity=f"windows:{creation_value}",
            alive=True,
            return_code=None,
        )
    finally:
        kernel32.CloseHandle(handle)


def _posix_process_metadata(pid: int) -> _ProcessMetadata:
    proc_root = Path("/proc") / str(pid)
    try:
        stat_line = (proc_root / "stat").read_text(encoding="utf-8")
        stat_fields = stat_line.rpartition(")")[2].split()
        executable_path = os.readlink(os.fspath(proc_root / "exe"))
    except OSError as error:
        raise ProcessLookupError(pid) from error
    if len(stat_fields) < 20:
        raise OSError("unable to read process start identity")
    state = stat_fields[0]
    return _ProcessMetadata(
        executable_path=executable_path,
        creation_identity=f"posix:{stat_fields[19]}",
        alive=state not in {"Z", "X"},
        return_code=None if state not in {"Z", "X"} else 0,
    )


def _process_metadata(pid: int) -> _ProcessMetadata:
    if os.name == "nt":
        return _windows_process_metadata(pid)
    if os.name == "posix":
        return _posix_process_metadata(pid)
    raise OSError("process identity is unsupported on this platform")


def _process_action(process: subprocess.Popen[bytes] | _ReconnectedProcess, action: str) -> None:
    getattr(process, action)()


class _ReconnectedProcess:
    """Small process handle for a process that is not this client's child."""

    def __init__(self, pid: int, executable_path: str, creation_identity: str) -> None:
        self.pid = pid
        self._executable_path = executable_path
        self._creation_identity = creation_identity

    def poll(self) -> int | None:
        metadata = _process_metadata(self.pid)
        if metadata.alive and (
            _normalise_executable(metadata.executable_path)
            != _normalise_executable(self._executable_path)
            or metadata.creation_identity != self._creation_identity
        ):
            raise SolverOwnershipError("reconnected process identity no longer matches")
        return None if metadata.alive else metadata.return_code

    def wait(self, timeout: float | None = None) -> int | None:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            return_code = self.poll()
            if return_code is not None:
                return return_code
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(str(self.pid), timeout or 0.0)
            time.sleep(0.05)


class SolverSupervisor:
    """Launch and monitor one solver process with attempt-owned outputs.

    The process is started without a shell and, where supported, in its own
    process group.  ``cancel`` and timeout handling operate on that owned
    group; callers never need to supply a raw PID.
    """

    def __init__(
        self,
        spec: SolverLaunchSpec,
        *,
        case_id: str | None = None,
        intent_id: str | None = None,
        attempt_id: str | None = None,
        fbs_adapter: FbsAdapterAuthority | None = None,
        requested_fields: Iterable[str] | None = None,
        log_validator: LogValidator | None = None,
    ) -> None:
        self.spec = spec
        if any(
            value is not None and (not isinstance(value, str) or not value.strip())
            for value in (case_id, intent_id, attempt_id)
        ):
            raise SolverConfigurationError("context identifiers must be non-empty strings")
        self._case_id = case_id or ""
        self._intent_id = intent_id or ""
        self._attempt_id = attempt_id or spec.attempt_root.name
        if fbs_adapter is not None:
            try:
                _authority_record(fbs_adapter)
            except TypeError as error:
                raise SolverConfigurationError(
                    "fbs_adapter must be issued by FbsAdapterManager"
                ) from error
        self._fbs_adapter = fbs_adapter
        self._requested_fields = (
            tuple(requested_fields) if requested_fields is not None else spec.requested_fields
        )
        self._log_validator = log_validator
        self._owner_token = uuid.uuid4().hex
        self._lock = threading.RLock()
        self._state = SolverState.NOT_STARTED
        self._process: subprocess.Popen[bytes] | _ReconnectedProcess | None = None
        self._started_at: datetime | None = None
        self._result: SolverRunResult | None = None
        self._process_record: dict[str, object] | None = None

    @property
    def state(self) -> SolverState:
        with self._lock:
            return self._state

    @property
    def process_id(self) -> int | None:
        with self._lock:
            return self._process.pid if self._process is not None else None

    @property
    def pid(self) -> int | None:
        """Read-only diagnostic PID; cancellation remains supervisor-owned."""

        return self.process_id

    @property
    def owner_token(self) -> str:
        return self._owner_token

    @property
    def process_record_path(self) -> Path:
        """The only persisted authority used to reconnect this attempt."""

        return self.spec.attempt_root / _PROCESS_RECORD_NAME

    @property
    def result(self) -> SolverRunResult | None:
        with self._lock:
            return self._result

    def start(self) -> SolverSupervisor:
        """Prepare fresh outputs and launch the owned process."""

        with self._lock:
            if self._state is not SolverState.NOT_STARTED:
                raise RuntimeError(f"solver cannot start from state {self._state}")

            try:
                self.spec.prepare_outputs()
                if os.path.lexists(os.fspath(self.process_record_path)):
                    raise SolverLaunchError(
                        f"process record already exists: {self.process_record_path}"
                    )
                if not self.spec.input_path.is_file():
                    raise FileNotFoundError(f"solver input does not exist: {self.spec.input_path}")
                environment = os.environ.copy()
                environment.update(self.spec.environment)
                # These markers let a child process discover its owned attempt
                # without granting it any additional filesystem authority.
                environment["FEBIO_CAE_HARNESS_OWNER"] = self._owner_token
                environment["FEBIO_CAE_HARNESS_ATTEMPT_ROOT"] = os.fspath(self.spec.attempt_root)
                outputs = self.spec.expected_outputs
                environment["FEBIO_CAE_HARNESS_LOG"] = os.fspath(outputs.log_path)
                environment["FEBIO_CAE_HARNESS_XPLT"] = os.fspath(outputs.xplt_path)

                process: subprocess.Popen[bytes] | None = None
                if os.name == "nt":
                    creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                    process = subprocess.Popen(
                        self.spec.command,
                        cwd=os.fspath(self.spec.cwd),
                        env=environment,
                        shell=False,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=creation_flags,
                    )
                else:
                    process = subprocess.Popen(
                        self.spec.command,
                        cwd=os.fspath(self.spec.cwd),
                        env=environment,
                        shell=False,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                started_at = datetime.now(UTC)
                metadata = _process_metadata(process.pid)
                self._process = process
                self._started_at = started_at
                self._process_record = self._make_process_record(process.pid, metadata, started_at)
                self._write_process_record(self._process_record)
            except (OSError, OutputFreshnessError, ValueError) as error:
                process = locals().get("process")
                if isinstance(process, subprocess.Popen):
                    try:
                        self._terminate_owned_process(process)
                    except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
                        _process_action(process, "kill")
                self._state = SolverState.FAILED
                raise SolverLaunchError(f"unable to launch solver: {error}") from error

            self._state = SolverState.RUNNING
            return self

    launch = start

    def poll(self) -> int | None:
        """Return the process return code, if it has exited."""

        with self._lock:
            process = self._process
        if process is None:
            return None
        return process.poll()

    @classmethod
    def reconnect(
        cls,
        spec: SolverLaunchSpec,
        *,
        case_id: str | None = None,
        intent_id: str | None = None,
        attempt_id: str | None = None,
        fbs_adapter: FbsAdapterAuthority | None = None,
        requested_fields: Iterable[str] | None = None,
        log_validator: LogValidator | None = None,
    ) -> SolverSupervisor:
        """Rebuild a supervisor only after validating its owned record."""

        supervisor = cls(
            spec,
            case_id=case_id,
            intent_id=intent_id,
            attempt_id=attempt_id,
            fbs_adapter=fbs_adapter,
            requested_fields=requested_fields,
            log_validator=log_validator,
        )
        record = supervisor._read_process_record()
        metadata, started_at = supervisor._validate_process_record(record)
        supervisor._process_record = record
        pid = cast(int, record["pid"])
        supervisor._process = _ReconnectedProcess(
            pid, metadata.executable_path, metadata.creation_identity
        )
        supervisor._started_at = started_at
        supervisor._state = SolverState.RUNNING
        return supervisor

    def wait(self, timeout_seconds: float | None = None) -> SolverRunResult:
        """Wait for completion, enforcing the configured timeout if present."""

        with self._lock:
            if self._result is not None:
                return self._result
            if self._state is SolverState.NOT_STARTED:
                raise RuntimeError("solver has not been started")
            process = self._process
            configured_timeout = self.spec.timeout_seconds
        if process is None:  # pragma: no cover - defensive state guard
            raise SolverLaunchError("solver process ownership was lost")

        effective_timeout = (
            self.spec.timeout_seconds if timeout_seconds is None else timeout_seconds
        )
        if effective_timeout is not None:
            if isinstance(effective_timeout, bool) or not isinstance(
                effective_timeout, (int, float)
            ):
                raise ValueError("timeout_seconds must be a positive finite number")
            if effective_timeout <= 0 or not math.isfinite(float(effective_timeout)):
                raise ValueError("timeout_seconds must be a positive finite number")
        elif configured_timeout is not None:
            effective_timeout = configured_timeout

        try:
            return_code = process.wait(timeout=effective_timeout)
        except subprocess.TimeoutExpired:
            self._terminate_owned_process(process)
            return self._complete(SolverState.TIMED_OUT, process.poll())
        return self._complete(
            SolverState.NORMAL_EXIT if return_code == 0 else SolverState.FAILED,
            return_code,
        )

    wait_for_completion = wait

    def run(self, timeout_seconds: float | None = None) -> SolverRunResult:
        """Start once and wait for the owned process."""

        with self._lock:
            started = self._state is not SolverState.NOT_STARTED
        if not started:
            self.start()
        return self.wait(timeout_seconds)

    def cancel(self) -> SolverRunResult:
        """Cancel the owned process tree and return a cancelled result."""

        with self._lock:
            if self._result is not None:
                return self._result
            if self._state is SolverState.NOT_STARTED:
                return self._complete(SolverState.CANCELLED, None)
            process = self._process
        if process is None:  # pragma: no cover - defensive state guard
            raise SolverLaunchError("solver process ownership was lost")
        self._terminate_owned_process(process)
        return self._complete(SolverState.CANCELLED, process.poll())

    def _make_process_record(
        self, pid: int, metadata: _ProcessMetadata, started_at: datetime
    ) -> dict[str, object]:
        outputs = self.spec.expected_outputs
        return {
            "case_id": self._case_id,
            "intent_id": self._intent_id,
            "attempt_id": self._attempt_id,
            "executable_path": str(self.spec.executable),
            "pid": pid,
            "process_creation_identity": metadata.creation_identity,
            "start_time": started_at.isoformat(),
            "owned_output_paths": {
                "log": str(outputs.log_path),
                "xplt": str(outputs.xplt_path),
            },
        }

    def _write_process_record(self, record: dict[str, object]) -> None:
        path = self.process_record_path
        temporary = path.with_name(f".{path.name}.{self._owner_token}.tmp")
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(record, stream, indent=2, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(os.fspath(temporary), os.fspath(path))
        except (OSError, TypeError, ValueError) as error:
            with contextlib.suppress(OSError):
                temporary.unlink(missing_ok=True)
            raise OSError(f"unable to persist process record: {error}") from error

    def _read_process_record(self) -> dict[str, object]:
        path = self.process_record_path
        if not path.is_file() or path.is_symlink():
            raise SolverOwnershipError(f"missing process record: {path}")
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise SolverOwnershipError(f"invalid process record: {path}") from error
        if not isinstance(record, dict):
            raise SolverOwnershipError("process record must be a JSON object")
        return record

    def _validate_record_path(self, value: object, expected: Path, label: str) -> None:
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise SolverOwnershipError(f"{label} must be an absolute path")
        candidate = Path(os.path.abspath(value))
        root = self.spec.attempt_root
        if (
            candidate != expected
            or not candidate.is_relative_to(root)
            or not Path(os.path.realpath(candidate)).is_relative_to(root)
        ):
            raise SolverOwnershipError(f"{label} escapes the attempt root")

    def _validate_process_record(
        self, record: dict[str, object]
    ) -> tuple[_ProcessMetadata, datetime]:
        expected_ids = {
            "case_id": self._case_id,
            "intent_id": self._intent_id,
            "attempt_id": self._attempt_id,
        }
        if any(record.get(name) != expected for name, expected in expected_ids.items()):
            raise SolverOwnershipError("process record identity does not match")

        executable_value = record.get("executable_path")
        if not isinstance(executable_value, str) or not Path(executable_value).is_absolute():
            raise SolverOwnershipError("process record executable must be absolute")
        if _normalise_executable(executable_value) != _normalise_executable(self.spec.executable):
            raise SolverOwnershipError("process record executable does not match")

        pid = record.get("pid")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise SolverOwnershipError("process record PID is invalid")
        identity = record.get("process_creation_identity")
        if not isinstance(identity, str) or not identity:
            raise SolverOwnershipError("process record creation identity is invalid")

        started_value = record.get("start_time")
        if not isinstance(started_value, str):
            raise SolverOwnershipError("process record start time is invalid")
        try:
            started_at = datetime.fromisoformat(started_value)
        except ValueError as error:
            raise SolverOwnershipError("process record start time is invalid") from error
        output_values = record.get("owned_output_paths")
        if not isinstance(output_values, dict):
            raise SolverOwnershipError("process record output paths are invalid")
        outputs = self.spec.expected_outputs
        self._validate_record_path(output_values.get("log"), outputs.log_path, "LOG path")
        self._validate_record_path(output_values.get("xplt"), outputs.xplt_path, "XPLT path")

        try:
            metadata = _process_metadata(pid)
        except OSError as error:
            raise SolverOwnershipError("recorded process is not live") from error
        if not metadata.alive:
            raise SolverOwnershipError("recorded process is not live")
        if _normalise_executable(metadata.executable_path) != _normalise_executable(
            executable_value
        ):
            raise SolverOwnershipError("current process executable does not match")
        if metadata.creation_identity != identity:
            raise SolverOwnershipError("current process creation identity does not match")
        return metadata, started_at

    def _terminate_owned_process(
        self, process: subprocess.Popen[bytes] | _ReconnectedProcess
    ) -> None:
        """Terminate only the process group created by this supervisor."""

        if process.poll() is not None:
            return
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    check=False,
                    capture_output=True,
                    text=True,
                )
            except OSError:
                _process_action(process, "terminate")
        else:
            try:
                if not self._kill_process_group(process.pid, "SIGTERM"):
                    _process_action(process, "terminate")
            except (OSError, ProcessLookupError):
                _process_action(process, "terminate")

        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                _process_action(process, "kill")
            else:
                try:
                    if not self._kill_process_group(process.pid, "SIGKILL"):
                        _process_action(process, "kill")
                except (OSError, ProcessLookupError):
                    _process_action(process, "kill")
            process.wait(timeout=2.0)

    @staticmethod
    def _kill_process_group(pid: int, signal_name: str) -> bool:
        kill_group = getattr(os, "killpg", None)
        get_group = getattr(os, "getpgid", None)
        signal_value = getattr(signal, signal_name, None)
        if not callable(kill_group) or not callable(get_group) or signal_value is None:
            return False
        kill_group(get_group(pid), signal_value)
        return True

    def _register_result(self, result: SolverRunResult) -> None:
        if type(result) is not SolverRunResult:
            raise SolverOwnershipError("solver result must be an exact SolverRunResult instance")
        process_record = self._process_record
        identity = process_record.get("process_creation_identity") if process_record else None
        issuance = _ResultIssuance(
            result=result,
            supervisor=self,
            spec=self.spec,
            process=self._process,
            process_record=process_record,
            case_id=self._case_id,
            intent_id=self._intent_id,
            attempt_id=self._attempt_id,
            executable_path=str(self.spec.executable),
            process_identity=identity if isinstance(identity, str) else None,
            started_at=self._started_at,
            log_path=self.spec.expected_outputs.log_path,
            xplt_path=self.spec.expected_outputs.xplt_path,
            finished_at=result.finished_at,
            snapshot=tuple(getattr(result, field) for field in _RESULT_FIELDS),
        )
        with _RESULT_REGISTRY_LOCK:
            existing = _RESULT_REGISTRY.get(id(result))
            if existing is not None and existing.result is not result:
                raise SolverOwnershipError("solver result registry collision")
            _RESULT_REGISTRY[id(result)] = issuance

    def _validate_result(self, candidate: object) -> SolverRunResult:
        if type(self) is not SolverSupervisor:
            raise SolverOwnershipError("solver supervisor must be an exact instance")
        if type(candidate) is not SolverRunResult:
            raise SolverOwnershipError("solver result must be an exact SolverRunResult instance")
        with self._lock:
            with _RESULT_REGISTRY_LOCK:
                issuance = _RESULT_REGISTRY.get(id(candidate))
            if issuance is None or issuance.result is not candidate:
                raise SolverOwnershipError("solver result was not issued by a supervisor")
            if issuance.supervisor is not self:
                raise SolverOwnershipError("solver result belongs to another supervisor")
            if self._result is not candidate:
                raise SolverOwnershipError("supervisor result binding is invalid")
            if self.spec is not issuance.spec:
                raise SolverOwnershipError("supervisor launch binding is invalid")
            for value, expected, label in (
                (self._case_id, issuance.case_id, "case_id"),
                (self._intent_id, issuance.intent_id, "intent_id"),
                (self._attempt_id, issuance.attempt_id, "attempt_id"),
            ):
                if (
                    not isinstance(value, str)
                    or not value.strip()
                    or not isinstance(expected, str)
                    or not expected.strip()
                    or value != expected
                ):
                    raise SolverOwnershipError(f"solver result {label} binding is invalid")
            if self._process is not issuance.process or self._process is None:
                raise SolverOwnershipError("solver result process binding is invalid")
            pid = getattr(self._process, "pid", None)
            if (
                isinstance(pid, bool)
                or not isinstance(pid, int)
                or pid <= 0
                or candidate.pid != pid
            ):
                raise SolverOwnershipError("solver result process identity is invalid")

            process_record = self._process_record
            if process_record is None or process_record is not issuance.process_record:
                raise SolverOwnershipError("solver result process record binding is invalid")
            if process_record.get("pid") != pid:
                raise SolverOwnershipError("solver result process record PID is invalid")
            executable = process_record.get("executable_path")
            if (
                not isinstance(executable, str)
                or not Path(executable).is_absolute()
                or executable != issuance.executable_path
                or _normalise_executable(executable) != _normalise_executable(self.spec.executable)
            ):
                raise SolverOwnershipError("solver result executable binding is invalid")
            identity = process_record.get("process_creation_identity")
            if (
                issuance.process_identity is None
                or not isinstance(identity, str)
                or not identity.strip()
                or identity != issuance.process_identity
            ):
                raise SolverOwnershipError("solver result process creation identity is invalid")

            started_at = self._started_at
            if (
                issuance.started_at is None
                or started_at is not issuance.started_at
                or not isinstance(started_at, datetime)
                or started_at.tzinfo is None
                or started_at.utcoffset() is None
                or candidate.started_at is not started_at
            ):
                raise SolverOwnershipError("solver result start time is invalid")
            start_value = process_record.get("start_time")
            if not isinstance(start_value, str):
                raise SolverOwnershipError("solver result process start time is invalid")
            try:
                record_started_at = datetime.fromisoformat(start_value)
            except ValueError as error:
                raise SolverOwnershipError("solver result process start time is invalid") from error
            if record_started_at != started_at:
                raise SolverOwnershipError("solver result process start time does not match")

            if (
                issuance.finished_at is None
                or not isinstance(candidate.finished_at, datetime)
                or candidate.finished_at is not issuance.finished_at
                or candidate.finished_at.tzinfo is None
                or candidate.finished_at.utcoffset() is None
                or candidate.finished_at < started_at
            ):
                raise SolverOwnershipError("solver result timestamps are invalid")
            output_values = process_record.get("owned_output_paths")
            if not isinstance(output_values, dict):
                raise SolverOwnershipError("solver result output paths are invalid")
            self._validate_record_path(output_values.get("log"), issuance.log_path, "LOG path")
            self._validate_record_path(output_values.get("xplt"), issuance.xplt_path, "XPLT path")
            for candidate_path, expected_path, path_label in (
                (candidate.log_path, issuance.log_path, "LOG path"),
                (candidate.xplt_path, issuance.xplt_path, "XPLT path"),
            ):
                if not isinstance(candidate_path, Path):
                    raise SolverOwnershipError(f"{path_label} must be a Path")
                self._validate_record_path(os.fspath(candidate_path), expected_path, path_label)
                if candidate_path != expected_path:
                    raise SolverOwnershipError(f"{path_label} binding is invalid")
            for field, expected_value in zip(_RESULT_FIELDS, issuance.snapshot, strict=True):
                try:
                    value = getattr(candidate, field)
                except AttributeError as error:
                    raise SolverOwnershipError("solver result fields are invalid") from error
                if value != expected_value:
                    raise SolverOwnershipError("solver result contents were modified")
            return candidate

    def _complete(self, state: SolverState, return_code: int | None) -> SolverRunResult:
        with self._lock:
            if self._result is not None:
                return self._result
            started_at = self._started_at
            pid = self._process.pid if self._process is not None else None

            log_validation = (
                self._log_validator.validate(self.spec.expected_outputs.log_path)
                if self._log_validator is not None
                else validate_log(
                    self.spec.expected_outputs.log_path,
                    expected_steps=self.spec.expected_steps,
                    expected_final_time=self.spec.expected_final_time,
                )
            )
            fbs_validation: FbsValidation | None = None
            classification = self._classify_process_and_outputs(
                state,
                return_code,
                log_validation,
            )
            if classification is None:
                if self._fbs_adapter is None:
                    classification = SolverClassification.FBS_UNVERIFIED
                else:
                    try:
                        fbs_validation = validate_requested_fields(
                            self._fbs_adapter,
                            self.spec.expected_outputs.xplt_path,
                            self._requested_fields,
                            attempt_root=self.spec.attempt_root,
                        )
                    except Exception as error:
                        fbs_validation = _invalid_validation(
                            self._fbs_adapter,
                            self.spec.expected_outputs.xplt_path,
                            self._requested_fields,
                            str(error),
                        )
                    if not fbs_validation.valid:
                        classification = SolverClassification.FBS_INVALID
                    else:
                        classification = SolverClassification.FBS_UNVERIFIED

            finished_at = datetime.now(UTC)
            result = SolverRunResult(
                state=state,
                classification=classification,
                return_code=return_code,
                pid=pid,
                command=self.spec.command,
                log_path=self.spec.expected_outputs.log_path,
                xplt_path=self.spec.expected_outputs.xplt_path,
                started_at=started_at,
                finished_at=finished_at,
                log_validation=log_validation,
                fbs_validation=fbs_validation,
            )
            self._register_result(result)
            self._state = state
            self._result = result
            return result

    def _classify_process_and_outputs(
        self,
        state: SolverState,
        return_code: int | None,
        log_validation: LogValidation,
    ) -> SolverClassification | None:
        if state is SolverState.TIMED_OUT:
            return SolverClassification.TIMEOUT
        if state is SolverState.CANCELLED:
            return SolverClassification.CANCELLED
        if log_validation.classification in {
            SolverClassification.NEGATIVE_JACOBIAN,
            SolverClassification.FATAL,
            SolverClassification.INIT_ONLY,
        }:
            return log_validation.classification
        if not self.spec.expected_outputs.present():
            return SolverClassification.MISSING_OUTPUT
        if return_code != 0:
            return SolverClassification.NONZERO_EXIT
        if log_validation.classification is not None:
            return log_validation.classification
        return None
