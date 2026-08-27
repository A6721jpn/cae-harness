"""Pure evaluation of the FEBio result success authority."""

from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import Path

from febio_cae_harness.solver import (
    FbsValidation,
    LogValidation,
    SolverClassification,
    SolverRunResult,
    SolverState,
)

from .types import (
    AttemptIdentity,
    EvidenceKind,
    EvidenceProvenance,
    EvidenceReference,
    FreshOutputValidation,
    ReportEvidence,
    SuccessGateEvaluation,
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


def _same_path(left: object, right: object) -> bool:
    try:
        return Path(left) == Path(right)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def _normalise_provenance(value: EvidenceProvenance | str) -> EvidenceProvenance:
    if isinstance(value, EvidenceProvenance):
        return value
    aliases = {
        "official": EvidenceProvenance.OFFICIAL,
        "official-fbs": EvidenceProvenance.OFFICIAL,
        "fbs": EvidenceProvenance.OFFICIAL,
        "synthetic": EvidenceProvenance.SYNTHETIC,
        "synthetic-adapter": EvidenceProvenance.SYNTHETIC,
        "fixture": EvidenceProvenance.SYNTHETIC,
        "unverified": EvidenceProvenance.UNVERIFIED,
        "unknown": EvidenceProvenance.UNVERIFIED,
    }
    if not isinstance(value, str):
        raise TypeError("provenance must be an EvidenceProvenance or string")
    try:
        return aliases[value.strip().casefold()]
    except KeyError as error:
        raise ValueError("provenance must be official, synthetic, or unverified") from error


def _fbs_provenance(validation: FbsValidation | None) -> EvidenceProvenance:
    if validation is None:
        return EvidenceProvenance.UNVERIFIED
    value = str(validation.provenance).casefold()
    if "synthetic" in value or "fixture" in value:
        return EvidenceProvenance.SYNTHETIC
    if validation.official and value in {"official", "official-fbs", "fbs"}:
        return EvidenceProvenance.OFFICIAL
    return EvidenceProvenance.UNVERIFIED


def _derive_provenance(
    fresh_outputs: FreshOutputValidation | None,
    fbs_validation: FbsValidation | None,
    evidence: ReportEvidence,
    supplied: EvidenceProvenance | str | None,
) -> EvidenceProvenance:
    values = [_fbs_provenance(fbs_validation)]
    if fresh_outputs is not None:
        values.append(fresh_outputs.normalized_provenance)
    values.extend(reference.normalized_provenance for reference in evidence.references)
    if any(value is EvidenceProvenance.UNVERIFIED for value in values):
        observed = EvidenceProvenance.UNVERIFIED
    elif any(value is EvidenceProvenance.SYNTHETIC for value in values):
        observed = EvidenceProvenance.SYNTHETIC
    else:
        observed = EvidenceProvenance.OFFICIAL
    if supplied is None:
        return observed
    requested = _normalise_provenance(supplied)
    if requested is EvidenceProvenance.UNVERIFIED:
        return requested
    if requested is EvidenceProvenance.SYNTHETIC:
        return requested if observed is EvidenceProvenance.OFFICIAL else observed
    return observed


def _empty_evidence() -> ReportEvidence:
    return ReportEvidence()


def _coerce_reference(value: object) -> EvidenceReference | None:
    if isinstance(value, EvidenceReference):
        return value
    if not isinstance(value, Mapping):
        return None
    values = dict(value)
    aliases = {
        "path": "reference",
        "locator": "reference",
        "intent_digest": "intent_id",
        "evidence_kind": "kind",
        "intent_satisfied": "satisfies_intent",
        "authoritative": "verified",
    }
    for alias, canonical in aliases.items():
        if alias in values and canonical not in values:
            values[canonical] = values[alias]
        values.pop(alias, None)
    try:
        return EvidenceReference(**values)
    except (TypeError, ValueError):
        return None


def _coerce_evidence(value: ReportEvidence | Mapping[str, object] | None) -> ReportEvidence:
    if isinstance(value, ReportEvidence):
        return value
    if not isinstance(value, Mapping):
        return _empty_evidence()
    aliases = {
        "mesh_quality": "mesh",
        "mesh_evidence": "mesh",
        "jacobian_quality": "jacobian",
        "jacobian_evidence": "jacobian",
        "roi_evidence": "roi",
        "evaluation_quantities": "evaluation",
        "evaluation_evidence": "evaluation",
    }
    values: dict[str, object] = dict(value)
    for alias, canonical in aliases.items():
        if alias in values and canonical not in values:
            values[canonical] = values[alias]
        values.pop(alias, None)
    for name in ("mesh", "jacobian", "roi", "evaluation"):
        if name in values:
            values[name] = _coerce_reference(values[name])
    additional = values.get("additional", ())
    if isinstance(additional, (list, tuple)):
        values["additional"] = tuple(
            reference for raw in additional if (reference := _coerce_reference(raw)) is not None
        )
    else:
        values["additional"] = ()
    try:
        return ReportEvidence.from_mapping(values)
    except (TypeError, ValueError):
        return _empty_evidence()


def _identity_check(identity: object) -> bool:
    return (
        isinstance(identity, AttemptIdentity)
        and bool(identity.case_id)
        and bool(identity.intent_id)
        and bool(identity.attempt_id)
    )


def _log_validation(
    fresh_outputs: FreshOutputValidation | None,
    solver_result: SolverRunResult,
) -> LogValidation | None:
    if fresh_outputs is not None and fresh_outputs.log_validation is not None:
        return fresh_outputs.log_validation
    return solver_result.log_validation


def evaluate_success_gates(
    attempt_identity: AttemptIdentity,
    solver_result: SolverRunResult,
    fresh_outputs: FreshOutputValidation | None = None,
    fbs_validation: FbsValidation | None = None,
    evidence: ReportEvidence | Mapping[str, object] | None = None,
    *,
    provenance: EvidenceProvenance | str | None = None,
) -> SuccessGateEvaluation:
    """Evaluate every authority condition without touching external state.

    All validation objects are supplied by earlier phases.  The function only
    compares their typed fields; it never executes a process or checks a path
    on disk.  Missing or malformed evidence therefore fails closed.
    """

    report_evidence = _coerce_evidence(evidence)
    supplied_fbs = fbs_validation
    if supplied_fbs is None and isinstance(solver_result, SolverRunResult):
        supplied_fbs = solver_result.fbs_validation

    identity_ok = _identity_check(attempt_identity)
    solver_ok = isinstance(solver_result, SolverRunResult)
    outputs_ok = isinstance(fresh_outputs, FreshOutputValidation)
    outputs = fresh_outputs if outputs_ok else None
    log = _log_validation(fresh_outputs, solver_result) if solver_ok else None

    checks: dict[str, bool] = {}
    checks["attempt_identity"] = identity_ok
    checks["owned_process_normal_exit"] = bool(
        solver_ok
        and solver_result.state is SolverState.NORMAL_EXIT
        and solver_result.return_code == 0
        and solver_result.pid is not None
    )
    checks["solver_classification"] = bool(
        solver_ok and solver_result.classification is SolverClassification.SUCCESS
    )
    checks["solver_error_free"] = bool(solver_ok and not solver_result.error)

    checks["fresh_log"] = bool(
        outputs_ok
        and identity_ok
        and outputs is not None
        and outputs.attempt_id == attempt_identity.attempt_id
        and _same_path(outputs.log_path, solver_result.log_path)
        and outputs.log_fresh
        and outputs.log_present
    )
    checks["fresh_xplt"] = bool(
        outputs_ok
        and identity_ok
        and outputs is not None
        and outputs.attempt_id == attempt_identity.attempt_id
        and _same_path(outputs.xplt_path, solver_result.xplt_path)
        and outputs.xplt_fresh
        and outputs.xplt_present
    )
    checks["distinct_output_paths"] = bool(
        outputs_ok and outputs is not None and outputs.log_path != outputs.xplt_path
    )

    checks["log_termination"] = bool(
        isinstance(log, LogValidation) and log.exists and log.normal_termination
    )
    checks["log_expected_step"] = bool(
        isinstance(log, LogValidation)
        and log.expected_steps is not None
        and log.observed_steps == log.expected_steps
    )
    checks["log_final_time"] = bool(
        isinstance(log, LogValidation)
        and log.expected_final_time is not None
        and log.observed_final_time is not None
        and math.isclose(
            log.observed_final_time,
            float(log.expected_final_time),
            rel_tol=1e-9,
            abs_tol=1e-9,
        )
    )
    checks["log_no_fatal_or_negative_jacobian"] = bool(
        isinstance(log, LogValidation)
        and log.exists
        and log.classification not in _LOG_FAILURES
        and not log.issues
    )
    checks["log_validation_binding"] = bool(
        isinstance(log, LogValidation)
        and outputs_ok
        and outputs is not None
        and _same_path(log.path, outputs.log_path)
        and _same_path(log.path, solver_result.log_path)
    )

    checks["official_fbs"] = bool(
        isinstance(supplied_fbs, FbsValidation)
        and _same_path(supplied_fbs.xplt_path, solver_result.xplt_path)
        and _fbs_provenance(supplied_fbs) is EvidenceProvenance.OFFICIAL
        and supplied_fbs.valid
    )
    checks["required_fields_finite"] = bool(
        isinstance(supplied_fbs, FbsValidation)
        and supplied_fbs.valid
        and supplied_fbs.all_requested_fields_finite
    )

    required: tuple[tuple[str, EvidenceKind], ...] = (
        ("mesh", EvidenceKind.MESH),
        ("jacobian", EvidenceKind.JACOBIAN),
        ("roi", EvidenceKind.ROI),
        ("evaluation", EvidenceKind.EVALUATION),
    )
    for name, kind in required:
        reference = report_evidence.for_kind(kind)
        checks[f"{name}_evidence"] = bool(
            reference is not None and reference.kind is kind and reference.authoritative
        )

    checks["evidence_binding"] = bool(identity_ok and report_evidence.matches(attempt_identity))
    checks["all_evidence_authoritative"] = bool(
        all(
            reference.authoritative and identity_ok and reference.matches(attempt_identity)
            for reference in report_evidence.references
        )
    )
    resolved_provenance = _derive_provenance(
        fresh_outputs,
        supplied_fbs,
        report_evidence,
        provenance,
    )
    checks["official_provenance"] = bool(
        resolved_provenance is EvidenceProvenance.OFFICIAL
        and isinstance(supplied_fbs, FbsValidation)
        and _fbs_provenance(supplied_fbs) is EvidenceProvenance.OFFICIAL
        and (outputs is not None and outputs.normalized_provenance is EvidenceProvenance.OFFICIAL)
        and all(reference.authoritative for reference in report_evidence.references)
    )

    failures = tuple(name for name, passed in checks.items() if not passed)
    return SuccessGateEvaluation(
        passed=not failures and all(checks.values()),
        checks=checks,
        failures=failures,
        provenance=resolved_provenance,
    )


def evaluate_success_gate(
    attempt_identity: AttemptIdentity,
    solver_result: SolverRunResult,
    fresh_outputs: FreshOutputValidation | None = None,
    fbs_validation: FbsValidation | None = None,
    evidence: ReportEvidence | Mapping[str, object] | None = None,
    *,
    provenance: EvidenceProvenance | str | None = None,
) -> SuccessGateEvaluation:
    """Singular-name alias for :func:`evaluate_success_gates`."""

    return evaluate_success_gates(
        attempt_identity,
        solver_result,
        fresh_outputs,
        fbs_validation,
        evidence,
        provenance=provenance,
    )


def evaluate_success(
    attempt_identity: AttemptIdentity,
    solver_result: SolverRunResult,
    fresh_outputs: FreshOutputValidation | None = None,
    fbs_validation: FbsValidation | None = None,
    evidence: ReportEvidence | Mapping[str, object] | None = None,
    *,
    provenance: EvidenceProvenance | str | None = None,
) -> SuccessGateEvaluation:
    """Short-name alias for success-gate evaluation."""

    return evaluate_success_gates(
        attempt_identity,
        solver_result,
        fresh_outputs,
        fbs_validation,
        evidence,
        provenance=provenance,
    )


evaluate_result_success = evaluate_success_gates


class SuccessGate:
    """Stateless object façade for callers that prefer dependency injection."""

    @staticmethod
    def evaluate(
        attempt_identity: AttemptIdentity,
        solver_result: SolverRunResult,
        fresh_outputs: FreshOutputValidation | None = None,
        fbs_validation: FbsValidation | None = None,
        evidence: ReportEvidence | Mapping[str, object] | None = None,
        *,
        provenance: EvidenceProvenance | str | None = None,
    ) -> SuccessGateEvaluation:
        return evaluate_success_gates(
            attempt_identity,
            solver_result,
            fresh_outputs,
            fbs_validation,
            evidence,
            provenance=provenance,
        )
