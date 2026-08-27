"""Pure, evidence-driven policy for the CAE case agent.

The policy layer consumes :class:`~febio_cae_harness.contracts.IntentContract`
and immutable evidence-shaped values.  It returns decisions only: it does not
start a process, modify a case, inspect geometry, or infer physical meaning.
Those boundaries make the functions deterministic and keep execution authority
in the solver and workspace phases.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from math import isfinite
from types import MappingProxyType
from typing import Any, NoReturn, Self, cast

from ..contracts import IntentContract, IntentState, JSONValue
from ..evidence import EvidenceIntegrityError, IntentSnapshotAuthority
from ..solver.supervisor import SolverSupervisor
from ..solver.types import SolverClassification, SolverRunResult, SolverState

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
    if isinstance(value, Mapping):
        if "authoritative" in value:
            return _as_bool(value["authoritative"])
        if "value" in value and _source_is_authoritative(value["value"]):
            return True
        for key in ("authority", "source_type", "kind"):
            if _normalise_token(value.get(key)) in {
                "authoritative",
                "user",
                "user_provided",
                "case_manifest",
                "input",
                "specification",
                "contract",
            }:
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_source_is_authoritative(item) for item in value)
    return _normalise_token(value) in {
        "authoritative",
        "user",
        "user_provided",
        "case_manifest",
        "input",
        "specification",
        "contract",
    }


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


def unresolved_authoritative_conditions(
    intent: IntentContract,
    additional: Iterable[PhysicalConditionEvidence] = (),
) -> tuple[PhysicalConditionEvidence, ...]:
    """Return only explicit, unresolved, authoritative physical conditions.

    The ``unresolved`` field remains opaque in the canonical contract.  This
    adapter recognizes only explicit authority markers on a condition record or
    its matching ``condition_sources`` entry.  Bare names and guessed sources
    therefore remain ``GATHERING`` and can never trigger ``ASK_AND_BLOCK``.
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
    result: list[PhysicalConditionEvidence] = []
    for record in _condition_records(intent.unresolved):
        name = _condition_name(record)
        if name is None:
            continue
        status = _normalise_token(record.get("status"))
        if _as_bool(record.get("resolved")) or status in {"resolved", "known", "specified"}:
            continue
        explicitly_false = "authoritative" in record and record["authoritative"] is False
        authoritative = not explicitly_false and (
            _as_bool(record.get("authoritative"))
            or _source_is_authoritative(record.get("source"))
            or _source_is_authoritative(sources.get(name.casefold()))
        )
        if authoritative:
            source_value = record.get("source")
            source = source_value if isinstance(source_value, str) else None
            matching_source = sources.get(name.casefold())
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
    for condition in additional:
        if condition.authoritative and condition.unresolved:
            result.append(condition)
    return tuple(result)


def has_authoritative_unresolved(intent: IntentContract) -> bool:
    """Return whether the intent contains an explicit blocking condition."""

    return bool(unresolved_authoritative_conditions(intent))


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
        blocking = unresolved_authoritative_conditions(persisted_intent)
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
        return self._validated_record()[7]

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
    intent: IntentContract | None = None,
) -> FailureRouting:
    """Route an evidence-backed failure to mesh, nonlinear, retry, or stop.

    An ``ASK_AND_BLOCK`` route is possible only when the evidence explicitly
    requests a physical decision *and* the intent has an unresolved
    authoritative condition.  Negative Jacobian and nonlinear evidence retain
    their dedicated diagnostic routes otherwise.
    """

    classification = classify_failure(evidence)
    blocking = (
        unresolved_authoritative_conditions(intent)
        if intent is not None and _physical_request(evidence)
        else ()
    )
    if blocking:
        return FailureRouting(
            classification=classification,
            route=FailureRoute.ASK_AND_BLOCK,
            rationale="an authoritative physical condition is unresolved",
            retryable=False,
            blocking_conditions=blocking,
        )
    if _physical_request(evidence):
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
    state_authority: IntentStateAuthority | IntentContract,
    proposal: Proposal,
    proposal_authority: ProposalAuthority | ProposalValidationReceipt | None = None,
    *,
    validation_receipt: ProposalValidationReceipt | None = None,
    authority: ProposalAuthority | ProposalValidationReceipt | None = None,
    validation_passed: bool = False,
) -> ProposalDecision:
    """Decide a proposal; only a live manager authority can produce AUTO_APPLY."""

    legacy = isinstance(state_authority, IntentContract)
    if legacy:
        intent = cast(IntentContract, state_authority)
    else:
        state_record = _state_authority_record(state_authority)
        intent = state_record[3]
    if not isinstance(proposal, Proposal):
        raise TypeError("proposal must be a Proposal")

    supplied = tuple(
        token for token in (proposal_authority, validation_receipt, authority) if token is not None
    )
    if len(supplied) > 1:
        raise TypeError("supply only one proposal authority or validation receipt")
    token_record: _ProposalTokenRecord | None = None
    if supplied:
        if legacy:
            raise TypeError("proposal authorization requires an IntentStateAuthority")
        token_record = _proposal_token_record(
            supplied[0],
            manager=cast(ProposalAuthorityManager, _proposal_token_record_for(supplied[0])[1]),
            state_authority=cast(IntentStateAuthority, state_authority),
            proposal=proposal,
        )
        intent = token_record[2].intent

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

    blocking = unresolved_authoritative_conditions(intent)
    if intent.state is IntentState.ASK_AND_BLOCK and not blocking:
        return result(
            ProposalAction.REJECT,
            "ASK_AND_BLOCK state lacks a current authoritative condition record",
        )
    scope = _proposal_scope(intent, proposal)
    if scope == "physical":
        if blocking:
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
    return result(ProposalAction.AUTO_APPLY, "declared mesh change has manager authority")


