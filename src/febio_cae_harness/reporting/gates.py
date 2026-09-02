"""Fail-closed evaluation of a live report authority."""

from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from febio_cae_harness.solver import (
    FbsValidation,
    LogValidation,
    SolverClassification,
    SolverRunResult,
    SolverState,
    SolverSupervisor,
)

from .authority import ReportAuthority, _require_issued_authority
from .physical import PhysicalEvidenceAuthority, _validated_physical_evidence
from .types import (
    AttemptIdentity,
    EvidenceKind,
    EvidenceProvenance,
    EvidenceReference,
    FreshOutputValidation,
    ReportEvidence,
    SuccessGateEvaluation,
    _issue_gate,
)

__all__ = [
    "SuccessGate",
    "evaluate_result_success",
    "evaluate_success",
    "evaluate_success_gate",
    "evaluate_success_gates",
]


_LOG_FAILURES = {
    SolverClassification.FATAL,
    SolverClassification.NEGATIVE_JACOBIAN,
    SolverClassification.INIT_ONLY,
    SolverClassification.MISSING_OUTPUT,
}


def _authority_provenance(record: tuple[Any, ...]) -> EvidenceProvenance:
    return (
        EvidenceProvenance.OFFICIAL if record[10] == "official" else EvidenceProvenance.UNVERIFIED
    )


def _diagnostic_evidence(
    identity: AttemptIdentity,
    paths: Mapping[EvidenceKind, Path],
    physical_evidence: PhysicalEvidenceAuthority | None,
) -> ReportEvidence:
    def reference(kind: EvidenceKind) -> EvidenceReference:
        authoritative = physical_evidence is not None and physical_evidence.authoritative(
            kind.value
        )
        return EvidenceReference(
            kind,
            paths[kind],
            identity.case_id,
            identity.intent_id,
            identity.attempt_id,
            verified=authoritative,
            satisfies_intent=authoritative,
            provenance=(
                EvidenceProvenance.OFFICIAL if authoritative else EvidenceProvenance.UNVERIFIED
            ),
        )

    return ReportEvidence(
        reference(EvidenceKind.MESH),
        reference(EvidenceKind.JACOBIAN),
        reference(EvidenceKind.ROI),
        reference(EvidenceKind.EVALUATION),
        identity=identity,
    )


def _diagnostic_inputs(
    authority: ReportAuthority,
) -> tuple[
    tuple[Any, ...],
    AttemptIdentity,
    SolverRunResult,
    FbsValidation,
    FreshOutputValidation,
    ReportEvidence,
]:
    record = _require_issued_authority(authority)
    identity = cast(AttemptIdentity, record[4])
    result = cast(SolverRunResult, record[3])
    fbs = result.fbs_validation
    if type(fbs) is not FbsValidation:
        raise TypeError("report authority result has no exact FbsValidation")
    paths = cast(Mapping[EvidenceKind, Path], record[6])
    physical_evidence = cast(PhysicalEvidenceAuthority | None, record[12])
    if physical_evidence is not None:
        _validated_physical_evidence(
            physical_evidence,
            supervisor=cast(SolverSupervisor, record[2]),
            result=result,
            receipt=record[11],
        )
    fresh = FreshOutputValidation(
        identity.attempt_id,
        result.log_path,
        result.xplt_path,
        True,
        True,
        result.log_validation,
        True,
        True,
        EvidenceProvenance.UNVERIFIED,
    )
    return (
        record,
        identity,
        result,
        fbs,
        fresh,
        _diagnostic_evidence(identity, paths, physical_evidence),
    )


