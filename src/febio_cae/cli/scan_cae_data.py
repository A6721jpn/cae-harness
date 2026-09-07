"""Scan a source tree for files and content that must remain outside Git."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

SCHEMA_VERSION = "1"
EXIT_OK = 0
EXIT_INPUT_ERROR = 2
EXIT_BOUNDARY_REJECTED = 4
MAX_CONTENT_BYTES = 1_048_576

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

INCOMPLETE_CODES = frozenset(
    {
        "CONTENT_TOO_LARGE",
        "FILESYSTEM_READ_ERROR",
        "GIT_BLOB_READ_ERROR",
        "GIT_INDEX_PARSE_ERROR",
        "GIT_PATH_INVALID",
        "GIT_ROOT_MISMATCH",
        "GIT_TRACKING_UNAVAILABLE",
        "UNSAFE_REPARSE_POINT",
        "UNSUPPORTED_GIT_MODE",
    }
)
_FEBIO_ROOT = re.compile(rb"<febio_spec(?:\s|>)", re.IGNORECASE)
_FEBIO_CLOSE = re.compile(rb"</febio_spec\s*>", re.IGNORECASE)
_PRIVATE_KEY = re.compile(
    rb"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----\s*"
    rb"([A-Z0-9+/=\r\n]{64,})\s*"
    rb"-----END(?: [A-Z0-9]+)? PRIVATE KEY-----",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ScanIssue:
    code: str
    path: str
    message: str


@dataclass(frozen=True)
class GitIndexEntry:
    mode: str
    object_id: str
    path: str


def _normalise_parts(relative_path: str) -> tuple[str, ...]:
    return tuple(part.casefold() for part in relative_path.replace("\\", "/").split("/") if part)


def _is_excluded(relative_path: str) -> bool:
    return _excluded_component(relative_path) is not None


def _excluded_component(relative_path: str) -> str | None:
    for original, normalised in zip(
        relative_path.replace("\\", "/").split("/"), _normalise_parts(relative_path), strict=True
    ):
        if (
            normalised in EXCLUDED_DIRECTORIES
            or normalised.endswith(".egg-info")
            or normalised.startswith(".venv-")
        ):
            return original
    return None


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


def _is_safe_git_path(relative_path: str) -> bool:
    path = PurePosixPath(relative_path.replace("\\", "/"))
    return not path.is_absolute() and ".." not in path.parts


def _git_index_entries(root: Path) -> tuple[bool, tuple[GitIndexEntry, ...], str | None]:
    try:
        root_probe = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True,
            check=False,
        )
    except OSError as error:
        return False, (), str(error)
    if root_probe.returncode != 0:
        message = root_probe.stderr.decode("utf-8", errors="replace").strip()
        return False, (), message or "Git root could not be resolved."

    git_root = Path(root_probe.stdout.decode("utf-8", errors="surrogateescape").strip())
    if os.path.normcase(str(git_root.resolve())) != os.path.normcase(str(root.resolve())):
        return (
            False,
            (),
            "GIT_ROOT_MISMATCH: the requested scan root is not the Git worktree root.",
        )

    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-s", "-z"],
            capture_output=True,
            check=False,
        )
    except OSError as error:
        return False, (), str(error)
    if completed.returncode != 0:
        message = completed.stderr.decode("utf-8", errors="replace").strip()
        return False, (), message or "git ls-files failed."

    entries: list[GitIndexEntry] = []
    for raw_entry in completed.stdout.split(b"\0"):
        if not raw_entry:
            continue
        try:
            header, raw_path = raw_entry.split(b"\t", maxsplit=1)
            raw_mode, raw_object_id, _raw_stage = header.split(b" ", maxsplit=2)
        except ValueError:
            return False, (), "Malformed Git index entry returned by git ls-files."
        entries.append(
            GitIndexEntry(
                mode=raw_mode.decode("ascii", errors="replace"),
                object_id=raw_object_id.decode("ascii", errors="replace"),
                path=raw_path.decode("utf-8", errors="surrogateescape"),
            )
        )
    return True, tuple(entries), None


def _git_tracked_paths(root: Path) -> tuple[bool, tuple[str, ...], str | None]:
    available, entries, error = _git_index_entries(root)
    return available, tuple(entry.path for entry in entries), error


def _read_git_blob(root: Path, object_id: str) -> tuple[bytes | None, str | None]:
    try:
        size_result = subprocess.run(
            ["git", "-C", str(root), "cat-file", "-s", object_id],
            capture_output=True,
            check=False,
        )
    except OSError as error:
        return None, str(error)
    if size_result.returncode != 0:
        return None, size_result.stderr.decode("utf-8", errors="replace").strip()
    try:
        size = int(size_result.stdout.strip())
    except ValueError:
        return None, "Git returned an invalid blob size."
    if size > MAX_CONTENT_BYTES:
        return None, f"content is larger than {MAX_CONTENT_BYTES} bytes"

    try:
        content_result = subprocess.run(
            ["git", "-C", str(root), "cat-file", "blob", object_id],
            capture_output=True,
            check=False,
        )
    except OSError as error:
        return None, str(error)
    if content_result.returncode != 0:
        return None, content_result.stderr.decode("utf-8", errors="replace").strip()
    if len(content_result.stdout) != size:
        return None, "Git blob size changed while it was being inspected."
    return content_result.stdout, None


def _content_issue(relative_path: str, content: bytes, *, source: str) -> ScanIssue | None:
    prefix = content
    if prefix.startswith(b"\xef\xbb\xbf"):
        prefix = prefix[3:]
    prefix = prefix.lstrip(b" \t\r\n")
    if prefix.startswith(b"<?xml"):
        declaration_end = prefix.find(b"?>")
        if declaration_end >= 0:
            prefix = prefix[declaration_end + 2 :].lstrip(b" \t\r\n")
    if _FEBIO_ROOT.match(prefix) and _FEBIO_CLOSE.search(content):
        return ScanIssue(
            code="FORBIDDEN_CAE_CONTENT",
            path=relative_path,
            message=f"Recognizable FEBio XML content was found in {source} bytes.",
        )
    if _PRIVATE_KEY.search(content):
        return ScanIssue(
            code="FORBIDDEN_SENSITIVE_CONTENT",
            path=relative_path,
            message=f"Recognizable private-key PEM content was found in {source} bytes.",
        )
    return None


def _merge_issue(issues: dict[tuple[str, str], ScanIssue], issue: ScanIssue) -> None:
    issues[(issue.code, issue.path)] = issue


def _inspect_git_index(
    root: Path, entries: Iterable[GitIndexEntry]
) -> tuple[tuple[ScanIssue, ...], int]:
    issues: dict[tuple[str, str], ScanIssue] = {}
    content_checked = 0
    for entry in entries:
        if not _is_safe_git_path(entry.path):
            _merge_issue(
                issues,
                ScanIssue(
                    code="GIT_PATH_INVALID",
                    path=entry.path,
                    message="Git returned a path outside the requested repository root.",
                ),
            )
            continue
        if _is_excluded(entry.path):
            continue
        path_issue = _issue_for_path(entry.path)
        if path_issue:
            _merge_issue(issues, path_issue)
        if entry.mode not in {"100644", "100755"}:
            _merge_issue(
                issues,
                ScanIssue(
                    code="UNSUPPORTED_GIT_MODE",
                    path=entry.path,
                    message=(
                        f"Git index mode {entry.mode} is not a regular file mode; "
                        "the entry was not inspected as content."
                    ),
                ),
            )
            continue
        if path_issue:
            continue
        content, error = _read_git_blob(root, entry.object_id)
        if error:
            code = "CONTENT_TOO_LARGE" if "larger than" in error else "GIT_BLOB_READ_ERROR"
            _merge_issue(
                issues,
                ScanIssue(
                    code=code,
                    path=entry.path,
                    message=f"Could not inspect Git index content: {error}",
                ),
            )
            continue
        assert content is not None
        content_checked += 1
        content_issue = _content_issue(entry.path, content, source="Git index")
        if content_issue:
            _merge_issue(issues, content_issue)
    return tuple(
        sorted(issues.values(), key=lambda issue: (issue.path, issue.code))
    ), content_checked


def _is_reparse_point(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _filesystem_error_path(root: Path, error: OSError) -> str:
    filename = getattr(error, "filename", None)
    if not filename:
        return "."
    candidate = Path(filename)
    try:
        return _relative_path(root, candidate.resolve())
    except ValueError:
        return str(candidate)


def _scan_filesystem(root: Path) -> tuple[tuple[ScanIssue, ...], int, tuple[str, ...]]:
    issues: dict[tuple[str, str], ScanIssue] = {}
    checked_files = 0
    excluded_paths: set[str] = set()

    def onerror(error: OSError) -> None:
        relative = _filesystem_error_path(root, error)
        _merge_issue(
            issues,
            ScanIssue(
                code="FILESYSTEM_READ_ERROR",
                path=relative,
                message=f"Filesystem traversal failed: {error}",
            ),
        )

    for current, directories, files in os.walk(
        root, topdown=True, followlinks=False, onerror=onerror
    ):
        current_path = Path(current)
        if current_path != root and _is_reparse_point(current_path):
            _merge_issue(
                issues,
                ScanIssue(
                    code="UNSAFE_REPARSE_POINT",
                    path=_relative_path(root, current_path),
                    message="A symlink or Windows reparse point was not traversed.",
                ),
            )
            directories[:] = []
            continue
        relative_current = "." if current_path == root else _relative_path(root, current_path)
        retained_directories: list[str] = []
        for directory in tuple(directories):
            relative_directory = (
                directory if relative_current == "." else f"{relative_current}/{directory}"
            )
            if _is_excluded(relative_directory):
                excluded_paths.add(_excluded_component(relative_directory) or relative_directory)
                continue
            directory_path = current_path / directory
            if _is_reparse_point(directory_path):
                _merge_issue(
                    issues,
                    ScanIssue(
                        code="UNSAFE_REPARSE_POINT",
                        path=relative_directory,
                        message="A symlink or Windows reparse point was not traversed.",
                    ),
                )
                continue
            if directory.casefold() == "02_cae":
                issue = _issue_for_path(relative_directory)
                if issue:
                    _merge_issue(issues, issue)
            retained_directories.append(directory)
        directories[:] = retained_directories

        for filename in files:
            relative_file = (
                filename if relative_current == "." else f"{relative_current}/{filename}"
            )
            if _is_excluded(relative_file):
                excluded_paths.add(_excluded_component(relative_file) or relative_file)
                continue
            file_path = current_path / filename
            if _is_reparse_point(file_path):
                _merge_issue(
                    issues,
                    ScanIssue(
                        code="UNSAFE_REPARSE_POINT",
                        path=relative_file,
                        message="A symlink or Windows reparse point was not inspected.",
                    ),
                )
                continue
            checked_files += 1
            path_issue = _issue_for_path(relative_file)
            if path_issue:
                _merge_issue(issues, path_issue)
                continue
            try:
                with file_path.open("rb") as stream:
                    content = stream.read(MAX_CONTENT_BYTES + 1)
            except OSError as error:
                _merge_issue(
                    issues,
                    ScanIssue(
                        code="FILESYSTEM_READ_ERROR",
                        path=relative_file,
                        message=f"Filesystem content read failed: {error}",
                    ),
                )
                continue
            if len(content) > MAX_CONTENT_BYTES:
                _merge_issue(
                    issues,
                    ScanIssue(
                        code="CONTENT_TOO_LARGE",
                        path=relative_file,
                        message=f"Content exceeds the {MAX_CONTENT_BYTES}-byte inspection limit.",
                    ),
                )
                continue
            content_issue = _content_issue(relative_file, content, source="worktree")
            if content_issue:
                _merge_issue(issues, content_issue)

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
        tracking_available, entries, tracking_error = _git_index_entries(root)
        tracked = tuple(entry.path for entry in entries)
    else:
        tracking_available = True
        entries = ()
        tracking_error = None
        tracked = tuple(tracked_paths)

    filesystem_issues, checked_files, excluded_paths = _scan_filesystem(root)
    issues: dict[tuple[str, str], ScanIssue] = {
        (issue.code, issue.path): issue for issue in filesystem_issues
    }
    index_content_checked = 0
    if tracking_available:
        index_issues, index_content_checked = _inspect_git_index(root, entries)
        for issue in index_issues:
            _merge_issue(issues, issue)
    else:
        error_code = (
            "GIT_ROOT_MISMATCH"
            if tracking_error and tracking_error.startswith("GIT_ROOT_MISMATCH:")
            else "GIT_TRACKING_UNAVAILABLE"
        )
        _merge_issue(
            issues,
            ScanIssue(
                code=error_code,
                path=".",
                message=tracking_error or "Git tracking could not be queried for this root.",
            ),
        )

    tracked_excluded: list[str] = []
    for path in tracked:
        if _is_excluded(path):
            tracked_excluded.append(path)
            _merge_issue(
                issues,
                ScanIssue(
                    code="TRACKED_EXCLUDED_PATH",
                    path=path,
                    message="An explicitly excluded generated path is tracked by Git.",
                ),
            )
        path_issue = _issue_for_path(path)
        if path_issue:
            _merge_issue(issues, path_issue)

    ordered_issues = tuple(sorted(issues.values(), key=lambda issue: (issue.path, issue.code)))
    diagnostics = [
        {
            "code": issue.code,
            "severity": "error",
            "message": issue.message,
        }
        for issue in ordered_issues
        if issue.code in INCOMPLETE_CODES
    ]
    if any(issue.code in INCOMPLETE_CODES for issue in ordered_issues):
        status = "INCOMPLETE"
    elif ordered_issues:
        status = "REJECT"
    else:
        status = "PASS"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "root": str(root),
        "checked_files": checked_files,
        "tracked_files_checked": len(tracked),
        "index_content_checked": index_content_checked,
        "git_tracking": {
            "available": tracking_available,
            "tracked_files": len(tracked),
            "excluded_tracked_files": len(tracked_excluded),
        },
        "excluded_paths": list(excluded_paths),
        "issues": [asdict(issue) for issue in ordered_issues],
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
    return EXIT_BOUNDARY_REJECTED if report["status"] != "PASS" else EXIT_OK
