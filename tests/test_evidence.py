from __future__ import annotations

import hashlib
import inspect
import json
import multiprocessing
import os
import pickle
import stat
import sys
import threading
import time
from collections.abc import Callable
from copy import copy, deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

import febio_cae_harness.evidence as evidence_module
import febio_cae_harness.workspace as workspace_module
from febio_cae_harness.contracts import IntentContract, IntentState
from febio_cae_harness.evidence import (
    EVENT_LOCK_FILE,
    EVENTS_FILE,
    EvidenceIntegrityError,
    EvidenceStore,
    ValidatorAuthority,
    ValidatorAuthorityManager,
    VerificationReceipt,
)
from febio_cae_harness.workspace import (
    CaseWorkspace,
    ValidatedCaseWorkspace,
    WorkspaceBoundaryError,
)


def make_case(tmp_path: Path) -> tuple[ValidatedCaseWorkspace, CaseWorkspace, IntentContract]:
    tool_root = tmp_path / "tool"
    cae_root = tmp_path / "02_CAE"
    tool_root.mkdir()
    workspace = ValidatedCaseWorkspace(tool_root=tool_root, cae_root=cae_root)
    case = workspace.create_case("case-a")
    intent = IntentContract(
        engineering_question="What is the displacement?",
        units={"length": "mm", "force": "N"},
    )
    return workspace, case, intent


def _append_event_from_replacement_process(
    tool_root: str,
    cae_root: str,
    case_id: str,
    lock_relative_path: str,
    started: Any,
    completed: Any,
    results: Any,
) -> None:
    """Attempt an append through a replacement lock pathname in a fresh process."""

    started.set()
    try:
        evidence_module.EVENT_LOCK_FILE = lock_relative_path
        workspace = ValidatedCaseWorkspace(Path(tool_root), Path(cae_root))
        store = EvidenceStore.open(workspace.open_case(case_id))
        event = store.append_event("replacement-child")
    except BaseException as error:  # pragma: no cover - assertion reports details
        results.put(("error", type(error).__name__, str(error)))
    else:
        results.put(("ok", event["sequence"]))
    finally:
        completed.set()


def _reopen_intent_from_fresh_process(
    tool_root: str,
    cae_root: str,
    case_id: str,
    results: Any,
) -> None:
    """Reopen one case in a spawned interpreter and return its projections."""

    try:
        workspace = ValidatedCaseWorkspace(Path(tool_root), Path(cae_root))
        store = EvidenceStore.open(workspace.open_case(case_id))
        results.put(("ok", store.intent.to_dict(), store.manifest))
    except BaseException as error:  # pragma: no cover - assertion reports details
        results.put(("error", type(error).__name__, str(error)))


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def test_verification_authority_is_manager_bound() -> None:
    assert hasattr(evidence_module, "ValidatorAuthorityManager")
    assert hasattr(evidence_module, "ValidatorAuthority")
    assert hasattr(evidence_module, "ValidatorExecution")
    assert "authority" in inspect.signature(EvidenceStore.record_verification).parameters
    assert "validator" not in inspect.signature(EvidenceStore.record_verification).parameters
    assert "status" not in inspect.signature(EvidenceStore.record_verification).parameters


def test_intent_snapshot_authority_api_is_present() -> None:
    assert hasattr(evidence_module, "IntentSnapshotAuthority")
    assert hasattr(EvidenceStore, "issue_intent_snapshot")


def test_intent_snapshot_authority_binds_immutable_live_identity(tmp_path: Path) -> None:
    _, case, intent = make_case(tmp_path)
    store = EvidenceStore(case, intent)

    snapshot = store.issue_intent_snapshot()

    assert type(snapshot) is evidence_module.IntentSnapshotAuthority
    assert snapshot.case_id == case.case_id
    assert snapshot.case_sha256 == store.manifest["case_sha256"]
    assert snapshot.intent_sha256 == store.manifest["intent"]["sha256"]
    assert snapshot.intent == intent
    assert store._validate_intent_snapshot(snapshot) == intent


def test_intent_snapshot_authority_rejects_forgery_and_mutation(tmp_path: Path) -> None:
    _, case, intent = make_case(tmp_path)
    store = EvidenceStore(case, intent)
    snapshot = store.issue_intent_snapshot()
    authority_type = evidence_module.IntentSnapshotAuthority

    with pytest.raises(TypeError):
        authority_type()
    with pytest.raises(EvidenceIntegrityError):
        store._validate_intent_snapshot(object.__new__(authority_type))
    with pytest.raises(TypeError):
        type("ForgedIntentSnapshot", (authority_type,), {})
    with pytest.raises(TypeError):
        copy(snapshot)
    with pytest.raises(TypeError):
        deepcopy(snapshot)
    with pytest.raises(TypeError):
        pickle.dumps(snapshot)
    with pytest.raises(TypeError):
        replace(snapshot, case_id="forged")  # type: ignore[type-var]
    with pytest.raises(AttributeError):
        snapshot.case_id = "forged"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        del snapshot.case_id


def test_intent_snapshot_authority_rejects_foreign_store_and_forged_values(
    tmp_path: Path,
) -> None:
    _, case, intent = make_case(tmp_path)
    store = EvidenceStore(case, intent)
    snapshot = store.issue_intent_snapshot()
    foreign_workspace = ValidatedCaseWorkspace(tmp_path / "tool-b", tmp_path / "02_CAE-b")
    foreign_store = EvidenceStore(foreign_workspace.create_case("case-a"), intent)

    with pytest.raises(EvidenceIntegrityError):
        foreign_store._validate_intent_snapshot(snapshot)
    for forged in (True, False, {}, {"case_id": case.case_id}, {"state": "BOUND"}):
        with pytest.raises(EvidenceIntegrityError):
            store._validate_intent_snapshot(forged)  # type: ignore[arg-type]


@pytest.mark.parametrize("tamper", ["intent", "manifest", "event", "artifact"])
def test_intent_snapshot_authority_live_revalidation_rejects_tampering(
    tmp_path: Path,
    tamper: str,
) -> None:
    _, case, intent = make_case(tmp_path)
    store = EvidenceStore(case, intent)
    store.append_event("intent_reviewed", {"reviewed": True})
    store.record_attempt("attempt-1")
    artifact = case.write_text(
        Path("90_Temporary") / "attempts" / "attempt-1" / "result.txt",
        "synthetic result",
    )
    store.record_artifact(artifact, attempt_id="attempt-1")
    snapshot = store.issue_intent_snapshot()

    if tamper == "intent":
        payload = json.loads(store.intent_path.read_text(encoding="utf-8"))
        payload["engineering_question"] = "tampered"
        store.intent_path.write_text(json.dumps(payload), encoding="utf-8")
    elif tamper == "manifest":
        payload = json.loads(store.manifest_path.read_text(encoding="utf-8"))
        payload["state"] = "BOUND"
        store.manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    elif tamper == "event":
        lines = store.events_path.read_text(encoding="utf-8").splitlines()
        lines[0] = lines[0].replace("intent_reviewed", "tampered")
        store.events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        artifact.write_text("tampered result", encoding="utf-8")

    with pytest.raises(EvidenceIntegrityError):
        store._validate_intent_snapshot(snapshot)


