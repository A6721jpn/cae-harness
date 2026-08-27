from __future__ import annotations

import inspect
import json
import pickle
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from copy import copy, deepcopy
from dataclasses import is_dataclass, replace
from pathlib import Path
from unittest.mock import patch

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
    validate_attempt_workspace,
)
from febio_cae_harness.contracts import IntentContract
from febio_cae_harness.evidence import (
    EvidenceIntegrityError,
    EvidenceStore,
    IntentSnapshotAuthority,
)
from febio_cae_harness.solver import headless as headless_module
from febio_cae_harness.solver.runtime import probe_febio
from febio_cae_harness.solver.supervisor import SolverSupervisor
from febio_cae_harness.solver.types import SolverClassification
from febio_cae_harness.workspace import (
    AttemptWorkspace,
    ValidatedCaseWorkspace,
    WorkspaceBoundaryError,
)


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


def timeout_supervisor(
    tmp_path: Path,
    attempt_id: str = "attempt-a",
    case_id: str = "case-a",
    classification: SolverClassification | None = None,
    *,
    intent: IntentContract | None = None,
    workspace_root: Path | None = None,
    record_attempt: bool = True,
) -> SolverSupervisor:
    workspace_temp: tempfile.TemporaryDirectory[str] | None
    if workspace_root is None:
        workspace_temp = tempfile.TemporaryDirectory(prefix="h3-", dir=tmp_path.parent.parent)
        workspace_root = Path(workspace_temp.name)
    else:
        workspace_temp = None
    manager = ValidatedCaseWorkspace(workspace_root / "t", workspace_root / "c")
    case = manager.create_case(case_id) if record_attempt else manager.open_case(case_id)
    store_intent = IntentContract() if record_attempt and intent is None else intent
    store = EvidenceStore(case, store_intent)
    if record_attempt:
        store.record_attempt(attempt_id)
    attempt = AttemptWorkspace._from_manager(
        case,
        attempt_id,
        case.temporary_root / "attempts" / attempt_id,
    )
    snapshot = store.issue_intent_snapshot()
    if classification is not None:
        code = (
            "import os; from pathlib import Path; "
            "Path(os.environ['FEBIO_CAE_HARNESS_LOG']).write_text('synthetic'); "
            "Path(os.environ['FEBIO_CAE_HARNESS_XPLT']).write_bytes(b'synthetic-xplt')"
        )
    else:
        code = "import time; time.sleep(30)"
    input_path = attempt.write_text("input.feb", code)

    class ProbeProcess:
        returncode = 0

        def communicate(self, input: bytes, timeout: float) -> tuple[bytes, bytes]:
            del timeout
            assert input == b"quit\n"
            return b"version 4.12.0\n", b""

    with patch(
        "febio_cae_harness.solver.runtime.subprocess.Popen",
        lambda command, **kwargs: ProbeProcess(),
    ):
        runtime = probe_febio(Path(sys.executable))
    capability = headless_module._issue_launch_capability(
        attempt,
        snapshot,
        runtime,
        input_path,
        expected_steps=None,
        expected_final_time=None,
        timeout_seconds=None,
    )
    supervisor = SolverSupervisor(capability)
    # Keep the short synthetic workspace alive for the supervisor lifetime;
    # this also avoids Windows MAX_PATH noise from pytest's long test names.
    if workspace_temp is not None:
        object.__setattr__(supervisor, "_test_workspace_temp", workspace_temp)
    if classification is not None:

        class SyntheticLogValidator:
            def validate(self, path: Path) -> SyntheticLogValidator:
                del path
                return self

        validator = SyntheticLogValidator()
        object.__setattr__(validator, "classification", classification)
        object.__setattr__(supervisor, "_log_validator", validator)
    return supervisor


