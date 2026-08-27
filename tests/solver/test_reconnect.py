"""Synthetic reconnect tests for attempt-owned solver processes."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from febio_cae_harness.solver.supervisor import SolverSupervisor, _process_metadata
from febio_cae_harness.solver.types import SolverLaunchSpec, SolverOwnershipError, SolverState


def _spec(tmp_path: Path) -> SolverLaunchSpec:
    input_path = tmp_path / "input.feb"
    input_path.write_text("synthetic", encoding="utf-8")
    return SolverLaunchSpec(
        executable=Path(sys.executable),
        input_path=input_path,
        attempt_root=tmp_path / "attempt",
        arguments=("-c", "import time; time.sleep(30)"),
    )


def _supervisor(spec: SolverLaunchSpec) -> SolverSupervisor:
    return SolverSupervisor(
        spec,
        case_id="case-a",
        intent_id="intent-a",
        attempt_id="attempt-a",
    )


def test_reconnect_resumes_monitoring_and_owned_cancellation(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    original = _supervisor(spec).start()
    resumed = SolverSupervisor.reconnect(
        spec,
        case_id="case-a",
        intent_id="intent-a",
        attempt_id="attempt-a",
    )
    assert resumed.state is SolverState.RUNNING
    assert resumed.pid == original.pid
    assert resumed.cancel().state is SolverState.CANCELLED


def test_reconnect_rejects_missing_record_even_when_pid_is_known(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    original = _supervisor(spec).start()
    try:
        original.process_record_path.unlink()
        with pytest.raises(SolverOwnershipError):
            SolverSupervisor.reconnect(
                spec,
                case_id="case-a",
                intent_id="intent-a",
                attempt_id="attempt-a",
            )
    finally:
        original.cancel()


def test_reconnect_rejects_tampered_identity_and_escaping_output(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    original = _supervisor(spec).start()
    record_path = original.process_record_path
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["process_creation_identity"] = "tampered"
        record["owned_output_paths"]["log"] = str(tmp_path / "outside.log")
        record_path.write_text(json.dumps(record), encoding="utf-8")
        with pytest.raises(SolverOwnershipError):
            SolverSupervisor.reconnect(
                spec,
                case_id="case-a",
                intent_id="intent-a",
                attempt_id="attempt-a",
            )
    finally:
        original.cancel()


def test_reconnect_rejects_stale_process_record(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    original = _supervisor(spec).start()
    original.cancel()

    with pytest.raises(SolverOwnershipError):
        SolverSupervisor.reconnect(
            spec,
            case_id="case-a",
            intent_id="intent-a",
            attempt_id="attempt-a",
        )


def test_reconnect_rejects_forged_record_for_unrelated_matching_process(
    tmp_path: Path,
) -> None:
    spec = _spec(tmp_path)
    original = _supervisor(spec).start()
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
                spec,
                case_id="case-a",
                intent_id="intent-a",
                attempt_id="attempt-a",
            )

        # Reconnect rejection must not signal or cancel the unrelated live process.
        assert unrelated.poll() is None
    finally:
        original.cancel()
        if unrelated.poll() is None:
            unrelated.terminate()
            unrelated.wait(timeout=5)
