"""Top-level command-line parser."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from febio_cae import __version__

from .case import run_case
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
    case = commands.add_parser(
        "case",
        help="register and explicitly specify a case",
    )
    case.add_argument(
        "--state-dir",
        default=None,
        help="override the product state directory (tests and isolated installations)",
    )
    case_commands = case.add_subparsers(dest="case_action", required=True)
    create = case_commands.add_parser("create", help="register a CAD source and case root")
    create.add_argument("--case-root", required=True)
    create.add_argument("--cad", required=True)
    create.add_argument("--json", action="store_true")
    inspect = case_commands.add_parser("inspect", help="read registered case metadata")
    inspect.add_argument("case_id")
    inspect.add_argument("--json", action="store_true")
    spec = case_commands.add_parser("spec", help="apply an explicit typed specification")
    spec.add_argument("case_id")
    spec.add_argument("--file", required=True)
    spec.add_argument("--expected-generation", required=True, type=int)
    spec.add_argument("--json", action="store_true")
    validate = case_commands.add_parser("validate", help="recompute validation authority")
    validate.add_argument("case_id")
    validate.add_argument("--json", action="store_true")
    freeze = case_commands.add_parser("freeze", help="create an immutable validated revision")
    freeze.add_argument("case_id")
    freeze.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    arguments = parser.parse_args(argv)

    if arguments.command == "doctor":
        return run_doctor(json_output=arguments.json)
    if arguments.command == "case":
        return run_case(arguments)

    parser.print_help()
    return 2
