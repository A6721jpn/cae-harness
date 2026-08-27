"""Synthetic reconnect tests for attempt-owned solver processes."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from febio_cae_harness.contracts import IntentContract
from febio_cae_harness.evidence import EvidenceStore
from febio_cae_harness.solver import headless as headless_module
from febio_cae_harness.solver.runtime import FebioRuntimeDiagnostic, probe_febio
from febio_cae_harness.solver.supervisor import SolverSupervisor, _process_metadata
from febio_cae_harness.solver.types import (
    SolverConfigurationError,
    SolverLaunchCapability,
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


def _normal_spec(tmp_path: Path) -> SolverLaunchSpec:
    input_path = tmp_path / "input.feb"
    input_path.write_text("synthetic", encoding="utf-8")
    return SolverLaunchSpec(
        executable=Path(sys.executable),
        input_path=input_path,
        attempt_root=tmp_path / "attempt",
        arguments=("-c", "pass"),
    )


@pytest.mark.parametrize(
    ("terminal", "expected_state"),
    (("normal", SolverState.NORMAL_EXIT), ("cancel", SolverState.CANCELLED)),
)
def test_terminal_completion_releases_parent_process_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    terminal: str,
    expected_state: SolverState,
) -> None:
    code = "pass" if terminal == "normal" else "import time; time.sleep(30)"
    supervisor = SolverSupervisor(_capability(tmp_path, monkeypatch, code=code)).start()
    authority = supervisor._process_authority
    assert authority is not None
    close = Mock(wraps=authority.close)
    monkeypatch.setattr(authority, "close", close)

    result = supervisor.wait() if terminal == "normal" else supervisor.cancel()

    assert result.state is expected_state
    close.assert_called_once_with()
    assert supervisor._process_authority is None


def test_reconnect_after_client_close_preserves_child_authority(tmp_path: Path) -> None:
    # The exact launch capability is retained by the original owner for the
    # reconnect call; no raw spec or caller labels cross the boundary.
    capability = _capability(tmp_path, pytest.MonkeyPatch(), code="import time; time.sleep(30)")
    original = SolverSupervisor(capability).start()
    authority = original._process_authority
    assert authority is not None
    authority.close()

    resumed = SolverSupervisor.reconnect(
        capability,
    )
    assert resumed.state is SolverState.RUNNING
    assert resumed.cancel().state is SolverState.CANCELLED


def test_reconnect_resumes_monitoring_and_owned_cancellation(tmp_path: Path) -> None:
    monkeypatch = pytest.MonkeyPatch()
    capability = _capability(tmp_path, monkeypatch, code="import time; time.sleep(30)")
    original = SolverSupervisor(capability).start()
    resumed = SolverSupervisor.reconnect(
        capability,
    )
    assert resumed.state is SolverState.RUNNING
    assert resumed.pid == original.pid
    assert resumed.cancel().state is SolverState.CANCELLED


def test_reconnect_rejects_missing_record_even_when_pid_is_known(tmp_path: Path) -> None:
    monkeypatch = pytest.MonkeyPatch()
    capability = _capability(tmp_path, monkeypatch, code="import time; time.sleep(30)")
    original = SolverSupervisor(capability).start()
    try:
        original.process_record_path.unlink()
        with pytest.raises(SolverOwnershipError):
            SolverSupervisor.reconnect(
                capability,
            )
    finally:
        original.cancel()


def test_reconnect_rejects_tampered_identity_and_escaping_output(tmp_path: Path) -> None:
    monkeypatch = pytest.MonkeyPatch()
    capability = _capability(tmp_path, monkeypatch, code="import time; time.sleep(30)")
    original = SolverSupervisor(capability).start()
    record_path = original.process_record_path
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["process_creation_identity"] = "tampered"
        record["owned_output_paths"]["log"] = str(tmp_path / "outside.log")
        record_path.write_text(json.dumps(record), encoding="utf-8")
        with pytest.raises(SolverOwnershipError):
            SolverSupervisor.reconnect(
                capability,
            )
    finally:
        original.cancel()


def test_reconnect_rejects_stale_process_record(tmp_path: Path) -> None:
    monkeypatch = pytest.MonkeyPatch()
    capability = _capability(tmp_path, monkeypatch, code="import time; time.sleep(30)")
    original = SolverSupervisor(capability).start()
    original.cancel()

    with pytest.raises(SolverOwnershipError):
        SolverSupervisor.reconnect(
            capability,
        )


def test_reconnect_rejects_forged_record_for_unrelated_matching_process(
    tmp_path: Path,
) -> None:
    monkeypatch = pytest.MonkeyPatch()
    capability = _capability(tmp_path, monkeypatch, code="import time; time.sleep(30)")
    spec = capability.spec
    original = SolverSupervisor(capability).start()
    unrelated = subprocess.Popen(
        spec.command,
        cwd=spec.cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        record = json.loads(original.process_record_path.read_text(encoding="utf-8"))
        metadata = _process_metadata(unrelated.pid)
        record["pid"] = unrelated.pid
        record["process_creation_identity"] = metadata.creation_identity
        original.process_record_path.write_text(json.dumps(record), encoding="utf-8")

        with pytest.raises(SolverOwnershipError):
            SolverSupervisor.reconnect(
                capability,
            )

        # Reconnect rejection must not signal or cancel the unrelated live process.
        assert unrelated.poll() is None
    finally:
        original.cancel()
        if unrelated.poll() is None:
            unrelated.terminate()
            unrelated.wait(timeout=5)


def test_reconnect_rejects_raw_launch_spec_before_record_read_or_file_creation(
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

    def unexpected_read(self: SolverSupervisor) -> dict[str, object]:
        del self
        raise AssertionError("raw reconnect spec reached process-record read")

    monkeypatch.setattr(SolverSupervisor, "_read_process_record", unexpected_read)
    with pytest.raises(SolverConfigurationError, match="capability"):
        SolverSupervisor.reconnect(spec)  # type: ignore[arg-type]

    assert not attempt_root.exists()
    assert not (attempt_root / "process.json").exists()
