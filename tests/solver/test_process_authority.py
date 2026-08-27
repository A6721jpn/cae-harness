from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

import febio_cae_harness.solver.process_authority as authority_module
import febio_cae_harness.solver.supervisor as supervisor_module
from febio_cae_harness.contracts import IntentContract
from febio_cae_harness.evidence import EvidenceStore
from febio_cae_harness.solver import headless as headless_module
from febio_cae_harness.solver.process_authority import ProcessAuthority, ProcessAuthorityError
from febio_cae_harness.solver.runtime import FebioRuntimeDiagnostic, probe_febio
from febio_cae_harness.solver.supervisor import SolverSupervisor, _ProcessMetadata
from febio_cae_harness.solver.types import (
    SolverLaunchCapability,
    SolverLaunchError,
    SolverOwnershipError,
)
from febio_cae_harness.workspace import AttemptWorkspace, ValidatedCaseWorkspace

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows process ordering")


class _FakeProcess:
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


class _FakeWindowsAuthority:
    def __init__(self, events: list[str]) -> None:
        self._events = events
        self._process: _FakeProcess | None = None
        self._context_digest = "f" * 64
        self.close = Mock(side_effect=lambda: self._events.append("close"))

    @property
    def claim(self) -> dict[str, object]:
        return {
            "kind": "windows-job",
            "attempt_binding": "test",
            "name": "test-job",
            "context_digest": self._context_digest,
        }

    def child_environment(self) -> dict[str, str]:
        return {}

    def child_handle(self) -> int:
        return 9876

    def bind(self, pid: int) -> None:
        assert pid == _FakeProcess.pid
        self._events.append("bind")

    def resume(self, pid: int) -> None:
        assert pid == _FakeProcess.pid
        self._events.append("resume")

    def terminate(self, pid: int, *, force: bool = False) -> None:
        assert pid == _FakeProcess.pid
        self._events.append("terminate")
        if self._process is not None:
            self._process._return_code = -15


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
    code: str = "pass",
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


def _fake_popen(
    authority: _FakeWindowsAuthority, events: list[str], kwargs: dict[str, Any]
) -> _FakeProcess:
    creation_flags = int(kwargs["creationflags"])
    events.append("created-suspended" if creation_flags & 0x00000004 else "child-code-before-bind")
    process = _FakeProcess(events)
    authority._process = process
    return process


def _patch_fake_windows(
    monkeypatch: pytest.MonkeyPatch, authority: _FakeWindowsAuthority, events: list[str]
) -> None:
    monkeypatch.setattr(os, "name", "nt")

    def fake_create(attempt_root: Path, context_digest: str) -> _FakeWindowsAuthority:
        del attempt_root
        authority._context_digest = context_digest
        return authority

    monkeypatch.setattr(ProcessAuthority, "create", fake_create)
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *args, **kwargs: _fake_popen(authority, events, kwargs),
    )
    monkeypatch.setattr(
        supervisor_module,
        "_process_metadata",
        lambda pid: _ProcessMetadata(str(Path(sys.executable)), "windows:test", True, None),
    )


def test_windows_primary_process_is_bound_before_any_child_code_or_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    authority = _FakeWindowsAuthority(events)

    _patch_fake_windows(monkeypatch, authority, events)

    capability = _capability(tmp_path, monkeypatch)
    supervisor = SolverSupervisor(capability)
    supervisor.start()
    assert events[:3] == ["created-suspended", "bind", "resume"]


def test_windows_immediate_descendant_runs_only_after_primary_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = tmp_path / "descendant-ran.txt"
    descendant_code = (
        "from pathlib import Path; "
        f"Path({str(sentinel)!r}).write_text('ran', encoding='utf-8'); "
        "import time; time.sleep(30)"
    )
    child_code = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {descendant_code!r}]); "
        "time.sleep(30)"
    )
    capability = _capability(tmp_path, monkeypatch, child_code)
    binding_observations: list[bool] = []
    native_bind = authority_module._WindowsProcessAuthority.bind

    def observe_bind(authority: object, pid: int) -> None:
        binding_observations.append(sentinel.exists())
        native_bind(authority, pid)  # type: ignore[arg-type]
        binding_observations.append(sentinel.exists())

    monkeypatch.setattr(authority_module._WindowsProcessAuthority, "bind", observe_bind)
    supervisor = SolverSupervisor(capability)
    supervisor.start()
    try:
        assert binding_observations == [False, False]
        deadline = time.monotonic() + 5.0
        while not sentinel.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert sentinel.read_text(encoding="utf-8") == "ran"
    finally:
        assert supervisor.cancel().cancelled


@pytest.mark.parametrize("failure", ("bind", "resume"))
def test_windows_native_failure_preserves_only_durable_authority_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    events: list[str] = []
    authority = _FakeWindowsAuthority(events)
    _patch_fake_windows(monkeypatch, authority, events)
    monkeypatch.setattr(
        authority,
        failure,
        Mock(side_effect=ProcessAuthorityError("bind failed")),
    )

    capability = _capability(tmp_path, monkeypatch)
    supervisor = SolverSupervisor(capability)
    with pytest.raises(SolverLaunchError):
        supervisor.start()

    assert ("process-kill" if failure == "bind" else "terminate") in events
    authority.close.assert_called_once_with()
    if failure == "bind":
        assert not supervisor.process_record_path.exists()
    else:
        record = json.loads(supervisor.process_record_path.read_text(encoding="utf-8"))
        assert record["state"] == "BOUND_SUSPENDED"
        assert events.count("terminate") == 1
        with pytest.raises(SolverOwnershipError, match="reconnectable|RUNNING"):
            SolverSupervisor.reconnect(capability)
    assert supervisor.result is None
