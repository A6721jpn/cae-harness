"""Small helpers shared by the immutable model records.

The model package deliberately keeps values evidence-shaped.  Lists and
mappings supplied by callers are copied into tuples and read-only mappings so
an inspection or plan cannot be changed through an alias held by the caller.
"""

from __future__ import annotations

from collections.abc import Mapping
from math import isfinite
from types import MappingProxyType
from typing import Any, cast

type JSONScalar = None | bool | int | float | str
type FrozenJSON = JSONScalar | tuple[FrozenJSON, ...] | Mapping[str, FrozenJSON]


def freeze_json(value: Any) -> FrozenJSON:
    """Recursively copy JSON-compatible data into immutable containers."""

    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("JSON values must contain only finite floats")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, FrozenJSON] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("mapping keys must be strings")
            frozen[key] = freeze_json(item)
        return cast(Mapping[str, FrozenJSON], MappingProxyType(frozen))
    if isinstance(value, (list, tuple)):
        return tuple(freeze_json(item) for item in value)
    raise TypeError("value must be JSON-compatible")


def freeze_value(value: Any) -> Any:
    """Freeze common mutable containers while preserving typed descriptors."""

    if isinstance(value, (Mapping, list, tuple)):
        return freeze_json(value)
    return value


def thaw_json(value: object) -> object:
    """Return a detached JSON-compatible projection for serialization."""

    if isinstance(value, Mapping):
        return {key: thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_json(item) for item in value]
    return value


def frozen_mapping(value: Mapping[str, Any] | None = None) -> Mapping[str, FrozenJSON]:
    """Normalize an optional mapping to a read-only mapping."""

    if value is None:
        value = {}
    frozen = freeze_json(value)
    if not isinstance(frozen, Mapping):  # pragma: no cover - guarded by input type
        raise TypeError("expected a mapping")
    return frozen


def frozen_strings(value: tuple[str, ...] | list[str] | None = None) -> tuple[str, ...]:
    """Normalize a sequence of labels and reject non-string labels."""

    if value is None:
        return ()
    result = tuple(value)
    if not all(isinstance(item, str) for item in result):
        raise TypeError("labels must be strings")
    return result


def frozen_provenance[T](
    value: T | tuple[T, ...] | list[T] | None,
) -> tuple[T, ...]:
    """Normalize a single record or sequence to a tuple."""

    if value is None:
        return ()
    if isinstance(value, tuple | list):
        return tuple(value)
    return (value,)
