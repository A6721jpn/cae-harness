"""Top-level command-line parser."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from febio_cae import __version__

from .doctor import run_doctor


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="febio-cae",
        description="Headless FEBio CAE Harness V2",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    commands = parser.add_subparsers(dest="command")
    doctor = commands.add_parser(
        "doctor",
        help="report registered native capabilities",
    )
    doctor.add_argument(
        "--json",
        action="store_true",
        help="emit the machine-readable doctor contract",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    arguments = parser.parse_args(argv)

    if arguments.command == "doctor":
        return run_doctor(json_output=arguments.json)

    parser.print_help()
    return 2
