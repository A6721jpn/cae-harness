from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import math
import os
import pickle
import stat
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import copy, deepcopy
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

from febio_cae_harness.contracts import IntentContract
from febio_cae_harness.evidence import EvidenceStore, IntentSnapshotAuthority
from febio_cae_harness.solver.runtime import FebioRuntimeDiagnostic, probe_febio
from febio_cae_harness.workspace import AttemptWorkspace, ValidatedCaseWorkspace

_REAL_POPEN = subprocess.Popen


@dataclass(frozen=True)
class _Context:
    attempt: AttemptWorkspace
    intent: IntentSnapshotAuthority
    runtime: FebioRuntimeDiagnostic
    input_path: Path
    store: EvidenceStore


def _execution_module() -> ModuleType:
    try:
        module = importlib.import_module("febio_cae_harness.solver.execution")
    except ModuleNotFoundError as error:
        pytest.fail(f"execution authority module is missing: {error}")
    required = {
        "ExecutionAuthority",
        "ExecutionAuthorityError",
        "issue_execution_authority",
        "reopen_execution_authority",
        "validate_execution_authority",
    }
    missing = sorted(name for name in required if not hasattr(module, name))
    assert not missing, f"execution authority API is incomplete: {missing}"
    return module


def _patch_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    class CompletedProcess:
        returncode = 0

        def communicate(self, input: bytes, timeout: float) -> tuple[bytes, bytes]:
            assert input == b"quit\n"
            assert timeout > 0
            return b"version 4.12.0\n", b""

    monkeypatch.setattr(
        "febio_cae_harness.solver.runtime.subprocess.Popen",
        lambda command, **kwargs: CompletedProcess(),
    )


def _context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    case_id: str = "case-a",
    attempt_id: str = "attempt-a",
    input_name: str = "model.feb",
    runtime_name: str = "fake-febio",
) -> _Context:
    tool_root = tmp_path / "tool"
    tool_root.mkdir(parents=True)
    manager = ValidatedCaseWorkspace(tool_root, tmp_path / "02_CAE")
    case = manager.create_case(case_id)
    store = EvidenceStore(case, IntentContract(engineering_question="synthetic execution"))
    store.record_attempt(attempt_id)
    attempt = AttemptWorkspace._from_manager(
        case,
        attempt_id,
        case.temporary_root / "attempts" / attempt_id,
    )
    input_path = attempt.write_text(input_name, "synthetic completed FEB")
    executable = tmp_path / runtime_name
    executable.write_bytes(b"synthetic FEBio executable")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    _patch_probe(monkeypatch)
    runtime = probe_febio(executable)
    return _Context(attempt, store.issue_intent_snapshot(), runtime, input_path, store)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _record_path(context: _Context) -> Path:
    return context.attempt.root / "execution.json"


def _issue(context: _Context, **kwargs: object) -> Any:
    module = _execution_module()
    return module.issue_execution_authority(
        context.attempt,
        context.intent,
        context.runtime,
        context.input_path,
        **kwargs,
    )


def _reopen(context: _Context) -> Any:
    module = _execution_module()
    return module.reopen_execution_authority(
        context.attempt,
        context.intent,
        context.runtime,
    )


def test_execution_authority_api_has_no_caller_selected_labels_or_outputs() -> None:
    module = _execution_module()
    issue_parameters = inspect.signature(module.issue_execution_authority).parameters
    reopen_parameters = inspect.signature(module.reopen_execution_authority).parameters

    assert tuple(issue_parameters) == (
        "attempt_workspace",
        "intent_snapshot",
        "runtime_diagnostic",
        "input_path",
        "requested_fields",
        "expected_steps",
        "expected_final_time",
    )
    assert tuple(reopen_parameters) == (
        "attempt_workspace",
        "intent_snapshot",
        "runtime_diagnostic",
    )
    forbidden = {
        "case_id",
        "case_root",
        "attempt_id",
        "intent_sha256",
        "runtime_path",
        "log_path",
        "xplt_path",
        "execution_path",
    }
    assert not forbidden.intersection(issue_parameters)
    assert not forbidden.intersection(reopen_parameters)