@dataclass(frozen=True, slots=True)
class RetryRecord:
    """One consumed retry-budget entry."""

    failure: FailureClass
    attempt_id: str | None = None
    proposal_id: str | None = None
    evidence_ids: tuple[str, ...] = ()

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
    return (failure, record.attempt_id, record.proposal_id, evidence_ids)


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


@dataclass(frozen=True, slots=True)
class RetryResult:
    """Retry decision and the resulting immutable ledger."""

    decision: RetryDecision
    ledger: RetryLedger
    failure: FailureClass
    reason: str
    route: FailureRoute

    @property
    def action(self) -> RetryDecision:
        return self.decision

    @property
    def next_ledger(self) -> RetryLedger:
        return self.ledger

    @property
    def retry_allowed(self) -> bool:
        return self.decision is RetryDecision.RETRY


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


_SOLVER_FAILURE_CLASSIFICATIONS: Mapping[SolverClassification, FailureClass] = MappingProxyType(
    {
        SolverClassification.SUCCESS: FailureClass.NONE,
        SolverClassification.INIT_ONLY: FailureClass.UNKNOWN,
        SolverClassification.MISSING_OUTPUT: FailureClass.MISSING_OUTPUT,
        SolverClassification.FATAL: FailureClass.FATAL,
        SolverClassification.NEGATIVE_JACOBIAN: FailureClass.NEGATIVE_JACOBIAN,
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

    authority = state_authority
    if authority is None and type(intent) is IntentStateAuthority:
        authority = intent
    route_intent: IntentContract | None = intent if type(intent) is IntentContract else None
    authority_record: _IntentStateRecord | None = None
    if authority is not None:
        try:
            authority_record = _state_authority_record(authority, bound=True)
            route_intent = authority_record[3]
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

    ask_and_block = (
        authority_record is not None and authority_record[5] is IntentState.ASK_AND_BLOCK
    ) or (route_intent is not None and route_intent.state is IntentState.ASK_AND_BLOCK)
    if ask_and_block and not routing.blocking_conditions:
        return stopped("ASK_AND_BLOCK state lacks a current authoritative condition record")
    if authority is not None and authority_record is None:
        return stopped("intent state authority is stale or invalid")
    if routing.route is FailureRoute.ASK_AND_BLOCK:
        return RetryResult(
            decision=RetryDecision.ASK_AND_BLOCK,
            ledger=ledger,
            failure=classification,
            reason=routing.rationale,
            route=routing.route,
        )
    if classification is FailureClass.DISCONNECTED:
        return RetryResult(
            decision=RetryDecision.WAIT_FOR_RECONNECT,
            ledger=ledger,
            failure=classification,
            reason=routing.rationale,
            route=routing.route,
        )
    if proposal is not None:
        if route_intent is None:
            raise ValueError("intent is required when evaluating a proposal")
        proposal_decision = decide_proposal(
            authority if authority is not None else route_intent,
            proposal,
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
    if not routing.retryable:
        return stopped(routing.rationale)
    if authority is None or authority_record is None:
        return stopped("retry requires a live BOUND intent state authority")
    try:
        _validated_retry_ledger(ledger, authority)
    except (EvidenceIntegrityError, TypeError):
        return stopped("retry requires a registry-issued ledger bound to the live intent")
    if not _solver_result_is_authoritative(effective_supervisor, effective_result):
        return stopped("retry requires the exact failed result issued by its solver supervisor")
    if object.__getattribute__(effective_supervisor, "_case_id") != authority_record[1].case_id:
        return stopped("solver result is not correlated to the live intent case")
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
    attempt_id = getattr(effective_supervisor, "_attempt_id", None)
    record = RetryRecord(
        failure=classification,
        attempt_id=attempt_id if isinstance(attempt_id, str) else None,
        proposal_id=proposal.proposal_id if proposal is not None else None,
        evidence_ids=evidence_ids,
    )
    next_ledger = _issue_retry_ledger(
        authority,
        used=ledger.used + 1,
        records=ledger.records + (record,),
    )
    return RetryResult(
        decision=RetryDecision.RETRY,
        ledger=next_ledger,
        failure=classification,
        reason=routing.rationale,
        route=routing.route,
    )


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
