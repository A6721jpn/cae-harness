from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

PROHIBITED_DIRECTORY_NAMES = frozenset(
    {
        "01_input",
        "02_cae",
        "02_model",
        "03_result",
        "04_report",
        "05_verification",
        "90_temporary",
    }
)
PROHIBITED_FILE_NAMES = frozenset({"case_manifest.json"})
PROHIBITED_SUFFIXES = frozenset({".fbs", ".feb", ".log", ".step", ".stp", ".xplt"})
IGNORED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".pytest_tmp",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "venv",
    }
)
IGNORED_UNTRACKED_FILE_NAMES = frozenset({"debug.log"})


class GitInspectionError(RuntimeError):
    """Raised when a repository index cannot be inspected reliably."""


def raise_walk_error(error: OSError) -> NoReturn:
    raise error


def is_prohibited_path(path: Path) -> bool:
    normalized_parts = tuple(part.casefold() for part in path.parts)
    return (
        any(part in PROHIBITED_DIRECTORY_NAMES for part in normalized_parts)
        or path.name.casefold() in PROHIBITED_FILE_NAMES
        or path.suffix.casefold() in PROHIBITED_SUFFIXES
    )


def git_index_paths(root: Path) -> tuple[Path, ...]:
    git_marker = root / ".git"
    try:
        top_level = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="surrogateescape",
            text=True,
        )
    except FileNotFoundError as error:
        if git_marker.exists():
            raise GitInspectionError("git executable is unavailable") from error
        return ()

    if top_level.returncode != 0:
        if git_marker.exists():
            detail = top_level.stderr.strip() or "git rev-parse failed"
            raise GitInspectionError(detail)
        return ()

    discovered_root = Path(top_level.stdout.strip()).resolve(strict=True)
    if discovered_root != root:
        return ()

    tracked = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--cached"],
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="surrogateescape",
        text=True,
    )
    if tracked.returncode != 0:
        detail = tracked.stderr.strip() or "git ls-files failed"
        raise GitInspectionError(detail)

    return tuple(Path(item) for item in tracked.stdout.split("\0") if item)


def find_violations(
    root: Path, *, on_error: Callable[[OSError], object] = raise_walk_error
) -> tuple[Path, ...]:
    resolved_root = root.resolve(strict=True)
    violations: set[Path] = set()

    for current_root, directory_names, file_names in os.walk(resolved_root, onerror=on_error):
        current_path = Path(current_root)
        retained_directories: list[str] = []

        for directory_name in directory_names:
            normalized_name = directory_name.casefold()
            candidate = current_path / directory_name
            if normalized_name in PROHIBITED_DIRECTORY_NAMES:
                violations.add(candidate.relative_to(resolved_root))
            elif normalized_name not in IGNORED_DIRECTORY_NAMES:
                retained_directories.append(directory_name)

        directory_names[:] = retained_directories

        for file_name in file_names:
            candidate = current_path / file_name
            relative_candidate = candidate.relative_to(resolved_root)
            if file_name.casefold() not in IGNORED_UNTRACKED_FILE_NAMES and is_prohibited_path(
                relative_candidate
            ):
                violations.add(relative_candidate)

    for tracked_path in git_index_paths(resolved_root):
        if is_prohibited_path(tracked_path):
            violations.add(tracked_path)

    return tuple(sorted(violations, key=lambda path: path.as_posix().casefold()))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reject real CAE data in the tool repository")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        violations = find_violations(arguments.root)
    except OSError as error:
        location = error.filename or arguments.root
        print(
            f"CAE boundary scan failed: cannot inspect {location}: {error.strerror}",
            file=sys.stderr,
        )
        return 2
    except GitInspectionError as error:
        print(f"CAE boundary scan failed: cannot inspect Git index: {error}", file=sys.stderr)
        return 2

    if violations:
        print("CAE boundary violations:", file=sys.stderr)
        for violation in violations:
            print(f"- {violation.as_posix()}", file=sys.stderr)
        return 1

    print("CAE boundary scan passed: 0 prohibited paths")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
