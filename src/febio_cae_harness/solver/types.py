"""Typed contracts for the headless FEBio solver boundary.

The solver package owns process lifecycle and output expectations only.  It does
not infer physical conditions from a model and it does not provide a fake FBS
implementation.  FBS reads enter through the injected adapter boundary in
``fbs.py``.
"""

from __future__ import annotations

import math
import os
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .fbs import FbsValidation
    from .log import LogValidation

__all__ = [
    "FailureClassification",
    "OutputExpectation",
    "OutputFreshnessError",
    "ProcessState",
    "RunState",
    "SolverClassification",
    "SolverConfigurationError",
    "SolverLaunchError",
    "SolverLaunchSpec",
    "SolverOwnershipError",
    "SolverRunResult",
    "SolverState",
]


class SolverState(StrEnum):
    """Lifecycle state of one owned solver process."""

    NOT_STARTED = "NOT_STARTED"
    RUNNING = "RUNNING"
    NORMAL_EXIT = "NORMAL_EXIT"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"

    # Readable aliases for clients that use the shorter lifecycle vocabulary.
    SUCCEEDED = "NORMAL_EXIT"
    TIMEOUT = "TIMED_OUT"


class SolverClassification(StrEnum):
    """Evidence-based classification of a solver attempt."""

    SUCCESS = "SUCCESS"
    INIT_ONLY = "INIT_ONLY"
    MISSING_OUTPUT = "MISSING_OUTPUT"
    FATAL = "FATAL"
    NEGATIVE_JACOBIAN = "NEGATIVE_JACOBIAN"
    INVALID_LOG = "INVALID_LOG"
    FBS_UNVERIFIED = "FBS_UNVERIFIED"
    FBS_INVALID = "FBS_INVALID"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    NONZERO_EXIT = "NONZERO_EXIT"

    # Aliases keep the canonical values stable while accommodating common
    # spelling used by callers and reports.
    INITIALIZATION_ONLY = "INIT_ONLY"
    MISSING_OUTPUTS = "MISSING_OUTPUT"
    NEGATIVE_JACOBIAN_ERROR = "NEGATIVE_JACOBIAN"


FailureClassification = SolverClassification
ProcessState = SolverState
RunState = SolverState


class SolverConfigurationError(ValueError):
    """Raised when a launch specification is unsafe or internally invalid."""


class OutputFreshnessError(SolverConfigurationError):
    """Raised when an attempt output already exists before launch."""


class SolverLaunchError(RuntimeError):
    """Raised when an owned solver process cannot be started."""


class SolverOwnershipError(RuntimeError):
    """Raised when an operation would escape the supervisor-owned process."""


_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _absolute_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return Path(os.path.abspath(os.fspath(path)))


def _reject_alias(path: Path, label: str) -> None:
    """Reject symlink/reparse ancestors without resolving outside the root."""

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
            raise SolverConfigurationError(f"cannot inspect {label}: {path}") from error
        if stat.S_ISLNK(metadata.st_mode) or bool(
            getattr(metadata, "st_file_attributes", 0) & _REPARSE_POINT
        ):
            raise SolverConfigurationError(f"{label} cannot contain a reparse-point alias")


def _normalise_fields(fields: Sequence[str] | tuple[str, ...]) -> tuple[str, ...]:
    normalised = tuple(fields)
    if any(not isinstance(field, str) or not field.strip() for field in normalised):
        raise SolverConfigurationError("requested_fields must contain non-empty strings")
    if len(set(normalised)) != len(normalised):
        raise SolverConfigurationError("requested_fields must not contain duplicates")
    return normalised


