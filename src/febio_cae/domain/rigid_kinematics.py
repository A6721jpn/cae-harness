"""Immutable rigid-tool kinematics and supplied-motion compatibility intent."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Literal, cast

from .canonical import canonical_bytes
from .evidence import EvidenceRef
from .motion import MotionProfile
from .rigid import RigidPrimitive
from .selection import SelectionRef
from .spatial import FrameId

SCHEMA_VERSION = "1"
_TOOL_CONTACT_ROLE = "tool_contact_surface"
_FRAME_TARGET = "rigid_tool.frame"
_CONTACT_SURFACE_TARGET = "rigid_tool.contact_surface"
_DOF_TARGETS = {
    "x": "rigid_tool.x",
    "y": "rigid_tool.y",
    "z": "rigid_tool.z",
    "rx": "rigid_tool.rx",
    "ry": "rigid_tool.ry",
    "rz": "rigid_tool.rz",
}
_TRANSLATIONAL_AXES = ("x", "y", "z")
_ROTATIONAL_AXES = ("rx", "ry", "rz")
_ALL_AXES = _TRANSLATIONAL_AXES + _ROTATIONAL_AXES


class RigidKinematicsValidationError(ValueError):
    """Raised when explicit rigid kinematics intent is structurally invalid."""


class RigidDofState(str, Enum):
    """The only explicit intent tags allowed for one rigid degree of freedom."""

    FIXED = "fixed"
    FREE = "free"
    PRESCRIBED = "prescribed"

    fixed = FIXED
    free = FREE
    prescribed = PRESCRIBED


RigidDOFState = RigidDofState
DofState = RigidDofState


def _require_evidence(value: object, target_field: str, field: str) -> EvidenceRef:
    if not isinstance(value, EvidenceRef):
        raise RigidKinematicsValidationError(f"{field} must be an EvidenceRef")
    if value.target_field != target_field:
        raise RigidKinematicsValidationError(
            f"{field} must target {target_field!r}, not {value.target_field!r}"
        )
    return value


def _canonical_selection_projection(selection: SelectionRef) -> dict[str, object]:
    projected = json.loads(selection.to_bytes().decode("utf-8"))
    if not isinstance(projected, dict):
        raise RigidKinematicsValidationError("canonical selection projection must be an object")
    return cast(dict[str, object], projected)


def _state(value: object, field: str) -> str:
    if isinstance(value, RigidDofState):
        return value.value
    if isinstance(value, str) and value in {item.value for item in RigidDofState}:
        return value
    raise RigidKinematicsValidationError(f"{field} must be 'fixed', 'free', or 'prescribed'")


@dataclass(frozen=True, slots=True)
class RigidDofComponent:
    """One explicitly evidenced fixed, free, or prescribed rigid DOF tag."""

    state: str | RigidDofState
    evidence: EvidenceRef

    def __post_init__(self) -> None:
        object.__setattr__(self, "state", _state(self.state, "state"))
        if not isinstance(self.evidence, EvidenceRef):
            raise RigidKinematicsValidationError("evidence must be an EvidenceRef")
        if self.evidence.target_field not in set(_DOF_TARGETS.values()):
            raise RigidKinematicsValidationError(
                "evidence must target one explicit rigid_tool DOF component"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "state": self.state,
            "evidence": self.evidence.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class RigidDofSpecification:
    """All six rigid DOF intent tags in one explicit frame."""

    frame: FrameId
    x: RigidDofComponent
    y: RigidDofComponent
    z: RigidDofComponent
    rx: RigidDofComponent
    ry: RigidDofComponent
    rz: RigidDofComponent
    frame_evidence: EvidenceRef

    def __post_init__(self) -> None:
        if not isinstance(self.frame, FrameId):
            raise RigidKinematicsValidationError("frame must be a FrameId")
        _require_evidence(self.frame_evidence, _FRAME_TARGET, "frame_evidence")
        for axis in _ALL_AXES:
            component = getattr(self, axis)
            if not isinstance(component, RigidDofComponent):
                raise RigidKinematicsValidationError(f"{axis} must be a RigidDofComponent")
            _require_evidence(component.evidence, _DOF_TARGETS[axis], f"{axis}.evidence")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "frame": self.frame.value,
            "frame_evidence": self.frame_evidence.to_dict(),
            **{axis: getattr(self, axis).to_dict() for axis in _ALL_AXES},
        }

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


RigidDOFComponent = RigidDofComponent
RigidDOFSpecification = RigidDofSpecification
DofComponent = RigidDofComponent
DofSpecification = RigidDofSpecification


@dataclass(frozen=True, slots=True)
class RigidToolIntent:
    """A primitive, explicit tool-contact surface, and six-DOF intent.

    This value intentionally does not contain a motion history.  ``MotionProfile``
    remains the sole owner of direction, reference marker, monotonic history, and
    quasi-static applicability.
    """

    primitive: RigidPrimitive
    contact_surface: SelectionRef
    dofs: RigidDofSpecification
    contact_surface_evidence: EvidenceRef

    def __post_init__(self) -> None:
        if not isinstance(self.primitive, RigidPrimitive):
            raise RigidKinematicsValidationError("primitive must be a RigidPrimitive")
        if not isinstance(self.contact_surface, SelectionRef):
            raise RigidKinematicsValidationError("contact_surface must be a SelectionRef")
        if self.contact_surface.stated_role != _TOOL_CONTACT_ROLE:
            raise RigidKinematicsValidationError(
                f"contact_surface role must be {_TOOL_CONTACT_ROLE!r}"
            )
        if self.contact_surface.body_id != self.primitive.body_id:
            raise RigidKinematicsValidationError(
                "contact_surface body_id must match primitive body_id"
            )
        target_frame = self.primitive.placement.target_frame
        if self.contact_surface.frame != target_frame:
            raise RigidKinematicsValidationError(
                "contact_surface frame must match primitive placement target_frame"
            )
        if not isinstance(self.dofs, RigidDofSpecification):
            raise RigidKinematicsValidationError("dofs must be a RigidDofSpecification")
        if self.dofs.frame != target_frame:
            raise RigidKinematicsValidationError(
                "dofs frame must match primitive placement target_frame"
            )
        _require_evidence(
            self.contact_surface_evidence,
            _CONTACT_SURFACE_TARGET,
            "contact_surface_evidence",
        )

        # Validate nested boundaries now.  This preserves the established
        # primitive and SelectionRef projections without inventing geometry data.
        self.primitive.to_bytes()
        self.contact_surface.to_bytes()
        self.dofs.to_bytes()

    @property
    def selection(self) -> SelectionRef:
        """Compatibility alias for the explicit contact surface."""

        return self.contact_surface

    @property
    def dof_specification(self) -> RigidDofSpecification:
        """Compatibility alias for the complete six-DOF specification."""

        return self.dofs

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "primitive": self.primitive.to_dict(),
            "contact_surface": _canonical_selection_projection(self.contact_surface),
            "contact_surface_evidence": self.contact_surface_evidence.to_dict(),
            "dofs": self.dofs.to_dict(),
        }

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


@dataclass(frozen=True, slots=True)
class KinematicCompatibility:
    """Pure result of checking supplied translational indentation consistency."""

    supported: bool
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.supported, bool):
            raise TypeError("supported must be a bool")
        if self.supported:
            if self.reason is not None:
                raise ValueError("supported compatibility must not have a reason")
        elif not isinstance(self.reason, str) or not self.reason:
            raise ValueError("unsupported compatibility must have a precise reason")

    @property
    def is_supported(self) -> bool:
        return self.supported

    @property
    def status(self) -> Literal["supported", "unsupported"]:
        return "supported" if self.supported else "unsupported"


MotionCompatibility = KinematicCompatibility


def _unsupported(reason: str) -> KinematicCompatibility:
    return KinematicCompatibility(supported=False, reason=reason)


def check_translational_indentation_compatibility(
    tool: RigidToolIntent,
    motion: MotionProfile,
) -> KinematicCompatibility:
    """Check one supplied motion against the explicit rigid-tool DOF tags.

    The check is intentionally pure.  It does not attach the ``MotionProfile``
    to the tool, create a second history, apply a tolerance to direction
    components, transform frames, or claim native solver support.
    """

    if not isinstance(tool, RigidToolIntent):
        raise TypeError("tool must be a RigidToolIntent")
    if not isinstance(motion, MotionProfile):
        raise TypeError("motion must be a MotionProfile")
    if motion.direction.frame != tool.dofs.frame:
        return _unsupported("motion frame must match rigid-tool DOF frame")

    for axis in _ROTATIONAL_AXES:
        state = getattr(tool.dofs, axis).state
        if state != "fixed":
            return _unsupported(
                f"rotation {axis} is {state}; translational indentation requires explicit fixed rotation"
            )

    for axis, component in zip(
        _TRANSLATIONAL_AXES, (motion.direction.x, motion.direction.y, motion.direction.z)
    ):
        state = getattr(tool.dofs, axis).state
        if component == 0.0:
            if state not in {"fixed", "prescribed"}:
                return _unsupported(
                    f"translation {axis} is {state}; motion direction component is exactly zero"
                )
        elif state != "prescribed":
            return _unsupported(
                f"translation {axis} is {state}; nonzero motion direction component requires prescribed state"
            )

    return KinematicCompatibility(supported=True)


check_motion_compatibility = check_translational_indentation_compatibility
validate_translational_indentation_compatibility = check_translational_indentation_compatibility


__all__ = [
    "SCHEMA_VERSION",
    "DofComponent",
    "DofSpecification",
    "DofState",
    "KinematicCompatibility",
    "MotionCompatibility",
    "RigidDOFComponent",
    "RigidDOFSpecification",
    "RigidDOFState",
    "RigidDofComponent",
    "RigidDofSpecification",
    "RigidDofState",
    "RigidKinematicsValidationError",
    "RigidToolIntent",
    "check_motion_compatibility",
    "check_translational_indentation_compatibility",
    "validate_translational_indentation_compatibility",
]
