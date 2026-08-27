"""Tests for the pure completed-FEB workflow state machine."""

import json
from dataclasses import FrozenInstanceError, dataclass

import pytest

from febio_cae_harness.workflow.state import (
    WorkflowIdentity,
    WorkflowPhase,
    WorkflowState,
    transition,
)

IDENTITY = WorkflowIdentity(case_id="case-a", intent_id="intent-a", attempt_id="attempt-1")


def test_phase_values_are_canonical_and_ordered() -> None:
    assert tuple(WorkflowPhase) == (
        WorkflowPhase.CREATED,
        WorkflowPhase.INSPECTED,
        WorkflowPhase.DERIVED,
        WorkflowPhase.PREFLIGHTED,
        WorkflowPhase.SOLVING,
        WorkflowPhase.VALIDATING,
        WorkflowPhase.REPORTED,
        WorkflowPhase.FAILED,
        WorkflowPhase.ASK_AND_BLOCK,
    )
    assert [phase.value for phase in WorkflowPhase] == [
        "CREATED",
        "INSPECTED",
        "DERIVED",
        "PREFLIGHTED",
        "SOLVING",
        "VALIDATING",
        "REPORTED",
        "FAILED",
        "ASK_AND_BLOCK",
    ]


def test_identity_and_state_are_immutable_and_validate_identity_fields() -> None:
    with pytest.raises(ValueError):
        WorkflowIdentity(case_id="", intent_id="intent-a", attempt_id="attempt-1")
    with pytest.raises(ValueError):
        WorkflowIdentity(case_id="case-a", intent_id=" ", attempt_id="attempt-1")

    state = WorkflowState(IDENTITY)
    assert state.state is WorkflowPhase.CREATED
    with pytest.raises(FrozenInstanceError):
        state.state = WorkflowPhase.INSPECTED  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        state.identity = WorkflowIdentity("case-b", "intent-a", "attempt-1")  # type: ignore[misc]


def test_state_normalizes_evidence_and_serializes_to_json_shape() -> None:
    state = WorkflowState(
        IDENTITY,
        evidence_ids=("source", "derived"),
    )
    payload = state.to_dict()
    assert payload == {
        "identity": {
            "case_id": "case-a",
            "intent_id": "intent-a",
            "attempt_id": "attempt-1",
        },
        "state": "CREATED",
        "evidence_ids": ["source", "derived"],
        "reason": None,
    }
    assert json.dumps(payload, sort_keys=True) == json.dumps(state.to_dict(), sort_keys=True)


def test_completed_feb_dag_advances_one_node_and_appends_ordered_evidence() -> None:
    state = WorkflowState(IDENTITY)
    for phase, evidence_id in (
        (WorkflowPhase.INSPECTED, "inspection"),
        (WorkflowPhase.DERIVED, "derived-feb"),
        (WorkflowPhase.PREFLIGHTED, "preflight"),
        (WorkflowPhase.SOLVING, "solver"),
        (WorkflowPhase.VALIDATING, "validation"),
        (WorkflowPhase.REPORTED, "report"),
    ):
        state = transition(state, phase, identity=IDENTITY, evidence_ids=(evidence_id,))

    assert state.state is WorkflowPhase.REPORTED
    assert state.evidence_ids == (
        "inspection",
        "derived-feb",
        "preflight",
        "solver",
        "validation",
        "report",
    )


def test_transition_rejects_skips_identity_mismatch_and_bad_evidence() -> None:
    state = WorkflowState(IDENTITY)
    with pytest.raises(ValueError):
        transition(state, WorkflowPhase.DERIVED, identity=IDENTITY)
    with pytest.raises(ValueError):
        transition(
            state,
            WorkflowPhase.INSPECTED,
            identity=WorkflowIdentity("case-b", "intent-a", "attempt-1"),
        )
    with pytest.raises(ValueError):
        transition(state, WorkflowPhase.INSPECTED, identity=IDENTITY, evidence_ids=("",))
    with pytest.raises(ValueError):
        transition(state, WorkflowPhase.INSPECTED, identity=IDENTITY, evidence_ids=("x", "x"))

    with pytest.raises(ValueError):
        transition(
            WorkflowState(IDENTITY, evidence_ids=("x",)),
            WorkflowPhase.INSPECTED,
            identity=IDENTITY,
            evidence_ids=("x",),
        )


def test_failure_is_explicit_and_terminal_states_cannot_transition() -> None:
    failed = transition(
        WorkflowState(IDENTITY),
        WorkflowPhase.FAILED,
        identity=IDENTITY,
        evidence_ids=("failure-log",),
        reason="solver reported a failure",
    )
    assert failed.state is WorkflowPhase.FAILED
    assert failed.reason == "solver reported a failure"
    with pytest.raises(ValueError):
        transition(failed, WorkflowPhase.CREATED, identity=IDENTITY)

    reported = WorkflowState(IDENTITY, WorkflowPhase.REPORTED)
    with pytest.raises(ValueError):
        transition(reported, WorkflowPhase.FAILED, identity=IDENTITY, reason="late failure")


@dataclass(frozen=True)
class AuthoritativeUnresolvedFact:
    condition: str
    authoritative: bool = True
    unresolved: bool = True


def test_ask_and_block_requires_explicit_authoritative_unresolved_fact() -> None:
    initial = WorkflowState(IDENTITY)
    fact = AuthoritativeUnresolvedFact("material")
    blocked = transition(
        initial,
        WorkflowPhase.ASK_AND_BLOCK,
        identity=IDENTITY,
        reason=fact,
    )
    assert blocked.state is WorkflowPhase.ASK_AND_BLOCK
    assert blocked.to_dict()["reason"] == {
        "authoritative": True,
        "condition": "material",
        "unresolved": True,
    }
    for reason in (
        "missing material",
        False,
        {"condition": "material", "authoritative": False},
        {"condition": "material"},
        {"condition": "material", "authoritative": True, "resolved": True},
    ):
        with pytest.raises(ValueError):
            transition(initial, WorkflowPhase.ASK_AND_BLOCK, identity=IDENTITY, reason=reason)

    with pytest.raises(ValueError):
        transition(blocked, WorkflowPhase.CREATED, identity=IDENTITY)
