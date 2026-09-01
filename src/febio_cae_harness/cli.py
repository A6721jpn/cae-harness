from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from febio_cae_harness import __version__
from febio_cae_harness.cli_context import (
    CaseContextError,
    CaseContextService,
    cli_failure,
    cli_success,
    dump_root_capability,
    load_answer_document,
    load_intent_document,
    load_root_capability,
)
from febio_cae_harness.model.feb import inspect_feb_file
from febio_cae_harness.model.preflight import PreflightResult, run_preflight
from febio_cae_harness.model.step import inspect_step_file
from febio_cae_harness.solver.runtime import RuntimeProbeError, probe_febio

_PREFLIGHT_EXIT_CODES = {
    "INVALID_FEB_ROOT": 2,
    "MISSING_REFERENCE": 3,
    "DUPLICATE_IDENTIFIER": 4,
}

_CONTEXT_EXIT_CODES = {
    "INVALID_INPUT": 20,
    "REGISTRY_AUTHORITY_REQUIRED": 21,
    "CASE_NOT_REGISTERED": 22,
    "REGISTRATION_CONFLICT": 23,
    "BOUNDARY_OR_IDENTITY_VIOLATION": 24,
    "EVIDENCE_INTEGRITY_FAILURE": 25,
    "STALE_INTENT_OR_QUESTION": 26,
    "IO_OR_LOCK_FAILURE": 27,
    "INTERNAL_ERROR": 70,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="febio-cae")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command")

    inspect_feb = commands.add_parser(
        "inspect-feb", help="inspect FEB XML structure and explicit references"
    )
    inspect_feb.add_argument("path", type=Path, metavar="PATH")

    preflight_feb = commands.add_parser(
        "preflight-feb", help="preflight FEB XML structure and explicit references"
    )
    preflight_feb.add_argument("path", type=Path, metavar="PATH")

    inspect_step = commands.add_parser(
        "inspect-step", help="inspect STEP structure and explicit unit evidence"
    )
    inspect_step.add_argument("path", type=Path, metavar="PATH")

    probe_febio_command = commands.add_parser(
        "probe-febio", help="read-only probe of one exact FEBio executable"
    )
    probe_febio_command.add_argument("path", type=Path, metavar="PATH")

    root = commands.add_parser("root", help="manage explicitly registered 02_CAE roots")
    root_commands = root.add_subparsers(dest="root_command", required=True)
    root_register = root_commands.add_parser(
        "register", help="register one existing exact 02_CAE root"
    )
    root_register.add_argument("--cae-root", required=True, type=Path, metavar="PATH")
    root_register.add_argument("--emit-capability", required=True, action="store_true")

    case = commands.add_parser("case", help="operate on registered case contexts")
    case_commands = case.add_subparsers(dest="case_command", required=True)
    case_create = case_commands.add_parser("create", help="create one registered case")
    case_create.add_argument("--case-id", required=True)
    case_create.add_argument("--capability-stdin", required=True, action="store_true")
    case_create.add_argument("--intent-file", required=True, type=Path, metavar="PATH")
    case_create.add_argument("--input", action="append", default=[], type=Path, metavar="PATH")
    for name in ("open", "show", "reconcile"):
        case_read = case_commands.add_parser(name, help=f"{name} one registered case")
        case_read.add_argument("--case-id", required=True)
        case_read.add_argument("--capability-stdin", required=True, action="store_true")
    case_revise = case_commands.add_parser("revise", help="append one complete intent revision")
    case_revise.add_argument("--case-id", required=True)
    case_revise.add_argument("--capability-stdin", required=True, action="store_true")
    case_revise.add_argument("--if-intent-sha256", required=True)
    case_revise.add_argument("--intent-file", required=True, type=Path, metavar="PATH")
    case_answer = case_commands.add_parser(
        "answer", help="answer the exact current authoritative question"
    )
    case_answer.add_argument("--case-id", required=True)
    case_answer.add_argument("--capability-stdin", required=True, action="store_true")
    case_answer.add_argument("--if-intent-sha256", required=True)
    case_answer.add_argument("--question-id", required=True)
    case_answer.add_argument("--answer-file", required=True, type=Path, metavar="PATH")

    commands.add_parser(
        "run-febio",
        help="disabled until a safe case-context command can reconstruct authorities",
    )
    return parser


