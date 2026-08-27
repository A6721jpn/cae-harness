"""Incomplete FEB inventory and evidence-backed physical questions."""

from __future__ import annotations

from dataclasses import dataclass

from .completeness import CompletenessResult
from .feb import FEBInspection
from .preflight import PreflightDiagnostic
from .types import (
    ASK_AND_BLOCK,
    EvidenceProvenance,
    MissingConditionFact,
    PhysicalConditionName,
    UnresolvedEvidenceField,
    normalise_provenance,
)


@dataclass(frozen=True, slots=True)
class MissingConditionQuestion:
    condition: str
    reason: str
    evidence: tuple[EvidenceProvenance, ...] = ()
    action: str = ASK_AND_BLOCK

    def __post_init__(self) -> None:
        if isinstance(self.condition, PhysicalConditionName):
            object.__setattr__(self, "condition", self.condition.value)
        if not isinstance(self.condition, str) or not self.condition.strip():
            raise ValueError("condition must be a non-empty string")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("reason must be a non-empty string")
        if self.action != ASK_AND_BLOCK:
            raise ValueError("missing condition questions must use ASK_AND_BLOCK")
        evidence = normalise_provenance(self.evidence)
        if not all(item.authoritative for item in evidence):
            raise ValueError("question evidence must be authoritative")
        object.__setattr__(self, "evidence", evidence)

    def to_dict(self) -> dict[str, object]:
        return {
            "condition": self.condition,
            "reason": self.reason,
            "evidence": [item.to_dict() for item in self.evidence],
            "action": self.action,
        }


@dataclass(frozen=True, slots=True)
class IncompleteFebInventory:
    structural_diagnostics: tuple[PreflightDiagnostic, ...]
    questions: tuple[MissingConditionQuestion, ...]
    ready: bool

    def __post_init__(self) -> None:
        diagnostics = tuple(self.structural_diagnostics)
        questions = tuple(self.questions)
        if not all(isinstance(item, PreflightDiagnostic) for item in diagnostics):
            raise TypeError("structural_diagnostics must contain PreflightDiagnostic records")
        if not all(isinstance(item, MissingConditionQuestion) for item in questions):
            raise TypeError("questions must contain MissingConditionQuestion records")
        if not isinstance(self.ready, bool):
            raise TypeError("ready must be a bool")
        object.__setattr__(self, "structural_diagnostics", diagnostics)
        object.__setattr__(self, "questions", questions)

    def to_dict(self) -> dict[str, object]:
        return {
            "structural_diagnostics": [item.to_dict() for item in self.structural_diagnostics],
            "questions": [item.to_dict() for item in self.questions],
            "ready": self.ready,
        }


def _structural_diagnostics(feb: FEBInspection) -> tuple[PreflightDiagnostic, ...]:
    diagnostics: list[PreflightDiagnostic] = []
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
                message=f"XML {kind} identifier {identifier!r} is defined more than once",
                location=f"/{feb.root_tag}",
            )
        )
    return tuple(diagnostics)


def _condition_name(value: str | PhysicalConditionName) -> str:
    return value.value if isinstance(value, PhysicalConditionName) else value


def _authoritative_evidence(
    facts: tuple[MissingConditionFact, ...],
    unresolved: tuple[UnresolvedEvidenceField, ...],
) -> tuple[EvidenceProvenance, ...]:
    evidence: list[EvidenceProvenance] = []
    for fact in facts:
        for provenance in fact.evidence:
            if provenance.authoritative and provenance not in evidence:
                evidence.append(provenance)
    for field in unresolved:
        for provenance in field.evidence:
            if provenance.authoritative and provenance not in evidence:
                evidence.append(provenance)
    return tuple(evidence)


def _questions(completeness: CompletenessResult) -> tuple[MissingConditionQuestion, ...]:
    missing: dict[str, list[MissingConditionFact]] = {}
    unresolved: dict[str, list[UnresolvedEvidenceField]] = {}
    resolved = {_condition_name(item) for item in completeness.resolved}
    for fact in completeness.missing:
        missing.setdefault(fact.condition, []).append(fact)
    for field in completeness.unresolved:
        if field.required:
            unresolved.setdefault(field.field_name, []).append(field)

    questions: list[MissingConditionQuestion] = []
    seen: set[str] = set()
    for raw_name in completeness.required:
        condition = _condition_name(raw_name)
        if condition in seen:
            continue
        seen.add(condition)
        if condition in resolved:
            continue
        facts = tuple(missing.get(condition, ()))
        fields = tuple(unresolved.get(condition, ()))
        authoritative_fact = next((item for item in facts if item.authoritative), None)
        evidence = _authoritative_evidence(facts, fields)
        reason_source = authoritative_fact or next(
            (item for item in fields if any(source.authoritative for source in item.evidence)),
            None,
        )
        if reason_source is None:
            reason_source = facts[0] if facts else (fields[0] if fields else None)
        if reason_source is None:
            continue
        reason = reason_source.reason
        questions.append(MissingConditionQuestion(condition, reason, evidence))
    return tuple(questions)


def inspect_incomplete_feb(
    feb: FEBInspection, completeness: CompletenessResult
) -> IncompleteFebInventory:
    if not isinstance(feb, FEBInspection):
        raise TypeError("feb must be a FEBInspection")
    if not isinstance(completeness, CompletenessResult):
        raise TypeError("completeness must be a CompletenessResult")
    diagnostics = _structural_diagnostics(feb)
    questions = _questions(completeness)
    unresolved_required = any(
        item.required and item.field_name in completeness.required
        for item in completeness.unresolved
    )
    ready = (
        not diagnostics
        and completeness.state == "BOUND"
        and not questions
        and not unresolved_required
    )
    return IncompleteFebInventory(diagnostics, questions, ready)


__all__ = ["IncompleteFebInventory", "MissingConditionQuestion", "inspect_incomplete_feb"]
