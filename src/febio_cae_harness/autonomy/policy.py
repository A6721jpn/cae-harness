"""Pure, evidence-driven policy for the CAE case agent.

The policy layer consumes registry-issued intent authorities and immutable
evidence-shaped values.  It returns decisions only: it does not
start a process, modify a case, inspect geometry, or infer physical meaning.
Those boundaries make the functions deterministic and keep execution authority
in the solver and workspace phases.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from math import isfinite
from pathlib import Path
from threading import Lock
from types import MappingProxyType
from typing import Any, NoReturn, Self, cast

from ..contracts import IntentContract, IntentState, JSONValue
from ..evidence import EvidenceIntegrityError, EvidenceStore, IntentSnapshotAuthority
from ..solver.supervisor import SolverSupervisor
from ..solver.types import SolverClassification, SolverRunResult, SolverState
from ..workspace import AttemptWorkspace, CaseWorkspace

__all__ = [
    "ExecutionAction",
    "ExecutionContext",
    "ExecutionDecision",
    "ExecutionEvent",
    "ExecutionSignal",
    "FailureClass",
    "FailureClassification",
    "FailureEvidence",
    "FailureKind",
    "FailureRoute",
    "FailureRouting",
    "IntentSnapshotAuthority",
    "IntentStateAuthority",
    "IntentState",
    "PhysicalConditionEvidence",
    "Proposal",
    "ProposalAuthority",
    "ProposalAuthorityManager",
    "ProposalAction",
    "ProposalClass",
    "ProposalDecision",
    "ProposalKind",
    "ProposalValidationReceipt",
    "RetryBudgetExceeded",
    "RetryDecision",
    "RetryLedger",
    "RetryRecord",
    "RetryResult",
    "StateTransition",
    "advance_intent_state",
    "bind_retry_proposal",
    "classify_failure",
    "decide_execution",
    "decide_proposal",
    "decide_retry",
    "evaluate_proposal",
    "execution_decision",
    "failure_route",
    "has_authoritative_unresolved",
    "next_intent_state",
    "retry_decision",
    "route_failure",
    "transition_intent",
    "transition_state",
    "unresolved_authoritative_conditions",
    "validate_attempt_workspace",
]


class ProposalClass(StrEnum):
    """Debug proposal classes from the canonical harness specification."""

    INTENT_PRESERVING = "INTENT_PRESERVING"
    INTENT_SENSITIVE = "INTENT_SENSITIVE"
    INTENT_CHANGING = "INTENT_CHANGING"


ProposalKind = ProposalClass


class ProposalAction(StrEnum):
    """Action a proposal policy permits the caller to take."""

    AUTO_APPLY = "AUTO_APPLY"
    REQUIRE_VALIDATION = "REQUIRE_VALIDATION"
    # ``HOLD_FOR_VALIDATION`` is a readable compatibility spelling.
    HOLD_FOR_VALIDATION = "REQUIRE_VALIDATION"
    ASK_AND_BLOCK = "ASK_AND_BLOCK"
    REJECT = "REJECT"


class FailureRoute(StrEnum):
    """Next diagnostic or lifecycle route for an observed failure."""

    NO_FAILURE = "NO_FAILURE"
    MESH_DIAGNOSTIC = "MESH_DIAGNOSTIC"
    # A mesh repair remains a diagnostic proposal until evidence authorizes it.
    MESH_REPAIR = "MESH_DIAGNOSTIC"
    NONLINEAR_DIAGNOSTIC = "NONLINEAR_DIAGNOSTIC"
    RETRY = "RETRY"
    RESUME_MONITORING = "RESUME_MONITORING"
    STOP = "STOP"
    ASK_AND_BLOCK = "ASK_AND_BLOCK"


class FailureClass(StrEnum):
    """Deterministic classes recognized from explicit failure evidence."""

    NONE = "NONE"
    NEGATIVE_JACOBIAN = "NEGATIVE_JACOBIAN"
    NEGATIVE_JAC = "NEGATIVE_JACOBIAN"
    NONLINEAR_CONVERGENCE = "NONLINEAR_CONVERGENCE"
    NONLINEAR = "NONLINEAR_CONVERGENCE"
    TIMEOUT = "TIMEOUT"
    TIMED_OUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    CANCEL = "CANCELLED"
    DISCONNECTED = "DISCONNECTED"
    MISSING_REFERENCE = "MISSING_REFERENCE"
    MISSING_OUTPUT = "MISSING_OUTPUT"
    FATAL = "FATAL"
    UNKNOWN = "UNKNOWN"

    @property
    def default_route(self) -> FailureRoute:
        """Return the route that applies before retry-budget accounting."""

        return {
            FailureClass.NONE: FailureRoute.NO_FAILURE,
            FailureClass.NEGATIVE_JACOBIAN: FailureRoute.MESH_DIAGNOSTIC,
            FailureClass.NONLINEAR_CONVERGENCE: FailureRoute.NONLINEAR_DIAGNOSTIC,
            FailureClass.TIMEOUT: FailureRoute.RETRY,
            FailureClass.CANCELLED: FailureRoute.STOP,
            FailureClass.DISCONNECTED: FailureRoute.RESUME_MONITORING,
            FailureClass.MISSING_REFERENCE: FailureRoute.STOP,
            FailureClass.MISSING_OUTPUT: FailureRoute.STOP,
            FailureClass.FATAL: FailureRoute.STOP,
            FailureClass.UNKNOWN: FailureRoute.STOP,
        }[self]

    @property
    def retryable(self) -> bool:
        """Whether this class can consume a retry budget entry."""

        return self in {
            FailureClass.NEGATIVE_JACOBIAN,
            FailureClass.NONLINEAR_CONVERGENCE,
            FailureClass.TIMEOUT,
        }


# Names used by callers that prefer ``kind`` or ``classification`` terminology.
FailureKind = FailureClass
FailureClassification = FailureClass


_REQUIRED_INTENT_FACTS: tuple[str, ...] = (
    "engineering_question",
    "units",
    "material",
    "loads",
    "constraints",
    "contact",
    "analysis_step",
    "roi",
    "evaluation_quantities",
)


class RetryDecision(StrEnum):
    """Result of applying failure routing and retry-budget policy."""

    RETRY = "RETRY"
    RETRY_REQUIRES_RESERVATION = "RETRY_REQUIRES_RESERVATION"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    WAIT_FOR_RECONNECT = "WAIT_FOR_RECONNECT"
    ASK_AND_BLOCK = "ASK_AND_BLOCK"
    STOP = "STOP"


class ExecutionSignal(StrEnum):
    """Signals accepted by the pure execution lifecycle policy."""

    STARTED = "STARTED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PROCESS_EXITED = "PROCESS_EXITED"
    TIMEOUT = "TIMEOUT"
    TIMED_OUT = "TIMEOUT"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCEL = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    DISCONNECTED = "DISCONNECTED"
    DISCONNECT = "DISCONNECTED"
    RECONNECTED = "RECONNECTED"
    RESUME_REQUESTED = "RECONNECTED"


ExecutionEvent = ExecutionSignal


class ExecutionAction(StrEnum):
    """Decision returned for a lifecycle signal; no action is performed here."""

    MONITOR = "MONITOR"
    VALIDATE_OUTPUT = "VALIDATE_OUTPUT"
    CANCEL_OWNED_PROCESS = "CANCEL_OWNED_PROCESS"
    # The alias emphasizes that a raw PID is intentionally not a policy action.
    CANCEL_PROCESS = "CANCEL_OWNED_PROCESS"
    HOLD_NO_NEW_WORK = "HOLD_NO_NEW_WORK"
    WAIT_FOR_RECONNECT = "HOLD_NO_NEW_WORK"
    RESUME_MONITORING = "RESUME_MONITORING"
    STOP = "STOP"
    NO_ACTION = "NO_ACTION"


def _freeze_json(value: object) -> JSONValue:
    """Recursively freeze a JSON-shaped value for policy records."""

    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("policy values must contain only finite floats")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, JSONValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("policy mapping keys must be strings")
            frozen[key] = _freeze_json(item)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    raise TypeError("policy values must be JSON-compatible")


def _as_bool(value: object) -> bool:
    """Accept only JSON booleans as affirmative evidence."""

    return value is True


def _normalise_token(value: object) -> str:
    if not isinstance(value, str):
        return ""
    token = value.strip().casefold()
    for character in ("-", " ", "/", "."):
        token = token.replace(character, "_")
    return token


_FAILURE_TOKEN_MAP: dict[str, FailureClass] = {
    "none": FailureClass.NONE,
    "success": FailureClass.NONE,
    "normal_termination": FailureClass.NONE,
    "negative_jacobian": FailureClass.NEGATIVE_JACOBIAN,
    "negative_jac": FailureClass.NEGATIVE_JACOBIAN,
    "negative_jacobian_failure": FailureClass.NEGATIVE_JACOBIAN,
    "nonlinear_convergence": FailureClass.NONLINEAR_CONVERGENCE,
    "nonlinear": FailureClass.NONLINEAR_CONVERGENCE,
    "convergence": FailureClass.NONLINEAR_CONVERGENCE,
    "timeout": FailureClass.TIMEOUT,
    "timed_out": FailureClass.TIMEOUT,
    "cancel": FailureClass.CANCELLED,
    "cancelled": FailureClass.CANCELLED,
    "canceled": FailureClass.CANCELLED,
    "disconnected": FailureClass.DISCONNECTED,
    "disconnect": FailureClass.DISCONNECTED,
    "missing_reference": FailureClass.MISSING_REFERENCE,
    "missing_ref": FailureClass.MISSING_REFERENCE,
    "missing_output": FailureClass.MISSING_OUTPUT,
    "fatal": FailureClass.FATAL,
    "fatal_error": FailureClass.FATAL,
}


def _failure_from_token(value: object) -> FailureClass | None:
    if isinstance(value, FailureClass):
        return value
    return _FAILURE_TOKEN_MAP.get(_normalise_token(value))


@dataclass(frozen=True, slots=True)
class PhysicalConditionEvidence:
    """An explicit source record for one unresolved physical condition.

    ``authoritative`` is deliberately explicit.  A condition name, geometry
    label, or convention alone cannot make this record authoritative.
    """

    condition: str
    source: str | None = None
    authoritative: bool = False
    resolved: bool = False
    detail: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.condition, str) or not self.condition.strip():
            raise ValueError("condition must be a non-empty string")
        object.__setattr__(self, "condition", self.condition.strip())
        if self.source is not None and not isinstance(self.source, str):
            raise TypeError("source must be a string or None")

    @property
    def unresolved(self) -> bool:
        return not self.resolved


def _condition_records(value: object) -> tuple[dict[str, object], ...]:
    """Normalize opaque intent condition data without assigning semantics."""

    if value is None:
        return ()
    if isinstance(value, Mapping):
        keys = {str(key) for key in value}
        if keys & {"condition", "name", "field", "authoritative", "resolved", "status"}:
            return (dict(value),)
        records: list[dict[str, object]] = []
        for key in sorted(value, key=str):
            item = value[key]
            if isinstance(item, Mapping):
                record = dict(item)
                record.setdefault("condition", str(key))
            else:
                record = {"condition": str(key), "value": item}
            records.append(record)
        return tuple(records)
    if isinstance(value, (list, tuple)):
        records = []
        for item in value:
            if isinstance(item, Mapping):
                records.append(dict(item))
            elif isinstance(item, str):
                records.append({"condition": item})
        return tuple(records)
    if isinstance(value, str):
        return ({"condition": value},)
    return ()


def _condition_name(record: Mapping[str, object]) -> str | None:
    for key in ("condition", "name", "field"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _source_is_authoritative(value: object) -> bool:
    # A source label is descriptive metadata, never an authority grant.  Only
    # the canonical record's explicit boolean marker can authorize a fact.
    return isinstance(value, Mapping) and _as_bool(value.get("authoritative"))


def _evidence_is_current(value: object) -> bool:
    """Reject an evidence record explicitly marked stale or not current."""

    if not isinstance(value, Mapping):
        if isinstance(value, (list, tuple)):
            return all(_evidence_is_current(item) for item in value)
        return True
    if value.get("stale") is True:
        return False
    if value.get("current") is False:
        return False
    if value.get("fresh") is False:
        return False
    if _normalise_token(value.get("status")) in {"stale", "expired", "superseded"}:
        return False
    return all(
        _evidence_is_current(value.get(key))
        for key in ("source", "provenance", "evidence")
        if key in value
    )


def _value_is_present(value: object) -> bool:
    """Return whether an intent fact has a non-empty explicit value."""

    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        if not value:
            return False
        if "value" in value:
            return _value_is_present(value["value"])
        return True
    if isinstance(value, (list, tuple)):
        return bool(value) and any(_value_is_present(item) for item in value)
    return True


def _intent_has_current_authoritative_completeness(intent: IntentContract) -> bool:
    """Check the current intent before accepting a completeness flag.

    ``conditions_complete`` is a caller hint, not evidence.  Re-evaluate the
    immutable intent projection so an empty, partially populated, or stale
    ``BOUND`` contract cannot stay bound merely because an old boolean was
    retained.  The required facts and their source records are evaluated at
    the point of transition, keeping the evidence tied to this intent
    instance.
    """

    source_records = _condition_records(intent.condition_sources)
    sources: dict[str, object] = {}
    for source_record in source_records:
        name = _condition_name(source_record)
        if name is not None:
            sources[name.casefold()] = source_record
        elif len(source_record) == 1:
            key, value = next(iter(source_record.items()))
            sources[str(key).casefold()] = value

    for name in _REQUIRED_INTENT_FACTS:
        value = getattr(intent, name)
        if not _value_is_present(value) or not _evidence_is_current(value):
            return False
        source = sources.get(name.casefold())
        if source is None:
            return False
        if not _evidence_is_current(source) or not _source_is_authoritative(source):
            return False
    return True


def _unresolved_authoritative_conditions_from_intent(
    intent: IntentContract,
) -> tuple[PhysicalConditionEvidence, ...]:
    """Project the current canonical intent's authoritative missing records."""

    if type(intent) is not IntentContract:
        return ()
    source_records = _condition_records(intent.condition_sources)
    sources: dict[str, object] = {}
    for source_record in source_records:
        name = _condition_name(source_record)
        if name is not None:
            sources[name.casefold()] = source_record
        elif len(source_record) == 1:
            key, value = next(iter(source_record.items()))
            sources[str(key).casefold()] = value
    result: list[PhysicalConditionEvidence] = []
    for record in _condition_records(intent.unresolved):
        name = _condition_name(record)
        if name is None:
            continue
        status = _normalise_token(record.get("status"))
        if _as_bool(record.get("resolved")) or status in {"resolved", "known", "specified"}:
            continue
        if not _evidence_is_current(record):
            continue
        # The missing-condition record itself must carry the authority bit.
        # Matching source labels may corroborate it, but can never mint it.
        if not _as_bool(record.get("authoritative")):
            continue
        matching_source = sources.get(name.casefold())
        if matching_source is None or not _evidence_is_current(matching_source):
            continue
        if not _source_is_authoritative(matching_source):
            continue
        source_value = record.get("source")
        source = source_value if isinstance(source_value, str) else None
        matching_source_value = (
            matching_source.get("source") if isinstance(matching_source, Mapping) else None
        )
        if (
            source is not None
            and isinstance(matching_source_value, str)
            and source.strip() != matching_source_value.strip()
        ):
            continue
        if source is None and isinstance(matching_source, Mapping):
            candidate = matching_source.get("source")
            source = candidate if isinstance(candidate, str) else None
        detail_value = record.get("detail")
        detail = detail_value if isinstance(detail_value, str) else None
        result.append(
            PhysicalConditionEvidence(
                condition=name,
                source=source,
                authoritative=True,
                detail=detail,
            )
        )
    return tuple(result)


