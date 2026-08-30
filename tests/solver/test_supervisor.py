"""Synthetic durability tests for the owned solver supervisor."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

import febio_cae_harness.solver.supervisor as supervisor_module
from febio_cae_harness.contracts import IntentContract
from febio_cae_harness.evidence import EvidenceStore
from febio_cae_harness.solver import headless as headless_module
from febio_cae_harness.solver.process_authority import ProcessAuthority
from febio_cae_harness.solver.runtime import FebioRuntimeDiagnostic, probe_febio
from febio_cae_harness.solver.supervisor import SolverSupervisor
from febio_cae_harness.solver.types import (
    SolverConfigurationError,
    SolverLaunchCapability,
    SolverLaunchError,
    SolverLaunchSpec,
    SolverOwnershipError,
    SolverState,
)
from febio_cae_harness.workspace import AttemptWorkspace, ValidatedCaseWorkspace


def _spec(tmp_path: Path) -> SolverLaunchSpec:
    input_path = tmp_path / "input.feb"
    input_path.write_text("synthetic", encoding="utf-8")
    return SolverLaunchSpec(
        executable=Path(sys.executable),
        input_path=input_path,
        attempt_root=tmp_path / "attempt",
        arguments=("-c", "import time; time.sleep(30)"),
    )


def _issued_runtime(monkeypatch: pytest.MonkeyPatch) -> FebioRuntimeDiagnostic:
    class ProbeProcess:
        returncode = 0

        def communicate(self, input: bytes, timeout: float) -> tuple[bytes, bytes]:
            del timeout
            assert input == b"quit\n"
            return b"version 4.12.0\n", b""

    with monkeypatch.context() as probe_patch:
        probe_patch.setattr(
            "febio_cae_harness.solver.runtime.subprocess.Popen",
            lambda command, **kwargs: ProbeProcess(),
        )
        return probe_febio(Path(sys.executable))


def _capability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    code: str,
) -> SolverLaunchCapability:
    manager = ValidatedCaseWorkspace(tmp_path / "tool", tmp_path / "cae")
    case = manager.create_case("case-a")
    store = EvidenceStore(case, IntentContract())
    store.record_attempt("attempt-a")
    attempt = AttemptWorkspace._from_manager(
        case,
        "attempt-a",
        case.temporary_root / "attempts" / "attempt-a",
    )
    intent = store.issue_intent_snapshot()
    input_path = attempt.write_text("input.feb", code)
    runtime = _issued_runtime(monkeypatch)
    return headless_module._issue_launch_capability(
        attempt,
        intent,
        runtime,
        input_path,
        expected_steps=None,
        expected_final_time=None,
        timeout_seconds=None,
    )


def test_start_persists_attempt_owned_process_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capability = _capability(tmp_path, monkeypatch, code="import time; time.sleep(30)")
    supervisor = SolverSupervisor(capability)
    spec = capability.spec
    intent = object.__getattribute__(capability, "_intent_snapshot")

    supervisor.start()
    try:
        record_path = supervisor.process_record_path
        assert record_path == spec.attempt_root / "process.json"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        assert record["case_id"] == "case-a"
        assert record["intent_id"] == intent.intent_sha256
        assert record["attempt_id"] == "attempt-a"
        assert record["executable_path"] == str(spec.executable)
        assert record["pid"] == supervisor.pid
        assert record["process_creation_identity"]
        assert record["start_time"]
        assert record["owned_output_paths"] == {
            "log": str(spec.expected_outputs.log_path),
            "xplt": str(spec.expected_outputs.xplt_path),
        }
        context = record["launch_context"]
        assert isinstance(context, dict)
        assert context["input_path"] == os.path.normcase(
            os.path.realpath(os.path.abspath(os.fspath(spec.input_path)))
        )
        assert context["input_sha256"] == hashlib.sha256(spec.input_path.read_bytes()).hexdigest()
        assert context["runtime_path"] == os.path.normcase(
            os.path.realpath(os.path.abspath(os.fspath(spec.executable)))
        )
        runtime = object.__getattribute__(capability, "_runtime_diagnostic")
        assert context["runtime_sha256"] == runtime.sha256
        assert context["runtime_version"] == runtime.version
        assert context["argv"] == list(spec.command)
        assert context["cwd"] == os.path.normcase(
            os.path.realpath(os.path.abspath(os.fspath(spec.cwd)))
        )
        assert context["output_paths"] == {
            "log": os.path.normcase(
                os.path.realpath(os.path.abspath(os.fspath(spec.expected_outputs.log_path)))
            ),
            "xplt": os.path.normcase(
                os.path.realpath(os.path.abspath(os.fspath(spec.expected_outputs.xplt_path)))
            ),
        }
        assert context["timeout_seconds"] is None
        assert context["expected_steps"] is None
        assert context["expected_final_time"] is None
        assert context["requested_fields"] == []
        assert isinstance(context["environment_digest"], str)
        assert record["launch_context_digest"]
        process_authority = record["process_authority"]
        assert isinstance(process_authority, dict)
        assert process_authority["context_digest"] == record["launch_context_digest"]
        assert process_authority["root_pid"] == record["pid"]
        assert process_authority["root_creation_identity"] == record["process_creation_identity"]
        assert "FEBIO_CAE_HARNESS_AUTHORITY_CONTEXT" not in record_path.read_text(encoding="utf-8")
    finally:
        supervisor.cancel()

    assert supervisor.state is SolverState.CANCELLED


def test_raw_launch_spec_is_rejected_before_popen_or_attempt_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path = tmp_path / "model.feb"
    attempt_root = tmp_path / "caller-selected-attempt"
    input_path.write_text("synthetic", encoding="utf-8")
    spec = SolverLaunchSpec(
        executable=Path(sys.executable),
        input_path=input_path,
        attempt_root=attempt_root,
    )

    def unexpected_popen(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("raw launch spec reached Popen")

    monkeypatch.setattr(subprocess, "Popen", unexpected_popen)
    with pytest.raises(SolverConfigurationError, match="capability"):
        SolverSupervisor(spec)  # type: ignore[arg-type]

    assert not attempt_root.exists()
    assert not (attempt_root / "process.json").exists()


def test_forged_fully_populated_launch_capability_is_rejected_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path = tmp_path / "model.feb"
    attempt_root = tmp_path / "forged-attempt"
    input_path.write_text("synthetic", encoding="utf-8")
    spec = SolverLaunchSpec(
        executable=Path(sys.executable),
        input_path=input_path,
        attempt_root=attempt_root,
    )
    forged = object.__new__(SolverLaunchCapability)
    object.__setattr__(forged, "_spec", spec)
    object.__setattr__(forged, "_attempt_workspace", object())
    object.__setattr__(forged, "_intent_snapshot", object())
    object.__setattr__(forged, "_runtime_diagnostic", object())

    def unexpected_popen(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("forged launch capability reached Popen")

    monkeypatch.setattr(subprocess, "Popen", unexpected_popen)
    with pytest.raises(SolverConfigurationError, match="issued|capability"):
        SolverSupervisor(forged)

    assert not attempt_root.exists()
    assert not (attempt_root / "process.json").exists()


def test_start_revalidates_after_prepare_outputs_before_popen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability = _capability(tmp_path, monkeypatch, code="pass")
    attempt = object.__getattribute__(capability, "_attempt_workspace")
    assert type(attempt) is AttemptWorkspace
    original_prepare_outputs = SolverLaunchSpec.prepare_outputs

    def racing_prepare_outputs(spec: SolverLaunchSpec) -> object:
        outputs = original_prepare_outputs(spec)
        object.__setattr__(attempt, "attempt_id", "attempt-b")
        return outputs

    monkeypatch.setattr(SolverLaunchSpec, "prepare_outputs", racing_prepare_outputs)
    popen_reached = False

    def unexpected_popen(*args: object, **kwargs: object) -> None:
        nonlocal popen_reached
        del args, kwargs
        popen_reached = True
        raise AssertionError("raced launch reached Popen")

    monkeypatch.setattr("febio_cae_harness.solver.supervisor.subprocess.Popen", unexpected_popen)
    supervisor = SolverSupervisor(capability)
    with pytest.raises(SolverConfigurationError, match="authority|binding|live"):
        supervisor.start()

    assert not popen_reached


def test_posix_launch_anchors_child_paths_across_attempt_root_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "posix":
        pytest.skip("POSIX-only launch path anchoring")

    manager = ValidatedCaseWorkspace(tmp_path / "tool", tmp_path / "cae")
    case = manager.create_case("case-a")
    store = EvidenceStore(case, IntentContract())
    store.record_attempt("attempt-a")
    attempt = AttemptWorkspace._from_manager(
        case,
        "attempt-a",
        case.temporary_root / "attempts" / "attempt-a",
    )
    intent = store.issue_intent_snapshot()
    attempt_root = Path(os.fspath(attempt))
    code = (
        "from pathlib import Path; import os; "
        f"root = Path({str(attempt_root)!r}); "
        "moved = root.with_name(root.name + '-moved'); "
        "root.rename(moved); root.mkdir(); "
        "Path(os.environ['FEBIO_CAE_HARNESS_LOG']).write_text('replacement', encoding='utf-8'); "
        "Path(os.environ['FEBIO_CAE_HARNESS_XPLT']).write_bytes(b'replacement')"
    )
    input_path = attempt.write_text("input.feb", code)
    runtime = _issued_runtime(monkeypatch)
    capability = headless_module._issue_launch_capability(
        attempt,
        intent,
        runtime,
        input_path,
        expected_steps=None,
        expected_final_time=None,
        timeout_seconds=None,
    )
    supervisor = SolverSupervisor(capability)

    with pytest.raises((SolverLaunchError, SolverOwnershipError)):
        supervisor.run()

    replacement_root = attempt_root
    moved_root = attempt_root.with_name(attempt_root.name + "-moved")
    assert not (replacement_root / "input.log").exists()
    assert (moved_root / "input.log").read_text(encoding="utf-8") == "replacement"


class _OrderingProcess:
    pid = 4242

    def __init__(self, events: list[str]) -> None:
        self._events = events
        self._return_code: int | None = None

    def poll(self) -> int | None:
        return self._return_code

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self._return_code = self._return_code if self._return_code is not None else -15
        return self._return_code

    def kill(self) -> None:
        self._events.append("process-kill")
        self._return_code = -9


class _OrderingAuthority:
    def __init__(self, events: list[str]) -> None:
        self._events = events
        self._process: _OrderingProcess | None = None
        self._context_digest = "f" * 64

    @property
    def claim(self) -> dict[str, object]:
        return {
            "kind": "windows-job",
            "attempt_binding": "test",
            "name": "test-job",
            "context_digest": self._context_digest,
            "root_pid": None if self._process is None else self._process.pid,
            "root_creation_identity": "windows:test",
        }

    def child_environment(self) -> dict[str, str]:
        return {}

    def child_handle(self) -> int:
        return 9876

    def bind(self, pid: int, expected_creation_identity: str | None = None) -> None:
        assert pid == _OrderingProcess.pid
        assert expected_creation_identity == "windows:test"
        self._events.append("bind")

    def resume(self, pid: int) -> None:
        assert pid == _OrderingProcess.pid
        self._events.append("resume")

    def terminate(self, pid: int, *, force: bool = False) -> None:
        del force
        assert pid == _OrderingProcess.pid
        self._events.append("terminate")
        if self._process is not None:
            self._process._return_code = -15

    def close(self) -> None:
        self._events.append("close")

    def drain(self) -> None:
        self._events.append("drain")


def test_windows_persists_bound_record_before_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ordering probe must fail on the old resume-before-record sequence."""

    capability = _capability(tmp_path, monkeypatch, code="pass")
    events: list[str] = []
    authority = _OrderingAuthority(events)

    class StartupInfo:
        lpAttributeList: object | None = None

    def fake_popen(*args: object, **kwargs: object) -> _OrderingProcess:
        del args
        creation_flags = kwargs["creationflags"]
        assert isinstance(creation_flags, int)
        assert creation_flags & 0x00000004
        events.append("created-suspended")
        process = _OrderingProcess(events)
        authority._process = process
        return process

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(subprocess, "STARTUPINFO", StartupInfo, raising=False)

    def fake_create(
        attempt_root: Path,
        context_digest: str | None = None,
        *,
        root_pid: int | None = None,
        root_creation_identity: str | None = None,
    ) -> _OrderingAuthority:
        del attempt_root
        del root_pid, root_creation_identity
        if context_digest is not None:
            authority._context_digest = context_digest
        return authority

    monkeypatch.setattr(ProcessAuthority, "create", fake_create)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        supervisor_module,
        "_process_metadata",
        lambda pid: supervisor_module._ProcessMetadata(
            str(Path(sys.executable)),
            "windows:test",
            True,
            None,
            datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )
    original_write = SolverSupervisor._write_process_record

    def record_write(supervisor: SolverSupervisor, record: dict[str, object]) -> None:
        events.append(f"persist:{record.get('state')}")
        original_write(supervisor, record)

    monkeypatch.setattr(SolverSupervisor, "_write_process_record", record_write)
    supervisor = SolverSupervisor(capability)
    try:
        supervisor.start()
        first_persist = next(
            index for index, event in enumerate(events) if event.startswith("persist:")
        )
        resume = events.index("resume")
        assert first_persist < resume
        assert events[first_persist] == "persist:BOUND_SUSPENDED"
        assert events.index("persist:RUNNING") > resume
    finally:
        supervisor.cancel()


