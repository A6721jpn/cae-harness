from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCANNER = REPOSITORY_ROOT / "scripts" / "scan_cae_data.py"


def _run_scanner(root: Path) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    source_root = REPOSITORY_ROOT / "src"
    pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        f"{source_root}{os.pathsep}{pythonpath}" if pythonpath else str(source_root)
    )
    return subprocess.run(
        [sys.executable, str(SCANNER), "--root", str(root), "--json"],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_clean_repository_boundary_scan_returns_structured_pass() -> None:
    completed = _run_scanner(REPOSITORY_ROOT)

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["schema_version"] == "1"
    assert payload["status"] == "PASS"
    assert payload["issues"] == []
    assert ".local" in payload["excluded_paths"]
    assert payload["git_tracking"]["available"] is True


def test_boundary_scan_rejects_cae_data_and_reports_reason(tmp_path: Path) -> None:
    forbidden_file = tmp_path / "02_CAE" / "synthetic-result.feb"
    forbidden_file.parent.mkdir()
    forbidden_file.write_text("synthetic fixture; not a real CAE result", encoding="utf-8")

    completed = _run_scanner(tmp_path)

    assert completed.returncode == 4, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "REJECT"
    assert any(issue["code"] == "FORBIDDEN_CAE_DATA" for issue in payload["issues"])
    assert any(issue["path"].endswith("synthetic-result.feb") for issue in payload["issues"])


def test_boundary_scan_excludes_local_execution_artifacts(tmp_path: Path) -> None:
    generated_file = tmp_path / ".local" / "verification" / "old-result.xplt"
    generated_file.parent.mkdir(parents=True)
    generated_file.write_text("synthetic local artifact", encoding="utf-8")

    completed = _run_scanner(tmp_path)

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] in {"PASS", "PASS_WITH_WARNINGS"}
    assert payload["issues"] == []
    assert ".local" in payload["excluded_paths"]