@dataclass(frozen=True, slots=True)
class OutputExpectation:
    """The two result paths that one attempt must create freshly."""

    attempt_root: Path
    log_path: Path
    xplt_path: Path

    def __post_init__(self) -> None:
        root = _absolute_path(self.attempt_root)
        log_path = _absolute_path(self.log_path)
        xplt_path = _absolute_path(self.xplt_path)
        _reject_alias(root, "attempt root")
        _reject_alias(log_path, "LOG path")
        _reject_alias(xplt_path, "XPLT path")
        if log_path == xplt_path:
            raise SolverConfigurationError("LOG and XPLT paths must be different")
        for path, label in ((log_path, "LOG path"), (xplt_path, "XPLT path")):
            if path == root or not path.is_relative_to(root):
                raise SolverConfigurationError(f"{label} must be inside the attempt root")
        object.__setattr__(self, "attempt_root", root)
        object.__setattr__(self, "log_path", log_path)
        object.__setattr__(self, "xplt_path", xplt_path)

    @property
    def log_file(self) -> Path:
        """Alias used by evidence/report callers."""

        return self.log_path

    @property
    def xplt_file(self) -> Path:
        """Alias used by evidence/report callers."""

        return self.xplt_path

    def prepare(self) -> OutputExpectation:
        """Create owned parent directories and reject stale output names."""

        _reject_alias(self.attempt_root, "attempt root")
        if self.attempt_root.exists() and not self.attempt_root.is_dir():
            raise SolverConfigurationError("attempt root must be a directory")
        self.attempt_root.mkdir(parents=True, exist_ok=True)
        _reject_alias(self.attempt_root, "attempt root")
        for path, label in ((self.log_path, "LOG path"), (self.xplt_path, "XPLT path")):
            _reject_alias(path.parent, f"{label} parent")
            path.parent.mkdir(parents=True, exist_ok=True)
            _reject_alias(path.parent, f"{label} parent")
            if path.exists() or path.is_symlink():
                raise OutputFreshnessError(f"{label} already exists: {path}")
        return self

    def present(self) -> bool:
        """Return whether both expected outputs are regular files."""

        return self.log_path.is_file() and self.xplt_path.is_file()


