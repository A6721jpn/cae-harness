from __future__ import annotations

from pathlib import Path

import pytest

from febio_cae_harness.reporting import (
    AttemptIdentity,
    EvidenceProvenance,
    EvidenceReference,
    FreshOutputValidation,
    ReportEvidence,
    SolverRunResult,
    assemble_report,
    evaluate_success_gates,
)
from febio_cae_harness.solver import (
    FbsValidation,
    LogValidation,
    SolverClassification,
    SolverState,
)

IDENTITY = AttemptIdentity(case_id="case-a", intent_id="intent-a", attempt_id="attempt-1")
LOG_PATH = Path("attempt-1.log")
XPLT_PATH = Path("attempt-1.xplt")


def make_log(*, classification: SolverClassification | None = None) -> LogValidation:
    return LogValidation(
        path=LOG_PATH,
        exists=True,
        normal_termination=classification is None,
        observed_steps=2,
        observed_final_time=1.0,
        expected_steps=2,
        expected_final_time=1.0,
        classification=classification,
    )


def make_fbs(*, official: bool = True, valid: bool = True) -> FbsValidation:
    return FbsValidation(
        xplt_path=XPLT_PATH,
        requested_fields=("displacement",),
        available_fields=("displacement",),
        values={"displacement": [0.1, 0.2]},
        missing_fields=(),
        non_finite_fields=(),
        valid=valid,
        official=official,
        provenance="official-fbs" if official else "synthetic-adapter",
        issues=(),
    )


def make_solver(
    *,
    classification: SolverClassification = SolverClassification.SUCCESS,
    return_code: int | None = 0,
    log: LogValidation | None = None,
    fbs: FbsValidation | None = None,
) -> SolverRunResult:
    return SolverRunResult(
        state=SolverState.NORMAL_EXIT,
        classification=classification,
        return_code=return_code,
        pid=123,
        command=("febio", "model.feb"),
        log_path=LOG_PATH,
        xplt_path=XPLT_PATH,
        log_validation=log or make_log(),
        fbs_validation=fbs or make_fbs(),
    )


def make_outputs(log: LogValidation | None = None) -> FreshOutputValidation:
    return FreshOutputValidation(
        attempt_id=IDENTITY.attempt_id,
        log_path=LOG_PATH,
        xplt_path=XPLT_PATH,
        log_fresh=True,
        xplt_fresh=True,
        log_validation=log or make_log(),
    )


def make_reference(kind: str) -> EvidenceReference:
    return EvidenceReference(
        kind=kind,
        reference=f"90_Temporary/attempts/{IDENTITY.attempt_id}/{kind}.json",
        case_id=IDENTITY.case_id,
        intent_id=IDENTITY.intent_id,
        attempt_id=IDENTITY.attempt_id,
        verified=True,
        satisfies_intent=True,
        provenance=EvidenceProvenance.OFFICIAL,
    )


def make_evidence() -> ReportEvidence:
    return ReportEvidence(
        mesh=make_reference("mesh"),
        jacobian=make_reference("jacobian"),
        roi=make_reference("roi"),
        evaluation=make_reference("evaluation"),
    )


def test_all_authority_conditions_are_required_for_success() -> None:
    evaluation = evaluate_success_gates(
        IDENTITY,
        make_solver(),
        make_outputs(),
        make_fbs(),
        make_evidence(),
        provenance=EvidenceProvenance.OFFICIAL,
    )

    assert evaluation.success
    assert evaluation.passed
    assert not evaluation.failures
    assert set(evaluation.checks) >= {
        "attempt_identity",
        "owned_process_normal_exit",
        "fresh_log",
        "fresh_xplt",
        "log_termination",
        "log_expected_step",
        "log_final_time",
        "log_no_fatal_or_negative_jacobian",
        "official_fbs",
        "required_fields_finite",
        "mesh_evidence",
        "jacobian_evidence",
        "roi_evidence",
        "evaluation_evidence",
        "evidence_binding",
        "official_provenance",
    }


