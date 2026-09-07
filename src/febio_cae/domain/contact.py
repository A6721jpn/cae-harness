"""Immutable, evidence-bound contact-pair physical intent."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import cast

from .canonical import canonical_bytes
from .evidence import EvidenceRef
from .selection import SelectionRef
from .spatial import FrameId, OpaqueId, UnitDirection
from .units import Dimension, Quantity

SCHEMA_VERSION = "1"
_LENGTH = Dimension(length=1)
_DIMENSIONLESS = Dimension()


class ContactValidationError(ValueError):
    """Raised when explicit contact intent is structurally or physically invalid."""


def _quantity_dict(quantity: Quantity) -> dict[str, float | str]:
    value = quantity.to_si()
    return {"value": float(value.value), "unit": value.unit}


def _require_evidence(value: object, target_field: str, field: str) -> EvidenceRef:
    if not isinstance(value, EvidenceRef):
        raise ContactValidationError(f"{field} must be an EvidenceRef")
    if value.target_field != target_field:
        raise ContactValidationError(
            f"{field} must target {target_field!r}, not {value.target_field!r}"
        )
    return value


def _canonical_selection_projection(selection: SelectionRef) -> dict[str, object]:
    projected = json.loads(selection.to_bytes().decode("utf-8"))
    if not isinstance(projected, dict):
        raise ContactValidationError("canonical selection projection must be an object")
    return cast(dict[str, object], projected)


@dataclass(frozen=True, slots=True)
class ContactId(OpaqueId):
    """Opaque identity for one explicit contact pair."""


@dataclass(frozen=True, slots=True)
class Frictionless:
    """Explicit frictionless intent with its own physical evidence."""

    model_evidence: EvidenceRef

    def __post_init__(self) -> None:
        _require_evidence(self.model_evidence, "contact.friction_model", "model_evidence")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "frictionless",
            "model_evidence": self.model_evidence.to_dict(),
        }

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


@dataclass(frozen=True, slots=True)
class CoulombFriction:
    """Explicit constant Coulomb friction with a nonnegative coefficient."""

    coefficient: Quantity
    model_evidence: EvidenceRef
    coefficient_evidence: EvidenceRef

    def __post_init__(self) -> None:
        if not isinstance(self.coefficient, Quantity):
            raise ContactValidationError("coefficient must be a Quantity")
        if self.coefficient.dimension != _DIMENSIONLESS:
            raise ContactValidationError("coefficient must be dimensionless")
        coefficient = self.coefficient.to_si().value
        if coefficient < 0:
            raise ContactValidationError("coefficient must be nonnegative")
        _require_evidence(self.model_evidence, "contact.friction_model", "model_evidence")
        _require_evidence(
            self.coefficient_evidence,
            "contact.friction_coefficient",
            "coefficient_evidence",
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "coulomb",
            "coefficient": _quantity_dict(self.coefficient),
            "model_evidence": self.model_evidence.to_dict(),
            "coefficient_evidence": self.coefficient_evidence.to_dict(),
        }

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


FrictionIntent = Frictionless | CoulombFriction


@dataclass(frozen=True, slots=True)
class AsPlaced:
    """Explicitly retain the supplied placement without repositioning."""

    arrangement_evidence: EvidenceRef

    def __post_init__(self) -> None:
        _require_evidence(
            self.arrangement_evidence,
            "contact.arrangement",
            "arrangement_evidence",
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "as_placed",
            "arrangement_evidence": self.arrangement_evidence.to_dict(),
        }

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


@dataclass(frozen=True, slots=True)
class SpecifiedGap:
    """Request a signed gap along a supplied part-to-tool direction."""

    gap: Quantity
    direction: UnitDirection
    arrangement_evidence: EvidenceRef
    gap_evidence: EvidenceRef
    direction_evidence: EvidenceRef

    def __post_init__(self) -> None:
        if not isinstance(self.gap, Quantity) or self.gap.dimension != _LENGTH:
            raise ContactValidationError("gap must be a length Quantity")
        self.gap.to_si()
        if not isinstance(self.direction, UnitDirection):
            raise ContactValidationError("direction must be a UnitDirection")
        _require_evidence(
            self.arrangement_evidence,
            "contact.arrangement",
            "arrangement_evidence",
        )
        _require_evidence(self.gap_evidence, "contact.gap", "gap_evidence")
        _require_evidence(
            self.direction_evidence,
            "contact.direction",
            "direction_evidence",
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "specified_gap",
            "gap": _quantity_dict(self.gap),
            "direction": self.direction.to_dict(),
            "arrangement_evidence": self.arrangement_evidence.to_dict(),
            "gap_evidence": self.gap_evidence.to_dict(),
            "direction_evidence": self.direction_evidence.to_dict(),
        }

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


ArrangementIntent = AsPlaced | SpecifiedGap


@dataclass(frozen=True, slots=True)
class ContactIntent:
    """One explicit part/tool contact pair without geometry or solver resolution.

    A specified-gap direction is expressed from the part toward the tool:
    positive gap requests separation, zero requests touching, and negative gap
    records initial overlap.  This intent does not measure or move geometry,
    choose primary/secondary surfaces, or couple contact to motion.
    """

    contact_id: ContactId
    part_surface: SelectionRef
    tool_surface: SelectionRef
    pair_frame: FrameId
    part_surface_evidence: EvidenceRef
    tool_surface_evidence: EvidenceRef
    pair_frame_evidence: EvidenceRef
    friction: FrictionIntent
    arrangement: ArrangementIntent

    def __post_init__(self) -> None:
        if not isinstance(self.contact_id, ContactId):
            raise ContactValidationError("contact_id must be a ContactId")
        if not isinstance(self.part_surface, SelectionRef):
            raise ContactValidationError("part_surface must be a SelectionRef")
        if not isinstance(self.tool_surface, SelectionRef):
            raise ContactValidationError("tool_surface must be a SelectionRef")
        if self.part_surface.stated_role != "part_contact_surface":
            raise ContactValidationError("part_surface role must be 'part_contact_surface'")
        if self.tool_surface.stated_role != "tool_contact_surface":
            raise ContactValidationError("tool_surface role must be 'tool_contact_surface'")
        if self.part_surface.body_id == self.tool_surface.body_id:
            raise ContactValidationError("part_surface and tool_surface must use distinct bodies")
        if not isinstance(self.pair_frame, FrameId):
            raise ContactValidationError("pair_frame must be a FrameId")
        if self.part_surface.frame != self.pair_frame:
            raise ContactValidationError("part_surface frame must match pair_frame")
        if self.tool_surface.frame != self.pair_frame:
            raise ContactValidationError("tool_surface frame must match pair_frame")
        _require_evidence(
            self.part_surface_evidence,
            "contact.part_surface",
            "part_surface_evidence",
        )
        _require_evidence(
            self.tool_surface_evidence,
            "contact.tool_surface",
            "tool_surface_evidence",
        )
        _require_evidence(self.pair_frame_evidence, "contact.frame", "pair_frame_evidence")
        if not isinstance(self.friction, (Frictionless, CoulombFriction)):
            raise ContactValidationError(
                "friction must be a Frictionless or CoulombFriction intent"
            )
        if not isinstance(self.arrangement, (AsPlaced, SpecifiedGap)):
            raise ContactValidationError("arrangement must be an AsPlaced or SpecifiedGap intent")
        if (
            isinstance(self.arrangement, SpecifiedGap)
            and self.arrangement.direction.frame != self.pair_frame
        ):
            raise ContactValidationError("specified-gap direction frame must match pair_frame")

        # Force all nested values through their established canonical/range
        # boundaries at construction instead of deferring failures to bytes.
        self.part_surface.to_bytes()
        self.tool_surface.to_bytes()
        self.friction.to_bytes()
        self.arrangement.to_bytes()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "contact_id": self.contact_id.value,
            "part_surface": _canonical_selection_projection(self.part_surface),
            "tool_surface": _canonical_selection_projection(self.tool_surface),
            "pair_frame": self.pair_frame.value,
            "part_surface_evidence": self.part_surface_evidence.to_dict(),
            "tool_surface_evidence": self.tool_surface_evidence.to_dict(),
            "pair_frame_evidence": self.pair_frame_evidence.to_dict(),
            "friction": self.friction.to_dict(),
            "arrangement": self.arrangement.to_dict(),
        }

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


__all__ = [
    "SCHEMA_VERSION",
    "ArrangementIntent",
    "AsPlaced",
    "ContactId",
    "ContactIntent",
    "ContactValidationError",
    "CoulombFriction",
    "FrictionIntent",
    "Frictionless",
    "SpecifiedGap",
]
