"""Tests for the pure completed-FEB workflow state machine."""

import json
from copy import copy, deepcopy
from dataclasses import FrozenInstanceError, dataclass
from pathlib import Path
from typing import Any

import pytest

from febio_cae_harness.contracts import IntentContract, IntentState
from febio_cae_harness.evidence import EvidenceIntegrityError, EvidenceStore
from febio_cae_harness.workflow.state import (
    WorkflowIdentity,
    WorkflowPhase,
    WorkflowState,
    transition,
)
from febio_cae_harness.workspace import ValidatedCaseWorkspace

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


def _ask_store(
    tmp_path: Path,
    case_id: str = "case-a",
    *,
    unresolved: Any = ({"condition": "material", "authoritative": True, "source": "user"},),
    condition_sources: Any | None = None,
    state: IntentState = IntentState.ASK_AND_BLOCK,
) -> EvidenceStore:
    if condition_sources is None:
        condition_sources = {
            "material": {"authoritative": True, "source": "user"},
        }
    manager = ValidatedCaseWorkspace(tmp_path / "tool", tmp_path / "cae")
    case = manager.create_case(case_id)
    intent = IntentContract(
        unresolved=unresolved,
        condition_sources=condition_sources,
        state=state,
    )
    return EvidenceStore(case, intent)


def test_ask_and_block_rejects_caller_boolean_authority(tmp_path: Path) -> None:
    initial = WorkflowState(IDENTITY)
    for reason in (
        {"authoritative": True, "unresolved": True},
        AuthoritativeUnresolvedFact("material"),
    ):
        with pytest.raises(ValueError):
            transition(initial, WorkflowPhase.ASK_AND_BLOCK, identity=IDENTITY, reason=reason)


def test_ask_and_block_requires_live_snapshot_and_projects_canonical_fields(
    tmp_path: Path,
) -> None:
    store = _ask_store(tmp_path)
    snapshot = store.issue_intent_snapshot()
    blocked = transition(
        WorkflowState(IDENTITY),
        WorkflowPhase.ASK_AND_BLOCK,
        identity=IDENTITY,
        reason=snapshot,
    )

    assert blocked.to_dict()["reason"] == {
        "condition_sources": [
            {
                "authoritative": True,
                "condition": "material",
                "resolved": False,
                "source": "user",
            }
        ],
        "unresolved": [
            {
                "authoritative": True,
                "condition": "material",
                "resolved": False,
                "source": "user",
            }
        ],
    }

    invalid_snapshots = (
        _ask_store(tmp_path / "bound", state=IntentState.BOUND).issue_intent_snapshot(),
        _ask_store(tmp_path / "unresolved", unresolved=()).issue_intent_snapshot(),
        _ask_store(tmp_path / "sources", condition_sources=()).issue_intent_snapshot(),
    )
    for invalid_snapshot in invalid_snapshots:
        with pytest.raises(ValueError):
            transition(
                WorkflowState(IDENTITY),
                WorkflowPhase.ASK_AND_BLOCK,
                identity=IDENTITY,
                reason=invalid_snapshot,
            )


def test_ask_and_block_rejects_non_authoritative_missing_condition_record(
    tmp_path: Path,
) -> None:
    store = _ask_store(
        tmp_path,
        unresolved=({"condition": "material", "authoritative": False, "source": "guess"},),
    )

    with pytest.raises(ValueError):
        transition(
            WorkflowState(IDENTITY),
            WorkflowPhase.ASK_AND_BLOCK,
            identity=IDENTITY,
            reason=store.issue_intent_snapshot(),
        )


def test_ask_and_block_rejects_source_label_without_authority_record(
    tmp_path: Path,
) -> None:
    store = _ask_store(
        tmp_path,
        unresolved=("material",),
        condition_sources={"material": "user"},
    )

    with pytest.raises(ValueError):
        transition(
            WorkflowState(IDENTITY),
            WorkflowPhase.ASK_AND_BLOCK,
            identity=IDENTITY,
            reason=store.issue_intent_snapshot(),
        )


def test_ask_and_block_rejects_copied_forged_and_foreign_snapshots(tmp_path: Path) -> None:
    store = _ask_store(tmp_path)
    snapshot = store.issue_intent_snapshot()
    initial = WorkflowState(IDENTITY)
    with pytest.raises(TypeError):
        copy(snapshot)
    with pytest.raises(TypeError):
        deepcopy(snapshot)

    forged = object.__new__(type(snapshot))
    with pytest.raises((TypeError, ValueError, EvidenceIntegrityError)):
        transition(initial, WorkflowPhase.ASK_AND_BLOCK, identity=IDENTITY, reason=forged)

    foreign = _ask_store(tmp_path / "foreign", "case-b").issue_intent_snapshot()
    with pytest.raises(ValueError):
        transition(initial, WorkflowPhase.ASK_AND_BLOCK, identity=IDENTITY, reason=foreign)


def test_ask_and_block_binding_rejects_rebinding_and_stale_use(tmp_path: Path) -> None:
    store = _ask_store(tmp_path)
    blocked = transition(
        WorkflowState(IDENTITY),
        WorkflowPhase.ASK_AND_BLOCK,
        identity=IDENTITY,
        reason=store.issue_intent_snapshot(),
    )
    object.__setattr__(blocked, "_intent_snapshot", object())
    with pytest.raises((TypeError, ValueError, EvidenceIntegrityError)):
        blocked.to_dict()

    rebound = transition(
        WorkflowState(IDENTITY),
        WorkflowPhase.ASK_AND_BLOCK,
        identity=IDENTITY,
        reason=store.issue_intent_snapshot(),
    )
    object.__setattr__(rebound, "reason", {"unresolved": ["forged"]})
    with pytest.raises((TypeError, ValueError, EvidenceIntegrityError)):
        rebound.to_dict()

    stale_store = _ask_store(tmp_path / "stale")
    stale = transition(
        WorkflowState(IDENTITY),
        WorkflowPhase.ASK_AND_BLOCK,
        identity=IDENTITY,
        reason=stale_store.issue_intent_snapshot(),
    )
    payload = stale_store.intent.to_dict()
    payload["unresolved"] = ["changed"]
    stale_store.case_workspace._write_control_text("intent.json", json.dumps(payload))
    with pytest.raises(EvidenceIntegrityError):
        stale.to_dict()

    with pytest.raises(ValueError):
        WorkflowState(
            IDENTITY,
            WorkflowPhase.ASK_AND_BLOCK,
            reason={"unresolved": ["material"], "condition_sources": ["requirements"]},
        )
