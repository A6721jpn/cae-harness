"""Typed contracts for the headless FEBio solver boundary.

The solver package owns process lifecycle and output expectations only.  It does
not infer physical conditions from a model and it does not provide a fake FBS
implementation.  FBS reads enter through the injected adapter boundary in
``fbs.py``.
"""

from __future__ import annotations

import hashlib
import math
import os
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, SupportsIndex

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
    "SolverLaunchCapability",
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


_LAUNCH_CAPABILITY_FACTORY = object()


class SolverLaunchCapability:
    """Opaque, authority-bound launch contract for one solver attempt.

    ``SolverLaunchSpec`` remains a useful value object for describing command
    details in lower-level tests and diagnostics, but it is deliberately not a
    supervisor input.  A capability can only be made by the private headless
    boundary after it has validated the live workspace, intent, runtime, and
    input authorities.  The registry and immutable snapshots let the
    supervisor reject forged, copied, or mutated capability state before it
    touches the attempt filesystem.
    """

    __slots__ = (
        "_spec",
        "_attempt_workspace",
        "_intent_snapshot",
        "_runtime_diagnostic",
    )
    _spec: SolverLaunchSpec
    _attempt_workspace: object
    _intent_snapshot: object
    _runtime_diagnostic: object

    def __new__(
        cls,
        *args: object,
        _factory: object | None = None,
        **kwargs: object,
    ) -> SolverLaunchCapability:
        del args, kwargs
        if cls is not SolverLaunchCapability or _factory is not _LAUNCH_CAPABILITY_FACTORY:
            raise TypeError("solver launch capabilities are issued by the headless boundary")
        return super().__new__(cls)

    def __init__(
        self,
        *args: object,
        _factory: object | None = None,
        **kwargs: object,
    ) -> None:
        del args, kwargs
        if _factory is not _LAUNCH_CAPABILITY_FACTORY:
            raise TypeError("solver launch capabilities are issued by the headless boundary")

    def __init_subclass__(cls, **kwargs: object) -> None:
        del kwargs
        raise TypeError("solver launch capabilities cannot be subclassed")

    def __setattr__(self, name: str, value: object) -> None:
        del name, value
        raise AttributeError("solver launch capabilities are immutable")

    def __delattr__(self, name: str) -> None:
        del name
        raise AttributeError("solver launch capabilities are immutable")

    def __copy__(self) -> SolverLaunchCapability:
        raise TypeError("solver launch capabilities cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> SolverLaunchCapability:
        del memo
        raise TypeError("solver launch capabilities cannot be copied")

    def __reduce__(self) -> str | tuple[Any, ...]:
        raise TypeError("solver launch capabilities cannot be serialized")

    def __reduce_ex__(self, protocol: SupportsIndex) -> str | tuple[Any, ...]:
        del protocol
        raise TypeError("solver launch capabilities cannot be serialized")

    def __getstate__(self) -> object:
        raise TypeError("solver launch capability state is not transferable")

    @classmethod
    def _issue(
        cls,
        attempt_workspace: object,
        intent_snapshot: object,
        runtime_diagnostic: object,
        spec: SolverLaunchSpec,
    ) -> SolverLaunchCapability:
        """Create a capability for the private headless boundary only."""

        if cls is not SolverLaunchCapability:
            raise TypeError("solver launch capabilities cannot be subclassed")
        if type(spec) is not SolverLaunchSpec:
            raise TypeError("solver launch capability requires an exact launch spec")
        capability = cls(_factory=_LAUNCH_CAPABILITY_FACTORY)
        object.__setattr__(capability, "_spec", spec)
        object.__setattr__(capability, "_attempt_workspace", attempt_workspace)
        object.__setattr__(capability, "_intent_snapshot", intent_snapshot)
        object.__setattr__(capability, "_runtime_diagnostic", runtime_diagnostic)
        _LAUNCH_CAPABILITY_REGISTRY[id(capability)] = _LaunchCapabilityRecord(
            capability=capability,
            spec=spec,
            spec_snapshot=_spec_snapshot(spec),
            attempt_workspace=attempt_workspace,
            intent_snapshot=intent_snapshot,
            runtime_diagnostic=runtime_diagnostic,
            attempt_root=spec.attempt_root,
            input_path=spec.input_path,
            input_snapshot=_input_snapshot(spec.input_path),
        )
        return capability

    @property
    def spec(self) -> SolverLaunchSpec:
        return _require_issued_launch_capability(self).spec


@dataclass(frozen=True, slots=True)
class _LaunchCapabilityRecord:
    capability: SolverLaunchCapability
    spec: SolverLaunchSpec
    spec_snapshot: tuple[object, ...]
    attempt_workspace: object
    intent_snapshot: object
    runtime_diagnostic: object
    attempt_root: Path
    input_path: Path
    input_snapshot: tuple[object, ...]


_LAUNCH_CAPABILITY_REGISTRY: dict[int, _LaunchCapabilityRecord] = {}


def _spec_snapshot(spec: SolverLaunchSpec) -> tuple[object, ...]:
    return (
        spec.executable,
        spec.input_path,
        spec.attempt_root,
        spec.log_path,
        spec.xplt_path,
        spec.arguments,
        spec.working_directory,
        tuple(spec.environment.items()),
        spec.timeout_seconds,
        spec.expected_steps,
        spec.expected_final_time,
        spec.requested_fields,
    )


def _input_snapshot(path: Path) -> tuple[object, ...]:
    """Capture a regular input's identity and bytes without changing it."""

    _reject_alias(path, "solver input")
    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise SolverConfigurationError("solver input must be a regular, unlinked file")
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if (
                opened.st_dev,
                opened.st_ino,
                opened.st_nlink,
                opened.st_size,
            ) != (
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_nlink,
                metadata.st_size,
            ):
                raise SolverConfigurationError("solver input changed while opening")
            digest = hashlib.sha256()
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
            finished = os.fstat(stream.fileno())
    except SolverConfigurationError:
        raise
    except OSError as error:
        raise SolverConfigurationError(f"unable to inspect solver input: {path}") from error
    if (
        finished.st_dev,
        finished.st_ino,
        finished.st_nlink,
        finished.st_size,
    ) != (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_nlink,
        metadata.st_size,
    ):
        raise SolverConfigurationError("solver input changed while reading")
    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(metadata.st_nlink),
        int(metadata.st_size),
        int(metadata.st_mtime_ns),
        digest.hexdigest(),
    )


