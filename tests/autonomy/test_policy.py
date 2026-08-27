from __future__ import annotations

import inspect
import json
import pickle
from copy import copy, deepcopy
from dataclasses import is_dataclass, replace
from pathlib import Path

import pytest

from febio_cae_harness.autonomy import (
    ExecutionAction,
    ExecutionContext,
    ExecutionSignal,
    FailureClass,
    FailureEvidence,
    FailureRoute,
    IntentState,
    Proposal,
    ProposalAction,
    ProposalAuthority,
    ProposalAuthorityManager,
    ProposalClass,
    RetryDecision,
    RetryLedger,
    StateTransition,
    classify_failure,
    decide_execution,
    decide_proposal,
    decide_retry,
    route_failure,
    transition_intent,
    unresolved_authoritative_conditions,
)
from febio_cae_harness.contracts import IntentContract
from febio_cae_harness.evidence import (
    EvidenceIntegrityError,
    EvidenceStore,
    IntentSnapshotAuthority,
)
from febio_cae_harness.workspace import ValidatedCaseWorkspace


def bound_intent(**overrides: object) -> IntentContract:
    values: dict[str, object] = {
        "engineering_question": {
            "source": "user",
            "value": "What is the displacement?",
        },
        "units": {"source": "user", "value": "mm"},
        "material": {"source": "user", "value": "steel"},
        "loads": ({"source": "user", "value": "100 N"},),
        "constraints": ({"source": "user", "value": "fixed"},),
        "contact": {"source": "user", "value": "none"},
        "analysis_step": {"source": "user", "value": 1},
        "roi": ({"source": "user", "value": "all"},),
        "evaluation_quantities": ({"source": "user", "value": "displacement"},),
        "condition_sources": {
            "engineering_question": {"authoritative": True, "source": "user"},
            "units": {"authoritative": True, "source": "user"},
            "material": {"authoritative": True, "source": "user"},
            "loads": {"authoritative": True, "source": "user"},
            "constraints": {"authoritative": True, "source": "user"},
            "contact": {"authoritative": True, "source": "user"},
            "analysis_step": {"authoritative": True, "source": "user"},
            "roi": {"authoritative": True, "source": "user"},
            "evaluation_quantities": {"authoritative": True, "source": "user"},
        },
        "state": IntentState.GATHERING,
    }
    values.update(overrides)
    return IntentContract(**values)  # type: ignore[arg-type]


def intent_snapshot(
    intent: IntentContract,
    tmp_path: Path,
    suffix: str = "a",
) -> IntentSnapshotAuthority:
    workspace = ValidatedCaseWorkspace(
        tmp_path / f"tool-{suffix}",
        tmp_path / f"02_CAE-{suffix}",
    )
    case = workspace.create_case(f"case-{suffix}")
    return EvidenceStore(case, intent).issue_intent_snapshot()


def run_transition(snapshot: IntentSnapshotAuthority) -> StateTransition:
    try:
        return transition_intent(snapshot)
    except Exception as error:
        raise AssertionError("transition requires a live intent snapshot") from error


def test_transition_binds_only_when_complete_and_blocks_authoritative_unknowns(
    tmp_path: Path,
) -> None:
    intent = bound_intent()
    snapshot = intent_snapshot(intent, tmp_path)
    transition = run_transition(snapshot)
    assert transition.previous is IntentState.GATHERING
    assert transition.current is IntentState.BOUND
    assert transition.intent.state is IntentState.BOUND

    unresolved = bound_intent(
        unresolved=({"condition": "contact", "authoritative": True, "source": "user"},),
    )
    blocked = run_transition(intent_snapshot(unresolved, tmp_path, "blocked"))
    assert blocked.current is IntentState.ASK_AND_BLOCK
    assert blocked.intent.state is IntentState.ASK_AND_BLOCK


def test_non_authoritative_unknowns_do_not_trigger_ask_and_block(tmp_path: Path) -> None:
    intent = bound_intent(
        unresolved=({"condition": "contact", "authoritative": False, "source": "guess"},),
    )
    assert unresolved_authoritative_conditions(intent) == ()
    assert run_transition(intent_snapshot(intent, tmp_path)).current is IntentState.BOUND


