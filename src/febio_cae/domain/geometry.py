"""Immutable intent for one inspected STEP geometry revision."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .canonical import canonical_bytes
from .evidence import EvidenceRef
from .spatial import BodyId, RigidTransform
from .units import Dimension, unit_definition

SCHEMA_VERSION = "1"
_LENGTH = Dimension(length=1)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class GeometryValidationError(ValueError):
    """Raised when a geometry intent is structurally or semantically invalid."""


def _require_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise GeometryValidationError(
            f"{field} must be a non-empty string without surrounding whitespace"
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise GeometryValidationError(f"{field} contains a control character")
    return value


def _require_digest(value: object, field: str) -> str:
    value = _require_text(value, field)
    if _SHA256.fullmatch(value) is None:
        raise GeometryValidationError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _require_step_unit(value: object) -> str:
    value = _require_text(value, "step_unit")
    try:
        definition = unit_definition(value)
    except ValueError as error:
        raise GeometryValidationError(f"unsupported STEP length unit: {value!r}") from error
    if definition.dimension != _LENGTH:
        raise GeometryValidationError("step_unit must have length dimension")
    return value


def _require_evidence(value: object, field: str, target_field: str) -> EvidenceRef:
    if not isinstance(value, EvidenceRef):
        raise GeometryValidationError(f"{field} must be an EvidenceRef")
    if value.target_field != target_field:
        raise GeometryValidationError(f"{field} must target {target_field}")
    return value


@dataclass(frozen=True, slots=True)
class GeometryIntent:
    """Explicit STEP source, inspected revision, body, unit, and placement intent.

    The three digest fields identify distinct supplied artifacts: original STEP
    bytes, the referenced geometry revision, and the inspection record. This
    value performs no parsing, registration, readiness check, or cross-object
    proof that those artifacts exist or are associated.
    """

    source_step_digest: str
    geometry_digest: str
    inspection_digest: str
    body_id: BodyId
    step_unit: str
    placement: RigidTransform
    body_evidence: EvidenceRef
    unit_evidence: EvidenceRef
    placement_evidence: EvidenceRef

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_step_digest",
            _require_digest(self.source_step_digest, "source_step_digest"),
        )
        object.__setattr__(
            self,
            "geometry_digest",
            _require_digest(self.geometry_digest, "geometry_digest"),
        )
        object.__setattr__(
            self,
            "inspection_digest",
            _require_digest(self.inspection_digest, "inspection_digest"),
        )
        if not isinstance(self.body_id, BodyId):
            raise GeometryValidationError("body_id must be a BodyId")
        object.__setattr__(self, "step_unit", _require_step_unit(self.step_unit))
        if not isinstance(self.placement, RigidTransform):
            raise GeometryValidationError("placement must be a RigidTransform")
        object.__setattr__(
            self,
            "body_evidence",
            _require_evidence(self.body_evidence, "body_evidence", "geometry.body_id"),
        )
        object.__setattr__(
            self,
            "unit_evidence",
            _require_evidence(self.unit_evidence, "unit_evidence", "geometry.step_unit"),
        )
        object.__setattr__(
            self,
            "placement_evidence",
            _require_evidence(
                self.placement_evidence,
                "placement_evidence",
                "geometry.placement",
            ),
        )

        # Validate the complete nested projection at construction. This keeps
        # unrepresentable translation values out of an otherwise frozen intent.
        try:
            self.to_bytes()
        except (TypeError, ValueError) as error:
            raise GeometryValidationError(
                f"geometry projection is not canonically serializable: {error}"
            ) from error

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "source_step_digest": self.source_step_digest,
            "geometry_digest": self.geometry_digest,
            "inspection_digest": self.inspection_digest,
            "body_id": self.body_id.value,
            "step_unit": self.step_unit,
            "placement": self.placement.to_dict(),
            "body_evidence": self.body_evidence.to_dict(),
            "unit_evidence": self.unit_evidence.to_dict(),
            "placement_evidence": self.placement_evidence.to_dict(),
        }

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


__all__ = ["SCHEMA_VERSION", "GeometryIntent", "GeometryValidationError"]
