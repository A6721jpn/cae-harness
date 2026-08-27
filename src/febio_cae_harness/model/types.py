"""Typed evidence and physical-condition records.

These records intentionally do not assign physical meaning to geometry,
element names, or file conventions.  A condition is resolved only when its
value is accompanied by explicit authoritative provenance.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from ._immutability import freeze_value, thaw_json

ASK_AND_BLOCK = "ASK_AND_BLOCK"


class PhysicalConditionName(StrEnum):
    """Canonical names for physical conditions that may be required."""

    ENGINEERING_QUESTION = "engineering_question"
    UNITS = "units"
    MATERIAL = "material"
    LOADS = "loads"
    CONSTRAINTS = "constraints"
    CONTACT = "contact"
    ANALYSIS_STEP = "analysis_step"
    ROI = "roi"
    LOAD_PATH = "load_path"
    EVALUATION_QUANTITIES = "evaluation_quantities"
    MESH = "mesh"
    ELEMENT = "element"


ConditionName = PhysicalConditionName


class IntentImpact(StrEnum):
    """Impact classification for a proposed model change."""

    INTENT_PRESERVING = "INTENT_PRESERVING"
    INTENT_SENSITIVE = "INTENT_SENSITIVE"
    INTENT_CHANGING = "INTENT_CHANGING"


@dataclass(frozen=True, slots=True)
class EvidenceProvenance:
    """Where an explicit fact came from.

    ``authoritative`` describes the source's authority for the fact; it is
    never inferred from a filename, geometry, or a naming convention.
    """

    source: str
    location: str = ""
    excerpt: str | None = None
    authoritative: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("evidence source must be a non-empty string")
        if not isinstance(self.location, str):
            raise TypeError("evidence location must be a string")
        if self.excerpt is not None and not isinstance(self.excerpt, str):
            raise TypeError("evidence excerpt must be a string or None")

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "location": self.location,
            "excerpt": self.excerpt,
            "authoritative": self.authoritative,
        }


def normalise_provenance(
    value: EvidenceProvenance | Iterable[EvidenceProvenance] | None,
) -> tuple[EvidenceProvenance, ...]:
    """Normalize one provenance record or a sequence to an immutable tuple."""

    if value is None:
        return ()
    if isinstance(value, EvidenceProvenance):
        return (value,)
    result = tuple(value)
    if not all(isinstance(item, EvidenceProvenance) for item in result):
        raise TypeError("provenance must contain EvidenceProvenance records")
    return result


@dataclass(frozen=True, slots=True)
class ConditionEvidence:
    """An explicit condition value and the provenance supporting it."""

    condition: str
    value: object | None
    provenance: tuple[EvidenceProvenance, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.condition, PhysicalConditionName):
            object.__setattr__(self, "condition", self.condition.value)
        if not isinstance(self.condition, str) or not self.condition.strip():
            raise ValueError("condition must be a non-empty string")
        provenance = normalise_provenance(self.provenance)
        object.__setattr__(self, "provenance", provenance)
        object.__setattr__(self, "value", freeze_value(self.value))

    @property
    def authoritative(self) -> bool:
        """Whether at least one supporting source is authoritative."""

        return any(item.authoritative for item in self.provenance)

    def to_dict(self) -> dict[str, object]:
        return {
            "condition": self.condition,
            "value": thaw_json(self.value),
            "provenance": [item.to_dict() for item in self.provenance],
            "authoritative": self.authoritative,
        }


@dataclass(frozen=True, slots=True)
class UnresolvedEvidenceField:
    """A required named field that cannot be resolved from authority."""

    field_name: str
    reason: str
    value: object | None = None
    evidence: tuple[EvidenceProvenance, ...] = ()
    required: bool = True

    def __post_init__(self) -> None:
        if isinstance(self.field_name, PhysicalConditionName):
            object.__setattr__(self, "field_name", self.field_name.value)
        if not isinstance(self.field_name, str) or not self.field_name.strip():
            raise ValueError("field_name must be a non-empty string")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("reason must be a non-empty string")
        evidence = normalise_provenance(self.evidence)
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(self, "value", freeze_value(self.value))

    @property
    def condition(self) -> str:
        """Alias used by completeness and preflight consumers."""

        return self.field_name

    def to_dict(self) -> dict[str, object]:
        return {
            "field_name": self.field_name,
            "reason": self.reason,
            "value": thaw_json(self.value),
            "evidence": [item.to_dict() for item in self.evidence],
            "required": self.required,
        }


@dataclass(frozen=True, slots=True)
class MissingConditionFact:
    """Authoritative fact that a required condition is missing.

    The action is deliberately fixed to ``ASK_AND_BLOCK``.  This fact records
    the absence of authoritative evidence, never a guessed physical value.
    """

    condition: str
    reason: str
    evidence: tuple[EvidenceProvenance, ...] = ()
    action: str = ASK_AND_BLOCK
    authoritative: bool = True

    def __post_init__(self) -> None:
        if isinstance(self.condition, PhysicalConditionName):
            object.__setattr__(self, "condition", self.condition.value)
        if not isinstance(self.condition, str) or not self.condition.strip():
            raise ValueError("condition must be a non-empty string")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("reason must be a non-empty string")
        if self.action != ASK_AND_BLOCK:
            raise ValueError("missing conditions must use ASK_AND_BLOCK")
        evidence = normalise_provenance(self.evidence)
        object.__setattr__(self, "evidence", evidence)

    @property
    def field_name(self) -> str:
        """Alias matching :class:`UnresolvedEvidenceField`."""

        return self.condition

    def to_dict(self) -> dict[str, object]:
        return {
            "condition": self.condition,
            "reason": self.reason,
            "evidence": [item.to_dict() for item in self.evidence],
            "action": self.action,
            "authoritative": self.authoritative,
        }


def normalise_evidence(value: Any, condition: str) -> ConditionEvidence:
    """Coerce common evidence-shaped inputs without assigning authority."""

    if isinstance(value, ConditionEvidence):
        if value.condition == condition:
            return value
        return ConditionEvidence(condition, value.value, value.provenance)
    if isinstance(value, EvidenceProvenance):
        return ConditionEvidence(condition, None, (value,))
    return ConditionEvidence(condition, value, ())


ConditionFact = ConditionEvidence
PhysicalCondition = ConditionEvidence
UnresolvedCondition = UnresolvedEvidenceField
MissingCondition = MissingConditionFact


__all__ = [
    "ASK_AND_BLOCK",
    "ConditionEvidence",
    "ConditionFact",
    "ConditionName",
    "EvidenceProvenance",
    "IntentImpact",
    "MissingConditionFact",
    "MissingCondition",
    "PhysicalCondition",
    "PhysicalConditionName",
    "UnresolvedCondition",
    "UnresolvedEvidenceField",
    "normalise_evidence",
    "normalise_provenance",
]