def test_windows_late_failure_preserves_replaced_process_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capability = _capability(tmp_path, monkeypatch, code="pass")
    events: list[str] = []
    authority = _OrderingAuthority(events)

    class StartupInfo:
        lpAttributeList: object | None = None

    def fake_popen(*args: object, **kwargs: object) -> _OrderingProcess:
        del args
        creation_flags = kwargs["creationflags"]
        assert isinstance(creation_flags, int)
        assert creation_flags & 0x00000004
        events.append("created-suspended")
        process = _OrderingProcess(events)
        authority._process = process
        return process

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(subprocess, "STARTUPINFO", StartupInfo, raising=False)

    def fake_create(
        attempt_root: Path,
        context_digest: str | None = None,
        *,
        root_pid: int | None = None,
        root_creation_identity: str | None = None,
    ) -> _OrderingAuthority:
        del attempt_root
        del root_pid, root_creation_identity
        if context_digest is not None:
            authority._context_digest = context_digest
        return authority

    def fail_resume(pid: int) -> None:
        assert pid == _OrderingProcess.pid
        events.append("resume-failure")
        raise OSError("synthetic late resume failure")

    monkeypatch.setattr(ProcessAuthority, "create", fake_create)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        supervisor_module,
        "_process_metadata",
        lambda pid: supervisor_module._ProcessMetadata(
            str(Path(sys.executable)),
            "windows:test",
            True,
            None,
            datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )
    monkeypatch.setattr(authority, "resume", fail_resume)
    original_write = SolverSupervisor._write_process_record

    def record_write(supervisor: SolverSupervisor, record: dict[str, object]) -> None:
        original_write(supervisor, record)
        if record.get("state") == "BOUND_SUSPENDED":
            replacement = dict(record)
            replacement["replacement_marker"] = "foreign-regular-file"
            replacement_path = supervisor.process_record_path.with_name("foreign.json")
            replacement_path.write_text(json.dumps(replacement, sort_keys=True), encoding="utf-8")
            os.replace(os.fspath(replacement_path), os.fspath(supervisor.process_record_path))
            events.append("replaced-process-record")

    monkeypatch.setattr(SolverSupervisor, "_write_process_record", record_write)
    supervisor = SolverSupervisor(capability)

    with pytest.raises(SolverLaunchError, match="late resume failure"):
        supervisor.start()

    record_path = supervisor.process_record_path
    replacement = json.loads(record_path.read_text(encoding="utf-8"))
    assert record_path.is_file()
    assert replacement["replacement_marker"] == "foreign-regular-file"
    assert replacement["state"] == "BOUND_SUSPENDED"
    assert supervisor.state is SolverState.FAILED
    assert events.index("replaced-process-record") < events.index("resume-failure")
    assert events.count("terminate") == 1
    assert events.count("close") == 1
    assert events.index("terminate") < events.index("close")

    with pytest.raises(SolverOwnershipError, match="reconnectable|RUNNING"):
        SolverSupervisor.reconnect(capability)


