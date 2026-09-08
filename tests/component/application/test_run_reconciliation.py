"""Bounded reconciliation checks using synthetic registered execution only."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from test_issued_runner_storage import _prepared
from test_registered_execution import test_registered_execution_publication_boundary as execute

from febio_cae.application.service import RegisteredCaseService
from febio_cae.domain.ports import PortError
from febio_cae.storage.registry import CaseStorage


def interrupted_fixture(tmp_path: Path) -> tuple[RegisteredCaseService, dict[str, str]]:
    class StopAtGap(BaseException):
        pass

    marker: dict[str, str] = {}
    original = CaseStorage.publish_manifest

    def stop(storage: Any, owner: Any, manifest: Any) -> Any:
        result = original(storage, owner, manifest)
        marker.update(case_id=owner.case_id, run_id=owner.run_id, manifest_id=result.manifest_id)
        raise StopAtGap

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(CaseStorage, "publish_manifest", stop)
        with pytest.raises(StopAtGap):
            execute(tmp_path, "state-time")
    return RegisteredCaseService(state_dir=tmp_path / "state"), marker


def test_resume_refuses_native_identity_without_adoption(tmp_path: Path) -> None:
    storage, owner, _, issued, _, _ = _prepared(tmp_path)
    storage._accept_runner_start(owner, issued)
    before = storage._attempt(owner).to_bytes()
    service = RegisteredCaseService(state_dir=tmp_path / "state")
    operation = getattr(service, "resume_run", None)
    assert callable(operation), "registered run reconciliation is missing"
    result = operation(owner.case_id, owner.run_id)
    assert result["status"] == "UNSUPPORTED_ENVIRONMENT"
    assert result["run_status"] == "RUNNING" and result["task_status"] != "COMPLETE"
    assert result["diagnostics"] and result["next_actions"]
    assert storage._attempt(owner).to_bytes() == before


def test_resume_refuses_unclosed_registered_writer(tmp_path: Path) -> None:
    service, marker = interrupted_fixture(tmp_path)
    storage = service._storage(marker["case_id"])
    with sqlite3.connect(storage.registry_path) as connection:
        connection.execute("UPDATE execution_lineage SET writer_closed=0")
        before = connection.execute("SELECT payload FROM owners").fetchall()
    operation = getattr(service, "resume_run", None)
    assert callable(operation), "registered run reconciliation is missing"
    with pytest.raises(PortError, match="writer"):
        operation(marker["case_id"], marker["run_id"])
    with sqlite3.connect(storage.registry_path) as connection:
        assert connection.execute("SELECT payload FROM owners").fetchall() == before
