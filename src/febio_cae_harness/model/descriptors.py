"""Immutable descriptors for mesh, element, contact, ROI, and evaluation intent."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite

from ._immutability import FrozenJSON, freeze_value, frozen_mapping, frozen_strings, thaw_json
from .types import EvidenceProvenance, UnresolvedEvidenceField, normalise_provenance


def _validate_number(name: str, value: float | None) -> None:
    if value is not None and (isinstance(value, bool) or not isfinite(value)):
        raise ValueError(f"{name} must be a finite number or None")


def _validate_optional_text(name: str, value: str | None) -> None:
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise ValueError(f"{name} must be a non-empty string or None")


def _validate_evidence(
    value: EvidenceProvenance | tuple[EvidenceProvenance, ...] | list[EvidenceProvenance],
) -> tuple[EvidenceProvenance, ...]:
    return normalise_provenance(value)


def _validate_unresolved(
    value: tuple[UnresolvedEvidenceField, ...],
) -> tuple[UnresolvedEvidenceField, ...]:
    result = tuple(value)
    if not all(isinstance(item, UnresolvedEvidenceField) for item in result):
        raise TypeError("unresolved must contain UnresolvedEvidenceField records")
    return result


@dataclass(frozen=True, slots=True)
class MeshIntent:
    """Explicit mesh requirements; absent values remain unresolved."""

    target_size: float | None = None
    element_type: str | None = None
    max_elements: int | None = None
    quality_limits: Mapping[str, FrozenJSON] | None = None
    regions: tuple[str, ...] = ()
    evidence: tuple[EvidenceProvenance, ...] = ()
    unresolved: tuple[UnresolvedEvidenceField, ...] = ()

    def __post_init__(self) -> None:
        _validate_number("target_size", self.target_size)
        if self.target_size is not None and self.target_size <= 0:
            raise ValueError("target_size must be positive or None")
        _validate_optional_text("element_type", self.element_type)
        if self.max_elements is not None and (
            isinstance(self.max_elements, bool) or self.max_elements < 1
        ):
            raise ValueError("max_elements must be a positive integer or None")
        object.__setattr__(self, "quality_limits", frozen_mapping(self.quality_limits))
        object.__setattr__(self, "regions", frozen_strings(self.regions))
        object.__setattr__(self, "evidence", _validate_evidence(self.evidence))
        object.__setattr__(self, "unresolved", _validate_unresolved(self.unresolved))

    @property
    def unresolved_fields(self) -> tuple[UnresolvedEvidenceField, ...]:
        return self.unresolved

    def to_dict(self) -> dict[str, object]:
        return {
            "target_size": self.target_size,
            "element_type": self.element_type,
            "max_elements": self.max_elements,
            "quality_limits": thaw_json(self.quality_limits),
            "regions": list(self.regions),
            "evidence": [item.to_dict() for item in self.evidence],
            "unresolved": [item.to_dict() for item in self.unresolved],
        }


@dataclass(frozen=True, slots=True)
class ElementIntent:
    """Explicit element formulation and order requirements."""

    element_type: str | None = None
    order: int | None = None
    integration: str | None = None
    material_id: str | int | None = None
    region: str | None = None
    count: int | None = None
    evidence: tuple[EvidenceProvenance, ...] = ()
    unresolved: tuple[UnresolvedEvidenceField, ...] = ()

    def __post_init__(self) -> None:
        _validate_optional_text("element_type", self.element_type)
        _validate_optional_text("integration", self.integration)
        _validate_optional_text("region", self.region)
        if self.order is not None and (
            isinstance(self.order, bool) or not isinstance(self.order, int) or self.order < 1
        ):
            raise ValueError("order must be a positive integer or None")
        if self.count is not None and (
            isinstance(self.count, bool) or not isinstance(self.count, int) or self.count < 0
        ):
            raise ValueError("count must be a non-negative integer or None")
        if self.material_id is not None and (
            isinstance(self.material_id, bool) or not isinstance(self.material_id, (str, int))
        ):
            raise TypeError("material_id must be a string, integer, or None")
        object.__setattr__(self, "evidence", _validate_evidence(self.evidence))
        object.__setattr__(self, "unresolved", _validate_unresolved(self.unresolved))

    @property
    def kind(self) -> str | None:
        """Convenient alias for the explicit element type."""

        return self.element_type

    def to_dict(self) -> dict[str, object]:
        return {
            "element_type": self.element_type,
            "order": self.order,
            "integration": self.integration,
            "material_id": self.material_id,
            "region": self.region,
            "count": self.count,
            "evidence": [item.to_dict() for item in self.evidence],
            "unresolved": [item.to_dict() for item in self.unresolved],
        }


@dataclass(frozen=True, slots=True)
class ContactIntent:
    """Explicit contact participants and formulation."""

    name: str | None = None
    master: str | None = None
    slave: str | None = None
    formulation: str | None = None
    friction: object | None = None
    enforcement: str | None = None
    evidence: tuple[EvidenceProvenance, ...] = ()
    unresolved: tuple[UnresolvedEvidenceField, ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("name", "master", "slave", "formulation", "enforcement"):
            _validate_optional_text(field_name, getattr(self, field_name))
        object.__setattr__(self, "friction", freeze_value(self.friction))
        object.__setattr__(self, "evidence", _validate_evidence(self.evidence))
        object.__setattr__(self, "unresolved", _validate_unresolved(self.unresolved))

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "master": self.master,
            "slave": self.slave,
            "formulation": self.formulation,
            "friction": thaw_json(self.friction),
            "enforcement": self.enforcement,
            "evidence": [item.to_dict() for item in self.evidence],
            "unresolved": [item.to_dict() for item in self.unresolved],
        }


@dataclass(frozen=True, slots=True)
class ROIIntent:
    """Explicit region-of-interest selection and requested fields."""

    name: str | None = None
    selector: str | None = None
    fields: tuple[str, ...] = ()
    load_path: str | None = None
    evidence: tuple[EvidenceProvenance, ...] = ()
    unresolved: tuple[UnresolvedEvidenceField, ...] = ()

    def __post_init__(self) -> None:
        _validate_optional_text("name", self.name)
        _validate_optional_text("selector", self.selector)
        _validate_optional_text("load_path", self.load_path)
        object.__setattr__(self, "fields", frozen_strings(self.fields))
        object.__setattr__(self, "evidence", _validate_evidence(self.evidence))
        object.__setattr__(self, "unresolved", _validate_unresolved(self.unresolved))

    @property
    def evaluation_fields(self) -> tuple[str, ...]:
        return self.fields

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "selector": self.selector,
            "fields": list(self.fields),
            "load_path": self.load_path,
            "evidence": [item.to_dict() for item in self.evidence],
            "unresolved": [item.to_dict() for item in self.unresolved],
        }


@dataclass(frozen=True, slots=True)
class EvaluationIntent:
    """Explicit output quantity, location, and reduction requirements."""

    name: str | None = None
    quantities: tuple[str, ...] = ()
    location: str | None = None
    reduction: str | None = None
    units: str | None = None
    evidence: tuple[EvidenceProvenance, ...] = ()
    unresolved: tuple[UnresolvedEvidenceField, ...] = ()

    def __post_init__(self) -> None:
        _validate_optional_text("name", self.name)
        _validate_optional_text("location", self.location)
        _validate_optional_text("reduction", self.reduction)
        _validate_optional_text("units", self.units)
        object.__setattr__(self, "quantities", frozen_strings(self.quantities))
        object.__setattr__(self, "evidence", _validate_evidence(self.evidence))
        object.__setattr__(self, "unresolved", _validate_unresolved(self.unresolved))

    @property
    def requested_quantities(self) -> tuple[str, ...]:
        return self.quantities

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "quantities": list(self.quantities),
            "location": self.location,
            "reduction": self.reduction,
            "units": self.units,
            "evidence": [item.to_dict() for item in self.evidence],
            "unresolved": [item.to_dict() for item in self.unresolved],
        }


MeshDescriptor = MeshIntent
ElementDescriptor = ElementIntent
ContactDescriptor = ContactIntent
ROIDescriptor = ROIIntent
EvaluationDescriptor = EvaluationIntent


__all__ = [
    "ContactDescriptor",
    "ContactIntent",
    "ElementDescriptor",
    "ElementIntent",
    "EvaluationDescriptor",
    "EvaluationIntent",
    "MeshDescriptor",
    "MeshIntent",
    "ROIDescriptor",
    "ROIIntent",
]