def _evaluate_authority(
    record: tuple[Any, ...],
    identity: AttemptIdentity,
    result: SolverRunResult,
    fbs: FbsValidation,
    fresh: FreshOutputValidation,
    evidence: ReportEvidence,
    authority: ReportAuthority,
) -> SuccessGateEvaluation:
    log = result.log_validation
    files = cast(Mapping[Path, tuple[Any, ...]], record[7])
    provenance = _authority_provenance(record)
    identity_ok = type(identity) is AttemptIdentity and all(
        (identity.case_id, identity.intent_id, identity.attempt_id)
    )
    log_ok = isinstance(log, LogValidation)
    checked_log = cast(LogValidation, log)
    checks: dict[str, bool] = {
        "report_authority": True,
        "attempt_identity": identity_ok,
        "owned_process_normal_exit": result.state is SolverState.NORMAL_EXIT
        and result.return_code == 0
        and result.pid is not None,
        "solver_classification": result.classification is SolverClassification.SUCCESS,
        "solver_error_free": not result.error,
        "fresh_log": identity_ok
        and fresh.attempt_id == identity.attempt_id
        and fresh.log_path == result.log_path
        and fresh.log_fresh
        and fresh.log_present
        and result.log_path in files,
        "fresh_xplt": identity_ok
        and fresh.attempt_id == identity.attempt_id
        and fresh.xplt_path == result.xplt_path
        and fresh.xplt_fresh
        and fresh.xplt_present
        and result.xplt_path in files,
        "distinct_output_paths": result.log_path != result.xplt_path,
        "log_termination": log_ok and checked_log.exists and checked_log.normal_termination,
        "log_expected_step": log_ok
        and checked_log.expected_steps is not None
        and checked_log.observed_steps == checked_log.expected_steps,
        "log_final_time": log_ok
        and checked_log.expected_final_time is not None
        and checked_log.observed_final_time is not None
        and math.isclose(
            checked_log.observed_final_time,
            float(checked_log.expected_final_time),
            rel_tol=1e-9,
            abs_tol=1e-9,
        ),
        "log_no_fatal_or_negative_jacobian": log_ok
        and checked_log.exists
        and checked_log.classification not in _LOG_FAILURES
        and not checked_log.issues,
        "log_validation_binding": log_ok
        and checked_log.path == fresh.log_path
        and checked_log.path == result.log_path,
        "official_fbs": (fbs.xplt_path == result.xplt_path and fbs.valid and fbs.official is True),
        "required_fields_finite": fbs.valid and fbs.all_requested_fields_finite,
    }
    for name, kind in (
        ("mesh", EvidenceKind.MESH),
        ("jacobian", EvidenceKind.JACOBIAN),
        ("roi", EvidenceKind.ROI),
        ("evaluation", EvidenceKind.EVALUATION),
    ):
        reference = evidence.for_kind(kind)
        checks[f"{name}_evidence"] = reference is not None and reference.authoritative
    checks["evidence_binding"] = evidence.matches(identity)
    checks["all_evidence_authoritative"] = all(
        reference.authoritative and reference.matches(identity) for reference in evidence.references
    )
    checks["official_provenance"] = (
        provenance is EvidenceProvenance.OFFICIAL
        and checks["official_fbs"]
        and checks["all_evidence_authoritative"]
    )
    failures = tuple(name for name, passed in checks.items() if not passed)
    passed = provenance is EvidenceProvenance.OFFICIAL and not failures
    return _issue_gate(
        authority=authority,
        identity=identity,
        checks=checks,
        failures=failures,
        provenance=provenance,
        passed=passed,
    )


def _evaluate(
    authority: ReportAuthority,
) -> tuple[
    SuccessGateEvaluation,
    AttemptIdentity,
    SolverRunResult,
    FbsValidation,
    FreshOutputValidation,
    ReportEvidence,
]:
    record, identity, result, fbs, fresh, evidence = _diagnostic_inputs(authority)
    evaluation = _evaluate_authority(record, identity, result, fbs, fresh, evidence, authority)
    return evaluation, identity, result, fbs, fresh, evidence


def evaluate_success_gates(authority: ReportAuthority) -> SuccessGateEvaluation:
    """Evaluate diagnostics from one exact, live, manager-issued authority."""

    return _evaluate(authority)[0]


def evaluate_success_gate(authority: ReportAuthority) -> SuccessGateEvaluation:
    return evaluate_success_gates(authority)


def evaluate_success(authority: ReportAuthority) -> SuccessGateEvaluation:
    return evaluate_success_gates(authority)


evaluate_result_success = evaluate_success_gates


class SuccessGate:
    """Stateless facade that accepts only a live report authority."""

    @staticmethod
    def evaluate(authority: ReportAuthority) -> SuccessGateEvaluation:
        return evaluate_success_gates(authority)