def retry_state_for_case(
    workspace_root: Path,
    case_id: str,
    intent: IntentContract,
    *,
    attempt_ids: tuple[str, ...] = (),
) -> tuple[StateTransition, IntentSnapshotAuthority]:
    manager = ValidatedCaseWorkspace(workspace_root / "t", workspace_root / "c")
    case = manager.create_case(case_id)
    store = EvidenceStore(case, intent)
    for attempt_id in attempt_ids:
        store.record_attempt(attempt_id)
    snapshot = store.issue_intent_snapshot()
    return run_transition(snapshot), snapshot


def short_workspace_root(
    tmp_path: Path,
) -> tuple[tempfile.TemporaryDirectory[str], Path]:
    workspace_temp = tempfile.TemporaryDirectory(prefix="h4b-", dir=tmp_path.parent.parent)
    return workspace_temp, Path(workspace_temp.name)


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
    snapshot = intent_snapshot(intent, tmp_path)
    assert unresolved_authoritative_conditions(snapshot) == ()
    assert run_transition(snapshot).current is IntentState.BOUND


def test_condition_source_mapping_can_authorize_a_named_unresolved_condition(
    tmp_path: Path,
) -> None:
    intent = bound_intent(
        unresolved=({"condition": "contact", "authoritative": True, "source": "user"},),
        condition_sources={"contact": {"authoritative": True, "source": "user"}},
    )
    snapshot = intent_snapshot(intent, tmp_path)
    conditions = unresolved_authoritative_conditions(snapshot)
    assert len(conditions) == 1
    assert conditions[0].condition == "contact"
    assert run_transition(snapshot).current is IntentState.ASK_AND_BLOCK


def test_source_labels_cannot_authorize_a_missing_condition(tmp_path: Path) -> None:
    intent = bound_intent(
        unresolved=("contact",),
        condition_sources={"contact": "user"},
    )
    snapshot = intent_snapshot(intent, tmp_path, "source-label")

    assert unresolved_authoritative_conditions(snapshot) == ()
    assert run_transition(snapshot).current is IntentState.GATHERING


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
        condition_sources={"load": {"authoritative": True, "source": "user"}},
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


def test_attempt_workspace_binding_is_live_and_case_root_exact(tmp_path: Path) -> None:
    intent = bound_intent()
    workspace = ValidatedCaseWorkspace(tmp_path / "tool", tmp_path / "02_CAE")
    case = workspace.create_case("case-a")
    store = EvidenceStore(case, intent)
    store.record_attempt("attempt-a")
    attempt = AttemptWorkspace._from_manager(
        case,
        "attempt-a",
        case.temporary_root / "attempts" / "attempt-a",
    )
    state = run_transition(store.issue_intent_snapshot())

    validate_attempt_workspace(state, attempt)

    foreign_workspace = ValidatedCaseWorkspace(
        tmp_path / "tool-foreign",
        tmp_path / "02_CAE-foreign",
    )
    foreign_case = foreign_workspace.create_case(case.case_id)
    foreign_attempt = foreign_case.allocate_attempt("attempt-a")
    with pytest.raises(EvidenceIntegrityError):
        validate_attempt_workspace(state, foreign_attempt)
    with pytest.raises(TypeError):
        validate_attempt_workspace(state, foreign_attempt.root)  # type: ignore[arg-type]

    forged = object.__new__(AttemptWorkspace)
    object.__setattr__(forged, "case_id", case.case_id)
    object.__setattr__(forged, "attempt_id", "attempt-a")
    object.__setattr__(forged, "root", attempt.root)
    with pytest.raises(WorkspaceBoundaryError):
        validate_attempt_workspace(state, forged)

    object.__setattr__(attempt, "attempt_id", "changed")
    with pytest.raises(WorkspaceBoundaryError):
        validate_attempt_workspace(state, attempt)


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