@dataclass(frozen=True, slots=True)
class SolverLaunchSpec:
    """Immutable, explicit command and output contract for one attempt."""

    executable: Path
    input_path: Path
    attempt_root: Path
    log_path: Path | None = None
    xplt_path: Path | None = None
    arguments: tuple[str, ...] = ()
    working_directory: Path | None = None
    environment: Mapping[str, str] = field(default_factory=dict)
    timeout_seconds: float | None = None
    expected_steps: int | None = None
    expected_final_time: float | None = None
    requested_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        executable = _absolute_path(self.executable)
        input_path = _absolute_path(self.input_path)
        attempt_root = _absolute_path(self.attempt_root)
        if self.log_path is None:
            log_path = attempt_root / f"{input_path.stem}.log"
        else:
            supplied_log = Path(self.log_path)
            log_path = supplied_log if supplied_log.is_absolute() else attempt_root / supplied_log
        if self.xplt_path is None:
            xplt_path = attempt_root / f"{input_path.stem}.xplt"
        else:
            supplied_xplt = Path(self.xplt_path)
            xplt_path = (
                supplied_xplt if supplied_xplt.is_absolute() else attempt_root / supplied_xplt
            )

        output_expectation = OutputExpectation(attempt_root, log_path, xplt_path)
        if self.working_directory is None:
            working_directory = attempt_root
        else:
            working_directory = _absolute_path(self.working_directory)
        _reject_alias(working_directory, "working directory")
        if working_directory != attempt_root and not working_directory.is_relative_to(attempt_root):
            raise SolverConfigurationError("working directory must be inside the attempt root")

        arguments = tuple(self.arguments)
        if any(not isinstance(argument, str) or not argument for argument in arguments):
            raise SolverConfigurationError("arguments must contain non-empty strings")

        if self.timeout_seconds is not None:
            if isinstance(self.timeout_seconds, bool) or not isinstance(
                self.timeout_seconds, (int, float)
            ):
                raise SolverConfigurationError("timeout_seconds must be a positive finite number")
            if self.timeout_seconds <= 0 or not math.isfinite(float(self.timeout_seconds)):
                raise SolverConfigurationError("timeout_seconds must be a positive finite number")

        if self.expected_steps is not None:
            if isinstance(self.expected_steps, bool) or not isinstance(self.expected_steps, int):
                raise SolverConfigurationError("expected_steps must be a non-negative integer")
            if self.expected_steps < 0:
                raise SolverConfigurationError("expected_steps must be a non-negative integer")

        if self.expected_final_time is not None:
            if isinstance(self.expected_final_time, bool) or not isinstance(
                self.expected_final_time, (int, float)
            ):
                raise SolverConfigurationError("expected_final_time must be finite")
            if not math.isfinite(float(self.expected_final_time)):
                raise SolverConfigurationError("expected_final_time must be finite")

        fields = _normalise_fields(self.requested_fields)
        env: dict[str, str] = {}
        for key, value in self.environment.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise SolverConfigurationError("environment keys and values must be strings")
            env[key] = value

        object.__setattr__(self, "executable", executable)
        object.__setattr__(self, "input_path", input_path)
        object.__setattr__(self, "attempt_root", attempt_root)
        object.__setattr__(self, "log_path", output_expectation.log_path)
        object.__setattr__(self, "xplt_path", output_expectation.xplt_path)
        object.__setattr__(self, "arguments", arguments)
        object.__setattr__(self, "working_directory", working_directory)
        object.__setattr__(self, "environment", MappingProxyType(env))
        object.__setattr__(self, "requested_fields", fields)

    @property
    def command(self) -> tuple[str, ...]:
        """Return the shell-free argv owned by the supervisor."""

        if self.arguments:
            return (os.fspath(self.executable), *self.arguments)
        return (os.fspath(self.executable), "-i", os.fspath(self.input_path))

    @property
    def argv(self) -> tuple[str, ...]:
        """Alias for :attr:`command`."""

        return self.command

    @property
    def args(self) -> tuple[str, ...]:
        """Alias exposing arguments without the executable."""

        return self.arguments

    @property
    def expected_outputs(self) -> OutputExpectation:
        if self.log_path is None or self.xplt_path is None:
            raise SolverConfigurationError("launch output paths were not initialized")
        return OutputExpectation(self.attempt_root, self.log_path, self.xplt_path)

    @property
    def output_expectation(self) -> OutputExpectation:
        return self.expected_outputs

    @property
    def cwd(self) -> Path:
        if self.working_directory is None:
            raise SolverConfigurationError("working directory was not initialized")
        return self.working_directory

    def prepare_outputs(self) -> OutputExpectation:
        return self.expected_outputs.prepare()


@dataclass(frozen=True, slots=True)
class SolverRunResult:
    """Immutable projection of process, LOG, output, and FBS evidence."""

    state: SolverState
    classification: SolverClassification
    return_code: int | None
    pid: int | None
    command: tuple[str, ...]
    log_path: Path
    xplt_path: Path
    started_at: datetime | None = None
    finished_at: datetime | None = None
    log_validation: LogValidation | None = None
    fbs_validation: FbsValidation | None = None
    error: str | None = None

    @property
    def success(self) -> bool:
        """True only after normal exit and all configured evidence gates pass."""

        return (
            self.state is SolverState.NORMAL_EXIT
            and self.classification is SolverClassification.SUCCESS
        )

    @property
    def succeeded(self) -> bool:
        return self.success

    @property
    def normal_exit(self) -> bool:
        return self.state is SolverState.NORMAL_EXIT

    @property
    def timed_out(self) -> bool:
        return self.state is SolverState.TIMED_OUT

    @property
    def cancelled(self) -> bool:
        return self.state is SolverState.CANCELLED

    @property
    def failure_classification(self) -> SolverClassification:
        return self.classification

    @property
    def outputs_present(self) -> bool:
        return self.log_path.is_file() and self.xplt_path.is_file()


def as_mapping(value: object) -> Mapping[str, Any]:
    """Internal helper shared by the FBS boundary without exposing mutability."""

    if not isinstance(value, Mapping):
        raise TypeError("expected a mapping")
    return value
