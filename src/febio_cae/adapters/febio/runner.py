"""Owned subprocess execution with explicit drain and lifecycle outcomes."""

from __future__ import annotations

import hashlib
import os
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

from febio_cae.domain import (
    AttemptRecord,
    Budget,
    CancelResult,
    ExecutionBundle,
    ExecutionSetting,
    OwnershipPort,
    PollResult,
    PortError,
    PortErrorCategory,
    ProcessIdentity,
    ReconcileResult,
    RunState,
    TrustedOwnerContext,
)

from ._windows_job import WindowsJobProcess


@dataclass
class _Managed:
    process: subprocess.Popen[bytes] | WindowsJobProcess
    attempt_root: Path
    started_at: float
    stdout: BinaryIO
    stderr: BinaryIO
    timed_out: bool = False
    cancel_requested: bool = False
    descendants: set[int] = field(default_factory=set)
    enumeration_unknown: bool = False
    returncode: int | None = None


class RunnerAdapter:
    """Run only the exact owner-issued argv in an isolated attempt directory."""

    def __init__(
        self, *, ownership: OwnershipPort, root: Path | str, bundle_store: object | None = None
    ) -> None:
        self.ownership = ownership
        self.root = Path(root).resolve()
        self.bundle_store = bundle_store
        self.root.mkdir(parents=True, exist_ok=True)
        self._managed: dict[str, _Managed] = {}

    def start(
        self, bundle: ExecutionBundle, owner: TrustedOwnerContext, budget: Budget
    ) -> AttemptRecord:
        if not isinstance(bundle, ExecutionBundle) or not isinstance(owner, TrustedOwnerContext):
            raise PortError(
                PortErrorCategory.INVALID_INPUT, "bundle and owner are required common records"
            )
        if bundle.case_id != owner.case_id:
            raise PortError(PortErrorCategory.CONFLICT, "owner case does not match bundle")
        if not isinstance(budget, Budget):
            raise PortError(PortErrorCategory.INVALID_INPUT, "budget must be a Budget")
        self._validate_owner_components(owner)
        executable = Path(bundle.argv[0]).resolve()
        try:
            executable_digest = hashlib.sha256(executable.read_bytes()).hexdigest()
        except (OSError, ValueError) as error:
            raise PortError(
                PortErrorCategory.ENVIRONMENT, "registered solver executable is unavailable"
            ) from error
        if executable_digest != bundle.tool.executable_digest:
            raise PortError(
                PortErrorCategory.INTEGRITY,
                "solver executable digest does not match the registered profile",
            )
        self.ownership.claim(owner)
        attempt_root = (self.root / owner.run_id / owner.attempt_id).resolve()
        try:
            attempt_root.relative_to(self.root)
        except ValueError as error:
            raise PortError(
                PortErrorCategory.INTEGRITY, "attempt path escapes runner root"
            ) from error
        try:
            attempt_root.mkdir(parents=True, exist_ok=False)
        except FileExistsError as error:
            raise PortError(
                PortErrorCategory.CONFLICT, "attempt directory already exists"
            ) from error
        (attempt_root / "output").mkdir()
        (attempt_root / "logs").mkdir()
        self._stage_bundle(bundle, attempt_root)
        preparing = AttemptRecord(
            attempt_id=owner.attempt_id,
            run_id=owner.run_id,
            case_id=bundle.case_id,
            revision_id=bundle.revision_id,
            owner_generation=owner.owner_generation,
            bundle_digest=bundle.bundle_digest,
            state=RunState.CREATED,
            process=None,
            settings=tuple(bundle.settings)
            + (ExecutionSetting("attempt_root", str(attempt_root)),),
        ).transition_to(RunState.PREPARING)
        try:
            process, stdout, stderr = self._spawn(bundle, attempt_root)
        except OSError as error:
            raise PortError(
                PortErrorCategory.ENVIRONMENT, f"solver process could not start: {error}"
            ) from error
        identity = ProcessIdentity(
            executable=str(executable),
            executable_digest=executable_digest,
            argv=bundle.argv,
            cwd=str(attempt_root),
            thread_count=bundle.thread_count,
            start_marker=(
                f"{process.pid}:{process.creation_time}:{uuid.uuid4().hex}"
                if isinstance(process, WindowsJobProcess)
                else f"{process.pid}:{uuid.uuid4().hex}"
            ),
        )
        attempt = AttemptRecord(
            attempt_id=preparing.attempt_id,
            run_id=preparing.run_id,
            case_id=preparing.case_id,
            revision_id=preparing.revision_id,
            owner_generation=preparing.owner_generation,
            bundle_digest=preparing.bundle_digest,
            state=RunState.RUNNING,
            process=identity,
            settings=tuple(preparing.settings)
            + (ExecutionSetting("max_elapsed_seconds", budget.max_elapsed.to_si().value),),
        )
        self._managed[attempt.attempt_id] = _Managed(
            process, attempt_root, time.monotonic(), stdout, stderr
        )
        return attempt

    def poll(self, attempt: AttemptRecord, owner: TrustedOwnerContext) -> PollResult:
        self._validate(attempt, owner)
        managed = self._managed.get(attempt.attempt_id)
        if managed is None:
            if attempt.state is RunState.RUNNING:
                return PollResult(
                    attempt.transition_to(RunState.INTERRUPTED),
                    ("owned process is not recoverable",),
                )
            return PollResult(attempt, ())
        if attempt.state not in {RunState.RUNNING, RunState.DRAINING}:
            return PollResult(attempt, ())
        self._refresh_descendants(managed)
        budget_seconds = self._budget_seconds(attempt)
        if budget_seconds is not None and time.monotonic() - managed.started_at > budget_seconds:
            managed.timed_out = True
            self._terminate(managed.process)
        return self._observe_exit(attempt, managed)

    def cancel(self, attempt: AttemptRecord, owner: TrustedOwnerContext) -> CancelResult:
        self._validate(attempt, owner)
        managed = self._managed.get(attempt.attempt_id)
        if managed is None:
            if attempt.state is RunState.RUNNING:
                return CancelResult(
                    attempt.transition_to(RunState.INTERRUPTED),
                    ("owned process is not recoverable",),
                )
            return CancelResult(attempt, ())
        if attempt.state not in {RunState.RUNNING, RunState.DRAINING}:
            return CancelResult(attempt, ())
        managed.cancel_requested = True
        self._terminate(managed.process)
        if attempt.state is RunState.RUNNING:
            current = attempt.transition_to(RunState.DRAINING)
        else:
            current = attempt
        drained = self._wait_for_drain(managed, force=True)
        if not drained:
            return CancelResult(current, ("owned descendants are not yet drained",))
        if current.state is RunState.DRAINING:
            current = current.transition_to(RunState.CANCELLED)
        self._close_streams(managed)
        self._managed.pop(attempt.attempt_id, None)
        return CancelResult(current, ("owned process group terminated",))

    def reconcile(self, attempt: AttemptRecord, owner: TrustedOwnerContext) -> ReconcileResult:
        self._validate(attempt, owner)
        managed = self._managed.get(attempt.attempt_id)
        if managed is None:
            if attempt.state is RunState.RUNNING:
                return ReconcileResult(
                    attempt.transition_to(RunState.INTERRUPTED),
                    ("process identity cannot be reattached",),
                )
            return ReconcileResult(attempt, ())
        if attempt.state in {RunState.RUNNING, RunState.DRAINING}:
            result = self.poll(attempt, owner)
            return ReconcileResult(result.attempt, result.diagnostics)
        return ReconcileResult(attempt, ())

    def _validate(self, attempt: AttemptRecord, owner: TrustedOwnerContext) -> None:
        if not isinstance(attempt, AttemptRecord) or not isinstance(owner, TrustedOwnerContext):
            raise PortError(
                PortErrorCategory.INVALID_INPUT, "attempt and owner are required common records"
            )
        try:
            self.ownership.validate(owner, attempt)
        except Exception as error:
            raise PortError(
                PortErrorCategory.CONFLICT, "attempt is not owned by this owner generation"
            ) from error

    @staticmethod
    def _validate_owner_components(owner: TrustedOwnerContext) -> None:
        for field_name in ("run_id", "attempt_id"):
            value = getattr(owner, field_name)
            if value in {".", ".."} or any(separator in value for separator in ("/", "\\", ":")):
                raise PortError(
                    PortErrorCategory.INVALID_INPUT, f"{field_name} must be one path component"
                )

    @staticmethod
    def _spawn(
        bundle: ExecutionBundle, attempt_root: Path
    ) -> tuple[subprocess.Popen[bytes] | WindowsJobProcess, BinaryIO, BinaryIO]:
        stdout = (attempt_root / "logs" / "solver.stdout.log").open("wb")
        try:
            stderr = (attempt_root / "logs" / "solver.stderr.log").open("wb")
        except OSError:
            stdout.close()
            raise
        if os.name == "nt":
            try:
                return (
                    WindowsJobProcess(tuple(bundle.argv), attempt_root, stdout, stderr),
                    stdout,
                    stderr,
                )
            except BaseException:
                stdout.close()
                stderr.close()
                raise
        kwargs: dict[str, Any] = {
            "args": list(bundle.argv),
            "cwd": str(attempt_root),
            "stdin": subprocess.DEVNULL,
            "stdout": stdout,
            "stderr": stderr,
            "shell": False,
        }
        kwargs["start_new_session"] = True
        try:
            return subprocess.Popen(**kwargs), stdout, stderr
        except (OSError, TypeError, ValueError):
            stdout.close()
            stderr.close()
            raise

    def _stage_bundle(self, bundle: ExecutionBundle, attempt_root: Path) -> None:
        if self.bundle_store is None:
            return
        resolver = getattr(self.bundle_store, "resolve", None)
        if not callable(resolver):
            raise PortError(
                PortErrorCategory.ENVIRONMENT, "bundle store cannot resolve compiler bytes"
            )
        for entry in bundle.files:
            content = resolver(bundle, entry.logical_path)
            if not isinstance(content, bytes):
                raise PortError(
                    PortErrorCategory.INTEGRITY, "bundle store returned non-byte content"
                )
            target = attempt_root / Path(*entry.logical_path.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)

    def _observe_exit(self, attempt: AttemptRecord, managed: _Managed) -> PollResult:
        if attempt.state is RunState.RUNNING:
            returncode = managed.process.poll()
            if returncode is None:
                return PollResult(attempt, ())
            managed.returncode = returncode
            draining = attempt.transition_to(RunState.DRAINING)
        elif attempt.state is RunState.DRAINING:
            returncode = managed.returncode
            if returncode is None:
                returncode = managed.process.poll()
                if returncode is None:
                    return PollResult(attempt, ())
                managed.returncode = returncode
            draining = attempt
        else:
            return PollResult(attempt, ())
        if not self._wait_for_drain(managed, force=False):
            return PollResult(draining, ("root exited; owned descendants are still draining",))
        self._managed.pop(attempt.attempt_id, None)
        self._close_streams(managed)
        if managed.timed_out:
            return PollResult(
                draining.transition_to(RunState.FAILED),
                ("attempt exceeded its elapsed budget",),
            )
        if managed.cancel_requested:
            return PollResult(draining.transition_to(RunState.CANCELLED), ("owned tree cancelled",))
        if returncode == 0:
            return PollResult(
                draining.transition_to(RunState.VALIDATING),
                ("root exited and owned descendants drained",),
            )
        return PollResult(
            draining.transition_to(RunState.FAILED), (f"solver exited with code {returncode}",)
        )

    @staticmethod
    def _close_streams(managed: _Managed) -> None:
        if isinstance(managed.process, WindowsJobProcess):
            managed.process.close()
        managed.stdout.close()
        managed.stderr.close()

    def _wait_for_drain(self, managed: _Managed, *, force: bool) -> bool:
        if isinstance(managed.process, WindowsJobProcess):
            if force:
                managed.process.terminate_tree()
            deadline = time.monotonic() + (2.0 if force or managed.timed_out else 0.15)
            while True:
                try:
                    if (
                        managed.process.active_processes() == 0
                        and managed.process.poll() is not None
                    ):
                        return True
                except OSError:
                    return False
                if time.monotonic() >= deadline:
                    return False
                time.sleep(0.02)
        deadline = time.monotonic() + (0.15 if not force else 2.0)
        while time.monotonic() < deadline:
            self._refresh_descendants(managed)
            if managed.enumeration_unknown:
                if force:
                    self._terminate_descendant_pids(managed.descendants)
                time.sleep(0.02)
                continue
            live = {pid for pid in managed.descendants if self._pid_alive(pid)}
            managed.descendants = live
            if not live:
                return True
            if force:
                self._terminate_descendant_pids(live)
            time.sleep(0.02)
        self._refresh_descendants(managed)
        if managed.enumeration_unknown:
            return False
        live = {pid for pid in managed.descendants if self._pid_alive(pid)}
        managed.descendants = live
        if force and live:
            self._terminate_descendant_pids(live)
            deadline = time.monotonic() + 0.25
            while live and time.monotonic() < deadline:
                time.sleep(0.02)
                live = {pid for pid in live if self._pid_alive(pid)}
            managed.descendants = live
        return not live

    @staticmethod
    def _terminate(process: subprocess.Popen[bytes] | WindowsJobProcess) -> None:
        if isinstance(process, WindowsJobProcess):
            process.terminate_tree()
            return
        if process.poll() is not None:
            return
        if os.name == "nt":
            raise OSError("Windows termination requires retained Job ownership")
        else:
            killpg = getattr(os, "killpg", None)
            if callable(killpg):
                try:
                    killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            process.kill()

    @staticmethod
    def _terminate_descendant_pids(pids: set[int] | tuple[int, ...]) -> None:
        if os.name == "nt":
            raise OSError("PID-only Windows termination is forbidden")
        else:
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        if os.name == "nt":
            raise OSError("PID-only Windows observation is forbidden")
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    def _refresh_descendants(self, managed: _Managed) -> None:
        if isinstance(managed.process, WindowsJobProcess):
            return  # Retained Job membership, not a process enumeration, is authority.
        observed = self._descendant_pids(managed.process.pid)
        if observed is None:
            managed.enumeration_unknown = True
            return
        managed.enumeration_unknown = False
        managed.descendants.update(observed)
        managed.descendants = {pid for pid in managed.descendants if self._pid_alive(pid)}

    @staticmethod
    def _descendant_pids(pid: int) -> tuple[int, ...] | None:
        if os.name == "nt":
            raise OSError("Windows descendants require retained Job ownership")
        proc = Path("/proc")
        if not proc.is_dir():
            return None
        parents: dict[int, list[int]] = {}
        for entry in proc.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                fields = (entry / "stat").read_text().split()
                parents.setdefault(int(fields[3]), []).append(int(entry.name))
            except (OSError, ValueError, IndexError):
                continue
        found: list[int] = []
        queue = [pid]
        while queue:
            parent = queue.pop()
            for child in parents.get(parent, []):
                if child not in found:
                    found.append(child)
                    queue.append(child)
        return tuple(found)

    @staticmethod
    def _budget_seconds(attempt: AttemptRecord) -> float | None:
        for setting in attempt.settings:
            if setting.name == "max_elapsed_seconds":
                return float(setting.value)
        return None


__all__ = ["RunnerAdapter"]