def test_issue_writes_one_canonical_deterministic_record_and_reopen_is_fresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch)
    module = _execution_module()

    authority = _issue(
        context,
        requested_fields=("displacement", "stress"),
        expected_steps=2,
        expected_final_time=1.25,
    )
    record_path = _record_path(context)
    original = record_path.read_bytes()
    record = json.loads(original)
    record_hash = record.pop("record_sha256")

    assert original.endswith(b"\n")
    assert original == _canonical({**record, "record_sha256": record_hash}) + b"\n"
    assert record_hash == hashlib.sha256(_canonical(record)).hexdigest()
    assert record == {
        "case": {
            "attempt_id": "attempt-a",
            "case_id": "case-a",
            "root": os.path.normcase(os.path.realpath(context.attempt.root.parents[2])),
        },
        "expectations": {"expected_final_time": 1.25, "expected_steps": 2},
        "input": {
            "identity": {
                "device": context.input_path.stat().st_dev,
                "inode": context.input_path.stat().st_ino,
                "mtime_ns": context.input_path.stat().st_mtime_ns,
                "nlink": 1,
            },
            "relative_path": "model.feb",
            "sha256": hashlib.sha256(context.input_path.read_bytes()).hexdigest(),
            "size": context.input_path.stat().st_size,
        },
        "intent_sha256": context.intent.intent_sha256,
        "outputs": {"log": "model.log", "xplt": "model.xplt"},
        "requested_fields": ["displacement", "stress"],
        "runtime": {
            "path": os.path.normcase(os.path.realpath(context.runtime.path)),
            "sha256": context.runtime.sha256,
            "size": context.runtime.size,
            "version": context.runtime.version,
        },
        "schema": "febio-cae-execution",
        "version": 1,
    }
    assert type(authority) is module.ExecutionAuthority
    assert authority.record_path == record_path
    assert authority.record_sha256 == record_hash
    assert authority.case_id == "case-a"
    assert authority.attempt_id == "attempt-a"
    assert authority.input_path == context.input_path
    assert authority.log_path == context.attempt.root / "model.log"
    assert authority.xplt_path == context.attempt.root / "model.xplt"
    assert authority.requested_fields == ("displacement", "stress")
    assert authority.expected_steps == 2
    assert authority.expected_final_time == 1.25
    assert module.validate_execution_authority(authority) is authority

    reopened = _reopen(context)
    assert type(reopened) is module.ExecutionAuthority
    assert reopened is not authority
    assert reopened.record_sha256 == authority.record_sha256
    assert reopened.input_sha256 == authority.input_sha256
    assert record_path.read_bytes() == original


def test_issue_rejects_duplicate_and_differing_replay_without_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch)
    module = _execution_module()
    _issue(context, requested_fields=("displacement",))
    record_path = _record_path(context)
    original = record_path.read_bytes()

    with pytest.raises(module.ExecutionAuthorityError, match="already exists|duplicate"):
        _issue(context, requested_fields=("displacement",))
    with pytest.raises(module.ExecutionAuthorityError, match="already exists|conflict|duplicate"):
        _issue(context, requested_fields=("stress",))

    assert record_path.read_bytes() == original