def _live_authority_intents(
    value: object,
) -> tuple[IntentSnapshotAuthority, IntentContract, IntentContract] | None:
    """Return persisted/effective intents only from a live registry authority."""

    if type(value) is IntentSnapshotAuthority:
        try:
            intent = value.intent
        except (AttributeError, EvidenceIntegrityError, TypeError, ValueError):
            return None
        if type(intent) is not IntentContract:
            return None
        return value, intent, intent

    if type(value) is not IntentStateAuthority:
        return None
    try:
        state_record = value._validated_record()
        snapshot = state_record[1]
        if type(snapshot) is not IntentSnapshotAuthority:
            return None
        persisted_intent = snapshot.intent
        if persisted_intent is not state_record[2]:
            return None
        effective_intent = state_record[3]
    except (AttributeError, EvidenceIntegrityError, TypeError, ValueError, IndexError):
        return None
    if type(persisted_intent) is not IntentContract or type(effective_intent) is not IntentContract:
        return None
    return snapshot, persisted_intent, effective_intent


def unresolved_authoritative_conditions(
    authority: IntentSnapshotAuthority | IntentStateAuthority | object,
) -> tuple[PhysicalConditionEvidence, ...]:
    """Return current authoritative missing records from a live authority only."""

    authority_intents = _live_authority_intents(authority)
    if authority_intents is None:
        return ()
    result = _unresolved_authoritative_conditions_from_intent(authority_intents[1])
    refreshed = _live_authority_intents(authority)
    if (
        refreshed is None
        or refreshed[0] is not authority_intents[0]
        or refreshed[1] is not authority_intents[1]
        or _unresolved_authoritative_conditions_from_intent(refreshed[1]) != result
    ):
        return ()
    return result


def has_authoritative_unresolved(
    authority: IntentSnapshotAuthority | IntentStateAuthority | object,
) -> bool:
    """Return whether a live registry authority has an explicit blocker."""

    return bool(unresolved_authoritative_conditions(authority))


_IntentStateRecord = tuple[
    object,
    IntentSnapshotAuthority,
    IntentContract,
    IntentContract,
    IntentState,
    IntentState,
    str,
    tuple[PhysicalConditionEvidence, ...],
]
_INTENT_STATE_RECORDS: dict[int, _IntentStateRecord] = {}


