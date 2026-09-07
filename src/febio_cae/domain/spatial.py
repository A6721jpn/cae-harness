"""Immutable, explicit spatial values for frame-aware domain contracts."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from .canonical import canonical_bytes
from .units import Dimension, Quantity

SCHEMA_VERSION = "1"
ORTHOGONALITY_TOLERANCE = 1.0e-10


class SpatialValidationError(ValueError):
    """Raised when a supplied spatial value is structurally invalid."""


def _require_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise SpatialValidationError(
            f"{field} must be a non-empty string without surrounding whitespace"
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise SpatialValidationError(f"{field} contains a control character")
    return value


def _finite_float(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SpatialValidationError(f"{field} must be a finite number")
    try:
        result = float(value)
    except OverflowError as error:
        raise SpatialValidationError(f"{field} is outside finite range") from error
    if not math.isfinite(result):
        raise SpatialValidationError(f"{field} must be finite")
    return result


def _strict_mapping(
    value: object, expected_keys: frozenset[str], field: str
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise SpatialValidationError(f"{field} must be an object")
    keys = set(value.keys())
    if any(not isinstance(key, str) for key in keys) or keys != expected_keys:
        raise SpatialValidationError(f"{field} has unknown or missing fields")
    return cast(Mapping[str, object], value)


def _quantity_dict(quantity: Quantity) -> dict[str, float | str]:
    si_quantity = quantity.to_si()
    return {"value": float(si_quantity.value), "unit": si_quantity.unit}


def _require_length_quantity(value: object, field: str) -> Quantity:
    if not isinstance(value, Quantity) or value.dimension != Dimension(length=1):
        raise SpatialValidationError(f"{field} must be a length Quantity")
    return value


@dataclass(frozen=True, slots=True)
class OpaqueId:
    """Case-sensitive opaque identifier with no inferred semantics."""

    value: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _require_text(self.value, "value"))

    def to_dict(self) -> dict[str, str]:
        return {"schema_version": SCHEMA_VERSION, "value": self.value}

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


@dataclass(frozen=True, slots=True)
class FrameId(OpaqueId):
    """Identifier for an explicit coordinate frame."""


@dataclass(frozen=True, slots=True)
class GeometryId(OpaqueId):
    """Identifier for a geometry revision supplied by an authority."""


@dataclass(frozen=True, slots=True)
class BodyId(OpaqueId):
    """Identifier for a body within a supplied geometry revision."""


@dataclass(frozen=True, slots=True)
class FaceId(OpaqueId):
    """Identifier for a face within a supplied body."""


@dataclass(frozen=True, slots=True)
class Point3:
    """A three-component length point expressed in an explicit frame."""

    frame: FrameId
    x: Quantity
    y: Quantity
    z: Quantity

    def __post_init__(self) -> None:
        if not isinstance(self.frame, FrameId):
            raise SpatialValidationError("frame must be a FrameId")
        for field, value in (("x", self.x), ("y", self.y), ("z", self.z)):
            _require_length_quantity(value, field)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "frame": self.frame.value,
            "x": _quantity_dict(self.x),
            "y": _quantity_dict(self.y),
            "z": _quantity_dict(self.z),
        }

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


@dataclass(frozen=True, slots=True)
class Translation3:
    """A three-component length translation expressed in an explicit frame."""

    frame: FrameId
    x: Quantity
    y: Quantity
    z: Quantity

    def __post_init__(self) -> None:
        if not isinstance(self.frame, FrameId):
            raise SpatialValidationError("frame must be a FrameId")
        for field, value in (("x", self.x), ("y", self.y), ("z", self.z)):
            _require_length_quantity(value, field)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "frame": self.frame.value,
            "x": _quantity_dict(self.x),
            "y": _quantity_dict(self.y),
            "z": _quantity_dict(self.z),
        }

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


@dataclass(frozen=True, slots=True)
class UnitDirection:
    """A finite, normalized direction in an explicit frame."""

    frame: FrameId
    x: float
    y: float
    z: float

    def __post_init__(self) -> None:
        if not isinstance(self.frame, FrameId):
            raise SpatialValidationError("frame must be a FrameId")
        components = tuple(
            _finite_float(value, field)
            for field, value in (("x", self.x), ("y", self.y), ("z", self.z))
        )
        scale = max(abs(component) for component in components)
        if not math.isfinite(scale) or scale == 0.0:
            raise SpatialValidationError("direction must be finite and nonzero")
        scaled = tuple(component / scale for component in components)
        norm = math.hypot(math.hypot(scaled[0], scaled[1]), scaled[2])
        if not math.isfinite(norm) or norm == 0.0:
            raise SpatialValidationError("direction must be finite and nonzero")
        object.__setattr__(self, "x", scaled[0] / norm)
        object.__setattr__(self, "y", scaled[1] / norm)
        object.__setattr__(self, "z", scaled[2] / norm)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "frame": self.frame.value,
            "x": self.x,
            "y": self.y,
            "z": self.z,
        }

    @classmethod
    def from_dict(cls, value: object) -> UnitDirection:
        payload = _strict_mapping(
            value,
            frozenset({"schema_version", "frame", "x", "y", "z"}),
            "direction",
        )
        if payload["schema_version"] != SCHEMA_VERSION:
            raise SpatialValidationError("unsupported direction schema version")
        raw_frame = payload["frame"]
        if not isinstance(raw_frame, str):
            raise SpatialValidationError("direction.frame must be a string")
        components = tuple(
            _finite_float(payload[field], f"direction.{field}") for field in ("x", "y", "z")
        )
        norm = math.hypot(math.hypot(components[0], components[1]), components[2])
        if not math.isfinite(norm) or norm == 0.0:
            raise SpatialValidationError("direction must be finite and nonzero")
        if not math.isclose(norm, 1.0, rel_tol=0.0, abs_tol=1.0e-12):
            raise SpatialValidationError("direction components must be normalized")

        restored = object.__new__(cls)
        object.__setattr__(restored, "frame", FrameId(raw_frame))
        object.__setattr__(restored, "x", components[0])
        object.__setattr__(restored, "y", components[1])
        object.__setattr__(restored, "z", components[2])
        return restored

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


Direction3 = UnitDirection


def _normalise_matrix(matrix: Sequence[Sequence[object]]) -> tuple[tuple[float, float, float], ...]:
    if isinstance(matrix, (str, bytes)) or not isinstance(matrix, Sequence) or len(matrix) != 3:
        raise SpatialValidationError("rotation must be a 3x3 numeric matrix")
    rows: list[tuple[float, float, float]] = []
    for row_index, matrix_row in enumerate(matrix):
        if (
            isinstance(matrix_row, (str, bytes))
            or not isinstance(matrix_row, Sequence)
            or len(matrix_row) != 3
        ):
            raise SpatialValidationError("rotation must be a 3x3 numeric matrix")
        rows.append(
            tuple(_finite_float(value, f"rotation[{row_index}]") for value in matrix_row)  # type: ignore[arg-type]
        )
    result = tuple(rows)
    for row_index in range(3):
        for column in range(3):
            dot = sum(result[index][row_index] * result[index][column] for index in range(3))
            expected = 1.0 if row_index == column else 0.0
            if abs(dot - expected) > ORTHOGONALITY_TOLERANCE:
                raise SpatialValidationError("rotation must be orthogonal within tolerance")
    determinant = (
        result[0][0] * (result[1][1] * result[2][2] - result[1][2] * result[2][1])
        - result[0][1] * (result[1][0] * result[2][2] - result[1][2] * result[2][0])
        + result[0][2] * (result[1][0] * result[2][1] - result[1][1] * result[2][0])
    )
    if abs(determinant - 1.0) > ORTHOGONALITY_TOLERANCE:
        raise SpatialValidationError("rotation must have determinant +1 within tolerance")
    return result


@dataclass(frozen=True, slots=True)
class ProperRotation:
    """An explicit 3x3 proper rotation matrix."""

    matrix: Sequence[Sequence[object]]

    def __post_init__(self) -> None:
        object.__setattr__(self, "matrix", _normalise_matrix(self.matrix))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "matrix": [list(row) for row in self.matrix],
        }

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


@dataclass(frozen=True, slots=True)
class RigidTransform:
    """An explicit transform mapping coordinates between named frames."""

    source_frame: FrameId
    target_frame: FrameId
    translation: Translation3
    rotation: ProperRotation | Sequence[Sequence[object]]

    def __post_init__(self) -> None:
        if not isinstance(self.source_frame, FrameId) or not isinstance(self.target_frame, FrameId):
            raise SpatialValidationError("source_frame and target_frame must be FrameId values")
        if not isinstance(self.translation, Translation3):
            raise SpatialValidationError("translation must be a Translation3")
        if self.translation.frame != self.target_frame:
            raise SpatialValidationError("translation must be expressed in target_frame")
        if not isinstance(self.rotation, ProperRotation):
            object.__setattr__(self, "rotation", ProperRotation(self.rotation))

    def to_dict(self) -> dict[str, object]:
        rotation = self.rotation
        if not isinstance(rotation, ProperRotation):
            rotation = ProperRotation(rotation)
        return {
            "schema_version": SCHEMA_VERSION,
            "source_frame": self.source_frame.value,
            "target_frame": self.target_frame.value,
            "translation": self.translation.to_dict(),
            "rotation": rotation.to_dict(),
        }

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


__all__ = [
    "ORTHOGONALITY_TOLERANCE",
    "SCHEMA_VERSION",
    "BodyId",
    "Direction3",
    "FaceId",
    "FrameId",
    "GeometryId",
    "OpaqueId",
    "Point3",
    "ProperRotation",
    "RigidTransform",
    "SpatialValidationError",
    "Translation3",
    "UnitDirection",
]
