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
from typing import Self, cast

from ..contracts import IntentContract, IntentState, JSONValue

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
    "IntentState",
    "PhysicalConditionEvidence",
    "Proposal",
    "ProposalAction",
    "ProposalClass",
    "ProposalDecision",
    "ProposalKind",
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
    return _normalise_token(value) in {
        "authoritative",
        "user",
        "user_provided",
        "case_manifest",
        "input",
        "specification",
        "contract",
    }


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


@dataclass(frozen=True, slots=True)
class StateTransition:
    """Immutable state transition result, including the updated intent."""

    intent: IntentContract
    previous: IntentState
    current: IntentState
    reason: str
    blocking_conditions: tuple[PhysicalConditionEvidence, ...] = ()

    @property
    def changed(self) -> bool:
        return self.previous is not self.current

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


def transition_intent(
    intent: IntentContract,
    *,
    conditions_complete: bool = False,
    additional_conditions: Iterable[PhysicalConditionEvidence] = (),
) -> StateTransition:
    """Compute the next intent state without mutating the canonical contract.

    Completeness is a caller-supplied, authoritative fact.  The policy does
    not infer it from geometry, field names, or defaults.
    """

    if not isinstance(intent, IntentContract):
        raise TypeError("intent must be an IntentContract")
    if not isinstance(conditions_complete, bool):
        raise TypeError("conditions_complete must be a bool")
    complete = conditions_complete
    blocking = unresolved_authoritative_conditions(intent, additional_conditions)
    previous = intent.state
    if blocking:
        current = IntentState.ASK_AND_BLOCK
        reason = "authoritative physical conditions remain unresolved"
    elif previous is IntentState.BOUND:
        # A bound intent remains bound until a new blocking condition appears.
        current = IntentState.BOUND
        reason = "intent remains bound"
    elif complete:
        current = IntentState.BOUND
        reason = "authoritative intent conditions are complete"
    else:
        current = IntentState.GATHERING
        reason = "authoritative intent conditions are still being gathered"
    updated = intent if current is previous else replace(intent, state=current)
    return StateTransition(
        intent=updated,
        previous=previous,
        current=current,
        reason=reason,
        blocking_conditions=blocking,
    )


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
    intent: IntentContract,
    proposal: Proposal,
    *,
    validation_passed: bool = False,
) -> ProposalDecision:
    """Apply debug-class policy while preserving the physical-condition boundary."""

    if not isinstance(intent, IntentContract):
        raise TypeError("intent must be an IntentContract")
    if not isinstance(proposal, Proposal):
        raise TypeError("proposal must be a Proposal")
    blocking = unresolved_authoritative_conditions(intent)
    if intent.state is IntentState.ASK_AND_BLOCK and not blocking:
        return ProposalDecision(
            proposal=proposal,
            action=ProposalAction.REJECT,
            reason="ASK_AND_BLOCK state lacks a current authoritative condition record",
            next_state=intent.state,
        )
    proposal_class = proposal.classification
    physical_change = (
        proposal.requires_physical_decision or proposal_class is ProposalClass.INTENT_CHANGING
    )
    if physical_change:
        if blocking:
            return ProposalDecision(
                proposal=proposal,
                action=ProposalAction.ASK_AND_BLOCK,
                reason="authoritative physical condition requires a user decision",
                next_state=IntentState.ASK_AND_BLOCK,
                blocking_conditions=blocking,
            )
        return ProposalDecision(
            proposal=proposal,
            action=ProposalAction.REJECT,
            reason="physical change has no unresolved authoritative condition to ask about",
            next_state=intent.state,
        )

    has_evidence = bool(proposal.evidence_ids)
    authorized = proposal.authorized or proposal.within_contract or not proposal.changes
    if not has_evidence or not authorized:
        return ProposalDecision(
            proposal=proposal,
            action=ProposalAction.REJECT,
            reason="proposal lacks explicit evidence or contract authorization",
            next_state=intent.state,
        )
    if proposal_class is ProposalClass.INTENT_SENSITIVE and not validation_passed:
        return ProposalDecision(
            proposal=proposal,
            action=ProposalAction.REQUIRE_VALIDATION,
            reason="intent-sensitive change requires result and intent validation",
            next_state=intent.state,
        )
    return ProposalDecision(
        proposal=proposal,
        action=ProposalAction.AUTO_APPLY,
        reason=(
            "intent-preserving evidence-backed proposal is within contract"
            if proposal_class is ProposalClass.INTENT_PRESERVING
            else "intent-sensitive proposal passed validation"
        ),
        next_state=intent.state,
    )


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
    def from_intent(cls, intent: IntentContract) -> Self:
        if not isinstance(intent, IntentContract):
            raise TypeError("intent must be an IntentContract")
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


def decide_retry(
    failure: FailureEvidence | Mapping[str, object] | FailureClass | str | None,
    ledger: RetryLedger,
    *,
    intent: IntentContract | None = None,
    proposal: Proposal | None = None,
    validation_passed: bool = False,
) -> RetryResult:
    """Classify a failure and account for exactly one permitted retry."""

    if not isinstance(ledger, RetryLedger):
        raise TypeError("ledger must be a RetryLedger")
    routing = route_failure(failure, intent=intent)
    classification = routing.classification
    if (
        intent is not None
        and intent.state is IntentState.ASK_AND_BLOCK
        and not routing.blocking_conditions
    ):
        return RetryResult(
            decision=RetryDecision.STOP,
            ledger=ledger,
            failure=classification,
            reason="ASK_AND_BLOCK state lacks a current authoritative condition record",
            route=FailureRoute.STOP,
        )
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
        if intent is None:
            raise ValueError("intent is required when evaluating a proposal")
        proposal_decision = decide_proposal(
            intent,
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
        return RetryResult(
            decision=RetryDecision.STOP,
            ledger=ledger,
            failure=classification,
            reason=routing.rationale,
            route=FailureRoute.STOP,
        )
    if not ledger.can_retry:
        return RetryResult(
            decision=RetryDecision.BUDGET_EXHAUSTED,
            ledger=ledger,
            failure=classification,
            reason="declared retry budget is exhausted",
            route=FailureRoute.STOP,
        )
    evidence_ids: tuple[str, ...] = ()
    if isinstance(failure, FailureEvidence):
        evidence_ids = failure.evidence_ids
        if failure.evidence_id is not None:
            evidence_ids = evidence_ids + (failure.evidence_id,)
    elif isinstance(failure, Mapping):
        raw_ids = failure.get("evidence_ids", ())
        if isinstance(raw_ids, (list, tuple)):
            evidence_ids = tuple(item for item in raw_ids if isinstance(item, str))
        raw_id = failure.get("evidence_id")
        if isinstance(raw_id, str):
            evidence_ids = evidence_ids + (raw_id,)
    next_ledger = ledger.consume(
        failure=classification,
        proposal_id=proposal.proposal_id if proposal is not None else None,
        evidence_ids=evidence_ids,
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


def decide_execution(
    signal: ExecutionSignal | str,
    context: ExecutionContext | None = None,
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
        if context.process_owned and context.process_running:
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
        if context.correlation_verified and context.process_owned:
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
