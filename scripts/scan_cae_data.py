from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

PROHIBITED_DIRECTORY_NAMES = frozenset({"02_cae"})
PROHIBITED_SUFFIXES = frozenset({".fbs", ".feb", ".xplt"})
IGNORED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tmp",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "venv",
    }
)


def raise_walk_error(error: OSError) -> NoReturn:
    raise error


def find_violations(
    root: Path, *, on_error: Callable[[OSError], object] = raise_walk_error
) -> tuple[Path, ...]:
    resolved_root = root.resolve(strict=True)
    violations: list[Path] = []

    for current_root, directory_names, file_names in os.walk(resolved_root, onerror=on_error):
        current_path = Path(current_root)
        retained_directories: list[str] = []

        for directory_name in directory_names:
            normalized_name = directory_name.casefold()
            candidate = current_path / directory_name
            if normalized_name in PROHIBITED_DIRECTORY_NAMES:
                violations.append(candidate.relative_to(resolved_root))
            elif normalized_name not in IGNORED_DIRECTORY_NAMES:
                retained_directories.append(directory_name)

        directory_names[:] = retained_directories

        for file_name in file_names:
            candidate = current_path / file_name
            if candidate.suffix.casefold() in PROHIBITED_SUFFIXES:
                violations.append(candidate.relative_to(resolved_root))

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

    if violations:
        print("CAE boundary violations:", file=sys.stderr)
        for violation in violations:
            print(f"- {violation.as_posix()}", file=sys.stderr)
        return 1

    print("CAE boundary scan passed: 0 prohibited paths")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