def make_authority(
    result: object = True,
    *,
    validator: object | None = None,
) -> tuple[ValidatorAuthorityManager, ValidatorAuthority]:
    manager = ValidatorAuthorityManager()
    executable = validator if validator is not None else lambda source: result
    authority = manager.register_validator(
        "synthetic-validator",
        executable,  # type: ignore[arg-type]
        runtime="synthetic",
    )
    return manager, authority


def test_authority_forgery_and_state_copies_are_rejected(tmp_path: Path) -> None:
    _, case, intent = make_case(tmp_path)
    manager, authority = make_authority()
    store = EvidenceStore(case, intent, manager)
    store.record_attempt("attempt-1")
    source = case.write_text(
        Path("90_Temporary") / "attempts" / "attempt-1" / "derived.feb",
        "derived",
    )

    event_log_before = store.events_path.read_text(encoding="utf-8")
    with pytest.raises(EvidenceIntegrityError):
        store.record_verification(
            source,
            Path("02_Model") / "derived.feb",
            attempt_id="attempt-1",
        )
    assert store.events_path.read_text(encoding="utf-8") == event_log_before
    forged = object.__new__(ValidatorAuthority)
    object.__setattr__(forged, "_registry", manager._registry)  # type: ignore[attr-defined]
    object.__setattr__(forged, "_capability", object())
    attempts = [forged, object.__new__(type("ForgedAuthority", (ValidatorAuthority,), {}))]
    for candidate in attempts:
        with pytest.raises(EvidenceIntegrityError):
            store.record_verification(
                source,
                Path("02_Model") / "derived.feb",
                attempt_id="attempt-1",
                authority=candidate,
            )
    for operation in (lambda: ValidatorAuthority(), lambda: pickle.dumps(authority)):
        with pytest.raises(TypeError):
            operation()
    with pytest.raises(TypeError):
        copy(authority)
    with pytest.raises(TypeError):
        deepcopy(authority)
    with pytest.raises(AttributeError):
        authority._registry = object()

    _, foreign_authority = make_authority()
    with pytest.raises(EvidenceIntegrityError):
        store.record_verification(
            source,
            Path("02_Model") / "foreign.feb",
            attempt_id="attempt-1",
            authority=foreign_authority,
        )


def test_store_persists_intent_and_manifest_projection(tmp_path: Path) -> None:
    _, case, intent = make_case(tmp_path)

    store = EvidenceStore(case, intent)

    assert json.loads(store.intent_path.read_text(encoding="utf-8")) == intent.to_dict()
    assert store.events_path.is_file()
    assert store.manifest_path.is_file()
    manifest = store.manifest
    assert manifest["case_id"] == "case-a"
    assert manifest["state"] == "GATHERING"
    assert manifest["events"]["count"] == 0
    assert manifest["attempts"] == []
    assert len(manifest["case_sha256"]) == hashlib.sha256().digest_size * 2


def test_event_lock_rejects_existing_hard_link_before_writing(tmp_path: Path) -> None:
    _, case, intent = make_case(tmp_path)
    outside = tmp_path / "outside-events.lock"
    outside.write_bytes(b"")
    target = case.case_root / "90_Temporary" / "events.lock"
    os.link(outside, target)

    with pytest.raises(EvidenceIntegrityError):
        EvidenceStore(case, intent)

    assert outside.read_bytes() == b""
    assert target.read_bytes() == b""
    assert not (case.case_root / "intent.json").exists()


def test_replaced_event_lock_path_cannot_create_second_append_authority(
    tmp_path: Path,
) -> None:
    _, case, intent = make_case(tmp_path)
    store = EvidenceStore(case, intent)
    temporary_root = case.case_root / "90_Temporary"
    lock_path = temporary_root / "events.lock"
    replacement_path = temporary_root / "events.lock.replacement"
    replacement_path.write_bytes(b"")
    replacement_relative_path = "90_Temporary/events.lock.replacement"

    context = multiprocessing.get_context("spawn")
    started = context.Event()
    completed = context.Event()
    results = context.Queue()
    child: Any = None
    child_lock_relative_path = EVENT_LOCK_FILE

    try:
        with store._transaction():
            try:
                os.replace(replacement_path, lock_path)
            except PermissionError:
                # Windows denies replacing an open file.  Pointing the child at
                # the prepared replacement inode exercises the same split-path
                # condition without weakening the production lock contract.
                child_lock_relative_path = replacement_relative_path

            child = context.Process(
                target=_append_event_from_replacement_process,
                args=(
                    str(tmp_path / "tool"),
                    str(tmp_path / "02_CAE"),
                    case.case_id,
                    child_lock_relative_path,
                    started,
                    completed,
                    results,
                ),
            )
            child.start()
            assert started.wait(timeout=10)
            assert not completed.wait(timeout=2)
    finally:
        if child is not None:
            child.join(timeout=10)

    assert child is not None
    assert not child.is_alive()
    assert child.exitcode == 0
    assert results.get(timeout=2)[0] == "ok"


