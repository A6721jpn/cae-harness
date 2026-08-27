from __future__ import annotations

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
    ProposalClass,
    RetryDecision,
    RetryLedger,
    classify_failure,
    decide_execution,
    decide_proposal,
    decide_retry,
    route_failure,
    transition_intent,
    unresolved_authoritative_conditions,
)
from febio_cae_harness.contracts import IntentContract


def bound_intent(**overrides: object) -> IntentContract:
    values: dict[str, object] = {
        "engineering_question": "What is the displacement?",
        "units": "mm",
        "material": {"source": "user", "value": "steel"},
        "loads": ({"source": "user", "value": "100 N"},),
        "constraints": ({"source": "user", "value": "fixed"},),
        "analysis_step": {"source": "user", "value": 1},
        "condition_sources": {
            "material": {"authoritative": True, "source": "user"},
            "loads": {"authoritative": True, "source": "user"},
            "constraints": {"authoritative": True, "source": "user"},
        },
        "state": IntentState.GATHERING,
    }
    values.update(overrides)
    return IntentContract(**values)  # type: ignore[arg-type]


def test_transition_binds_only_when_complete_and_blocks_authoritative_unknowns() -> None:
    intent = bound_intent()
    transition = transition_intent(intent, conditions_complete=True)
    assert transition.previous is IntentState.GATHERING
    assert transition.current is IntentState.BOUND
    assert transition.intent.state is IntentState.BOUND

    unresolved = bound_intent(
        unresolved=({"condition": "contact", "authoritative": True, "source": "user"},),
    )
    blocked = transition_intent(unresolved, conditions_complete=True)
    assert blocked.current is IntentState.ASK_AND_BLOCK
    assert blocked.intent.state is IntentState.ASK_AND_BLOCK


def test_non_authoritative_unknowns_do_not_trigger_ask_and_block() -> None:
    intent = bound_intent(
        unresolved=({"condition": "contact", "authoritative": False, "source": "guess"},),
    )
    assert unresolved_authoritative_conditions(intent) == ()
    assert transition_intent(intent, conditions_complete=True).current is IntentState.BOUND


def test_condition_source_mapping_can_authorize_a_named_unresolved_condition() -> None:
    intent = bound_intent(
        unresolved=("contact",),
        condition_sources={"contact": "user"},
    )
    conditions = unresolved_authoritative_conditions(intent)
    assert len(conditions) == 1
    assert conditions[0].condition == "contact"
    assert transition_intent(intent, conditions_complete=True).current is IntentState.ASK_AND_BLOCK


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


def test_proposal_policy_respects_debug_class_and_physical_boundary() -> None:
    intent = bound_intent()
    preserving = Proposal(
        proposal_id="mesh-1",
        proposal_class=ProposalClass.INTENT_PRESERVING,
        rationale="repair an evidenced local element",
        evidence_ids=("log-1",),
        authorized=True,
    )
    assert decide_proposal(intent, preserving).action is ProposalAction.AUTO_APPLY

    sensitive = Proposal(
        proposal_id="solver-1",
        proposal_class=ProposalClass.INTENT_SENSITIVE,
        rationale="change a numerical control",
        evidence_ids=("log-2",),
        authorized=True,
    )
    assert decide_proposal(intent, sensitive).action is ProposalAction.REQUIRE_VALIDATION
    assert (
        decide_proposal(intent, sensitive, validation_passed=True).action
        is ProposalAction.AUTO_APPLY
    )

    changing = Proposal(
        proposal_id="load-1",
        proposal_class=ProposalClass.INTENT_CHANGING,
        rationale="change a load",
        requires_physical_decision=True,
    )
    assert decide_proposal(intent, changing).action is ProposalAction.REJECT
    unresolved = bound_intent(
        unresolved=({"condition": "load", "authoritative": True, "source": "user"},),
    )
    assert decide_proposal(unresolved, changing).action is ProposalAction.ASK_AND_BLOCK


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
    assert applied.decision is RetryDecision.RETRY
    assert applied.ledger.used == 1


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