def test_concurrent_create_has_one_winner_and_never_overwrites(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch)
    module = _execution_module()
    contenders = 8
    barrier = threading.Barrier(contenders)

    def compete(index: int) -> tuple[str, str | None]:
        barrier.wait(timeout=10.0)
        try:
            authority = module.issue_execution_authority(
                context.attempt,
                context.intent,
                context.runtime,
                context.input_path,
                requested_fields=(f"field-{index}",),
            )
        except module.ExecutionAuthorityError:
            return "lost", None
        return "won", authority.record_sha256

    with ThreadPoolExecutor(max_workers=contenders) as executor:
        outcomes = list(executor.map(compete, range(contenders)))

    winners = [digest for outcome, digest in outcomes if outcome == "won"]
    assert len(winners) == 1
    raw = _record_path(context).read_bytes()
    record = json.loads(raw)
    assert record["record_sha256"] == winners[0]
    assert raw == _canonical(record) + b"\n"
    assert _reopen(context).record_sha256 == winners[0]


@pytest.mark.parametrize("mode", ["tampered", "truncated", "noncanonical"])
def test_reopen_rejects_tampered_truncated_and_noncanonical_record_without_mutating_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    context = _context(tmp_path, monkeypatch)
    module = _execution_module()
    _issue(context)
    record_path = _record_path(context)
    record = json.loads(record_path.read_bytes())
    if mode == "tampered":
        record["case"]["case_id"] = "forged"
        damaged = _canonical(record) + b"\n"
    elif mode == "truncated":
        damaged = record_path.read_bytes()[:23]
    else:
        damaged = json.dumps(record, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    record_path.write_bytes(damaged)

    with pytest.raises(module.ExecutionAuthorityError, match="record|canonical|digest|JSON"):
        _reopen(context)

    assert record_path.read_bytes() == damaged


def test_json_labels_and_recomputed_hash_do_not_create_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch)
    module = _execution_module()
    _issue(context)
    path = _record_path(context)
    record = json.loads(path.read_bytes())
    record["case"]["case_id"] = "foreign-case"
    payload = dict(record)
    payload.pop("record_sha256")
    record["record_sha256"] = hashlib.sha256(_canonical(payload)).hexdigest()
    forged = _canonical(record) + b"\n"
    path.write_bytes(forged)

    with pytest.raises(module.ExecutionAuthorityError, match="case|authority|binding"):
        _reopen(context)
    assert path.read_bytes() == forged


@pytest.mark.parametrize("drift", ["digest", "replacement"])
def test_input_digest_or_exact_identity_drift_invalidates_authority_and_reopen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
) -> None:
    context = _context(tmp_path, monkeypatch)
    module = _execution_module()
    authority = _issue(context)
    record_path = _record_path(context)
    record_bytes = record_path.read_bytes()
    if drift == "digest":
        context.input_path.write_bytes(b"mutated synthetic FEB input")
    else:
        original = context.input_path.read_bytes()
        displaced = context.input_path.with_suffix(".displaced")
        context.input_path.replace(displaced)
        context.input_path.write_bytes(original)

    with pytest.raises(module.ExecutionAuthorityError, match="input|identity|digest|authority"):
        module.validate_execution_authority(authority)
    with pytest.raises(module.ExecutionAuthorityError, match="input|identity|digest|authority"):
        _reopen(context)

    assert record_path.read_bytes() == record_bytes


def test_issue_detects_input_change_during_record_construction_and_leaves_no_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch)
    module = _execution_module()
    original = module._canonical_payload_bytes
    changed = False

    def mutate_during_build(value: object) -> bytes:
        nonlocal changed
        encoded = cast(bytes, original(value))
        if not changed and isinstance(value, dict) and value.get("schema") == "febio-cae-execution":
            changed = True
            context.input_path.write_bytes(b"concurrent replacement bytes")
        return encoded

    monkeypatch.setattr(module, "_canonical_payload_bytes", mutate_during_build)

    with pytest.raises(module.ExecutionAuthorityError, match="input|changed|create|record"):
        _issue(context)

    assert changed
    assert not _record_path(context).exists()


