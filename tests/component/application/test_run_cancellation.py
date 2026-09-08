"""Synthetic registered prelaunch cancel/refusal boundaries; no native process."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from test_issued_runner_storage import _prepared as native_prepared
from test_persistence_authority import _created, _populate_complete, _profile
from test_registered_execution import _build

from febio_cae.application.service import RegisteredCaseService
from febio_cae.domain import RunState
from febio_cae.domain.ports import PortError, TrustedOwnerContext


def prepared(tmp_path: Path) -> tuple[Any, Any, Any]:
    service, created, storage = _created(tmp_path)
    _populate_complete(service, created)
    revision = service.freeze_case(created.case_id).revision
    assert revision is not None
    owner = TrustedOwnerContext(created.case_id, "run-prelaunch", "attempt-prelaunch", 1)
    destination = (
        storage.root / f"cases/{created.case_id}/runs/{owner.run_id}/attempts/{owner.attempt_id}"
    )
    mesh, bundle, inputs = _build(revision, destination)
    storage.claim(owner)
    storage._register_execution(owner, bundle, mesh, _profile(bundle.profile_id), inputs)
    return service, storage, owner


def dump(path: Path) -> tuple[str, ...]:
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
        return tuple(connection.iterdump())


@pytest.mark.parametrize("boundary", ["native-context", "native-process", "preparing"])
def test_cancel_refuses_native_or_started_attempt_without_mutation(
    tmp_path: Path, boundary: str
) -> None:
    if boundary.startswith("native"):
        storage, owner, _, issued, _, _ = native_prepared(tmp_path)
        if boundary == "native-process":
            storage._accept_runner_start(owner, issued)
        service = RegisteredCaseService(state_dir=tmp_path / "state")
    else:
        service, storage, owner = prepared(tmp_path)
        storage._transition_attempt(owner, RunState.PREPARING)
    before = dump(storage.registry_path)
    state = storage._attempt(owner).state.value
    operation = getattr(service, "cancel_run", None)
    assert callable(operation), "public registered prelaunch cancellation is missing"
    result = operation(owner.case_id, owner.run_id)
    assert result["status"] == "UNSUPPORTED_ENVIRONMENT" and result["run_status"] == state
    assert result["diagnostics"] and result["task_status"] != "COMPLETE"
    assert dump(storage.registry_path) == before


def test_cancel_requires_no_output_publication(tmp_path: Path) -> None:
    service, storage, owner = prepared(tmp_path)
    with sqlite3.connect(storage.registry_path) as connection:
        connection.execute("UPDATE execution_lineage SET writer_closed=1")
    before = dump(storage.registry_path)
    operation = getattr(service, "cancel_run", None)
    assert callable(operation), "public registered prelaunch cancellation is missing"
    with pytest.raises(PortError, match="prelaunch|publication"):
        operation(owner.case_id, owner.run_id)
    assert dump(storage.registry_path) == before
