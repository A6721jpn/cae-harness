"""Shared lifecycle, task-status, and structured service vocabulary."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum, IntEnum

from .canonical import canonical_bytes

SCHEMA_VERSION = "1"


class LifecycleValidationError(ValueError):
    """Raised for an illegal lifecycle transition or malformed diagnostic."""


class RunState(str, Enum):
    CREATED = "CREATED"
    PREPARING = "PREPARING"
    RUNNING = "RUNNING"
    DRAINING = "DRAINING"
    VALIDATING = "VALIDATING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"


class CasePreparationState(str, Enum):
    DRAFT = "DRAFT"
    NEEDS_INPUT = "NEEDS_INPUT"
    READY = "READY"


class TaskStatus(str, Enum):
    READY = "READY"
    NEEDS_INPUT = "NEEDS_INPUT"
    RUNNING = "RUNNING"
    NEEDS_PREVIEW = "NEEDS_PREVIEW"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"


class PreviewStatus(str, Enum):
    REQUESTED = "REQUESTED"
    LAUNCHED = "LAUNCHED"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"


class ServiceExitCode(IntEnum):
    SUCCESS = 0
    INVALID_INPUT = 2
    NEEDS_PHYSICAL_INPUT = 3
    UNSUPPORTED_ENVIRONMENT = 4
    EXECUTION_FAILED = 5
    RESULT_QUALITY_FAILED = 6
    CANCELLED_OR_INTERRUPTED = 7
    CONFLICT = 8


class ServiceErrorCategory(str, Enum):
    INVALID_INPUT = "invalid_input"
    NEEDS_PHYSICAL_INPUT = "needs_physical_input"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    ENVIRONMENT = "environment"
    EXECUTION = "execution"
    INTEGRITY = "integrity"
    QUALITY = "quality"
    CANCELLED = "cancelled"
    CONFLICT = "conflict"


_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.CREATED: frozenset({RunState.PREPARING}),
    RunState.PREPARING: frozenset({RunState.RUNNING, RunState.FAILED}),
    RunState.RUNNING: frozenset({RunState.DRAINING, RunState.INTERRUPTED}),
    RunState.DRAINING: frozenset({RunState.VALIDATING, RunState.FAILED, RunState.CANCELLED}),
    RunState.VALIDATING: frozenset({RunState.SUCCEEDED, RunState.FAILED, RunState.INTERRUPTED}),
    RunState.SUCCEEDED: frozenset(),
    RunState.FAILED: frozenset(),
    RunState.CANCELLED: frozenset(),
    RunState.INTERRUPTED: frozenset(),
}


def transition_allowed(current: RunState, target: RunState) -> bool:
    if not isinstance(current, RunState) or not isinstance(target, RunState):
        return False
    return target in _TRANSITIONS[current]


def require_transition(current: RunState, target: RunState) -> None:
    if not transition_allowed(current, target):
        raise LifecycleValidationError(f"illegal run transition {current.value} -> {target.value}")


@dataclass(frozen=True, slots=True)
class ServiceDiagnostic:
    code: ServiceErrorCategory
    message: str
    field: str | None = None
    retryable: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.code, ServiceErrorCategory):
            raise LifecycleValidationError("code must be a ServiceErrorCategory")
        if (
            not isinstance(self.message, str)
            or not self.message
            or self.message != self.message.strip()
        ):
            raise LifecycleValidationError("message must be non-empty text")
        if self.field is not None and (
            not isinstance(self.field, str) or not self.field or self.field != self.field.strip()
        ):
            raise LifecycleValidationError("field must be non-empty text or None")
        if not isinstance(self.retryable, bool):
            raise LifecycleValidationError("retryable must be a bool")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "code": self.code.value,
            "message": self.message,
            "field": self.field,
            "retryable": self.retryable,
        }


@dataclass(frozen=True, slots=True)
class OperationStatus:
    """Typed CLI/service response shape; it is not an authority claim."""

    status: str
    case_id: str | None = None
    revision_id: str | None = None
    run_id: str | None = None
    diagnostics: Sequence[ServiceDiagnostic] = ()
    next_actions: Sequence[str] = ()
    run_status: RunState | None = None
    quality_status: str | None = None
    preview_status: PreviewStatus | None = None
    task_status: TaskStatus | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.status, str)
            or not self.status
            or self.status != self.status.strip()
        ):
            raise LifecycleValidationError("status must be non-empty text")
        for field_name in ("case_id", "revision_id", "run_id"):
            value = getattr(self, field_name)
            if value is not None and (
                not isinstance(value, str) or not value or value != value.strip()
            ):
                raise LifecycleValidationError(f"{field_name} must be non-empty text or None")
        diagnostics = tuple(self.diagnostics)
        if any(not isinstance(item, ServiceDiagnostic) for item in diagnostics):
            raise LifecycleValidationError("diagnostics contains an invalid value")
        actions = tuple(self.next_actions)
        if any(not isinstance(item, str) or not item or item != item.strip() for item in actions):
            raise LifecycleValidationError("next_actions contains invalid text")
        if self.run_status is not None and not isinstance(self.run_status, RunState):
            raise LifecycleValidationError("run_status must be a RunState or None")
        if self.preview_status is not None and not isinstance(self.preview_status, PreviewStatus):
            raise LifecycleValidationError("preview_status must be a PreviewStatus or None")
        if self.task_status is not None and not isinstance(self.task_status, TaskStatus):
            raise LifecycleValidationError("task_status must be a TaskStatus or None")
        object.__setattr__(self, "diagnostics", diagnostics)
        object.__setattr__(self, "next_actions", actions)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": self.status,
            "case_id": self.case_id,
            "revision_id": self.revision_id,
            "run_id": self.run_id,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "next_actions": list(self.next_actions),
            "run_status": None if self.run_status is None else self.run_status.value,
            "quality_status": self.quality_status,
            "preview_status": None if self.preview_status is None else self.preview_status.value,
            "task_status": None if self.task_status is None else self.task_status.value,
        }

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


__all__ = [
    "SCHEMA_VERSION",
    "CasePreparationState",
    "LifecycleValidationError",
    "OperationStatus",
    "PreviewStatus",
    "RunState",
    "ServiceDiagnostic",
    "ServiceErrorCategory",
    "ServiceExitCode",
    "TaskStatus",
    "require_transition",
    "transition_allowed",
]
