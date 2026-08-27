"""Immutable contracts shared by the CAE harness phases.

The intent values deliberately remain evidence-shaped JSON data.  This module
stores the values and their lifecycle state; it does not infer physical
conditions or implement solver/model behavior.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from types import MappingProxyType
from typing import Any, Self

__all__ = [
    "AnalysisIntent",
    "ExecutionBudget",
    "Intent",
    "IntentContract",
    "IntentState",
    "JSONValue",
]


type JSONValue = None | bool | int | float | str | tuple[JSONValue, ...] | Mapping[str, JSONValue]
type JSONInput = None | bool | int | float | str | list[Any] | tuple[Any, ...] | Mapping[str, Any]


class IntentState(StrEnum):
    """Lifecycle states allowed for an analysis intent."""

    GATHERING = "GATHERING"
    BOUND = "BOUND"
    ASK_AND_BLOCK = "ASK_AND_BLOCK"


def _validate_nonnegative_limit(name: str, value: int | float | None) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a non-negative number or None")
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    if isinstance(value, float) and not isfinite(value):
        raise ValueError(f"{name} must be finite")


@dataclass(frozen=True, slots=True)
class ExecutionBudget:
    """Optional execution limits recorded as part of an analysis intent."""

    retry_budget: int | None = None
    time_budget_seconds: float | None = None
    cpu_budget_seconds: float | None = None
    memory_budget_mb: int | None = None

    def __post_init__(self) -> None:
        _validate_nonnegative_limit("retry_budget", self.retry_budget)
        _validate_nonnegative_limit("time_budget_seconds", self.time_budget_seconds)
        _validate_nonnegative_limit("cpu_budget_seconds", self.cpu_budget_seconds)
        _validate_nonnegative_limit("memory_budget_mb", self.memory_budget_mb)


def _freeze(value: Any) -> JSONValue:
    """Return a recursively immutable representation of JSON-compatible data."""

    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, JSONValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("intent mapping keys must be strings")
            frozen[key] = _freeze(item)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    raise TypeError(
        "intent values must contain only JSON-compatible scalars, lists, tuples, and mappings"
    )


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class IntentContract:
    """Immutable, evidence-backed analysis intent.

    Fields use JSON-compatible values so authoritative source records can keep
    their original structure.  Missing values are represented by ``None``;
    this contract never fills them from geometry, naming conventions, or other
    non-authoritative assumptions.
    """

    engineering_question: JSONInput = None
    units: JSONInput = None
    material: JSONInput = None
    loads: JSONInput = ()
    constraints: JSONInput = ()
    contact: JSONInput = None
    analysis_step: JSONInput = None
    roi: JSONInput = ()
    load_path: JSONInput = ()
    evaluation_quantities: JSONInput = ()
    protected_geometry: JSONInput = None
    invariants: JSONInput = ()
    allowed_mesh_changes: JSONInput = ()
    allowed_numerical_changes: JSONInput = ()
    retry_budget: int | None = None
    time_budget_seconds: float | None = None
    cpu_budget_seconds: float | None = None
    memory_budget_mb: int | None = None
    condition_sources: JSONInput = ()
    unresolved: JSONInput = ()
    state: IntentState = IntentState.GATHERING

    def __post_init__(self) -> None:
        try:
            normalized_state = IntentState(self.state)
        except (TypeError, ValueError) as error:
            raise ValueError("state must be one of GATHERING, BOUND, or ASK_AND_BLOCK") from error
        object.__setattr__(self, "state", normalized_state)

        for field_name in _INTENT_VALUE_FIELDS:
            object.__setattr__(self, field_name, _freeze(getattr(self, field_name)))

        ExecutionBudget(
            retry_budget=self.retry_budget,
            time_budget_seconds=self.time_budget_seconds,
            cpu_budget_seconds=self.cpu_budget_seconds,
            memory_budget_mb=self.memory_budget_mb,
        )

    @property
    def question(self) -> JSONInput:
        """Short alias for the canonical ``engineering_question`` field."""

        return self.engineering_question

    @property
    def sources(self) -> JSONInput:
        """Short alias for the canonical ``condition_sources`` field."""

        return self.condition_sources

    @property
    def unresolved_conditions(self) -> JSONInput:
        """Explicit alias for unresolved physical conditions."""

        return self.unresolved

    @property
    def execution_budget(self) -> ExecutionBudget:
        """Return the immutable budget view represented by the budget fields."""

        return ExecutionBudget(
            retry_budget=self.retry_budget,
            time_budget_seconds=self.time_budget_seconds,
            cpu_budget_seconds=self.cpu_budget_seconds,
            memory_budget_mb=self.memory_budget_mb,
        )

    def to_dict(self) -> dict[str, object]:
        """Return a detached JSON-serializable projection of this contract."""

        return {
            "engineering_question": _thaw(self.engineering_question),
            "units": _thaw(self.units),
            "material": _thaw(self.material),
            "loads": _thaw(self.loads),
            "constraints": _thaw(self.constraints),
            "contact": _thaw(self.contact),
            "analysis_step": _thaw(self.analysis_step),
            "roi": _thaw(self.roi),
            "load_path": _thaw(self.load_path),
            "evaluation_quantities": _thaw(self.evaluation_quantities),
            "protected_geometry": _thaw(self.protected_geometry),
            "invariants": _thaw(self.invariants),
            "allowed_mesh_changes": _thaw(self.allowed_mesh_changes),
            "allowed_numerical_changes": _thaw(self.allowed_numerical_changes),
            "retry_budget": self.retry_budget,
            "time_budget_seconds": self.time_budget_seconds,
            "cpu_budget_seconds": self.cpu_budget_seconds,
            "memory_budget_mb": self.memory_budget_mb,
            "condition_sources": _thaw(self.condition_sources),
            "unresolved": _thaw(self.unresolved),
            "state": self.state.value,
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> Self:
        """Create a contract from its JSON-shaped mapping projection."""

        values = dict(payload)
        unknown = set(values) - set(_CONTRACT_FIELD_NAMES)
        if unknown:
            names = ", ".join(sorted(unknown))
            raise TypeError(f"unknown intent field(s): {names}")
        return cls(**values)


_INTENT_VALUE_FIELDS = (
    "engineering_question",
    "units",
    "material",
    "loads",
    "constraints",
    "contact",
    "analysis_step",
    "roi",
    "load_path",
    "evaluation_quantities",
    "protected_geometry",
    "invariants",
    "allowed_mesh_changes",
    "allowed_numerical_changes",
    "condition_sources",
    "unresolved",
)
_CONTRACT_FIELD_NAMES = _INTENT_VALUE_FIELDS + (
    "retry_budget",
    "time_budget_seconds",
    "cpu_budget_seconds",
    "memory_budget_mb",
    "state",
)


# These aliases keep one canonical runtime type while allowing downstream
# phases to use the domain term that best fits their own API.
AnalysisIntent = IntentContract
Intent = IntentContract
