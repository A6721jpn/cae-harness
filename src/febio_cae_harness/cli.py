from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from febio_cae_harness import __version__
from febio_cae_harness.model.feb import inspect_feb_file
from febio_cae_harness.model.preflight import PreflightResult, run_preflight
from febio_cae_harness.model.step import inspect_step_file
from febio_cae_harness.solver.headless import headless_exit_code, run_headless_febio
from febio_cae_harness.solver.runtime import RuntimeProbeError, probe_febio
from febio_cae_harness.solver.types import SolverLaunchError

_PREFLIGHT_EXIT_CODES = {
    "INVALID_FEB_ROOT": 2,
    "MISSING_REFERENCE": 3,
    "DUPLICATE_IDENTIFIER": 4,
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

    run_febio_command = commands.add_parser(
        "run-febio", help="run one completed FEB inside a fresh attempt root"
    )
    run_febio_command.add_argument("--executable", required=True, type=Path)
    run_febio_command.add_argument("--input", dest="input_path", required=True, type=Path)
    run_febio_command.add_argument("--attempt-root", required=True, type=Path)
    run_febio_command.add_argument("--case-id", required=True)
    run_febio_command.add_argument("--intent-id", required=True)
    run_febio_command.add_argument("--attempt-id", required=True)
    run_febio_command.add_argument("--expected-steps", type=int)
    run_febio_command.add_argument("--expected-final-time", type=float)
    run_febio_command.add_argument("--timeout-seconds", type=float)
    run_febio_command.add_argument("--argument", action="append", default=[])
    run_febio_command.add_argument("--probe-argument", action="append", default=[])
    run_febio_command.add_argument("--probe-timeout-seconds", type=float, default=5.0)
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


def _emit_run(arguments: argparse.Namespace) -> int:
    try:
        diagnostic = run_headless_febio(
            arguments.executable,
            arguments.input_path,
            arguments.attempt_root,
            case_id=arguments.case_id,
            intent_id=arguments.intent_id,
            attempt_id=arguments.attempt_id,
            expected_steps=arguments.expected_steps,
            expected_final_time=arguments.expected_final_time,
            timeout_seconds=arguments.timeout_seconds,
            arguments=tuple(arguments.argument),
            probe_arguments=tuple(arguments.probe_argument),
            probe_timeout_seconds=arguments.probe_timeout_seconds,
        )
    except (OSError, RuntimeProbeError, SolverLaunchError, ValueError, TypeError) as error:
        return _error(str(error))
    print(json.dumps(diagnostic.to_dict(), sort_keys=True))
    return headless_exit_code(diagnostic)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.command in {"inspect-feb", "inspect-step"}:
        return _emit_inspection(arguments.path, arguments.command)
    if arguments.command == "preflight-feb":
        return _emit_preflight(arguments.path)
    if arguments.command == "probe-febio":
        return _emit_probe(arguments.path)
    if arguments.command == "run-febio":
        return _emit_run(arguments)
    return 0
