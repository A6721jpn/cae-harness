"""Shell-free headless FEBio execution diagnostic."""

from __future__ import annotations

import os
import stat
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from ..evidence import EvidenceStore, IntentSnapshotAuthority
from ..workspace import AttemptWorkspace, _identity_stamp
from .execution import ExecutionAuthority, reopen_execution_authority
from .runtime import (
    FebioRuntimeDiagnostic,
    validate_runtime_diagnostic,
)
from .supervisor import SolverSupervisor
from .types import (
    SolverClassification,
    SolverConfigurationError,
    SolverLaunchCapability,
    SolverLaunchSpec,
    SolverRunResult,
    SolverState,
)

__all__ = [
    "HeadlessConfigurationError",
    "HeadlessReconnectSession",
    "HeadlessRunDiagnostic",
    "HeadlessRunSession",
    "headless_exit_code",
    "recover_headless_febio",
    "reconnect_headless_febio",
    "run_headless_febio",
    "run_headless_febio_session",
]

_REPARSE_POINT: Final[int] = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class HeadlessConfigurationError(SolverConfigurationError):
    pass


def _reject_alias(path: Path, label: str) -> None:
    current = path
    ancestors: list[Path] = []
    while True:
        ancestors.append(current)
        if current == current.parent:
            break
        current = current.parent
    for ancestor in reversed(ancestors):
        try:
            if not os.path.lexists(os.fspath(ancestor)):
                continue
            metadata = ancestor.lstat()
        except OSError as error:
            raise HeadlessConfigurationError(f"cannot inspect {label}: {path}") from error
        if stat.S_ISLNK(metadata.st_mode) or bool(
            getattr(metadata, "st_file_attributes", 0) & _REPARSE_POINT
        ):
            raise HeadlessConfigurationError(f"{label} cannot contain a reparse-point alias")


def _validate_input(input_value: str | Path, attempt_root: Path) -> Path:
    if not isinstance(input_value, (str, Path)):
        raise HeadlessConfigurationError("input path must be a string or Path")
    supplied = Path(input_value).expanduser()
    if not supplied.is_absolute():
        raise HeadlessConfigurationError("input path must be absolute")
    input_path = Path(os.path.abspath(os.fspath(supplied)))
    _reject_alias(attempt_root, "attempt root")
    try:
        root_metadata = attempt_root.lstat()
    except OSError as error:
        raise HeadlessConfigurationError(f"attempt root does not exist: {attempt_root}") from error
    if not stat.S_ISDIR(root_metadata.st_mode):
        raise HeadlessConfigurationError(f"attempt root is not a directory: {attempt_root}")

    _reject_alias(input_path, "input path")
    try:
        metadata = input_path.lstat()
    except OSError as error:
        raise HeadlessConfigurationError(f"input file does not exist: {input_path}") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise HeadlessConfigurationError(f"input file is not a regular file: {input_path}")
    if metadata.st_nlink != 1:
        raise HeadlessConfigurationError(f"input file must not be a hardlink: {input_path}")
    if input_path == attempt_root or not input_path.is_relative_to(attempt_root):
        raise HeadlessConfigurationError(
            f"input file must be inside the attempt root: {input_path}"
        )
    root_real = Path(os.path.realpath(os.fspath(attempt_root)))
    input_real = Path(os.path.realpath(os.fspath(input_path)))
    if not input_real.is_relative_to(root_real):
        raise HeadlessConfigurationError(
            f"input file must be physically inside the attempt root: {input_path}"
        )
    return input_path


@dataclass(frozen=True, slots=True)
class HeadlessRunDiagnostic:
    """Immutable projection of runtime and terminal solver evidence."""

    runtime_identity: FebioRuntimeDiagnostic
    state: SolverState
    classification: SolverClassification
    return_code: int | None
    pid: int | None
    log_path: Path
    xplt_path: Path
    official_fbs: bool = False
    success: bool = False

    @property
    def runtime(self) -> FebioRuntimeDiagnostic:
        return self.runtime_identity

    def to_dict(self) -> dict[str, object]:
        return {
            "classification": self.classification.value,
            "log": os.fspath(self.log_path),
            "official_fbs": False,
            "pid": self.pid,
            "return_code": self.return_code,
            "runtime": self.runtime_identity.to_dict(),
            "state": self.state.value,
            "success": self.success,
            "xplt": os.fspath(self.xplt_path),
        }