def test_retry_ledger_accounts_only_allowed_retries(tmp_path: Path) -> None:
    intent = bound_intent(retry_budget=2)
    _workspace_temp, workspace_root = short_workspace_root(tmp_path)
    state, _ = retry_state_for_case(
        workspace_root,
        "case-ledger",
        intent,
        attempt_ids=("attempt-a", "attempt-b", "attempt-c"),
    )
    ledger = RetryLedger.from_authority(state)

    first_supervisor = timeout_supervisor(
        tmp_path / "first",
        case_id="case-ledger",
        intent=intent,
        workspace_root=workspace_root,
        record_attempt=False,
    )
    first_result = first_supervisor.run(timeout_seconds=0.1)
    first = decide_retry(
        FailureClass.TIMEOUT,
        ledger,
        intent=state,
        solver_supervisor=first_supervisor,
        solver_result=first_result,
    )
    assert first.decision is RetryDecision.RETRY
    assert first.ledger.used == 1

    second_supervisor = timeout_supervisor(
        tmp_path / "second",
        "attempt-b",
        case_id="case-ledger",
        intent=intent,
        workspace_root=workspace_root,
        record_attempt=False,
    )
    second_result = second_supervisor.run(timeout_seconds=0.1)
    second = decide_retry(
        FailureClass.TIMEOUT,
        first.ledger,
        intent=state,
        supervisor=second_supervisor,
        result=second_result,
    )
    assert second.decision is RetryDecision.RETRY
    third_supervisor = timeout_supervisor(
        tmp_path / "third",
        "attempt-c",
        case_id="case-ledger",
        intent=intent,
        workspace_root=workspace_root,
        record_attempt=False,
    )
    third_result = third_supervisor.run(timeout_seconds=0.1)
    exhausted = decide_retry(
        FailureClass.TIMEOUT,
        second.ledger,
        intent=state,
        supervisor=third_supervisor,
        result=third_result,
    )
    assert exhausted.decision is RetryDecision.BUDGET_EXHAUSTED
    assert exhausted.ledger.used == 2


def test_retry_rejects_foreign_case_root_with_same_case_and_intent_digests(
    tmp_path: Path,
) -> None:
    intent = bound_intent(retry_budget=1)
    local_workspace_temp, local_root = short_workspace_root(tmp_path)
    foreign_workspace_temp, foreign_root = short_workspace_root(tmp_path)
    local_state, local_snapshot = retry_state_for_case(
        local_root, "same-case", intent, attempt_ids=("attempt-a",)
    )
    ledger = RetryLedger.from_authority(local_state)
    foreign_state, foreign_snapshot = retry_state_for_case(
        foreign_root, "same-case", intent, attempt_ids=("attempt-a",)
    )
    del foreign_state
    foreign_supervisor = timeout_supervisor(
        tmp_path / "foreign-supervisor",
        case_id="same-case",
        intent=intent,
        workspace_root=foreign_root,
        record_attempt=False,
    )
    foreign_result = foreign_supervisor.run(timeout_seconds=0.1)

    local_case_workspace = object.__getattribute__(local_snapshot, "_case_workspace")
    foreign_case_workspace = object.__getattribute__(foreign_snapshot, "_case_workspace")
    assert local_snapshot.case_id == foreign_snapshot.case_id == "same-case"
    assert local_snapshot.case_sha256 == foreign_snapshot.case_sha256
    assert local_snapshot.intent_sha256 == foreign_snapshot.intent_sha256
    assert local_case_workspace.root != foreign_case_workspace.root

    decision = decide_retry(
        FailureClass.TIMEOUT,
        ledger,
        intent=local_state,
        supervisor=foreign_supervisor,
        result=foreign_result,
    )

    assert decision.decision is RetryDecision.STOP
    assert decision.ledger is ledger
    assert ledger.used == 0
    del local_workspace_temp, foreign_workspace_temp


