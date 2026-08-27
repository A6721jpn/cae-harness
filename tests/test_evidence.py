from __future__ import annotations

import hashlib
import inspect
import json
import os
import pickle
import threading
import time
from copy import copy, deepcopy
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

import febio_cae_harness.evidence as evidence_module
from febio_cae_harness.contracts import IntentContract
from febio_cae_harness.evidence import (
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

    with pytest.raises(EvidenceIntegrityError):
        store.record_verification(
            source,
            Path("02_Model") / "derived.feb",
            attempt_id="attempt-1",
        )
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
    original_append_text = CaseWorkspace.append_text
    state_lock = threading.Lock()
    started = threading.Barrier(2)
    active_event_appends = 0
    overlapped_event_appends = False

    def delayed_append(
        self: CaseWorkspace,
        relative_path: str | Path,
        text: str,
        *,
        encoding: str = "utf-8",
    ) -> Path:
        nonlocal active_event_appends, overlapped_event_appends
        if Path(relative_path).as_posix() == EVENTS_FILE:
            with state_lock:
                active_event_appends += 1
                overlapped_event_appends = overlapped_event_appends or active_event_appends > 1
            try:
                time.sleep(0.05)
                return original_append_text(self, relative_path, text, encoding=encoding)
            finally:
                with state_lock:
                    active_event_appends -= 1
        return original_append_text(self, relative_path, text, encoding=encoding)

    monkeypatch.setattr(CaseWorkspace, "append_text", delayed_append)

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
    assert not errors
    assert not overlapped_event_appends
    assert sorted(cast(int, event["sequence"]) for event in events) == [1, 2]
    assert store_a.reopen() is store_a
    assert store_b.reopen() is store_b


def test_record_verification_persists_bound_evidence_and_issues_opaque_receipt(
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

    assert isinstance(receipt, VerificationReceipt)
    assert repr(receipt) == "VerificationReceipt(<opaque>)"
    with pytest.raises(EvidenceIntegrityError):
        store.promote_verified({"evidence_digest": str(receipt)})  # type: ignore[arg-type]
    record = json.loads(store.events_path.read_text(encoding="utf-8").splitlines()[-1])
    assert record["event_type"] == "artifact_verified"
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
    assert str(receipt).endswith(payload["evidence_digest"])


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


def test_artifact_verified_cannot_be_fabricated_through_generic_event_api(
    tmp_path: Path,
) -> None:
    _, case, intent = make_case(tmp_path)
    store = EvidenceStore(case, intent)

    with pytest.raises(EvidenceIntegrityError):
        store.append_event("artifact_verified", {})
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
    assert receipt is not None

    with pytest.raises(EvidenceIntegrityError):
        store.record_verification(
            source,
            Path("02_Model") / "derived.feb",
            attempt_id="attempt-1",
            authority=authority,
        )
    source.write_text("mutated", encoding="utf-8")
    with pytest.raises(EvidenceIntegrityError):
        store.promote_verified(receipt)


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
    assert receipt is not None

    workspace_b = ValidatedCaseWorkspace(tmp_path / "tool-b", tmp_path / "02_CAE-b")
    case_b = workspace_b.create_case("case-a")
    store_b = EvidenceStore(case_b, IntentContract(engineering_question="q"))
    with pytest.raises(EvidenceIntegrityError):
        store_b.promote_verified(receipt)


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
    assert receipt is not None

    assert store.promote_verified(receipt).read_text(encoding="utf-8") == "derived"
    with pytest.raises(FileExistsError):
        store.promote_verified(receipt)


def test_promotion_consumes_receipt_before_copy_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
    assert receipt is not None

    def fail_copy(*args: object, **kwargs: object) -> Path:
        del args, kwargs
        raise OSError("simulated copy failure")

    monkeypatch.setattr(CaseWorkspace, "_copy_create_new", fail_copy)
    with pytest.raises(OSError, match="simulated copy failure"):
        store.promote_verified(receipt)

    event_lines = store.events_path.read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in event_lines]
    assert records[-1]["event_type"] == "artifact_promotion_consumed"
    assert records[-1]["previous_sha256"] == records[-2]["sha256"]
    assert records[-1]["payload"] == records[-2]["payload"]
    assert str(receipt).endswith(records[-1]["payload"]["evidence_digest"])
    assert not (case.case_root / "02_Model" / "derived.feb").exists()

    monkeypatch.undo()
    with pytest.raises(EvidenceIntegrityError):
        store.promote_verified(receipt)


def test_consumed_receipt_cannot_replay_after_destination_is_removed(tmp_path: Path) -> None:
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
    assert receipt is not None

    assert store.promote_verified(receipt) == destination
    destination.write_text("tampered", encoding="utf-8")
    with pytest.raises(EvidenceIntegrityError):
        store.promote_verified(receipt)
    destination.unlink()

    with pytest.raises(EvidenceIntegrityError):
        store.promote_verified(receipt)
