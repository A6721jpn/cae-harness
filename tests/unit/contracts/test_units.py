from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any

import pytest


def _optional_module(name: str) -> ModuleType | None:
    try:
        return importlib.import_module(name)
    except (ImportError, ModuleNotFoundError):
        return None


UNITS_MODULE = _optional_module("febio_cae.domain.units")


def _quantity_type() -> Any:
    if UNITS_MODULE is None:
        pytest.skip("Quantity API availability is covered by the dedicated assertion")
    quantity = getattr(UNITS_MODULE, "Quantity", None)
    if quantity is None:
        pytest.skip("Quantity API availability is covered by the dedicated assertion")
    return quantity


def test_units_api_is_available() -> None:
    assert UNITS_MODULE is not None, "P1-A units module is not available"
    assert getattr(UNITS_MODULE, "Quantity", None) is not None
    assert getattr(UNITS_MODULE, "SUPPORTED_UNITS", None) is not None


def test_quantity_converts_supported_length_and_pressure_units() -> None:
    Quantity = _quantity_type()

    assert Quantity(1000, "mm").to_si() == Quantity(1.0, "m")
    assert Quantity(2, "MPa").convert_to("Pa") == Quantity(2_000_000.0, "Pa")
    assert Quantity(3, "N").to_si() == Quantity(3, "N")


def test_supported_registry_retains_derived_dimensions() -> None:
    _quantity_type()
    assert UNITS_MODULE is not None
    definitions = UNITS_MODULE.SUPPORTED_UNITS

    assert {"m", "mm", "m2", "mm2", "m3", "mm3", "s", "ms", "N", "Pa", "MPa", "1"} <= set(
        definitions
    )
    assert definitions["mm2"].dimension.length == 2
    assert definitions["mm3"].dimension.length == 3
    assert definitions["1"].dimension.is_dimensionless


@pytest.mark.parametrize("bad_value", [True, float("nan"), float("inf"), -float("inf")])
def test_quantity_rejects_bool_and_non_finite_values(bad_value: object) -> None:
    Quantity = _quantity_type()

    with pytest.raises(ValueError):
        Quantity(bad_value, "mm")


def test_quantity_allows_signed_values_and_distinguishes_absent_from_zero() -> None:
    Quantity = _quantity_type()
    negative = Quantity(-2, "mm")
    zero = Quantity(0, "mm")

    assert negative.value == -2
    assert zero.value == 0
    assert None != zero


def test_quantity_rejects_unknown_units_and_dimension_mismatch() -> None:
    Quantity = _quantity_type()

    with pytest.raises(ValueError):
        Quantity(1, "inch")
    with pytest.raises(ValueError):
        Quantity(1, "mm").convert_to("MPa")
