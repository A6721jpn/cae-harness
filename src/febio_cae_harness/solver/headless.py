"""Shell-free headless FEBio execution diagnostic."""

from __future__ import annotations

import os
import stat
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .runtime import FebioRuntimeDiagnostic, probe_febio
from .supervisor import SolverSupervisor
from .types import (
    SolverClassification,
    SolverConfigurationError,
    SolverLaunchSpec,
    SolverState,
)

__all__ = [
    "HeadlessRunDiagnostic",
    "headless_exit_code",
    "run_headless_febio",
]

_DEFAULT_PROBE_TIMEOUT_SECONDS: Final[float] = 5.0
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


def run_headless_febio(
    executable: str | Path,
    input_path: str | Path,
    attempt_root: str | Path,
    *,
    case_id: str,
    intent_id: str,
    attempt_id: str,
    expected_steps: int | None = None,
    expected_final_time: float | None = None,
    timeout_seconds: float | None = None,
    arguments: Sequence[str] = (),
    probe_arguments: Sequence[str] = (),
    probe_timeout_seconds: float = _DEFAULT_PROBE_TIMEOUT_SECONDS,
) -> HeadlessRunDiagnostic:
    """Probe, validate, and run one already-placed FEB without a shell."""

    if probe_arguments:
        runtime = probe_febio(
            executable,
            timeout_seconds=probe_timeout_seconds,
            runner_arguments=probe_arguments,
        )
    elif probe_timeout_seconds != _DEFAULT_PROBE_TIMEOUT_SECONDS:
        runtime = probe_febio(executable, timeout_seconds=probe_timeout_seconds)
    else:
        runtime = probe_febio(executable)
    root = Path(os.path.abspath(os.fspath(Path(attempt_root).expanduser())))
    input_file = _validate_input(input_path, root)
    spec = SolverLaunchSpec(
        executable=runtime.path,
        input_path=input_file,
        attempt_root=root,
        arguments=tuple(arguments),
        timeout_seconds=timeout_seconds,
        expected_steps=expected_steps,
        expected_final_time=expected_final_time,
    )
    result = SolverSupervisor(
        spec,
        case_id=case_id,
        intent_id=intent_id,
        attempt_id=attempt_id,
    ).run()
    return HeadlessRunDiagnostic(
        runtime_identity=runtime,
        state=result.state,
        classification=result.classification,
        return_code=result.return_code,
        pid=result.pid,
        log_path=result.log_path,
        xplt_path=result.xplt_path,
        success=result.success is True,
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