class IntentStateAuthority:
    """Opaque, snapshot-bound authority for one derived intent transition."""

    __slots__ = ()

    def __new__(
        cls,
        *args: object,
        **kwargs: object,
    ) -> IntentStateAuthority:
        del args, kwargs
        raise TypeError("intent state authorities are issued by transition_intent")

    def __init__(
        self,
        *args: object,
        **kwargs: object,
    ) -> None:
        del args, kwargs
        raise TypeError("intent state authorities are issued by transition_intent")

    def __init_subclass__(cls, **kwargs: object) -> None:
        del kwargs
        raise TypeError("intent state authorities cannot be subclassed")

    @classmethod
    def _issue(
        cls,
        snapshot: IntentSnapshotAuthority,
    ) -> IntentStateAuthority:
        if cls is not IntentStateAuthority:
            raise TypeError("intent state authorities cannot be subclassed")
        if type(snapshot) is not IntentSnapshotAuthority:
            raise TypeError("intent state authority requires an IntentSnapshotAuthority")
        persisted_intent = snapshot.intent
        complete = _intent_has_current_authoritative_completeness(persisted_intent)
        blocking = _unresolved_authoritative_conditions_from_intent(persisted_intent)
        if snapshot.intent is not persisted_intent:
            raise EvidenceIntegrityError("intent snapshot changed during state authorization")
        previous = persisted_intent.state
        if blocking:
            current = IntentState.ASK_AND_BLOCK
            reason = "authoritative physical conditions remain unresolved"
        elif previous is IntentState.BOUND and complete:
            current = IntentState.BOUND
            reason = "intent remains bound"
        elif complete:
            current = IntentState.BOUND
            reason = "authoritative intent conditions are complete"
        else:
            current = IntentState.GATHERING
            reason = "authoritative intent conditions are still being gathered"
        intent = (
            persisted_intent if current is previous else replace(persisted_intent, state=current)
        )
        authority = object.__new__(cls)
        _INTENT_STATE_RECORDS[id(authority)] = (
            authority,
            snapshot,
            persisted_intent,
            intent,
            previous,
            current,
            reason,
            blocking,
        )
        return authority

    def __repr__(self) -> str:
        return "IntentStateAuthority(<opaque>)"

    __str__ = __repr__

    def __setattr__(self, name: str, value: object) -> None:
        del name, value
        raise AttributeError("intent state authorities are immutable")

    def __delattr__(self, name: str) -> None:
        del name
        raise AttributeError("intent state authorities are immutable")

    def __copy__(self) -> IntentStateAuthority:
        raise TypeError("intent state authorities cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> IntentStateAuthority:
        del memo
        raise TypeError("intent state authorities cannot be copied")

    def __reduce__(self) -> NoReturn:
        raise TypeError("intent state authorities cannot be pickled")

    def __reduce_ex__(self, protocol: object) -> NoReturn:
        del protocol
        raise TypeError("intent state authorities cannot be pickled")

    def _validated_record(self) -> _IntentStateRecord:
        record = _INTENT_STATE_RECORDS.get(id(self))
        if record is None or record[0] is not self:
            raise EvidenceIntegrityError("intent state authority is invalid")
        snapshot = record[1]
        if type(snapshot) is not IntentSnapshotAuthority:
            raise EvidenceIntegrityError("intent state snapshot authority is invalid")
        if snapshot.intent is not record[2]:
            raise EvidenceIntegrityError("intent state snapshot is stale")
        return record

    def _validated_for(self, snapshot: IntentSnapshotAuthority) -> _IntentStateRecord:
        record = self._validated_record()
        if type(snapshot) is not IntentSnapshotAuthority or snapshot is not record[1]:
            raise EvidenceIntegrityError("intent state authority is bound to another snapshot")
        return record

    @property
    def intent(self) -> IntentContract:
        return self._validated_record()[3]

    @property
    def previous(self) -> IntentState:
        return self._validated_record()[4]

    @property
    def current(self) -> IntentState:
        return self._validated_record()[5]

    @property
    def reason(self) -> str:
        return self._validated_record()[6]

    @property
    def blocking_conditions(self) -> tuple[PhysicalConditionEvidence, ...]:
        record = self._validated_record()
        current = _unresolved_authoritative_conditions_from_intent(record[2])
        if current != record[7]:
            raise EvidenceIntegrityError("intent blocking-condition authority is stale")
        return current

    @property
    def changed(self) -> bool:
        record = self._validated_record()
        return record[4] is not record[5]

    @property
    def state(self) -> IntentState:
        return self.current

    @property
    def next_state(self) -> IntentState:
        return self.current

    @property
    def from_state(self) -> IntentState:
        return self.previous

    @property
    def to_state(self) -> IntentState:
        return self.current


# Keep the established result name while making the authority's sealed class
# canonical.  Both names refer to exactly the same runtime type.
StateTransition = IntentStateAuthority


def transition_intent(snapshot: IntentSnapshotAuthority) -> StateTransition:
    """Derive a sealed state authority from one live persisted intent snapshot."""

    if type(snapshot) is not IntentSnapshotAuthority:
        raise TypeError("snapshot must be an IntentSnapshotAuthority")
    return StateTransition._issue(snapshot)


transition_state = transition_intent
advance_intent_state = transition_intent


@dataclass(frozen=True, slots=True)
class FailureEvidence:
    """Explicit evidence fields used by :func:`classify_failure`.

    Boolean fields are intentionally opt-in.  A free-form message is retained
    for rationale but is not sufficient to classify a failure by itself unless
    it contains one of the exact known failure tokens.
    """

    failure_class: FailureClass | str | None = None
    kind: FailureClass | str | None = None
    source: str | None = None
    message: str | None = None
    negative_jacobian: bool = False
    nonlinear_convergence: bool = False
    timeout: bool = False
    cancelled: bool = False
    disconnected: bool = False
    missing_reference: bool = False
    missing_output: bool = False
    fatal: bool = False
    normal_termination: bool = False
    evidence_id: str | None = None
    evidence_ids: tuple[str, ...] = ()
    phase: str | None = None
    step: int | str | None = None
    time: float | None = None
    element_ids: tuple[int | str, ...] = ()
    all_integration_points_checked: bool = False
    requires_physical_decision: bool = False

    def __post_init__(self) -> None:
        if self.time is not None and (
            not isinstance(self.time, (int, float)) or not isfinite(self.time)
        ):
            raise ValueError("time must be a finite number or None")
        object.__setattr__(self, "element_ids", tuple(self.element_ids))
        object.__setattr__(self, "evidence_ids", tuple(self.evidence_ids))


def _explicit_failure_class(payload: Mapping[str, object]) -> FailureClass | None:
    for key in ("failure_class", "failure_kind", "kind", "type", "failure"):
        value = payload.get(key)
        classification = _failure_from_token(value)
        if classification is not None:
            return classification
    return None


def classify_failure(
    evidence: FailureEvidence | Mapping[str, object] | FailureClass | str | None,
) -> FailureClass:
    """Classify explicit evidence with a fixed precedence order.

    Negative Jacobian evidence takes precedence over nonlinear evidence because
    it identifies a mesh/geometry diagnostic path.  Unknown or empty evidence
    is never promoted to a physical-condition question.
    """

    if isinstance(evidence, FailureClass):
        return evidence
    if isinstance(evidence, str):
        return _failure_from_token(evidence) or FailureClass.UNKNOWN
    if evidence is None:
        return FailureClass.UNKNOWN
    if isinstance(evidence, FailureEvidence):
        explicit = _failure_from_token(evidence.failure_class) or _failure_from_token(evidence.kind)
        if explicit is not None:
            return explicit
        values: Mapping[str, object] = {
            "negative_jacobian": evidence.negative_jacobian,
            "nonlinear_convergence": evidence.nonlinear_convergence,
            "missing_reference": evidence.missing_reference,
            "fatal": evidence.fatal,
            "timeout": evidence.timeout,
            "cancelled": evidence.cancelled,
            "disconnected": evidence.disconnected,
            "missing_output": evidence.missing_output,
            "normal_termination": evidence.normal_termination,
            "message": evidence.message,
        }
    elif isinstance(evidence, Mapping):
        explicit = _explicit_failure_class(evidence)
        if explicit is not None:
            return explicit
        values = evidence
    else:
        return FailureClass.UNKNOWN

    checks: tuple[tuple[FailureClass, tuple[str, ...]], ...] = (
        (FailureClass.NEGATIVE_JACOBIAN, ("negative_jacobian", "negative_jac", "negativeJacobian")),
        (
            FailureClass.NONLINEAR_CONVERGENCE,
            ("nonlinear_convergence", "nonlinear", "convergence_failure"),
        ),
        (FailureClass.MISSING_REFERENCE, ("missing_reference", "missing_ref")),
        (FailureClass.FATAL, ("fatal", "fatal_error")),
        (FailureClass.TIMEOUT, ("timeout", "timed_out")),
        (FailureClass.CANCELLED, ("cancelled", "canceled", "cancel")),
        (FailureClass.DISCONNECTED, ("disconnected", "disconnect")),
        (FailureClass.MISSING_OUTPUT, ("missing_output", "output_missing")),
    )
    for classification, keys in checks:
        if any(
            _as_bool(values.get(key))
            or any(_as_bool(item) for name, item in values.items() if _normalise_token(name) == key)
            for key in keys
        ):
            return classification
    if _as_bool(values.get("normal_termination")):
        return FailureClass.NONE
    message = values.get("message")
    if isinstance(message, str):
        normalized_message = _normalise_token(message)
        # Exact tokens are accepted as solver evidence; arbitrary prose is not.
        for token, classification in _FAILURE_TOKEN_MAP.items():
            if token in normalized_message:
                return classification
    return FailureClass.UNKNOWN


@dataclass(frozen=True, slots=True)
class FailureRouting:
    """Failure class plus the distinct diagnostic route it selects."""

    classification: FailureClass
    route: FailureRoute
    rationale: str
    retryable: bool
    proposal_class: ProposalClass | None = None
    evidence_complete: bool = False
    blocking_conditions: tuple[PhysicalConditionEvidence, ...] = ()

    @property
    def failure_class(self) -> FailureClass:
        return self.classification

    @property
    def kind(self) -> FailureClass:
        return self.classification

    @property
    def next_route(self) -> FailureRoute:
        return self.route

    @property
    def action(self) -> FailureRoute:
        return self.route

    @property
    def requires_authoritative_physical_condition(self) -> bool:
        return bool(self.blocking_conditions)


def _physical_request(evidence: FailureEvidence | Mapping[str, object] | object) -> bool:
    if isinstance(evidence, FailureEvidence):
        return evidence.requires_physical_decision
    if isinstance(evidence, Mapping):
        return any(
            _as_bool(evidence.get(key))
            for key in (
                "requires_physical_decision",
                "physical_condition_unresolved",
                "requires_authoritative_physical_condition",
            )
        )
    return False


def route_failure(
    evidence: FailureEvidence | Mapping[str, object] | FailureClass | str | None,
    *,
    intent: IntentSnapshotAuthority | IntentStateAuthority | IntentContract | None = None,
) -> FailureRouting:
    """Route an evidence-backed failure to mesh, nonlinear, retry, or stop.

    An ``ASK_AND_BLOCK`` route is possible only when the evidence explicitly
    requests a physical decision *and* a live registry authority has an
    unresolved authoritative condition.  Raw contracts are diagnostic input
    only and fail closed before any physical question can be emitted.
    """

    classification = classify_failure(evidence)
    physical_request = _physical_request(evidence)
    authority_intents = _live_authority_intents(intent) if intent is not None else None
    if intent is not None and authority_intents is None:
        return FailureRouting(
            classification=classification,
            route=FailureRoute.STOP,
            rationale="failure routing requires the current registry-issued live authority",
            retryable=False,
        )
    canonical_intent = authority_intents[1] if authority_intents is not None else None
    blocking = (
        unresolved_authoritative_conditions(intent)
        if canonical_intent is not None and physical_request
        else ()
    )
    if authority_intents is not None and physical_request:
        refreshed = _live_authority_intents(intent)
        if (
            refreshed is None
            or refreshed[0] is not authority_intents[0]
            or refreshed[1] is not authority_intents[1]
        ):
            return FailureRouting(
                classification=classification,
                route=FailureRoute.STOP,
                rationale="physical-condition authority changed during failure routing",
                retryable=False,
            )
        canonical_intent = refreshed[2]
        if canonical_intent.state is not IntentState.ASK_AND_BLOCK:
            blocking = ()
    if blocking:
        return FailureRouting(
            classification=classification,
            route=FailureRoute.ASK_AND_BLOCK,
            rationale="an authoritative physical condition is unresolved",
            retryable=False,
            blocking_conditions=blocking,
        )
    if physical_request:
        return FailureRouting(
            classification=classification,
            route=FailureRoute.STOP,
            rationale="physical change requested without authoritative unresolved evidence",
            retryable=False,
        )
    if classification is FailureClass.NEGATIVE_JACOBIAN:
        complete = False
        if isinstance(evidence, FailureEvidence):
            complete = bool(
                evidence.phase and evidence.element_ids and evidence.all_integration_points_checked
            )
        elif isinstance(evidence, Mapping):
            phase_present = bool(evidence.get("phase"))
            elements_present = bool(evidence.get("element_ids"))
            points_checked = _as_bool(evidence.get("all_integration_points_checked")) or any(
                _as_bool(item)
                for name, item in evidence.items()
                if _normalise_token(name) == "all_integration_points_checked"
            )
            complete = phase_present and elements_present and points_checked
        return FailureRouting(
            classification=classification,
            route=FailureRoute.MESH_DIAGNOSTIC,
            rationale=(
                "inspect initial/deformed phase, step/time, element and integration-point "
                "Jacobians, mesh quality, and ROI/contact/constraint relationships"
            ),
            retryable=True,
            proposal_class=ProposalClass.INTENT_PRESERVING,
            evidence_complete=complete,
        )
    if classification is FailureClass.NONLINEAR_CONVERGENCE:
        return FailureRouting(
            classification=classification,
            route=FailureRoute.NONLINEAR_DIAGNOSTIC,
            rationale="diagnose numerical controls, step sizing, and convergence evidence",
            retryable=True,
            proposal_class=ProposalClass.INTENT_SENSITIVE,
        )
    if classification is FailureClass.TIMEOUT:
        return FailureRouting(
            classification=classification,
            route=FailureRoute.RETRY,
            rationale="timeout may be retried only within the declared retry budget",
            retryable=True,
        )
    if classification is FailureClass.DISCONNECTED:
        return FailureRouting(
            classification=classification,
            route=FailureRoute.RESUME_MONITORING,
            rationale="client disconnect does not authorize new model changes or retries",
            retryable=False,
        )
    if classification is FailureClass.NONE:
        return FailureRouting(
            classification=classification,
            route=FailureRoute.NO_FAILURE,
            rationale="no failure was evidenced",
            retryable=False,
        )
    return FailureRouting(
        classification=classification,
        route=FailureRoute.STOP,
        rationale="failure is not authorized for an automatic retry",
        retryable=False,
    )


@dataclass(frozen=True, slots=True)
class Proposal:
    """An immutable, evidence-linked debugging proposal."""

    proposal_id: str
    proposal_class: ProposalClass | str
    rationale: str = ""
    evidence_ids: tuple[str, ...] = ()
    changes: Mapping[str, JSONValue] = field(default_factory=dict)
    authorized: bool = False
    requires_physical_decision: bool = False
    within_contract: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.proposal_id, str) or not self.proposal_id.strip():
            raise ValueError("proposal_id must be a non-empty string")
        try:
            normalized_class = ProposalClass(self.proposal_class)
        except (TypeError, ValueError) as error:
            raise ValueError("proposal_class must be a supported ProposalClass") from error
        object.__setattr__(self, "proposal_id", self.proposal_id.strip())
        object.__setattr__(self, "proposal_class", normalized_class)
        object.__setattr__(self, "evidence_ids", tuple(self.evidence_ids))
        if not isinstance(self.changes, Mapping):
            raise TypeError("changes must be a JSON mapping")
        frozen_changes = _freeze_json(self.changes)
        assert isinstance(frozen_changes, Mapping)
        object.__setattr__(self, "changes", frozen_changes)

    @property
    def classification(self) -> ProposalClass:
        return cast(ProposalClass, self.proposal_class)

    @property
    def kind(self) -> ProposalClass:
        return self.classification


def _structural_subset(candidate: object, declared: object) -> bool:
    if isinstance(candidate, Mapping):
        return isinstance(declared, Mapping) and all(
            key in declared and _structural_subset(value, declared[key])
            for key, value in candidate.items()
        )
    if isinstance(candidate, tuple):
        return (
            isinstance(declared, tuple)
            and len(candidate) == len(declared)
            and all(map(_structural_subset, candidate, declared))
        )
    return type(candidate) is type(declared) and candidate == declared


def _matches_declaration(value: object, declarations: object, namespace: str) -> bool:
    if isinstance(declarations, Mapping) and namespace in declarations:
        declarations = declarations[namespace]
    if isinstance(declarations, tuple):
        return _structural_subset(value, declarations) or any(
            _matches_declaration(value, declaration, namespace) for declaration in declarations
        )
    return _structural_subset(value, declarations)


def _change_value_present(value: object) -> bool:
    if value is None or isinstance(value, str) and not value.strip():
        return False
    if isinstance(value, Mapping):
        return bool(value) and all(_change_value_present(item) for item in value.values())
    return not isinstance(value, tuple) or bool(value)


def _namespaced_scope(
    changes: Mapping[str, JSONValue], declarations: object, namespace: str
) -> str:
    matched = _matches_declaration(
        changes[namespace], declarations, namespace
    ) or _matches_declaration(changes, declarations, namespace)
    return namespace if matched else "invalid"


