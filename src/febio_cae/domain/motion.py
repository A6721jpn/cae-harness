"""Immutable, frame-explicit monotonic motion intent values."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import cast

from .canonical import canonical_bytes
from .evidence import EvidenceRef
from .spatial import FrameId, Point3, SpatialValidationError, UnitDirection
from .units import Dimension, Quantity

SCHEMA_VERSION = "1"
_TIME = Dimension(time=1)
_LENGTH = Dimension(length=1)


class MotionValidationError(ValueError):
    """Raised when a motion intent or its applicability is invalid."""


def _require_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise MotionValidationError(
            f"{field} must be a non-empty string without surrounding whitespace"
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise MotionValidationError(f"{field} contains a control character")
    return value


def _strict_mapping(
    value: object, expected_keys: frozenset[str], field: str
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise MotionValidationError(f"{field} must be an object")
    keys = set(value.keys())
    if any(not isinstance(key, str) for key in keys) or keys != expected_keys:
        raise MotionValidationError(f"{field} has unknown or missing fields")
    return cast(Mapping[str, object], value)


def _quantity_from_dict(value: object, field: str) -> Quantity:
    payload = _strict_mapping(value, frozenset({"value", "unit"}), field)
    raw_value = payload["value"]
    raw_unit = payload["unit"]
    if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
        raise MotionValidationError(f"{field}.value must be an int or float")
    if not isinstance(raw_unit, str):
        raise MotionValidationError(f"{field}.unit must be a string")
    return Quantity(raw_value, raw_unit)


def _quantity_dict(quantity: Quantity) -> dict[str, float | str]:
    si_quantity = quantity.to_si()
    return {"value": float(si_quantity.value), "unit": si_quantity.unit}


def _evidence_from_dict(value: object, field: str) -> EvidenceRef:
    payload = _strict_mapping(
        value,
        frozenset({"schema_version", "source_kind", "reference", "target_field", "content_digest"}),
        field,
    )
    fields: dict[str, str] = {}
    for key in ("schema_version", "source_kind", "reference", "target_field", "content_digest"):
        item = payload[key]
        if not isinstance(item, str):
            raise MotionValidationError(f"{field}.{key} must be a string")
        fields[key] = item
    return EvidenceRef(**fields)


def _require_evidence(value: object, target_field: str, field: str) -> EvidenceRef:
    if not isinstance(value, EvidenceRef):
        raise MotionValidationError(f"{field} must be an EvidenceRef")
    if value.target_field != target_field:
        raise MotionValidationError(
            f"{field} must target {target_field!r}, not {value.target_field!r}"
        )
    return value


def _number(value: object, field: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MotionValidationError(f"{field} must be an int or float")
    return value


def _direction_from_dict(value: object) -> UnitDirection:
    try:
        return UnitDirection.from_dict(value)
    except SpatialValidationError as error:
        raise MotionValidationError(str(error)) from error


def _point_from_dict(value: object) -> Point3:
    payload = _strict_mapping(
        value,
        frozenset({"schema_version", "frame", "x", "y", "z"}),
        "initial_reference_point",
    )
    if payload["schema_version"] != SCHEMA_VERSION:
        raise MotionValidationError("unsupported point schema version")
    frame = _require_text(payload["frame"], "initial_reference_point.frame")
    return Point3(
        FrameId(frame),
        _quantity_from_dict(payload["x"], "initial_reference_point.x"),
        _quantity_from_dict(payload["y"], "initial_reference_point.y"),
        _quantity_from_dict(payload["z"], "initial_reference_point.z"),
    )


@dataclass(frozen=True, slots=True)
class MotionApplicability:
    """Supplied quasi-static and rate-independent applicability evidence."""

    quasi_static_statement: str
    quasi_static_evidence: EvidenceRef
    rate_independent_statement: str
    rate_independent_evidence: EvidenceRef

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "quasi_static_statement",
            _require_text(self.quasi_static_statement, "quasi_static_statement"),
        )
        object.__setattr__(
            self,
            "rate_independent_statement",
            _require_text(self.rate_independent_statement, "rate_independent_statement"),
        )
        _require_evidence(
            self.quasi_static_evidence,
            "motion.quasi_static_applicability",
            "quasi_static_evidence",
        )
        _require_evidence(
            self.rate_independent_evidence,
            "motion.rate_independent_applicability",
            "rate_independent_evidence",
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "quasi_static_statement": self.quasi_static_statement,
            "quasi_static_evidence": self.quasi_static_evidence.to_dict(),
            "rate_independent_statement": self.rate_independent_statement,
            "rate_independent_evidence": self.rate_independent_evidence.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: object) -> MotionApplicability:
        payload = _strict_mapping(
            value,
            frozenset(
                {
                    "schema_version",
                    "quasi_static_statement",
                    "quasi_static_evidence",
                    "rate_independent_statement",
                    "rate_independent_evidence",
                }
            ),
            "applicability",
        )
        if payload["schema_version"] != SCHEMA_VERSION:
            raise MotionValidationError("unsupported applicability schema version")
        return cls(
            quasi_static_statement=_require_text(
                payload["quasi_static_statement"], "quasi_static_statement"
            ),
            quasi_static_evidence=_evidence_from_dict(
                payload["quasi_static_evidence"], "quasi_static_evidence"
            ),
            rate_independent_statement=_require_text(
                payload["rate_independent_statement"], "rate_independent_statement"
            ),
            rate_independent_evidence=_evidence_from_dict(
                payload["rate_independent_evidence"], "rate_independent_evidence"
            ),
        )

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


@dataclass(frozen=True, slots=True)
class MotionSample:
    """One explicitly supplied time and nonnegative travel magnitude sample."""

    time: Quantity
    displacement: Quantity

    def __post_init__(self) -> None:
        if not isinstance(self.time, Quantity) or self.time.dimension != _TIME:
            raise MotionValidationError("time must be a time Quantity")
        if self.time.to_si().value < 0:
            raise MotionValidationError("time must be nonnegative")
        if not isinstance(self.displacement, Quantity) or self.displacement.dimension != _LENGTH:
            raise MotionValidationError("displacement must be a length Quantity")
        if self.displacement.to_si().value < 0:
            raise MotionValidationError("displacement must be nonnegative")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "time": _quantity_dict(self.time),
            "displacement": _quantity_dict(self.displacement),
        }

    @classmethod
    def from_dict(cls, value: object) -> MotionSample:
        payload = _strict_mapping(
            value,
            frozenset({"schema_version", "time", "displacement"}),
            "motion_sample",
        )
        if payload["schema_version"] != SCHEMA_VERSION:
            raise MotionValidationError("unsupported motion sample schema version")
        return cls(
            time=_quantity_from_dict(payload["time"], "time"),
            displacement=_quantity_from_dict(payload["displacement"], "displacement"),
        )

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


_MOTION_KEYS = frozenset(
    {
        "schema_version",
        "direction",
        "initial_reference_point",
        "samples",
        "applicability",
        "direction_evidence",
        "initial_reference_point_evidence",
        "history_evidence",
    }
)


@dataclass(frozen=True, slots=True)
class MotionProfile:
    """Explicit monotonic initial-state pushing motion in one named frame."""

    direction: UnitDirection
    initial_reference_point: Point3
    samples: Sequence[MotionSample]
    applicability: MotionApplicability
    direction_evidence: EvidenceRef
    initial_reference_point_evidence: EvidenceRef
    history_evidence: EvidenceRef

    def __post_init__(self) -> None:
        if not isinstance(self.direction, UnitDirection):
            raise MotionValidationError("direction must be a UnitDirection")
        if not isinstance(self.initial_reference_point, Point3):
            raise MotionValidationError("initial_reference_point must be a Point3")
        if self.direction.frame != self.initial_reference_point.frame:
            raise MotionValidationError(
                "direction and initial_reference_point must use the same frame"
            )
        if not isinstance(self.applicability, MotionApplicability):
            raise MotionValidationError("applicability must be a MotionApplicability")
        _require_evidence(self.direction_evidence, "motion.direction", "direction_evidence")
        _require_evidence(
            self.initial_reference_point_evidence,
            "motion.initial_reference_point",
            "initial_reference_point_evidence",
        )
        _require_evidence(self.history_evidence, "motion.history", "history_evidence")
        if isinstance(self.samples, (str, bytes)) or not isinstance(self.samples, Sequence):
            raise MotionValidationError("samples must be a sequence")
        copied = tuple(self.samples)
        if any(not isinstance(sample, MotionSample) for sample in copied):
            raise MotionValidationError("samples contains an invalid MotionSample")
        if len(copied) < 2:
            raise MotionValidationError("samples must contain at least two entries")
        times = [float(sample.time.to_si().value) for sample in copied]
        displacements = [float(sample.displacement.to_si().value) for sample in copied]
        if any(later <= earlier for earlier, later in pairwise(times)):
            raise MotionValidationError("sample times must be strictly increasing")
        if displacements[0] != 0.0:
            raise MotionValidationError("the initial sample displacement must be zero")
        if displacements[-1] <= 0.0:
            raise MotionValidationError("the final sample displacement must be positive")
        if any(later < earlier for earlier, later in pairwise(displacements)):
            raise MotionValidationError("displacement must be nondecreasing")
        object.__setattr__(self, "samples", copied)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "direction": self.direction.to_dict(),
            "initial_reference_point": self.initial_reference_point.to_dict(),
            "samples": [sample.to_dict() for sample in self.samples],
            "applicability": self.applicability.to_dict(),
            "direction_evidence": self.direction_evidence.to_dict(),
            "initial_reference_point_evidence": self.initial_reference_point_evidence.to_dict(),
            "history_evidence": self.history_evidence.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: object) -> MotionProfile:
        payload = _strict_mapping(value, _MOTION_KEYS, "motion_profile")
        if payload["schema_version"] != SCHEMA_VERSION:
            raise MotionValidationError("unsupported motion profile schema version")
        raw_samples = payload["samples"]
        if not isinstance(raw_samples, list):
            raise MotionValidationError("samples must be a list")
        return cls(
            direction=_direction_from_dict(payload["direction"]),
            initial_reference_point=_point_from_dict(payload["initial_reference_point"]),
            samples=[MotionSample.from_dict(item) for item in raw_samples],
            applicability=MotionApplicability.from_dict(payload["applicability"]),
            direction_evidence=_evidence_from_dict(
                payload["direction_evidence"], "direction_evidence"
            ),
            initial_reference_point_evidence=_evidence_from_dict(
                payload["initial_reference_point_evidence"],
                "initial_reference_point_evidence",
            ),
            history_evidence=_evidence_from_dict(payload["history_evidence"], "history_evidence"),
        )

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


__all__ = [
    "SCHEMA_VERSION",
    "MotionApplicability",
    "MotionProfile",
    "MotionSample",
    "MotionValidationError",
]
