"""Residual actual-content/SQLite identity and repeated editing regressions."""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import replace
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
    patch = CasePatch(first.revision_id, first.spec_digest,
        (CasePatchEdit("budget", Budget(Quantity(30, "s"), 1, 1, 0, 0), True),), (_evidence("budget"),))
    current = service.apply_patch(created.case_id, patch)
    second = service.freeze_case(created.case_id).revision
    assert second is not None
    next_patch = CasePatch(second.revision_id, second.spec_digest,
        (CasePatchEdit("budget", Budget(Quantity(40, "s"), 1, 1, 0, 0), True),), (_evidence("budget"),))
    updated = service.apply_patch(created.case_id, next_patch, expected_generation=current.generation)
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
    with pytest.raises((StorageIntegrityError, OSError)):
        with _connect(database):
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
    assert CaseStorage(created.case_root).current_draft(created.case_id).to_bytes() == storage.current_draft(created.case_id).to_bytes()
