"""Pre-existing alias protection, with no concurrent or hostile mid-write actor."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from test_persistence_authority import _created

from febio_cae.storage._ownership import lease
from febio_cae.storage.registry import StorageIntegrityError


@pytest.mark.parametrize("initial", [b"", b"external-content"])
def test_linked_lock_is_not_initialized_or_appended(tmp_path: Path, initial: bytes) -> None:
    root = tmp_path / "owned"
    root.mkdir()
    sibling = tmp_path / "outside.bin"
    sibling.write_bytes(initial)
    os.link(sibling, root / ".publication.lock")
    with pytest.raises(OSError, match="hard link"):
        with lease(root):
            pytest.fail("linked publication lock was acquired")
    assert sibling.read_bytes() == initial


def test_current_draft_preserves_preexisting_lock_sibling(tmp_path: Path) -> None:
    _, created, storage = _created(tmp_path)
    lock = storage.root / ".publication.lock"
    sibling = tmp_path / "lock-alias.bin"
    os.link(lock, sibling)
    before = sibling.read_bytes()
    with pytest.raises(StorageIntegrityError, match="hard link"):
        storage.current_draft(created.case_id)
    assert sibling.read_bytes() == before


def test_normal_lease_remains_reentrant_and_reacquirable(tmp_path: Path) -> None:
    root = tmp_path / "owned"
    root.mkdir()
    with lease(root) as first:
        assert first
        with lease(root) as second:
            assert second
    with lease(root) as next_owner:
        assert next_owner
