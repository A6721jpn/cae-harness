"""A stable compatibility profile ID cannot change under existing consumers."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier

import pytest

from febio_cae.domain import CompatibilityProfile, PortError, PortErrorCategory
from febio_cae.storage.profiles import SQLiteCompatibilityRegistry

from .fixtures import make_profile


def test_registered_profile_is_idempotent_but_cannot_be_replaced(tmp_path: Path) -> None:
    registry = SQLiteCompatibilityRegistry(tmp_path / "profiles.sqlite3")
    original = make_profile()
    registry.register(original)
    registry.register(original)
    changed = replace(original, reader=replace(original.reader, version="different-reader"))
    with pytest.raises(PortError) as rejected:
        registry.register(changed)
    assert rejected.value.category is PortErrorCategory.CONFLICT
    assert registry.get_profile(original.profile_id) == original


def test_competing_profile_registration_has_one_immutable_winner(tmp_path: Path) -> None:
    path = tmp_path / "profiles.sqlite3"
    registry = SQLiteCompatibilityRegistry(path)
    original = make_profile()
    candidates = (
        original,
        replace(original, reader=replace(original.reader, version="different-reader")),
    )
    barrier = Barrier(2)

    def register(profile: CompatibilityProfile) -> tuple[str, CompatibilityProfile]:
        local = SQLiteCompatibilityRegistry(path)
        barrier.wait(timeout=10)
        try:
            local.register(profile)
        except PortError as error:
            assert error.category is PortErrorCategory.CONFLICT
            return "conflict", profile
        return "stored", profile

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(register, candidates))
    winners = [profile for status, profile in outcomes if status == "stored"]
    assert len(winners) == 1
    assert sum(status == "conflict" for status, _ in outcomes) == 1
    assert registry.get_profile(original.profile_id) == winners[0]
