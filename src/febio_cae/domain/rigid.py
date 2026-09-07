"""Immutable, evidence-bound rigid-tool primitive intent."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import ClassVar

from .canonical import canonical_bytes
from .evidence import EvidenceRef
from .spatial import BodyId, FrameId, RigidTransform
from .units import Dimension, Quantity

SCHEMA_VERSION = "1"
_LENGTH = Dimension(length=1)
_KINDS: dict[str, tuple[frozenset[str], str]] = {
    "sphere": (frozenset({"radius"}), "sphere center at local origin"),
    "cylinder": (frozenset({"radius", "height"}), "cylinder centered on local z axis"),
    "box": (frozenset({"length", "width", "height"}), "box centered on local x/y/z axes"),
}


class RigidValidationError(ValueError):
    """Raised when a rigid primitive intent is structurally invalid."""


def _require_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise RigidValidationError(
            f"{field} must be a non-empty string without surrounding whitespace"
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise RigidValidationError(f"{field} contains a control character")
    return value


def _require_evidence(value: object, target_field: str, field: str) -> EvidenceRef:
    if not isinstance(value, EvidenceRef):
        raise RigidValidationError(f"{field} must be an EvidenceRef")
    if value.target_field != target_field:
        raise RigidValidationError(
            f"{field} must target {target_field!r}, not {value.target_field!r}"
        )
    return value


def _quantity_dict(quantity: Quantity) -> dict[str, float | str]:
    value = quantity.to_si()
    return {"value": float(value.value), "unit": value.unit}


def _require_dimensions(value: object, kind: str) -> Mapping[str, Quantity]:
    if kind not in _KINDS:
        raise RigidValidationError(f"unsupported rigid primitive kind: {kind!r}")
    if not isinstance(value, Mapping):
        raise RigidValidationError("dimensions must be a mapping")
    expected, _ = _KINDS[kind]
    keys = set(value.keys())
    if any(not isinstance(key, str) for key in keys) or keys != expected:
        raise RigidValidationError(f"{kind} dimensions must be exactly {sorted(expected)!r}")
    copied: dict[str, Quantity] = {}
    for name in sorted(expected):
        quantity = value[name]
        if not isinstance(quantity, Quantity) or quantity.dimension != _LENGTH:
            raise RigidValidationError(f"dimensions[{name!r}] must be a length Quantity")
        si_value = quantity.to_si().value
        if si_value <= 0:
            raise RigidValidationError(f"dimensions[{name!r}] must be positive")
        copied[name] = quantity
    return MappingProxyType(copied)


def _require_dimension_evidence(
    value: object, expected_dimensions: frozenset[str]
) -> Mapping[str, EvidenceRef]:
    if not isinstance(value, Mapping):
        raise RigidValidationError("dimension_evidence must be a mapping")
    keys = set(value.keys())
    if any(not isinstance(key, str) for key in keys) or keys != expected_dimensions:
        raise RigidValidationError("dimension_evidence must match dimensions exactly")
    copied: dict[str, EvidenceRef] = {}
    for name in sorted(expected_dimensions):
        copied[name] = _require_evidence(
            value[name], f"rigid_tool.{name}", f"dimension_evidence[{name!r}]"
        )
    return MappingProxyType(copied)


@dataclass(frozen=True, slots=True)
class RigidPrimitive:
    """One sphere, cylinder, or box with explicit placement and provenance."""

    kind: str
    body_id: BodyId
    local_frame: FrameId
    placement: RigidTransform
    dimensions: Mapping[str, Quantity]
    dimension_evidence: Mapping[str, EvidenceRef]
    model_evidence: EvidenceRef
    placement_evidence: EvidenceRef

    _KIND_DIMENSIONS: ClassVar[dict[str, frozenset[str]]] = {
        kind: dimensions for kind, (dimensions, _) in _KINDS.items()
    }

    def __post_init__(self) -> None:
        kind = _require_text(self.kind, "kind")
        if kind not in _KINDS:
            raise RigidValidationError(f"unsupported rigid primitive kind: {kind!r}")
        if not isinstance(self.body_id, BodyId):
            raise RigidValidationError("body_id must be a BodyId")
        if not isinstance(self.local_frame, FrameId):
            raise RigidValidationError("local_frame must be a FrameId")
        if not isinstance(self.placement, RigidTransform):
            raise RigidValidationError("placement must be a RigidTransform")
        if self.placement.source_frame != self.local_frame:
            raise RigidValidationError("placement source_frame must match local_frame")
        dimensions = _require_dimensions(self.dimensions, kind)
        dimension_evidence = _require_dimension_evidence(self.dimension_evidence, _KINDS[kind][0])
        _require_evidence(self.model_evidence, "rigid_tool.model", "model_evidence")
        _require_evidence(self.placement_evidence, "rigid_tool.placement", "placement_evidence")
        for field in ("x", "y", "z"):
            getattr(self.placement.translation, field).to_si()
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "dimensions", dimensions)
        object.__setattr__(self, "dimension_evidence", dimension_evidence)

    @property
    def convention(self) -> str:
        return _KINDS[self.kind][1]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": self.kind,
            "body_id": self.body_id.value,
            "local_frame": self.local_frame.value,
            "placement": self.placement.to_dict(),
            "dimensions": {
                name: _quantity_dict(self.dimensions[name]) for name in sorted(self.dimensions)
            },
            "dimension_evidence": {
                name: self.dimension_evidence[name].to_dict()
                for name in sorted(self.dimension_evidence)
            },
            "model_evidence": self.model_evidence.to_dict(),
            "placement_evidence": self.placement_evidence.to_dict(),
            "convention": self.convention,
        }

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


__all__ = [
    "SCHEMA_VERSION",
    "RigidPrimitive",
    "RigidValidationError",
]
