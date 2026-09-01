"""Synthetic reconnect tests for attempt-owned solver processes."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest

import febio_cae_harness.solver.supervisor as supervisor_module
from febio_cae_harness import workspace as workspace_module
from febio_cae_harness.contracts import IntentContract
from febio_cae_harness.evidence import EvidenceStore
from febio_cae_harness.solver import headless as headless_module
from febio_cae_harness.solver import types as solver_types
from febio_cae_harness.solver.log import LogValidation
from febio_cae_harness.solver.log import validate_log as validate_solver_log
from febio_cae_harness.solver.runtime import FebioRuntimeDiagnostic, probe_febio
from febio_cae_harness.solver.supervisor import SolverSupervisor, _process_metadata
from febio_cae_harness.solver.types import (
    SolverConfigurationError,
    SolverLaunchCapability,
    SolverLaunchSpec,
    SolverOwnershipError,
    SolverRunResult,
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
    (
        ("normal", SolverState.NORMAL_EXIT),
        ("failed", SolverState.FAILED),
        ("cancel", SolverState.CANCELLED),
    ),
)
def test_terminal_completion_releases_parent_process_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    terminal: str,
    expected_state: SolverState,
) -> None:
    code = {
        "normal": "pass",
        "failed": "import os; os._exit(7)",
        "cancel": "import time; time.sleep(30)",
    }[terminal]
    supervisor = SolverSupervisor(_capability(tmp_path, monkeypatch, code=code)).start()
    authority = supervisor._process_authority
    assert authority is not None
    close = Mock(wraps=authority.close)
    monkeypatch.setattr(authority, "close", close)

    result = supervisor.cancel() if terminal == "cancel" else supervisor.wait()

    assert result.state is expected_state
    if terminal == "failed":
        assert result.return_code == 7
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


def test_reconnect_rejects_tampered_started_at(tmp_path: Path) -> None:
    monkeypatch = pytest.MonkeyPatch()
    capability = _capability(tmp_path, monkeypatch, code="import time; time.sleep(30)")
    original = SolverSupervisor(capability).start()
    record_path = original.process_record_path
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["start_time"] = datetime(2000, 1, 1, tzinfo=UTC).isoformat()
        record_path.write_text(json.dumps(record), encoding="utf-8")
        with pytest.raises(SolverOwnershipError, match="start|identity|authority"):
            SolverSupervisor.reconnect(capability)
    finally:
        original.cancel()


def test_normal_exit_drains_owned_descendant_before_releasing_authority(
    tmp_path: Path,
) -> None:
    monkeypatch = pytest.MonkeyPatch()
    descendant_started = tmp_path / "descendant-started.txt"
    descendant_survived = tmp_path / "descendant-survived.txt"
    descendant_code = (
        "from pathlib import Path; import time; "
        f"Path({str(descendant_started)!r}).write_text('started', encoding='utf-8'); "
        "time.sleep(1.0); "
        f"Path({str(descendant_survived)!r}).write_text('survived', encoding='utf-8')"
    )
    root_code = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {descendant_code!r}]); "
        "time.sleep(0.1)"
    )
    supervisor = SolverSupervisor(_capability(tmp_path, monkeypatch, code=root_code)).start()
    result = supervisor.wait(timeout_seconds=5)
    assert result.state is SolverState.NORMAL_EXIT
    deadline = time.monotonic() + 2.0
    while not descendant_started.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    time.sleep(1.2)
    assert descendant_started.exists()
    assert not descendant_survived.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="requires native Windows Job Objects")
def test_cancel_after_root_exit_drains_owned_descendant_before_validation(
    tmp_path: Path,
) -> None:
    monkeypatch = pytest.MonkeyPatch()
    owned_ready = tmp_path / "owned-descendant-ready.txt"
    owned_release = tmp_path / "owned-descendant-release.txt"
    owned_late_marker = tmp_path / "owned-descendant-late.txt"
    unrelated_ready = tmp_path / "unrelated-ready.txt"
    unrelated_release = tmp_path / "unrelated-release.txt"
    unrelated_marker = tmp_path / "unrelated-marker.txt"

    def wait_for_path(path: Path, timeout_seconds: float) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        return path.exists()

    descendant_code = "\n".join(
        (
            "from pathlib import Path",
            "import os",
            "import time",
            f"ready = Path({str(owned_ready)!r})",
            f"release = Path({str(owned_release)!r})",
            f"marker = Path({str(owned_late_marker)!r})",
            "ready.write_text(str(os.getpid()), encoding='utf-8')",
            "deadline = time.monotonic() + 15.0",
            "while not release.exists() and time.monotonic() < deadline:",
            "    time.sleep(0.01)",
            "if release.exists():",
            "    marker.write_text('survived', encoding='utf-8')",
        )
    )
    root_code = "\n".join(
        (
            "from pathlib import Path",
            "import os",
            "import subprocess",
            "import sys",
            "import time",
            f"ready = Path({str(owned_ready)!r})",
            f"descendant_code = {descendant_code!r}",
            "subprocess.Popen(",
            "    [sys.executable, '-c', descendant_code],",
            "    stdin=subprocess.DEVNULL,",
            "    stdout=subprocess.DEVNULL,",
            "    stderr=subprocess.DEVNULL,",
            ")",
            "deadline = time.monotonic() + 5.0",
            "while not ready.exists() and time.monotonic() < deadline:",
            "    time.sleep(0.01)",
            "os._exit(0 if ready.exists() else 7)",
        )
    )
    unrelated_code = "\n".join(
        (
            "from pathlib import Path",
            "import os",
            "import time",
            f"ready = Path({str(unrelated_ready)!r})",
            f"release = Path({str(unrelated_release)!r})",
            f"marker = Path({str(unrelated_marker)!r})",
            "ready.write_text(str(os.getpid()), encoding='utf-8')",
            "deadline = time.monotonic() + 15.0",
            "while not release.exists() and time.monotonic() < deadline:",
            "    time.sleep(0.01)",
            "if release.exists():",
            "    marker.write_text('completed', encoding='utf-8')",
        )
    )

    supervisor = SolverSupervisor(_capability(tmp_path, monkeypatch, code=root_code)).start()
    process = supervisor._process
    assert isinstance(process, subprocess.Popen)
    unrelated = subprocess.Popen(
        [sys.executable, "-c", unrelated_code],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert wait_for_path(owned_ready, 5.0)
        owned_pid = int(owned_ready.read_text(encoding="utf-8"))
        owned_before_root_exit = _process_metadata(owned_pid)
        assert owned_before_root_exit.alive

        assert process.wait(timeout=5.0) == 0
        assert process.poll() == 0
        owned_after_root_exit = _process_metadata(owned_pid)
        assert owned_after_root_exit.alive
        assert owned_after_root_exit.creation_identity == owned_before_root_exit.creation_identity

        assert wait_for_path(unrelated_ready, 5.0)
        assert int(unrelated_ready.read_text(encoding="utf-8")) == unrelated.pid
        unrelated_before_cancel = _process_metadata(unrelated.pid)
        assert unrelated_before_cancel.alive

        order: list[str] = []
        original_validate_log = validate_solver_log
        original_register_result = supervisor._register_result

        def validate_after_drain(
            path: str | Path,
            *,
            expected_steps: int | None = None,
            expected_final_time: float | None = None,
            final_time_tolerance: float = 1e-9,
        ) -> LogValidation:
            order.append("validate")
            owned_release.write_text("validate", encoding="utf-8")
            assert not wait_for_path(owned_late_marker, 1.0)
            return original_validate_log(
                path,
                expected_steps=expected_steps,
                expected_final_time=expected_final_time,
                final_time_tolerance=final_time_tolerance,
            )

        def publish_after_drain(result: SolverRunResult) -> None:
            order.append("publish")
            assert owned_release.exists()
            assert not owned_late_marker.exists()
            original_register_result(result)

        monkeypatch.setattr(supervisor_module, "validate_log", validate_after_drain)
        monkeypatch.setattr(supervisor, "_register_result", publish_after_drain)

        result = supervisor.cancel()

        assert result.state is SolverState.CANCELLED
        assert order == ["validate", "publish"]
        assert not wait_for_path(owned_late_marker, 0.5)
        try:
            owned_after_cancel = _process_metadata(owned_pid)
        except ProcessLookupError:
            pass
        else:
            assert (
                not owned_after_cancel.alive
                or owned_after_cancel.creation_identity != owned_before_root_exit.creation_identity
            )

        unrelated_after_cancel = _process_metadata(unrelated.pid)
        assert unrelated_after_cancel.alive
        assert unrelated_after_cancel.creation_identity == unrelated_before_cancel.creation_identity
        unrelated_release.write_text("complete", encoding="utf-8")
        assert unrelated.wait(timeout=5.0) == 0
        assert unrelated_marker.read_text(encoding="utf-8") == "completed"
    finally:
        monkeypatch.undo()
        owned_release.touch()
        unrelated_release.touch()
        if unrelated.poll() is None:
            try:
                unrelated.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                unrelated.kill()
                unrelated.wait(timeout=5.0)
        if supervisor._result_latch is None:
            supervisor.cancel()


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


def test_reconnect_rejects_same_job_descendant_even_when_record_claim_is_forged(
    tmp_path: Path,
) -> None:
    monkeypatch = pytest.MonkeyPatch()
    descendant_pid_path = tmp_path / "descendant.pid"
    descendant_code = (
        "from pathlib import Path; import os, time; "
        f"Path({str(descendant_pid_path)!r}).write_text(str(os.getpid()), encoding='utf-8'); "
        "time.sleep(30)"
    )
    root_code = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {descendant_code!r}]); "
        "time.sleep(30)"
    )
    capability = _capability(tmp_path, monkeypatch, code=root_code)
    original = SolverSupervisor(capability).start()
    try:
        deadline = time.monotonic() + 5.0
        while not descendant_pid_path.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        descendant_pid = int(descendant_pid_path.read_text(encoding="utf-8"))
        descendant_metadata = _process_metadata(descendant_pid)
        assert descendant_metadata.started_at is not None
        record = json.loads(original.process_record_path.read_text(encoding="utf-8"))
        record["pid"] = descendant_pid
        record["process_creation_identity"] = descendant_metadata.creation_identity
        record["start_time"] = descendant_metadata.started_at.isoformat()
        process_claim = record["process_authority"]
        assert isinstance(process_claim, dict)
        process_claim["root_pid"] = descendant_pid
        process_claim["root_creation_identity"] = descendant_metadata.creation_identity
        original.process_record_path.write_text(json.dumps(record), encoding="utf-8")

        with pytest.raises(SolverOwnershipError, match="root|identity|authority"):
            SolverSupervisor.reconnect(capability)
    finally:
        original.cancel()


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


def test_reconnect_rejects_attempt_identity_mutated_after_registry_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability = _capability(tmp_path, monkeypatch, code="pass")
    attempt = object.__getattribute__(capability, "_attempt_workspace")
    assert type(attempt) is AttemptWorkspace
    original_validate = workspace_module._require_registered_attempt

    def racing_validate(value: object) -> AttemptWorkspace:
        validated = original_validate(value)
        object.__setattr__(attempt, "attempt_id", "attempt-b")
        return validated

    monkeypatch.setattr(workspace_module, "_require_registered_attempt", racing_validate)

    def unexpected_read(self: SolverSupervisor) -> dict[str, object]:
        del self
        raise AssertionError("raced reconnect reached process-record read")

    monkeypatch.setattr(SolverSupervisor, "_read_process_record", unexpected_read)
    with pytest.raises(SolverConfigurationError, match="authority|binding|live"):
        SolverSupervisor.reconnect(capability)


def test_reconnect_rejects_attempt_identity_mutated_after_input_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability = _capability(tmp_path, monkeypatch, code="pass")
    attempt = object.__getattribute__(capability, "_attempt_workspace")
    assert type(attempt) is AttemptWorkspace
    original_input_snapshot = solver_types._input_snapshot

    def racing_input_snapshot(path: Path) -> tuple[object, ...]:
        snapshot = original_input_snapshot(path)
        object.__setattr__(attempt, "attempt_id", "attempt-b")
        return snapshot

    monkeypatch.setattr(solver_types, "_input_snapshot", racing_input_snapshot)

    def unexpected_read(self: SolverSupervisor) -> dict[str, object]:
        del self
        raise AssertionError("raced reconnect reached process-record read")

    monkeypatch.setattr(SolverSupervisor, "_read_process_record", unexpected_read)
    with pytest.raises(SolverConfigurationError, match="authority|binding|live"):
        SolverSupervisor.reconnect(capability)


def test_reconnect_revalidates_after_process_record_read_before_return(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability = _capability(tmp_path, monkeypatch, code="import time; time.sleep(30)")
    original = SolverSupervisor(capability).start()
    attempt = object.__getattribute__(capability, "_attempt_workspace")
    assert type(attempt) is AttemptWorkspace
    original_read_process_record = SolverSupervisor._read_process_record

    def racing_read_process_record(self: SolverSupervisor) -> dict[str, object]:
        record = original_read_process_record(self)
        object.__setattr__(attempt, "attempt_id", "attempt-b")
        return record

    monkeypatch.setattr(SolverSupervisor, "_read_process_record", racing_read_process_record)
    reconnected: SolverSupervisor | None = None
    try:
        try:
            reconnected = SolverSupervisor.reconnect(capability)
        except SolverConfigurationError:
            pass
        else:
            assert reconnected.state is not SolverState.RUNNING, (
                "raced reconnect returned RUNNING after process-record read"
            )
    finally:
        if reconnected is not None:
            reconnected.cancel()
        else:
            original.cancel()


def test_reconnect_rejects_fresh_capability_for_same_stem_alternate_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A nested same-stem input must not inherit an existing process record."""

    capability = _capability(tmp_path, monkeypatch, code="import time; time.sleep(30)")
    attempt = object.__getattribute__(capability, "_attempt_workspace")
    intent = object.__getattribute__(capability, "_intent_snapshot")
    runtime = object.__getattribute__(capability, "_runtime_diagnostic")
    assert type(attempt) is AttemptWorkspace
    assert type(runtime) is FebioRuntimeDiagnostic

    input_a = attempt.write_text("nested-a/model.feb", "import time; time.sleep(30)")
    input_b = attempt.write_text("nested-b/model.feb", "import time; time.sleep(30)")
    original_capability = headless_module._issue_launch_capability(
        attempt,
        intent,
        runtime,
        input_a,
        expected_steps=None,
        expected_final_time=None,
        timeout_seconds=None,
    )
    alternate_capability = headless_module._issue_launch_capability(
        attempt,
        intent,
        runtime,
        input_b,
        expected_steps=None,
        expected_final_time=None,
        timeout_seconds=None,
    )
    original = SolverSupervisor(original_capability).start()
    try:

        def unexpected_process_metadata(pid: int) -> object:
            del pid
            raise AssertionError("alternate launch context reached process metadata")

        monkeypatch.setattr(supervisor_module, "_process_metadata", unexpected_process_metadata)
        with pytest.raises(SolverOwnershipError):
            SolverSupervisor.reconnect(alternate_capability)
    finally:
        original.cancel()


def test_reconnect_rejects_bound_suspended_record_before_process_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability = _capability(tmp_path, monkeypatch, code="import time; time.sleep(30)")
    original = SolverSupervisor(capability).start()
    try:
        record_path = original.process_record_path
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["state"] = "BOUND_SUSPENDED"
        record_path.write_text(json.dumps(record), encoding="utf-8")

        def unexpected_process_metadata(pid: int) -> object:
            del pid
            raise AssertionError("bound-suspended reconnect touched process metadata")

        monkeypatch.setattr(supervisor_module, "_process_metadata", unexpected_process_metadata)
        with pytest.raises(SolverOwnershipError, match="reconnectable|RUNNING"):
            SolverSupervisor.reconnect(capability)
    finally:
        original.cancel()
