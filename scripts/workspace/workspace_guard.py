from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

REQUIRED_CASE_DIRS = (
    "01_Input",
    "02_Model",
    "03_Result",
    "04_Report",
    "05_Verification",
    "90_Temporary",
)


def find_git_markers(root: Path) -> list[Path]:
    root = root.resolve()
    return sorted(
        (
            path
            for path in root.rglob(".git")
            if path.is_dir() or path.is_file()
        ),
        key=lambda path: path.as_posix(),
    )


def validate_case(case_dir: Path) -> list[str]:
    errors: list[str] = []
    manifest_path = case_dir / "CASE_MANIFEST.json"
    if not manifest_path.is_file():
        return ["Missing CASE_MANIFEST.json"]
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"Invalid CASE_MANIFEST.json: {exc}"]
    if data.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if data.get("analysis_id") != case_dir.name:
        errors.append("analysis_id must match the case directory")
    if data.get("status") not in {"active", "archived", "needs-review"}:
        errors.append("status must be active, archived, or needs-review")
    if data.get("git_prohibited") is not True:
        errors.append("git_prohibited must be true")
    for name in REQUIRED_CASE_DIRS:
        if not (case_dir / name).is_dir():
            errors.append(f"Missing directory: {name}")
    return errors


def validate_cae_root(cae_root: Path) -> list[str]:
    errors: list[str] = []
    result = subprocess.run(
        [
            "git",
            "-C",
            str(cae_root),
            "rev-parse",
            "--is-inside-work-tree",
        ],
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode == 0 and result.stdout.strip() == "true":
        errors.append("CAE root is inside a Git work tree")
    errors.extend(
        f"Git marker found: {path.relative_to(cae_root).as_posix()}"
        for path in find_git_markers(cae_root)
    )
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("cae_root", type=Path)
    args = parser.parse_args(argv)
    errors = validate_cae_root(args.cae_root)
    for error in errors:
        print(error)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