def test_condition_source_mapping_can_authorize_a_named_unresolved_condition(
    tmp_path: Path,
) -> None:
    intent = bound_intent(
        unresolved=("contact",),
        condition_sources={"contact": "user"},
    )
    conditions = unresolved_authoritative_conditions(intent)
    assert len(conditions) == 1
    assert conditions[0].condition == "contact"
    assert run_transition(intent_snapshot(intent, tmp_path)).current is IntentState.ASK_AND_BLOCK


def test_conditions_complete_flag_cannot_bind_empty_or_stale_intent(tmp_path: Path) -> None:
    signature = inspect.signature(transition_intent)
    assert "conditions_complete" not in signature.parameters
    assert "additional_conditions" not in signature.parameters

    complete_snapshot = intent_snapshot(bound_intent(), tmp_path)
    with pytest.raises(TypeError):
        transition_intent(complete_snapshot, conditions_complete=True)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        transition_intent(
            complete_snapshot,
            additional_conditions=(
                {"condition": "contact", "authoritative": True, "source": "caller"},
            ),
        )  # type: ignore[call-arg]

    for forged in (bound_intent(), True, False, {}, {"state": "BOUND"}):
        with pytest.raises((TypeError, EvidenceIntegrityError)):
            transition_intent(forged)  # type: ignore[arg-type]

    empty = IntentContract()
    assert (
        run_transition(intent_snapshot(empty, tmp_path, "empty")).current is IntentState.GATHERING
    )

    incomplete = bound_intent(contact=None)
    assert (
        run_transition(intent_snapshot(incomplete, tmp_path, "incomplete")).current
        is IntentState.GATHERING
    )

    stale = IntentContract(state=IntentState.BOUND)
    assert (
        run_transition(intent_snapshot(stale, tmp_path, "stale")).current is IntentState.GATHERING
    )

    sources = dict(bound_intent().condition_sources)  # type: ignore[arg-type]
    sources["material"] = {"authoritative": True, "source": "user", "stale": True}
    stale_evidence = bound_intent(
        condition_sources=sources,
        state=IntentState.BOUND,
    )
    stale_snapshot = intent_snapshot(stale_evidence, tmp_path, "stale-evidence")
    assert run_transition(stale_snapshot).current is IntentState.GATHERING


def test_transition_authority_is_opaque_and_live_bound(tmp_path: Path) -> None:
    snapshot = intent_snapshot(bound_intent(), tmp_path)
    transition = run_transition(snapshot)

    assert type(transition) is StateTransition
    assert not is_dataclass(transition)
    with pytest.raises(TypeError):
        StateTransition()
    forged = object.__new__(StateTransition)
    with pytest.raises(EvidenceIntegrityError):
        _ = forged.current
    with pytest.raises(TypeError):
        type("ForgedStateTransition", (StateTransition,), {})
    with pytest.raises(TypeError):
        copy(transition)
    with pytest.raises(TypeError):
        deepcopy(transition)
    with pytest.raises(TypeError):
        pickle.dumps(transition)
    with pytest.raises(TypeError):
        replace(transition, current=IntentState.ASK_AND_BLOCK)  # type: ignore[type-var]
    with pytest.raises(AttributeError):
        transition.current = IntentState.ASK_AND_BLOCK  # type: ignore[misc]
    foreign_snapshot = intent_snapshot(bound_intent(), tmp_path, "foreign")
    with pytest.raises(EvidenceIntegrityError):
        transition._validated_for(foreign_snapshot)

    store = object.__getattribute__(snapshot, "_store")
    payload = json.loads(store.intent_path.read_text(encoding="utf-8"))
    payload["engineering_question"] = "tampered"
    store.intent_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(EvidenceIntegrityError):
        transition_intent(snapshot)
    with pytest.raises(EvidenceIntegrityError):
        _ = transition.current


def test_failure_classification_is_explicit_and_deterministic() -> None:
    assert (
        classify_failure(FailureEvidence(negative_jacobian=True, source="LOG"))
        is FailureClass.NEGATIVE_JACOBIAN
    )
    assert classify_failure({"nonlinear_convergence": True}) is FailureClass.NONLINEAR_CONVERGENCE
    assert classify_failure({"timeout": True}) is FailureClass.TIMEOUT
    assert classify_failure({"message": "unrelated text"}) is FailureClass.UNKNOWN
    assert classify_failure({"failure_class": "NONE"}) is FailureClass.NONE