def _proposal_scope(intent: IntentContract, proposal: Proposal) -> str:
    changes = proposal.changes
    if not isinstance(changes, Mapping):
        return "invalid"
    if not changes or proposal.requires_physical_decision:
        return "physical"
    if proposal.classification is ProposalClass.INTENT_CHANGING:
        return "physical"
    if not _change_value_present(changes):
        return "invalid"
    names = set(changes)
    if names == {"mesh"}:
        return _namespaced_scope(changes, intent.allowed_mesh_changes, "mesh")
    if names == {"numerical"}:
        return _namespaced_scope(changes, intent.allowed_numerical_changes, "numerical")
    if _matches_declaration(changes, intent.allowed_numerical_changes, "numerical"):
        return "numerical"
    return "physical"


class _Opaque:
    __slots__ = ()

    def _forbidden(self, *args: object) -> NoReturn:
        raise TypeError("opaque proposal authorities cannot be copied or pickled")

    __copy__ = __deepcopy__ = __reduce__ = __reduce_ex__ = _forbidden


class ProposalAuthority(_Opaque):
    __slots__ = ()

    def __new__(cls, *args: object, **kwargs: object) -> ProposalAuthority:
        del args, kwargs
        raise TypeError("proposal tokens are manager-issued")

    def __init_subclass__(cls, **kwargs: object) -> NoReturn:
        raise TypeError("proposal authorities cannot be subclassed")


class ProposalAuthorityManager(_Opaque):
    __slots__ = ()

    def __init__(self, state_authority: IntentStateAuthority) -> None:
        record = _state_authority_record(state_authority, bound=True)
        snapshot = record[1]
        _PROPOSAL_MANAGERS[id(self)] = (
            self,
            state_authority,
            snapshot,
            snapshot.case_id,
            snapshot.case_sha256,
            snapshot.intent_sha256,
        )

    def issue(self, proposal: Proposal) -> ProposalAuthority:
        manager = _proposal_manager_record(self)
        return _issue_proposal_token(manager, proposal)

    def validate(
        self,
        proposal: Proposal | ProposalAuthority | ProposalValidationReceipt,
    ) -> ProposalValidationReceipt:
        _proposal_manager_record(self)
        raise EvidenceIntegrityError(
            "proposal validation requires a canonical result-validation authority"
        )


ProposalValidationReceipt = ProposalAuthority
type _ProposalManagerRecord = tuple[Any, ...]
type _ProposalTokenRecord = tuple[Any, ...]
_PROPOSAL_MANAGERS: dict[int, _ProposalManagerRecord] = {}
_PROPOSAL_TOKENS: dict[int, _ProposalTokenRecord] = {}


def _state_authority_record(value: object, *, bound: bool = False) -> _IntentStateRecord:
    if type(value) is not IntentStateAuthority:
        raise TypeError("proposal policy requires an exact IntentStateAuthority")
    record = value._validated_record()
    if bound and record[5] is not IntentState.BOUND:
        raise EvidenceIntegrityError("proposal authorization requires a BOUND state authority")
    return record


def validate_attempt_workspace(
    state_authority: IntentStateAuthority,
    attempt_workspace: AttemptWorkspace,
) -> None:
    """Require a live attempt issued for the state authority's exact case.

    The workspace object is the capability; no caller-supplied path, case
    identifier, or digest is accepted as an authorization input.  The helper
    intentionally returns no identity material: consumers must retain the
    manager-issued handle and use its checked filesystem protocol directly.
    """

    state_record = _state_authority_record(state_authority, bound=True)
    snapshot = state_record[1]
    if type(attempt_workspace) is not AttemptWorkspace:
        raise TypeError("attempt_workspace must be an exact AttemptWorkspace")

    # ``__fspath__`` performs the workspace registry, case, attempt, and live
    # directory identity checks.  Keep that validation before reading any
    # state from the handle so forged or stale handles fail closed.
    attempt_id = attempt_workspace.attempt_id
    attempt_root = Path(os.fspath(attempt_workspace))
    if attempt_workspace.attempt_id != attempt_id:
        raise EvidenceIntegrityError("attempt workspace changed during validation")
    refreshed_state = _state_authority_record(state_authority, bound=True)
    if refreshed_state[1] is not snapshot:
        raise EvidenceIntegrityError("intent state authority changed during workspace validation")

    try:
        snapshot_case_workspace = object.__getattribute__(snapshot, "_case_workspace")
    except AttributeError as error:
        raise EvidenceIntegrityError("intent snapshot case binding is invalid") from error
    if type(snapshot_case_workspace) is not CaseWorkspace:
        raise EvidenceIntegrityError("intent snapshot case binding is invalid")

    expected_attempt_root = Path(snapshot_case_workspace.temporary_root) / "attempts" / attempt_id
    if attempt_root != expected_attempt_root:
        raise EvidenceIntegrityError("attempt workspace is not inside the intent snapshot case")


def _proposal_manager_record(value: object) -> _ProposalManagerRecord:
    if type(value) is not ProposalAuthorityManager:
        raise TypeError("proposal manager must be an exact ProposalAuthorityManager")
    record = _PROPOSAL_MANAGERS.get(id(value))
    if record is None or record[0] is not value:
        raise EvidenceIntegrityError("proposal manager is not manager-issued")
    if (
        _state_authority_record(record[1], bound=True)[1] is not record[2]
        or (record[2].case_id, record[2].case_sha256, record[2].intent_sha256) != record[3:6]
    ):
        raise EvidenceIntegrityError("proposal manager binding is stale")
    return record


def _proposal_projection(proposal: Proposal) -> tuple[object, ...]:
    if type(proposal) is not Proposal:
        raise TypeError("proposal must be an exact Proposal")
    try:
        evidence_ids = tuple(proposal.evidence_ids)
        frozen_changes = _freeze_json(proposal.changes)
    except (AttributeError, TypeError, ValueError) as error:
        raise EvidenceIntegrityError("proposal changes are invalid") from error
    if any(not isinstance(value, str) or not value.strip() for value in evidence_ids):
        raise EvidenceIntegrityError("proposal evidence identifiers are invalid")
    return (
        proposal.proposal_id,
        proposal.proposal_class,
        proposal.rationale,
        evidence_ids,
        frozen_changes,
        proposal.authorized,
        proposal.requires_physical_decision,
        proposal.within_contract,
    )


def _json_projection(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _json_projection(item) for key, item in sorted(value.items())}
    if isinstance(value, tuple):
        return [_json_projection(item) for item in value]
    if value is None or type(value) in {bool, int, float, str}:
        return value
    if isinstance(value, StrEnum):
        return value.value
    raise EvidenceIntegrityError("proposal projection is not canonical JSON")


def _proposal_fingerprint(proposal: Proposal) -> str:
    projection = _proposal_projection(proposal)
    body = {
        "authorized": projection[5],
        "changes": _json_projection(projection[4]),
        "evidence_ids": _json_projection(projection[3]),
        "proposal_class": cast(ProposalClass, projection[1]).value,
        "rationale": projection[2],
        "requires_physical_decision": projection[6],
        "within_contract": projection[7],
    }
    payload = json.dumps(
        body,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def bind_retry_proposal(proposal: Proposal) -> Proposal:
    """Return a proposal whose identifier binds its complete retry projection."""

    if type(proposal) is not Proposal:
        raise TypeError("proposal must be an exact Proposal")
    return replace(proposal, proposal_id=_proposal_fingerprint(proposal))


def _issue_proposal_token(
    manager: _ProposalManagerRecord,
    proposal: Proposal,
) -> ProposalAuthority:
    projection = _proposal_projection(proposal)
    if not proposal.evidence_ids:
        raise EvidenceIntegrityError("proposal requires explicit evidence identifiers")
    token = object.__new__(ProposalAuthority)
    _PROPOSAL_TOKENS[id(token)] = (token, manager[0], manager[1], manager[2], proposal, projection)
    return token


def _proposal_token_record_for(value: object) -> _ProposalTokenRecord:
    if type(value) is not ProposalAuthority:
        raise TypeError("proposal authorization requires a manager-issued authority or receipt")
    record = _PROPOSAL_TOKENS.get(id(value))
    if record is None:
        raise EvidenceIntegrityError("proposal authority or receipt is not manager-issued")
    if record[0] is not value:
        raise EvidenceIntegrityError("proposal authority or receipt is not manager-issued")
    return record


def _proposal_token_record(
    value: object,
    *,
    manager: ProposalAuthorityManager,
    state_authority: IntentStateAuthority | None = None,
    proposal: Proposal | None = None,
) -> _ProposalTokenRecord:
    record = _proposal_token_record_for(value)
    manager_record = _proposal_manager_record(manager)
    if (
        record[1] is not manager
        or record[2] is not manager_record[1]
        or (state_authority is not None and record[2] is not state_authority)
    ):
        raise EvidenceIntegrityError("proposal authorization belongs to another manager or state")
    if _state_authority_record(record[2], bound=True)[1] is not record[3]:
        raise EvidenceIntegrityError("proposal authorization snapshot is stale")
    if proposal is not None and (
        proposal is not record[4] or _proposal_projection(proposal) != record[5]
    ):
        raise EvidenceIntegrityError("proposal authorization does not match the proposal")
    return record


@dataclass(frozen=True, slots=True)
class ProposalDecision:
    """Decision for a proposal, with no application side effect."""

    proposal: Proposal
    action: ProposalAction
    reason: str
    next_state: IntentState
    blocking_conditions: tuple[PhysicalConditionEvidence, ...] = ()

    @property
    def should_apply(self) -> bool:
        return self.action is ProposalAction.AUTO_APPLY

    @property
    def accepted(self) -> bool:
        return self.should_apply


def decide_proposal(
    state_authority: IntentStateAuthority | IntentContract | object,
    proposal: Proposal,
    proposal_authority: ProposalAuthority | ProposalValidationReceipt | None = None,
    *,
    validation_receipt: ProposalValidationReceipt | None = None,
    authority: ProposalAuthority | ProposalValidationReceipt | None = None,
    validation_passed: bool = False,
) -> ProposalDecision:
    """Decide a proposal; only a live manager authority can produce AUTO_APPLY."""

    if not isinstance(proposal, Proposal):
        raise TypeError("proposal must be a Proposal")

    # A raw contract, state enum, boolean, or forged object may describe a
    # proposal but cannot authorize consuming its action fields.  Return a
    # deterministic rejection so this path cannot fabricate a question.
    if type(state_authority) is not IntentStateAuthority:
        return ProposalDecision(
            proposal=proposal,
            action=ProposalAction.REJECT,
            reason="proposal requires a registry-issued live intent state authority",
            next_state=IntentState.GATHERING,
        )

    state_record = _state_authority_record(state_authority)
    intent = state_record[3]

    supplied = tuple(
        token for token in (proposal_authority, validation_receipt, authority) if token is not None
    )
    if len(supplied) > 1:
        raise TypeError("supply only one proposal authority or validation receipt")
    token_record: _ProposalTokenRecord | None = None
    if supplied:
        token_record = _proposal_token_record(
            supplied[0],
            manager=cast(ProposalAuthorityManager, _proposal_token_record_for(supplied[0])[1]),
            state_authority=state_authority,
            proposal=proposal,
        )
        state_record = _state_authority_record(state_authority)
        intent = state_record[3]

    def result(
        action: ProposalAction,
        reason: str,
        next_state: IntentState | None = None,
        blocking_conditions: tuple[PhysicalConditionEvidence, ...] = (),
    ) -> ProposalDecision:
        return ProposalDecision(
            proposal=proposal,
            action=action,
            reason=reason,
            next_state=intent.state if next_state is None else next_state,
            blocking_conditions=blocking_conditions,
        )

    # Re-read the canonical persisted intent through the live snapshot before
    # consuming any proposal action field.  The state authority's cached
    # transition is only valid while that exact snapshot remains current.
    state_record = _state_authority_record(state_authority)
    intent = state_record[3]
    blocking = _unresolved_authoritative_conditions_from_intent(state_record[2])
    if intent.state is IntentState.ASK_AND_BLOCK and not blocking:
        return result(
            ProposalAction.REJECT,
            "ASK_AND_BLOCK state lacks a current authoritative condition record",
        )
    scope = _proposal_scope(intent, proposal)
    if scope == "physical":
        if blocking:
            try:
                refreshed_record = _state_authority_record(state_authority)
                refreshed_blocking = _unresolved_authoritative_conditions_from_intent(
                    refreshed_record[2]
                )
            except (EvidenceIntegrityError, TypeError):
                return result(
                    ProposalAction.REJECT,
                    "proposal physical-condition authority is stale",
                )
            if (
                refreshed_record[1] is not state_record[1]
                or refreshed_record[5] is not IntentState.ASK_AND_BLOCK
                or not refreshed_blocking
            ):
                return result(
                    ProposalAction.REJECT,
                    "proposal physical-condition authority changed",
                )
            state_record = refreshed_record
            intent = refreshed_record[3]
            blocking = refreshed_blocking
            return result(
                ProposalAction.ASK_AND_BLOCK,
                "authoritative physical condition requires a user decision",
                IntentState.ASK_AND_BLOCK,
                blocking,
            )
        return result(
            ProposalAction.REJECT,
            "physical change has no unresolved authoritative condition to ask about",
        )
    if scope == "invalid":
        return result(
            ProposalAction.REJECT,
            "proposal changes are not explicitly declared by the live intent contract",
        )

    if not proposal.evidence_ids:
        return result(
            ProposalAction.REJECT,
            "proposal lacks explicit evidence identifiers",
        )
    if scope == "numerical":
        return result(
            ProposalAction.REQUIRE_VALIDATION,
            "declared numerical change requires canonical result validation",
        )
    if token_record is None:
        return result(
            ProposalAction.REQUIRE_VALIDATION,
            "manager-issued proposal authority is required before APPLY",
        )

    # Validate the exact proposal token and live state again immediately before
    # returning an APPLY decision.  Frozen dataclasses can still be tampered
    # with by a caller using object-level mutation, so no earlier projection is
    # sufficient for authorization.
    try:
        _state_authority_record(state_authority)
        _proposal_token_record(
            token_record[0],
            manager=cast(ProposalAuthorityManager, token_record[1]),
            state_authority=state_authority,
            proposal=proposal,
        )
    except (EvidenceIntegrityError, TypeError):
        return result(
            ProposalAction.REJECT,
            "proposal authority or live intent state is stale",
        )
    return result(ProposalAction.AUTO_APPLY, "declared mesh change has manager authority")


@dataclass(frozen=True, slots=True)
class RetryRecord:
    """One consumed retry-budget entry."""

    failure: FailureClass
    attempt_id: str | None = None
    proposal_id: str | None = None
    evidence_ids: tuple[str, ...] = ()
    reservation_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "failure", FailureClass(self.failure))
        object.__setattr__(self, "evidence_ids", tuple(self.evidence_ids))


