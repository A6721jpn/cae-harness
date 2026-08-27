"""Pure completed-FEB workflow state machine."""

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import StrEnum
from math import isfinite
from types import MappingProxyType
from typing import cast

from ..contracts import IntentContract, IntentState
from ..evidence import IntentSnapshotAuthority

__all__ = ["WorkflowIdentity", "WorkflowPhase", "WorkflowState", "transition"]

type _FrozenValue = (
    None | bool | int | float | str | tuple[_FrozenValue, ...] | Mapping[str, _FrozenValue]
)


class WorkflowPhase(StrEnum):
    CREATED = "CREATED"
    INSPECTED = "INSPECTED"
    DERIVED = "DERIVED"
    PREFLIGHTED = "PREFLIGHTED"
    SOLVING = "SOLVING"
    VALIDATING = "VALIDATING"
    REPORTED = "REPORTED"
    FAILED = "FAILED"
    ASK_AND_BLOCK = "ASK_AND_BLOCK"


@dataclass(frozen=True, slots=True)
class WorkflowIdentity:
    case_id: str
    intent_id: str
    attempt_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", _required_text(self.case_id, "case_id"))
        object.__setattr__(self, "intent_id", _required_text(self.intent_id, "intent_id"))
        object.__setattr__(self, "attempt_id", _required_text(self.attempt_id, "attempt_id"))

    def to_dict(self) -> dict[str, str]:
        return {
            "case_id": self.case_id,
            "intent_id": self.intent_id,
            "attempt_id": self.attempt_id,
        }


_BLOCKED_BINDINGS: dict[
    int,
    tuple[
        object,
        object,
        WorkflowIdentity,
        tuple[str, str, str],
        WorkflowPhase,
        tuple[str, ...],
        _FrozenValue,
    ],
] = {}


@dataclass(frozen=True, slots=True)
class WorkflowState:
    identity: WorkflowIdentity
    state: WorkflowPhase = WorkflowPhase.CREATED
    evidence_ids: tuple[str, ...] = ()
    reason: object | None = None
    _intent_snapshot: object | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.identity, WorkflowIdentity):
            raise TypeError("identity must be a WorkflowIdentity")
        try:
            normalized_state = WorkflowPhase(self.state)
        except (TypeError, ValueError) as error:
            raise ValueError("state must be a canonical WorkflowPhase") from error
        object.__setattr__(self, "state", normalized_state)
        object.__setattr__(self, "evidence_ids", _normalise_evidence_ids(self.evidence_ids))
        if normalized_state is WorkflowPhase.ASK_AND_BLOCK:
            snapshot, intent = _require_live_snapshot(self.identity, self.reason)
            projection = _reason_projection(intent)
            object.__setattr__(self, "_intent_snapshot", snapshot)
            object.__setattr__(self, "reason", projection)
            _BLOCKED_BINDINGS[id(self)] = (
                self,
                snapshot,
                self.identity,
                (self.identity.case_id, self.identity.intent_id, self.identity.attempt_id),
                normalized_state,
                self.evidence_ids,
                projection,
            )
        elif self.reason is not None:
            object.__setattr__(self, "reason", _freeze(self.reason))

    def to_dict(self) -> dict[str, object]:
        _validate_state_binding(self)
        return {
            "identity": self.identity.to_dict(),
            "state": self.state.value,
            "evidence_ids": list(self.evidence_ids),
            "reason": _thaw(cast(_FrozenValue, self.reason)) if self.reason is not None else None,
        }


def transition(
    current: WorkflowState,
    target: WorkflowPhase | str,
    *,
    identity: WorkflowIdentity,
    evidence_ids: Iterable[str] = (),
    reason: object | None = None,
) -> WorkflowState:
    if not isinstance(current, WorkflowState):
        raise TypeError("current must be a WorkflowState")
    if not isinstance(identity, WorkflowIdentity):
        raise TypeError("identity must be a WorkflowIdentity")
    if current.identity != identity:
        raise ValueError("transition identity does not match current state")
    _validate_state_binding(current)
    try:
        next_state = WorkflowPhase(target)
    except (TypeError, ValueError) as error:
        raise ValueError("target must be a canonical WorkflowPhase") from error
    if current.state in _TERMINAL_STATES:
        raise ValueError("terminal workflow states cannot transition")
    if next_state not in {
        _NEXT_STATE[current.state],
        WorkflowPhase.FAILED,
        WorkflowPhase.ASK_AND_BLOCK,
    }:
        raise ValueError(f"illegal workflow transition: {current.state} -> {next_state}")

    additions = _normalise_evidence_ids(evidence_ids)
    if set(current.evidence_ids).intersection(additions):
        raise ValueError("evidence IDs must be unique across the workflow")
    return WorkflowState(
        identity=current.identity,
        state=next_state,
        evidence_ids=current.evidence_ids + additions,
        reason=reason,
    )


