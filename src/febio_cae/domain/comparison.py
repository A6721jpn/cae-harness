"""Immutable comparison request records without comparison algorithms."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
import math

from .canonical import canonical_bytes

SCHEMA_VERSION = "1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ComparisonValidationError(ValueError):
    """Raised when a comparison request is structurally invalid."""


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ComparisonValidationError(
            f"{field_name} must be non-empty text without surrounding whitespace"
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ComparisonValidationError(f"{field_name} contains a control character")
    return value


def _digest_or_id(value: object, field_name: str) -> str:
    value = _text(value, field_name)
    if len(value) == 64 and _SHA256.fullmatch(value) is None:
        raise ComparisonValidationError(f"{field_name} has invalid digest syntax")
    return value


def _texts(value: object, field_name: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ComparisonValidationError(f"{field_name} must be a sequence")
    result = tuple(_text(item, f"{field_name}[]") for item in value)
    if not allow_empty and not result:
        raise ComparisonValidationError(f"{field_name} must not be empty")
    if len(set(result)) != len(result):
        raise ComparisonValidationError(f"{field_name} must not contain duplicates")
    return result


def _finite(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ComparisonValidationError(f"{field_name} must be finite numeric data")
    result = float(value)
    if not math.isfinite(result):
        raise ComparisonValidationError(f"{field_name} must be finite numeric data")
    return result


@dataclass(frozen=True, slots=True)
class ComparisonInterval:
    """One common finite interval used by both comparison runs."""

    unit: str
    lower: float
    upper: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "unit", _text(self.unit, "unit"))
        lower = _finite(self.lower, "lower")
        upper = _finite(self.upper, "upper")
        if lower >= upper:
            raise ComparisonValidationError("interval lower bound must be less than upper bound")
        object.__setattr__(self, "lower", lower)
        object.__setattr__(self, "upper", upper)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "unit": self.unit,
            "lower": self.lower,
            "upper": self.upper,
        }


@dataclass(frozen=True, slots=True)
class ComparisonAxis:
    axis_id: str
    unit: str
    roi_id: str
    measure_id: str
    aggregation_id: str
    interval: ComparisonInterval
    interpolation: str

    def __post_init__(self) -> None:
        for field_name in ("axis_id", "unit", "roi_id", "measure_id", "aggregation_id"):
            object.__setattr__(self, field_name, _text(getattr(self, field_name), field_name))
        if not isinstance(self.interval, ComparisonInterval):
            raise ComparisonValidationError("interval must be a ComparisonInterval")
        if self.interval.unit != self.unit:
            raise ComparisonValidationError("interval unit must match axis unit")
        interpolation = _text(self.interpolation, "interpolation").lower()
        if interpolation not in {"linear", "nearest", "step"}:
            raise ComparisonValidationError("unsupported interpolation method")
        object.__setattr__(self, "interpolation", interpolation)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "axis_id": self.axis_id,
            "unit": self.unit,
            "roi_id": self.roi_id,
            "measure_id": self.measure_id,
            "aggregation_id": self.aggregation_id,
            "interval": self.interval.to_dict(),
            "interpolation": self.interpolation,
        }


@dataclass(frozen=True, slots=True)
class ComparisonSpec:
    comparison_id: str
    baseline_manifest_id: str
    candidate_manifest_id: str
    intended_changes: Sequence[str]
    fixed_conditions: Sequence[str]
    axes: Sequence[ComparisonAxis]

    def __post_init__(self) -> None:
        object.__setattr__(self, "comparison_id", _text(self.comparison_id, "comparison_id"))
        object.__setattr__(
            self,
            "baseline_manifest_id",
            _digest_or_id(self.baseline_manifest_id, "baseline_manifest_id"),
        )
        object.__setattr__(
            self,
            "candidate_manifest_id",
            _digest_or_id(self.candidate_manifest_id, "candidate_manifest_id"),
        )
        object.__setattr__(
            self, "intended_changes", _texts(self.intended_changes, "intended_changes")
        )
        object.__setattr__(
            self, "fixed_conditions", _texts(self.fixed_conditions, "fixed_conditions")
        )
        axes = tuple(self.axes)
        if not axes or any(not isinstance(item, ComparisonAxis) for item in axes):
            raise ComparisonValidationError("axes must be a non-empty sequence of ComparisonAxis")
        if len({item.axis_id for item in axes}) != len(axes):
            raise ComparisonValidationError("axes contains duplicate IDs")
        object.__setattr__(self, "axes", axes)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "comparison_id": self.comparison_id,
            "baseline_manifest_id": self.baseline_manifest_id,
            "candidate_manifest_id": self.candidate_manifest_id,
            "intended_changes": list(self.intended_changes),
            "fixed_conditions": list(self.fixed_conditions),
            "axes": [item.to_dict() for item in self.axes],
        }

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


__all__ = [
    "SCHEMA_VERSION",
    "ComparisonAxis",
    "ComparisonInterval",
    "ComparisonSpec",
    "ComparisonValidationError",
]
