"""Deterministic, bounded canonical JSON serialization for domain values."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping, Sequence

SCHEMA_VERSION = "1"

type JsonScalar = None | bool | int | float | str
type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
type Path = tuple[str, ...]


class CanonicalizationError(ValueError):
    """Raised when a value cannot satisfy the canonical JSON contract."""


def canonical_bytes(
    value: object,
    *,
    unordered_paths: Iterable[Sequence[str]] = (),
    unique_id_paths: Iterable[Sequence[str]] = (),
) -> bytes:
    """Serialize a JSON-like value with stable UTF-8 bytes.

    Dict keys are always sorted. Lists retain their order unless their exact
    object path is explicitly named in ``unordered_paths``. A path named in
    ``unique_id_paths`` must contain dict items with distinct non-empty ``id``
    strings. These controls keep semantic set handling explicit instead of
    silently sorting every array.
    """

    unordered = _normalise_paths(unordered_paths, "unordered_paths")
    unique_ids = _normalise_paths(unique_id_paths, "unique_id_paths")
    _validate_declared_collection_paths(value, unordered | unique_ids)
    normalised = _normalise(value, (), unordered, unique_ids)
    return _encode(normalised)


def _normalise_paths(paths: Iterable[Sequence[str]], name: str) -> frozenset[Path]:
    result: set[Path] = set()
    for path in paths:
        if isinstance(path, str):
            raise CanonicalizationError(f"{name} entries must be path sequences, not strings")
        normalised = tuple(path)
        if any(not isinstance(part, str) or not part for part in normalised):
            raise CanonicalizationError(f"{name} contains an invalid path")
        result.add(normalised)
    return frozenset(result)


def _normalise(
    value: object,
    path: Path,
    unordered_paths: frozenset[Path],
    unique_id_paths: frozenset[Path],
) -> JsonValue:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalizationError("NaN and infinity are not canonical numeric values")
        return 0.0 if value == 0.0 else value
    if isinstance(value, Mapping):
        normalised_mapping: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalizationError("canonical object keys must be strings")
            normalised_mapping[key] = _normalise(
                item, path + (key,), unordered_paths, unique_id_paths
            )
        return normalised_mapping
    if isinstance(value, list):
        normalised_items = [
            _normalise(item, path + (str(index),), unordered_paths, unique_id_paths)
            for index, item in enumerate(value)
        ]
        if path in unique_id_paths:
            _validate_unique_ids(normalised_items)
        if path in unordered_paths:
            normalised_items.sort(key=_encode)
        return normalised_items
    raise CanonicalizationError(f"unsupported canonical value type: {type(value).__name__}")


def _validate_declared_collection_paths(value: object, paths: frozenset[Path]) -> None:
    for path in paths:
        target = value
        for part in path:
            if isinstance(target, Mapping):
                if part not in target:
                    raise CanonicalizationError(
                        f"declared collection path does not resolve: {path!r}"
                    )
                target = target[part]
                continue
            if isinstance(target, list) and part.isascii() and part.isdecimal():
                index = int(part)
                if str(index) != part or index >= len(target):
                    raise CanonicalizationError(
                        f"declared collection path does not resolve: {path!r}"
                    )
                target = target[index]
                continue
            raise CanonicalizationError(f"declared collection path does not resolve: {path!r}")
        if not isinstance(target, list):
            raise CanonicalizationError(
                f"declared collection path must resolve to a list: {path!r}"
            )


def _validate_unique_ids(items: list[JsonValue]) -> None:
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise CanonicalizationError(
                "declared unique-id collections require non-empty string ids"
            )
        identifier = item.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise CanonicalizationError(
                "declared unique-id collections require non-empty string ids"
            )
        if identifier in seen:
            raise CanonicalizationError(f"duplicate id in declared collection: {identifier!r}")
        seen.add(identifier)


def _encode(value: JsonValue) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as error:
        raise CanonicalizationError("value is not JSON serializable") from error
    return encoded.encode("utf-8")


__all__ = ["SCHEMA_VERSION", "CanonicalizationError", "JsonValue", "canonical_bytes"]