def test_foreign_fabricated_and_mutated_authorities_are_rejected_before_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _execution_module()
    local = _context(tmp_path / "local", monkeypatch, case_id="same-case")
    foreign = _context(
        tmp_path / "foreign",
        monkeypatch,
        case_id="same-case",
        runtime_name="foreign-febio",
    )

    with pytest.raises(module.ExecutionAuthorityError, match="case|foreign|binding|authority"):
        module.issue_execution_authority(
            local.attempt,
            foreign.intent,
            local.runtime,
            local.input_path,
        )

    forged_attempt = object.__new__(AttemptWorkspace)
    object.__setattr__(forged_attempt, "case_id", local.attempt.case_id)
    object.__setattr__(forged_attempt, "attempt_id", local.attempt.attempt_id)
    object.__setattr__(forged_attempt, "root", local.attempt.root)
    forged_intent = object.__new__(IntentSnapshotAuthority)
    forged_runtime = FebioRuntimeDiagnostic(
        Path(sys.executable).absolute(),
        "a" * 64,
        1,
        "4.12.0",
    )
    for attempt, intent, runtime in (
        (forged_attempt, local.intent, local.runtime),
        (local.attempt, forged_intent, local.runtime),
        (local.attempt, local.intent, forged_runtime),
    ):
        with pytest.raises(
            module.ExecutionAuthorityError, match="authority|issued|live|registered"
        ):
            module.issue_execution_authority(attempt, intent, runtime, local.input_path)

    assert not _record_path(local).exists()


@pytest.mark.parametrize("stale", ["attempt", "intent", "runtime"])
def test_stale_or_mutated_bound_authority_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stale: str,
) -> None:
    context = _context(tmp_path, monkeypatch)
    module = _execution_module()
    authority = _issue(context)
    original = _record_path(context).read_bytes()
    if stale == "attempt":
        object.__setattr__(context.attempt, "attempt_id", "mutated-attempt")
    elif stale == "intent":
        context.store.revise_intent(IntentContract(engineering_question="revised synthetic"))
    else:
        object.__setattr__(context.runtime, "version", "4.12.1")

    with pytest.raises(
        module.ExecutionAuthorityError, match="authority|binding|stale|modified|live"
    ):
        module.validate_execution_authority(authority)

    assert _record_path(context).read_bytes() == original


def test_reopen_requires_the_current_matching_probe_issued_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path / "local", monkeypatch)
    foreign = _context(
        tmp_path / "foreign",
        monkeypatch,
        case_id="foreign-case",
        runtime_name="other-febio",
    )
    module = _execution_module()
    _issue(context)
    original = _record_path(context).read_bytes()

    with pytest.raises(module.ExecutionAuthorityError, match="runtime|identity|record"):
        module.reopen_execution_authority(context.attempt, context.intent, foreign.runtime)

    assert _record_path(context).read_bytes() == original


@pytest.mark.parametrize(
    "kwargs",
    [
        {"requested_fields": ("",)},
        {"requested_fields": ("stress", "stress")},
        {"expected_steps": -1},
        {"expected_steps": True},
        {"expected_final_time": math.inf},
        {"expected_final_time": math.nan},
    ],
)
def test_issue_rejects_invalid_requested_fields_and_nonfinite_expectations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kwargs: dict[str, object],
) -> None:
    context = _context(tmp_path, monkeypatch)
    module = _execution_module()

    with pytest.raises(module.ExecutionAuthorityError, match="field|step|finite|expectation"):
        _issue(context, **kwargs)

    assert not _record_path(context).exists()


