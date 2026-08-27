"""Completeness checks that stop when physical evidence is insufficient."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .types import (
    ASK_AND_BLOCK,
    ConditionEvidence,
    EvidenceProvenance,
    MissingConditionFact,
    PhysicalConditionName,
    UnresolvedEvidenceField,
    normalise_evidence,
)

DEFAULT_REQUIRED_CONDITIONS: tuple[str, ...] = (
    PhysicalConditionName.ENGINEERING_QUESTION.value,
    PhysicalConditionName.UNITS.value,
    PhysicalConditionName.MATERIAL.value,
    PhysicalConditionName.LOADS.value,
    PhysicalConditionName.CONSTRAINTS.value,
    PhysicalConditionName.CONTACT.value,
    PhysicalConditionName.ANALYSIS_STEP.value,
    PhysicalConditionName.ROI.value,
    PhysicalConditionName.EVALUATION_QUANTITIES.value,
)


def _condition_name(value: str | PhysicalConditionName) -> str:
    if isinstance(value, PhysicalConditionName):
        return value.value
    if not isinstance(value, str) or not value.strip():
        raise ValueError("condition names must be non-empty strings")
    return value


def _provenance(value: Any) -> tuple[EvidenceProvenance, ...]:
    if value is None:
        return ()
    if isinstance(value, EvidenceProvenance):
        return (value,)
    if isinstance(value, Mapping):
        value = (value,)
    if isinstance(value, (list, tuple)):
        result: list[EvidenceProvenance] = []
        for item in value:
            if isinstance(item, EvidenceProvenance):
                result.append(item)
            elif isinstance(item, Mapping):
                result.append(
                    EvidenceProvenance(
                        source=str(item.get("source", "")),
                        location=str(item.get("location", "")),
                        excerpt=(None if item.get("excerpt") is None else str(item.get("excerpt"))),
                        authoritative=bool(item.get("authoritative", False)),
                    )
                )
            else:
                raise TypeError("provenance must contain EvidenceProvenance records")
        return tuple(result)
    raise TypeError("provenance must be a record or sequence of records")


def _coerce_condition_evidence(name: str, value: Any) -> ConditionEvidence:
    if isinstance(value, ConditionEvidence):
        return normalise_evidence(value, name)
    if isinstance(value, Mapping) and ("value" in value or "provenance" in value):
        return ConditionEvidence(
            name,
            value.get("value"),
            _provenance(value.get("provenance")),
        )
    return normalise_evidence(value, name)


@dataclass(frozen=True, slots=True)
class CompletenessResult:
    """Evidence-backed completeness result.

    ``complete`` means all requested names have explicit values and at least
    one authoritative provenance record.  It says nothing about solver or
    FEBio success.
    """

    required: tuple[str, ...]
    resolved: tuple[str, ...]
    missing: tuple[MissingConditionFact, ...]
    unresolved: tuple[UnresolvedEvidenceField, ...]
    evidence: tuple[ConditionEvidence, ...] = ()
    state: str = ASK_AND_BLOCK

    def __post_init__(self) -> None:
        required = tuple(self.required)
        resolved = tuple(self.resolved)
        missing = tuple(self.missing)
        unresolved = tuple(self.unresolved)
        evidence = tuple(self.evidence)
        if not all(isinstance(item, str) and item for item in required + resolved):
            raise TypeError("required and resolved condition names must be strings")
        if not all(isinstance(item, MissingConditionFact) for item in missing):
            raise TypeError("missing must contain MissingConditionFact records")
        if not all(isinstance(item, UnresolvedEvidenceField) for item in unresolved):
            raise TypeError("unresolved must contain UnresolvedEvidenceField records")
        if not all(isinstance(item, ConditionEvidence) for item in evidence):
            raise TypeError("evidence must contain ConditionEvidence records")
        if self.state not in {"BOUND", ASK_AND_BLOCK}:
            raise ValueError("state must be BOUND or ASK_AND_BLOCK")
        object.__setattr__(self, "required", required)
        object.__setattr__(self, "resolved", resolved)
        object.__setattr__(self, "missing", missing)
        object.__setattr__(self, "unresolved", unresolved)
        object.__setattr__(self, "evidence", evidence)

    @property
    def complete(self) -> bool:
        return not self.missing and not self.unresolved

    @property
    def is_complete(self) -> bool:
        return self.complete

    @property
    def ask_and_block(self) -> bool:
        return self.state == ASK_AND_BLOCK

    @property
    def missing_conditions(self) -> tuple[MissingConditionFact, ...]:
        return self.missing

    @property
    def missing_condition_facts(self) -> tuple[MissingConditionFact, ...]:
        return self.missing

    @property
    def unresolved_fields(self) -> tuple[UnresolvedEvidenceField, ...]:
        return self.unresolved

    @property
    def evidence_by_condition(self) -> Mapping[str, ConditionEvidence]:
        return MappingProxyType({item.condition: item for item in self.evidence})

    def to_dict(self) -> dict[str, object]:
        return {
            "required": list(self.required),
            "resolved": list(self.resolved),
            "missing": [item.to_dict() for item in self.missing],
            "unresolved": [item.to_dict() for item in self.unresolved],
            "evidence": [item.to_dict() for item in self.evidence],
            "complete": self.complete,
            "state": self.state,
        }


def assess_completeness(
    required_conditions: Iterable[str | PhysicalConditionName] | Mapping[str, Any] | None = None,
    evidence: Mapping[str, Any] | None = None,
) -> CompletenessResult:
    """Assess required condition names using only explicit evidence.

    When the first argument is a mapping and ``evidence`` is omitted, its keys
    are treated as the required names and its values as evidence.  A bare value
    is intentionally *not* authoritative; callers must attach an
    :class:`EvidenceProvenance` with ``authoritative=True``.
    """

    if isinstance(required_conditions, Mapping):
        if evidence is not None:
            raise TypeError("evidence must be omitted when required_conditions is a mapping")
        evidence = required_conditions
        required = tuple(_condition_name(name) for name in required_conditions)
    elif required_conditions is None:
        required = DEFAULT_REQUIRED_CONDITIONS
    else:
        required = tuple(_condition_name(item) for item in required_conditions)

    if len(set(required)) != len(required):
        raise ValueError("required condition names must be unique")
    supplied = evidence or {}
    records: list[ConditionEvidence] = []
    resolved: list[str] = []
    missing: list[MissingConditionFact] = []
    unresolved: list[UnresolvedEvidenceField] = []

    for name in required:
        record = _coerce_condition_evidence(name, supplied[name]) if name in supplied else None
        if record is not None:
            records.append(record)
        if record is not None and record.value is not None and record.authoritative:
            resolved.append(name)
            continue

        if record is None:
            reason = "No authoritative evidence was supplied for this required condition"
            value = None
            support: tuple[EvidenceProvenance, ...] = ()
        elif record.value is None:
            reason = "Authoritative evidence did not provide a condition value"
            value = None
            support = record.provenance
        else:
            reason = "The supplied condition value has no authoritative provenance"
            value = record.value
            support = record.provenance

        unresolved.append(
            UnresolvedEvidenceField(
                field_name=name,
                reason=reason,
                value=value,
                evidence=support,
            )
        )
        # This is an authoritative completeness *fact*, not an authoritative
        # physical value.  The provenance identifies the check that produced
        # the absence and makes the stop reason auditable.
        fact_basis = EvidenceProvenance(
            source="completeness-check",
            location=name,
            excerpt="required condition unresolved",
            authoritative=True,
        )
        missing.append(
            MissingConditionFact(
                condition=name,
                reason=reason,
                evidence=(fact_basis,),
            )
        )

    state = "BOUND" if not missing else ASK_AND_BLOCK
    return CompletenessResult(
        required=required,
        resolved=tuple(resolved),
        missing=tuple(missing),
        unresolved=tuple(unresolved),
        evidence=tuple(records),
        state=state,
    )


def assess_intent_completeness(
    intent: Mapping[str, Any] | Any,
    required_conditions: Iterable[str | PhysicalConditionName] | None = None,
) -> CompletenessResult:
    """Assess an intent projection while preserving its explicit sources.

    ``IntentContract`` exposes a ``to_dict`` projection.  This adapter keeps
    the model package independent of that common contract while allowing a
    caller to pass either the contract or an equivalent mapping.
    """

    payload: Mapping[str, Any]
    if isinstance(intent, Mapping):
        payload = intent
    elif hasattr(intent, "to_dict"):
        projected = intent.to_dict()
        if not isinstance(projected, Mapping):
            raise TypeError("intent.to_dict() must return a mapping")
        payload = projected
    else:
        raise TypeError("intent must be a mapping or expose to_dict()")
    names = (
        tuple(_condition_name(item) for item in required_conditions)
        if required_conditions is not None
        else DEFAULT_REQUIRED_CONDITIONS
    )
    raw_sources = payload.get("condition_sources", {})
    sources = raw_sources if isinstance(raw_sources, Mapping) else {}
    records: dict[str, ConditionEvidence] = {}
    for name in names:
        if name not in payload:
            continue
        records[name] = ConditionEvidence(
            condition=name,
            value=payload[name],
            provenance=_provenance(sources.get(name)),
        )
    return assess_completeness(names, records)


check_completeness = assess_completeness
evaluate_completeness = assess_completeness
completeness_result = assess_completeness
Completeness = CompletenessResult
check_intent_completeness = assess_intent_completeness
completeness_from_intent = assess_intent_completeness


__all__ = [
    "Completeness",
    "CompletenessResult",
    "DEFAULT_REQUIRED_CONDITIONS",
    "assess_completeness",
    "assess_intent_completeness",
    "check_completeness",
    "check_intent_completeness",
    "completeness_from_intent",
    "evaluate_completeness",
    "completeness_result",
]