_NEXT_STATE: dict[WorkflowPhase, WorkflowPhase] = {
    WorkflowPhase.CREATED: WorkflowPhase.INSPECTED,
    WorkflowPhase.INSPECTED: WorkflowPhase.DERIVED,
    WorkflowPhase.DERIVED: WorkflowPhase.PREFLIGHTED,
    WorkflowPhase.PREFLIGHTED: WorkflowPhase.SOLVING,
    WorkflowPhase.SOLVING: WorkflowPhase.VALIDATING,
    WorkflowPhase.VALIDATING: WorkflowPhase.REPORTED,
}
_TERMINAL_STATES = frozenset(
    {WorkflowPhase.REPORTED, WorkflowPhase.FAILED, WorkflowPhase.ASK_AND_BLOCK}
)


def _required_text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _normalise_evidence_ids(values: Iterable[str]) -> tuple[str, ...]:
    if isinstance(values, str):
        raise TypeError("evidence_ids must be an iterable of strings")
    result: list[str] = []
    seen: set[str] = set()
    try:
        iterator = iter(values)
    except TypeError as error:
        raise TypeError("evidence_ids must be an iterable of strings") from error
    for value in iterator:
        if not isinstance(value, str):
            raise TypeError("evidence IDs must be strings")
        normalized = value.strip()
        if not normalized:
            raise ValueError("evidence IDs must be non-empty strings")
        if normalized in seen:
            raise ValueError("evidence IDs must be unique")
        seen.add(normalized)
        result.append(normalized)
    return tuple(result)


def _require_live_snapshot(
    identity: WorkflowIdentity,
    value: object | None,
) -> tuple[IntentSnapshotAuthority, IntentContract]:
    if type(value) is not IntentSnapshotAuthority:
        raise ValueError("ASK_AND_BLOCK requires an EvidenceStore-issued intent snapshot")
    snapshot = value
    if snapshot.case_id != identity.case_id:
        raise ValueError("ASK_AND_BLOCK snapshot case does not match workflow identity")
    intent = snapshot.intent
    if type(intent) is not IntentContract:
        raise ValueError("ASK_AND_BLOCK snapshot must resolve to an IntentContract")
    if intent.state is not IntentState.ASK_AND_BLOCK:
        raise ValueError("ASK_AND_BLOCK snapshot intent must be in ASK_AND_BLOCK state")
    if not _nonempty(intent.unresolved):
        raise ValueError("ASK_AND_BLOCK snapshot intent must contain unresolved conditions")
    if not _nonempty(intent.condition_sources):
        raise ValueError("ASK_AND_BLOCK snapshot intent must contain condition sources")
    return snapshot, intent


def _nonempty(value: object) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    return value is not None and bool(value)


def _reason_projection(intent: IntentContract) -> _FrozenValue:
    return _freeze(
        {
            "condition_sources": intent.condition_sources,
            "unresolved": intent.unresolved,
        }
    )


def _validate_state_binding(state: WorkflowState) -> None:
    binding = _BLOCKED_BINDINGS.get(id(state))
    if state.state is not WorkflowPhase.ASK_AND_BLOCK:
        if binding is not None:
            raise ValueError("workflow state binding was changed")
        return
    if binding is None or binding[0] is not state:
        raise ValueError("ASK_AND_BLOCK state is not bound to a live intent snapshot")
    _, snapshot, identity, identity_values, phase, evidence_ids, reason = binding
    try:
        bound_snapshot = object.__getattribute__(state, "_intent_snapshot")
    except AttributeError as error:
        raise ValueError("ASK_AND_BLOCK state binding is invalid") from error
    if (
        bound_snapshot is not snapshot
        or state.identity is not identity
        or (state.identity.case_id, state.identity.intent_id, state.identity.attempt_id)
        != identity_values
        or state.state is not phase
        or state.evidence_ids is not evidence_ids
        or state.reason is not reason
        or type(snapshot) is not IntentSnapshotAuthority
    ):
        raise ValueError("ASK_AND_BLOCK state binding was changed")
    _, intent = _require_live_snapshot(state.identity, snapshot)
    if state.reason != _reason_projection(intent):
        raise ValueError("ASK_AND_BLOCK reason projection was changed")


def _freeze(value: object) -> _FrozenValue:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("workflow reasons must contain only finite floats")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, _FrozenValue] = {}
        for key, item in sorted(value.items(), key=lambda pair: str(pair[0])):
            if not isinstance(key, str):
                raise TypeError("workflow reason mapping keys must be strings")
            frozen[key] = _freeze(item)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _freeze(to_dict())
    if is_dataclass(value) and not isinstance(value, type):
        return _freeze(asdict(value))
    raise TypeError("workflow reasons must be JSON-shaped or provide to_dict()")


def _thaw(value: _FrozenValue) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value
