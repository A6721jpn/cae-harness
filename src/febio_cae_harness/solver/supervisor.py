"""Owned, headless FEBio process supervision."""

from __future__ import annotations

import contextlib
import json
import math
import os
import subprocess
import threading
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from ..evidence import IntentSnapshotAuthority
from .fbs import (
    FbsAdapterAuthority,
    FbsValidation,
    _authority_record,
    _invalid_validation,
    validate_requested_fields,
)
from .log import LogValidation, LogValidator, validate_log
from .process_authority import ProcessAuthority, ProcessAuthorityError
from .types import (
    OutputFreshnessError,
    SolverClassification,
    SolverConfigurationError,
    SolverLaunchCapability,
    SolverLaunchError,
    SolverLaunchSpec,
    SolverOwnershipError,
    SolverRunResult,
    SolverState,
    _json_digest,
    _validate_launch_capability,
)

__all__ = ["SolverSupervisor"]

_PROCESS_RECORD_NAME = "process.json"
_CREATE_SUSPENDED = 0x00000004
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
    started_at: datetime | None = None


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
        started_at = datetime(1601, 1, 1, tzinfo=UTC) + timedelta(microseconds=creation_value // 10)
        return _ProcessMetadata(
            executable_path=buffer.value,
            creation_identity=f"windows:{creation_value}",
            alive=True,
            return_code=None,
            started_at=started_at,
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
    try:
        boot_time = next(
            int(line.split()[1])
            for line in Path("/proc/stat").read_text(encoding="utf-8").splitlines()
            if line.startswith("btime ")
        )
        sysconf = getattr(os, "sysconf", None)
        if not callable(sysconf):
            raise OSError("process clock tick query is unsupported")
        clock_ticks = int(sysconf("SC_CLK_TCK"))
        if clock_ticks <= 0 or not stat_fields[19].isdigit():
            raise ValueError
        started_at = datetime.fromtimestamp(
            boot_time + int(stat_fields[19]) / clock_ticks,
            UTC,
        )
    except (OSError, StopIteration, ValueError, TypeError, OverflowError) as error:
        raise OSError("unable to read process start identity") from error
    return _ProcessMetadata(
        executable_path=executable_path,
        creation_identity=f"posix:{stat_fields[19]}",
        alive=state not in {"Z", "X"},
        return_code=None if state not in {"Z", "X"} else 0,
        started_at=started_at,
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

    def __init__(
        self,
        pid: int,
        executable_path: str,
        creation_identity: str,
        authority: ProcessAuthority,
    ) -> None:
        self.pid = pid
        self._executable_path = executable_path
        self._creation_identity = creation_identity
        self._authority = authority

    def poll(self) -> int | None:
        try:
            metadata = _process_metadata(self.pid)
        except ProcessLookupError:
            return 1
        if not metadata.alive:
            return metadata.return_code
        if metadata.alive and (
            _normalise_executable(metadata.executable_path)
            != _normalise_executable(self._executable_path)
            or metadata.creation_identity != self._creation_identity
        ):
            raise SolverOwnershipError("reconnected process identity no longer matches")
        try:
            self._authority.verify(self.pid)
        except ProcessAuthorityError as error:
            raise SolverOwnershipError(
                "reconnected process authority is no longer valid"
            ) from error
        return None

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
        launch_capability: SolverLaunchCapability,
        *,
        fbs_adapter: FbsAdapterAuthority | None = None,
        requested_fields: Iterable[str] | None = None,
        log_validator: LogValidator | None = None,
    ) -> None:
        (
            _capability_record,
            case_id,
            intent_id,
            attempt_id,
            _attempt_root,
            launch_context,
            launch_context_digest,
        ) = _validate_launch_capability(launch_capability)
        self._launch_capability = launch_capability
        self.spec = _capability_record.spec
        self._case_id = case_id
        self._intent_id = intent_id
        self._attempt_id = attempt_id
        self._launch_context = launch_context
        self._launch_context_digest = launch_context_digest
        if fbs_adapter is not None:
            try:
                _authority_record(fbs_adapter)
            except TypeError as error:
                raise SolverConfigurationError(
                    "fbs_adapter must be issued by FbsAdapterManager"
                ) from error
        self._fbs_adapter = fbs_adapter
        self._requested_fields = (
            tuple(requested_fields) if requested_fields is not None else self.spec.requested_fields
        )
        self._log_validator = log_validator
        self._owner_token = uuid.uuid4().hex
        self._lock = threading.RLock()
        self._state = SolverState.NOT_STARTED
        self._process: subprocess.Popen[bytes] | _ReconnectedProcess | None = None
        self._process_authority: ProcessAuthority | None = None
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

    def _revalidate_launch_binding(self) -> None:
        """Require the supervisor's capability and authority binding to remain exact."""

        (
            capability_record,
            case_id,
            intent_id,
            attempt_id,
            _attempt_root,
            launch_context,
            launch_context_digest,
        ) = _validate_launch_capability(self._launch_capability)
        if capability_record.spec is not self.spec:
            raise SolverConfigurationError("supervisor launch binding is invalid")
        if (case_id, intent_id, attempt_id) != (
            self._case_id,
            self._intent_id,
            self._attempt_id,
        ):
            raise SolverConfigurationError("supervisor authority binding is invalid")
        if (
            launch_context != self._launch_context
            or launch_context_digest != self._launch_context_digest
        ):
            raise SolverConfigurationError("supervisor launch context binding is invalid")

    def _validated_retry_correlation(self) -> tuple[object, ...]:
        """Return the live case/intent correlation bound to this supervisor."""

        try:
            (
                capability_record,
                case_id,
                intent_sha256,
                attempt_id,
                attempt_root,
                _launch_context,
                _launch_context_digest,
            ) = _validate_launch_capability(self._launch_capability)
            if capability_record.spec is not self.spec:
                raise SolverConfigurationError("supervisor launch binding is invalid")
            if (case_id, intent_sha256, attempt_id) != (
                self._case_id,
                self._intent_id,
                self._attempt_id,
            ):
                raise SolverConfigurationError("supervisor authority binding is invalid")

            launch_snapshot = capability_record.intent_snapshot
            if type(launch_snapshot) is not IntentSnapshotAuthority:
                raise SolverConfigurationError("supervisor launch snapshot is invalid")
            attempt_workspace = capability_record.attempt_workspace
            case_workspace = object.__getattribute__(launch_snapshot, "_case_workspace")
            case_sha256 = launch_snapshot.case_sha256
            case_root = case_workspace.root
            if not attempt_root.is_relative_to(case_root):
                raise SolverConfigurationError("supervisor attempt is outside its case")

            # Revalidate after reading the snapshot projection so a late
            # capability or snapshot mutation cannot be returned as authority.
            (
                final_record,
                final_case_id,
                final_intent_sha256,
                final_attempt_id,
                final_attempt_root,
                _final_launch_context,
                _final_launch_context_digest,
            ) = _validate_launch_capability(self._launch_capability)
            final_snapshot = final_record.intent_snapshot
            if type(final_snapshot) is not IntentSnapshotAuthority:
                raise SolverConfigurationError("supervisor launch snapshot is invalid")
            final_case_workspace = object.__getattribute__(final_snapshot, "_case_workspace")
            final_case_sha256 = final_snapshot.case_sha256
            final_case_root = final_case_workspace.root
            if (
                final_record is not capability_record
                or final_record.spec is not self.spec
                or final_case_id != case_id
                or final_intent_sha256 != intent_sha256
                or final_attempt_id != attempt_id
                or final_attempt_root != attempt_root
                or final_snapshot is not launch_snapshot
                or final_case_workspace is not case_workspace
                or final_case_sha256 != case_sha256
                or final_case_root != case_root
            ):
                raise SolverConfigurationError(
                    "supervisor retry correlation changed during validation"
                )
        except SolverConfigurationError:
            raise
        except Exception as error:
            raise SolverConfigurationError("supervisor retry correlation is not live") from error

        return (
            self._launch_capability,
            capability_record.spec,
            attempt_workspace,
            launch_snapshot,
            case_workspace,
            case_root,
            case_id,
            case_sha256,
            intent_sha256,
            attempt_id,
            attempt_root,
        )

    def start(self) -> SolverSupervisor:
        """Prepare fresh outputs and launch the owned process."""

        with self._lock:
            if self._state is not SolverState.NOT_STARTED:
                raise RuntimeError(f"solver cannot start from state {self._state}")

            # Revalidate every authority immediately before the first mutation
            # of the attempt directory or process creation.
            self._revalidate_launch_binding()

            authority: ProcessAuthority | None = None
            process: subprocess.Popen[bytes] | None = None
            bound = False
            try:
                self.spec.prepare_outputs()
                self._revalidate_launch_binding()
                if os.path.lexists(os.fspath(self.process_record_path)):
                    raise SolverLaunchError(
                        f"process record already exists: {self.process_record_path}"
                    )
                if not self.spec.input_path.is_file():
                    raise FileNotFoundError(f"solver input does not exist: {self.spec.input_path}")
                authority = ProcessAuthority.create(
                    self.spec.attempt_root, self._launch_context_digest
                )
                environment = os.environ.copy()
                environment.update(self.spec.environment)
                # These markers let a child process discover its owned attempt
                # without granting it any additional filesystem authority.
                environment["FEBIO_CAE_HARNESS_OWNER"] = self._owner_token
                environment["FEBIO_CAE_HARNESS_ATTEMPT_ROOT"] = os.fspath(self.spec.attempt_root)
                outputs = self.spec.expected_outputs
                environment["FEBIO_CAE_HARNESS_LOG"] = os.fspath(outputs.log_path)
                environment["FEBIO_CAE_HARNESS_XPLT"] = os.fspath(outputs.xplt_path)
                environment.update(authority.child_environment())

                if os.name == "nt":
                    creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | (
                        _CREATE_SUSPENDED
                    )
                    child_handle = authority.child_handle()
                    if child_handle is None:
                        raise ProcessAuthorityError("process attestation handle is unavailable")
                    startup_info = subprocess.STARTUPINFO()
                    startup_info.lpAttributeList = {"handle_list": [child_handle]}
                    command = self.spec.command
                    cwd = os.fspath(self.spec.cwd)
                    self._revalidate_launch_binding()
                    process = subprocess.Popen(
                        command,
                        cwd=cwd,
                        env=environment,
                        shell=False,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=creation_flags,
                        close_fds=True,
                        startupinfo=startup_info,
                    )
                else:
                    command = self.spec.command
                    cwd = os.fspath(self.spec.cwd)
                    pass_fds = authority.child_pass_fds()
                    self._revalidate_launch_binding()
                    process = subprocess.Popen(
                        command,
                        cwd=cwd,
                        env=environment,
                        shell=False,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                        pass_fds=pass_fds,
                    )
                metadata = _process_metadata(process.pid)
                authority.bind(process.pid, metadata.creation_identity)
                bound = True
                bound_metadata = _process_metadata(process.pid)
                if (
                    not bound_metadata.alive
                    or bound_metadata.creation_identity != metadata.creation_identity
                ):
                    raise ProcessAuthorityError(
                        "process creation identity changed during assignment"
                    )
                metadata = bound_metadata
                if metadata.started_at is None:
                    raise ProcessAuthorityError("process start identity is unavailable")
                started_at = metadata.started_at
                self._revalidate_launch_binding()
                if os.name == "nt":
                    bound_record = self._make_process_record(
                        process.pid,
                        metadata,
                        started_at,
                        authority,
                        state="BOUND_SUSPENDED",
                    )
                    self._write_process_record(bound_record)
                    authority.resume(process.pid)
                    self._revalidate_launch_binding()
                running_record = self._make_process_record(
                    process.pid, metadata, started_at, authority, state=SolverState.RUNNING.value
                )
                self._write_process_record(running_record)
                self._process = process
                self._started_at = started_at
                self._process_authority = authority
                self._process_record = running_record
            except (OSError, OutputFreshnessError, ValueError) as error:
                process = locals().get("process")
                if process is not None:
                    try:
                        if authority is not None and self._process_authority is authority:
                            self._terminate_owned_process(process)
                        elif authority is not None and bound and os.name == "nt":
                            authority.terminate(process.pid, force=True)
                        else:
                            process.kill()
                        process.wait(timeout=2.0)
                    except (
                        OSError,
                        ProcessLookupError,
                        SolverOwnershipError,
                        subprocess.TimeoutExpired,
                    ):
                        with contextlib.suppress(OSError):
                            process.kill()
                if authority is not None:
                    if bound and os.name != "nt":
                        with contextlib.suppress(ProcessAuthorityError):
                            authority.drain()
                    authority.close()
                self._state = SolverState.FAILED
                if isinstance(error, SolverConfigurationError):
                    raise
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
        launch_capability: SolverLaunchCapability,
        *,
        fbs_adapter: FbsAdapterAuthority | None = None,
        requested_fields: Iterable[str] | None = None,
        log_validator: LogValidator | None = None,
    ) -> SolverSupervisor:
        """Rebuild a supervisor only after validating its owned record."""

        supervisor = cls(
            launch_capability,
            fbs_adapter=fbs_adapter,
            requested_fields=requested_fields,
            log_validator=log_validator,
        )
        record = supervisor._read_process_record()
        supervisor._revalidate_launch_binding()
        metadata, started_at, authority = supervisor._validate_process_record(record)
        try:
            supervisor._revalidate_launch_binding()
            supervisor._process_record = record
            pid = cast(int, record["pid"])
            supervisor._process = _ReconnectedProcess(
                pid, metadata.executable_path, metadata.creation_identity, authority
            )
            supervisor._process_authority = authority
            supervisor._started_at = started_at
            supervisor._state = SolverState.RUNNING
            return supervisor
        except BaseException:
            authority.close()
            raise

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
        self,
        pid: int,
        metadata: _ProcessMetadata,
        started_at: datetime,
        authority: ProcessAuthority,
        *,
        state: str,
    ) -> dict[str, object]:
        outputs = self.spec.expected_outputs
        claim = authority.claim
        if claim.get("context_digest") != self._launch_context_digest:
            raise ProcessAuthorityError("process authority context binding is invalid")
        if claim.get("root_pid") != pid:
            raise ProcessAuthorityError("process authority root PID binding is invalid")
        if claim.get("root_creation_identity") != metadata.creation_identity:
            raise ProcessAuthorityError(
                "process authority root creation identity binding is invalid"
            )
        return {
            "case_id": self._case_id,
            "intent_id": self._intent_id,
            "attempt_id": self._attempt_id,
            "state": state,
            "executable_path": str(self.spec.executable),
            "pid": pid,
            "process_creation_identity": metadata.creation_identity,
            "start_time": started_at.isoformat(),
            "owned_output_paths": {
                "log": str(outputs.log_path),
                "xplt": str(outputs.xplt_path),
            },
            "launch_context": self._launch_context,
            "launch_context_digest": self._launch_context_digest,
            "process_authority": claim,
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
    ) -> tuple[_ProcessMetadata, datetime, ProcessAuthority]:
        expected_ids = {
            "case_id": self._case_id,
            "intent_id": self._intent_id,
            "attempt_id": self._attempt_id,
        }
        if any(record.get(name) != expected for name, expected in expected_ids.items()):
            raise SolverOwnershipError("process record identity does not match")

        if record.get("state") != SolverState.RUNNING.value:
            raise SolverOwnershipError("process record is not a reconnectable RUNNING state")
        context = record.get("launch_context")
        if not isinstance(context, dict):
            raise SolverOwnershipError("process record launch context is invalid")
        context_digest = record.get("launch_context_digest")
        if not isinstance(context_digest, str):
            raise SolverOwnershipError("process record launch context digest is invalid")
        try:
            recomputed_digest = _json_digest(context)
        except SolverConfigurationError as error:
            raise SolverOwnershipError("process record launch context is invalid") from error
        if (
            context_digest != self._launch_context_digest
            or recomputed_digest != context_digest
            or context != self._launch_context
        ):
            raise SolverOwnershipError("process record launch context does not match")

        authority_claim = record.get("process_authority")
        if (
            not isinstance(authority_claim, dict)
            or authority_claim.get("context_digest") != self._launch_context_digest
            or authority_claim.get("root_pid") != record.get("pid")
            or authority_claim.get("root_creation_identity")
            != record.get("process_creation_identity")
        ):
            raise SolverOwnershipError("process record authority context does not match")

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
        if started_at.tzinfo is None or started_at.utcoffset() is None:
            raise SolverOwnershipError("process record start time is invalid")
        output_values = record.get("owned_output_paths")
        if not isinstance(output_values, dict):
            raise SolverOwnershipError("process record output paths are invalid")
        outputs = self.spec.expected_outputs
        self._validate_record_path(output_values.get("log"), outputs.log_path, "LOG path")
        self._validate_record_path(output_values.get("xplt"), outputs.xplt_path, "XPLT path")

        authority: ProcessAuthority | None = None
        try:
            authority = ProcessAuthority.from_claim(
                self.spec.attempt_root,
                authority_claim,
                self._launch_context_digest,
            )
            authority.verify(pid)
        except ProcessAuthorityError as error:
            if authority is not None:
                authority.close()
            raise SolverOwnershipError("recorded process authority is invalid") from error
        try:
            metadata = _process_metadata(pid)
        except OSError as error:
            authority.close()
            raise SolverOwnershipError("recorded process is not live") from error
        if not metadata.alive:
            authority.close()
            raise SolverOwnershipError("recorded process is not live")
        if _normalise_executable(metadata.executable_path) != _normalise_executable(
            executable_value
        ):
            authority.close()
            raise SolverOwnershipError("current process executable does not match")
        if metadata.creation_identity != identity:
            authority.close()
            raise SolverOwnershipError("current process creation identity does not match")
        if metadata.started_at is None or metadata.started_at != started_at:
            authority.close()
            raise SolverOwnershipError("current process start time does not match")
        if authority is None:  # pragma: no cover - defensive type/state guard
            raise SolverOwnershipError("recorded process authority is unavailable")
        return metadata, metadata.started_at, authority

    def _terminate_owned_process(
        self, process: subprocess.Popen[bytes] | _ReconnectedProcess
    ) -> None:
        """Terminate only the process group created by this supervisor."""

        if process.poll() is not None:
            return
        authority = self._process_authority
        if authority is None:
            raise SolverOwnershipError("process authority is unavailable")
        try:
            authority.terminate(process.pid)
        except ProcessAuthorityError as error:
            raise SolverOwnershipError("process authority could not be verified") from error

        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            try:
                authority.terminate(process.pid, force=True)
            except ProcessAuthorityError as error:
                raise SolverOwnershipError("process authority could not be verified") from error
            process.wait(timeout=2.0)

    def _release_process_authority(self) -> None:
        authority = self._process_authority
        if authority is None:
            return
        try:
            authority.drain()
            authority.close()
        except ProcessAuthorityError as error:
            raise SolverOwnershipError("owned process descendants could not be drained") from error
        self._process_authority = None

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
        with self._lock, contextlib.ExitStack() as cleanup:
            if self._result is not None:
                return self._result
            cleanup.callback(self._release_process_authority)
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