def test_negative_jacobian_and_nonlinear_failures_have_distinct_routes() -> None:
    negative_jacobian = route_failure(
        FailureEvidence(
            negative_jacobian=True,
            source="LOG",
            phase="initial",
            element_ids=(12,),
            all_integration_points_checked=True,
        )
    )
    nonlinear = route_failure(FailureEvidence(nonlinear_convergence=True, source="LOG"))
    assert negative_jacobian.route is FailureRoute.MESH_DIAGNOSTIC
    assert nonlinear.route is FailureRoute.NONLINEAR_DIAGNOSTIC


def test_proposal_policy_respects_debug_class_and_physical_boundary(tmp_path: Path) -> None:
    intent = bound_intent(
        allowed_mesh_changes={"mesh": {"element_ids": [1], "target_size": 0.5}},
        allowed_numerical_changes={"numerical": {"time_step": 0.1}},
    )
    state_authority = run_transition(intent_snapshot(intent, tmp_path, "proposal"))
    manager = ProposalAuthorityManager(state_authority)
    preserving = Proposal(
        proposal_id="mesh-1",
        proposal_class=ProposalClass.INTENT_PRESERVING,
        rationale="repair an evidenced local element",
        evidence_ids=("log-1",),
        changes={"mesh": {"element_ids": (1,)}},
        authorized=True,
    )
    preserving_authority = manager.issue(preserving)
    assert (
        decide_proposal(state_authority, preserving, preserving_authority).action
        is ProposalAction.AUTO_APPLY
    )

    sensitive = Proposal(
        proposal_id="solver-1",
        proposal_class=ProposalClass.INTENT_SENSITIVE,
        rationale="change a numerical control",
        evidence_ids=("log-2",),
        changes={"numerical": {"time_step": 0.1}},
        authorized=True,
    )
    sensitive_authority = manager.issue(sensitive)
    assert (
        decide_proposal(state_authority, sensitive, sensitive_authority).action
        is ProposalAction.REQUIRE_VALIDATION
    )
    with pytest.raises(EvidenceIntegrityError):
        manager.validate(sensitive_authority)
    assert (
        decide_proposal(
            state_authority, sensitive, sensitive_authority, validation_passed=True
        ).action
        is ProposalAction.REQUIRE_VALIDATION
    )

    changing = Proposal(
        proposal_id="load-1",
        proposal_class=ProposalClass.INTENT_CHANGING,
        rationale="change a load",
        requires_physical_decision=True,
    )
    assert decide_proposal(state_authority, changing).action is ProposalAction.REJECT
    unresolved = bound_intent(
        unresolved=({"condition": "load", "authoritative": True, "source": "user"},),
    )
    unresolved_state = run_transition(intent_snapshot(unresolved, tmp_path, "proposal-blocked"))
    assert decide_proposal(unresolved_state, changing).action is ProposalAction.ASK_AND_BLOCK


def test_proposal_authority_scope_exploits_fail_closed(tmp_path: Path) -> None:
    cases = (
        (
            "empty",
            bound_intent(),
            Proposal(
                proposal_id="empty",
                proposal_class=ProposalClass.INTENT_PRESERVING,
                evidence_ids=("caller-assertion",),
                authorized=True,
            ),
            ProposalAction.REJECT,
        ),
        (
            "undeclared-mesh",
            bound_intent(
                allowed_mesh_changes={"mesh": {"element_ids": [1], "target_size": 0.5}},
            ),
            Proposal(
                proposal_id="undeclared-mesh",
                proposal_class=ProposalClass.INTENT_PRESERVING,
                evidence_ids=("caller-assertion",),
                changes={"mesh": {"element_ids": (2,), "target_size": 0.5}},
                authorized=True,
                within_contract=True,
            ),
            ProposalAction.REJECT,
        ),
        (
            "class-mismatch",
            bound_intent(allowed_numerical_changes={"numerical": {"time_step": 0.1}}),
            Proposal(
                proposal_id="class-mismatch",
                proposal_class=ProposalClass.INTENT_PRESERVING,
                evidence_ids=("caller-assertion",),
                changes={"numerical": {"time_step": 0.1}},
                authorized=True,
                within_contract=True,
            ),
            ProposalAction.REQUIRE_VALIDATION,
        ),
    )
    for suffix, intent, proposal, expected in cases:
        state = run_transition(intent_snapshot(intent, tmp_path, suffix))
        authority = ProposalAuthorityManager(state).issue(proposal)
        assert decide_proposal(state, proposal, authority).action is expected

    numerical = Proposal(
        proposal_id="evidence-free-validate",
        proposal_class=ProposalClass.INTENT_SENSITIVE,
        evidence_ids=("caller-assertion",),
        changes={"numerical": {"time_step": 0.1}},
        authorized=True,
    )
    numerical_state = run_transition(
        intent_snapshot(
            bound_intent(allowed_numerical_changes={"numerical": {"time_step": 0.1}}),
            tmp_path,
            "evidence-free-validate",
        )
    )
    numerical_manager = ProposalAuthorityManager(numerical_state)
    numerical_authority = numerical_manager.issue(numerical)
    with pytest.raises(EvidenceIntegrityError):
        numerical_manager.validate(numerical_authority)
    assert (
        decide_proposal(
            numerical_state, numerical, numerical_authority, validation_passed=True
        ).action
        is ProposalAction.REQUIRE_VALIDATION
    )


