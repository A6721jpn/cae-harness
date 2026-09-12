from __future__ import annotations

import gc
import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from test_persistence_authority import (
    CAD_BYTES,
    _created,
    _evidence,
    _populate_complete,
    complete_spec,
)

from febio_cae.application.service import ServiceConflictError
from febio_cae.domain import (
    AttemptRecord,
    Budget,
    CaseRevision,
    ExecutionBundle,
    FileEntry,
    RunState,
)
from febio_cae.domain.case_patch import CasePatch, CasePatchEdit
from febio_cae.domain.codec import encode_record
from febio_cae.domain.compatibility import ToolIdentity
from febio_cae.domain.partial_case_spec import PartialCaseSpec
from febio_cae.domain.ports import PortError, TrustedOwnerContext
from febio_cae.domain.selection import FaceSetRule
from febio_cae.domain.spatial import FaceId
from febio_cae.domain.units import Quantity
from febio_cae.storage.registry import CaseStorage, StorageConflictError, StorageIntegrityError


def test_h1_claim_binds_current_generation(tmp_path: Path) -> None:
    _, created, storage = _created(tmp_path)
    with pytest.raises(PortError):
        storage.claim(TrustedOwnerContext(created.case_id, "run", "attempt", 99))
    storage.claim(TrustedOwnerContext(created.case_id, "run", "attempt", 0))


def test_h1_validate_cannot_register_caller_attempt(tmp_path: Path) -> None:
    _, created, storage = _created(tmp_path)
    owner = TrustedOwnerContext(created.case_id, "run", "attempt", 0)
    storage.claim(owner)
    forged = AttemptRecord(
        "attempt", "run", created.case_id, "absent", 0, "a" * 64, RunState.RUNNING, None, ()
    )
    with pytest.raises(PortError):
        storage.validate(owner, forged)


def test_h1_unregistered_bundle_cannot_resolve_file(tmp_path: Path) -> None:
    _, created, storage = _created(tmp_path)
    entry = FileEntry("input.feb", hashlib.sha256(b"input").hexdigest(), 5, "input")
    bundle = ExecutionBundle(
        "bundle",
        created.case_id,
        "absent",
        "a" * 64,
        "b" * 64,
        "solver",
        ToolIdentity("solver", "1", "c" * 64),
        (entry,),
        ("solver",),
        str(tmp_path),
        1,
        (),
    )
    attempt = AttemptRecord(
        "attempt",
        "run",
        created.case_id,
        "absent",
        0,
        bundle.bundle_digest,
        RunState.RUNNING,
        None,
        (),
    )
    path = storage.root / f"cases/{created.case_id}/runs/run/attempts/attempt/input.feb"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"input")
    with pytest.raises(PortError):
        storage.resolve_file(entry, bundle, attempt)


def test_h5_mapping_evidence_must_resolve(tmp_path: Path) -> None:
    service, created, _ = _created(tmp_path)
    spec = complete_spec()
    primitive = spec.rigid_tool.primitive
    changed = dict(primitive.dimension_evidence)
    key = next(iter(changed))
    changed[key] = replace(changed[key], reference="missing", content_digest="0" * 64)
    spec = replace(
        spec,
        rigid_tool=replace(
            spec.rigid_tool, primitive=replace(primitive, dimension_evidence=changed)
        ),
    )
    _populate_complete(service, created, spec)
    assert service.freeze_case(created.case_id).status != "FROZEN"


def test_h6_freeze_holds_evidence_until_publication(tmp_path: Path, monkeypatch: Any) -> None:
    service, created, storage = _created(tmp_path)
    _populate_complete(service, created)
    validate = service._validate
    source = storage.root / f"cases/{created.case_id}/sources/cad.bin"

    def mutate_after_validation(case_id: str) -> Any:
        result = validate(case_id)
        assert result.status == "VALIDATED"
        child = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import pathlib,sys; p=pathlib.Path(sys.argv[1]);\n"
                    "try: p.write_bytes(b'changed')\n"
                    "except PermissionError: print('write-denied')\n"
                ),
                str(source),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert child.returncode == 0, child.stderr
        return result

    monkeypatch.setattr(service, "_validate", mutate_after_validation)
    result = service.freeze_case(created.case_id)
    assert result.status == "FROZEN"
    assert source.read_bytes() == CAD_BYTES
    assert result.revision_id is not None
    assert service.get_revision(created.case_id, result.revision_id).spec_digest


