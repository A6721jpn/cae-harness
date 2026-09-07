"""Immutable computational limits for one case operation."""

from __future__ import annotations

from dataclasses import dataclass

from .canonical import canonical_bytes
from .units import Dimension, Quantity

SCHEMA_VERSION = "1"
_TIME = Dimension(time=1)


class BudgetValidationError(ValueError):
    """Raised when a computational budget is structurally or numerically invalid."""


def _require_count(value: object, field: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BudgetValidationError(f"{field} must be an integer")
    if value < minimum:
        raise BudgetValidationError(f"{field} must be at least {minimum}")
    return value


def _quantity_dict(quantity: Quantity) -> dict[str, float | str]:
    value = quantity.to_si()
    return {"value": float(value.value), "unit": value.unit}


@dataclass(frozen=True, slots=True)
class Budget:
    """Explicit computational limits without accounting or enforcement semantics.

    ``max_elapsed`` bounds the enclosing operation rather than resetting for an
    attempt.  LLM call and token values are aggregate allocations for the case
    operation and its retries; zero explicitly disables that allocation.
    """

    max_elapsed: Quantity
    max_attempts: int
    cpu_workers: int
    max_llm_calls: int
    max_llm_tokens: int

    def __post_init__(self) -> None:
        if not isinstance(self.max_elapsed, Quantity):
            raise BudgetValidationError("max_elapsed must be a Quantity")
        if self.max_elapsed.dimension != _TIME:
            raise BudgetValidationError("max_elapsed must be a time Quantity")
        try:
            elapsed_si = self.max_elapsed.to_si()
        except ValueError as error:
            raise BudgetValidationError(str(error)) from error
        if elapsed_si.value <= 0:
            raise BudgetValidationError("max_elapsed must be strictly positive")

        _require_count(self.max_attempts, "max_attempts", 1)
        _require_count(self.cpu_workers, "cpu_workers", 1)
        _require_count(self.max_llm_calls, "max_llm_calls", 0)
        _require_count(self.max_llm_tokens, "max_llm_tokens", 0)

        # Validate the complete parent projection while the object is created.
        # This rejects unsupported integer sizes at the shared JSON boundary
        # instead of changing interpreter limits or rounding counts.
        try:
            self.to_bytes()
        except (TypeError, ValueError) as error:
            raise BudgetValidationError(
                "budget projection is not canonically serializable"
            ) from error

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "max_elapsed": _quantity_dict(self.max_elapsed),
            "max_attempts": self.max_attempts,
            "cpu_workers": self.cpu_workers,
            "max_llm_calls": self.max_llm_calls,
            "max_llm_tokens": self.max_llm_tokens,
        }

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


__all__ = ["SCHEMA_VERSION", "Budget", "BudgetValidationError"]
