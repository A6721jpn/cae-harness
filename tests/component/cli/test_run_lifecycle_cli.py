"""Owned Python crash at genuine manifest commit; normal fresh-process CLI recovery."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

CHILD = """
import json, os, sys
from pathlib import Path
from febio_cae.storage.registry import CaseStorage
from test_registered_execution import test_registered_execution_publication_boundary
original = CaseStorage.publish_manifest
def gap(storage, owner, manifest):
    result = original(storage, owner, manifest)
    print(json.dumps({'case_id':owner.case_id,'run_id':owner.run_id,'manifest_id':result.manifest_id}), flush=True)
    assert sys.stdin.readline().strip() == 'crash'
    os._exit(73)
CaseStorage.publish_manifest = gap
test_registered_execution_publication_boundary(Path(sys.argv[1]), 'state-time')
"""


def preserved(root: Path) -> dict[str, Any]:
    with sqlite3.connect(root / "registry.sqlite3") as connection:
        data = {
            table: connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
            for table in ("sources", "revisions", "execution_lineage", "manifests", "numeric_data")
        }
        data["history"] = connection.execute(
            "SELECT * FROM attempt_history ORDER BY rowid"
        ).fetchall()
    data["files"] = {
        str(p.relative_to(root)): p.read_bytes() for p in (root / "cases").rglob("*") if p.is_file()
    }
    return data


def test_public_status_resume_reconciles_one_crash_and_preserves_outputs(tmp_path: Path) -> None:
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
        )
        if proc.returncode == 2 and not proc.stdout:
            return proc.returncode, {"parser_error": proc.stderr.decode()}
        return proc.returncode, json.loads(proc.stdout)

    before = preserved(tmp_path / "case")
    try:
        code, busy = cli("resume")
        assert code == 8, busy
        assert busy["status"] == "CONFLICT"
        assert preserved(tmp_path / "case") == before
    finally:
        out, err = child.communicate("crash\n", timeout=30)
        assert child.returncode == 73, (out, err)
    code, status = cli("status")
    assert code == 0 and status["run_status"] == "VALIDATING", status
    assert status["task_status"] != "COMPLETE" and status["diagnostics"] and status["next_actions"]
    assert preserved(tmp_path / "case") == before
    code, resumed = cli("resume")
    assert code == 7 and resumed["run_status"] == "INTERRUPTED", resumed
    assert resumed["task_status"] == "INTERRUPTED" and resumed["status"] == "INTERRUPTED"
    assert resumed["diagnostics"] and resumed["next_actions"]
    after = preserved(tmp_path / "case")
    assert len(after["history"]) == len(before["history"]) + 1
    assert after["history"][:-1] == before["history"]
    assert {k: v for k, v in after.items() if k != "history"} == {
        k: v for k, v in before.items() if k != "history"
    }
    code, again = cli("resume")
    assert code == 7 and again == resumed
    assert preserved(tmp_path / "case") == after
    code, final = cli("status")
    assert code == 0 and final["run_status"] == "INTERRUPTED"
    assert final["diagnostics"] == resumed["diagnostics"]
    assert {
        "schema_version",
        "status",
        "case_id",
        "revision_id",
        "run_id",
        "diagnostics",
        "next_actions",
        "run_status",
        "quality_status",
        "preview_status",
        "task_status",
    } <= set(final)
