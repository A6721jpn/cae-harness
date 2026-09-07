from __future__ import annotations

import importlib
from dataclasses import FrozenInstanceError
from types import ModuleType
from typing import Any

import pytest

from febio_cae.domain import Quantity, canonical_bytes


def _optional_module(name: str) -> ModuleType | None:
    try:
        return importlib.import_module(name)
    except (ImportError, ModuleNotFoundError):
        return None


BUDGET_MODULE = _optional_module("febio_cae.domain.budget")
_MISSING = object()


def _budget() -> ModuleType:
    if BUDGET_MODULE is None:
        pytest.skip("budget API availability is covered by the dedicated assertion")
    return BUDGET_MODULE


def _value(
    budget: ModuleType,
    *,
    max_elapsed: object = _MISSING,
    max_attempts: object = 3,
    cpu_workers: object = 2,
    max_llm_calls: object = 0,
    max_llm_tokens: object = 0,
) -> Any:
    return budget.Budget(
        max_elapsed=Quantity(2, "s") if max_elapsed is _MISSING else max_elapsed,
        max_attempts=max_attempts,
        cpu_workers=cpu_workers,
        max_llm_calls=max_llm_calls,
        max_llm_tokens=max_llm_tokens,
    )


def test_budget_api_is_available() -> None:
    assert BUDGET_MODULE is not None, "P1-B6 budget module is not available"
    for name in ("SCHEMA_VERSION", "Budget", "BudgetValidationError"):
        assert getattr(BUDGET_MODULE, name, None) is not None, name


def test_budget_preserves_all_required_fields_and_canonical_projection() -> None:
    budget = _budget()
    value = _value(
        budget,
        max_elapsed=Quantity(2000, "ms"),
        max_attempts=4,
        cpu_workers=3,
        max_llm_calls=5,
        max_llm_tokens=700,
    )
    payload = value.to_dict()

    assert payload == {
        "schema_version": "1",
        "max_elapsed": {"value": 2.0, "unit": "s"},
        "max_attempts": 4,
        "cpu_workers": 3,
        "max_llm_calls": 5,
        "max_llm_tokens": 700,
    }
    assert value.to_bytes() == canonical_bytes(payload)


def test_budget_requires_every_field_and_rejects_unknown_fields() -> None:
    budget = _budget()
    values = {
        "max_elapsed": Quantity(1, "s"),
        "max_attempts": 1,
        "cpu_workers": 1,
        "max_llm_calls": 0,
        "max_llm_tokens": 0,
    }
    for missing in tuple(values):
        incomplete = values.copy()
        incomplete.pop(missing)
        with pytest.raises(TypeError):
            budget.Budget(**incomplete)
    with pytest.raises(TypeError):
        budget.Budget(**values, unexpected=True)


@pytest.mark.parametrize(
    "bad_elapsed",
    [
        Quantity(0, "s"),
        Quantity(-1, "s"),
        Quantity(1, "mm"),
        Quantity(1, "m"),
        "1 s",
        None,
    ],
    ids=["zero", "negative", "length-mm", "length-m", "string", "none"],
)
def test_budget_requires_strictly_positive_finite_time_quantity(bad_elapsed: object) -> None:
    budget = _budget()
    with pytest.raises(ValueError):
        _value(budget, max_elapsed=bad_elapsed)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf")])
def test_budget_rejects_nonfinite_time_quantity(bad_value: float) -> None:
    budget = _budget()
    with pytest.raises(ValueError):
        _value(budget, max_elapsed=Quantity(bad_value, "s"))


@pytest.mark.parametrize("field", ["max_attempts", "cpu_workers"])
@pytest.mark.parametrize("bad_value", [0, -1, -10])
def test_budget_requires_positive_attempt_and_cpu_counts(field: str, bad_value: int) -> None:
    budget = _budget()
    with pytest.raises(ValueError):
        _value(budget, **{field: bad_value})


@pytest.mark.parametrize("field", ["max_llm_calls", "max_llm_tokens"])
@pytest.mark.parametrize("bad_value", [-1, -10])
def test_budget_rejects_negative_llm_allocations(field: str, bad_value: int) -> None:
    budget = _budget()
    with pytest.raises(ValueError):
        _value(budget, **{field: bad_value})


@pytest.mark.parametrize(
    "field", ["max_attempts", "cpu_workers", "max_llm_calls", "max_llm_tokens"]
)
@pytest.mark.parametrize("bad_value", [True, False, 1.0, 1.5, "1", None])
def test_budget_counts_are_strict_integers_and_reject_bool(field: str, bad_value: object) -> None:
    budget = _budget()
    with pytest.raises((TypeError, ValueError)):
        _value(budget, **{field: bad_value})


def test_budget_explicit_zero_llm_limits_are_retained_and_distinct() -> None:
    budget = _budget()
    zero = _value(budget, max_llm_calls=0, max_llm_tokens=0)
    positive = _value(budget, max_llm_calls=1, max_llm_tokens=1)

    assert zero.to_dict()["max_llm_calls"] == 0
    assert zero.to_dict()["max_llm_tokens"] == 0
    assert zero.to_bytes() != positive.to_bytes()


def test_budget_equivalent_time_units_have_identical_canonical_bytes() -> None:
    budget = _budget()
    seconds = _value(budget, max_elapsed=Quantity(1, "s"))
    milliseconds = _value(budget, max_elapsed=Quantity(1000, "ms"))

    assert seconds.to_bytes() == milliseconds.to_bytes()


def test_budget_is_immutable_and_projection_mutation_does_not_change_value() -> None:
    budget = _budget()
    value = _value(budget, max_llm_calls=4)
    payload = value.to_dict()
    payload["max_llm_calls"] = 99

    with pytest.raises(FrozenInstanceError):
        value.max_attempts = 99  # type: ignore[misc]
    assert value.max_llm_calls == 4
    assert value.to_dict()["max_llm_calls"] == 4


@pytest.mark.parametrize(
    "bad_elapsed",
    [Quantity(10**400, "s"), Quantity(5e-324, "ms")],
    ids=["overflow", "underflow"],
)
def test_budget_rejects_unrepresentable_si_time_at_construction(bad_elapsed: Quantity) -> None:
    budget = _budget()
    with pytest.raises(ValueError, match="outside finite range|underflows"):
        _value(budget, max_elapsed=bad_elapsed)


def test_budget_accepts_large_but_serializable_integer_identity_without_float_rounding() -> None:
    budget = _budget()
    large = 10**1000
    value = _value(budget, max_attempts=large, max_llm_tokens=large)

    assert value.max_attempts == large
    assert value.max_llm_tokens == large
    assert value.to_dict()["max_attempts"] == large
    assert value.to_dict()["max_llm_tokens"] == large
    assert (
        value.to_bytes()
        == budget.Budget(
            max_elapsed=Quantity(2, "s"),
            max_attempts=large,
            cpu_workers=2,
            max_llm_calls=0,
            max_llm_tokens=large,
        ).to_bytes()
    )


def test_budget_rejects_integer_beyond_shared_json_serialization_range_at_construction() -> None:
    budget = _budget()
    with pytest.raises(ValueError, match="serial|digit|canonical"):
        _value(budget, max_attempts=10**5000)
