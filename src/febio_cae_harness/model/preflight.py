"""Preflight diagnostics for immutable inspections, plans, and evidence."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from .completeness import CompletenessResult, assess_completeness
from .feb import FEBInspection
from .plan import DerivedModelPlan
from .step import STEPInspection
from .types import EvidenceProvenance, PhysicalConditionName, normalise_provenance


class PreflightSeverity(StrEnum):
    """Severity levels emitted before any solver or model write."""

    INFO = "INFO"
    WARNING = "WARNING"
    BLOCKING = "BLOCKING"


DiagnosticSeverity = PreflightSeverity


@dataclass(frozen=True, slots=True)
class PreflightDiagnostic:
    """One actionable structural or evidence diagnostic."""

    code: str
    message: str
    severity: PreflightSeverity | str = PreflightSeverity.BLOCKING
    location: str = ""
    evidence: tuple[EvidenceProvenance, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not self.code.strip():
            raise ValueError("diagnostic code must be a non-empty string")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("diagnostic message must be a non-empty string")
        try:
            severity = PreflightSeverity(self.severity)
        except (TypeError, ValueError) as error:
            raise ValueError("severity must be INFO, WARNING, or BLOCKING") from error
        evidence = normalise_provenance(self.evidence)
        object.__setattr__(self, "severity", severity)
        object.__setattr__(self, "evidence", evidence)

    @property
    def blocking(self) -> bool:
        return PreflightSeverity(self.severity) is PreflightSeverity.BLOCKING

    @property
    def is_blocking(self) -> bool:
        return self.blocking

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": PreflightSeverity(self.severity).value,
            "location": self.location,
            "evidence": [item.to_dict() for item in self.evidence],
            "blocking": self.blocking,
        }


@dataclass(frozen=True, slots=True)
class PreflightResult:
    """Preflight outcome; ``ready`` is not solver success."""

    diagnostics: tuple[PreflightDiagnostic, ...] = ()
    completeness: CompletenessResult | None = None
    ready: bool = field(init=False)

    def __post_init__(self) -> None:
        diagnostics = tuple(self.diagnostics)
        if not all(isinstance(item, PreflightDiagnostic) for item in diagnostics):
            raise TypeError("diagnostics must contain PreflightDiagnostic records")
        object.__setattr__(self, "diagnostics", diagnostics)
        object.__setattr__(self, "ready", not any(item.blocking for item in diagnostics))

    @property
    def ok(self) -> bool:
        return self.ready

    @property
    def status(self) -> str:
        return "READY" if self.ready else "BLOCKED"

    @property
    def blocking_diagnostics(self) -> tuple[PreflightDiagnostic, ...]:
        return tuple(item for item in self.diagnostics if item.blocking)

    @property
    def errors(self) -> tuple[PreflightDiagnostic, ...]:
        return self.blocking_diagnostics

    def to_dict(self) -> dict[str, object]:
        return {
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "ready": self.ready,
            "status": self.status,
            "completeness": None if self.completeness is None else self.completeness.to_dict(),
        }


def run_preflight(
    feb: FEBInspection | None = None,
    step: STEPInspection | None = None,
    plan: DerivedModelPlan | None = None,
    completeness: CompletenessResult | None = None,
    required_conditions: Iterable[str | PhysicalConditionName] | Mapping[str, object] | None = None,
    evidence: Mapping[str, object] | None = None,
) -> PreflightResult:
    """Run structural and evidence checks without invoking a solver."""

    if completeness is None and (required_conditions is not None or evidence is not None):
        completeness = assess_completeness(required_conditions, evidence)
    diagnostics: list[PreflightDiagnostic] = []

    if feb is not None:
        if feb.root_tag != "febio_spec":
            diagnostics.append(
                PreflightDiagnostic(
                    code="INVALID_FEB_ROOT",
                    message=f"expected febio_spec root, found {feb.root_tag!r}",
                    location="/",
                )
            )
        for reference in feb.unresolved_references:
            diagnostics.append(
                PreflightDiagnostic(
                    code="MISSING_REFERENCE",
                    message=(
                        f"unresolved {reference.target_kind} reference {reference.value!r} "
                        f"from {reference.attribute}"
                    ),
                    location=reference.path,
                )
            )
        for kind, identifier in feb.duplicate_keys:
            diagnostics.append(
                PreflightDiagnostic(
                    code="DUPLICATE_IDENTIFIER",
                    message=(f"XML {kind} identifier {identifier!r} is defined more than once"),
                    location=f"/{feb.root_tag}",
                )
            )

    if step is not None:
        if not step.entities:
            diagnostics.append(
                PreflightDiagnostic(
                    code="EMPTY_STEP",
                    message="STEP contains no explicit entity assignments",
                    location=step.source_name or "STEP",
                )
            )
        units_resolved = completeness is not None and "units" in completeness.resolved
        if not step.units and not units_resolved:
            diagnostics.append(
                PreflightDiagnostic(
                    code="UNRESOLVED_UNITS",
                    message="STEP contains no explicit unit declaration; units must be supplied",
                    location=step.source_name or "STEP",
                )
            )

    if plan is not None:
        if not plan.original.verify():
            diagnostics.append(
                PreflightDiagnostic(
                    code="ORIGINAL_SOURCE_CHANGED",
                    message="original input no longer matches its captured SHA-256 digest",
                    location=str(
                        plan.original.source_path or plan.original.source_name or "original"
                    ),
                )
            )
        for change in plan.changes:
            if change.requires_ask_and_block:
                diagnostics.append(
                    PreflightDiagnostic(
                        code="INTENT_CHANGE_REQUIRES_AUTHORITY",
                        message=f"change {change.target!r} is classified as intent-changing",
                        location=change.target,
                        evidence=change.evidence,
                    )
                )

    if completeness is not None:
        for missing in completeness.missing:
            diagnostics.append(
                PreflightDiagnostic(
                    code="MISSING_PHYSICAL_CONDITION",
                    message=f"{missing.condition}: {missing.reason}; ask and block",
                    location=missing.condition,
                    evidence=missing.evidence,
                )
            )
        if completeness.state == "ASK_AND_BLOCK" and not completeness.missing:
            diagnostics.append(
                PreflightDiagnostic(
                    code="INCOMPLETE_CONDITION_RESULT",
                    message="completeness state is ASK_AND_BLOCK",
                    location="completeness",
                )
            )
        for unresolved in completeness.unresolved:
            if unresolved.field_name not in {item.condition for item in completeness.missing}:
                diagnostics.append(
                    PreflightDiagnostic(
                        code="UNRESOLVED_PHYSICAL_CONDITION",
                        message=f"{unresolved.field_name}: {unresolved.reason}; ask and block",
                        location=unresolved.field_name,
                        evidence=unresolved.evidence,
                    )
                )

    if feb is None and step is None and plan is None and completeness is None:
        diagnostics.append(
            PreflightDiagnostic(
                code="NO_INPUT",
                message=(
                    "preflight requires a FEB inspection, STEP inspection, plan, "
                    "or completeness result"
                ),
            )
        )
    return PreflightResult(diagnostics=tuple(diagnostics), completeness=completeness)


preflight = run_preflight
inspect_preflight = run_preflight
preflight_model = run_preflight


__all__ = [
    "DiagnosticSeverity",
    "PreflightDiagnostic",
    "PreflightResult",
    "PreflightSeverity",
    "inspect_preflight",
    "preflight",
    "preflight_model",
    "run_preflight",
]
