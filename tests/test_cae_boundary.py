from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCANNER = ROOT / "scripts" / "scan_cae_data.py"


def run_scan(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCANNER), "--root", str(root)],
        check=False,
        capture_output=True,
        text=True,
    )


def test_tool_only_tree_passes(tmp_path: Path) -> None:
    (tmp_path / "tool.txt").write_text("tool-only", encoding="utf-8")

    completed = run_scan(tmp_path)

    assert completed.returncode == 0
    assert completed.stdout == "CAE boundary scan passed: 0 prohibited paths\n"
    assert completed.stderr == ""


@pytest.mark.parametrize("suffix", [".feb", ".xplt", ".FBS"])
def test_cae_artifact_suffix_is_rejected(tmp_path: Path, suffix: str) -> None:
    prohibited_file = tmp_path / f"real-model{suffix}"
    prohibited_file.write_bytes(b"not real CAE data")

    completed = run_scan(tmp_path)

    assert completed.returncode == 1
    assert prohibited_file.name in completed.stderr


def test_02_cae_directory_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "02_CAE").mkdir()

    completed = run_scan(tmp_path)

    assert completed.returncode == 1
    assert "02_CAE" in completed.stderr


def test_ignored_environment_directory_is_not_scanned(tmp_path: Path) -> None:
    environment_directory = tmp_path / ".venv"
    environment_directory.mkdir()
    (environment_directory / "fixture.feb").write_bytes(b"ignored environment data")

    completed = run_scan(tmp_path)

    assert completed.returncode == 0