class RetryBudgetExceeded(RuntimeError):
    """Raised when a caller tries to consume an exhausted retry budget."""


type _RetryLedgerRecord = tuple[
    RetryLedger,
    IntentStateAuthority,
    IntentSnapshotAuthority,
    str,
    str,
    str,
    int | None,
    tuple[object, ...],
]
_RETRY_LEDGER_RECORDS: dict[int, _RetryLedgerRecord] = {}
_RETRY_CONSUMPTION_LOCK = Lock()
_RETRY_CONSUMED_LEDGERS: dict[int, RetryLedger] = {}
_RETRY_CONSUMED_RESULTS: dict[int, SolverRunResult] = {}
_RETRY_CONSUMED_PAIRS: dict[tuple[int, int], tuple[RetryLedger, SolverRunResult]] = {}


@dataclass(frozen=True, slots=True)
class RetryLedger:
    """Immutable retry accounting derived from ``IntentContract.execution_budget``."""

    budget: int | None = None
    used: int = 0
    records: tuple[RetryRecord, ...] = ()

    def __post_init__(self) -> None:
        if self.budget is not None and (
            isinstance(self.budget, bool) or not isinstance(self.budget, int) or self.budget < 0
        ):
            raise ValueError("budget must be a non-negative integer or None")
        if isinstance(self.used, bool) or not isinstance(self.used, int) or self.used < 0:
            raise ValueError("used must be a non-negative integer")
        if self.budget is not None and self.used > self.budget:
            raise ValueError("used cannot exceed budget")
        object.__setattr__(self, "records", tuple(self.records))

    @classmethod
    def from_authority(cls, authority: IntentStateAuthority) -> Self:
        """Issue an authorized empty ledger from a live, bound state authority."""

        if type(authority) is not IntentStateAuthority:
            raise TypeError("authority must be an exact IntentStateAuthority")
        return cast(Self, _issue_retry_ledger(authority))

    @classmethod
    def from_store(
        cls,
        authority: IntentStateAuthority,
        store: EvidenceStore,
    ) -> Self:
        """Restore a registry-issued ledger from a validated append-only chain."""

        if cls is not RetryLedger:
            raise TypeError("retry ledger subclasses are not supported")
        return cast(Self, _restore_retry_ledger(authority, store))

    @classmethod
    def from_intent(cls, intent: IntentContract) -> Self:
        """Create an unregistered diagnostic ledger from a raw intent contract."""

        if type(intent) is not IntentContract:
            raise TypeError("intent must be an exact IntentContract")
        return cls(budget=intent.execution_budget.retry_budget)

    @classmethod
    def from_budget(cls, budget: int | None) -> Self:
        return cls(budget=budget)

    @property
    def retry_budget(self) -> int | None:
        return self.budget

    @property
    def remaining(self) -> int | None:
        if self.budget is None:
            return None
        return self.budget - self.used

    @property
    def remaining_retries(self) -> int | None:
        return self.remaining

    @property
    def retry_budget_remaining(self) -> int | None:
        return self.remaining

    @property
    def used_retries(self) -> int:
        return self.used

    @property
    def budget_exhausted(self) -> bool:
        return not self.can_retry

    @property
    def can_retry(self) -> bool:
        return self.budget is None or self.used < self.budget

    def consume(
        self,
        *,
        failure: FailureClass | str = FailureClass.UNKNOWN,
        attempt_id: str | None = None,
        proposal_id: str | None = None,
        evidence_ids: Iterable[str] = (),
    ) -> Self:
        """Return a ledger with one retry consumed, or raise if exhausted."""

        if not self.can_retry:
            raise RetryBudgetExceeded("retry budget is exhausted")
        record = RetryRecord(
            failure=FailureClass(failure),
            attempt_id=attempt_id,
            proposal_id=proposal_id,
            evidence_ids=tuple(evidence_ids),
        )
        return replace(self, used=self.used + 1, records=self.records + (record,))

    def record_retry(
        self,
        failure: FailureClass | str = FailureClass.UNKNOWN,
        *,
        attempt_id: str | None = None,
        proposal_id: str | None = None,
        evidence_ids: Iterable[str] = (),
    ) -> Self:
        return self.consume(
            failure=failure,
            attempt_id=attempt_id,
            proposal_id=proposal_id,
            evidence_ids=evidence_ids,
        )

    def claim_attempt(
        self,
        store: EvidenceStore,
        authority: IntentStateAuthority,
        reservation_id: str,
        attempt_id: str,
        *,
        proposal: Proposal | None = None,
        proposal_authority: ProposalAuthority | ProposalValidationReceipt | None = None,
    ) -> dict[str, object]:
        """Atomically bind one durable authorized reservation to a fresh attempt."""

        return _claim_retry_attempt(
            self,
            store,
            authority,
            reservation_id,
            attempt_id,
            proposal=proposal,
            proposal_authority=proposal_authority,
        )

    def begin_attempt_setup(
        self,
        store: EvidenceStore,
        authority: IntentStateAuthority,
        reservation_id: str,
        attempt_id: str,
        *,
        proposal: Proposal | None = None,
        proposal_authority: ProposalAuthority | ProposalValidationReceipt | None = None,
    ) -> bool:
        """Atomically mark a claimed retry as unsafe to launch more than once."""

        return _begin_retry_attempt_setup(
            self,
            store,
            authority,
            reservation_id,
            attempt_id,
            proposal=proposal,
            proposal_authority=proposal_authority,
        )


def _retry_record_projection(record: RetryRecord) -> tuple[object, ...]:
    if type(record) is not RetryRecord:
        raise EvidenceIntegrityError("retry ledger contains a non-canonical record")
    failure = record.failure
    if type(failure) is not FailureClass:
        raise EvidenceIntegrityError("retry ledger failure classification is invalid")
    for value, label in (
        (record.attempt_id, "attempt identifier"),
        (record.proposal_id, "proposal identifier"),
    ):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise EvidenceIntegrityError(f"retry ledger {label} is invalid")
    evidence_ids = tuple(record.evidence_ids)
    if any(not isinstance(value, str) or not value.strip() for value in evidence_ids):
        raise EvidenceIntegrityError("retry ledger evidence identifiers are invalid")
    reservation_id = record.reservation_id
    if reservation_id is not None and (
        not isinstance(reservation_id, str)
        or len(reservation_id) != 64
        or any(character not in "0123456789abcdef" for character in reservation_id)
    ):
        raise EvidenceIntegrityError("retry ledger reservation identifier is invalid")
    return (failure, record.attempt_id, record.proposal_id, evidence_ids, reservation_id)


def _retry_ledger_projection(ledger: RetryLedger) -> tuple[object, ...]:
    if type(ledger) is not RetryLedger:
        raise EvidenceIntegrityError("retry ledger must be an exact RetryLedger")
    records = tuple(_retry_record_projection(record) for record in ledger.records)
    if ledger.used != len(records):
        raise EvidenceIntegrityError("retry ledger used count does not match its records")
    return (ledger.budget, ledger.used, records)


def _issue_retry_ledger(
    state_authority: IntentStateAuthority,
    *,
    used: int = 0,
    records: Iterable[RetryRecord] = (),
) -> RetryLedger:
    state_record = _state_authority_record(state_authority, bound=True)
    snapshot = state_record[1]
    intent = state_record[3]
    budget = intent.execution_budget.retry_budget
    ledger = RetryLedger(budget=budget, used=used, records=tuple(records))
    projection = _retry_ledger_projection(ledger)
    if any(record.reservation_id is None for record in ledger.records):
        raise EvidenceIntegrityError("authority retry ledger requires durable reservations")
    _RETRY_LEDGER_RECORDS[id(ledger)] = (
        ledger,
        state_authority,
        snapshot,
        snapshot.case_id,
        snapshot.case_sha256,
        snapshot.intent_sha256,
        budget,
        projection,
    )
    return ledger


def _validated_retry_ledger(
    ledger: RetryLedger,
    state_authority: IntentStateAuthority,
) -> _RetryLedgerRecord:
    if type(ledger) is not RetryLedger:
        raise TypeError("retry ledger must be an exact RetryLedger")
    record = _RETRY_LEDGER_RECORDS.get(id(ledger))
    if record is None or record[0] is not ledger:
        raise EvidenceIntegrityError("retry ledger is not registry-issued")
    state_record = _state_authority_record(state_authority, bound=True)
    snapshot = state_record[1]
    if (
        record[1] is not state_authority
        or record[2] is not snapshot
        or (snapshot.case_id, snapshot.case_sha256, snapshot.intent_sha256) != record[3:6]
    ):
        raise EvidenceIntegrityError("retry ledger authority or snapshot is stale")
    canonical_budget = state_record[3].execution_budget.retry_budget
    if record[6] != canonical_budget or ledger.budget != canonical_budget:
        raise EvidenceIntegrityError("retry ledger budget does not match the live intent")
    if _retry_ledger_projection(ledger) != record[7]:
        raise EvidenceIntegrityError("retry ledger projection was modified")
    return record


def _validate_retry_claim_proposal(
    retry_record: RetryRecord,
    authority: IntentStateAuthority,
    proposal: Proposal | None,
    proposal_authority: ProposalAuthority | ProposalValidationReceipt | None,
) -> None:
    if retry_record.proposal_id is None:
        if (
            retry_record.failure is not FailureClass.TIMEOUT
            or proposal is not None
            or proposal_authority is not None
        ):
            raise EvidenceIntegrityError("no-change retry reservation is invalid")
        return
    if (
        retry_record.failure is not FailureClass.NEGATIVE_JACOBIAN
        or type(proposal) is not Proposal
        or proposal_authority is None
        or proposal.proposal_id != retry_record.proposal_id
        or _proposal_fingerprint(proposal) != retry_record.proposal_id
    ):
        raise EvidenceIntegrityError("retry reservation requires its exact proposal authority")
    decision = decide_proposal(authority, proposal, proposal_authority)
    if decision.action is not ProposalAction.AUTO_APPLY:
        raise EvidenceIntegrityError("retry reservation requires its exact proposal authority")


