"""Finite typed quantities and the explicit SI/display unit registry."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

type Numeric = int | float


class UnitError(ValueError):
    """Base error for strict quantity and unit validation."""


class UnknownUnitError(UnitError):
    """Raised when a unit is not in the supported registry."""


class DimensionMismatchError(UnitError):
    """Raised when quantities with different physical dimensions are converted."""


@dataclass(frozen=True, slots=True)
class Dimension:
    length: int = 0
    mass: int = 0
    time: int = 0

    @property
    def is_dimensionless(self) -> bool:
        return self == Dimension()


@dataclass(frozen=True, slots=True)
class UnitDefinition:
    symbol: str
    dimension: Dimension
    scale_to_si: float
    si_symbol: str


_LENGTH = Dimension(length=1)
_AREA = Dimension(length=2)
_VOLUME = Dimension(length=3)
_TIME = Dimension(time=1)
_FORCE = Dimension(length=1, mass=1, time=-2)
_PRESSURE = Dimension(length=-1, mass=1, time=-2)

_UNIT_DEFINITIONS: dict[str, UnitDefinition] = {
    "1": UnitDefinition("1", Dimension(), 1.0, "1"),
    "m": UnitDefinition("m", _LENGTH, 1.0, "m"),
    "mm": UnitDefinition("mm", _LENGTH, 1.0e-3, "m"),
    "m2": UnitDefinition("m2", _AREA, 1.0, "m2"),
    "mm2": UnitDefinition("mm2", _AREA, 1.0e-6, "m2"),
    "m3": UnitDefinition("m3", _VOLUME, 1.0, "m3"),
    "mm3": UnitDefinition("mm3", _VOLUME, 1.0e-9, "m3"),
    "s": UnitDefinition("s", _TIME, 1.0, "s"),
    "ms": UnitDefinition("ms", _TIME, 1.0e-3, "s"),
    "N": UnitDefinition("N", _FORCE, 1.0, "N"),
    "Pa": UnitDefinition("Pa", _PRESSURE, 1.0, "Pa"),
    "MPa": UnitDefinition("MPa", _PRESSURE, 1.0e6, "Pa"),
}
SUPPORTED_UNITS: Mapping[str, UnitDefinition] = MappingProxyType(_UNIT_DEFINITIONS)


def unit_definition(symbol: str) -> UnitDefinition:
    if not isinstance(symbol, str) or symbol not in SUPPORTED_UNITS:
        raise UnknownUnitError(f"unsupported unit: {symbol!r}")
    return SUPPORTED_UNITS[symbol]


def _validate_value(value: object) -> Numeric:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UnitError("quantity values must be int or float, not bool or another type")
    if isinstance(value, float) and not math.isfinite(value):
        raise UnitError("quantity values must be finite")
    return value


@dataclass(frozen=True, slots=True)
class Quantity:
    value: Numeric
    unit: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _validate_value(self.value))
        unit_definition(self.unit)

    @property
    def dimension(self) -> Dimension:
        return unit_definition(self.unit).dimension

    def to_si(self) -> Quantity:
        definition = unit_definition(self.unit)
        if definition.scale_to_si == 1.0:
            value = self.value
        else:
            value = self.value * definition.scale_to_si
        return Quantity(value, definition.si_symbol)

    def convert_to(self, target_unit: str) -> Quantity:
        source = unit_definition(self.unit)
        target = unit_definition(target_unit)
        if source.dimension != target.dimension:
            raise DimensionMismatchError(
                f"cannot convert {source.symbol} ({source.dimension}) to "
                f"{target.symbol} ({target.dimension})"
            )
        if source.scale_to_si == target.scale_to_si:
            value = self.value
        else:
            value = self.value * source.scale_to_si / target.scale_to_si
        return Quantity(value, target.symbol)


__all__ = [
    "SUPPORTED_UNITS",
    "Dimension",
    "DimensionMismatchError",
    "Quantity",
    "UnitDefinition",
    "UnitError",
    "UnknownUnitError",
    "unit_definition",
]
