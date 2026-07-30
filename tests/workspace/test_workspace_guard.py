import json
from pathlib import Path
import subprocess

from scripts.workspace.workspace_guard import (
    find_git_markers,
    validate_cae_root,
    validate_case,
)


def test_find_git_markers_detects_directory_and_pointer(tmp_path: Path):
    (tmp_path / "a" / ".git").mkdir(parents=True)
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / ".git").write_text(
        "gitdir: C:/repo/.git/worktrees/b",
        encoding="utf-8",
    )

    assert {
        path.relative_to(tmp_path).as_posix()
        for path in find_git_markers(tmp_path)
    } == {
        "a/.git",
        "b/.git",
    }


def test_validate_case_accepts_active_case_with_required_schema(tmp_path: Path):
    case = tmp_path / "2026-07-30_Project_Case_Purpose_r01"
    for name in (
        "01_Input",
        "02_Model",
        "03_Result",
        "04_Report",
        "05_Verification",
        "90_Temporary",
    ):
        (case / name).mkdir(parents=True)
    manifest = {
        "schema_version": 1,
        "analysis_id": case.name,
        "project": "Project",
        "status": "active",
        "git_prohibited": True,
        "source_files": [],
        "model_files": [],
        "result_files": [],
        "report_files": [],
        "verification_files": [],
        "tool_versions": [],
        "solver": {"status": "not-run"},
        "temporary_status": "present",
    }
    (case / "CASE_MANIFEST.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    assert validate_case(case) == []


def test_validate_case_accepts_windows_powershell_utf8_bom(tmp_path: Path):
    case = tmp_path / "2026-07-30_Project_Case_BOM_r01"
    for name in (
        "01_Input",
        "02_Model",
        "03_Result",
        "04_Report",
        "05_Verification",
        "90_Temporary",
    ):
        (case / name).mkdir(parents=True)
    manifest = {
        "schema_version": 1,
        "analysis_id": case.name,
        "project": "Project",
        "status": "active",
        "git_prohibited": True,
    }
    (case / "CASE_MANIFEST.json").write_text(
        json.dumps(manifest),
        encoding="utf-8-sig",
    )

    assert validate_case(case) == []


def test_validate_cae_root_rejects_git_marker(tmp_path: Path):
    (tmp_path / "01_Active" / "case" / ".git").mkdir(parents=True)

    assert validate_cae_root(tmp_path) == [
        "Git marker found: 01_Active/case/.git"
    ]


def test_validate_cae_root_rejects_ancestor_git_work_tree(tmp_path: Path):
    repository = tmp_path / "repository"
    cae_root = repository / "02_CAE"
    cae_root.mkdir(parents=True)
    subprocess.run(
        ["git", "init", str(repository)],
        check=True,
        capture_output=True,
        text=True,
    )

    assert validate_cae_root(cae_root) == [
        "CAE root is inside a Git work tree"
    ]