def _claim_retry_attempt(
    ledger: RetryLedger,
    store: EvidenceStore,
    authority: IntentStateAuthority,
    reservation_id: str,
    attempt_id: str,
    *,
    proposal: Proposal | None,
    proposal_authority: ProposalAuthority | ProposalValidationReceipt | None,
) -> dict[str, object]:
    """Validate policy authority before the evidence layer consumes a reservation."""

    if type(store) is not EvidenceStore:
        raise TypeError("store must be an exact EvidenceStore")
    if type(authority) is not IntentStateAuthority:
        raise TypeError("authority must be an exact IntentStateAuthority")
    if not isinstance(reservation_id, str):
        raise TypeError("reservation_id must be a string")
    if not isinstance(attempt_id, str):
        raise TypeError("attempt_id must be a string")
    ledger_record = _validated_retry_ledger(ledger, authority)
    matches = [record for record in ledger.records if record.reservation_id == reservation_id]
    if len(matches) != 1:
        raise EvidenceIntegrityError("retry reservation is not present in the authorized ledger")
    retry_record = matches[0]
    _validate_retry_claim_proposal(
        retry_record,
        authority,
        proposal,
        proposal_authority,
    )
    if retry_record.attempt_id is None:
        raise EvidenceIntegrityError("retry reservation parent attempt is missing")
    snapshot = ledger_record[2]
    record_index = ledger.records.index(retry_record)
    return store._claim_retry_attempt(
        snapshot,
        attempt_id,
        {
            "failure": retry_record.failure.value,
            "intent_sha256": snapshot.intent_sha256,
            "parent_attempt_id": retry_record.attempt_id,
            "proposal_id": retry_record.proposal_id,
            "reservation_id": reservation_id,
            "retry_budget": ledger.budget,
            "retry_used": record_index + 1,
        },
    )


def _begin_retry_attempt_setup(
    ledger: RetryLedger,
    store: EvidenceStore,
    authority: IntentStateAuthority,
    reservation_id: str,
    attempt_id: str,
    *,
    proposal: Proposal | None,
    proposal_authority: ProposalAuthority | ProposalValidationReceipt | None,
) -> bool:
    """Validate policy authority before recording the retry setup boundary."""

    if type(store) is not EvidenceStore:
        raise TypeError("store must be an exact EvidenceStore")
    if type(authority) is not IntentStateAuthority:
        raise TypeError("authority must be an exact IntentStateAuthority")
    if not isinstance(reservation_id, str):
        raise TypeError("reservation_id must be a string")
    if not isinstance(attempt_id, str):
        raise TypeError("attempt_id must be a string")
    ledger_record = _validated_retry_ledger(ledger, authority)
    matches = [record for record in ledger.records if record.reservation_id == reservation_id]
    if len(matches) != 1:
        raise EvidenceIntegrityError("retry reservation is not present in the authorized ledger")
    retry_record = matches[0]
    _validate_retry_claim_proposal(
        retry_record,
        authority,
        proposal,
        proposal_authority,
    )
    if retry_record.attempt_id is None:
        raise EvidenceIntegrityError("retry reservation parent attempt is missing")
    snapshot = ledger_record[2]
    return store._begin_retry_attempt_setup(snapshot, attempt_id, reservation_id)


@dataclass(frozen=True, slots=True)
class RetryResult:
    """Retry decision and the resulting immutable ledger."""

    decision: RetryDecision
    ledger: RetryLedger
    failure: FailureClass
    reason: str
    route: FailureRoute
    reservation_id: str | None = None

    @property
    def action(self) -> RetryDecision:
        return self.decision

    @property
    def next_ledger(self) -> RetryLedger:
        return self.ledger

    @property
    def retry_allowed(self) -> bool:
        record = _RETRY_RESULT_RECORDS.get(id(self))
        return (
            self.decision is RetryDecision.RETRY
            and record is not None
            and record[0] is self
            and _retry_result_projection(self) == record[7]
        )

    @property
    def reservation_required(self) -> bool:
        return self.decision is RetryDecision.RETRY_REQUIRES_RESERVATION

    def persist(
        self,
        store: EvidenceStore,
        authority: IntentStateAuthority,
    ) -> dict[str, object]:
        """Atomically reserve this pending retry and its exact solver terminal evidence."""

        return _persist_retry_result(self, store, authority)


type _RetryResultRecord = tuple[
    RetryResult,
    IntentStateAuthority,
    IntentSnapshotAuthority,
    RetryLedger,
    SolverSupervisor,
    SolverRunResult,
    str,
    tuple[object, ...],
]
_RETRY_RESULT_RECORDS: dict[int, _RetryResultRecord] = {}


def _retry_result_projection(result: RetryResult) -> tuple[object, ...]:
    if type(result) is not RetryResult:
        raise EvidenceIntegrityError("retry result must be an exact RetryResult")
    return (
        result.decision,
        result.ledger,
        result.failure,
        result.reason,
        result.route,
        result.reservation_id,
        _retry_ledger_projection(result.ledger),
    )


def _restore_retry_ledger(
    authority: IntentStateAuthority,
    store: EvidenceStore,
) -> RetryLedger:
    if type(store) is not EvidenceStore:
        raise TypeError("store must be an exact EvidenceStore")
    state_record = _state_authority_record(authority, bound=True)
    snapshot = state_record[1]
    consumptions = store._read_retry_consumptions(snapshot)
    canonical_budget = state_record[3].execution_budget.retry_budget
    records: list[RetryRecord] = []
    for expected_used, payload in enumerate(consumptions, start=1):
        if payload.get("retry_budget") != canonical_budget:
            raise EvidenceIntegrityError("durable retry budget differs from the live intent")
        if payload.get("retry_used") != expected_used:
            raise EvidenceIntegrityError("durable retry ledger sequence is invalid")
        attempt_id = payload.get("attempt_id")
        proposal_id = payload.get("proposal_id")
        reservation_id = payload.get("reservation_id")
        failure_value = payload.get("failure")
        solver_classification_value = payload.get("solver_classification")
        solver_state_value = payload.get("solver_state")
        if not isinstance(attempt_id, str):
            raise EvidenceIntegrityError("durable retry attempt is invalid")
        if proposal_id is not None and not isinstance(proposal_id, str):
            raise EvidenceIntegrityError("durable retry proposal is invalid")
        if (
            not isinstance(reservation_id, str)
            or len(reservation_id) != 64
            or any(character not in "0123456789abcdef" for character in reservation_id)
        ):
            raise EvidenceIntegrityError("durable retry reservation is invalid")
        if not isinstance(failure_value, str):
            raise EvidenceIntegrityError("durable retry failure is invalid")
        if not isinstance(solver_classification_value, str):
            raise EvidenceIntegrityError("durable solver classification is invalid")
        if not isinstance(solver_state_value, str):
            raise EvidenceIntegrityError("durable solver state is invalid")
        try:
            failure = FailureClass(failure_value)
        except (TypeError, ValueError) as error:
            raise EvidenceIntegrityError("durable retry failure is invalid") from error
        try:
            solver_classification = SolverClassification(solver_classification_value)
        except (TypeError, ValueError) as error:
            raise EvidenceIntegrityError("durable solver classification is invalid") from error
        try:
            solver_state = SolverState(solver_state_value)
        except (TypeError, ValueError) as error:
            raise EvidenceIntegrityError("durable solver state is invalid") from error
        if (
            solver_classification is SolverClassification.TIMEOUT
            and solver_state is not SolverState.TIMED_OUT
        ) or (
            solver_classification is SolverClassification.CANCELLED
            and solver_state is not SolverState.CANCELLED
        ):
            raise EvidenceIntegrityError("durable solver state differs from its classification")
        if solver_classification not in {
            SolverClassification.TIMEOUT,
            SolverClassification.CANCELLED,
        } and solver_state not in {SolverState.FAILED, SolverState.NORMAL_EXIT}:
            raise EvidenceIntegrityError("durable solver state differs from its classification")
        if _SOLVER_FAILURE_CLASSIFICATIONS.get(solver_classification) is not failure:
            raise EvidenceIntegrityError("durable retry failure differs from solver evidence")
        records.append(
            RetryRecord(
                failure=failure,
                attempt_id=attempt_id,
                proposal_id=proposal_id,
                reservation_id=reservation_id,
            )
        )
    return _issue_retry_ledger(
        authority,
        used=len(records),
        records=records,
    )


def _persist_retry_result(
    result: RetryResult,
    store: EvidenceStore,
    authority: IntentStateAuthority,
) -> dict[str, object]:
    if type(store) is not EvidenceStore:
        raise TypeError("store must be an exact EvidenceStore")
    if type(authority) is not IntentStateAuthority:
        raise TypeError("authority must be an exact IntentStateAuthority")
    record = _RETRY_RESULT_RECORDS.get(id(result))
    if record is None or record[0] is not result:
        raise EvidenceIntegrityError("retry result is not policy-issued")
    if (
        result.decision is not RetryDecision.RETRY_REQUIRES_RESERVATION
        or result.reservation_id is None
    ):
        raise EvidenceIntegrityError("retry result does not carry a pending reservation")
    state_record = _state_authority_record(authority, bound=True)
    if (
        record[1] is not authority
        or record[2] is not state_record[1]
        or record[3] is not result.ledger
        or _retry_result_projection(result) != record[7]
    ):
        raise EvidenceIntegrityError("retry result authority or projection changed")
    _validated_retry_ledger(result.ledger, authority)
    supervisor = record[4]
    solver_result = record[5]
    if not _solver_result_is_authoritative(supervisor, solver_result):
        raise EvidenceIntegrityError("retry solver result authority is stale")
    launch_correlation = _validated_supervisor_correlation(supervisor)
    if launch_correlation is None or not _retry_authority_matches(launch_correlation, state_record):
        raise EvidenceIntegrityError("retry solver launch authority is stale")
    attempt_id = record[6]
    if launch_correlation[9] != attempt_id:
        raise EvidenceIntegrityError("retry attempt authority changed")
    retry_record = result.ledger.records[-1]
    if (
        retry_record.attempt_id != attempt_id
        or retry_record.reservation_id != result.reservation_id
    ):
        raise EvidenceIntegrityError("retry ledger attempt binding changed")
    return store._record_retry_terminal(
        state_record[1],
        {
            "attempt_id": attempt_id,
            "classification": solver_result.classification.value,
            "official_fbs": False,
            "return_code": solver_result.return_code,
            "solver_state": solver_result.state.value,
            "status": "SOLVER_FAILED",
        },
        {
            "attempt_id": attempt_id,
            "failure": result.failure.value,
            "proposal_id": retry_record.proposal_id,
            "reservation_id": result.reservation_id,
            "retry_budget": result.ledger.budget,
            "retry_used": result.ledger.used,
            "solver_classification": solver_result.classification.value,
        },
    )


def _solver_result_is_authoritative(
    supervisor: object,
    result: object,
) -> bool:
    if type(supervisor) is not SolverSupervisor or type(result) is not SolverRunResult:
        return False
    try:
        validated = SolverSupervisor._validate_result(supervisor, result)
    except Exception:
        return False
    if validated is not result:
        return False
    return not (
        result.success
        or (
            result.state is SolverState.NORMAL_EXIT
            and result.classification.value == "FBS_UNVERIFIED"
        )
    )


def _validated_supervisor_correlation(value: object) -> tuple[object, ...] | None:
    """Return only an exact supervisor's live launch correlation."""

    if type(value) is not SolverSupervisor:
        return None
    try:
        correlation = SolverSupervisor._validated_retry_correlation(value)
    except Exception:
        return None
    if not isinstance(correlation, tuple) or len(correlation) != 11:
        return None
    return correlation


