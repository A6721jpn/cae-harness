"""Top-level command-line parser."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from febio_cae import __version__

from .case import run_case
from .compare import COMPARISON_HELP, run_compare
from .doctor import run_doctor
from .run import run_lifecycle


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
    inspect.add_argument("--native", action="store_true", help="observe registered STEP topology")
    inspect.add_argument("--wall-seconds", type=float, default=600)
    inspect.add_argument("--cpu-workers", type=int, default=None)
    prepare = case_commands.add_parser(
        "prepare-planar", help="prepare the explicit registered planar case"
    )
    prepare.add_argument("case_id")
    prepare.add_argument("--file", required=True)
    prepare.add_argument("--expected-generation", required=True, type=int)
    prepare.add_argument("--json", action="store_true")
    spec = case_commands.add_parser("spec", help="apply an explicit typed specification")
    spec.add_argument("case_id")
    spec.add_argument("--file", required=True)
    spec.add_argument("--expected-generation", required=True, type=int)
    spec.add_argument("--json", action="store_true")
    for action in ("intent", "answer", "edit"):
        natural = case_commands.add_parser(
            action,
            help="bounded affirmative clauses; no inferred physics",
            description="Whole field = value clauses only. Material model/E/nu/applicability or explicit registered component adoption. E edit only; no automatic execution.",
        )
        natural.add_argument("case_id")
        natural.add_argument("--text", required=True)
        natural.add_argument("--expected-generation", required=True, type=int)
        natural.add_argument("--operation-id", required=True)
        natural.add_argument(
            "--llm-settings",
            required=True,
            help="explicit provider/model/key ENV NAME/Budget/input/output/socket JSON; local 2*I+O reservation, not provider billing",
        )
        natural.add_argument("--json", action="store_true")
        if action == "answer":
            natural.add_argument("--question", required=True)
        if action == "edit":
            natural.add_argument("--base", required=True)
    patch = case_commands.add_parser("patch", help="apply a parent-bound typed CasePatch")
    patch.add_argument("case_id")
    patch.add_argument("--file", required=True)
    patch.add_argument("--expected-generation", required=True, type=int)
    patch.add_argument("--json", action="store_true")
    validate = case_commands.add_parser("validate", help="recompute validation authority")
    validate.add_argument("case_id")
    validate.add_argument("--json", action="store_true")
    freeze = case_commands.add_parser("freeze", help="create an immutable validated revision")
    freeze.add_argument("case_id")
    freeze.add_argument("--json", action="store_true")
    demo = case_commands.add_parser("run-demo", help="execute the registered planar prototype")
    demo.add_argument("case_id")
    demo.add_argument("--revision-id", required=True)
    demo.add_argument("--solver", required=True)
    demo.add_argument("--preflight", action="store_true", help="compile only; no native launch")
    demo.add_argument("--json", action="store_true")
    preview = case_commands.add_parser(
        "preview", help="observe one existing Studio session without launching"
    )
    preview.add_argument("case_id")
    preview.add_argument("--manifest-id", required=True)
    preview.add_argument("--window-id", type=int, required=True)
    preview.add_argument("--studio", required=True)
    preview.add_argument("--timeout", type=float, required=True)
    preview.add_argument("--json", action="store_true")
    preview_status = case_commands.add_parser(
        "preview-status", help="revalidate stored preview evidence"
    )
    preview_status.add_argument("case_id")
    preview_status.add_argument("--preview-id", required=True)
    preview_status.add_argument("--json", action="store_true")
    compare = commands.add_parser(
        "compare",
        help="compare registered planar material-edit force and part-displacement curves",
        description=COMPARISON_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    compare.add_argument("baseline_run")
    compare.add_argument("candidate_run")
    compare.add_argument("--case-id", required=True)
    compare.add_argument("--state-dir", default=None)
    compare.add_argument("--spec", required=True, help="explicit common ComparisonSpec JSON")
    compare.add_argument("--json", action="store_true")
    for name in ("status", "resume", "cancel"):
        run = commands.add_parser(
            name,
            help="read run state"
            if name == "status"
            else "cancel registered synchronous CREATED run before preparation"
            if name == "cancel"
            else "diagnose interrupted synchronous publication without restarting",
        )
        run.add_argument("run_id")
        run.add_argument("--case-id", required=True)
        run.add_argument("--state-dir", default=None)
        run.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    arguments = parser.parse_args(argv)

    if arguments.command == "doctor":
        return run_doctor(json_output=arguments.json)
    if arguments.command == "case":
        return run_case(arguments)
    if arguments.command == "compare":
        return run_compare(arguments)
    if arguments.command in {"status", "resume", "cancel"}:
        return run_lifecycle(arguments)

    parser.print_help()
    return 2