def test_zero_exit_and_output_presence_cannot_bypass_log_and_evidence_gates() -> None:
    bad_log = make_log(classification=SolverClassification.INVALID_LOG)
    evidence = ReportEvidence(
        mesh=make_reference("mesh"),
        jacobian=make_reference("jacobian"),
        roi=make_reference("roi"),
        evaluation=EvidenceReference(
            kind="evaluation",
            reference="evaluation.json",
            case_id="other-case",
            intent_id=IDENTITY.intent_id,
            attempt_id=IDENTITY.attempt_id,
            verified=True,
            satisfies_intent=True,
            provenance=EvidenceProvenance.OFFICIAL,
        ),
    )

    evaluation = evaluate_success_gates(
        IDENTITY,
        make_solver(log=bad_log),
        make_outputs(bad_log),
        make_fbs(),
        evidence,
        provenance=EvidenceProvenance.OFFICIAL,
    )

    assert not evaluation.success
    assert not evaluation.checks["log_termination"]
    assert not evaluation.checks["evidence_binding"]


def test_synthetic_fbs_is_explicitly_unverified_and_cannot_produce_success() -> None:
    synthetic_fbs = make_fbs(official=False)
    evaluation = evaluate_success_gates(
        IDENTITY,
        make_solver(
            classification=SolverClassification.FBS_UNVERIFIED,
            fbs=synthetic_fbs,
        ),
        make_outputs(),
        synthetic_fbs,
        make_evidence(),
        provenance=EvidenceProvenance.SYNTHETIC,
    )
    report = assemble_report(
        IDENTITY,
        make_solver(
            classification=SolverClassification.FBS_UNVERIFIED,
            fbs=synthetic_fbs,
        ),
        make_outputs(),
        synthetic_fbs,
        make_evidence(),
        provenance=EvidenceProvenance.SYNTHETIC,
    )

    assert not evaluation.success
    assert not evaluation.checks["official_fbs"]
    assert not evaluation.checks["official_provenance"]
    assert report.provenance is EvidenceProvenance.SYNTHETIC
    assert not report.success


def test_freshness_and_all_evidence_categories_must_be_authoritative() -> None:
    evidence = make_evidence()
    invalid = EvidenceReference(
        kind="jacobian",
        reference="jacobian.json",
        case_id=IDENTITY.case_id,
        intent_id=IDENTITY.intent_id,
        attempt_id=IDENTITY.attempt_id,
        verified=True,
        satisfies_intent=False,
        provenance=EvidenceProvenance.OFFICIAL,
    )
    evidence = ReportEvidence(
        mesh=evidence.mesh,
        jacobian=invalid,
        roi=evidence.roi,
        evaluation=evidence.evaluation,
    )
    outputs = FreshOutputValidation(
        attempt_id=IDENTITY.attempt_id,
        log_path=LOG_PATH,
        xplt_path=XPLT_PATH,
        log_fresh=True,
        xplt_fresh=False,
        log_validation=make_log(),
    )

    evaluation = evaluate_success_gates(
        IDENTITY,
        make_solver(),
        outputs,
        make_fbs(),
        evidence,
        provenance=EvidenceProvenance.OFFICIAL,
    )

    assert not evaluation.success
    assert not evaluation.checks["fresh_xplt"]
    assert not evaluation.checks["jacobian_evidence"]


def test_reporting_does_not_execute_processes_or_read_result_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_file_access(*args: object, **kwargs: object) -> bool:
        del args, kwargs
        raise AssertionError("reporting must consume supplied validation only")

    monkeypatch.setattr(Path, "is_file", no_file_access)
    evaluation = evaluate_success_gates(
        IDENTITY,
        make_solver(),
        make_outputs(),
        make_fbs(),
        make_evidence(),
        provenance=EvidenceProvenance.OFFICIAL,
    )

    assert evaluation.success
