from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCANNER = ROOT / "scripts" / "scan_cae_data.py"
IGNORED_UNTRACKED_DIRECTORY_NAMES = [
    "build",
    "dist",
    "venv",
    ".venv",
    "pytest",
    ".pytest_cache",
    ".pytest_tmp",
    "mypy",
    ".mypy_cache",
    "ruff",
    ".ruff_cache",
    "cache",
    "__pycache__",
]


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


@pytest.mark.parametrize("suffix", [".feb", ".xplt", ".FBS", ".step", ".STP", ".log"])
def test_cae_artifact_suffix_is_rejected(tmp_path: Path, suffix: str) -> None:
    prohibited_file = tmp_path / f"real-model{suffix}"
    prohibited_file.write_bytes(b"not real CAE data")

    completed = run_scan(tmp_path)

    assert completed.returncode == 1
    assert prohibited_file.name in completed.stderr


@pytest.mark.parametrize(
    "directory_name",
    ["02_CAE", "01_Input", "02_Model", "03_Result", "04_Report", "05_Verification", "90_Temporary"],
)
def test_case_directory_is_rejected(tmp_path: Path, directory_name: str) -> None:
    (tmp_path / directory_name).mkdir()

    completed = run_scan(tmp_path)

    assert completed.returncode == 1
    assert directory_name in completed.stderr


def test_case_manifest_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "CASE_MANIFEST.json").write_text("{}", encoding="utf-8")

    completed = run_scan(tmp_path)

    assert completed.returncode == 1
    assert "CASE_MANIFEST.json" in completed.stderr


@pytest.mark.parametrize("directory_name", [".venv", ".pytest_tmp", "build", "dist"])
def test_noncandidate_in_ignored_environment_directory_is_allowed(
    tmp_path: Path, directory_name: str
) -> None:
    environment_directory = tmp_path / directory_name
    environment_directory.mkdir()
    (environment_directory / "fixture.txt").write_bytes(b"synthetic environment data")

    completed = run_scan(tmp_path)

    assert completed.returncode == 0


@pytest.mark.parametrize("directory_name", IGNORED_UNTRACKED_DIRECTORY_NAMES)
def test_ignored_untracked_artifact_suffix_is_rejected(tmp_path: Path, directory_name: str) -> None:
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text(f"{directory_name}/\n", encoding="utf-8")
    ignored_directory = tmp_path / directory_name
    ignored_directory.mkdir()
    prohibited_file = ignored_directory / "synthetic-model.feb"
    prohibited_file.write_bytes(b"synthetic prohibited suffix fixture")

    completed = run_scan(tmp_path)

    assert completed.returncode == 1
    assert prohibited_file.relative_to(tmp_path).as_posix() in completed.stderr.replace("\\", "/")


@pytest.mark.parametrize("directory_name", IGNORED_UNTRACKED_DIRECTORY_NAMES)
def test_ignored_untracked_case_directory_is_rejected(tmp_path: Path, directory_name: str) -> None:
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text(f"{directory_name}/\n", encoding="utf-8")
    ignored_directory = tmp_path / directory_name
    ignored_directory.mkdir()
    prohibited_directory = ignored_directory / "02_CAE"
    prohibited_directory.mkdir()
    (prohibited_directory / "synthetic-case.txt").write_bytes(
        b"synthetic prohibited case directory fixture"
    )

    completed = run_scan(tmp_path)

    assert completed.returncode == 1
    assert prohibited_directory.relative_to(tmp_path).as_posix() in completed.stderr.replace(
        "\\", "/"
    )


def test_untracked_local_tool_debug_log_is_not_scanned(tmp_path: Path) -> None:
    (tmp_path / "debug.log").write_text("local tool output", encoding="utf-8")

    completed = run_scan(tmp_path)

    assert completed.returncode == 0


def test_git_index_rejects_force_tracked_local_tool_debug_log(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("debug.log\n", encoding="utf-8")
    (tmp_path / "debug.log").write_text("synthetic solver LOG marker", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "--force", "debug.log"],
        check=True,
    )

    completed = run_scan(tmp_path)

    assert completed.returncode == 1
    assert "debug.log" in completed.stderr


def test_git_index_rejects_force_tracked_artifact_in_ignored_directory(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("build/\n", encoding="utf-8")
    ignored_directory = tmp_path / "build"
    ignored_directory.mkdir()
    (ignored_directory / "model.step").write_bytes(b"synthetic STEP marker")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "--force", "build/model.step"],
        check=True,
    )

    completed = run_scan(tmp_path)

    assert completed.returncode == 1
    assert "build/model.step" in completed.stderr.replace("\\", "/")


def test_missing_root_fails_closed(tmp_path: Path) -> None:
    missing_root = tmp_path / "missing"

    completed = run_scan(missing_root)

    assert completed.returncode == 2
    assert "CAE boundary scan failed" in completed.stderr
