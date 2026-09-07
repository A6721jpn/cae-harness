"""Immutable, explicit support intent and component constraints."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .canonical import canonical_bytes
from .evidence import EvidenceRef
from .selection import SelectionRef
from .spatial import FrameId, OpaqueId, RigidTransform

SCHEMA_VERSION = "1"
_SUPPORT_ROLE = "support_surface"
_COMPONENT_TARGETS = frozenset({"support.x", "support.y", "support.z"})


class SupportValidationError(ValueError):
    """Raised when explicit support intent is structurally invalid."""


@dataclass(frozen=True, slots=True)
class SupportId(OpaqueId):
    """Opaque identifier for one support intent."""


@dataclass(frozen=True, slots=True)
class SupportComponent:
    """One explicit translational support component."""

    state: str
    evidence: EvidenceRef

    def __post_init__(self) -> None:
        if self.state not in {"fixed", "free"}:
            raise SupportValidationError("support component state must be 'fixed' or 'free'")
        if not isinstance(self.evidence, EvidenceRef):
            raise SupportValidationError("evidence must be an EvidenceRef")
        if self.evidence.target_field not in _COMPONENT_TARGETS:
            raise SupportValidationError(
                "support component evidence must target support.x, support.y, or support.z"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "state": self.state,
            "evidence": self.evidence.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class SolidSupport:
    """A support region with explicit x/y/z intent in an explicit frame."""

    support_id: SupportId
    selection: SelectionRef
    frame: FrameId
    x: SupportComponent
    y: SupportComponent
    z: SupportComponent
    transform: RigidTransform | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.support_id, SupportId):
            raise SupportValidationError("support_id must be a SupportId")
        if not isinstance(self.selection, SelectionRef):
            raise SupportValidationError("selection must be a SelectionRef")
        if self.selection.stated_role != _SUPPORT_ROLE:
            raise SupportValidationError(
                f"selection role must be {_SUPPORT_ROLE!r} for a SolidSupport"
            )
        if not isinstance(self.frame, FrameId):
            raise SupportValidationError("frame must be a FrameId")
        for axis, component in (("x", self.x), ("y", self.y), ("z", self.z)):
            if not isinstance(component, SupportComponent):
                raise SupportValidationError(f"{axis} must be a SupportComponent")
            if component.evidence.target_field != f"support.{axis}":
                raise SupportValidationError(f"{axis}.evidence must target 'support.{axis}'")
        if self.transform is not None and not isinstance(self.transform, RigidTransform):
            raise SupportValidationError("transform must be a RigidTransform or None")
        if self.transform is None:
            if self.selection.frame != self.frame:
                raise SupportValidationError(
                    "selection frame must match support frame when transform is omitted"
                )
        elif (
            self.transform.source_frame != self.selection.frame
            or self.transform.target_frame != self.frame
        ):
            raise SupportValidationError(
                "support transform must map selection.frame to the support frame"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "support_id": self.support_id.value,
            "selection": self.selection.to_dict(),
            "frame": self.frame.value,
            "x": self.x.to_dict(),
            "y": self.y.to_dict(),
            "z": self.z.to_dict(),
            "transform": None if self.transform is None else self.transform.to_dict(),
        }

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


@dataclass(frozen=True, slots=True)
class SupportSet:
    """Immutable semantic set of supports sorted by their explicit IDs."""

    supports: Sequence[SolidSupport]

    def __post_init__(self) -> None:
        if isinstance(self.supports, (str, bytes)) or not isinstance(self.supports, Sequence):
            raise SupportValidationError("supports must be a sequence")
        copied = tuple(self.supports)
        if any(not isinstance(item, SolidSupport) for item in copied):
            raise SupportValidationError("supports contains an invalid SolidSupport")
        identifiers = [item.support_id.value for item in copied]
        if len(set(identifiers)) != len(identifiers):
            raise SupportValidationError("supports must not contain duplicate support IDs")
        object.__setattr__(
            self,
            "supports",
            tuple(sorted(copied, key=lambda item: item.support_id.value)),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "supports": [support.to_dict() for support in self.supports],
        }

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


SupportCollection = SupportSet


__all__ = [
    "SCHEMA_VERSION",
    "SolidSupport",
    "SupportCollection",
    "SupportComponent",
    "SupportId",
    "SupportSet",
    "SupportValidationError",
]