def test_retry_rejects_mutated_capability_before_accounting(
    tmp_path: Path,
) -> None:
    intent = bound_intent(retry_budget=1)
    local_workspace_temp, local_root = short_workspace_root(tmp_path)
    foreign_workspace_temp, foreign_root = short_workspace_root(tmp_path)
    local_state, _ = retry_state_for_case(
        local_root, "same-case", intent, attempt_ids=("attempt-a",)
    )
    ledger = RetryLedger.from_authority(local_state)
    _foreign_state, foreign_snapshot = retry_state_for_case(
        foreign_root, "same-case", intent, attempt_ids=("attempt-a",)
    )
    supervisor = timeout_supervisor(
        tmp_path / "supervisor",
        case_id="same-case",
        intent=intent,
        workspace_root=local_root,
        record_attempt=False,
    )
    result = supervisor.run(timeout_seconds=0.1)
    capability = object.__getattribute__(supervisor, "_launch_capability")
    object.__setattr__(capability, "_intent_snapshot", foreign_snapshot)

    decision = decide_retry(
        FailureClass.TIMEOUT,
        ledger,
        intent=local_state,
        supervisor=supervisor,
        result=result,
    )

    assert decision.decision is RetryDecision.STOP
    assert decision.ledger is ledger
    assert ledger.used == 0
    del local_workspace_temp, foreign_workspace_temp


def test_retry_rejects_mutated_capability_inside_accounting_lock(
    tmp_path: Path,
) -> None:
    intent = bound_intent(retry_budget=1)
    local_workspace_temp, local_root = short_workspace_root(tmp_path)
    foreign_workspace_temp, foreign_root = short_workspace_root(tmp_path)
    local_state, _ = retry_state_for_case(
        local_root, "same-case", intent, attempt_ids=("attempt-a",)
    )
    ledger = RetryLedger.from_authority(local_state)
    _foreign_state, foreign_snapshot = retry_state_for_case(
        foreign_root, "same-case", intent, attempt_ids=("attempt-a",)
    )
    supervisor = timeout_supervisor(
        tmp_path / "supervisor",
        case_id="same-case",
        intent=intent,
        workspace_root=local_root,
        record_attempt=False,
    )
    result = supervisor.run(timeout_seconds=0.1)
    capability = object.__getattribute__(supervisor, "_launch_capability")

    class MutatingLock:
        def __enter__(self) -> MutatingLock:
            object.__setattr__(capability, "_intent_snapshot", foreign_snapshot)
            return self

        def __exit__(self, *args: object) -> None:
            del args

    with patch(
        "febio_cae_harness.autonomy.policy._RETRY_CONSUMPTION_LOCK",
        MutatingLock(),
    ):
        decision = decide_retry(
            FailureClass.TIMEOUT,
            ledger,
            intent=local_state,
            supervisor=supervisor,
            result=result,
        )

    assert decision.decision is RetryDecision.STOP
    assert decision.ledger is ledger
    assert ledger.used == 0
    del local_workspace_temp, foreign_workspace_temp


@pytest.mark.parametrize(
    ("solver_classification", "failure", "expected_failure"),
    [
        (SolverClassification.INIT_ONLY, FailureClass.TIMEOUT, FailureClass.UNKNOWN),
        (SolverClassification.MISSING_OUTPUT, FailureClass.TIMEOUT, FailureClass.MISSING_OUTPUT),
        (SolverClassification.FATAL, FailureClass.TIMEOUT, FailureClass.FATAL),
        (
            SolverClassification.NEGATIVE_JACOBIAN,
            FailureClass.TIMEOUT,
            FailureClass.NEGATIVE_JACOBIAN,
        ),
        (SolverClassification.INVALID_LOG, FailureClass.TIMEOUT, FailureClass.UNKNOWN),
        (SolverClassification.FBS_INVALID, FailureClass.TIMEOUT, FailureClass.UNKNOWN),
        (SolverClassification.TIMEOUT, FailureClass.NEGATIVE_JACOBIAN, FailureClass.TIMEOUT),
        (SolverClassification.CANCELLED, FailureClass.TIMEOUT, FailureClass.CANCELLED),
        (SolverClassification.CANCELLED, FailureClass.NEGATIVE_JACOBIAN, FailureClass.CANCELLED),
    ],
)
def test_mismatched_failure_cannot_promote_an_issued_solver_result(
    tmp_path: Path,
    solver_classification: SolverClassification,
    failure: FailureClass,
    expected_failure: FailureClass,
) -> None:
    state = run_transition(intent_snapshot(bound_intent(retry_budget=1), tmp_path, "mismatch"))
    if solver_classification is SolverClassification.TIMEOUT:
        supervisor = timeout_supervisor(tmp_path / "solver", case_id="case-mismatch")
        result = supervisor.run(timeout_seconds=0.1)
    elif solver_classification is SolverClassification.CANCELLED:
        supervisor = timeout_supervisor(tmp_path / "solver", case_id="case-mismatch")
        supervisor.start()
        result = supervisor.cancel()
    else:
        supervisor = timeout_supervisor(
            tmp_path / "solver",
            case_id="case-mismatch",
            classification=solver_classification,
        )
        result = supervisor.run()
    decision = decide_retry(
        failure,
        RetryLedger.from_authority(state),
        intent=state,
        supervisor=supervisor,
        result=result,
    )
    assert decision.decision is not RetryDecision.RETRY
    assert decision.failure is expected_failure


