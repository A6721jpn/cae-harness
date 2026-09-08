"""Actual owned Python prelaunch boundary and normal fresh-process cancel CLI."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

from test_run_lifecycle_cli import preserved

CHILD = """
import json, os, sys
from pathlib import Path
from febio_cae.storage.registry import CaseStorage
from test_registered_execution import test_registered_execution_publication_boundary
original = CaseStorage._register_execution
def prelaunch(storage, owner, bundle, mesh, profile, inputs):
    result = original(storage, owner, bundle, mesh, profile, inputs)
    assert result.state.value == 'CREATED'
    print(json.dumps({'case_id':owner.case_id,'run_id':owner.run_id}), flush=True)
    assert sys.stdin.readline().strip() == 'exit'
    os._exit(73)
CaseStorage._register_execution = prelaunch
test_registered_execution_publication_boundary(Path(sys.argv[1]), 'state-time')
"""


def test_cancel_prelaunch_is_atomic_idempotent_and_never_restarted(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[3]
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(repo / "src"), str(repo / "tests/component/application")]
    )
    env.pop("PYTHONHOME", None)
    child = subprocess.Popen(
        [sys.executable, "-u", "-c", CHILD, str(tmp_path)],
        cwd=tmp_path,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert child.stdout is not None
    marker = json.loads(child.stdout.readline())

    def cli(action: str) -> tuple[int, dict[str, Any]]:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "febio_cae",
                action,
                marker["run_id"],
                "--case-id",
                marker["case_id"],
                "--state-dir",
                str(tmp_path / "state"),
                "--json",
            ],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            timeout=40,
            check=False,
        )
        if proc.returncode == 2 and not proc.stdout:
            return proc.returncode, {"parser_error": proc.stderr.decode()}
        return proc.returncode, json.loads(proc.stdout)

    def database() -> tuple[str, ...]:
        with sqlite3.connect(tmp_path / "case/registry.sqlite3") as connection:
            return tuple(connection.iterdump())

    before = preserved(tmp_path / "case")
    original = database()
    try:
        code, busy = cli("cancel")
        assert code == 8 and busy["status"] == "CONFLICT", busy
        assert database() == original
    finally:
        out, err = child.communicate("exit\n", timeout=30)
        assert child.returncode == 73, (out, err)
    code, initial = cli("status")
    assert code == 0 and initial["run_status"] == "CREATED"
    assert initial["task_status"] != "COMPLETE" and database() == original
    code, cancelled = cli("cancel")
    assert code == 7 and cancelled["run_status"] == "CANCELLED", cancelled
    assert cancelled["status"] == "CANCELLED" and cancelled["task_status"] == "INTERRUPTED"
    assert cancelled["diagnostics"] and cancelled["next_actions"]
    after = preserved(tmp_path / "case")
    assert len(after["history"]) == len(before["history"]) + 1
    assert after["history"][:-1] == before["history"]
    assert {k: v for k, v in after.items() if k != "history"} == {
        k: v for k, v in before.items() if k != "history"
    }
    finished = database()
    code, again = cli("cancel")
    assert code == 7 and again == cancelled and database() == finished
    code, status = cli("status")
    assert code == 0 and status["run_status"] == "CANCELLED"
    assert status["diagnostics"] == cancelled["diagnostics"] and database() == finished
    code, resumed = cli("resume")
    assert code == 7 and resumed["run_status"] == "CANCELLED" and database() == finished
