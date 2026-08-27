from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from febio_cae_harness import __version__
from febio_cae_harness.model.feb import inspect_feb_file
from febio_cae_harness.model.step import inspect_step_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="febio-cae")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command")

    inspect_feb = commands.add_parser(
        "inspect-feb", help="inspect FEB XML structure and explicit references"
    )
    inspect_feb.add_argument("path", type=Path, metavar="PATH")

    inspect_step = commands.add_parser(
        "inspect-step", help="inspect STEP structure and explicit unit evidence"
    )
    inspect_step.add_argument("path", type=Path, metavar="PATH")
    return parser


def _error(message: str) -> int:
    concise = " ".join(message.splitlines())
    print(f"febio-cae: {concise}", file=sys.stderr)
    return 1


def _emit_inspection(path: Path, command: str) -> int:
    try:
        inspection = inspect_feb_file(path) if command == "inspect-feb" else inspect_step_file(path)
    except (OSError, ValueError) as error:
        return _error(str(error))
    print(json.dumps(inspection.to_dict(), sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.command in {"inspect-feb", "inspect-step"}:
        return _emit_inspection(arguments.path, arguments.command)
    return 0
