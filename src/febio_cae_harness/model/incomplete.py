"""Incomplete FEB inventory and evidence-backed physical questions."""

from __future__ import annotations

from dataclasses import dataclass

from ..evidence import EvidenceIntegrityError, IntentSnapshotAuthority
from ._immutability import freeze_json
from .completeness import CompletenessResult, _validated_authoritative_result
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
        diagnostics = tuple(object.__getattribute__(self, "structural_diagnostics"))
        questions = tuple(object.__getattribute__(self, "questions"))
        if not all(isinstance(item, PreflightDiagnostic) for item in diagnostics):
            raise TypeError("structural_diagnostics must contain PreflightDiagnostic records")
        if not all(isinstance(item, MissingConditionQuestion) for item in questions):
            raise TypeError("questions must contain MissingConditionQuestion records")
        ready = object.__getattribute__(self, "ready")
        if not isinstance(ready, bool):
            raise TypeError("ready must be a bool")
        object.__setattr__(self, "structural_diagnostics", diagnostics)
        object.__setattr__(self, "questions", questions)

    def __getattribute__(self, name: str) -> object:
        if name == "questions":
            state = _safe_inventory_binding(self)
            return () if state is None else state.questions
        if name == "ready":
            state = _safe_inventory_binding(self)
            return False if state is None else state.ready
        return object.__getattribute__(self, name)

    def to_dict(self) -> dict[str, object]:
        state = _safe_inventory_binding(self)
        if state is None:
            diagnostics = object.__getattribute__(self, "structural_diagnostics")
            questions: tuple[MissingConditionQuestion, ...] = ()
            ready = False
        else:
            diagnostics = state.structural_diagnostics
            questions = state.questions
            ready = state.ready
        return {
            "structural_diagnostics": [item.to_dict() for item in diagnostics],
            "questions": [item.to_dict() for item in questions],
            "ready": ready,
        }


@dataclass(frozen=True, slots=True)
class _IncompleteFebInventoryBinding:
    inventory: IncompleteFebInventory
    structural_diagnostics: tuple[PreflightDiagnostic, ...]
    questions: tuple[MissingConditionQuestion, ...]
    ready: bool
    completeness: CompletenessResult | None
    snapshot: IntentSnapshotAuthority | None
    projection: object


_INCOMPLETE_INVENTORY_STATES: dict[int, _IncompleteFebInventoryBinding] = {}


def _inventory_projection(inventory: IncompleteFebInventory) -> object:
    try:
        return freeze_json(
            {
                "structural_diagnostics": [
                    item.to_dict()
                    for item in object.__getattribute__(inventory, "structural_diagnostics")
                ],
                "questions": [
                    item.to_dict() for item in object.__getattribute__(inventory, "questions")
                ],
                "ready": object.__getattribute__(inventory, "ready"),
            }
        )
    except Exception as error:
        raise EvidenceIntegrityError("incomplete FEB inventory projection is invalid") from error


def _validated_inventory_binding(
    inventory: IncompleteFebInventory,
) -> _IncompleteFebInventoryBinding:
    state = _INCOMPLETE_INVENTORY_STATES.get(id(inventory))
    if state is None or state.inventory is not inventory:
        raise EvidenceIntegrityError("incomplete FEB inventory is not authority-issued")
    if state.completeness is not None:
        if state.snapshot is None:
            raise EvidenceIntegrityError("incomplete FEB inventory snapshot binding is invalid")
        _validated_authoritative_result(state.completeness, state.snapshot)
    if _inventory_projection(inventory) != state.projection:
        raise EvidenceIntegrityError("incomplete FEB inventory projection changed")
    if state.completeness is not None:
        if state.snapshot is None:  # pragma: no cover - guarded above
            raise EvidenceIntegrityError("incomplete FEB inventory snapshot binding is invalid")
        _validated_authoritative_result(state.completeness, state.snapshot)
        # Keep one final live check after the action projection was consumed.
        _validated_authoritative_result(state.completeness, state.snapshot)
    return state


def _safe_inventory_binding(
    inventory: IncompleteFebInventory,
) -> _IncompleteFebInventoryBinding | None:
    try:
        return _validated_inventory_binding(inventory)
    except Exception:
        return None


def _issue_inventory(
    structural_diagnostics: tuple[PreflightDiagnostic, ...],
    questions: tuple[MissingConditionQuestion, ...],
    ready: bool,
    *,
    completeness: CompletenessResult | None,
    snapshot: IntentSnapshotAuthority | None,
) -> IncompleteFebInventory:
    inventory = IncompleteFebInventory(structural_diagnostics, questions, ready)
    projection = _inventory_projection(inventory)
    _INCOMPLETE_INVENTORY_STATES[id(inventory)] = _IncompleteFebInventoryBinding(
        inventory=inventory,
        structural_diagnostics=tuple(structural_diagnostics),
        questions=tuple(questions),
        ready=ready,
        completeness=completeness,
        snapshot=snapshot,
        projection=projection,
    )
    return inventory


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


def _questions(
    required: tuple[str, ...],
    resolved_values: tuple[str, ...],
    missing_values: tuple[MissingConditionFact, ...],
    unresolved_values: tuple[UnresolvedEvidenceField, ...],
) -> tuple[MissingConditionQuestion, ...]:
    missing: dict[str, list[MissingConditionFact]] = {}
    unresolved: dict[str, list[UnresolvedEvidenceField]] = {}
    resolved = {_condition_name(item) for item in resolved_values}
    for fact in missing_values:
        missing.setdefault(fact.condition, []).append(fact)
    for field in unresolved_values:
        if field.required:
            unresolved.setdefault(field.field_name, []).append(field)

    questions: list[MissingConditionQuestion] = []
    seen: set[str] = set()
    for raw_name in required:
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
    feb: FEBInspection,
    completeness: CompletenessResult,
    *,
    snapshot: IntentSnapshotAuthority | None = None,
) -> IncompleteFebInventory:
    if not isinstance(feb, FEBInspection):
        raise TypeError("feb must be a FEBInspection")
    if not isinstance(completeness, CompletenessResult):
        raise TypeError("completeness must be a CompletenessResult")
    diagnostics = _structural_diagnostics(feb)
    try:
        _validated_authoritative_result(completeness, snapshot)
        required = tuple(completeness.required)
        resolved = tuple(completeness.resolved)
        missing = tuple(completeness.missing)
        unresolved = tuple(completeness.unresolved)
        state = completeness.state
        questions = _questions(required, resolved, missing, unresolved)
        unresolved_required = any(
            item.required and item.field_name in required for item in unresolved
        )
        # Revalidate after consuming every result field used for action.
        _validated_authoritative_result(completeness, snapshot)
    except EvidenceIntegrityError:
        return _issue_inventory(
            diagnostics,
            (),
            False,
            completeness=None,
            snapshot=None,
        )
    except Exception:
        return _issue_inventory(
            diagnostics,
            (),
            False,
            completeness=None,
            snapshot=None,
        )
    ready = not diagnostics and state == "BOUND" and not questions and not unresolved_required
    return _issue_inventory(
        diagnostics,
        questions,
        ready,
        completeness=completeness,
        snapshot=snapshot,
    )


__all__ = ["IncompleteFebInventory", "MissingConditionQuestion", "inspect_incomplete_feb"]