def test_proposal_authority_requires_bound_state_and_exact_inputs(tmp_path: Path) -> None:
    intent = bound_intent(allowed_mesh_changes={"mesh": {"element_ids": [1]}})
    gathering = run_transition(intent_snapshot(bound_intent(contact=None), tmp_path, "gathering"))
    with pytest.raises(EvidenceIntegrityError):
        ProposalAuthorityManager(gathering)
    state = run_transition(intent_snapshot(intent, tmp_path, "required"))
    proposal = Proposal(
        proposal_id="required",
        proposal_class=ProposalClass.INTENT_PRESERVING,
        evidence_ids=("log-1",),
        changes={"mesh": {"element_ids": (1,)}},
    )
    assert (
        decide_proposal(state, proposal, validation_passed=True).action
        is ProposalAction.REQUIRE_VALIDATION
    )
    authority = ProposalAuthorityManager(state).issue(proposal)
    assert decide_proposal(state, proposal, authority).action is ProposalAction.AUTO_APPLY
    with pytest.raises(TypeError):
        decide_proposal(state, proposal, proposal_authority=True)  # type: ignore[arg-type]


def test_proposal_authority_rejects_forgery_foreign_and_tampered_state(tmp_path: Path) -> None:
    snapshot = intent_snapshot(
        bound_intent(allowed_mesh_changes={"mesh": "local"}), tmp_path, "authority-a"
    )
    state = run_transition(snapshot)
    proposal = Proposal(
        proposal_id="bound",
        proposal_class=ProposalClass.INTENT_PRESERVING,
        evidence_ids=("log-1",),
        changes={"mesh": "local"},
    )
    authority = ProposalAuthorityManager(state).issue(proposal)
    assert decide_proposal(state, proposal, authority).action is ProposalAction.AUTO_APPLY
    foreign = run_transition(intent_snapshot(bound_intent(), tmp_path, "authority-b"))
    with pytest.raises(EvidenceIntegrityError):
        decide_proposal(foreign, proposal, authority)
    with pytest.raises((TypeError, EvidenceIntegrityError)):
        decide_proposal(state, proposal, object.__new__(ProposalAuthority))
    for operation in (copy, deepcopy, pickle.dumps):
        with pytest.raises(TypeError):
            operation(authority)
    with pytest.raises(TypeError):
        replace(authority)  # type: ignore[type-var]
    with pytest.raises((AttributeError, TypeError)):
        object.__setattr__(authority, "forged", True)
    with pytest.raises(TypeError):

        class AuthorityChild(ProposalAuthority):
            pass

    with pytest.raises(EvidenceIntegrityError):
        decide_proposal(state, copy(proposal), authority)
    object.__setattr__(proposal, "evidence_ids", ("tampered-evidence",))
    with pytest.raises(EvidenceIntegrityError):
        decide_proposal(state, proposal, authority)
    store = object.__getattribute__(snapshot, "_store")
    payload = json.loads(store.intent_path.read_text(encoding="utf-8"))
    payload["engineering_question"] = "tampered"
    store.intent_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(EvidenceIntegrityError):
        decide_proposal(
            state,
            Proposal(
                "bound",
                ProposalClass.INTENT_PRESERVING,
                evidence_ids=("log-1",),
                changes={"mesh": "local"},
            ),
            authority,
        )