@dataclass(frozen=True, slots=True)
class HeadlessRunSession:
    """Exact supervisor result retained alongside its public diagnostic."""

    supervisor: SolverSupervisor
    result: SolverRunResult
    diagnostic: HeadlessRunDiagnostic


@dataclass(frozen=True, slots=True)
class HeadlessReconnectSession:
    """Authenticated recovery state for one recorded execution attempt."""

    attempt: AttemptWorkspace
    execution: ExecutionAuthority
    supervisor: SolverSupervisor


def run_headless_febio(
    attempt_workspace: AttemptWorkspace,
    intent_snapshot: IntentSnapshotAuthority,
    runtime_diagnostic: FebioRuntimeDiagnostic,
    input_path: str | Path,
    *,
    expected_steps: int | None = None,
    expected_final_time: float | None = None,
    timeout_seconds: float | None = None,
    requested_fields: Sequence[str] = (),
) -> HeadlessRunDiagnostic:
    """Run one attempt using only live, manager-issued context capabilities."""

    return run_headless_febio_session(
        attempt_workspace,
        intent_snapshot,
        runtime_diagnostic,
        input_path,
        expected_steps=expected_steps,
        expected_final_time=expected_final_time,
        timeout_seconds=timeout_seconds,
        requested_fields=requested_fields,
    ).diagnostic


def run_headless_febio_session(
    attempt_workspace: AttemptWorkspace,
    intent_snapshot: IntentSnapshotAuthority,
    runtime_diagnostic: FebioRuntimeDiagnostic,
    input_path: str | Path,
    *,
    expected_steps: int | None = None,
    expected_final_time: float | None = None,
    timeout_seconds: float | None = None,
    requested_fields: Sequence[str] = (),
) -> HeadlessRunSession:
    """Run one attempt while retaining exact retry-policy authorities."""

    _, _, _, root = _validate_context(
        attempt_workspace,
        intent_snapshot,
    )
    runtime = validate_runtime_diagnostic(runtime_diagnostic)
    input_file = _validate_input(input_path, root)
    capability = _issue_launch_capability(
        attempt_workspace,
        intent_snapshot,
        runtime,
        input_file,
        expected_steps=expected_steps,
        expected_final_time=expected_final_time,
        timeout_seconds=timeout_seconds,
        requested_fields=requested_fields,
    )
    supervisor = SolverSupervisor(capability)
    result = supervisor.run()
    diagnostic = HeadlessRunDiagnostic(
        runtime_identity=runtime,
        state=result.state,
        classification=result.classification,
        return_code=result.return_code,
        pid=result.pid,
        log_path=result.log_path,
        xplt_path=result.xplt_path,
        success=result.success is True,
    )
    return HeadlessRunSession(
        supervisor=supervisor,
        result=result,
        diagnostic=diagnostic,
    )


def reconnect_headless_febio(
    attempt_workspace: AttemptWorkspace,
    intent_snapshot: IntentSnapshotAuthority,
    runtime_diagnostic: FebioRuntimeDiagnostic,
    input_path: str | Path,
    *,
    expected_steps: int | None = None,
    expected_final_time: float | None = None,
    timeout_seconds: float | None = None,
    requested_fields: Sequence[str] = (),
) -> SolverSupervisor:
    """Reconnect to one attempt using the same authority-bound launch contract."""

    _, _, _, root = _validate_context(
        attempt_workspace,
        intent_snapshot,
    )
    runtime = validate_runtime_diagnostic(runtime_diagnostic)
    input_file = _validate_input(input_path, root)
    capability = _issue_launch_capability(
        attempt_workspace,
        intent_snapshot,
        runtime,
        input_file,
        expected_steps=expected_steps,
        expected_final_time=expected_final_time,
        timeout_seconds=timeout_seconds,
        requested_fields=requested_fields,
    )
    return SolverSupervisor.reconnect(capability)


