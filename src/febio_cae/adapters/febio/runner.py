"""Owned subprocess execution with explicit drain and lifecycle outcomes."""

from __future__ import annotations

import hashlib
import os
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass
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


@dataclass
class _Managed:
    process: subprocess.Popen[bytes]
    attempt_root: Path
    started_at: float
    stdout: BinaryIO
    stderr: BinaryIO
    timed_out: bool = False


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
        self.ownership.claim(owner)
        attempt_root = self.root / owner.run_id / owner.attempt_id
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
        executable = Path(bundle.argv[0])
        try:
            digest = hashlib.sha256(executable.read_bytes()).hexdigest()
        except (OSError, ValueError):
            digest = bundle.tool.executable_digest
        identity = ProcessIdentity(
            executable=str(executable),
            executable_digest=digest,
            argv=bundle.argv,
            cwd=str(attempt_root),
            thread_count=bundle.thread_count,
            start_marker=f"{process.pid}:{uuid.uuid4().hex}",
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
        if attempt.state is not RunState.RUNNING:
            return PollResult(attempt, ())
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
        if attempt.state is RunState.RUNNING:
            self._terminate(managed.process)
            current = attempt.transition_to(RunState.DRAINING)
        else:
            current = attempt
        self._wait_for_drain(managed, force=True)
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
        if attempt.state is RunState.RUNNING:
            return ReconcileResult(
                self._observe_exit(attempt, managed).attempt, ("reconciled owned process",)
            )
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
    def _spawn(
        bundle: ExecutionBundle, attempt_root: Path
    ) -> tuple[subprocess.Popen[bytes], BinaryIO, BinaryIO]:
        stdout = (attempt_root / "logs" / "solver.stdout.log").open("wb")
        stderr = (attempt_root / "logs" / "solver.stderr.log").open("wb")
        kwargs: dict[str, Any] = {
            "args": list(bundle.argv),
            "cwd": str(attempt_root),
            "stdin": subprocess.DEVNULL,
            "stdout": stdout,
            "stderr": stderr,
            "shell": False,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
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
        returncode = managed.process.poll()
        if returncode is None:
            return PollResult(attempt, ())
        draining = attempt.transition_to(RunState.DRAINING)
        if not self._wait_for_drain(managed, force=False):
            return PollResult(draining, ("root exited; owned descendants are still draining",))
        self._managed.pop(attempt.attempt_id, None)
        self._close_streams(managed)
        if managed.timed_out:
            return PollResult(
                draining.transition_to(RunState.INTERRUPTED),
                ("attempt exceeded its elapsed budget",),
            )
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
        managed.stdout.close()
        managed.stderr.close()

    def _wait_for_drain(self, managed: _Managed, *, force: bool) -> bool:
        deadline = time.monotonic() + (0.15 if not force else 2.0)
        while time.monotonic() < deadline:
            if self._descendant_pids(managed.process.pid):
                if force:
                    self._terminate_descendants(managed.process.pid)
                time.sleep(0.02)
                continue
            return True
        if force:
            self._terminate_descendants(managed.process.pid)
            return not self._descendant_pids(managed.process.pid)
        return not self._descendant_pids(managed.process.pid)

    @staticmethod
    def _terminate(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                check=False,
            )
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
    def _terminate_descendants(pid: int) -> None:
        if os.name == "nt":
            subprocess.run(
                ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                check=False,
            )

    @staticmethod
    def _descendant_pids(pid: int) -> tuple[int, ...]:
        if os.name == "nt":
            command = (
                "$root=" + str(pid) + "; $all=Get-CimInstance Win32_Process; $seen=@($root); "
                "$changed=$true; while($changed){$changed=$false; foreach($p in $all){"
                "if($seen -contains [int]$p.ParentProcessId -and -not($seen -contains [int]$p.ProcessId)){"
                "$seen += [int]$p.ProcessId; $changed=$true}}}; $seen | Select-Object -Skip 1"
            )
            try:
                completed = subprocess.run(
                    ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                    check=False,
                    text=True,
                    timeout=1.0,
                )
                return tuple(int(line) for line in completed.stdout.split() if line.isdigit())
            except (OSError, subprocess.SubprocessError, ValueError):
                return ()
        proc = Path("/proc")
        if not proc.is_dir():
            return ()
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
