from __future__ import annotations

import importlib
import math
from types import ModuleType
from typing import Any

import pytest


def _optional_module(name: str) -> ModuleType | None:
    try:
        return importlib.import_module(name)
    except (ImportError, ModuleNotFoundError):
        return None


CANONICAL_MODULE = _optional_module("febio_cae.domain.canonical")


def _canonical_bytes() -> Any:
    if CANONICAL_MODULE is None:
        pytest.skip("canonical API availability is covered by the dedicated assertion")
    function = getattr(CANONICAL_MODULE, "canonical_bytes", None)
    if function is None:
        pytest.skip("canonical_bytes API availability is covered by the dedicated assertion")
    return function


def test_canonical_api_is_available() -> None:
    assert CANONICAL_MODULE is not None, "P1-A canonical module is not available"
    assert getattr(CANONICAL_MODULE, "canonical_bytes", None) is not None


def test_canonical_bytes_stabilises_keys_and_only_declared_sets() -> None:
    canonical_bytes = _canonical_bytes()
    first = {
        "schema_version": "1",
        "members": [{"id": "B", "value": 2.0}, {"id": "A", "value": 1}],
        "history": [2, 1],
    }
    second = {
        "history": [2, 1],
        "members": [{"value": 1, "id": "A"}, {"value": 2.0, "id": "B"}],
        "schema_version": "1",
    }

    first_bytes = canonical_bytes(
        first,
        unordered_paths=(("members",),),
        unique_id_paths=(("members",),),
    )
    second_bytes = canonical_bytes(
        second,
        unordered_paths=(("members",),),
        unique_id_paths=(("members",),),
    )

    assert first_bytes == second_bytes
    assert first_bytes == (
        b'{"history":[2,1],"members":[{"id":"A","value":1},'
        b'{"id":"B","value":2.0}],"schema_version":"1"}'
    )
    assert canonical_bytes({"history": [1, 2]}) != canonical_bytes({"history": [2, 1]})


def test_canonical_bytes_has_explicit_numeric_and_metadata_representation() -> None:
    canonical_bytes = _canonical_bytes()

    assert canonical_bytes({"zero": -0.0}) == b'{"zero":0.0}'
    assert canonical_bytes({"zero": 0}) == b'{"zero":0}'
    assert canonical_bytes({"value": 1}) != canonical_bytes({"value": 1.0})
    encoded = canonical_bytes({"timestamp": "keep", "hash": "keep", "enabled": True, "value": 1.0})
    assert encoded == b'{"enabled":true,"hash":"keep","timestamp":"keep","value":1.0}'


@pytest.mark.parametrize("bad_value", [math.nan, math.inf, -math.inf])
def test_canonical_bytes_rejects_non_finite_numbers(bad_value: float) -> None:
    canonical_bytes = _canonical_bytes()

    with pytest.raises(ValueError):
        canonical_bytes({"value": bad_value})


def test_canonical_bytes_rejects_duplicate_ids_only_when_collection_is_declared() -> None:
    canonical_bytes = _canonical_bytes()
    duplicate_items = {"items": [{"id": "A"}, {"id": "A"}]}

    with pytest.raises(ValueError):
        canonical_bytes(duplicate_items, unique_id_paths=(("items",),))
    assert canonical_bytes({"history": duplicate_items["items"]})


@pytest.mark.parametrize("control", ["unordered_paths", "unique_id_paths"])
@pytest.mark.parametrize(
    "value",
    [
        {},
        {"members": None},
        {"members": 0},
        {"members": {"first": {"id": "duplicate"}, "second": {"id": "duplicate"}}},
    ],
)
def test_declared_collection_paths_require_list_targets(control: str, value: object) -> None:
    canonical_bytes = _canonical_bytes()

    with pytest.raises(ValueError):
        canonical_bytes(value, **{control: (("members",),)})


def test_declared_collections_support_root_empty_and_nested_lists() -> None:
    canonical_bytes = _canonical_bytes()

    assert canonical_bytes([], unordered_paths=((),), unique_id_paths=((),)) == b"[]"
    assert (
        canonical_bytes(
            {"outer": {"members": []}},
            unordered_paths=(("outer", "members"),),
            unique_id_paths=(("outer", "members"),),
        )
        == b'{"outer":{"members":[]}}'
    )

    nested = {"groups": [{"members": [{"id": "B"}, {"id": "A"}]}]}
    assert (
        canonical_bytes(
            nested,
            unordered_paths=(("groups", "0", "members"),),
            unique_id_paths=(("groups", "0", "members"),),
        )
        == b'{"groups":[{"members":[{"id":"A"},{"id":"B"}]}]}'
    )


def test_declared_unique_collection_does_not_reorder_an_ordered_list() -> None:
    canonical_bytes = _canonical_bytes()

    encoded = canonical_bytes(
        {"history": [{"id": "B"}, {"id": "A"}]},
        unique_id_paths=(("history",),),
    )

    assert encoded == b'{"history":[{"id":"B"},{"id":"A"}]}'