@pytest.mark.parametrize("replace_with_foreign", [False, True])
def test_windows_final_guard_rolls_back_owned_record_without_foreign_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replace_with_foreign: bool,
) -> None:
    capability = _capability(tmp_path, monkeypatch, code="pass")
    events: list[str] = []
    authority = _OrderingAuthority(events)

    class StartupInfo:
        lpAttributeList: object | None = None

    def fake_popen(*args: object, **kwargs: object) -> _OrderingProcess:
        del args
        creation_flags = kwargs["creationflags"]
        assert isinstance(creation_flags, int)
        assert creation_flags & 0x00000004
        events.append("created-suspended")
        process = _OrderingProcess(events)
        authority._process = process
        return process

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(subprocess, "STARTUPINFO", StartupInfo, raising=False)

    def fake_create(
        attempt_root: Path,
        context_digest: str | None = None,
        *,
        root_pid: int | None = None,
        root_creation_identity: str | None = None,
    ) -> _OrderingAuthority:
        del attempt_root
        del root_pid, root_creation_identity
        if context_digest is not None:
            authority._context_digest = context_digest
        return authority

    monkeypatch.setattr(ProcessAuthority, "create", fake_create)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        supervisor_module,
        "_process_metadata",
        lambda pid: supervisor_module._ProcessMetadata(
            str(Path(sys.executable)),
            "windows:test",
            True,
            None,
            datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )
    original_write = SolverSupervisor._write_process_record

    def record_write(supervisor: SolverSupervisor, record: dict[str, object]) -> None:
        original_write(supervisor, record)
        if replace_with_foreign and record.get("state") == "RUNNING":
            replacement = dict(record)
            replacement["replacement_marker"] = "foreign-final-guard"
            replacement_path = supervisor.process_record_path.with_name("foreign.json")
            replacement_path.write_text(json.dumps(replacement, sort_keys=True), encoding="utf-8")
            os.replace(os.fspath(replacement_path), os.fspath(supervisor.process_record_path))

    monkeypatch.setattr(SolverSupervisor, "_write_process_record", record_write)
    original_revalidate = SolverSupervisor._revalidate_launch_binding
    guard_failed = False

    def final_guard(supervisor: SolverSupervisor) -> None:
        nonlocal guard_failed
        original_revalidate(supervisor)
        if any(event == "persist:RUNNING" for event in events) and not guard_failed:
            guard_failed = True
            raise SolverConfigurationError("synthetic final guard failure")

    def record_events(supervisor: SolverSupervisor, record: dict[str, object]) -> None:
        events.append(f"persist:{record.get('state')}")
        record_write(supervisor, record)

    monkeypatch.setattr(SolverSupervisor, "_revalidate_launch_binding", final_guard)
    monkeypatch.setattr(SolverSupervisor, "_write_process_record", record_events)
    supervisor = SolverSupervisor(capability)

    with pytest.raises(SolverConfigurationError, match="final guard"):
        supervisor.start()

    assert supervisor.state is SolverState.FAILED
    assert supervisor._process is None
    assert supervisor._process_authority is None
    record_path = supervisor.process_record_path
    if replace_with_foreign:
        replacement = json.loads(record_path.read_text(encoding="utf-8"))
        assert replacement["replacement_marker"] == "foreign-final-guard"
    else:
        assert not record_path.exists()
