"""Owned, headless FEBio process supervision."""

from __future__ import annotations

import math
import os
import signal
import subprocess
import threading
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime

from .fbs import FbsAdapterBoundary, FbsValidation, OfficialFbsAdapterBoundary
from .log import LogValidation, LogValidator, validate_log
from .types import (
    OutputFreshnessError,
    SolverClassification,
    SolverLaunchError,
    SolverLaunchSpec,
    SolverRunResult,
    SolverState,
)

__all__ = ["SolverSupervisor"]


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
        fbs_adapter: OfficialFbsAdapterBoundary | object | None = None,
        requested_fields: Iterable[str] | None = None,
        log_validator: LogValidator | None = None,
    ) -> None:
        self.spec = spec
        if fbs_adapter is None or isinstance(fbs_adapter, OfficialFbsAdapterBoundary):
            self._fbs_adapter = fbs_adapter
        else:
            self._fbs_adapter = FbsAdapterBoundary(fbs_adapter)
        self._requested_fields = (
            tuple(requested_fields) if requested_fields is not None else spec.requested_fields
        )
        self._log_validator = log_validator
        self._owner_token = uuid.uuid4().hex
        self._lock = threading.RLock()
        self._state = SolverState.NOT_STARTED
        self._process: subprocess.Popen[bytes] | None = None
        self._started_at: datetime | None = None
        self._result: SolverRunResult | None = None

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
            except (OSError, OutputFreshnessError, ValueError) as error:
                self._state = SolverState.FAILED
                raise SolverLaunchError(f"unable to launch solver: {error}") from error

            self._process = process
            self._started_at = datetime.now(UTC)
            self._state = SolverState.RUNNING
            return self

    launch = start

    def poll(self) -> int | None:
        """Return the process return code, if it has exited."""

        with self._lock:
            process = self._process
        return process.poll() if process is not None else None

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

    def _terminate_owned_process(self, process: subprocess.Popen[bytes]) -> None:
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
                process.terminate()
        else:
            try:
                if not self._kill_process_group(process.pid, "SIGTERM"):
                    process.terminate()
            except (OSError, ProcessLookupError):
                process.terminate()

        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                process.kill()
            else:
                try:
                    if not self._kill_process_group(process.pid, "SIGKILL"):
                        process.kill()
                except (OSError, ProcessLookupError):
                    process.kill()
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
                        fbs_validation = self._fbs_adapter.validate(
                            self.spec.expected_outputs.xplt_path,
                            self._requested_fields,
                        )
                    except Exception as error:
                        fbs_validation = FbsValidation.invalid(
                            self.spec.expected_outputs.xplt_path,
                            self._requested_fields,
                            str(error),
                        )
                    if not fbs_validation.valid:
                        classification = SolverClassification.FBS_INVALID
                    elif not fbs_validation.official:
                        classification = SolverClassification.FBS_UNVERIFIED
                    else:
                        classification = SolverClassification.SUCCESS

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
