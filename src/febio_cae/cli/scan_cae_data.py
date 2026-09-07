"""Scan a source tree for files that must remain outside the Git product."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

SCHEMA_VERSION = "1"
EXIT_OK = 0
EXIT_INPUT_ERROR = 2
EXIT_BOUNDARY_REJECTED = 4

EXCLUDED_DIRECTORIES = frozenset(
    {
        ".git",
        ".local",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        ".venv-",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
    }
)
FORBIDDEN_SUFFIXES = frozenset(
    {
        ".dmp",
        ".f3d",
        ".fbs",
        ".feb",
        ".fs2",
        ".key",
        ".lnk",
        ".pem",
        ".sldasm",
        ".sldprt",
        ".sqlite",
        ".sqlite3",
        ".stp",
        ".step",
        ".xplt",
    }
)
FORBIDDEN_NAMES = frozenset(
    {
        "auth.json",
        "credentials.json",
        "desktop.ini",
        "secrets.json",
        "token.json",
    }
)


@dataclass(frozen=True)
class ScanIssue:
    code: str
    path: str
    message: str


def _normalise_parts(relative_path: str) -> tuple[str, ...]:
    return tuple(part.casefold() for part in relative_path.replace("\\", "/").split("/") if part)


def _is_excluded(relative_path: str) -> bool:
    parts = _normalise_parts(relative_path)
    return any(
        part in EXCLUDED_DIRECTORIES or part.endswith(".egg-info") or part.startswith(".venv-")
        for part in parts
    )


def _relative_path(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _issue_for_path(relative_path: str) -> ScanIssue | None:
    normalised = _normalise_parts(relative_path)
    if "02_cae" in normalised:
        return ScanIssue(
            code="FORBIDDEN_CAE_DATA",
            path=relative_path,
            message="02_CAE data is outside the product repository boundary.",
        )

    name = normalised[-1] if normalised else ""
    suffix = Path(name).suffix.casefold()
    if suffix in FORBIDDEN_SUFFIXES:
        return ScanIssue(
            code="FORBIDDEN_CAE_DATA",
            path=relative_path,
            message=f"CAE or desktop-state extension is forbidden: {suffix}",
        )
    if (
        name in FORBIDDEN_NAMES
        or name == ".env"
        or (name.startswith(".env.") and name != ".env.example")
        or any(
            name.startswith(prefix) and name.endswith(".json")
            for prefix in ("auth", "credential", "secret", "token")
        )
    ):
        return ScanIssue(
            code="FORBIDDEN_SENSITIVE_DATA",
            path=relative_path,
            message="Credentials, tokens, or desktop state must not enter the product repository.",
        )
    return None


def _git_tracked_paths(root: Path) -> tuple[bool, tuple[str, ...], str | None]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True,
            check=False,
        )
    except OSError as error:
        return False, (), str(error)
    if completed.returncode != 0:
        message = (
            completed.stderr.decode("utf-8", errors="replace").strip() or "git ls-files failed"
        )
        return False, (), message
    paths = tuple(
        item.decode("utf-8", errors="surrogateescape")
        for item in completed.stdout.split(b"\0")
        if item
    )
    return True, paths, None


def _scan_filesystem(root: Path) -> tuple[tuple[ScanIssue, ...], int, tuple[str, ...]]:
    issues: dict[tuple[str, str], ScanIssue] = {}
    checked_files = 0
    excluded_paths: set[str] = set()

    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        relative_current = "." if current_path == root else _relative_path(root, current_path)
        for directory in tuple(directories):
            relative_directory = (
                directory if relative_current == "." else f"{relative_current}/{directory}"
            )
            if _is_excluded(relative_directory):
                excluded_paths.add(relative_directory.split("/", maxsplit=1)[0])
            if directory.casefold() == "02_cae":
                issue = _issue_for_path(relative_directory)
                if issue:
                    issues[(issue.code, issue.path)] = issue
        directories[:] = [
            directory
            for directory in directories
            if not _is_excluded(
                directory if relative_current == "." else f"{relative_current}/{directory}"
            )
        ]

        for filename in files:
            relative_file = (
                filename if relative_current == "." else f"{relative_current}/{filename}"
            )
            if _is_excluded(relative_file):
                excluded_paths.add(relative_file.split("/", maxsplit=1)[0])
                continue
            checked_files += 1
            issue = _issue_for_path(relative_file)
            if issue:
                issues[(issue.code, issue.path)] = issue

    return (
        tuple(sorted(issues.values(), key=lambda issue: (issue.path, issue.code))),
        checked_files,
        tuple(sorted(excluded_paths)),
    )


def scan_root(root: Path, *, tracked_paths: Iterable[str] | None = None) -> dict[str, object]:
    """Return a structured, non-mutating boundary report for ``root``."""

    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"scan root is not a directory: {root}")

    if tracked_paths is None:
        tracking_available, discovered_paths, tracking_error = _git_tracked_paths(root)
        tracked = discovered_paths
    else:
        tracking_available = True
        tracking_error = None
        tracked = tuple(tracked_paths)

    issues, checked_files, excluded_paths = _scan_filesystem(root)
    tracked_excluded: list[str] = []
    for path in tracked:
        if _is_excluded(path):
            tracked_excluded.append(path)
        issue = _issue_for_path(path)
        if issue:
            issues = tuple(
                {(existing.code, existing.path): existing for existing in (*issues, issue)}.values()
            )
    if tracked_excluded:
        issues = tuple(
            {
                (existing.code, existing.path): existing
                for existing in (
                    *issues,
                    *(
                        ScanIssue(
                            code="TRACKED_EXCLUDED_PATH",
                            path=path,
                            message="An explicitly excluded generated path is tracked by Git.",
                        )
                        for path in tracked_excluded
                    ),
                )
            }.values()
        )
    issues = tuple(sorted(issues, key=lambda issue: (issue.path, issue.code)))

    diagnostics: list[dict[str, str]] = []
    if not tracking_available:
        diagnostics.append(
            {
                "code": "GIT_TRACKING_UNAVAILABLE",
                "severity": "warning",
                "message": tracking_error or "Git tracking could not be queried for this root.",
            }
        )
    if issues:
        status = "REJECT"
    elif diagnostics:
        status = "PASS_WITH_WARNINGS"
    else:
        status = "PASS"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "root": str(root),
        "checked_files": checked_files,
        "tracked_files_checked": len(tracked),
        "git_tracking": {
            "available": tracking_available,
            "tracked_files": len(tracked),
            "excluded_tracked_files": len(tracked_excluded),
        },
        "excluded_paths": list(excluded_paths),
        "issues": [asdict(issue) for issue in issues],
        "diagnostics": diagnostics,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scan the repository CAE data boundary.")
    parser.add_argument("--root", type=Path, default=Path("."), help="repository root to scan")
    parser.add_argument("--json", action="store_true", help="emit the machine-readable report")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    try:
        report = scan_root(arguments.root)
    except ValueError as error:
        print(
            json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "status": "INVALID_INPUT",
                    "diagnostics": [{"code": "INVALID_ROOT", "message": str(error)}],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return EXIT_INPUT_ERROR

    print(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return EXIT_BOUNDARY_REJECTED if report["status"] == "REJECT" else EXIT_OK
