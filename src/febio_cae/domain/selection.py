"""Immutable, provenance-aware selection intent and resolution observations."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, cast

from .canonical import canonical_bytes
from .evidence import EvidenceRef
from .spatial import BodyId, FaceId, FrameId, Point3, UnitDirection
from .units import Dimension, Quantity

SCHEMA_VERSION = "1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ROLE_EVIDENCE_TARGET_FIELDS = frozenset({"selection.role"})


class SelectionValidationError(ValueError):
    """Raised when selection intent or an observation is structurally invalid."""


def _require_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise SelectionValidationError(
            f"{field} must be a non-empty string without surrounding whitespace"
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise SelectionValidationError(f"{field} contains a control character")
    return value


def _require_digest(value: object, field: str) -> str:
    value = _require_text(value, field)
    if _SHA256.fullmatch(value) is None:
        raise SelectionValidationError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _quantity_dict(quantity: Quantity) -> dict[str, float | str]:
    si_quantity = quantity.to_si()
    return {"value": float(si_quantity.value), "unit": si_quantity.unit}


def _require_area(value: object, field: str) -> Quantity:
    if not isinstance(value, Quantity) or value.dimension != Dimension(length=2):
        raise SelectionValidationError(f"{field} must be an area Quantity")
    return value


def _copy_sequence[T](value: object, item_type: type[T], field: str) -> tuple[T, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise SelectionValidationError(f"{field} must be a sequence")
    result = tuple(value)
    if any(not isinstance(item, item_type) for item in result):
        raise SelectionValidationError(f"{field} contains an invalid item")
    return cast(tuple[T, ...], result)


def _unique_ids(values: Sequence[FaceId], field: str) -> tuple[FaceId, ...]:
    copied = tuple(values)
    if not copied:
        raise SelectionValidationError(f"{field} must not be empty")
    identifiers = [value.value for value in copied]
    if len(set(identifiers)) != len(identifiers):
        raise SelectionValidationError(f"{field} must not contain duplicate IDs")
    return copied


@dataclass(frozen=True, slots=True)
class NamedAttributeRule:
    attribute: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "attribute", _require_text(self.attribute, "attribute"))

    def to_dict(self) -> dict[str, str]:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "named_attribute",
            "attribute": self.attribute,
        }


@dataclass(frozen=True, slots=True)
class CoordinatePredicate:
    axis: UnitDirection
    operator: Literal["eq", "gte", "lte", "between"]
    value: Quantity
    upper: Quantity | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.axis, UnitDirection):
            raise SelectionValidationError("axis must be a UnitDirection")
        if self.operator not in {"eq", "gte", "lte", "between"}:
            raise SelectionValidationError("unsupported coordinate predicate operator")
        if not isinstance(self.value, Quantity) or self.value.dimension != Dimension(length=1):
            raise SelectionValidationError("value must be a length Quantity")
        if self.operator == "between":
            if not isinstance(self.upper, Quantity) or self.upper.dimension != Dimension(length=1):
                raise SelectionValidationError(
                    "between predicates require an upper length Quantity"
                )
            if self.value.to_si().value > self.upper.to_si().value:
                raise SelectionValidationError("between lower bound must not exceed upper bound")
        elif self.upper is not None:
            raise SelectionValidationError("upper is only valid for between predicates")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "axis": self.axis.to_dict(),
            "operator": self.operator,
            "value": _quantity_dict(self.value),
            "upper": None if self.upper is None else _quantity_dict(self.upper),
        }


@dataclass(frozen=True, slots=True)
class CoordinatePredicateRule:
    body_id: BodyId
    frame: FrameId
    predicates: Sequence[CoordinatePredicate]

    def __post_init__(self) -> None:
        if not isinstance(self.body_id, BodyId) or not isinstance(self.frame, FrameId):
            raise SelectionValidationError("body_id and frame must be typed spatial IDs")
        copied = _copy_sequence(self.predicates, CoordinatePredicate, "predicates")
        if not copied:
            raise SelectionValidationError("predicates must not be empty")
        if any(predicate.axis.frame != self.frame for predicate in copied):
            raise SelectionValidationError("predicate axes must use the rule frame")
        object.__setattr__(self, "predicates", copied)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "coordinate_predicate",
            "body_id": self.body_id.value,
            "frame": self.frame.value,
            "predicates": [predicate.to_dict() for predicate in self.predicates],
        }


@dataclass(frozen=True, slots=True)
class FaceSetRule:
    geometry_digest: str
    body_id: BodyId
    frame: FrameId
    face_ids: Sequence[FaceId]
    provenance: EvidenceRef

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "geometry_digest", _require_digest(self.geometry_digest, "geometry_digest")
        )
        if not isinstance(self.body_id, BodyId) or not isinstance(self.frame, FrameId):
            raise SelectionValidationError("body_id and frame must be typed spatial IDs")
        if not isinstance(self.provenance, EvidenceRef):
            raise SelectionValidationError("provenance must be an EvidenceRef")
        if isinstance(self.face_ids, (str, bytes)) or not isinstance(self.face_ids, Sequence):
            raise SelectionValidationError("face_ids must be a sequence")
        copied = tuple(self.face_ids)
        if any(not isinstance(face_id, FaceId) for face_id in copied):
            raise SelectionValidationError("face_ids contains an invalid ID")
        object.__setattr__(self, "face_ids", _unique_ids(copied, "face_ids"))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "face_set",
            "geometry_digest": self.geometry_digest,
            "body_id": self.body_id.value,
            "frame": self.frame.value,
            "face_ids": [face_id.to_dict() for face_id in self.face_ids],
            "provenance": self.provenance.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class WholeBodyRule:
    body_id: BodyId

    def __post_init__(self) -> None:
        if not isinstance(self.body_id, BodyId):
            raise SelectionValidationError("body_id must be a BodyId")

    def to_dict(self) -> dict[str, str]:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "whole_body",
            "body_id": self.body_id.value,
        }


ExplicitFaceRule = FaceSetRule
FaceSelectionRule = FaceSetRule
type SelectionRule = NamedAttributeRule | CoordinatePredicateRule | FaceSetRule | WholeBodyRule


@dataclass(frozen=True, slots=True)
class FaceMeasurement:
    face_id: FaceId
    area: Quantity
    centroid: Point3

    def __post_init__(self) -> None:
        if not isinstance(self.face_id, FaceId):
            raise SelectionValidationError("face_id must be a FaceId")
        _require_area(self.area, "area")
        if not isinstance(self.centroid, Point3):
            raise SelectionValidationError("centroid must be a Point3")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "face_id": self.face_id.to_dict(),
            "area": _quantity_dict(self.area),
            "centroid": self.centroid.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ResolutionSnapshot:
    geometry_digest: str
    body_id: BodyId
    frame: FrameId
    faces: Sequence[FaceMeasurement]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "geometry_digest", _require_digest(self.geometry_digest, "geometry_digest")
        )
        if not isinstance(self.body_id, BodyId) or not isinstance(self.frame, FrameId):
            raise SelectionValidationError("body_id and frame must be typed spatial IDs")
        if isinstance(self.faces, (str, bytes)) or not isinstance(self.faces, Sequence):
            raise SelectionValidationError("faces must be a sequence")
        copied = tuple(self.faces)
        if any(not isinstance(face, FaceMeasurement) for face in copied):
            raise SelectionValidationError("faces contains an invalid measurement")
        if not copied:
            raise SelectionValidationError("faces must not be empty")
        identifiers = [face.face_id.value for face in copied]
        if len(set(identifiers)) != len(identifiers):
            raise SelectionValidationError("faces must not contain duplicate IDs")
        if any(face.centroid.frame != self.frame for face in copied):
            raise SelectionValidationError("face centroid frames must match the snapshot frame")
        object.__setattr__(self, "faces", copied)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "geometry_digest": self.geometry_digest,
            "body_id": self.body_id.value,
            "frame": self.frame.value,
            "faces": [face.to_dict() for face in self.faces],
        }


@dataclass(frozen=True, slots=True)
class SelectionRef:
    name: str
    role: str
    role_evidence: EvidenceRef
    geometry_digest: str
    body_id: BodyId
    frame: FrameId
    rule: SelectionRule
    resolution: ResolutionSnapshot | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _require_text(self.name, "name"))
        object.__setattr__(self, "role", _require_text(self.role, "role"))
        object.__setattr__(
            self, "geometry_digest", _require_digest(self.geometry_digest, "geometry_digest")
        )
        if not isinstance(self.role_evidence, EvidenceRef):
            raise SelectionValidationError("role_evidence must be an EvidenceRef")
        if self.role_evidence.target_field not in _ROLE_EVIDENCE_TARGET_FIELDS:
            raise SelectionValidationError(
                "role_evidence must target the explicit selection.role field"
            )
        if not isinstance(self.body_id, BodyId) or not isinstance(self.frame, FrameId):
            raise SelectionValidationError("body_id and frame must be typed spatial IDs")
        if not isinstance(
            self.rule, (NamedAttributeRule, CoordinatePredicateRule, FaceSetRule, WholeBodyRule)
        ):
            raise SelectionValidationError("rule must be a supported SelectionRule")
        if isinstance(self.rule, CoordinatePredicateRule):
            if self.rule.body_id != self.body_id or self.rule.frame != self.frame:
                raise SelectionValidationError("coordinate rule context must match SelectionRef")
        elif isinstance(self.rule, FaceSetRule):
            if (
                self.rule.geometry_digest != self.geometry_digest
                or self.rule.body_id != self.body_id
                or self.rule.frame != self.frame
            ):
                raise SelectionValidationError("face rule context must match SelectionRef")
        elif isinstance(self.rule, WholeBodyRule) and self.rule.body_id != self.body_id:
            raise SelectionValidationError("whole-body rule context must match SelectionRef")
        if self.resolution is not None:
            if not isinstance(self.resolution, ResolutionSnapshot):
                raise SelectionValidationError("resolution must be a ResolutionSnapshot or None")
            if (
                self.resolution.geometry_digest != self.geometry_digest
                or self.resolution.body_id != self.body_id
                or self.resolution.frame != self.frame
            ):
                raise SelectionValidationError("resolution context must match SelectionRef")
            if isinstance(self.rule, FaceSetRule):
                rule_face_ids = {face_id.value for face_id in self.rule.face_ids}
                resolution_face_ids = {face.face_id.value for face in self.resolution.faces}
                if rule_face_ids != resolution_face_ids:
                    raise SelectionValidationError(
                        "explicit face rule and resolution must contain the same face IDs"
                    )

    @property
    def stated_role(self) -> str:
        return self.role

    @property
    def source_geometry_digest(self) -> str:
        return self.geometry_digest

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "name": self.name,
            "stated_role": self.role,
            "role_evidence": self.role_evidence.to_dict(),
            "geometry_digest": self.geometry_digest,
            "body_id": self.body_id.value,
            "frame": self.frame.value,
            "rule": self.rule.to_dict(),
            "resolution": None if self.resolution is None else self.resolution.to_dict(),
        }

    def to_bytes(self) -> bytes:
        unordered_paths: list[tuple[str, ...]] = []
        if isinstance(self.rule, FaceSetRule):
            unordered_paths.append(("rule", "face_ids"))
        if self.resolution is not None:
            unordered_paths.append(("resolution", "faces"))
        return canonical_bytes(self.to_dict(), unordered_paths=unordered_paths)


__all__ = [
    "SCHEMA_VERSION",
    "CoordinatePredicate",
    "CoordinatePredicateRule",
    "ExplicitFaceRule",
    "FaceMeasurement",
    "FaceSelectionRule",
    "FaceSetRule",
    "NamedAttributeRule",
    "ResolutionSnapshot",
    "SelectionRef",
    "SelectionRule",
    "SelectionValidationError",
    "WholeBodyRule",
]