def recover_headless_febio(
    store: EvidenceStore,
    intent_snapshot: IntentSnapshotAuthority,
    runtime_diagnostic: FebioRuntimeDiagnostic,
    attempt_id: str,
) -> HeadlessReconnectSession:
    """Rebuild recorded launch context, then authenticate its live process.

    The executable is never learned from untrusted crash state.  Callers must
    provide a current probe-issued runtime, while input and launch expectations
    come only from the exact durable execution record.
    """

    if type(store) is not EvidenceStore:
        raise HeadlessConfigurationError("recovery requires an exact EvidenceStore")
    if type(intent_snapshot) is not IntentSnapshotAuthority:
        raise HeadlessConfigurationError(
            "recovery requires an exact IntentSnapshotAuthority capability"
        )
    try:
        store._validate_intent_snapshot(intent_snapshot)
        manifest = store.manifest
        attempts = manifest.get("attempts")
        if not isinstance(attempts, list) or not any(
            isinstance(item, dict) and item.get("attempt_id") == attempt_id for item in attempts
        ):
            raise HeadlessConfigurationError("recovery attempt is not recorded")
        case = store.case_workspace
        attempt_root = case.temporary_root / "attempts" / attempt_id
        attempt_stamp = _identity_stamp(attempt_root, "recovery attempt root")
        attempt = AttemptWorkspace._from_manager(
            case,
            attempt_id,
            attempt_root,
            expected_root_stamp=attempt_stamp,
        )
        execution = reopen_execution_authority(
            attempt,
            intent_snapshot,
            runtime_diagnostic,
        )
        supervisor = reconnect_headless_febio(
            attempt,
            intent_snapshot,
            runtime_diagnostic,
            execution.input_path,
            expected_steps=execution.expected_steps,
            expected_final_time=execution.expected_final_time,
            timeout_seconds=execution.timeout_seconds,
            requested_fields=execution.requested_fields,
        )
    except HeadlessConfigurationError:
        raise
    except (AttributeError, TypeError, ValueError) as error:
        raise HeadlessConfigurationError("recorded recovery context is invalid") from error
    return HeadlessReconnectSession(attempt, execution, supervisor)


def _validate_context(
    attempt_workspace: AttemptWorkspace,
    intent_snapshot: IntentSnapshotAuthority,
) -> tuple[str, str, str, Path]:
    if type(attempt_workspace) is not AttemptWorkspace:
        raise HeadlessConfigurationError(
            "headless launch requires an exact AttemptWorkspace capability"
        )
    if type(intent_snapshot) is not IntentSnapshotAuthority:
        raise HeadlessConfigurationError(
            "headless launch requires an exact IntentSnapshotAuthority capability"
        )
    try:
        root = Path(os.fspath(attempt_workspace))
        case_id = attempt_workspace.case_id
        attempt_id = attempt_workspace.attempt_id
        intent_case_id = intent_snapshot.case_id
        intent_id = intent_snapshot.intent_sha256
        intent_case_root = object.__getattribute__(intent_snapshot, "_case_workspace").root
        os.fspath(attempt_workspace)
    except Exception as error:
        raise HeadlessConfigurationError("headless launch requires live authorities") from error
    if case_id != intent_case_id or intent_case_root != root.parents[2]:
        raise HeadlessConfigurationError("attempt and intent authorities must refer to one case")
    return case_id, intent_id, attempt_id, root


def _issue_launch_capability(
    attempt_workspace: AttemptWorkspace,
    intent_snapshot: IntentSnapshotAuthority,
    runtime: FebioRuntimeDiagnostic,
    input_path: Path,
    *,
    expected_steps: int | None,
    expected_final_time: float | None,
    timeout_seconds: float | None,
    requested_fields: Sequence[str] = (),
) -> SolverLaunchCapability:
    """Build the sole supervisor input after the public boundary checks."""

    # The values are deliberately supplied only by ``_validate_context`` and
    # ``_validate_input`` in this module.  Keep this issuer private: callers
    # must not be able to select executable, roots, or context labels.
    attempt_root = Path(os.fspath(attempt_workspace))
    spec = SolverLaunchSpec(
        executable=runtime.path,
        input_path=input_path,
        attempt_root=attempt_root,
        timeout_seconds=timeout_seconds,
        expected_steps=expected_steps,
        expected_final_time=expected_final_time,
        requested_fields=tuple(requested_fields),
    )
    return SolverLaunchCapability._issue(
        # The capability itself retains the exact authority objects.  The
        # arguments are recovered from the validated public call above rather
        # than from caller-selected paths or labels.
        attempt_workspace,
        intent_snapshot,
        runtime,
        spec,
    )


def headless_exit_code(diagnostic: HeadlessRunDiagnostic) -> int:
    """Map one terminal diagnostic to the documented CLI exit code."""

    if diagnostic.success:
        return 0
    if (
        diagnostic.classification is SolverClassification.FBS_UNVERIFIED
        and diagnostic.state is SolverState.NORMAL_EXIT
        and diagnostic.return_code == 0
    ):
        return 5
    return 4
