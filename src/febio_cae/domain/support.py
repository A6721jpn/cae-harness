"""Immutable, explicit support intent and component constraints."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .canonical import canonical_bytes
from .evidence import EvidenceRef
from .selection import FaceSetRule, SelectionRef
from .spatial import FrameId, OpaqueId, RigidTransform

SCHEMA_VERSION = "1"
_SUPPORT_ROLE = "support_surface"
_COMPONENT_TARGETS = frozenset({"support.x", "support.y", "support.z"})
_FRAME_TARGET = "support.frame"
_TRANSFORM_TARGET = "support.transform"


class SupportValidationError(ValueError):
    """Raised when explicit support intent is structurally invalid."""


def _selection_unordered_paths(
    selection: SelectionRef, prefix: tuple[str, ...]
) -> list[tuple[str, ...]]:
    paths: list[tuple[str, ...]] = []
    if isinstance(selection.rule, FaceSetRule):
        paths.append(prefix + ("rule", "face_ids"))
    if selection.resolution is not None:
        paths.append(prefix + ("resolution", "faces"))
    return paths


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
    frame_evidence: EvidenceRef | None = None
    transform_evidence: EvidenceRef | None = None

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
        if not isinstance(self.frame_evidence, EvidenceRef):
            raise SupportValidationError("frame_evidence must be an EvidenceRef")
        if self.frame_evidence.target_field != _FRAME_TARGET:
            raise SupportValidationError(
                f"frame_evidence must target {_FRAME_TARGET!r}, not "
                f"{self.frame_evidence.target_field!r}"
            )
        for axis, component in (("x", self.x), ("y", self.y), ("z", self.z)):
            if not isinstance(component, SupportComponent):
                raise SupportValidationError(f"{axis} must be a SupportComponent")
            if component.evidence.target_field != f"support.{axis}":
                raise SupportValidationError(f"{axis}.evidence must target 'support.{axis}'")
        if self.transform is not None and not isinstance(self.transform, RigidTransform):
            raise SupportValidationError("transform must be a RigidTransform or None")
        if self.transform is None:
            if self.transform_evidence is not None:
                raise SupportValidationError(
                    "transform_evidence is only valid when transform is supplied"
                )
        elif not isinstance(self.transform_evidence, EvidenceRef):
            raise SupportValidationError(
                "transform_evidence must be supplied when transform is supplied"
            )
        elif self.transform_evidence.target_field != _TRANSFORM_TARGET:
            raise SupportValidationError(
                f"transform_evidence must target {_TRANSFORM_TARGET!r}, not "
                f"{self.transform_evidence.target_field!r}"
            )
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
        # Nested selection/spatial values defer SI conversion until projection.
        # Validate the complete support boundary here so accepted values cannot
        # become unserializable later.
        self.selection.to_bytes()
        if self.transform is not None:
            self.transform.to_bytes()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "support_id": self.support_id.value,
            "selection": self.selection.to_dict(),
            "frame": self.frame.value,
            "frame_evidence": self.frame_evidence.to_dict(),
            "x": self.x.to_dict(),
            "y": self.y.to_dict(),
            "z": self.z.to_dict(),
            "transform": None if self.transform is None else self.transform.to_dict(),
            "transform_evidence": (
                None if self.transform_evidence is None else self.transform_evidence.to_dict()
            ),
        }

    def to_bytes(self) -> bytes:
        unordered_paths = _selection_unordered_paths(self.selection, ("selection",))
        return canonical_bytes(self.to_dict(), unordered_paths=unordered_paths)


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
        unordered_paths: list[tuple[str, ...]] = []
        for index, support in enumerate(self.supports):
            unordered_paths.extend(
                _selection_unordered_paths(support.selection, ("supports", str(index), "selection"))
            )
        return canonical_bytes(self.to_dict(), unordered_paths=unordered_paths)


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
