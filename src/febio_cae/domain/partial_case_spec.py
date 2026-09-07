"""Immutable top-level partial composition of explicit case-domain values."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import cast

from .budget import Budget
from .canonical import canonical_bytes
from .case_spec import CaseSpec
from .contact import ContactIntent
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


class PartialCaseSpecValidationError(ValueError):
    """Raised when a partial case specification is structurally invalid."""


def _canonical_projection(value: object, field_name: str) -> dict[str, object]:
    to_bytes = getattr(value, "to_bytes", None)
    if not callable(to_bytes):
        raise PartialCaseSpecValidationError(f"{field_name} has no public canonical projection")
    try:
        projection = json.loads(to_bytes().decode("utf-8"))
    except (AttributeError, TypeError, UnicodeError, ValueError, OverflowError) as error:
        raise PartialCaseSpecValidationError(
            f"{field_name} canonical projection is invalid: {error}"
        ) from error
    if not isinstance(projection, dict):
        raise PartialCaseSpecValidationError(f"{field_name} canonical projection must be an object")
    return cast(dict[str, object], projection)


@dataclass(frozen=True, slots=True)
class PartialCaseSpec:
    """An immutable top-level snapshot with explicitly absent child values."""

    geometry: GeometryIntent | None = None
    material: IsotropicLinearElastic | CompressibleNeoHookean | None = None
    support: SupportSet | None = None
    rigid_tool: RigidToolIntent | None = None
    motion: MotionProfile | None = None
    contact: ContactIntent | None = None
    mesh_policy: MeshPolicy | None = None
    solver_policy: SolverPolicy | None = None
    outputs: OutputPolicy | None = None
    quality_policy: QualityPolicy | None = None
    budget: Budget | None = None
    unresolved_fields: tuple[str, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        missing: list[str] = []
        for field_name in _FIELD_ORDER:
            value = getattr(self, field_name)
            if value is None:
                missing.append(field_name)
                continue
            expected_types = _FIELD_TYPES[field_name]
            if not isinstance(value, expected_types):
                expected = ", ".join(item.__name__ for item in expected_types)
                raise PartialCaseSpecValidationError(f"{field_name} must be None or a {expected}")
            _canonical_projection(value, field_name)

        object.__setattr__(self, "unresolved_fields", tuple(sorted(missing)))
        self.to_bytes()

    def to_case_spec(self) -> CaseSpec:
        """Convert a complete snapshot through the existing CaseSpec validator."""

        if self.unresolved_fields:
            raise PartialCaseSpecValidationError(
                "cannot convert partial case spec; missing fields: "
                + ", ".join(self.unresolved_fields)
            )
        return CaseSpec(
            geometry=cast(GeometryIntent, self.geometry),
            material=cast(IsotropicLinearElastic | CompressibleNeoHookean, self.material),
            support=cast(SupportSet, self.support),
            rigid_tool=cast(RigidToolIntent, self.rigid_tool),
            motion=cast(MotionProfile, self.motion),
            contact=cast(ContactIntent, self.contact),
            mesh_policy=cast(MeshPolicy, self.mesh_policy),
            solver_policy=cast(SolverPolicy, self.solver_policy),
            outputs=cast(OutputPolicy, self.outputs),
            quality_policy=cast(QualityPolicy, self.quality_policy),
            budget=cast(Budget, self.budget),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            **{
                field_name: (
                    None
                    if (value := getattr(self, field_name)) is None
                    else _canonical_projection(value, field_name)
                )
                for field_name in _FIELD_ORDER
            },
        }

    def to_bytes(self) -> bytes:
        try:
            return canonical_bytes(self.to_dict())
        except PartialCaseSpecValidationError:
            raise
        except (TypeError, UnicodeError, ValueError, OverflowError) as error:
            raise PartialCaseSpecValidationError(
                f"partial case spec canonical projection is invalid: {error}"
            ) from error


__all__ = ["SCHEMA_VERSION", "PartialCaseSpec", "PartialCaseSpecValidationError"]
