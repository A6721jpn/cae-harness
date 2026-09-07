"""Immutable, evidence-bound material candidate values."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar, cast

from .canonical import canonical_bytes
from .evidence import EvidenceRef
from .units import Dimension, Quantity

SCHEMA_VERSION = "1"
_PRESSURE = Dimension(length=-1, mass=1, time=-2)
_MATERIAL_TARGET_FIELDS = frozenset(
    {
        "material.model",
        "material.youngs_modulus",
        "material.poisson_ratio",
        "material.strain_applicability",
        "material.rate_applicability",
    }
)


class MaterialValidationError(ValueError):
    """Raised when a material candidate is structurally or semantically invalid."""


def _require_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise MaterialValidationError(
            f"{field} must be a non-empty string without surrounding whitespace"
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise MaterialValidationError(f"{field} contains a control character")
    return value


def _strict_mapping(
    value: object, expected_keys: frozenset[str], field: str
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise MaterialValidationError(f"{field} must be an object")
    keys = set(value.keys())
    if any(not isinstance(key, str) for key in keys) or keys != expected_keys:
        raise MaterialValidationError(f"{field} has unknown or missing fields")
    return cast(Mapping[str, object], value)


def _quantity_from_dict(value: object, field: str) -> Quantity:
    payload = _strict_mapping(value, frozenset({"value", "unit"}), field)
    raw_value = payload["value"]
    raw_unit = payload["unit"]
    if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
        raise MaterialValidationError(f"{field}.value must be an int or float")
    if not isinstance(raw_unit, str):
        raise MaterialValidationError(f"{field}.unit must be a string")
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
            raise MaterialValidationError(f"{field}.{key} must be a string")
        fields[key] = item
    return EvidenceRef(**fields)


def _require_evidence(value: object, target_field: str, field: str) -> EvidenceRef:
    if not isinstance(value, EvidenceRef):
        raise MaterialValidationError(f"{field} must be an EvidenceRef")
    if value.target_field != target_field:
        raise MaterialValidationError(
            f"{field} must target {target_field!r}, not {value.target_field!r}"
        )
    if target_field not in _MATERIAL_TARGET_FIELDS:
        raise MaterialValidationError(f"unsupported material evidence target: {target_field!r}")
    return value


@dataclass(frozen=True, slots=True)
class MaterialApplicability:
    """Supplied strain and rate applicability statements with field-bound evidence."""

    strain_statement: str
    strain_evidence: EvidenceRef
    rate_statement: str
    rate_evidence: EvidenceRef

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "strain_statement", _require_text(self.strain_statement, "strain_statement")
        )
        object.__setattr__(
            self, "rate_statement", _require_text(self.rate_statement, "rate_statement")
        )
        _require_evidence(
            self.strain_evidence,
            "material.strain_applicability",
            "strain_evidence",
        )
        _require_evidence(self.rate_evidence, "material.rate_applicability", "rate_evidence")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "strain_statement": self.strain_statement,
            "strain_evidence": self.strain_evidence.to_dict(),
            "rate_statement": self.rate_statement,
            "rate_evidence": self.rate_evidence.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: object) -> MaterialApplicability:
        payload = _strict_mapping(
            value,
            frozenset(
                {
                    "schema_version",
                    "strain_statement",
                    "strain_evidence",
                    "rate_statement",
                    "rate_evidence",
                }
            ),
            "applicability",
        )
        if payload["schema_version"] != SCHEMA_VERSION:
            raise MaterialValidationError("unsupported applicability schema version")
        return cls(
            strain_statement=_require_text(payload["strain_statement"], "strain_statement"),
            strain_evidence=_evidence_from_dict(payload["strain_evidence"], "strain_evidence"),
            rate_statement=_require_text(payload["rate_statement"], "rate_statement"),
            rate_evidence=_evidence_from_dict(payload["rate_evidence"], "rate_evidence"),
        )

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


_MATERIAL_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "youngs_modulus",
        "poisson_ratio",
        "model_evidence",
        "youngs_modulus_evidence",
        "poisson_ratio_evidence",
        "applicability",
    }
)


@dataclass(frozen=True, slots=True)
class _MaterialCandidate:
    youngs_modulus: Quantity
    poisson_ratio: Quantity
    model_evidence: EvidenceRef
    youngs_modulus_evidence: EvidenceRef
    poisson_ratio_evidence: EvidenceRef
    applicability: MaterialApplicability

    _KIND: ClassVar[str] = ""

    def __post_init__(self) -> None:
        if not isinstance(self.youngs_modulus, Quantity):
            raise MaterialValidationError("youngs_modulus must be a Quantity")
        if self.youngs_modulus.dimension != _PRESSURE:
            raise MaterialValidationError("youngs_modulus must have pressure dimension")
        if self.youngs_modulus.to_si().value <= 0:
            raise MaterialValidationError("youngs_modulus must be positive")
        if not isinstance(self.poisson_ratio, Quantity):
            raise MaterialValidationError("poisson_ratio must be a Quantity")
        if not self.poisson_ratio.dimension.is_dimensionless:
            raise MaterialValidationError("poisson_ratio must be dimensionless")
        poisson_ratio = float(self.poisson_ratio.to_si().value)
        if not -1.0 < poisson_ratio < 0.5:
            raise MaterialValidationError("poisson_ratio must satisfy -1 < nu < 0.5")
        _require_evidence(self.model_evidence, "material.model", "model_evidence")
        _require_evidence(
            self.youngs_modulus_evidence,
            "material.youngs_modulus",
            "youngs_modulus_evidence",
        )
        _require_evidence(
            self.poisson_ratio_evidence,
            "material.poisson_ratio",
            "poisson_ratio_evidence",
        )
        if not isinstance(self.applicability, MaterialApplicability):
            raise MaterialValidationError("applicability must be a MaterialApplicability")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": self._KIND,
            "youngs_modulus": _quantity_dict(self.youngs_modulus),
            "poisson_ratio": _quantity_dict(self.poisson_ratio),
            "model_evidence": self.model_evidence.to_dict(),
            "youngs_modulus_evidence": self.youngs_modulus_evidence.to_dict(),
            "poisson_ratio_evidence": self.poisson_ratio_evidence.to_dict(),
            "applicability": self.applicability.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: object) -> _MaterialCandidate:
        payload = _strict_mapping(value, _MATERIAL_KEYS, "material")
        if payload["schema_version"] != SCHEMA_VERSION:
            raise MaterialValidationError("unsupported material schema version")
        kind = _require_text(payload["kind"], "kind")
        if kind != cls._KIND:
            raise MaterialValidationError(f"material kind must be {cls._KIND!r}")
        return cls(
            youngs_modulus=_quantity_from_dict(payload["youngs_modulus"], "youngs_modulus"),
            poisson_ratio=_quantity_from_dict(payload["poisson_ratio"], "poisson_ratio"),
            model_evidence=_evidence_from_dict(payload["model_evidence"], "model_evidence"),
            youngs_modulus_evidence=_evidence_from_dict(
                payload["youngs_modulus_evidence"], "youngs_modulus_evidence"
            ),
            poisson_ratio_evidence=_evidence_from_dict(
                payload["poisson_ratio_evidence"], "poisson_ratio_evidence"
            ),
            applicability=MaterialApplicability.from_dict(payload["applicability"]),
        )

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


@dataclass(frozen=True, slots=True)
class IsotropicLinearElastic(_MaterialCandidate):
    """Isotropic linear-elastic candidate parameterized by E and nu."""

    _KIND: ClassVar[str] = "isotropic_linear_elastic"


@dataclass(frozen=True, slots=True)
class CompressibleNeoHookean(_MaterialCandidate):
    """Compressible neo-Hookean candidate parameterized by E and nu."""

    _KIND: ClassVar[str] = "compressible_neo_hookean"


type MaterialCandidate = IsotropicLinearElastic | CompressibleNeoHookean


__all__ = [
    "SCHEMA_VERSION",
    "CompressibleNeoHookean",
    "IsotropicLinearElastic",
    "MaterialApplicability",
    "MaterialCandidate",
    "MaterialValidationError",
]
