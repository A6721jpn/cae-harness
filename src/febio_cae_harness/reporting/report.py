"""Fail-closed assembly of diagnostics for one live report authority."""

from __future__ import annotations

from .authority import ReportAuthority
from .gates import _evaluate
from .types import ResultReport, _issue_report

__all__ = [
    "ReportAssembler",
    "ReportBuilder",
    "assemble_report",
    "assemble_result_report",
    "build_report",
]


def assemble_report(authority: ReportAuthority) -> ResultReport:
    """Assemble a report only from the exact, live, issued authority."""

    evaluation, identity, result, fbs, fresh, evidence = _evaluate(authority)
    return _issue_report(
        authority=authority,
        identity=identity,
        solver_result=result,
        fresh_outputs=fresh,
        fbs_validation=fbs,
        evidence=evidence,
        gates=evaluation,
        provenance=evaluation.provenance,
    )


assemble_result_report = assemble_report
build_report = assemble_report


class ReportBuilder:
    """Stateless facade that accepts only a live report authority."""

    def assemble(self, authority: ReportAuthority) -> ResultReport:
        return assemble_report(authority)

    build = assemble
    __call__ = assemble


ReportAssembler = ReportBuilder