def test_issue_rejects_outside_nonregular_hardlinked_and_reparse_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _execution_module()

    outside_context = _context(tmp_path / "outside-case", monkeypatch)
    outside = tmp_path / "outside.feb"
    outside.write_bytes(b"outside synthetic FEB")
    with pytest.raises(module.ExecutionAuthorityError, match="inside|attempt|input"):
        module.issue_execution_authority(
            outside_context.attempt,
            outside_context.intent,
            outside_context.runtime,
            outside,
        )

    directory_context = _context(tmp_path / "directory-case", monkeypatch)
    directory_input = directory_context.attempt.root / "directory-input"
    directory_input.mkdir()
    with pytest.raises(module.ExecutionAuthorityError, match="regular|input|file"):
        module.issue_execution_authority(
            directory_context.attempt,
            directory_context.intent,
            directory_context.runtime,
            directory_input,
        )

    hardlink_context = _context(tmp_path / "hardlink-case", monkeypatch)
    hardlink = hardlink_context.attempt.root / "hardlinked.feb"
    os.link(hardlink_context.input_path, hardlink)
    with pytest.raises(module.ExecutionAuthorityError, match="hard|link|regular|input"):
        module.issue_execution_authority(
            hardlink_context.attempt,
            hardlink_context.intent,
            hardlink_context.runtime,
            hardlink,
        )

    alias_context = _context(tmp_path / "alias-case", monkeypatch)
    foreign_directory = tmp_path / "foreign-directory"
    foreign_directory.mkdir()
    (foreign_directory / "aliased.feb").write_bytes(b"foreign synthetic FEB")
    alias = alias_context.attempt.root / "alias"
    if os.name == "nt":
        process = _REAL_POPEN(
            ["cmd", "/c", "mklink", "/J", str(alias), str(foreign_directory)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=5.0)
        finally:
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
        assert process.returncode == 0, (stdout, stderr)
    else:
        alias.symlink_to(foreign_directory, target_is_directory=True)
    with pytest.raises(module.ExecutionAuthorityError, match="alias|reparse|directory|input"):
        module.issue_execution_authority(
            alias_context.attempt,
            alias_context.intent,
            alias_context.runtime,
            alias / "aliased.feb",
        )


def test_issue_derives_outputs_and_rejects_input_or_existing_output_collisions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _execution_module()
    collision = _context(tmp_path / "input-collision", monkeypatch, input_name="model.log")
    with pytest.raises(module.ExecutionAuthorityError, match="collision|output|input"):
        _issue(collision)
    assert not _record_path(collision).exists()

    record_collision = _context(
        tmp_path / "record-collision",
        monkeypatch,
        input_name="execution.json",
    )
    with pytest.raises(module.ExecutionAuthorityError, match="collision|execution|input"):
        _issue(record_collision)

    existing = _context(tmp_path / "existing-output", monkeypatch)
    existing.attempt.write_text("model.log", "stale synthetic LOG")
    with pytest.raises(module.ExecutionAuthorityError, match="output|exists|collision|fresh"):
        _issue(existing)
    assert not _record_path(existing).exists()


def test_execution_authority_is_opaque_immutable_noncopyable_and_nonserializable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch)
    module = _execution_module()
    authority = _issue(context)

    assert repr(authority) == "ExecutionAuthority(<opaque>)"
    assert not hasattr(authority, "__dict__")
    with pytest.raises(AttributeError):
        authority.record_sha256 = "0" * 64
    with pytest.raises(AttributeError):
        del authority.record_sha256
    for operation in (copy, deepcopy, pickle.dumps):
        with pytest.raises(TypeError):
            operation(authority)
    with pytest.raises(TypeError):
        json.dumps(authority)
    with pytest.raises(TypeError):
        type("ForgedExecutionAuthority", (module.ExecutionAuthority,), {})


def test_record_replacement_with_identical_bytes_invalidates_issued_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path, monkeypatch)
    module = _execution_module()
    authority = _issue(context)
    record_sha256 = authority.record_sha256
    record_path = _record_path(context)
    original = record_path.read_bytes()
    displaced = record_path.with_suffix(".displaced")
    record_path.replace(displaced)
    record_path.write_bytes(original)

    with pytest.raises(module.ExecutionAuthorityError, match="record|identity|replaced"):
        module.validate_execution_authority(authority)

    reopened = _reopen(context)
    assert reopened.record_sha256 == record_sha256
    assert record_path.read_bytes() == original