def _require_issued_launch_capability(value: object) -> _LaunchCapabilityRecord:
    if type(value) is not SolverLaunchCapability:
        raise SolverConfigurationError(
            "solver supervisor requires an exact issued launch capability"
        )
    record = _LAUNCH_CAPABILITY_REGISTRY.get(id(value))
    if record is None or record.capability is not value:
        raise SolverConfigurationError("solver launch capability was not issued by the boundary")
    capability = value
    try:
        if (
            capability._spec is not record.spec
            or capability._attempt_workspace is not record.attempt_workspace
            or capability._intent_snapshot is not record.intent_snapshot
            or capability._runtime_diagnostic is not record.runtime_diagnostic
        ):
            raise SolverConfigurationError("solver launch capability binding was changed")
    except AttributeError as error:
        raise SolverConfigurationError("solver launch capability state is invalid") from error
    if _spec_snapshot(record.spec) != record.spec_snapshot:
        raise SolverConfigurationError("solver launch specification was modified")
    return record


def _validate_launch_capability(
    value: object,
) -> tuple[_LaunchCapabilityRecord, str, str, str, Path]:
    """Validate every live authority bound into a capability."""

    record = _require_issued_launch_capability(value)
    spec = record.spec
    try:
        from ..evidence import IntentSnapshotAuthority
        from ..workspace import AttemptWorkspace
        from .runtime import validate_runtime_diagnostic

        attempt = record.attempt_workspace
        intent = record.intent_snapshot
        runtime = record.runtime_diagnostic
        if type(attempt) is not AttemptWorkspace:
            raise SolverConfigurationError("launch capability attempt is not an exact workspace")
        if type(intent) is not IntentSnapshotAuthority:
            raise SolverConfigurationError("launch capability intent is not an exact authority")
        root = Path(os.fspath(attempt))
        if root != record.attempt_root or root != spec.attempt_root:
            raise SolverConfigurationError("launch capability attempt root binding changed")
        case_id = attempt.case_id
        attempt_id = attempt.attempt_id
        intent_case_id = intent.case_id
        intent_id = intent.intent_sha256
        intent_case_root = object.__getattribute__(intent, "_case_workspace").root
        if case_id != intent_case_id or intent_case_root != root.parents[2]:
            raise SolverConfigurationError("launch capability authorities refer to different cases")
        if not isinstance(case_id, str) or not case_id.strip():
            raise SolverConfigurationError("launch capability case identity is invalid")
        if not isinstance(attempt_id, str) or not attempt_id.strip():
            raise SolverConfigurationError("launch capability attempt identity is invalid")
        if not isinstance(intent_id, str) or not intent_id.strip():
            raise SolverConfigurationError("launch capability intent identity is invalid")
        validated_runtime = validate_runtime_diagnostic(runtime)
        input_path = spec.input_path
        if input_path != record.input_path:
            raise SolverConfigurationError("launch capability input binding changed")
        if input_path == root or not input_path.is_relative_to(root):
            raise SolverConfigurationError("solver input must be inside the attempt root")
        root_metadata = root.lstat()
        if not stat.S_ISDIR(root_metadata.st_mode):
            raise SolverConfigurationError("attempt root is not a directory")
        current_input = _input_snapshot(input_path)
        if current_input != record.input_snapshot:
            raise SolverConfigurationError("solver input authority is stale")
        expected_log = root / f"{input_path.stem}.log"
        expected_xplt = root / f"{input_path.stem}.xplt"
        if (
            spec.executable != validated_runtime.path
            or spec.arguments != ()
            or spec.environment
            or spec.working_directory != root
            or spec.log_path != expected_log
            or spec.xplt_path != expected_xplt
        ):
            raise SolverConfigurationError("launch capability command binding is invalid")
        if _spec_snapshot(spec) != record.spec_snapshot:
            raise SolverConfigurationError("solver launch specification was modified")
    except SolverConfigurationError:
        raise
    except Exception as error:
        raise SolverConfigurationError(
            "solver launch capability authorities are not live"
        ) from error
    return record, case_id, intent_id, attempt_id, root


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