def test_retry_ledger_accounts_only_allowed_retries() -> None:
    ledger = RetryLedger(budget=2)
    first = decide_retry(FailureClass.NONLINEAR_CONVERGENCE, ledger)
    assert first.decision is RetryDecision.RETRY
    assert first.ledger.used == 1
    second = decide_retry(FailureClass.TIMEOUT, first.ledger)
    assert second.decision is RetryDecision.RETRY
    exhausted = decide_retry(FailureClass.TIMEOUT, second.ledger)
    assert exhausted.decision is RetryDecision.BUDGET_EXHAUSTED
    assert exhausted.ledger.used == 2


def test_retry_does_not_turn_unresolved_non_authoritative_data_into_a_question() -> None:
    intent = bound_intent(
        unresolved=({"condition": "contact", "authoritative": False, "source": "guess"},),
    )
    decision = decide_retry(
        FailureClass.NONLINEAR_CONVERGENCE, RetryLedger(budget=1), intent=intent
    )
    assert decision.decision is RetryDecision.RETRY


def test_sensitive_proposal_validation_is_required_before_retry_accounting() -> None:
    intent = bound_intent()
    proposal = Proposal(
        proposal_id="solver-2",
        proposal_class=ProposalClass.INTENT_SENSITIVE,
        evidence_ids=("log-3",),
        authorized=True,
    )
    held = decide_retry(
        FailureClass.NONLINEAR_CONVERGENCE,
        RetryLedger(budget=1),
        intent=intent,
        proposal=proposal,
    )
    assert held.decision is RetryDecision.STOP
    applied = decide_retry(
        FailureClass.NONLINEAR_CONVERGENCE,
        RetryLedger(budget=1),
        intent=intent,
        proposal=proposal,
        validation_passed=True,
    )
    assert applied.decision is RetryDecision.STOP
    assert applied.ledger.used == 0


def test_stale_ask_and_block_state_fails_closed_without_emitting_a_new_question() -> None:
    intent = bound_intent(state=IntentState.ASK_AND_BLOCK)
    proposal = Proposal(
        proposal_id="mesh-2",
        proposal_class=ProposalClass.INTENT_PRESERVING,
        evidence_ids=("log-4",),
        authorized=True,
    )
    assert decide_proposal(intent, proposal).action is ProposalAction.REJECT
    assert (
        decide_retry(FailureClass.TIMEOUT, RetryLedger(budget=1), intent=intent).decision
        is RetryDecision.STOP
    )


def test_execution_policy_handles_timeout_cancel_and_disconnect_resume() -> None:
    running = ExecutionContext(process_owned=True, process_running=True)
    assert (
        decide_execution(ExecutionSignal.TIMEOUT, running).action
        is ExecutionAction.CANCEL_OWNED_PROCESS
    )
    assert (
        decide_execution(ExecutionSignal.CANCEL_REQUESTED, running).action
        is ExecutionAction.CANCEL_OWNED_PROCESS
    )
    assert (
        decide_execution(ExecutionSignal.DISCONNECTED, running).action
        is ExecutionAction.HOLD_NO_NEW_WORK
    )
    assert (
        decide_execution(ExecutionSignal.RECONNECTED, running).action
        is ExecutionAction.RESUME_MONITORING
    )
    uncorrelated = ExecutionContext(
        process_owned=True,
        process_running=True,
        correlation_verified=False,
    )
    assert (
        decide_execution(ExecutionSignal.RECONNECTED, uncorrelated).action is ExecutionAction.STOP
    )


def test_ask_and_block_is_not_a_generic_failure_route() -> None:
    route = route_failure(FailureEvidence(nonlinear_convergence=True, source="LOG"))
    assert route.route is not FailureRoute.ASK_AND_BLOCK


@pytest.mark.parametrize(
    ("signal", "action"),
    [
        (ExecutionSignal.RUNNING, ExecutionAction.MONITOR),
        (ExecutionSignal.COMPLETED, ExecutionAction.VALIDATE_OUTPUT),
        (ExecutionSignal.CANCELLED, ExecutionAction.STOP),
    ],
)
def test_execution_terminal_signals_are_deterministic(
    signal: ExecutionSignal,
    action: ExecutionAction,
) -> None:
    assert decide_execution(signal, ExecutionContext()).action is action
