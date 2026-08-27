from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from febio_cae_harness.contracts import IntentContract
from febio_cae_harness.evidence import EvidenceIntegrityError, EvidenceStore
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


def test_events_are_chained_and_append_only(tmp_path: Path) -> None:
    _, case, intent = make_case(tmp_path)
    store = EvidenceStore(case, intent)

    first = store.append_event("input_collected", {"source": "synthetic"})
    second = store.append_event("intent_reviewed", {"reviewed": True})

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
    case.write_text("intent.json", "{}")

    with pytest.raises(EvidenceIntegrityError):
        EvidenceStore(case, intent)

    store = EvidenceStore(workspace.create_case("case-b"), intent)
    with pytest.raises(WorkspaceBoundaryError):
        store.record_artifact(workspace.cae_root / "outside.txt")