def test_h7_replayed_patch_preserves_newer_intent(tmp_path: Path) -> None:
    service, created, _ = _created(tmp_path)
    _populate_complete(service, created)
    frozen = service.freeze_case(created.case_id).revision
    assert frozen is not None
    patch = CasePatch(
        frozen.revision_id,
        frozen.spec_digest,
        (CasePatchEdit("material", frozen.spec.material, True),),
        (_evidence("material"),),
    )
    first = service.apply_patch(created.case_id, patch)
    latest = service.set_spec(
        created.case_id,
        values=PartialCaseSpec(budget=Budget(Quantity(55, "s"), 1, 1, 0, 0)),
        expected_generation=first.generation,
    )
    with pytest.raises(ServiceConflictError):
        service.apply_patch(created.case_id, patch)
    assert service.current_draft(created.case_id) == latest


def test_h8_answer_changes_only_issued_targets(tmp_path: Path) -> None:
    service, created, _ = _created(tmp_path)
    question = service.issue_question(
        created.case_id,
        target_fields=("material",),
        question_time_evidence=(_evidence("material"),),
    )
    with pytest.raises(ServiceConflictError):
        service.answer_question(
            created.case_id,
            question.question_id,
            values=PartialCaseSpec(budget=Budget(Quantity(999, "s"), 1, 1, 0, 0)),
            expected_generation=0,
            evidence=(_evidence("material"),),
        )
    assert service.current_draft(created.case_id).generation == 0
    answered = service.answer_question(
        created.case_id,
        question.question_id,
        values=PartialCaseSpec(material=complete_spec().material),
        expected_generation=0,
        evidence=(_evidence("material"),),
    )
    assert answered.values.material is not None


@pytest.mark.parametrize("mismatch", ["unit", "faces"])
def test_h9_geometry_conflicts_cannot_freeze(tmp_path: Path, mismatch: str) -> None:
    service, created, _ = _created(tmp_path)
    spec = complete_spec()
    if mismatch == "unit":
        spec = replace(spec, geometry=replace(spec.geometry, step_unit="m"))
    else:
        support = spec.support.supports[0]
        selection = support.selection
        rule = FaceSetRule(
            selection.geometry_digest,
            selection.body_id,
            selection.frame,
            (FaceId("explicit-face"),),
            _evidence("selection.rule"),
        )
        support = replace(support, selection=replace(selection, rule=rule))
        spec = replace(spec, support=replace(spec.support, supports=(support,)))
    _populate_complete(service, created, spec)
    assert service.freeze_case(created.case_id).status != "FROZEN"


def test_m1_retained_evidence_allows_partial_update(tmp_path: Path) -> None:
    service, created, _ = _created(tmp_path)
    first = service.set_spec(
        created.case_id,
        values=PartialCaseSpec(),
        expected_generation=0,
        evidence=(_evidence("material"),),
    )
    second = service.set_spec(
        created.case_id,
        values=PartialCaseSpec(material=complete_spec().material),
        expected_generation=first.generation,
        evidence=first.evidence,
    )
    assert second.evidence == first.evidence
    assert second.values.material is not None


def _junction(link: Path, target: Path) -> None:
    result = subprocess.run(
        ["cmd", "/d", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("ancestor", [False, True])
def test_h4_existing_storage_rejects_substitution(tmp_path: Path, ancestor: bool) -> None:
    work = tmp_path / "owned"
    work.mkdir()
    _, created, storage = _created(work)
    replaced = work if ancestor else storage.root
    saved = tmp_path / "saved"
    gc.collect()
    replaced.rename(saved)
    _junction(replaced, saved)
    try:
        with pytest.raises((StorageIntegrityError, OSError)):
            storage.ingest_source(
                asset_id="escaped",
                source_kind="user_instruction",
                media_type="text/plain",
                content=b"bad",
            )
        with pytest.raises((StorageIntegrityError, OSError)):
            CaseStorage(storage.root)
        target = (
            saved / ("case" if ancestor else "") / f"cases/{created.case_id}/sources/escaped.bin"
        )
        assert not target.exists()
    finally:
        os.rmdir(replaced)


def test_h4_publication_directory_cannot_be_redirected(tmp_path: Path) -> None:
    service, created, storage = _created(tmp_path)
    _populate_complete(service, created)
    outside = tmp_path / "outside"
    outside.mkdir()
    parent = storage.root / f"cases/{created.case_id}/revisions/final-boundary"
    moved = parent.with_name("moved")
    redirected = False

    def attack(point: str) -> None:
        nonlocal redirected
        if point == "after_file_write":
            try:
                parent.rename(moved)
            except PermissionError:
                return
            _junction(parent, outside)
            redirected = True

    revision = CaseRevision(
        created.case_id,
        "final-boundary",
        None,
        None,
        complete_spec(),
        (_evidence("case_revision.spec"),),
    )
    storage.failure_injector = attack
    try:
        storage.register_revision_if_current(revision, expected_generation=1)
    except (StorageIntegrityError, OSError):
        pass
    finally:
        if redirected:
            os.rmdir(parent)
    assert not (outside / "revision.json").exists()
    assert not redirected, "directory handle must prevent rename while publishing"


def _worker(
    root: Path, action: str, argument: str, boundary: str = "none"
) -> subprocess.Popen[str]:
    driver = Path(__file__).parents[1] / "storage/registered_process_worker.py"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).parents[3] / "src")
    child = subprocess.Popen(
        [sys.executable, str(driver), str(root), action, argument, boundary],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    _process_records[child.pid] = root.parent / f"process-{child.pid}.jsonl"
    _record_process(child, {"event": "spawn", "argv": child.args})
    return child


_process_records: dict[int, Path] = {}


def _record_process(child: subprocess.Popen[str], record: dict[str, Any]) -> None:
    path = _process_records[child.pid]
    with path.open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps({"launcher_pid": child.pid, "time_ns": time.time_ns(), **record}) + "\n"
        )