def _live_snapshot_correlation(value: object) -> tuple[object, ...] | None:
    """Read a snapshot's exact live case binding and immutable identities."""

    if type(value) is not IntentSnapshotAuthority:
        return None
    try:
        snapshot_case_workspace = object.__getattribute__(value, "_case_workspace")
        if type(snapshot_case_workspace) is not CaseWorkspace:
            return None
        case_root = snapshot_case_workspace.root
        case_id = value.case_id
        case_sha256 = value.case_sha256
        intent_sha256 = value.intent_sha256
        intent = value.intent
        if type(intent) is not IntentContract:
            return None

        # Re-read all identity-bearing values so a mutation during the
        # projection cannot be mistaken for a stable authority.
        if (
            object.__getattribute__(value, "_case_workspace") is not snapshot_case_workspace
            or snapshot_case_workspace.root != case_root
            or value.case_id != case_id
            or value.case_sha256 != case_sha256
            or value.intent_sha256 != intent_sha256
            or value.intent is not intent
        ):
            return None
    except Exception:
        return None
    return (
        value,
        snapshot_case_workspace,
        case_root,
        case_id,
        case_sha256,
        intent_sha256,
        intent,
    )


def _retry_authority_matches(
    launch_correlation: tuple[object, ...],
    state_record: _IntentStateRecord,
) -> bool:
    """Correlate a live launch capability with the exact live state snapshot."""

    if len(launch_correlation) != 11:
        return False
    try:
        (
            _capability,
            _spec,
            attempt_workspace,
            launch_snapshot,
            launch_case_workspace,
            launch_case_root,
            launch_case_id,
            launch_case_sha256,
            launch_intent_sha256,
            _attempt_id,
            launch_attempt_root,
        ) = launch_correlation
        if type(attempt_workspace) is not AttemptWorkspace:
            return False
        launch_snapshot_record = _live_snapshot_correlation(launch_snapshot)
        state_snapshot_record = _live_snapshot_correlation(state_record[1])
        if launch_snapshot_record is None or state_snapshot_record is None:
            return False
        if launch_snapshot_record[0] is not launch_snapshot:
            return False
        if launch_snapshot_record[1] is not launch_case_workspace:
            return False
        if (
            launch_snapshot_record[2] != launch_case_root
            or launch_snapshot_record[3] != launch_case_id
            or launch_snapshot_record[4] != launch_case_sha256
            or launch_snapshot_record[5] != launch_intent_sha256
        ):
            return False

        (
            state_snapshot,
            state_case_workspace,
            state_case_root,
            state_case_id,
            state_case_sha256,
            state_intent_sha256,
            state_intent,
        ) = state_snapshot_record
        if state_intent is not state_record[2]:
            return False
        if type(state_snapshot) is not IntentSnapshotAuthority:
            return False
        if (
            not isinstance(launch_case_root, Path)
            or not isinstance(launch_attempt_root, Path)
            or not launch_attempt_root.is_relative_to(launch_case_root)
        ):
            return False
        return (
            type(state_case_workspace) is CaseWorkspace
            and launch_case_workspace is state_case_workspace
            and launch_case_root == state_case_root
            and launch_case_id == state_case_id == state_snapshot.case_id
            and launch_case_sha256 == state_case_sha256 == state_snapshot.case_sha256
            and launch_intent_sha256 == state_intent_sha256 == state_snapshot.intent_sha256
        )
    except Exception:
        return False


def _retry_authorities_are_same(
    first: tuple[object, ...],
    second: tuple[object, ...],
) -> bool:
    """Require the launch projection to remain the exact same authority."""

    if len(first) != 11 or len(second) != 11:
        return False
    return (
        first[0] is second[0]
        and first[1] is second[1]
        and first[2] is second[2]
        and first[3] is second[3]
        and first[4] is second[4]
        and first[5:] == second[5:]
    )


_SOLVER_FAILURE_CLASSIFICATIONS: Mapping[SolverClassification, FailureClass] = MappingProxyType(
    {
        SolverClassification.SUCCESS: FailureClass.NONE,
        SolverClassification.INIT_ONLY: FailureClass.UNKNOWN,
        SolverClassification.MISSING_OUTPUT: FailureClass.MISSING_OUTPUT,
        SolverClassification.FATAL: FailureClass.FATAL,
        SolverClassification.NEGATIVE_JACOBIAN: FailureClass.NEGATIVE_JACOBIAN,
        SolverClassification.NONLINEAR_CONVERGENCE: FailureClass.NONLINEAR_CONVERGENCE,
        SolverClassification.INVALID_LOG: FailureClass.UNKNOWN,
        SolverClassification.FBS_UNVERIFIED: FailureClass.UNKNOWN,
        SolverClassification.FBS_INVALID: FailureClass.UNKNOWN,
        SolverClassification.TIMEOUT: FailureClass.TIMEOUT,
        SolverClassification.CANCELLED: FailureClass.CANCELLED,
        SolverClassification.NONZERO_EXIT: FailureClass.UNKNOWN,
    }
)


def _solver_result_failure(result: SolverRunResult) -> FailureClass:
    """Map issued solver evidence to the only retry classification it permits."""

    return _SOLVER_FAILURE_CLASSIFICATIONS.get(result.classification, FailureClass.UNKNOWN)


def decide_retry(
    failure: FailureEvidence | Mapping[str, object] | FailureClass | str | None,
    ledger: RetryLedger,
    *,
    intent: IntentStateAuthority | IntentContract | None = None,
    state_authority: IntentStateAuthority | None = None,
    proposal: Proposal | None = None,
    proposal_authority: ProposalAuthority | ProposalValidationReceipt | None = None,
    validation_passed: bool = False,
    result: SolverRunResult | None = None,
    solver_result: SolverRunResult | None = None,
    supervisor: SolverSupervisor | None = None,
    solver_supervisor: SolverSupervisor | None = None,
) -> RetryResult:
    """Classify a failure and account for one authority-backed retry."""

    if not isinstance(ledger, RetryLedger):
        raise TypeError("ledger must be a RetryLedger")

    if result is not None and solver_result is not None and result is not solver_result:
        raise TypeError("supply only one solver result")
    if (
        supervisor is not None
        and solver_supervisor is not None
        and supervisor is not solver_supervisor
    ):
        raise TypeError("supply only one solver supervisor")
    effective_result = result if result is not None else solver_result
    effective_supervisor = supervisor if supervisor is not None else solver_supervisor

    if intent is not None and type(intent) not in {IntentContract, IntentStateAuthority}:
        raise TypeError("intent must be an IntentContract or IntentStateAuthority")
    if state_authority is not None and type(state_authority) is not IntentStateAuthority:
        raise TypeError("state_authority must be an exact IntentStateAuthority")
    if (
        state_authority is not None
        and type(intent) is IntentStateAuthority
        and state_authority is not intent
    ):
        raise TypeError("intent and state_authority must identify the same authority")
    if type(intent) is IntentContract and state_authority is not None:
        raise TypeError("raw IntentContract cannot be combined with a state authority")
    if proposal is None and proposal_authority is not None:
        raise TypeError("proposal_authority requires an exact proposal")

    authority = state_authority
    raw_intent_supplied = type(intent) is IntentContract
    if authority is None and type(intent) is IntentStateAuthority:
        authority = intent
    # Raw contracts are never routed as an authority-bearing intent.  They
    # remain diagnostic input only and cannot mint an ASK_AND_BLOCK decision.
    route_intent: IntentStateAuthority | None = authority
    authority_record: _IntentStateRecord | None = None
    if authority is not None:
        try:
            authority_record = _state_authority_record(authority, bound=True)
        except (EvidenceIntegrityError, TypeError):
            authority_record = None

    issued_result_failure: FailureClass | None = None
    if _solver_result_is_authoritative(effective_supervisor, effective_result):
        issued_result_failure = _solver_result_failure(cast(SolverRunResult, effective_result))

    if issued_result_failure is not None:
        classification = issued_result_failure
        routing = route_failure(issued_result_failure, intent=route_intent)
        failure_mismatch = (
            failure is not None and classify_failure(failure) is not issued_result_failure
        )
    else:
        routing = route_failure(failure, intent=route_intent)
        classification = routing.classification
        failure_mismatch = False

    def stopped(reason: str) -> RetryResult:
        return RetryResult(
            decision=RetryDecision.STOP,
            ledger=ledger,
            failure=classification,
            reason=reason,
            route=FailureRoute.STOP,
        )

    if failure_mismatch:
        return stopped("failure evidence does not match the exact issued solver result")

    # Refresh after failure classification/routing and before any proposal or
    # retry action field is consumed.  This closes the mutable snapshot window.
    if authority is not None:
        try:
            refreshed_record = _state_authority_record(authority, bound=True)
        except (EvidenceIntegrityError, TypeError):
            return stopped("intent state authority is stale or invalid")
        if authority_record is None or refreshed_record[1] is not authority_record[1]:
            return stopped("intent state authority changed during failure routing")
        authority_record = refreshed_record

    current_blocking = (
        _unresolved_authoritative_conditions_from_intent(authority_record[2])
        if authority_record is not None
        else ()
    )
    ask_and_block = (
        authority_record is not None and authority_record[5] is IntentState.ASK_AND_BLOCK
    )
    if ask_and_block and not routing.blocking_conditions:
        return stopped("ASK_AND_BLOCK state lacks a current authoritative condition record")
    if authority is not None and authority_record is None:
        return stopped("intent state authority is stale or invalid")
    if (
        authority_record is not None
        and routing.route is FailureRoute.ASK_AND_BLOCK
        and routing.blocking_conditions != current_blocking
    ):
        return stopped("physical-condition authority changed during failure routing")
    if routing.route is FailureRoute.ASK_AND_BLOCK:
        return RetryResult(
            decision=RetryDecision.ASK_AND_BLOCK,
            ledger=ledger,
            failure=classification,
            reason=routing.rationale,
            route=routing.route,
        )
    if raw_intent_supplied and authority is None:
        return stopped("retry requires a registry-issued live intent state authority")
    if classification is FailureClass.DISCONNECTED:
        return RetryResult(
            decision=RetryDecision.WAIT_FOR_RECONNECT,
            ledger=ledger,
            failure=classification,
            reason=routing.rationale,
            route=routing.route,
        )
    if classification is FailureClass.NEGATIVE_JACOBIAN and proposal is None:
        return stopped(
            "negative-Jacobian retry requires a completed mesh diagnostic and explicit repair "
            "proposal"
        )
    if proposal is not None:
        if raw_intent_supplied or authority is None or authority_record is None:
            return stopped("proposal evaluation requires a live BOUND intent state authority")
        proposal_decision = decide_proposal(
            authority,
            proposal,
            proposal_authority,
            validation_passed=validation_passed,
        )
        if proposal_decision.action is ProposalAction.ASK_AND_BLOCK:
            return RetryResult(
                decision=RetryDecision.ASK_AND_BLOCK,
                ledger=ledger,
                failure=classification,
                reason=proposal_decision.reason,
                route=FailureRoute.ASK_AND_BLOCK,
            )
        if proposal_decision.action in {ProposalAction.REJECT, ProposalAction.REQUIRE_VALIDATION}:
            return RetryResult(
                decision=RetryDecision.STOP,
                ledger=ledger,
                failure=classification,
                reason=proposal_decision.reason,
                route=FailureRoute.STOP,
            )
        if proposal.proposal_id != _proposal_fingerprint(proposal):
            return stopped("retry proposal identifier does not bind its exact projection")
    if not routing.retryable:
        return stopped(routing.rationale)
    if authority is None or authority_record is None:
        return stopped("retry requires a live BOUND intent state authority")
    try:
        _validated_retry_ledger(ledger, authority)
    except (EvidenceIntegrityError, TypeError):
        return stopped("retry requires a registry-issued ledger bound to the live intent")
    if any(record.failure is classification for record in ledger.records):
        return stopped("same failure recurred; retry loop stopped without consuming budget")
    if not _solver_result_is_authoritative(effective_supervisor, effective_result):
        return stopped("retry requires the exact failed result issued by its solver supervisor")
    launch_correlation = _validated_supervisor_correlation(effective_supervisor)
    if launch_correlation is None:
        return stopped("retry requires the exact live solver launch capability")
    if not _retry_authority_matches(launch_correlation, authority_record):
        return stopped("solver launch authority is not correlated to the live intent snapshot")
    issued_result = cast(SolverRunResult, effective_result)
    with _RETRY_CONSUMPTION_LOCK:
        # Revalidate all authority-bearing inputs immediately before mutating
        # the accounting registry.  Classification above is diagnostic only;
        # this second pass is the authorization boundary.
        try:
            refreshed_record = _state_authority_record(authority, bound=True)
            _validated_retry_ledger(ledger, authority)
        except (EvidenceIntegrityError, TypeError):
            return stopped("intent or retry ledger authority became stale")
        if authority_record is None or refreshed_record[1] is not authority_record[1]:
            return stopped("intent state authority changed before retry accounting")
        refreshed_launch_correlation = _validated_supervisor_correlation(effective_supervisor)
        if refreshed_launch_correlation is None:
            return stopped("solver launch authority became stale before retry accounting")
        if not _retry_authorities_are_same(launch_correlation, refreshed_launch_correlation):
            return stopped("solver launch authority changed before retry accounting")
        if not _retry_authority_matches(refreshed_launch_correlation, refreshed_record):
            return stopped("solver launch authority is not correlated before retry accounting")
        if not _solver_result_is_authoritative(effective_supervisor, issued_result):
            return stopped("retry result authority became stale before accounting")
        consumed_ledger = _RETRY_CONSUMED_LEDGERS.get(id(ledger))
        pair_key = (id(ledger), id(issued_result))
        consumed_pair = _RETRY_CONSUMED_PAIRS.get(pair_key)
        consumed_result = _RETRY_CONSUMED_RESULTS.get(id(issued_result))
        if consumed_ledger is ledger:
            return stopped("retry ledger has already minted a successor")
        if consumed_result is issued_result or (
            consumed_pair is not None
            and consumed_pair[0] is ledger
            and consumed_pair[1] is issued_result
        ):
            return stopped("exact failed solver result has already consumed a retry")
        if not ledger.can_retry:
            return RetryResult(
                decision=RetryDecision.BUDGET_EXHAUSTED,
                ledger=ledger,
                failure=classification,
                reason="declared retry budget is exhausted",
                route=FailureRoute.STOP,
            )
        # A caller-supplied failure record is diagnostic context only.  The issued
        # result carries the complete supervisor binding; no raw evidence IDs may
        # become part of an authority-backed ledger record.
        evidence_ids: tuple[str, ...] = ()
        attempt_id_value = refreshed_launch_correlation[9]
        if not isinstance(attempt_id_value, str) or not attempt_id_value.strip():
            return stopped("solver launch authority attempt identity is invalid")
        if any(record.attempt_id == attempt_id_value for record in ledger.records):
            return stopped("solver attempt already has a durable retry reservation")
        reservation_id = secrets.token_hex(32)
        record = RetryRecord(
            failure=classification,
            attempt_id=attempt_id_value,
            proposal_id=proposal.proposal_id if proposal is not None else None,
            evidence_ids=evidence_ids,
            reservation_id=reservation_id,
        )
        next_ledger = _issue_retry_ledger(
            authority,
            used=ledger.used + 1,
            records=ledger.records + (record,),
        )
        _RETRY_CONSUMED_LEDGERS[id(ledger)] = ledger
        _RETRY_CONSUMED_RESULTS[id(issued_result)] = issued_result
        _RETRY_CONSUMED_PAIRS[pair_key] = (ledger, issued_result)
    retry_result = RetryResult(
        decision=RetryDecision.RETRY_REQUIRES_RESERVATION,
        ledger=next_ledger,
        failure=classification,
        reason=routing.rationale,
        route=routing.route,
        reservation_id=reservation_id,
    )
    _RETRY_RESULT_RECORDS[id(retry_result)] = (
        retry_result,
        authority,
        authority_record[1],
        next_ledger,
        cast(SolverSupervisor, effective_supervisor),
        issued_result,
        attempt_id_value,
        _retry_result_projection(retry_result),
    )
    return retry_result


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    """Evidence needed to choose a safe lifecycle action."""

    case_id: str | None = None
    attempt_id: str | None = None
    process_owned: bool = False
    process_running: bool = False
    client_connected: bool = True
    correlation_verified: bool = True
    exit_code: int | None = None
    intent_id: str | None = None
    supervisor: SolverSupervisor | None = None
    solver_supervisor: SolverSupervisor | None = None