def test_events_are_chained_and_append_only(tmp_path: Path) -> None:
    _, case, intent = make_case(tmp_path)
    store = EvidenceStore(case, intent)

    first = store.append_event("input_collected", {"source": "synthetic"})

    original_write_text = CaseWorkspace.write_text

    def reject_event_rewrite(
        self: CaseWorkspace,
        relative_path: str | Path,
        text: str,
        *,
        encoding: str = "utf-8",
    ) -> Path:
        if Path(relative_path).as_posix() == "90_Temporary/events.jsonl":
            raise AssertionError("events.jsonl must be appended, not rewritten")
        return original_write_text(self, relative_path, text, encoding=encoding)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(CaseWorkspace, "write_text", reject_event_rewrite)
    try:
        second = store.append_event("intent_reviewed", {"reviewed": True})
    finally:
        monkeypatch.undo()

    lines = (
        case.case_root.joinpath("90_Temporary", "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    assert len(lines) == 2
    first_record = json.loads(lines[0])
    second_record = json.loads(lines[1])
    assert first["sequence"] == 1
    assert second["sequence"] == 2
    assert first_record["previous_sha256"] is None
    assert second_record["previous_sha256"] == first_record["sha256"]
    assert store.manifest["events"]["count"] == 2
    assert store.manifest["events"]["last_sha256"] == second_record["sha256"]


def test_attempt_records_and_artifact_digests_are_projected(tmp_path: Path) -> None:
    _, case, intent = make_case(tmp_path)
    store = EvidenceStore(case, intent)

    attempt = store.record_attempt("attempt-1", {"status": "started"})
    artifact_path = case.write_text(
        Path("90_Temporary") / "attempts" / "attempt-1" / "output.txt",
        "synthetic result",
    )
    artifact = store.record_artifact(artifact_path, attempt_id="attempt-1")

    assert attempt["attempt_id"] == "attempt-1"
    assert isinstance(attempt["sha256"], str)
    assert len(attempt["sha256"]) == hashlib.sha256().digest_size * 2
    assert artifact["path"] == "90_Temporary/attempts/attempt-1/output.txt"
    expected_digest = hashlib.sha256(b"synthetic result").hexdigest()
    assert artifact["sha256"] == expected_digest
    assert store.manifest["attempts"][0]["attempt_id"] == "attempt-1"
    assert store.manifest["artifacts"][0]["sha256"] == expected_digest

    with pytest.raises(FileExistsError):
        store.record_attempt("attempt-1", {"status": "duplicate"})


def test_reopen_validates_and_restores_persisted_state(tmp_path: Path) -> None:
    _, case, intent = make_case(tmp_path)
    store = EvidenceStore(case, intent)
    store.append_event("input_collected", {"source": "synthetic"})
    store.record_attempt("attempt-1", {"status": "started"})

    reopened = EvidenceStore.open(case)

    assert reopened.reopen() is reopened
    assert reopened.intent == intent
    assert reopened.manifest == store.manifest


def test_revise_intent_appends_complete_revision_and_reprojects_manifest(
    tmp_path: Path,
) -> None:
    _, case, intent = make_case(tmp_path)
    store = EvidenceStore(case, intent)
    snapshot = store.issue_intent_snapshot()
    revised = IntentContract(
        engineering_question="What is the revised synthetic displacement?",
        units={"length": "mm", "force": "N"},
        material={"source": "authoritative-synthetic-input"},
        condition_sources=["synthetic-user-revision"],
        state=IntentState.BOUND,
    )

    event = store.revise_intent(revised)

    assert event["event_type"] == "intent_revised"
    payload = cast(dict[str, object], event["payload"])
    assert payload == {
        "previous_intent": intent.to_dict(),
        "previous_intent_sha256": _canonical_digest(intent.to_dict()),
        "new_intent": revised.to_dict(),
        "new_intent_sha256": _canonical_digest(revised.to_dict()),
    }
    persisted_event = json.loads(store.events_path.read_text(encoding="utf-8").splitlines()[-1])
    assert persisted_event == event
    assert json.loads(store.intent_path.read_text(encoding="utf-8")) == revised.to_dict()
    assert store.intent == revised
    assert store.manifest["state"] == "BOUND"
    assert store.manifest["intent"]["sha256"] == _canonical_digest(revised.to_dict())
    assert store.manifest["events"]["last_sha256"] == event["sha256"]
    with pytest.raises(EvidenceIntegrityError, match="stale"):
        _ = snapshot.intent


def test_revise_intent_expected_snapshot_cas_allows_one_cross_store_race_winner(
    tmp_path: Path,
) -> None:
    assert "expected_snapshot" in inspect.signature(EvidenceStore.revise_intent).parameters
    _, case, intent = make_case(tmp_path)
    issuer = EvidenceStore(case, intent)
    expected_snapshot = issuer.issue_intent_snapshot()
    writers = (EvidenceStore.open(case), EvidenceStore.open(case))
    candidates = (
        IntentContract(engineering_question="CAS writer A", state=IntentState.GATHERING),
        IntentContract(engineering_question="CAS writer B", state=IntentState.GATHERING),
    )
    barrier = threading.Barrier(3)
    result_lock = threading.Lock()
    results: list[tuple[str, object]] = []

    def revise(store: EvidenceStore, candidate: IntentContract) -> None:
        barrier.wait(timeout=5)
        result: tuple[str, object]
        try:
            event = store.revise_intent(candidate, expected_snapshot=expected_snapshot)
        except BaseException as error:  # pragma: no cover - assertions inspect it
            result = ("error", error)
        else:
            result = ("ok", event)
        with result_lock:
            results.append(result)

    threads = [
        threading.Thread(target=revise, args=(store, candidate), daemon=True)
        for store, candidate in zip(writers, candidates, strict=True)
    ]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert [kind for kind, _ in results].count("ok") == 1
    assert [kind for kind, _ in results].count("error") == 1
    error = next(value for kind, value in results if kind == "error")
    assert isinstance(error, EvidenceIntegrityError)
    assert "stale" in str(error).casefold()
    persisted = EvidenceStore.open(case)
    assert persisted.intent in candidates
    assert len(persisted.events_path.read_text(encoding="utf-8").splitlines()) == 1
    with pytest.raises(EvidenceIntegrityError, match="stale"):
        persisted.revise_intent(
            IntentContract(engineering_question="lost update"),
            expected_snapshot=expected_snapshot,
        )
    assert len(persisted.events_path.read_text(encoding="utf-8").splitlines()) == 1
    with pytest.raises(EvidenceIntegrityError, match="stale"):
        _ = expected_snapshot.intent


def test_revise_intent_expected_snapshot_rejects_foreign_and_fabricated_without_append(
    tmp_path: Path,
) -> None:
    assert "expected_snapshot" in inspect.signature(EvidenceStore.revise_intent).parameters
    current_root = tmp_path / "current"
    current_root.mkdir()
    _, case, intent = make_case(current_root)
    store = EvidenceStore(case, intent)
    foreign_tool = tmp_path / "foreign-tool"
    foreign_tool.mkdir()
    foreign_workspace = ValidatedCaseWorkspace(
        foreign_tool,
        tmp_path / "foreign-02_CAE",
    )
    foreign_store = EvidenceStore(foreign_workspace.create_case(case.case_id), intent)
    foreign_snapshot = foreign_store.issue_intent_snapshot()
    fabricated = object.__new__(evidence_module.IntentSnapshotAuthority)
    before = store.events_path.read_bytes()

    for expected_snapshot in (foreign_snapshot, fabricated):
        with pytest.raises(EvidenceIntegrityError):
            store.revise_intent(
                IntentContract(engineering_question="must not append"),
                expected_snapshot=expected_snapshot,
            )
        assert store.events_path.read_bytes() == before


def test_revise_intent_survives_spawned_fresh_process_reopen(tmp_path: Path) -> None:
    workspace, case, intent = make_case(tmp_path)
    store = EvidenceStore(case, intent)
    revised = IntentContract(
        engineering_question="Fresh-process revised intent",
        units={"length": "mm"},
        unresolved=["synthetic-condition"],
        state=IntentState.ASK_AND_BLOCK,
    )
    event = store.revise_intent(revised)
    context = multiprocessing.get_context("spawn")
    results = context.Queue()

    child = context.Process(
        target=_reopen_intent_from_fresh_process,
        args=(
            str(workspace.tool_root),
            str(workspace.cae_root),
            case.case_id,
            results,
        ),
    )
    child.start()
    child.join(timeout=10)

    assert not child.is_alive()
    assert child.exitcode == 0
    result = results.get(timeout=5)
    results.close()
    results.join_thread()
    assert result[0] == "ok", result
    assert result[1] == revised.to_dict()
    assert result[2]["intent"]["sha256"] == _canonical_digest(revised.to_dict())
    assert result[2]["events"]["last_sha256"] == event["sha256"]


def test_revise_intent_rejects_stale_or_fabricated_revision_history(
    tmp_path: Path,
) -> None:
    _, case, intent = make_case(tmp_path)
    store = EvidenceStore(case, intent)
    revised = IntentContract(
        engineering_question="Current revision",
        state=IntentState.BOUND,
    )
    first = store.revise_intent(revised)

    with pytest.raises(EvidenceIntegrityError, match="supplied intent"):
        EvidenceStore(case, intent)

    fabricated = IntentContract(
        engineering_question="Fabricated revision",
        state=IntentState.BOUND,
    )
    payload = {
        "previous_intent": intent.to_dict(),
        "previous_intent_sha256": _canonical_digest(intent.to_dict()),
        "new_intent": fabricated.to_dict(),
        "new_intent_sha256": _canonical_digest(fabricated.to_dict()),
    }
    body = {
        "schema_version": evidence_module.SCHEMA_VERSION,
        "case_id": case.case_id,
        "sequence": 2,
        "event_type": "intent_revised",
        "payload": payload,
        "previous_sha256": first["sha256"],
    }
    fabricated_event = {**body, "sha256": _canonical_digest(body)}
    case.append_text(
        EVENTS_FILE,
        json.dumps(
            fabricated_event,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n",
    )

    with pytest.raises(EvidenceIntegrityError, match="intent revision chain"):
        EvidenceStore.open(case)


def test_internal_intent_revision_event_cannot_be_fabricated_through_generic_event_api(
    tmp_path: Path,
) -> None:
    _, case, intent = make_case(tmp_path)
    store = EvidenceStore(case, intent)

    with pytest.raises(EvidenceIntegrityError, match="dedicated API"):
        store.append_event("intent_revised", {})
    assert store.events_path.read_text(encoding="utf-8") == ""


@pytest.mark.parametrize("interrupted_file", ["intent.json", "CASE_MANIFEST.json"])
def test_recovery_replace_keeps_exact_temporary_owner_until_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interrupted_file: str,
) -> None:
    _, case, intent = make_case(tmp_path)
    store = EvidenceStore(case, intent)
    revised = IntentContract(
        engineering_question=f"Revision interrupted before {interrupted_file}",
        state=IntentState.BOUND,
    )
    original_write = workspace_module._ExactCaseTransaction.replace_bytes
    interrupted = False

    def interrupt_projection_once(
        self: Any,
        relative_path: str | Path,
        data: bytes,
    ) -> None:
        nonlocal interrupted
        if not interrupted and Path(relative_path).as_posix() == interrupted_file:
            interrupted = True
            raise OSError("simulated projection interruption")
        original_write(self, relative_path, data)

    with monkeypatch.context() as patch:
        patch.setattr(
            workspace_module._ExactCaseTransaction,
            "replace_bytes",
            interrupt_projection_once,
        )
        with pytest.raises(EvidenceIntegrityError, match="intent revision projection"):
            store.revise_intent(revised)

    terminal_event = json.loads(store.events_path.read_text(encoding="utf-8").splitlines()[-1])
    assert terminal_event["event_type"] == "intent_revised"

    original_replace = workspace_module._replace_exact_entry
    observed: list[tuple[None, bool]] = []

    def observe_replace(*args: Any, **kwargs: Any) -> None:
        temporary = kwargs["temporary"]
        observed.append((temporary.validate(), temporary.released))
        original_replace(*args, **kwargs)

    monkeypatch.setattr(workspace_module, "_replace_exact_entry", observe_replace)

    reopened = EvidenceStore.open(case)

    assert observed and all(item == (None, False) for item in observed)
    assert reopened.intent == revised
    assert json.loads(reopened.intent_path.read_text(encoding="utf-8")) == revised.to_dict()
    assert reopened.manifest["intent"]["sha256"] == _canonical_digest(revised.to_dict())
    assert reopened.manifest["events"]["last_sha256"] == terminal_event["sha256"]
    assert not tuple(case.case_root.glob(".intent.json.*"))
    assert not tuple(case.case_root.glob(".CASE_MANIFEST.json.*"))


@pytest.mark.parametrize("tamper", ["intent", "event", "attempt", "manifest"])
def test_reopen_fails_closed_on_tampering(tmp_path: Path, tamper: str) -> None:
    _, case, intent = make_case(tmp_path)
    store = EvidenceStore(case, intent)
    store.append_event("input_collected", {"source": "synthetic"})
    store.record_attempt("attempt-1", {"status": "started"})

    if tamper == "intent":
        payload = json.loads(store.intent_path.read_text(encoding="utf-8"))
        payload["engineering_question"] = "tampered"
        store.intent_path.write_text(json.dumps(payload), encoding="utf-8")
    elif tamper == "event":
        event_lines = store.events_path.read_text(encoding="utf-8").splitlines()
        event_lines[0], event_lines[1] = event_lines[1], event_lines[0]
        store.events_path.write_text("\n".join(event_lines) + "\n", encoding="utf-8")
    elif tamper == "attempt":
        attempt_path = case.case_root / "90_Temporary" / "attempts" / "attempt-1" / "ATTEMPT.json"
        payload = json.loads(attempt_path.read_text(encoding="utf-8"))
        payload["payload"]["status"] = "tampered"
        attempt_path.write_text(json.dumps(payload), encoding="utf-8")
    else:
        manifest = json.loads(store.manifest_path.read_text(encoding="utf-8"))
        manifest["state"] = "BOUND"
        store.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(EvidenceIntegrityError):
        EvidenceStore.open(case)


def test_partial_persistence_and_boundary_escape_fail_closed(tmp_path: Path) -> None:
    workspace, case, intent = make_case(tmp_path)
    case.case_root.joinpath("intent.json").write_text("{}", encoding="utf-8")

    with pytest.raises(EvidenceIntegrityError):
        EvidenceStore(case, intent)

    store = EvidenceStore(workspace.create_case("case-b"), intent)
    with pytest.raises(WorkspaceBoundaryError):
        store.record_artifact(workspace.cae_root / "outside.txt")


def test_concurrent_store_event_writers_do_not_race_sequence_or_predecessor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, case, intent = make_case(tmp_path)
    store_a = EvidenceStore(case, intent)
    store_b = EvidenceStore.open(case)
    original_append = workspace_module._ExactCaseTransaction.append_bytes
    state_lock = threading.Lock()
    started = threading.Barrier(2)
    active_event_appends = 0
    overlapped_event_appends = False

    def delayed_append(
        self: Any,
        relative_path: str | Path,
        data: bytes,
    ) -> None:
        nonlocal active_event_appends, overlapped_event_appends
        if Path(relative_path).as_posix() == EVENTS_FILE:
            with state_lock:
                active_event_appends += 1
                overlapped_event_appends = overlapped_event_appends or active_event_appends > 1
            try:
                time.sleep(0.05)
                original_append(self, relative_path, data)
                return
            finally:
                with state_lock:
                    active_event_appends -= 1
        original_append(self, relative_path, data)

    monkeypatch.setattr(
        workspace_module._ExactCaseTransaction,
        "append_bytes",
        delayed_append,
    )

    errors: list[BaseException] = []
    events: list[dict[str, object]] = []

    def append_from(store: EvidenceStore, event_type: str) -> None:
        try:
            started.wait(timeout=2)
            events.append(store.append_event(event_type))
        except BaseException as error:  # pragma: no cover - assertion reports details
            errors.append(error)

    first = threading.Thread(target=append_from, args=(store_a, "first"))
    second = threading.Thread(target=append_from, args=(store_b, "second"))
    first.start()
    second.start()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert not errors, [(error, error.__cause__) for error in errors]
    assert not overlapped_event_appends
    assert sorted(cast(int, event["sequence"]) for event in events) == [1, 2]
    assert store_a.reopen() is store_a
    assert store_b.reopen() is store_b


def test_caller_true_validator_is_diagnostic_not_authoritative(
    tmp_path: Path,
) -> None:
    _, case, intent = make_case(tmp_path)
    manager, authority = make_authority()
    store = EvidenceStore(case, intent, manager)
    store.record_attempt("attempt-1")
    source = case.write_text(
        Path("90_Temporary") / "attempts" / "attempt-1" / "derived.feb",
        "derived",
    )

    receipt = store.record_verification(
        source,
        Path("02_Model") / "derived.feb",
        attempt_id="attempt-1",
        authority=authority,
    )
    assert receipt is None
    with pytest.raises(EvidenceIntegrityError):
        store.promote_verified(receipt)  # type: ignore[arg-type]
    record = json.loads(store.events_path.read_text(encoding="utf-8").splitlines()[-1])
    assert record["event_type"] == "artifact_validation_diagnostic"
    payload = record["payload"]
    assert payload["case_id"] == case.case_id
    assert payload["attempt_id"] == "attempt-1"
    assert payload["source"] == "90_Temporary/attempts/attempt-1/derived.feb"
    assert payload["sha256"] == hashlib.sha256(b"derived").hexdigest()
    assert payload["destination"] == "02_Model/derived.feb"
    assert payload["validator"] == "synthetic-validator"
    assert payload["runtime"] == "synthetic"
    assert payload["result"] is True
    assert isinstance(payload["evidence_digest"], str)
    assert payload["authority"] == "unverified"
    assert payload["promotable"] is False


def test_failed_verification_has_no_promotion_receipt(tmp_path: Path) -> None:
    _, case, intent = make_case(tmp_path)
    manager, authority = make_authority(False)
    store = EvidenceStore(case, intent, manager)
    store.record_attempt("attempt-1")
    source = case.write_text(
        Path("90_Temporary") / "attempts" / "attempt-1" / "derived.feb",
        "derived",
    )

    receipt = store.record_verification(
        source,
        Path("02_Model") / "derived.feb",
        attempt_id="attempt-1",
        authority=authority,
    )

    assert receipt is None
    with pytest.raises(EvidenceIntegrityError):
        store.promote_verified(receipt)  # type: ignore[arg-type]


@pytest.mark.parametrize("result", [1, "passed", None])
def test_verification_requires_actual_bool_result(tmp_path: Path, result: object) -> None:
    _, case, intent = make_case(tmp_path)
    manager, authority = make_authority(result)
    store = EvidenceStore(case, intent, manager)
    store.record_attempt("attempt-1")
    source = case.write_text(
        Path("90_Temporary") / "attempts" / "attempt-1" / "derived.feb",
        "derived",
    )

    with pytest.raises(EvidenceIntegrityError, match="actual bool"):
        store.record_verification(
            source,
            Path("02_Model") / "derived.feb",
            attempt_id="attempt-1",
            authority=authority,
        )
    assert not any(
        json.loads(line)["event_type"] == "artifact_verified"
        for line in store.events_path.read_text(encoding="utf-8").splitlines()
    )


def test_validator_source_mutation_never_issues_receipt(tmp_path: Path) -> None:
    _, case, intent = make_case(tmp_path)

    def mutate(source: Path) -> bool:
        source.write_text("changed", encoding="utf-8")
        return True

    manager, authority = make_authority(validator=mutate)
    store = EvidenceStore(case, intent, manager)
    store.record_attempt("attempt-1")
    source = case.write_text(
        Path("90_Temporary") / "attempts" / "attempt-1" / "derived.feb",
        "derived",
    )

    with pytest.raises(EvidenceIntegrityError, match="mutated"):
        store.record_verification(
            source,
            Path("02_Model") / "derived.feb",
            attempt_id="attempt-1",
            authority=authority,
        )
    assert not any(
        json.loads(line)["event_type"] == "artifact_verified"
        for line in store.events_path.read_text(encoding="utf-8").splitlines()
    )


@pytest.mark.parametrize("event_type", ["artifact_verified", "artifact_validation_diagnostic"])
def test_internal_verification_events_cannot_be_fabricated_through_generic_event_api(
    tmp_path: Path,
    event_type: str,
) -> None:
    _, case, intent = make_case(tmp_path)
    store = EvidenceStore(case, intent)

    with pytest.raises(EvidenceIntegrityError):
        store.append_event(event_type, {})
    assert store.events_path.read_text(encoding="utf-8") == ""


def test_verification_rejects_duplicate_and_source_mutation(tmp_path: Path) -> None:
    _, case, intent = make_case(tmp_path)
    manager, authority = make_authority()
    store = EvidenceStore(case, intent, manager)
    store.record_attempt("attempt-1")
    source = case.write_text(
        Path("90_Temporary") / "attempts" / "attempt-1" / "derived.feb",
        "derived",
    )
    receipt = store.record_verification(
        source,
        Path("02_Model") / "derived.feb",
        attempt_id="attempt-1",
        authority=authority,
    )
    assert receipt is None

    with pytest.raises(EvidenceIntegrityError):
        store.record_verification(
            source,
            Path("02_Model") / "derived.feb",
            attempt_id="attempt-1",
            authority=authority,
        )
    source.write_text("mutated", encoding="utf-8")
    with pytest.raises(EvidenceIntegrityError):
        store.promote_verified(receipt)  # type: ignore[arg-type]


def test_receipt_is_case_bound_even_for_identical_case_ids(tmp_path: Path) -> None:
    workspace_a = ValidatedCaseWorkspace(tmp_path / "tool-a", tmp_path / "02_CAE-a")
    case_a = workspace_a.create_case("case-a")
    manager, authority = make_authority()
    store_a = EvidenceStore(case_a, IntentContract(engineering_question="q"), manager)
    store_a.record_attempt("attempt-1")
    source_a = case_a.write_text(
        Path("90_Temporary") / "attempts" / "attempt-1" / "derived.feb",
        "derived",
    )
    receipt = store_a.record_verification(
        source_a,
        Path("02_Model") / "derived.feb",
        attempt_id="attempt-1",
        authority=authority,
    )
    assert receipt is None
    payload = json.loads(store_a.events_path.read_text(encoding="utf-8").splitlines()[-1])[
        "payload"
    ]
    receipt_like = VerificationReceipt._issue(
        store_a._receipt_case_token(), payload["evidence_digest"]
    )

    workspace_b = ValidatedCaseWorkspace(tmp_path / "tool-b", tmp_path / "02_CAE-b")
    case_b = workspace_b.create_case("case-a")
    store_b = EvidenceStore(case_b, IntentContract(engineering_question="q"))
    with pytest.raises(EvidenceIntegrityError):
        store_b.promote_verified(receipt_like)


def test_promotion_is_create_new_and_rejects_duplicate_receipt_use(tmp_path: Path) -> None:
    _, case, intent = make_case(tmp_path)
    manager, authority = make_authority()
    store = EvidenceStore(case, intent, manager)
    store.record_attempt("attempt-1")
    source = case.write_text(
        Path("90_Temporary") / "attempts" / "attempt-1" / "derived.feb",
        "derived",
    )
    receipt = store.record_verification(
        source,
        Path("02_Model") / "derived.feb",
        attempt_id="attempt-1",
        authority=authority,
    )
    assert receipt is None
    with pytest.raises(EvidenceIntegrityError):
        store.promote_verified(receipt)  # type: ignore[arg-type]
    assert not (case.case_root / "02_Model" / "derived.feb").exists()


def test_promotion_rejects_diagnostic_receipt_before_copy(tmp_path: Path) -> None:
    _, case, intent = make_case(tmp_path)
    manager, authority = make_authority()
    store = EvidenceStore(case, intent, manager)
    store.record_attempt("attempt-1")
    source = case.write_text(
        Path("90_Temporary") / "attempts" / "attempt-1" / "derived.feb",
        "derived",
    )
    receipt = store.record_verification(
        source,
        Path("02_Model") / "derived.feb",
        attempt_id="attempt-1",
        authority=authority,
    )
    assert receipt is None
    with pytest.raises(EvidenceIntegrityError):
        store.promote_verified(receipt)  # type: ignore[arg-type]
    assert not (case.case_root / "02_Model" / "derived.feb").exists()


def test_unissued_receipt_cannot_replay_after_destination_is_removed(tmp_path: Path) -> None:
    _, case, intent = make_case(tmp_path)
    manager, authority = make_authority()
    store = EvidenceStore(case, intent, manager)
    store.record_attempt("attempt-1")
    source = case.write_text(
        Path("90_Temporary") / "attempts" / "attempt-1" / "derived.feb",
        "derived",
    )
    destination = case.case_root / "02_Model" / "derived.feb"
    receipt = store.record_verification(
        source,
        destination,
        attempt_id="attempt-1",
        authority=authority,
    )
    assert receipt is None
    with pytest.raises(EvidenceIntegrityError):
        store.promote_verified(receipt)  # type: ignore[arg-type]
    assert not destination.exists()


def test_phase1_generic_validator_is_diagnostic_only(tmp_path: Path) -> None:
    _, case, intent = make_case(tmp_path)
    manager, authority = make_authority()
    store = EvidenceStore(case, intent, manager)
    store.record_attempt("attempt-1")
    source = case.write_text(
        Path("90_Temporary") / "attempts" / "attempt-1" / "derived.feb",
        "derived",
    )

    diagnostic_receipt = store.record_verification(
        source,
        Path("02_Model") / "derived.feb",
        attempt_id="attempt-1",
        authority=authority,
    )

    assert diagnostic_receipt is None
    event = json.loads(store.events_path.read_text(encoding="utf-8").splitlines()[-1])
    assert event["event_type"] == "artifact_validation_diagnostic"
    assert event["payload"]["result"] is True
    assert event["payload"]["authority"] == "unverified"
    assert event["payload"]["promotable"] is False
    with pytest.raises(EvidenceIntegrityError):
        store.promote_verified(diagnostic_receipt)  # type: ignore[arg-type]


def test_default_verification_is_explicitly_unverified(tmp_path: Path) -> None:
    _, case, intent = make_case(tmp_path)
    store = EvidenceStore(case, intent)
    store.record_attempt("attempt-1")
    source = case.write_text(
        Path("90_Temporary") / "attempts" / "attempt-1" / "derived.feb",
        "derived",
    )

    receipt = store.record_verification(
        source,
        Path("02_Model") / "derived.feb",
        attempt_id="attempt-1",
    )

    assert receipt is None
    payload = json.loads(store.events_path.read_text(encoding="utf-8").splitlines()[-1])["payload"]
    assert payload["validator"] == "unverified"
    assert payload["result"] is False
    assert payload["authority"] == "unverified"
    assert payload["promotable"] is False


def test_receipt_token_construction_and_reopen_store_are_not_authority(
    tmp_path: Path,
) -> None:
    _, case, intent = make_case(tmp_path)
    manager, authority = make_authority()
    store = EvidenceStore(case, intent, manager)
    store.record_attempt("attempt-1")
    source = case.write_text(
        Path("90_Temporary") / "attempts" / "attempt-1" / "derived.feb",
        "derived",
    )
    store.record_verification(
        source,
        Path("02_Model") / "derived.feb",
        attempt_id="attempt-1",
        authority=authority,
    )
    payload = json.loads(store.events_path.read_text(encoding="utf-8").splitlines()[-1])["payload"]
    token = (
        f"{evidence_module._RECEIPT_PREFIX}{store._receipt_case_token()}"
        f":{payload['evidence_digest']}"
    )
    forged = str.__new__(VerificationReceipt, token)
    issued_like = VerificationReceipt._issue(
        store._receipt_case_token(), payload["evidence_digest"]
    )

    for candidate in (token, forged, issued_like):
        with pytest.raises(EvidenceIntegrityError):
            store.promote_verified(candidate)  # type: ignore[arg-type]
    with pytest.raises(EvidenceIntegrityError):
        EvidenceStore.open(case, manager).promote_verified(forged)
    with pytest.raises(TypeError):
        copy(issued_like)
    with pytest.raises(TypeError):
        deepcopy(issued_like)
    with pytest.raises(TypeError):
        pickle.dumps(issued_like)
    with pytest.raises(TypeError):
        object.__new__(VerificationReceipt)
    with pytest.raises(TypeError):
        type("ForgedReceipt", (VerificationReceipt,), {})


def test_posix_lock_descriptor_open_is_relative_to_exact_root() -> None:
    calls: list[tuple[str, int, int | None]] = []

    def fake_open(path: str, flags: int, *, dir_fd: int | None = None) -> int:
        calls.append((path, flags, dir_fd))
        return 103

    evidence_module._open_posix_lock_descriptor(71, opener=fake_open)
    assert calls == [(".", calls[0][1], 71)]


def test_posix_lock_acquire_never_double_closes_a_reused_descriptor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    close_calls: list[int] = []
    fake_fcntl = SimpleNamespace(
        LOCK_EX=1,
        flock=lambda descriptor, operation: (_ for _ in ()).throw(OSError("lock failed")),
    )

    monkeypatch.setitem(sys.modules, "fcntl", fake_fcntl)
    monkeypatch.setattr(evidence_module, "_open_posix_lock_descriptor", lambda _root: 103)
    monkeypatch.setattr(
        os,
        "fstat",
        lambda _descriptor: SimpleNamespace(
            st_dev=1,
            st_ino=2,
            st_mode=stat.S_IFDIR,
        ),
    )
    monkeypatch.setattr(os, "close", lambda descriptor: close_calls.append(descriptor))

    with pytest.raises(EvidenceIntegrityError, match="cannot acquire"):
        evidence_module._acquire_posix_event_lock(71)

    assert close_calls == [103]


@pytest.mark.skipif(os.name != "nt", reason="requires Windows mutex cleanup")
def test_windows_event_lock_reports_lost_close_status() -> None:
    class Kernel32:
        @staticmethod
        def ReleaseMutex(_handle: object) -> int:
            return 1

        @staticmethod
        def CloseHandle(_handle: object) -> int:
            return 0

    with pytest.raises(EvidenceIntegrityError, match="close case event lock"):
        evidence_module._release_windows_event_lock((Kernel32(), 17))


@pytest.mark.skipif(os.name != "nt", reason="requires Windows rename substitution")
def test_record_attempt_rejects_cross_case_directory_substitution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = ValidatedCaseWorkspace(tmp_path / "tool", tmp_path / "02_CAE")
    case_a = workspace.create_case("case-a")
    case_b = workspace.create_case("case-b")
    intent = IntentContract(engineering_question="Attempt authority")
    store_a = EvidenceStore(case_a, intent)
    store_b = EvidenceStore(case_b, intent)
    foreign_record = store_b.record_attempt("attempt-1", {"owner": "case-b"})
    relative = Path("90_Temporary") / "attempts" / "attempt-1"
    foreign = case_b.case_root / relative
    displaced = case_a.temporary_root / "attempts" / "attempt-1-owned"
    original_directory = workspace_module._ExactCaseTransaction._directory
    substituted = False
    blocked = False

    def substitute_before_open(self: Any, parts: tuple[str, ...]) -> Any:
        nonlocal blocked, substituted
        target = self.root.joinpath(*parts)
        if not substituted and target == case_a.case_root / relative and target.exists():
            try:
                target.rename(displaced)
            except OSError:
                blocked = True
            else:
                foreign.rename(target)
                substituted = True
        return original_directory(self, parts)

    monkeypatch.setattr(
        workspace_module._ExactCaseTransaction,
        "_directory",
        substitute_before_open,
    )

    try:
        owned_record = store_a.record_attempt("attempt-1", {"owner": "case-a"})
    except (WorkspaceBoundaryError, EvidenceIntegrityError):
        owned_record = None

    assert blocked or (substituted and owned_record is None)
    if blocked:
        assert owned_record is not None
        persisted = json.loads(
            (case_a.case_root / relative / "ATTEMPT.json").read_text(encoding="utf-8")
        )
        foreign_persisted = json.loads(foreign.joinpath("ATTEMPT.json").read_text(encoding="utf-8"))
        assert persisted["sha256"] == owned_record["sha256"]
        assert persisted["payload"] == {"owner": "case-a"}
        assert foreign_persisted["sha256"] == foreign_record["sha256"]
        assert foreign_persisted["payload"] == {"owner": "case-b"}
        assert not displaced.exists()
    else:
        persisted = json.loads(
            (case_a.case_root / relative / "ATTEMPT.json").read_text(encoding="utf-8")
        )
        assert persisted["sha256"] == foreign_record["sha256"]
        assert persisted["payload"] == {"owner": "case-b"}
        assert tuple(displaced.iterdir()) == ()


@pytest.mark.parametrize("operation", ["event", "attempt"])
def test_reopen_rolls_forward_acknowledged_event_after_manifest_interruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    _, case, intent = make_case(tmp_path)
    store = EvidenceStore(case, intent)
    original_replace = workspace_module._ExactCaseTransaction.replace_bytes
    interrupted = False

    def interrupt_manifest_once(
        self: Any,
        relative_path: str | Path,
        data: bytes,
    ) -> None:
        nonlocal interrupted
        if not interrupted and Path(relative_path).as_posix() == "CASE_MANIFEST.json":
            interrupted = True
            raise OSError("simulated manifest interruption")
        original_replace(self, relative_path, data)

    with monkeypatch.context() as patch:
        patch.setattr(
            workspace_module._ExactCaseTransaction,
            "replace_bytes",
            interrupt_manifest_once,
        )
        with pytest.raises((OSError, EvidenceIntegrityError)):
            if operation == "event":
                store.append_event("ordinary_acknowledged", {"status": "durable"})
            else:
                store.record_attempt("attempt-1", {"status": "durable"})

    acknowledged = json.loads(store.events_path.read_text(encoding="utf-8").splitlines()[-1])
    reopened = EvidenceStore.open(case)

    assert interrupted
    assert reopened.manifest["events"]["last_sha256"] == acknowledged["sha256"]
    assert reopened.manifest["events"]["count"] == 1
    if operation == "attempt":
        assert reopened.manifest["attempts"][0]["attempt_id"] == "attempt-1"
    assert not tuple(case.case_root.glob(".CASE_MANIFEST.json.*"))


def test_reopen_recovers_attempt_interrupted_before_recovery_backed_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, case, intent = make_case(tmp_path)
    store = EvidenceStore(case, intent)
    original_append = workspace_module._ExactCaseTransaction.append_bytes
    interrupted = False

    def interrupt_attempt_event_once(
        self: Any,
        relative_path: str | Path,
        data: bytes,
    ) -> None:
        nonlocal interrupted
        if not interrupted and Path(relative_path).as_posix() == EVENTS_FILE:
            interrupted = True
            raise OSError("simulated attempt event interruption")
        original_append(self, relative_path, data)

    with monkeypatch.context() as patch:
        patch.setattr(
            workspace_module._ExactCaseTransaction,
            "append_bytes",
            interrupt_attempt_event_once,
        )
        with pytest.raises(EvidenceIntegrityError):
            store.record_attempt("attempt-1", {"status": "prepared"})

    assert interrupted
    assert store.events_path.read_text(encoding="utf-8") == ""
    assert (case.temporary_root / "attempts" / "attempt-1" / "ATTEMPT.json").is_file()

    reopened = EvidenceStore.open(case)
    events = [
        json.loads(line) for line in reopened.events_path.read_text(encoding="utf-8").splitlines()
    ]

    assert len(events) == 1
    assert events[0]["event_type"] == "attempt_recorded"
    assert events[0]["payload"]["attempt_id"] == "attempt-1"
    assert reopened.manifest["attempts"] == [
        {
            "attempt_id": "attempt-1",
            "path": "90_Temporary/attempts/attempt-1/ATTEMPT.json",
            "sha256": events[0]["payload"]["sha256"],
        }
    ]


def test_attempt_recovery_marker_publication_failure_is_evidence_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, case, intent = make_case(tmp_path)
    store = EvidenceStore(case, intent)
    original_replace = workspace_module._ExactCaseTransaction.replace_bytes
    interruption_attempted = False

    def fail_marker_publication(
        self: Any,
        relative_path: str | Path,
        data: bytes,
    ) -> None:
        nonlocal interruption_attempted
        relative = Path(relative_path).as_posix()
        if relative == "90_Temporary/event-recovery.json":
            interruption_attempted = True
            raise OSError("injected recovery marker publication failure")
        original_replace(self, relative_path, data)

    with monkeypatch.context() as patch:
        patch.setattr(
            workspace_module._ExactCaseTransaction,
            "replace_bytes",
            fail_marker_publication,
        )
        with pytest.raises(
            EvidenceIntegrityError,
            match="event log is missing or unreadable",
        ):
            store.record_attempt("attempt-1", {"status": "prepared"})

    reopened = EvidenceStore.open(case)

    assert interruption_attempted
    assert not (case.temporary_root / "attempts" / "attempt-1").exists()
    assert reopened.events_path.read_bytes() == b""
    assert reopened.manifest["events"]["count"] == 0
    assert reopened.manifest["attempts"] == []


@pytest.mark.skipif(os.name != "nt", reason="requires Windows exact directory cleanup")
def test_attempt_recovery_after_directory_uses_live_same_process_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, case, intent = make_case(tmp_path)
    store = EvidenceStore(case, intent)
    original_replace = workspace_module._ExactCaseTransaction.replace_bytes
    interruption_attempted = False

    def fail_attempt_file_publication(
        self: Any,
        relative_path: str | Path,
        data: bytes,
    ) -> None:
        nonlocal interruption_attempted
        if Path(relative_path).as_posix().endswith("/ATTEMPT.json"):
            interruption_attempted = True
            raise OSError("injected interruption before attempt file durability")
        original_replace(self, relative_path, data)

    with monkeypatch.context() as patch:
        patch.setattr(
            workspace_module._ExactCaseTransaction,
            "replace_bytes",
            fail_attempt_file_publication,
        )
        with pytest.raises(
            OSError,
            match="injected interruption before attempt file durability",
        ):
            store.record_attempt("attempt-1", {"status": "prepared"})

    reopened = EvidenceStore.open(case)

    assert interruption_attempted
    assert not (case.temporary_root / "attempts" / "attempt-1").exists()
    assert reopened.events_path.read_bytes() == b""
    assert reopened.manifest["events"]["count"] == 0
    assert reopened.manifest["attempts"] == []


def test_fresh_process_reopen_retains_empty_attempt_and_marker_without_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, case, intent = make_case(tmp_path)
    store = EvidenceStore(case, intent)
    original_replace = workspace_module._ExactCaseTransaction.replace_bytes

    def fail_attempt_file_publication(
        self: Any,
        relative_path: str | Path,
        data: bytes,
    ) -> None:
        if Path(relative_path).as_posix().endswith("/ATTEMPT.json"):
            raise OSError("injected interruption before attempt file durability")
        original_replace(self, relative_path, data)

    with monkeypatch.context() as patch:
        patch.setattr(
            workspace_module._ExactCaseTransaction,
            "replace_bytes",
            fail_attempt_file_publication,
        )
        with pytest.raises(
            OSError,
            match="injected interruption before attempt file durability",
        ):
            store.record_attempt("attempt-1", {"status": "prepared"})

    attempt_root = case.temporary_root / "attempts" / "attempt-1"
    marker_path = case.temporary_root / "event-recovery.json"
    case_stamp = workspace_module._registered_case_stamp(case)
    claim_key = (case.case_root, case_stamp, "attempt-1")
    claim = workspace_module._ATTEMPT_ROOT_STAMPS[claim_key]
    workspace_module._release_attempt_root_claim(claim_key, claim)

    with pytest.raises(
        EvidenceIntegrityError,
        match="empty pending attempt directory could not be recovered",
    ):
        EvidenceStore.open(case)

    assert claim_key not in workspace_module._ATTEMPT_ROOT_STAMPS
    assert attempt_root.is_dir()
    assert marker_path.is_file()


def test_reopen_rejects_invalid_enhanced_attempt_marker_before_directory(
    tmp_path: Path,
) -> None:
    _, case, intent = make_case(tmp_path)
    EvidenceStore(case, intent)
    marker = {
        "schema_version": evidence_module.SCHEMA_VERSION,
        "case_id": case.case_id,
        "sequence": 1,
        "previous_sha256": None,
        "event_sha256": "0" * 64,
        "attempt_id": "attempt-1",
        "attempt_sha256": "1" * 64,
    }
    marker_path = case.temporary_root / "event-recovery.json"
    marker_path.write_bytes(
        (json.dumps(marker, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
    )

    with pytest.raises(
        EvidenceIntegrityError,
        match="pending attempt recovery event binding is invalid",
    ):
        EvidenceStore.open(case)

    assert marker_path.is_file()
    assert not (case.temporary_root / "attempts" / "attempt-1").exists()


def test_reopen_repairs_one_canonical_partial_attempt_event_idempotently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, case, intent = make_case(tmp_path)
    store = EvidenceStore(case, intent)
    original_append = workspace_module._ExactCaseTransaction.append_bytes
    appended_prefix = b""

    def append_strict_prefix_then_fail(
        self: Any,
        relative_path: str | Path,
        data: bytes,
    ) -> None:
        nonlocal appended_prefix
        if not appended_prefix and Path(relative_path).as_posix() == EVENTS_FILE:
            appended_prefix = data[: len(data) // 2]
            assert appended_prefix and len(appended_prefix) < len(data)
            original_append(self, relative_path, appended_prefix)
            raise OSError("injected partial event append")
        original_append(self, relative_path, data)

    with monkeypatch.context() as patch:
        patch.setattr(
            workspace_module._ExactCaseTransaction,
            "append_bytes",
            append_strict_prefix_then_fail,
        )
        with pytest.raises(EvidenceIntegrityError):
            store.record_attempt("attempt-1", {"status": "prepared"})

    assert store.events_path.read_bytes() == appended_prefix

    reopened = EvidenceStore.open(case)
    repaired = reopened.events_path.read_bytes()
    events = [json.loads(line) for line in repaired.decode("utf-8").splitlines()]
    expected = (
        json.dumps(
            events[0],
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )

    assert len(events) == 1
    assert events[0]["event_type"] == "attempt_recorded"
    assert events[0]["payload"]["attempt_id"] == "attempt-1"
    assert repaired == expected
    assert reopened.manifest["events"]["count"] == 1

    reopened_again = EvidenceStore.open(case)
    assert reopened_again.events_path.read_bytes() == repaired
    assert reopened_again.manifest == reopened.manifest


@pytest.mark.parametrize(
    "operation",
    [
        "append",
        "revise",
        "attempt",
        "artifact",
        "verification",
        "artifact_source",
        "verification_source",
    ],
)
def test_public_transactions_reject_case_or_source_substitution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    _, case, intent = make_case(tmp_path)
    attempted = False

    def replace_source(source: Path) -> bool:
        nonlocal attempted
        attempted = True
        try:
            source.rename(source.with_suffix(".displaced"))
        except OSError:
            return True
        source.write_text("foreign", encoding="utf-8")  # pragma: no cover - POSIX gate
        return True

    source_attack = operation.endswith("_source")
    action = operation.removesuffix("_source")
    validator = replace_source if source_attack else lambda _: True
    manager, authority = make_authority(validator=validator)
    store = EvidenceStore(case, intent, manager)
    if action == "verification":
        store.record_attempt("attempt-1")
    prefix = "90_Temporary/attempts/attempt-1" if action == "verification" else "90_Temporary"
    operand = case.write_text(f"{prefix}/derived.feb", "owned")
    original_load = EvidenceStore._load_and_validate

    def substitute_after_validation(
        self: EvidenceStore,
        supplied_intent: IntentContract | None,
    ) -> None:
        nonlocal attempted
        original_load(self, supplied_intent)
        attempted = True
        try:
            case.case_root.rename(tmp_path / "displaced-case")
        except OSError:
            return
        case.case_root.mkdir()  # pragma: no cover - POSIX gate
        case.case_root.joinpath("foreign.txt").write_text("foreign", encoding="utf-8")

    if operation == "artifact_source":
        original_file = EvidenceStore._safe_case_file

        def replace_after_snapshot(self: EvidenceStore, path: str | Path) -> Path:
            result = original_file(self, path)
            replace_source(operand)
            return result

        monkeypatch.setattr(EvidenceStore, "_safe_case_file", replace_after_snapshot)
    elif not source_attack:
        monkeypatch.setattr(EvidenceStore, "_load_and_validate", substitute_after_validation)
    operations: dict[str, Callable[[], object]] = {
        "append": lambda: store.append_event("exact_append"),
        "revise": lambda: store.revise_intent(IntentContract(engineering_question="revised")),
        "attempt": lambda: store.record_attempt("attempt-2"),
        "artifact": lambda: store.record_artifact(operand),
        "verification": lambda: store.record_verification(
            operand, "02_Model/derived.feb", attempt_id="attempt-1", authority=authority
        ),
    }
    if os.name == "nt" or not source_attack:
        operations[action]()
    else:  # pragma: no cover - POSIX gate
        with pytest.raises((EvidenceIntegrityError, WorkspaceBoundaryError)):
            operations[action]()
    assert attempted
    if source_attack:
        persisted = case.case_root.joinpath("CASE_MANIFEST.json").read_text(encoding="utf-8")
        persisted += case.case_root.joinpath(EVENTS_FILE).read_text(encoding="utf-8")
        assert hashlib.sha256(b"foreign").hexdigest() not in persisted
    if os.name != "nt" and not source_attack:  # pragma: no cover - POSIX gate
        assert tuple(path.name for path in case.case_root.iterdir()) == ("foreign.txt",)
