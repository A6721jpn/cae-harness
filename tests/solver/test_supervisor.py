"""Synthetic durability tests for the owned solver supervisor."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from febio_cae_harness.solver.supervisor import SolverSupervisor
from febio_cae_harness.solver.types import SolverLaunchSpec, SolverState


def _spec(tmp_path: Path) -> SolverLaunchSpec:
    input_path = tmp_path / "input.feb"
    input_path.write_text("synthetic", encoding="utf-8")
    return SolverLaunchSpec(
        executable=Path(sys.executable),
        input_path=input_path,
        attempt_root=tmp_path / "attempt",
        arguments=("-c", "import time; time.sleep(30)"),
    )


def test_start_persists_attempt_owned_process_record(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    supervisor = SolverSupervisor(
        spec,
        case_id="case-a",
        intent_id="intent-a",
        attempt_id="attempt-a",
    )

    supervisor.start()
    try:
        record_path = supervisor.process_record_path
        assert record_path == spec.attempt_root / "process.json"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        assert record["case_id"] == "case-a"
        assert record["intent_id"] == "intent-a"
        assert record["attempt_id"] == "attempt-a"
        assert record["executable_path"] == str(spec.executable)
        assert record["pid"] == supervisor.pid
        assert record["process_creation_identity"]
        assert record["start_time"]
        assert record["owned_output_paths"] == {
            "log": str(spec.expected_outputs.log_path),
            "xplt": str(spec.expected_outputs.xplt_path),
        }
    finally:
        supervisor.cancel()

    assert supervisor.state is SolverState.CANCELLED
