"""Residual actual-content/SQLite identity and repeated editing regressions."""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
from pathlib import Path

import pytest
from test_persistence_authority import _created, _evidence, _populate_complete

from febio_cae.application.service import ServiceConflictError
from febio_cae.domain import Budget, CasePatch, CasePatchEdit, Quantity
from febio_cae.storage.registry import CaseStorage, StorageIntegrityError, _connect


def test_newly_frozen_child_accepts_fresh_patch_not_old_context(tmp_path: Path) -> None:
    service, created, storage = _created(tmp_path)
    _populate_complete(service, created)
    first = service.freeze_case(created.case_id).revision
    assert first is not None
    patch = CasePatch(
        first.revision_id,
        first.spec_digest,
        (CasePatchEdit("budget", Budget(Quantity(30, "s"), 1, 1, 0, 0), True),),
        (_evidence("budget"),),
    )
    current = service.apply_patch(created.case_id, patch)
    second = service.freeze_case(created.case_id).revision
    assert second is not None
    next_patch = CasePatch(
        second.revision_id,
        second.spec_digest,
        (CasePatchEdit("budget", Budget(Quantity(40, "s"), 1, 1, 0, 0), True),),
        (_evidence("budget"),),
    )
    updated = service.apply_patch(
        created.case_id, next_patch, expected_generation=current.generation
    )
    assert updated.values.budget is not None
    assert updated.values.budget.max_elapsed.to_si().value == 40
    with pytest.raises(ServiceConflictError):
        service.apply_patch(created.case_id, patch, expected_generation=updated.generation)
    with pytest.raises(ServiceConflictError):
        service.apply_patch(created.case_id, next_patch, expected_generation=updated.generation)
    assert storage.get_revision(created.case_id, first.revision_id).to_bytes() == first.to_bytes()
    assert storage.get_revision(created.case_id, second.revision_id).to_bytes() == second.to_bytes()


@pytest.mark.parametrize("suffix", ["", "-wal", "-shm"])
def test_sqlite_alias_rejected_before_writable_open(tmp_path: Path, suffix: str) -> None:
    service, created, storage = _created(tmp_path)
    _populate_complete(service, created)
    database = storage.registry_path
    target = Path(str(database) + suffix)
    outside = tmp_path / "sibling.sqlite"
    if target.exists():
        shutil.copyfile(target, outside)
        target.rename(tmp_path / "saved-file")
    else:
        outside.write_bytes(b"")
    os.link(outside, target)
    before = hashlib.sha256(outside.read_bytes()).hexdigest()
    with pytest.raises((StorageIntegrityError, OSError)), _connect(database):
        pass
    assert hashlib.sha256(outside.read_bytes()).hexdigest() == before


def test_existing_storage_rejects_database_replacement(tmp_path: Path) -> None:
    service, created, storage = _created(tmp_path)
    _populate_complete(service, created)
    saved = tmp_path / "saved.sqlite"
    storage.registry_path.rename(saved)
    shutil.copyfile(saved, storage.registry_path)
    with pytest.raises((StorageIntegrityError, OSError)):
        storage.current_draft(created.case_id)


def test_normal_sqlite_reopen_preserves_registered_draft(tmp_path: Path) -> None:
    service, created, storage = _created(tmp_path)
    _populate_complete(service, created)
    assert (
        CaseStorage(created.case_root).current_draft(created.case_id).to_bytes()
        == storage.current_draft(created.case_id).to_bytes()
    )


def test_existing_wal_database_reopens_without_changing_mode_or_data(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE sample(value)")
        connection.execute("INSERT INTO sample VALUES(1)")
    connection.close()
    with _connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("SELECT value FROM sample").fetchone()[0] == 1
        connection.execute("INSERT INTO sample VALUES(2)")
    with _connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sample").fetchone()[0] == 2


def test_database_and_sidecars_cannot_be_retargeted_during_connection(tmp_path: Path) -> None:
    path = tmp_path / "pinned.sqlite"
    with _connect(path) as connection:
        connection.execute("CREATE TABLE sample(value)")
        for suffix in ("", "-journal", "-wal", "-shm"):
            target = Path(str(path) + suffix)
            with pytest.raises(OSError):
                target.rename(tmp_path / ("moved" + suffix))
        connection.execute("INSERT INTO sample VALUES(1)")


def test_new_hardlink_is_detected_before_next_normal_sqlite_write(tmp_path: Path) -> None:
    path = tmp_path / "pinned.sqlite"
    with _connect(path) as connection:
        connection.execute("CREATE TABLE sample(value)")
        alias = tmp_path / "new-alias.sqlite"
        os.link(path, alias)  # Windows permits new links despite the rename pin.
        before = hashlib.sha256(alias.read_bytes()).hexdigest()
        with pytest.raises(OSError):
            connection.execute("INSERT INTO sample VALUES(1)")
        assert hashlib.sha256(alias.read_bytes()).hexdigest() == before
