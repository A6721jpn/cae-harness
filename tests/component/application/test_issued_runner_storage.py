"""Synthetic issued-runner persistence, not an actual process/drain test."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from test_persistence_authority import _created, _populate_complete, _profile
from test_registered_execution import _build

from febio_cae.domain import ExecutionSetting, ProcessIdentity, RunState
from febio_cae.domain.ports import PortError, TrustedOwnerContext
from febio_cae.storage.registry import CaseStorage


def test_xplt_capacity_owned_sealing_and_oversize_no_publication(tmp_path: Path) -> None:
    (tmp_path / "admitted").mkdir()
    (tmp_path / "rejected").mkdir()
    storage, owner, _, issued, root, bundle = _prepared(tmp_path / "admitted")
    storage._accept_runner_start(owner, issued)
    (root / "output").mkdir(parents=True)
    content = b"x" * (32 * 1024 * 1024 + 1)
    (root / "output/results.xplt").write_bytes(content)
    validating = issued.transition_to(RunState.DRAINING).transition_to(RunState.VALIDATING)
    storage._accept_runner_poll(owner, issued, validating)
    entries = storage._seal_native_output(owner)
    assert len(entries) == 1
    resolved = storage.resolve_file(entries[0], bundle, validating)
    assert resolved.content == content
    assert entries[0].size_bytes == len(content)
    assert entries[0].digest == hashlib.sha256(content).hexdigest()

    rejected, owner, _, issued, root, _ = _prepared(tmp_path / "rejected")
    rejected._accept_runner_start(owner, issued)
    (root / "output").mkdir(parents=True)
    # Seek-created sparse payload: refusal must occur before capture/publication.
    with (root / "output/results.xplt").open("wb") as stream:
        stream.seek(128 * 1024 * 1024)
        stream.write(b"x")
    validating = issued.transition_to(RunState.DRAINING).transition_to(RunState.VALIDATING)
    rejected._accept_runner_poll(owner, issued, validating)
    with pytest.raises(PortError, match="oversized result"):
        rejected._seal_native_output(owner)
    _, lineage = rejected._lineage(validating)
    assert lineage["sealed_files"] is None and not lineage["writer_closed"]
    destination = (
        rejected.root
        / f"cases/{owner.case_id}/runs/{owner.run_id}/attempts/{owner.attempt_id}/output"
    )
    assert not destination.exists()


def _prepared(tmp_path: Path) -> tuple[Any, ...]:
    service, created, storage = _created(tmp_path)
    _populate_complete(service, created)
    revision = service.freeze_case(created.case_id).revision
    assert revision is not None
    owner = TrustedOwnerContext(created.case_id, "run", "attempt", 1)
    destination = storage.root / f"cases/{created.case_id}/runs/run/attempts/attempt"
    mesh, bundle, inputs = _build(revision, destination)
    storage.claim(owner)
    original = storage._register_execution(owner, bundle, mesh, _profile(bundle.profile_id), inputs)
    root = storage.root / "native" / created.case_id / "run" / "attempt" / "1"
    storage._prepare_runner(owner, root)
    issued = replace(
        original,
        state=RunState.RUNNING,
        process=ProcessIdentity(
            bundle.argv[0],
            bundle.tool.executable_digest,
            bundle.argv,
            str(root),
            bundle.thread_count,
            "synthetic-issued-not-a-live-process",
        ),
        settings=(
            *bundle.settings,
            ExecutionSetting("attempt_root", str(root)),
            ExecutionSetting("max_elapsed_seconds", revision.spec.budget.max_elapsed.to_si().value),
        ),
    )
    return storage, owner, original, issued, root, bundle


def test_issued_running_snapshot_is_persisted_but_not_publicly_adopted(tmp_path: Path) -> None:
    storage, owner, original, issued, _, _ = _prepared(tmp_path)
    with pytest.raises(PortError):
        storage.validate(owner, issued)
    assert storage._attempt(owner) == original
    storage._accept_runner_start(owner, issued)
    reopened = CaseStorage(storage.root)
    assert reopened._attempt(owner) == issued
    reopened.validate(owner, issued)
    with pytest.raises(PortError):
        storage._accept_runner_start(owner, issued)


@pytest.mark.parametrize("wrong", ["root", "argv", "generation", "budget"])
def test_issued_start_must_match_prepared_context(tmp_path: Path, wrong: str) -> None:
    storage, owner, _, issued, root, _ = _prepared(tmp_path)
    if wrong == "generation":
        issued = replace(issued, owner_generation=2)
    elif wrong == "budget":
        issued = replace(issued, settings=())
    else:
        process = replace(
            issued.process,
            **({"cwd": str(root.parent)} if wrong == "root" else {"argv": ("foreign",)}),
        )
        issued = replace(issued, process=process)
    with pytest.raises(PortError):
        storage._accept_runner_start(owner, issued)


def test_only_observed_drained_validation_can_seal_native_xplt(tmp_path: Path) -> None:
    storage, owner, _, issued, root, bundle = _prepared(tmp_path)
    storage._accept_runner_start(owner, issued)
    (root / "output").mkdir(parents=True)
    (root / "output/results.xplt").write_bytes(b"synthetic-xplt-not-native")
    with pytest.raises(PortError):
        storage._seal_native_output(owner)
    validating = issued.transition_to(RunState.DRAINING).transition_to(RunState.VALIDATING)
    storage._accept_runner_poll(owner, issued, validating)
    with pytest.raises(PortError):
        storage._accept_runner_poll(owner, issued, validating)
    entries = storage._seal_native_output(owner)
    assert len(entries) == 1
    assert entries[0].logical_path == "output/results.xplt"
    assert entries[0].role == "result"
    assert (
        storage.resolve_file(entries[0], bundle, validating).content == b"synthetic-xplt-not-native"
    )
    with pytest.raises(PortError):
        storage._seal_native_output(owner)


def test_failed_runner_cannot_seal_native_xplt(tmp_path: Path) -> None:
    storage, owner, _, issued, _, _ = _prepared(tmp_path)
    storage._accept_runner_start(owner, issued)
    failed = issued.transition_to(RunState.DRAINING).transition_to(RunState.FAILED)
    storage._accept_runner_poll(owner, issued, failed)
    with pytest.raises(PortError):
        storage._seal_native_output(owner)


@pytest.mark.parametrize("log", [b"synthetic log", None])
def test_solver_log_binding_owned_capture(tmp_path: Path, log: bytes | None) -> None:
    from febio_cae.storage import CaseStorage

    storage, owner, _, issued, root, bundle = _prepared(tmp_path)
    storage._accept_runner_start(owner, issued)
    (root / "output").mkdir(parents=True)
    (root / "output/results.xplt").write_bytes(b"synthetic xplt")
    if log is not None:
        (root / "output/solver.log").write_bytes(log)
    with pytest.raises(PortError):
        storage._seal_native_output(owner)
    validating = issued.transition_to(RunState.DRAINING).transition_to(RunState.VALIDATING)
    storage._accept_runner_poll(owner, issued, validating)
    entries = storage._seal_native_output(owner)
    assert len(entries) == (2 if log is not None else 1)
    reopened = CaseStorage(storage.root)
    assert reopened.resolve_file(entries[0], bundle, validating).content == b"synthetic xplt"
    if log is not None:
        assert entries[1].logical_path == "output/solver.log"
        assert entries[1].role == "solver_log"
        assert reopened.resolve_file(entries[1], bundle, validating).content == log
    else:
        (root / "output/solver.log").write_bytes(b"late log")
    with pytest.raises(PortError):
        reopened._seal_native_output(owner)


def test_solver_log_binding_size_refuses_partial_publication(tmp_path: Path) -> None:
    from febio_cae.domain.ports import PortErrorCategory

    storage, owner, _, issued, root, _ = _prepared(tmp_path)
    storage._accept_runner_start(owner, issued)
    (root / "output").mkdir(parents=True)
    (root / "output/results.xplt").write_bytes(b"synthetic xplt")
    (root / "output/solver.log").write_bytes(b"x" * (8 * 1024 * 1024 + 1))
    validating = issued.transition_to(RunState.DRAINING).transition_to(RunState.VALIDATING)
    storage._accept_runner_poll(owner, issued, validating)
    with pytest.raises(PortError) as failure:
        storage._seal_native_output(owner)
    assert failure.value.category is PortErrorCategory.INTEGRITY
    _, lineage = storage._lineage(validating)
    assert lineage["sealed_files"] is None and not lineage["writer_closed"]
    destination = (
        storage.root
        / f"cases/{owner.case_id}/runs/{owner.run_id}/attempts/{owner.attempt_id}/output"
    )
    assert not destination.exists()