@dataclass(frozen=True, slots=True)
class ExecutionDecision:
    """Pure lifecycle decision; callers perform any authorized side effect."""

    signal: ExecutionSignal
    action: ExecutionAction
    reason: str
    process_owned: bool = False
    requires_output_validation: bool = False

    @property
    def next_action(self) -> ExecutionAction:
        return self.action


def _execution_signal(value: ExecutionSignal | str) -> ExecutionSignal:
    if isinstance(value, ExecutionSignal):
        return value
    normalized = _normalise_token(value)
    aliases = {
        "started": ExecutionSignal.STARTED,
        "running": ExecutionSignal.RUNNING,
        "completed": ExecutionSignal.COMPLETED,
        "process_exited": ExecutionSignal.PROCESS_EXITED,
        "timeout": ExecutionSignal.TIMEOUT,
        "timed_out": ExecutionSignal.TIMEOUT,
        "cancel_requested": ExecutionSignal.CANCEL_REQUESTED,
        "cancel": ExecutionSignal.CANCEL_REQUESTED,
        "cancelled": ExecutionSignal.CANCELLED,
        "canceled": ExecutionSignal.CANCELLED,
        "disconnected": ExecutionSignal.DISCONNECTED,
        "disconnect": ExecutionSignal.DISCONNECTED,
        "reconnected": ExecutionSignal.RECONNECTED,
        "resume_requested": ExecutionSignal.RECONNECTED,
    }
    try:
        return aliases[normalized]
    except KeyError as error:
        raise ValueError(f"unsupported execution signal: {value!r}") from error


def _live_correlated_supervisor(
    context: ExecutionContext,
    supervisor: object,
) -> bool:
    if type(supervisor) is not SolverSupervisor:
        return False
    context_supervisors = tuple(
        value for value in (context.supervisor, context.solver_supervisor) if value is not None
    )
    if any(value is not supervisor for value in context_supervisors):
        return False
    if (
        context.process_owned is not True
        or context.process_running is not True
        or context.correlation_verified is not True
    ):
        return False
    try:
        if supervisor.state is not SolverState.RUNNING:
            return False
        process = object.__getattribute__(supervisor, "_process")
        process_authority = object.__getattribute__(supervisor, "_process_authority")
        internal_record = object.__getattribute__(supervisor, "_process_record")
        if process is None or process_authority is None or not isinstance(internal_record, dict):
            return False
        pid = getattr(process, "pid", None)
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            return False
        record = SolverSupervisor._read_process_record(supervisor)
        if record != internal_record:
            return False
        metadata, _, authority = SolverSupervisor._validate_process_record(supervisor, record)
        try:
            if (
                not metadata.alive
                or metadata.return_code is not None
                or metadata.creation_identity == ""
            ):
                return False
            if metadata.creation_identity != record.get("process_creation_identity"):
                return False
            if record.get("pid") != pid:
                return False
            for field_name in ("case_id", "intent_id", "attempt_id"):
                expected = object.__getattribute__(supervisor, f"_{field_name}")
                if not isinstance(expected, str) or not expected.strip():
                    return False
                if record.get(field_name) != expected:
                    return False
                context_value = getattr(context, field_name)
                if context_value is not None and context_value != expected:
                    return False
            return True
        finally:
            authority.close()
    except Exception:
        return False


def decide_execution(
    signal: ExecutionSignal | str,
    context: ExecutionContext | None = None,
    *,
    supervisor: SolverSupervisor | None = None,
    solver_supervisor: SolverSupervisor | None = None,
) -> ExecutionDecision:
    """Decide timeout, cancel, disconnect, and resume behavior.

    Timeout and cancel produce an owned-process cancellation decision only when
    ownership and liveness are evidenced.  Disconnect holds all new work; a
    reconnect resumes monitoring only after case/attempt/process correlation is
    verified.
    """

    context = context or ExecutionContext()
    if not isinstance(context, ExecutionContext):
        raise TypeError("context must be an ExecutionContext")
    if (
        supervisor is not None
        and solver_supervisor is not None
        and supervisor is not solver_supervisor
    ):
        raise TypeError("supply only one solver supervisor")
    effective_supervisor = supervisor if supervisor is not None else solver_supervisor
    context_supervisor = context.supervisor or context.solver_supervisor
    if (
        effective_supervisor is not None
        and context_supervisor is not None
        and effective_supervisor is not context_supervisor
    ):
        raise TypeError("context and argument supervisors must identify the same authority")
    if effective_supervisor is None:
        effective_supervisor = context_supervisor
    normalized_signal = _execution_signal(signal)
    if normalized_signal in {ExecutionSignal.STARTED, ExecutionSignal.RUNNING}:
        return ExecutionDecision(
            signal=normalized_signal,
            action=ExecutionAction.MONITOR,
            reason="owned supervisor continues monitoring",
            process_owned=context.process_owned,
        )
    if normalized_signal is ExecutionSignal.COMPLETED:
        return ExecutionDecision(
            signal=normalized_signal,
            action=ExecutionAction.VALIDATE_OUTPUT,
            reason="normal completion still requires LOG/XPLT/result validation",
            process_owned=context.process_owned,
            requires_output_validation=True,
        )
    if normalized_signal is ExecutionSignal.PROCESS_EXITED:
        if context.exit_code == 0:
            return ExecutionDecision(
                signal=normalized_signal,
                action=ExecutionAction.VALIDATE_OUTPUT,
                reason="successful process exit still requires output validation",
                process_owned=context.process_owned,
                requires_output_validation=True,
            )
        return ExecutionDecision(
            signal=normalized_signal,
            action=ExecutionAction.STOP,
            reason="non-zero process exit requires failure classification",
            process_owned=context.process_owned,
        )
    if normalized_signal in {ExecutionSignal.TIMEOUT, ExecutionSignal.CANCEL_REQUESTED}:
        if _live_correlated_supervisor(context, effective_supervisor):
            return ExecutionDecision(
                signal=normalized_signal,
                action=ExecutionAction.CANCEL_OWNED_PROCESS,
                reason="cancel only the case-owned running process tree",
                process_owned=True,
            )
        return ExecutionDecision(
            signal=normalized_signal,
            action=ExecutionAction.STOP,
            reason="cannot cancel without verified case-owned process liveness",
            process_owned=context.process_owned,
        )
    if normalized_signal is ExecutionSignal.DISCONNECTED:
        return ExecutionDecision(
            signal=normalized_signal,
            action=ExecutionAction.HOLD_NO_NEW_WORK,
            reason="hold model changes and retries while the client is disconnected",
            process_owned=context.process_owned,
        )
    if normalized_signal is ExecutionSignal.RECONNECTED:
        if _live_correlated_supervisor(context, effective_supervisor):
            return ExecutionDecision(
                signal=normalized_signal,
                action=ExecutionAction.RESUME_MONITORING,
                reason="case, attempt, process, and event correlation is verified",
                process_owned=context.process_owned,
            )
        return ExecutionDecision(
            signal=normalized_signal,
            action=ExecutionAction.STOP,
            reason="reconnect correlation is not verified",
            process_owned=context.process_owned,
        )
    return ExecutionDecision(
        signal=normalized_signal,
        action=ExecutionAction.STOP,
        reason="terminal lifecycle signal requires stop",
        process_owned=context.process_owned,
    )


# Verb-oriented aliases keep the policy discoverable without duplicating logic.
next_intent_state = transition_intent
failure_route = route_failure
evaluate_proposal = decide_proposal
retry_decision = decide_retry
execution_decision = decide_execution