def _case_service() -> CaseContextService:
    return CaseContextService()


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


def _preflight_exit_code(result: PreflightResult) -> int:
    for diagnostic in result.diagnostics:
        exit_code = _PREFLIGHT_EXIT_CODES.get(diagnostic.code)
        if exit_code is not None:
            return exit_code
    return 0 if result.ready else 1


def _emit_preflight(path: Path) -> int:
    try:
        inspection = inspect_feb_file(path)
    except (OSError, ValueError) as error:
        return _error(str(error))
    result = run_preflight(feb=inspection)
    print(json.dumps(result.to_dict(), sort_keys=True))
    return _preflight_exit_code(result)


def _emit_probe(path: Path) -> int:
    try:
        diagnostic = probe_febio(path)
    except (OSError, RuntimeProbeError, ValueError) as error:
        return _error(str(error))
    print(json.dumps(diagnostic.to_dict(), sort_keys=True))
    return 0


def _emit_context_json(payload: object, *, error: bool = False) -> None:
    print(
        json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True),
        file=sys.stderr if error else sys.stdout,
    )


def _context_command_name(arguments: argparse.Namespace) -> str:
    if arguments.command == "root":
        return f"root.{arguments.root_command}"
    return f"case.{arguments.case_command}"


def _read_root_capability_stdin() -> object:
    try:
        data = sys.stdin.buffer.read(16_385)
    except (AttributeError, OSError) as error:
        raise CaseContextError(
            "REGISTRY_AUTHORITY_REQUIRED", "cannot read root capability from stdin"
        ) from error
    return load_root_capability(data)


def _run_context_command(arguments: argparse.Namespace) -> int:
    command = _context_command_name(arguments)
    try:
        service = _case_service()
        if command == "root.register":
            registered = service.register_root(arguments.cae_root)
            if arguments.emit_capability:
                sys.stdout.write(dump_root_capability(registered["capability"]).decode("utf-8"))
                return 0
            payload = cli_success(command, root={"root_id": registered["root_id"]})
        elif command == "case.create":
            root_capability = _read_root_capability_stdin()
            intent = load_intent_document(arguments.intent_file)
            case = service.create_case(
                root_capability=root_capability,
                case_id=arguments.case_id,
                sources=tuple(arguments.input),
                intent=intent,
            )
            payload = cli_success(command, case=case)
        elif command in {"case.open", "case.show", "case.reconcile"}:
            root_capability = _read_root_capability_stdin()
            payload = cli_success(
                command,
                case=service.open_case(root_capability=root_capability, case_id=arguments.case_id),
            )
        elif command == "case.revise":
            root_capability = _read_root_capability_stdin()
            intent = load_intent_document(arguments.intent_file)
            case = service.revise_case(
                root_capability=root_capability,
                case_id=arguments.case_id,
                expected_intent_sha256=arguments.if_intent_sha256,
                intent=intent,
            )
            payload = cli_success(command, case=case)
        elif command == "case.answer":
            root_capability = _read_root_capability_stdin()
            value, source, detail = load_answer_document(arguments.answer_file)
            case = service.answer_case(
                root_capability=root_capability,
                case_id=arguments.case_id,
                expected_intent_sha256=arguments.if_intent_sha256,
                question_id=arguments.question_id,
                value=value,
                source=source,
                detail=detail,
            )
            payload = cli_success(command, case=case)
        else:  # pragma: no cover - parser owns the command vocabulary
            raise CaseContextError("INTERNAL_ERROR", "unknown case context command")
    except CaseContextError as error:
        _emit_context_json(cli_failure(command, error), error=True)
        return _CONTEXT_EXIT_CODES.get(error.code, 70)
    _emit_context_json(payload)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.command in {"inspect-feb", "inspect-step"}:
        return _emit_inspection(arguments.path, arguments.command)
    if arguments.command == "preflight-feb":
        return _emit_preflight(arguments.path)
    if arguments.command == "probe-febio":
        return _emit_probe(arguments.path)
    if arguments.command in {"root", "case"}:
        return _run_context_command(arguments)
    if arguments.command == "run-febio":
        return _error("run-febio is disabled until a safe case-context command is available")
    return 0
