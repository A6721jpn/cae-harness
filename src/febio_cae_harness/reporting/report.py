"""Pure assembly of typed attempt reports."""

from __future__ import annotations

from collections.abc import Mapping

from febio_cae_harness.solver import FbsValidation, SolverRunResult

from .gates import evaluate_success_gates
from .types import (
    AttemptIdentity,
    EvidenceProvenance,
    FreshOutputValidation,
    ReportEvidence,
    ResultReport,
)

__all__ = [
    "ReportAssembler",
    "ReportBuilder",
    "assemble_report",
    "assemble_result_report",
    "build_report",
]


def assemble_report(
    attempt_identity: AttemptIdentity,
    solver_result: SolverRunResult,
    fresh_outputs: FreshOutputValidation | None = None,
    fbs_validation: FbsValidation | None = None,
    evidence: ReportEvidence | Mapping[str, object] | None = None,
    *,
    provenance: EvidenceProvenance | str | None = None,
) -> ResultReport:
    """Assemble a report from supplied validations and evidence references.

    This function is deliberately side-effect free.  It stores the supplied
    output/FBS/evidence projections and the complete gate decision; it does
    not execute a solver, inspect a file, or persist a report.
    """

    if isinstance(evidence, ReportEvidence):
        report_evidence = evidence
    elif isinstance(evidence, Mapping):
        try:
            report_evidence = ReportEvidence.from_mapping(evidence)
        except (TypeError, ValueError):
            report_evidence = ReportEvidence()
    else:
        report_evidence = ReportEvidence()

    supplied_fbs = fbs_validation
    if supplied_fbs is None:
        supplied_fbs = solver_result.fbs_validation

    gates = evaluate_success_gates(
        attempt_identity,
        solver_result,
        fresh_outputs,
        supplied_fbs,
        report_evidence,
        provenance=provenance,
    )
    return ResultReport(
        identity=attempt_identity,
        solver_result=solver_result,
        fresh_outputs=fresh_outputs,
        fbs_validation=supplied_fbs,
        evidence=report_evidence,
        gates=gates,
        provenance=gates.provenance,
    )


assemble_result_report = assemble_report
build_report = assemble_report


class ReportBuilder:
    """Stateless builder façade around :func:`assemble_report`."""

    def assemble(
        self,
        attempt_identity: AttemptIdentity,
        solver_result: SolverRunResult,
        fresh_outputs: FreshOutputValidation | None = None,
        fbs_validation: FbsValidation | None = None,
        evidence: ReportEvidence | Mapping[str, object] | None = None,
        *,
        provenance: EvidenceProvenance | str | None = None,
    ) -> ResultReport:
        return assemble_report(
            attempt_identity,
            solver_result,
            fresh_outputs,
            fbs_validation,
            evidence,
            provenance=provenance,
        )

    build = assemble
    __call__ = assemble


ReportAssembler = ReportBuilder
