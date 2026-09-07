"""Immutable numerical quality-criterion intent and applicability grounds."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import cast

from .canonical import canonical_bytes
from .evidence import EvidenceRef
from .mesh_policy import NumericalProfileRef
from .units import Quantity

SCHEMA_VERSION = "1"
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class QualityPolicyValidationError(ValueError):
    """Raised when a quality-policy intent value is structurally invalid."""


def _require_identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise QualityPolicyValidationError(
            f"{field} must be a non-empty ASCII identifier starting with a letter or underscore"
        )
    return value


def _require_quantity(value: object, field: str) -> Quantity:
    if not isinstance(value, Quantity):
        raise QualityPolicyValidationError(f"{field} must be a Quantity")
    try:
        value.to_si()
    except (TypeError, ValueError, UnicodeError) as error:
        raise QualityPolicyValidationError(f"{field} must be SI-representable") from error
    return value


def _quantity_dict(value: Quantity) -> dict[str, float | str]:
    si_value = value.to_si()
    return {"value": float(si_value.value), "unit": si_value.unit}


def _copy_sequence(value: object, field: str) -> tuple[object, ...]:
    if isinstance(value, (str, bytes, bytearray, Mapping, AbstractSet)) or not isinstance(
        value, Sequence
    ):
        raise QualityPolicyValidationError(f"{field} must be a sequence")
    return tuple(value)


def _require_reason(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise QualityPolicyValidationError(
            "applicability_reason must be a non-empty string without surrounding whitespace"
        )
    if any(ord(character) < 32 for character in value):
        raise QualityPolicyValidationError("applicability_reason contains a control character")
    return value


def _require_evidence(value: object, criterion_id: str) -> EvidenceRef:
    if not isinstance(value, EvidenceRef):
        raise QualityPolicyValidationError("evidence must be an EvidenceRef")
    target_field = f"quality_policy.criteria.{criterion_id}"
    if value.target_field != target_field:
        raise QualityPolicyValidationError(
            f"evidence must target exactly {target_field!r}, got {value.target_field!r}"
        )
    return value


@dataclass(frozen=True, slots=True)
class QualityThreshold:
    """One typed numerical parameter for a declared quality method."""

    parameter_id: str
    value: Quantity

    def __post_init__(self) -> None:
        parameter_id = _require_identifier(self.parameter_id, "parameter_id")
        value = _require_quantity(self.value, "value")
        object.__setattr__(self, "parameter_id", parameter_id)
        object.__setattr__(self, "value", value)
        try:
            self.to_bytes()
        except (TypeError, ValueError, UnicodeError) as error:
            raise QualityPolicyValidationError(
                "quality threshold is not canonically serializable"
            ) from error

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "parameter_id": self.parameter_id,
            "value": _quantity_dict(self.value),
        }

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


@dataclass(frozen=True, slots=True)
class QualityCriterion:
    """One explicit quality obligation and its applicability grounds."""

    criterion_id: str
    metric_id: str
    evaluation_ids: Sequence[str]
    thresholds: Sequence[QualityThreshold]
    applicability_reason: str
    evidence: EvidenceRef

    def __post_init__(self) -> None:
        criterion_id = _require_identifier(self.criterion_id, "criterion_id")
        metric_id = _require_identifier(self.metric_id, "metric_id")

        raw_evaluation_ids = _copy_sequence(self.evaluation_ids, "evaluation_ids")
        evaluation_ids = tuple(
            _require_identifier(value, f"evaluation_ids[{index}]")
            for index, value in enumerate(raw_evaluation_ids)
        )
        if len(set(evaluation_ids)) != len(evaluation_ids):
            duplicate = min(
                identifier
                for identifier in set(evaluation_ids)
                if evaluation_ids.count(identifier) > 1
            )
            raise QualityPolicyValidationError(f"duplicate evaluation_id: {duplicate}")
        evaluation_ids = tuple(sorted(evaluation_ids))

        raw_thresholds = _copy_sequence(self.thresholds, "thresholds")
        if any(not isinstance(item, QualityThreshold) for item in raw_thresholds):
            raise QualityPolicyValidationError("thresholds contains an invalid QualityThreshold")
        thresholds = cast(tuple[QualityThreshold, ...], tuple(raw_thresholds))
        parameter_ids = [item.parameter_id for item in thresholds]
        if len(set(parameter_ids)) != len(parameter_ids):
            duplicate = min(
                identifier
                for identifier in set(parameter_ids)
                if parameter_ids.count(identifier) > 1
            )
            raise QualityPolicyValidationError(f"duplicate parameter_id: {duplicate}")
        thresholds = tuple(sorted(thresholds, key=lambda item: item.parameter_id))

        applicability_reason = _require_reason(self.applicability_reason)
        evidence = _require_evidence(self.evidence, criterion_id)

        object.__setattr__(self, "criterion_id", criterion_id)
        object.__setattr__(self, "metric_id", metric_id)
        object.__setattr__(self, "evaluation_ids", evaluation_ids)
        object.__setattr__(self, "thresholds", thresholds)
        object.__setattr__(self, "applicability_reason", applicability_reason)
        object.__setattr__(self, "evidence", evidence)
        try:
            self.to_bytes()
        except (TypeError, ValueError, UnicodeError) as error:
            raise QualityPolicyValidationError(
                "quality criterion is not canonically serializable"
            ) from error

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "criterion_id": self.criterion_id,
            "metric_id": self.metric_id,
            "evaluation_ids": list(self.evaluation_ids),
            "thresholds": [threshold.to_dict() for threshold in self.thresholds],
            "applicability_reason": self.applicability_reason,
            "evidence": self.evidence.to_dict(),
        }

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


@dataclass(frozen=True, slots=True)
class QualityPolicy:
    """Immutable quality obligations bounded by one quality profile reference."""

    profile: NumericalProfileRef
    criteria: Sequence[QualityCriterion]

    def __post_init__(self) -> None:
        if not isinstance(self.profile, NumericalProfileRef):
            raise QualityPolicyValidationError("profile must be a NumericalProfileRef")
        if self.profile.purpose != "quality":
            raise QualityPolicyValidationError("profile must have quality purpose")

        raw_criteria = _copy_sequence(self.criteria, "criteria")
        if not raw_criteria:
            raise QualityPolicyValidationError("criteria must be nonempty")
        if any(not isinstance(item, QualityCriterion) for item in raw_criteria):
            raise QualityPolicyValidationError("criteria contains an invalid QualityCriterion")
        criteria = cast(tuple[QualityCriterion, ...], tuple(raw_criteria))
        criterion_ids = [item.criterion_id for item in criteria]
        if len(set(criterion_ids)) != len(criterion_ids):
            duplicate = min(
                identifier
                for identifier in set(criterion_ids)
                if criterion_ids.count(identifier) > 1
            )
            raise QualityPolicyValidationError(f"duplicate criterion_id: {duplicate}")
        criteria = tuple(sorted(criteria, key=lambda item: item.criterion_id))

        object.__setattr__(self, "criteria", criteria)
        try:
            self.to_bytes()
        except (TypeError, ValueError, UnicodeError) as error:
            raise QualityPolicyValidationError(
                "quality policy is not canonically serializable"
            ) from error

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "profile": self.profile.to_dict(),
            "criteria": [criterion.to_dict() for criterion in self.criteria],
        }

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


__all__ = [
    "SCHEMA_VERSION",
    "QualityCriterion",
    "QualityPolicy",
    "QualityPolicyValidationError",
    "QualityThreshold",
]
