"""Preflight diagnostics for immutable inspections, plans, and evidence."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from ..evidence import EvidenceIntegrityError, IntentSnapshotAuthority
from ._immutability import freeze_json
from .completeness import (
    CompletenessResult,
    _validated_authoritative_result,
    assess_completeness,
)
from .feb import FEBInspection
from .plan import DerivedModelPlan
from .step import STEPInspection
from .types import (
    EvidenceProvenance,
    MissingConditionFact,
    PhysicalConditionName,
    UnresolvedEvidenceField,
    normalise_provenance,
)


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
        diagnostics = tuple(object.__getattribute__(self, "diagnostics"))
        if not all(isinstance(item, PreflightDiagnostic) for item in diagnostics):
            raise TypeError("diagnostics must contain PreflightDiagnostic records")
        object.__setattr__(self, "diagnostics", diagnostics)
        object.__setattr__(
            self,
            "ready",
            not any(item.blocking for item in diagnostics),
        )

    def __getattribute__(self, name: str) -> object:
        if name == "completeness":
            state = _safe_preflight_binding(self)
            return None if state is None else state.completeness
        if name == "ready":
            state = _safe_preflight_binding(self)
            return False if state is None else state.ready
        return object.__getattribute__(self, name)

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
        state = _safe_preflight_binding(self)
        if state is None:
            diagnostics = object.__getattribute__(self, "diagnostics")
            completeness_payload: dict[str, object] | None = None
            ready = False
        else:
            diagnostics = state.diagnostics
            ready = state.ready
            completeness_payload = None
            try:
                if state.completeness is not None:
                    completeness_payload = state.completeness.to_dict()
                # Serialization is another consumer boundary.  Do not return
                # an actionable projection if the live authority went stale
                # while its payload was being read.
                _validated_preflight_binding(self)
            except Exception:
                diagnostics = object.__getattribute__(self, "diagnostics")
                completeness_payload = None
                ready = False
        return {
            "diagnostics": [item.to_dict() for item in diagnostics],
            "ready": ready,
            "status": "READY" if ready else "BLOCKED",
            "completeness": completeness_payload,
        }


@dataclass(frozen=True, slots=True)
class _PreflightResultBinding:
    result: PreflightResult
    diagnostics: tuple[PreflightDiagnostic, ...]
    completeness: CompletenessResult | None
    snapshot: IntentSnapshotAuthority | None
    ready: bool
    projection: object


_PREFLIGHT_RESULT_STATES: dict[int, _PreflightResultBinding] = {}


def _preflight_projection(result: PreflightResult) -> object:
    try:
        return freeze_json(
            {
                "diagnostics": [
                    item.to_dict() for item in object.__getattribute__(result, "diagnostics")
                ],
                "completeness": (
                    None
                    if object.__getattribute__(result, "completeness") is None
                    else object.__getattribute__(result, "completeness").to_dict()
                ),
                "ready": object.__getattribute__(result, "ready"),
            }
        )
    except Exception as error:
        raise EvidenceIntegrityError("preflight result projection is invalid") from error


def _validated_preflight_binding(result: PreflightResult) -> _PreflightResultBinding:
    state = _PREFLIGHT_RESULT_STATES.get(id(result))
    if state is None or state.result is not result:
        raise EvidenceIntegrityError("preflight result is not authority-issued")
    if state.completeness is not None:
        if state.snapshot is None:
            raise EvidenceIntegrityError("preflight result snapshot binding is invalid")
        _validated_authoritative_result(state.completeness, state.snapshot)
    if _preflight_projection(result) != state.projection:
        raise EvidenceIntegrityError("preflight result projection changed")
    if state.completeness is not None:
        if state.snapshot is None:  # pragma: no cover - guarded above
            raise EvidenceIntegrityError("preflight result snapshot binding is invalid")
        _validated_authoritative_result(state.completeness, state.snapshot)
        # Keep one final live check after the complete result projection was
        # consumed, including serialization consumers.
        _validated_authoritative_result(state.completeness, state.snapshot)
    return state


def _safe_preflight_binding(result: PreflightResult) -> _PreflightResultBinding | None:
    try:
        return _validated_preflight_binding(result)
    except Exception:
        return None


def _issue_preflight_result(
    diagnostics: tuple[PreflightDiagnostic, ...],
    completeness: CompletenessResult | None,
    snapshot: IntentSnapshotAuthority | None,
) -> PreflightResult:
    result = PreflightResult(diagnostics=diagnostics, completeness=completeness)
    projection = _preflight_projection(result)
    _PREFLIGHT_RESULT_STATES[id(result)] = _PreflightResultBinding(
        result=result,
        diagnostics=tuple(diagnostics),
        completeness=completeness,
        snapshot=snapshot,
        ready=not any(item.blocking for item in diagnostics),
        projection=projection,
    )
    return result


def run_preflight(
    feb: FEBInspection | None = None,
    step: STEPInspection | None = None,
    plan: DerivedModelPlan | None = None,
    completeness: CompletenessResult | None = None,
    required_conditions: Iterable[str | PhysicalConditionName] | Mapping[str, object] | None = None,
    evidence: Mapping[str, object] | None = None,
    *,
    snapshot: IntentSnapshotAuthority | None = None,
) -> PreflightResult:
    """Run structural and evidence checks without invoking a solver."""

    if completeness is None and (required_conditions is not None or evidence is not None):
        completeness = assess_completeness(required_conditions, evidence)
    diagnostics: list[PreflightDiagnostic] = []
    completeness_fields: (
        tuple[
            tuple[str, ...],
            tuple[MissingConditionFact, ...],
            tuple[UnresolvedEvidenceField, ...],
            str,
        ]
        | None
    ) = None
    if completeness is not None:
        try:
            _validated_authoritative_result(completeness, snapshot)
            resolved_values = tuple(completeness.resolved)
            missing_values = tuple(completeness.missing)
            unresolved_values = tuple(completeness.unresolved)
            state_value = completeness.state
            # Revalidate after consuming every result field used for action.
            _validated_authoritative_result(completeness, snapshot)
            completeness_fields = (
                resolved_values,
                missing_values,
                unresolved_values,
                state_value,
            )
        except EvidenceIntegrityError:
            diagnostics.append(
                PreflightDiagnostic(
                    code="INVALID_COMPLETENESS_AUTHORITY",
                    message="completeness result is not a validated live authority result",
                    location="completeness",
                )
            )
        except Exception:
            diagnostics.append(
                PreflightDiagnostic(
                    code="INVALID_COMPLETENESS_AUTHORITY",
                    message="completeness result is not a validated live authority result",
                    location="completeness",
                )
            )

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
        units_resolved = completeness_fields is not None and "units" in completeness_fields[0]
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

    if completeness_fields is not None:
        _, missing_values, unresolved_values, state = completeness_fields
        for missing in missing_values:
            diagnostics.append(
                PreflightDiagnostic(
                    code="MISSING_PHYSICAL_CONDITION",
                    message=f"{missing.condition}: {missing.reason}; ask and block",
                    location=missing.condition,
                    evidence=missing.evidence,
                )
            )
        if state == "ASK_AND_BLOCK" and not missing_values:
            diagnostics.append(
                PreflightDiagnostic(
                    code="INCOMPLETE_CONDITION_RESULT",
                    message="completeness state is ASK_AND_BLOCK",
                    location="completeness",
                )
            )
        missing_conditions = {item.condition for item in missing_values}
        for unresolved in unresolved_values:
            if unresolved.field_name not in missing_conditions:
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
    return _issue_preflight_result(
        tuple(diagnostics),
        completeness if completeness_fields is not None else None,
        snapshot if completeness_fields is not None else None,
    )


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
