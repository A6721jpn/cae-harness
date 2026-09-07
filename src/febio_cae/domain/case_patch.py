"""Typed, evidence-bound partial revision edits without apply authority."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

from .budget import Budget
from .canonical import canonical_bytes
from .contact import ContactIntent
from .evidence import EvidenceRef
from .geometry import GeometryIntent
from .material import CompressibleNeoHookean, IsotropicLinearElastic
from .mesh_policy import MeshPolicy
from .motion import MotionProfile
from .output_policy import OutputPolicy
from .quality_policy import QualityPolicy
from .rigid_kinematics import RigidToolIntent
from .solver_policy import SolverPolicy
from .support import SupportSet

SCHEMA_VERSION = "1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FIELD_TYPES: dict[str, tuple[type[object], ...]] = {
    "geometry": (GeometryIntent,),
    "material": (IsotropicLinearElastic, CompressibleNeoHookean),
    "support": (SupportSet,),
    "rigid_tool": (RigidToolIntent,),
    "motion": (MotionProfile,),
    "contact": (ContactIntent,),
    "mesh_policy": (MeshPolicy,),
    "solver_policy": (SolverPolicy,),
    "outputs": (OutputPolicy,),
    "quality_policy": (QualityPolicy,),
    "budget": (Budget,),
}


class CasePatchValidationError(ValueError):
    """Raised when a typed parent-bound patch is malformed."""


def _identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise CasePatchValidationError(
            f"{field_name} must be non-empty text without surrounding whitespace"
        )
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise CasePatchValidationError(f"{field_name} contains a Unicode control character")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise CasePatchValidationError(f"{field_name} must be UTF-8 representable") from error
    return value


def _digest(value: object, field_name: str) -> str:
    value = _identifier(value, field_name)
    if _SHA256.fullmatch(value) is None:
        raise CasePatchValidationError(
            f"{field_name} must be a lowercase 64-character SHA-256 digest"
        )
    return value


def _evidence(value: object, field_name: str) -> tuple[EvidenceRef, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise CasePatchValidationError(f"{field_name} must be a sequence")
    copied = tuple(value)
    if not copied or any(not isinstance(item, EvidenceRef) for item in copied):
        raise CasePatchValidationError(f"{field_name} must be a non-empty EvidenceRef sequence")
    keyed = [(canonical_bytes(item.to_dict()), item) for item in copied]
    if len({key for key, _ in keyed}) != len(keyed):
        raise CasePatchValidationError(f"{field_name} contains duplicate complete evidence")
    return tuple(item for _, item in sorted(keyed, key=lambda pair: pair[0]))


@dataclass(frozen=True, slots=True)
class CasePatchEdit:
    """One allowed top-level CaseSpec edit with explicit absence semantics."""

    field: str
    value: object | None
    present: bool

    def __post_init__(self) -> None:
        if self.field not in _FIELD_TYPES:
            raise CasePatchValidationError(f"unsupported patch field: {self.field!r}")
        if not isinstance(self.present, bool):
            raise CasePatchValidationError("present must be a bool")
        if self.present:
            if not isinstance(self.value, _FIELD_TYPES[self.field]):
                names = ", ".join(item.__name__ for item in _FIELD_TYPES[self.field])
                raise CasePatchValidationError(f"{self.field} value must be one of {names}")
        elif self.value is not None:
            raise CasePatchValidationError("an absent edit must carry value=None")

    def to_dict(self) -> dict[str, object]:
        value = None if self.value is None else cast(Any, self.value).to_dict()
        return {
            "schema_version": SCHEMA_VERSION,
            "field": self.field,
            "present": self.present,
            "value": value,
        }


@dataclass(frozen=True, slots=True)
class CasePatch:
    parent_revision_id: str
    parent_spec_digest: str
    edits: Sequence[CasePatchEdit]
    evidence: Sequence[EvidenceRef]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "parent_revision_id", _identifier(self.parent_revision_id, "parent_revision_id")
        )
        object.__setattr__(
            self, "parent_spec_digest", _digest(self.parent_spec_digest, "parent_spec_digest")
        )
        edits = tuple(self.edits)
        if not edits or any(not isinstance(item, CasePatchEdit) for item in edits):
            raise CasePatchValidationError("edits must be a non-empty CasePatchEdit sequence")
        if len({item.field for item in edits}) != len(edits):
            raise CasePatchValidationError("edits must not contain duplicate fields")
        object.__setattr__(self, "edits", edits)
        object.__setattr__(self, "evidence", _evidence(self.evidence, "evidence"))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "parent_revision_id": self.parent_revision_id,
            "parent_spec_digest": self.parent_spec_digest,
            "edits": [item.to_dict() for item in self.edits],
            "evidence": [item.to_dict() for item in self.evidence],
        }

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


__all__ = ["SCHEMA_VERSION", "CasePatch", "CasePatchEdit", "CasePatchValidationError"]