def _event(child: subprocess.Popen[str]) -> dict[str, Any]:
    assert child.stdout is not None
    lines: queue.Queue[str] = queue.Queue()
    stream = child.stdout
    threading.Thread(target=lambda: lines.put(stream.readline()), daemon=True).start()
    line = lines.get(timeout=10)
    assert line, "worker exited before its handshake"
    result = json.loads(line)
    _record_process(child, result)
    return result


def _finish(child: subprocess.Popen[str], line: str = "continue\n") -> tuple[str, str]:
    out, err = child.communicate(line, timeout=15)
    _record_process(
        child, {"event": "reaped", "exit": child.returncode, "stdout": out, "stderr": err}
    )
    return out, err


@pytest.mark.parametrize("action", ["revision", "source"])
def test_h2_competing_publication_preserves_winner(tmp_path: Path, action: str) -> None:
    service, created, storage = _created(tmp_path)
    _populate_complete(service, created)
    first = CaseRevision(
        created.case_id, "contended", None, None, complete_spec(), (_evidence("first"),)
    )
    second = replace(first, evidence=(_evidence("second"),))
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_bytes(encode_record(first))
    b.write_bytes(encode_record(second))
    boundary = "after_file_write" if action == "revision" else "after_source_write"
    one = _worker(storage.root, action, str(a) if action == "revision" else "first", boundary)
    two: subprocess.Popen[str] | None = None
    try:
        assert _event(one)["event"] == "opened"
        assert _event(one)["event"] == boundary
        two = _worker(storage.root, action, str(b) if action == "revision" else "second")
        assert _event(two)["event"] == "opened"
        out1, err1 = _finish(one)
        out2, err2 = _finish(two)
        assert one.returncode == two.returncode == 0, (err1, err2)
        outcomes = [json.loads(out1)["result"], json.loads(out2)["result"]]
        assert sorted(outcomes) == ["conflict", "ok"]
        reopened = CaseStorage(storage.root)
        if action == "revision":
            assert (
                reopened.get_revision(created.case_id, first.revision_id).to_bytes()
                == first.to_bytes()
            )
        else:
            assert reopened.resolve_source(reopened.source_asset("contended")).content == b"first"
    finally:
        for child in (one, two):
            if child is not None and child.poll() is None:
                child.kill()
                child.communicate(timeout=10)


@pytest.mark.parametrize(
    "crash", [None, "after_prepare", "after_file_write", "after_file_replace", "after_commit"]
)
def test_h3_live_publisher_and_abandoned_recovery(tmp_path: Path, crash: str | None) -> None:
    service, created, storage = _created(tmp_path)
    _populate_complete(service, created)
    revision = CaseRevision(
        created.case_id,
        "recoverable",
        None,
        None,
        complete_spec(),
        (_evidence("case_revision.spec"),),
    )
    payload = tmp_path / "revision.json"
    payload.write_bytes(encode_record(revision))
    boundary = crash or "after_file_write"
    child = _worker(storage.root, "revision", str(payload), boundary)
    opener: subprocess.Popen[str] | None = None
    try:
        assert _event(child)["event"] == "opened"
        assert _event(child)["event"] == boundary
        opener = _worker(storage.root, "open", "none")
        out, err = _finish(opener, "")
        assert opener.returncode == 0, (out, err)
        out, err = _finish(child, "crash\n" if crash else "continue\n")
        assert child.returncode == (73 if crash else 0), (out, err)
        reopened = CaseStorage(storage.root)
        if crash in {"after_prepare", "after_file_write"}:
            with pytest.raises(StorageConflictError):
                reopened.get_revision(created.case_id, revision.revision_id)
        else:
            assert (
                reopened.get_revision(created.case_id, revision.revision_id).to_bytes()
                == revision.to_bytes()
            )
    finally:
        for process in (child, opener):
            if process is not None and process.poll() is None:
                process.kill()
                _finish(process, "")
