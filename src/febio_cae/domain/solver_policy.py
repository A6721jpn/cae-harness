"""Immutable numerical solver-policy intent values."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from .canonical import canonical_bytes
from .mesh_policy import NumericalProfileRef
from .units import Dimension, Quantity

SCHEMA_VERSION = "1"
_TIME = Dimension(time=1)
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SolverPolicyValidationError(ValueError):
    """Raised when a solver numerical intent value is structurally invalid."""


def _require_identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise SolverPolicyValidationError(
            f"{field} must be a non-empty ASCII identifier starting with a letter or underscore"
        )
    return value


def _require_time_quantity(value: object, field: str, *, positive: bool) -> Quantity:
    if not isinstance(value, Quantity) or value.dimension != _TIME:
        raise SolverPolicyValidationError(f"{field} must be a time Quantity")
    try:
        si_value = value.to_si()
    except ValueError as error:
        raise SolverPolicyValidationError(f"{field} is not SI-representable: {error}") from error
    if positive and si_value.value <= 0:
        raise SolverPolicyValidationError(f"{field} must be strictly positive")
    if not positive and si_value.value < 0:
        raise SolverPolicyValidationError(f"{field} must be nonnegative")
    return value


def _quantity_dict(value: Quantity) -> dict[str, float | str]:
    si_value = value.to_si()
    return {"value": float(si_value.value), "unit": si_value.unit}


def _strict_integer(value: object, field: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SolverPolicyValidationError(f"{field} must be an integer")
    if value < minimum:
        raise SolverPolicyValidationError(f"{field} must be at least {minimum}")
    return value


def _copy_sequence(value: object, field: str) -> tuple[object, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise SolverPolicyValidationError(f"{field} must be a sequence")
    return tuple(value)


def _control_value_projection(value: object) -> dict[str, object]:
    if isinstance(value, Quantity):
        return {"kind": "quantity", "value": _quantity_dict(value)}
    if isinstance(value, bool):
        return {"kind": "boolean", "value": value}
    if isinstance(value, int):
        return {"kind": "integer", "value": value}
    raise SolverPolicyValidationError("value must be a Quantity, integer, or boolean")


@dataclass(frozen=True, slots=True)
class SolverControl:
    """One named numerical setting supplied by a registered solver profile."""

    name: str
    value: Quantity | int | bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _require_identifier(self.name, "name"))
        if not isinstance(self.value, (Quantity, int, bool)):
            raise SolverPolicyValidationError("value must be a Quantity, integer, or boolean")
        try:
            self.to_bytes()
        except (TypeError, ValueError) as error:
            raise SolverPolicyValidationError(
                "solver control projection is not canonically serializable"
            ) from error

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "name": self.name,
            "value": _control_value_projection(self.value),
        }

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


@dataclass(frozen=True, slots=True)
class TimeIncrementPolicy:
    """Explicit numerical subdivision bounds over an existing motion timeline."""

    initial_step: Quantity
    minimum_step: Quantity
    maximum_step: Quantity
    adaptive: bool
    max_steps: int
    max_step_retries: int
    must_points: Sequence[Quantity]

    def __post_init__(self) -> None:
        initial_step = _require_time_quantity(self.initial_step, "initial_step", positive=True)
        minimum_step = _require_time_quantity(self.minimum_step, "minimum_step", positive=True)
        maximum_step = _require_time_quantity(self.maximum_step, "maximum_step", positive=True)
        if minimum_step.to_si().value > initial_step.to_si().value:
            raise SolverPolicyValidationError("minimum_step must not exceed initial_step")
        if initial_step.to_si().value > maximum_step.to_si().value:
            raise SolverPolicyValidationError("initial_step must not exceed maximum_step")
        object.__setattr__(self, "initial_step", initial_step)
        object.__setattr__(self, "minimum_step", minimum_step)
        object.__setattr__(self, "maximum_step", maximum_step)

        if type(self.adaptive) is not bool:
            raise SolverPolicyValidationError("adaptive must be a boolean")
        max_steps = _strict_integer(self.max_steps, "max_steps", 1)
        max_step_retries = _strict_integer(self.max_step_retries, "max_step_retries", 0)
        object.__setattr__(self, "max_steps", max_steps)
        object.__setattr__(self, "max_step_retries", max_step_retries)

        raw_points = _copy_sequence(self.must_points, "must_points")
        points: list[Quantity] = []
        previous: float | None = None
        for index, point in enumerate(raw_points):
            field = f"must_points[{index}]"
            current = _require_time_quantity(point, field, positive=False)
            current_si = float(current.to_si().value)
            if previous is not None and current_si <= previous:
                raise SolverPolicyValidationError(
                    "must_points must be strictly increasing with no duplicates"
                )
            points.append(current)
            previous = current_si
        object.__setattr__(self, "must_points", tuple(points))

        try:
            self.to_bytes()
        except (TypeError, ValueError) as error:
            raise SolverPolicyValidationError(
                "time increment policy projection is not canonically serializable"
            ) from error

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "initial_step": _quantity_dict(self.initial_step),
            "minimum_step": _quantity_dict(self.minimum_step),
            "maximum_step": _quantity_dict(self.maximum_step),
            "adaptive": self.adaptive,
            "max_steps": self.max_steps,
            "max_step_retries": self.max_step_retries,
            "must_points": [_quantity_dict(point) for point in self.must_points],
        }

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


@dataclass(frozen=True, slots=True)
class SolverPolicy:
    """Selected numerical intent bounded by one immutable solver profile reference."""

    profile: NumericalProfileRef
    controls: Sequence[SolverControl]
    increments: TimeIncrementPolicy
    retry_recipe_ids: Sequence[str]

    def __post_init__(self) -> None:
        if not isinstance(self.profile, NumericalProfileRef):
            raise SolverPolicyValidationError("profile must be a NumericalProfileRef")
        if self.profile.purpose != "solver":
            raise SolverPolicyValidationError("profile must have solver purpose")

        raw_controls = _copy_sequence(self.controls, "controls")
        if not raw_controls:
            raise SolverPolicyValidationError("controls must be nonempty")
        if any(not isinstance(control, SolverControl) for control in raw_controls):
            raise SolverPolicyValidationError("controls contains an invalid SolverControl")
        controls = cast(tuple[SolverControl, ...], tuple(raw_controls))
        names = [control.name for control in controls]
        if len(set(names)) != len(names):
            duplicates = sorted(name for name in set(names) if names.count(name) > 1)
            raise SolverPolicyValidationError(f"duplicate control name: {duplicates[0]}")
        object.__setattr__(self, "controls", tuple(sorted(controls, key=lambda item: item.name)))

        if not isinstance(self.increments, TimeIncrementPolicy):
            raise SolverPolicyValidationError("increments must be a TimeIncrementPolicy")

        raw_retry_ids = _copy_sequence(self.retry_recipe_ids, "retry_recipe_ids")
        retry_ids = tuple(
            _require_identifier(value, f"retry_recipe_ids[{index}]")
            for index, value in enumerate(raw_retry_ids)
        )
        object.__setattr__(self, "retry_recipe_ids", retry_ids)

        try:
            self.to_bytes()
        except (TypeError, ValueError) as error:
            raise SolverPolicyValidationError(
                "solver policy projection is not canonically serializable"
            ) from error

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "profile": self.profile.to_dict(),
            "controls": [control.to_dict() for control in self.controls],
            "increments": self.increments.to_dict(),
            "retry_recipe_ids": list(self.retry_recipe_ids),
        }

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


__all__ = [
    "SCHEMA_VERSION",
    "SolverControl",
    "SolverPolicy",
    "SolverPolicyValidationError",
    "TimeIncrementPolicy",
]
