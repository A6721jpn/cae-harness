"""Immutable composition of explicit case-domain intent values."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import cast

from .budget import Budget
from .canonical import canonical_bytes
from .contact import ContactIntent
from .geometry import GeometryIntent
from .material import CompressibleNeoHookean, IsotropicLinearElastic, MaterialCandidate
from .mesh_policy import MeshPolicy
from .motion import MotionProfile
from .output_policy import OutputPolicy
from .quality_policy import QualityPolicy
from .rigid_kinematics import RigidToolIntent
from .selection import SelectionRef
from .solver_policy import SolverPolicy
from .support import SupportSet

SCHEMA_VERSION = "1"


class CaseSpecValidationError(ValueError):
    """Raised when a composed case specification is invalid."""


_MATERIAL_TYPES = (IsotropicLinearElastic, CompressibleNeoHookean)
_FIELD_TYPES: dict[str, tuple[type[object], ...]] = {
    "geometry": (GeometryIntent,),
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
_FIELD_ORDER = (
    "geometry",
    "material",
    "support",
    "rigid_tool",
    "motion",
    "contact",
    "mesh_policy",
    "solver_policy",
    "outputs",
    "quality_policy",
    "budget",
)


def _canonical_projection(value: object, field: str) -> dict[str, object]:
    """Decode a child owner's public canonical bytes without reserializing it privately."""

    to_bytes = getattr(value, "to_bytes", None)
    if not callable(to_bytes):
        raise CaseSpecValidationError(f"{field} has no public canonical projection")
    try:
        raw = to_bytes()
        projection = json.loads(raw.decode("utf-8"))
    except (AttributeError, TypeError, UnicodeError, ValueError, OverflowError) as error:
        raise CaseSpecValidationError(
            f"{field} canonical projection is invalid: {error}"
        ) from error
    if not isinstance(projection, dict):
        raise CaseSpecValidationError(f"{field} canonical projection must be an object")
    return cast(dict[str, object], projection)


def _require_selection_context(
    selection: SelectionRef,
    field: str,
    expected: tuple[str, object, object],
    tool: tuple[str, object, object],
    *,
    allow_tool: bool = True,
) -> None:
    geometry_digest, body_id, frame = expected
    tool_geometry_digest, tool_body_id, tool_frame = tool
    if selection.frame != frame:
        raise CaseSpecValidationError(f"{field} selection must use the geometry placement frame")
    allowed_identities = {(geometry_digest, body_id)}
    if allow_tool:
        allowed_identities.add((tool_geometry_digest, tool_body_id))
    if (selection.geometry_digest, selection.body_id) not in allowed_identities:
        raise CaseSpecValidationError(
            f"{field} selection must identify the declared part or rigid tool"
        )
    if tool_frame != frame:
        raise CaseSpecValidationError(
            "rigid tool contact selection must use the geometry placement frame"
        )


@dataclass(frozen=True, slots=True)
class CaseSpec:
    """One explicit, locally cross-referenced case intent composition."""

    geometry: GeometryIntent
    material: MaterialCandidate
    support: SupportSet
    rigid_tool: RigidToolIntent
    motion: MotionProfile
    contact: ContactIntent
    mesh_policy: MeshPolicy
    solver_policy: SolverPolicy
    outputs: OutputPolicy
    quality_policy: QualityPolicy
    budget: Budget

    def __post_init__(self) -> None:
        for field, expected_types in _FIELD_TYPES.items():
            if not isinstance(getattr(self, field), expected_types):
                expected = ", ".join(item.__name__ for item in expected_types)
                raise CaseSpecValidationError(f"{field} must be a {expected}")
        if not isinstance(self.material, _MATERIAL_TYPES):
            raise CaseSpecValidationError(
                "material must be an IsotropicLinearElastic or CompressibleNeoHookean"
            )

        # Every child remains the sole owner of its own projection and semantic
        # set rules.  The parent consumes only the public canonical bytes.
        for field in _FIELD_ORDER:
            _canonical_projection(getattr(self, field), field)

        geometry = self.geometry
        target_frame = geometry.placement.target_frame
        part_context = (geometry.geometry_digest, geometry.body_id, target_frame)
        tool_context = (
            self.rigid_tool.contact_surface.geometry_digest,
            self.rigid_tool.contact_surface.body_id,
            self.rigid_tool.contact_surface.frame,
        )

        if self.geometry.body_id == self.rigid_tool.primitive.body_id:
            raise CaseSpecValidationError(
                "geometry and rigid_tool primitive must use distinct body IDs"
            )
        if self.rigid_tool.primitive.placement.target_frame != target_frame:
            raise CaseSpecValidationError("rigid_tool placement target frame must match geometry")
        if self.contact.pair_frame != target_frame:
            raise CaseSpecValidationError(
                "contact pair_frame must match geometry placement target frame"
            )
        if self.motion.direction.frame != target_frame:
            raise CaseSpecValidationError(
                "motion direction frame must match geometry placement target frame"
            )

        for index, support in enumerate(self.support.supports):
            _require_selection_context(
                support.selection,
                f"support.supports[{index}]",
                part_context,
                tool_context,
                allow_tool=False,
            )
        _require_selection_context(
            self.contact.part_surface,
            "contact.part_surface",
            part_context,
            tool_context,
            allow_tool=False,
        )

        if self.contact.tool_surface.to_bytes() != self.rigid_tool.contact_surface.to_bytes():
            raise CaseSpecValidationError(
                "contact.tool_surface must equal rigid_tool.contact_surface canonically"
            )

        for index, refinement in enumerate(self.mesh_policy.local_refinements):
            _require_selection_context(
                refinement.selection,
                f"mesh_policy.local_refinements[{index}]",
                part_context,
                tool_context,
            )
        for index, request in enumerate(self.outputs.requests):
            _require_selection_context(
                request.selection,
                f"outputs.requests[{index}]",
                part_context,
                tool_context,
            )
        for index, evaluation in enumerate(self.outputs.evaluations):
            _require_selection_context(
                evaluation.selection,
                f"outputs.evaluations[{index}]",
                part_context,
                tool_context,
            )

        evaluation_ids = {evaluation.evaluation_id for evaluation in self.outputs.evaluations}
        for criterion in self.quality_policy.criteria:
            for evaluation_id in criterion.evaluation_ids:
                if evaluation_id not in evaluation_ids:
                    raise CaseSpecValidationError(
                        f"quality_policy criterion {criterion.criterion_id!r} references "
                        f"missing evaluation {evaluation_id!r}"
                    )

        start_time = float(self.motion.samples[0].time.to_si().value)
        end_time = float(self.motion.samples[-1].time.to_si().value)
        for index, saved_time in enumerate(self.outputs.saved_times):
            current_time = float(saved_time.to_si().value)
            if current_time < start_time or current_time > end_time:
                raise CaseSpecValidationError(
                    f"outputs.saved_times[{index}] must lie within the inclusive motion sample interval"
                )

        try:
            self.to_bytes()
        except (TypeError, UnicodeError, ValueError, OverflowError) as error:
            raise CaseSpecValidationError(
                f"case spec canonical projection is invalid: {error}"
            ) from error

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            **{field: _canonical_projection(getattr(self, field), field) for field in _FIELD_ORDER},
        }

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


__all__ = ["SCHEMA_VERSION", "CaseSpec", "CaseSpecValidationError"]