def test_mismatched_failure_evidence_cannot_promote_same_supervisor_result(
    tmp_path: Path,
) -> None:
    state = run_transition(
        intent_snapshot(bound_intent(retry_budget=1), tmp_path, "evidence-mismatch")
    )
    supervisor = timeout_supervisor(tmp_path / "solver", case_id="case-evidence-mismatch")
    result = supervisor.run(timeout_seconds=0.1)
    decision = decide_retry(
        FailureEvidence(negative_jacobian=True, source="LOG"),
        RetryLedger.from_authority(state),
        intent=state,
        supervisor=supervisor,
        result=result,
    )
    assert decision.decision is not RetryDecision.RETRY
    assert decision.failure is FailureClass.TIMEOUT


def test_retry_does_not_turn_unresolved_non_authoritative_data_into_a_question(
    tmp_path: Path,
) -> None:
    intent = bound_intent(
        retry_budget=1,
        unresolved=({"condition": "contact", "authoritative": False, "source": "guess"},),
    )
    _workspace_temp, workspace_root = short_workspace_root(tmp_path)
    state, _ = retry_state_for_case(
        workspace_root,
        "case-non-authoritative",
        intent,
        attempt_ids=("attempt-a",),
    )
    supervisor = timeout_supervisor(
        tmp_path / "non-authoritative-solver",
        case_id="case-non-authoritative",
        intent=intent,
        workspace_root=workspace_root,
        record_attempt=False,
    )
    result = supervisor.run(timeout_seconds=0.1)
    decision = decide_retry(
        FailureClass.TIMEOUT,
        RetryLedger.from_authority(state),
        intent=state,
        supervisor=supervisor,
        result=result,
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


def test_raw_intent_cannot_emit_a_physical_question() -> None:
    intent = bound_intent(
        unresolved=({"condition": "load", "authoritative": True, "source": "user"},),
    )
    proposal = Proposal(
        proposal_id="raw-physical",
        proposal_class=ProposalClass.INTENT_CHANGING,
        evidence_ids=("log-raw-physical",),
        requires_physical_decision=True,
    )

    decision = decide_proposal(intent, proposal)

    assert decision.action is ProposalAction.REJECT
    assert decision.blocking_conditions == ()


def test_raw_intent_cannot_route_a_physical_question() -> None:
    intent = bound_intent(
        unresolved=({"condition": "load", "authoritative": True, "source": "user"},),
        condition_sources={"load": {"authoritative": True, "source": "user"}},
    )

    route = route_failure(
        FailureEvidence(timeout=True, requires_physical_decision=True),
        intent=intent,
    )

    assert route.route is FailureRoute.STOP
    assert route.blocking_conditions == ()


def test_live_authority_can_route_only_its_current_physical_question(tmp_path: Path) -> None:
    intent = bound_intent(
        state=IntentState.ASK_AND_BLOCK,
        unresolved=({"condition": "load", "authoritative": True, "source": "user"},),
        condition_sources={"load": {"authoritative": True, "source": "user"}},
    )
    snapshot = intent_snapshot(intent, tmp_path, "route-authority")

    route = route_failure(
        FailureEvidence(timeout=True, requires_physical_decision=True),
        intent=snapshot,
    )

    assert route.route is FailureRoute.ASK_AND_BLOCK
    assert tuple(condition.condition for condition in route.blocking_conditions) == ("load",)


def test_execution_policy_handles_timeout_cancel_and_disconnect_resume(tmp_path: Path) -> None:
    supervisor = timeout_supervisor(tmp_path / "execution")
    supervisor.start()
    running = ExecutionContext(
        case_id=supervisor._case_id,
        intent_id=supervisor._intent_id,
        attempt_id=supervisor._attempt_id,
        process_owned=True,
        process_running=True,
        supervisor=supervisor,
    )
    try:
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
    finally:
        supervisor.cancel()
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


def test_raw_retry_inputs_never_authorize_retry(tmp_path: Path) -> None:
    intent = bound_intent(retry_budget=1)
    state = run_transition(intent_snapshot(intent, tmp_path, "raw-retry"))

    for failure, ledger, supplied_intent in (
        (FailureClass.TIMEOUT, RetryLedger(budget=1), None),
        (FailureEvidence(timeout=True, source="LOG"), RetryLedger.from_budget(1), intent),
        (FailureClass.TIMEOUT, RetryLedger(budget=1), state),
    ):
        decision = decide_retry(failure, ledger, intent=supplied_intent)
        assert decision.decision is not RetryDecision.RETRY


def test_execution_actions_require_live_correlated_supervisor() -> None:
    context = ExecutionContext(process_owned=True, process_running=True)
    assert decide_execution(ExecutionSignal.TIMEOUT, context).action is ExecutionAction.STOP
    assert (
        decide_execution(ExecutionSignal.CANCEL_REQUESTED, context).action is ExecutionAction.STOP
    )
    assert decide_execution(ExecutionSignal.RECONNECTED, context).action is ExecutionAction.STOP


def test_policy_has_no_importable_intent_state_factory_bypass() -> None:
    import febio_cae_harness.autonomy.policy as policy

    assert not hasattr(policy, "_INTENT_STATE_FACTORY")
    assert not hasattr(policy, "_PROPOSAL_AUTHORITY_FACTORY")


def test_retry_authority_rejects_forged_ledger_and_result(tmp_path: Path) -> None:
    intent = bound_intent(retry_budget=1)
    _workspace_temp, workspace_root = short_workspace_root(tmp_path)
    state, _ = retry_state_for_case(
        workspace_root,
        "case-authority",
        intent,
        attempt_ids=("attempt-a",),
    )
    supervisor = timeout_supervisor(
        tmp_path / "solver",
        case_id="case-authority",
        intent=intent,
        workspace_root=workspace_root,
        record_attempt=False,
    )
    result = supervisor.run(timeout_seconds=0.1)
    try:
        ledger = RetryLedger.from_authority(state)
        accepted = decide_retry(
            FailureClass.TIMEOUT,
            ledger,
            intent=state,
            supervisor=supervisor,
            result=result,
        )
        assert accepted.decision is RetryDecision.RETRY

        for diagnostic in (
            RetryLedger(budget=1),
            RetryLedger.from_intent(intent),
            RetryLedger.from_budget(1),
            RetryLedger(budget=1).consume(failure=FailureClass.TIMEOUT),
            RetryLedger(budget=1).record_retry(FailureClass.TIMEOUT),
        ):
            assert (
                decide_retry(
                    FailureClass.TIMEOUT,
                    diagnostic,
                    intent=state,
                    supervisor=supervisor,
                    result=result,
                ).decision
                is RetryDecision.STOP
            )
        forged_result = replace(result)
        assert (
            decide_retry(
                FailureClass.TIMEOUT,
                ledger,
                intent=state,
                supervisor=supervisor,
                result=forged_result,
            ).decision
            is RetryDecision.STOP
        )

        tampered = RetryLedger.from_authority(state)
        object.__setattr__(tampered, "budget", 99)
        assert (
            decide_retry(
                FailureClass.TIMEOUT,
                tampered,
                intent=state,
                supervisor=supervisor,
                result=result,
            ).decision
            is RetryDecision.STOP
        )
    finally:
        # ``run`` has already released the process authority; this is a safe
        # idempotent cleanup if a future supervisor implementation changes that.
        if supervisor.result is None:
            supervisor.cancel()


def test_retry_authority_revalidates_the_snapshot_before_consuming_budget(
    tmp_path: Path,
) -> None:
    snapshot = intent_snapshot(bound_intent(retry_budget=1), tmp_path, "stale-ledger")
    state = run_transition(snapshot)
    ledger = RetryLedger.from_authority(state)
    supervisor = timeout_supervisor(tmp_path / "stale-solver", case_id="case-stale-ledger")
    result = supervisor.run(timeout_seconds=0.1)
    store = object.__getattribute__(snapshot, "_store")
    payload = json.loads(store.intent_path.read_text(encoding="utf-8"))
    payload["retry_budget"] = 99
    store.intent_path.write_text(json.dumps(payload), encoding="utf-8")

    decision = decide_retry(
        FailureClass.TIMEOUT,
        ledger,
        intent=state,
        supervisor=supervisor,
        result=result,
    )
    assert decision.decision is RetryDecision.STOP


def test_retry_replay_of_parent_ledger_and_failed_result_stops_without_minting(
    tmp_path: Path,
) -> None:
    intent = bound_intent(retry_budget=3)
    _workspace_temp, workspace_root = short_workspace_root(tmp_path)
    state, _ = retry_state_for_case(
        workspace_root,
        "case-retry-replay-parent",
        intent,
        attempt_ids=("attempt-a",),
    )
    ledger = RetryLedger.from_authority(state)
    supervisor = timeout_supervisor(
        tmp_path / "retry-replay-parent-solver",
        case_id="case-retry-replay-parent",
        intent=intent,
        workspace_root=workspace_root,
        record_attempt=False,
    )
    result = supervisor.run(timeout_seconds=0.1)

    first = decide_retry(
        FailureClass.TIMEOUT,
        ledger,
        intent=state,
        supervisor=supervisor,
        result=result,
    )
    replay = decide_retry(
        FailureClass.TIMEOUT,
        ledger,
        intent=state,
        supervisor=supervisor,
        result=result,
    )

    assert first.decision is RetryDecision.RETRY
    assert first.ledger.budget == state.intent.execution_budget.retry_budget == 3
    assert first.ledger.used == 1
    assert len(first.ledger.records) == 1
    assert replay.decision is RetryDecision.STOP
    assert replay.ledger is ledger


def test_retry_replay_parent_ledger_cannot_mint_with_a_different_failed_result(
    tmp_path: Path,
) -> None:
    intent = bound_intent(retry_budget=3)
    _workspace_temp, workspace_root = short_workspace_root(tmp_path)
    state, _ = retry_state_for_case(
        workspace_root,
        "case-retry-replay-different-result",
        intent,
        attempt_ids=("attempt-first", "attempt-second"),
    )
    ledger = RetryLedger.from_authority(state)
    first_supervisor = timeout_supervisor(
        tmp_path / "retry-replay-different-result-first",
        attempt_id="attempt-first",
        case_id="case-retry-replay-different-result",
        intent=intent,
        workspace_root=workspace_root,
        record_attempt=False,
    )
    first_result = first_supervisor.run(timeout_seconds=0.1)
    second_supervisor = timeout_supervisor(
        tmp_path / "retry-replay-different-result-second",
        attempt_id="attempt-second",
        case_id="case-retry-replay-different-result",
        intent=intent,
        workspace_root=workspace_root,
        record_attempt=False,
    )
    second_result = second_supervisor.run(timeout_seconds=0.1)

    first = decide_retry(
        FailureClass.TIMEOUT,
        ledger,
        intent=state,
        supervisor=first_supervisor,
        result=first_result,
    )
    replay = decide_retry(
        FailureClass.TIMEOUT,
        ledger,
        intent=state,
        supervisor=second_supervisor,
        result=second_result,
    )

    assert first.decision is RetryDecision.RETRY
    assert replay.decision is RetryDecision.STOP
    assert replay.ledger is ledger


def test_retry_replay_of_consumed_result_with_successor_ledger_stops(
    tmp_path: Path,
) -> None:
    intent = bound_intent(retry_budget=3)
    _workspace_temp, workspace_root = short_workspace_root(tmp_path)
    state, _ = retry_state_for_case(
        workspace_root,
        "case-retry-replay-successor",
        intent,
        attempt_ids=("attempt-a",),
    )
    ledger = RetryLedger.from_authority(state)
    supervisor = timeout_supervisor(
        tmp_path / "retry-replay-successor-solver",
        case_id="case-retry-replay-successor",
        intent=intent,
        workspace_root=workspace_root,
        record_attempt=False,
    )
    result = supervisor.run(timeout_seconds=0.1)

    first = decide_retry(
        FailureClass.TIMEOUT,
        ledger,
        intent=state,
        supervisor=supervisor,
        result=result,
    )
    replay = decide_retry(
        FailureClass.TIMEOUT,
        first.ledger,
        intent=state,
        supervisor=supervisor,
        result=result,
    )

    assert first.decision is RetryDecision.RETRY
    assert first.ledger.budget == 3
    assert first.ledger.used == 1
    assert len(first.ledger.records) == 1
    assert replay.decision is RetryDecision.STOP
    assert replay.ledger is first.ledger


def test_retry_replay_consumption_is_atomic_under_concurrent_calls(
    tmp_path: Path,
) -> None:
    intent = bound_intent(retry_budget=3)
    _workspace_temp, workspace_root = short_workspace_root(tmp_path)
    state, _ = retry_state_for_case(
        workspace_root,
        "case-retry-replay-concurrent",
        intent,
        attempt_ids=("attempt-a",),
    )
    ledger = RetryLedger.from_authority(state)
    supervisor = timeout_supervisor(
        tmp_path / "retry-replay-concurrent-solver",
        case_id="case-retry-replay-concurrent",
        intent=intent,
        workspace_root=workspace_root,
        record_attempt=False,
    )
    result = supervisor.run(timeout_seconds=0.1)

    def consume() -> RetryDecision:
        return decide_retry(
            FailureClass.TIMEOUT,
            ledger,
            intent=state,
            supervisor=supervisor,
            result=result,
        ).decision

    with ThreadPoolExecutor(max_workers=2) as executor:
        decisions = tuple(executor.map(lambda _: consume(), range(2)))

    assert decisions.count(RetryDecision.RETRY) == 1
    assert decisions.count(RetryDecision.STOP) == 1


def test_execution_authority_rejects_mismatched_context_and_tampered_supervisor(
    tmp_path: Path,
) -> None:
    supervisor = timeout_supervisor(tmp_path / "execution-authority")
    supervisor.start()
    original_case_id = supervisor._case_id
    context = ExecutionContext(
        case_id=supervisor._case_id,
        intent_id=supervisor._intent_id,
        attempt_id=supervisor._attempt_id,
        process_owned=True,
        process_running=True,
        supervisor=supervisor,
    )
    try:
        mismatch = replace(context, case_id="case-b")
        assert decide_execution(ExecutionSignal.TIMEOUT, mismatch).action is ExecutionAction.STOP
        object.__setattr__(supervisor, "_case_id", "case-b")
        assert decide_execution(ExecutionSignal.TIMEOUT, context).action is ExecutionAction.STOP
    finally:
        object.__setattr__(supervisor, "_case_id", original_case_id)
        supervisor.cancel()
