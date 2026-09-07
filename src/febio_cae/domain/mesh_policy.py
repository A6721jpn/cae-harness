"""Immutable computational mesh-policy intent values."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from .canonical import canonical_bytes
from .selection import FaceSetRule, SelectionRef
from .units import Dimension, Quantity

SCHEMA_VERSION = "1"
_LENGTH = Dimension(length=1)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PROFILE_PURPOSES = frozenset({"mesh_quality", "solver", "outputs", "quality"})


class MeshPolicyValidationError(ValueError):
    """Raised when a mesh policy value is structurally or numerically invalid."""


def _require_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise MeshPolicyValidationError(
            f"{field} must be a non-empty string without surrounding whitespace"
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise MeshPolicyValidationError(f"{field} contains a control character")
    return value


def _require_digest(value: object, field: str) -> str:
    value = _require_text(value, field)
    if _SHA256.fullmatch(value) is None:
        raise MeshPolicyValidationError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _require_positive_length(value: object, field: str) -> Quantity:
    if not isinstance(value, Quantity) or value.dimension != _LENGTH:
        raise MeshPolicyValidationError(f"{field} must be a length Quantity")
    try:
        si_value = value.to_si()
    except ValueError as error:
        raise MeshPolicyValidationError(f"{field} is not SI-representable: {error}") from error
    if si_value.value <= 0:
        raise MeshPolicyValidationError(f"{field} must be strictly positive")
    return value


def _quantity_dict(value: Quantity) -> dict[str, float | str]:
    si_value = value.to_si()
    return {"value": float(si_value.value), "unit": si_value.unit}


def _selection_unordered_paths(
    prefix: tuple[str, ...], selection: SelectionRef
) -> list[tuple[str, ...]]:
    paths: list[tuple[str, ...]] = []
    if isinstance(selection.rule, FaceSetRule):
        paths.append(prefix + ("rule", "face_ids"))
    if selection.resolution is not None:
        paths.append(prefix + ("resolution", "faces"))
    return paths


@dataclass(frozen=True, slots=True)
class NumericalProfileRef:
    """Identity of an immutable numerical profile record supplied elsewhere."""

    profile_id: str
    purpose: str
    record_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile_id", _require_text(self.profile_id, "profile_id"))
        purpose = _require_text(self.purpose, "purpose")
        if purpose not in _PROFILE_PURPOSES:
            raise MeshPolicyValidationError(f"unsupported profile purpose: {purpose!r}")
        object.__setattr__(self, "purpose", purpose)
        object.__setattr__(
            self,
            "record_digest",
            _require_digest(self.record_digest, "record_digest"),
        )
        try:
            self.to_bytes()
        except (TypeError, ValueError) as error:
            raise MeshPolicyValidationError(
                f"profile projection is not canonically serializable: {error}"
            ) from error

    def to_dict(self) -> dict[str, str]:
        return {
            "schema_version": SCHEMA_VERSION,
            "profile_id": self.profile_id,
            "purpose": self.purpose,
            "record_digest": self.record_digest,
        }

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


@dataclass(frozen=True, slots=True)
class LocalRefinement:
    """A named local upper element-size bound over an explicit selection."""

    refinement_id: str
    selection: SelectionRef
    size: Quantity

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "refinement_id",
            _require_text(self.refinement_id, "refinement_id"),
        )
        if not isinstance(self.selection, SelectionRef):
            raise MeshPolicyValidationError("selection must be a SelectionRef")
        object.__setattr__(self, "size", _require_positive_length(self.size, "size"))
        try:
            self.to_bytes()
        except (TypeError, ValueError) as error:
            raise MeshPolicyValidationError(
                f"local refinement projection is not canonically serializable: {error}"
            ) from error

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "refinement_id": self.refinement_id,
            "selection": self.selection.to_dict(),
            "size": _quantity_dict(self.size),
        }

    def to_bytes(self) -> bytes:
        return canonical_bytes(
            self.to_dict(),
            unordered_paths=_selection_unordered_paths(("selection",), self.selection),
        )


@dataclass(frozen=True, slots=True)
class MeshPolicy:
    """Explicit tet10 mesh sizing and profile-reference intent."""

    element_type: str
    global_size: Quantity
    local_refinements: Sequence[LocalRefinement]
    quality_profile: NumericalProfileRef
    max_refinements: int

    def __post_init__(self) -> None:
        element_type = _require_text(self.element_type, "element_type")
        if element_type != "tet10":
            raise MeshPolicyValidationError("element_type must be exactly 'tet10'")
        object.__setattr__(self, "element_type", element_type)

        global_size = _require_positive_length(self.global_size, "global_size")
        object.__setattr__(self, "global_size", global_size)

        if isinstance(self.local_refinements, (str, bytes)) or not isinstance(
            self.local_refinements, Sequence
        ):
            raise MeshPolicyValidationError("local_refinements must be a sequence")
        copied = tuple(self.local_refinements)
        if any(not isinstance(item, LocalRefinement) for item in copied):
            raise MeshPolicyValidationError("local_refinements contains an invalid LocalRefinement")
        identifiers = [item.refinement_id for item in copied]
        if len(set(identifiers)) != len(identifiers):
            duplicates = sorted(
                identifier for identifier in set(identifiers) if identifiers.count(identifier) > 1
            )
            raise MeshPolicyValidationError(f"duplicate refinement_id: {duplicates[0]}")
        ordered = tuple(sorted(copied, key=lambda item: item.refinement_id))
        for item in ordered:
            if item.size.to_si().value > global_size.to_si().value:
                raise MeshPolicyValidationError(
                    f"local refinement {item.refinement_id!r} size must not exceed global_size"
                )
        object.__setattr__(self, "local_refinements", ordered)

        if not isinstance(self.quality_profile, NumericalProfileRef):
            raise MeshPolicyValidationError("quality_profile must be a NumericalProfileRef")
        if self.quality_profile.purpose != "mesh_quality":
            raise MeshPolicyValidationError("quality_profile must have mesh_quality purpose")

        if isinstance(self.max_refinements, bool) or not isinstance(self.max_refinements, int):
            raise MeshPolicyValidationError("max_refinements must be an integer")
        if self.max_refinements < 0:
            raise MeshPolicyValidationError("max_refinements must be nonnegative")

        try:
            self.to_bytes()
        except (TypeError, ValueError) as error:
            raise MeshPolicyValidationError(
                f"mesh policy projection is not canonically serializable: {error}"
            ) from error

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "element_type": self.element_type,
            "global_size": _quantity_dict(self.global_size),
            "local_refinements": [item.to_dict() for item in self.local_refinements],
            "quality_profile": self.quality_profile.to_dict(),
            "max_refinements": self.max_refinements,
        }

    def to_bytes(self) -> bytes:
        payload = self.to_dict()
        unordered_paths: list[tuple[str, ...]] = []
        for index, item in enumerate(self.local_refinements):
            unordered_paths.extend(
                _selection_unordered_paths(
                    ("local_refinements", str(index), "selection"), item.selection
                )
            )
        return canonical_bytes(payload, unordered_paths=unordered_paths)


__all__ = [
    "SCHEMA_VERSION",
    "LocalRefinement",
    "MeshPolicy",
    "MeshPolicyValidationError",
    "NumericalProfileRef",
]
