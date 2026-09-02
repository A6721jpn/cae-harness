from __future__ import annotations

import argparse
import json
import math
import secrets
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from febio_cae_harness import __version__
from febio_cae_harness.autonomy import RetryLedger, decide_retry, transition_intent
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
from febio_cae_harness.contracts import IntentState
from febio_cae_harness.evidence import EvidenceIntegrityError, EvidenceStore
from febio_cae_harness.model.completeness import assess_authoritative_completeness
from febio_cae_harness.model.feb import inspect_feb_file
from febio_cae_harness.model.preflight import PreflightResult, run_preflight
from febio_cae_harness.model.step import inspect_step_file
from febio_cae_harness.solver.execution import (
    ExecutionAuthorityError,
    claim_execution_outputs,
    issue_execution_authority,
    record_execution_output_artifacts,
)
from febio_cae_harness.solver.headless import (
    HeadlessConfigurationError,
    recover_headless_febio,
    run_headless_febio,
)
from febio_cae_harness.solver.runtime import RuntimeProbeError, probe_febio
from febio_cae_harness.solver.types import (
    SolverClassification,
    SolverConfigurationError,
    SolverOwnershipError,
    SolverState,
)
from febio_cae_harness.workspace import AttemptWorkspace, WorkspaceBoundaryError

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

_RUN_FAILURE_EXIT = 4
_FBS_UNAVAILABLE_EXIT = 5


class _RunCommandError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("value must be a positive integer") from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


def _positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("value must be a positive finite number") from error
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a positive finite number")
    return parsed


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

    run_febio = commands.add_parser(
        "run-febio",
        help="run one registered FEB case through the headless authority boundary",
    )
    run_febio.add_argument("--capability-stdin", required=True, action="store_true")
    run_febio.add_argument("--case-id", required=True)
    run_febio.add_argument("--input-name")
    run_febio.add_argument("--runtime-probe", required=True, type=Path, metavar="PATH")
    run_febio.add_argument("--expected-steps", required=True, type=_positive_int)
    run_febio.add_argument("--expected-final-time", required=True, type=_positive_float)
    run_febio.add_argument(
        "--requested-field",
        required=True,
        action="append",
        metavar="FIELD",
    )
    run_febio.add_argument("--timeout-seconds", type=_positive_float)

    reconnect_febio = commands.add_parser(
        "reconnect-febio",
        help="recover one recorded running FEBio attempt through exact authorities",
    )
    reconnect_febio.add_argument("--capability-stdin", required=True, action="store_true")
    reconnect_febio.add_argument("--case-id", required=True)
    reconnect_febio.add_argument("--attempt-id", required=True)
    reconnect_febio.add_argument("--runtime-probe", required=True, type=Path, metavar="PATH")
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
    except OSError:
        context_error = CaseContextError(
            "IO_OR_LOCK_FAILURE", "case context I/O failed", retryable=True
        )
        _emit_context_json(cli_failure(command, context_error), error=True)
        return _CONTEXT_EXIT_CODES[context_error.code]
    _emit_context_json(payload)
    return 0


def _run_failure(
    code: str,
    message: str,
    *,
    command: str = "run-febio",
    retryable: bool = False,
    attempt: Mapping[str, object] | None = None,
    solver: Mapping[str, object] | None = None,
) -> dict[str, object]:
    payload = cli_failure(
        command,
        CaseContextError(code, message, retryable=retryable),
    )
    if attempt is not None:
        payload["attempt"] = dict(attempt)
    if solver is not None:
        payload["solver"] = dict(solver)
    return payload


def _fail_run(error: _RunCommandError, *, command: str = "run-febio") -> int:
    _emit_context_json(
        _run_failure(
            error.code,
            str(error),
            command=command,
            retryable=error.retryable,
        ),
        error=True,
    )
    return _RUN_FAILURE_EXIT


def _append_run_terminal(
    store: EvidenceStore,
    attempt_id: str,
    status: str,
    *,
    solver: Mapping[str, object] | None = None,
) -> bool:
    payload: dict[str, object] = {
        "attempt_id": attempt_id,
        "official_fbs": False,
        "status": status,
    }
    if solver is not None:
        payload.update(
            {
                "classification": solver["classification"],
                "return_code": solver["return_code"],
                "solver_state": solver["state"],
            }
        )
    try:
        store.record_attempt_terminal(attempt_id, payload)
    except (EvidenceIntegrityError, OSError, ValueError, WorkspaceBoundaryError):
        return False
    return True


def _fail_run_after_terminal(
    store: EvidenceStore | None,
    attempt_id: str | None,
    status: str,
    error: _RunCommandError,
    *,
    command: str = "run-febio",
) -> int:
    if (
        store is not None
        and attempt_id is not None
        and not _append_run_terminal(store, attempt_id, status)
    ):
        error = _RunCommandError(
            "EVIDENCE_INTEGRITY_FAILURE",
            "run terminal evidence could not be recorded",
        )
    return _fail_run(error, command=command)


def _select_feb_input(
    inputs: object,
    selected_name: object,
) -> tuple[Path, str, str]:
    if selected_name is not None and (
        not isinstance(selected_name, str)
        or not selected_name
        or Path(selected_name).name != selected_name
        or "/" in selected_name
        or "\\" in selected_name
    ):
        raise _RunCommandError("INVALID_INPUT", "input_name must be one exact basename")
    if not isinstance(inputs, list):
        raise _RunCommandError("EVIDENCE_INTEGRITY_FAILURE", "case inputs are invalid")
    candidates: list[tuple[Path, str, str]] = []
    for raw in inputs:
        if not isinstance(raw, Mapping):
            raise _RunCommandError("EVIDENCE_INTEGRITY_FAILURE", "case inputs are invalid")
        path_value = raw.get("path")
        digest = raw.get("sha256")
        if not isinstance(path_value, str) or not isinstance(digest, str):
            raise _RunCommandError("EVIDENCE_INTEGRITY_FAILURE", "case inputs are invalid")
        path = Path(path_value)
        if path.suffix.casefold() != ".feb":
            continue
        name = path.name
        if selected_name is None or name == selected_name:
            candidates.append((path, name, digest))
    if not candidates:
        code = "INPUT_NOT_FOUND" if selected_name is not None else "INPUT_SELECTION_REQUIRED"
        raise _RunCommandError(code, "registered FEB input selection failed")
    if len(candidates) != 1:
        raise _RunCommandError(
            "INPUT_SELECTION_REQUIRED",
            "multiple registered FEB inputs require one exact input_name",
        )
    return candidates[0]


def _run_febio_command(arguments: argparse.Namespace) -> int:
    terminal_store: EvidenceStore | None = None
    attempt_id: str | None = None
    try:
        service = _case_service()
        root_capability = _read_root_capability_stdin()
        opened = service._open_context(root_capability, arguments.case_id)
        if opened.result.state is not IntentState.BOUND or opened.result.question is not None:
            raise _RunCommandError(
                "ASK_AND_BLOCK",
                "case intent is not authoritatively bound",
            )
        snapshot = opened.result.snapshot
        completeness = assess_authoritative_completeness(snapshot)
        if completeness.state != IntentState.BOUND.value:
            raise _RunCommandError(
                "ASK_AND_BLOCK",
                "required physical conditions are not authoritatively bound",
            )
        manifest = opened.store.manifest
        source_relative, input_name, expected_sha256 = _select_feb_input(
            manifest.get("inputs"),
            arguments.input_name,
        )
        if not arguments.runtime_probe.is_absolute():
            raise _RunCommandError(
                "INVALID_INPUT",
                "runtime_probe must be one absolute executable path",
            )
        requested_fields = tuple(cast(list[str], arguments.requested_field))
        if any(not field.strip() for field in requested_fields) or len(
            set(requested_fields)
        ) != len(requested_fields):
            raise _RunCommandError(
                "INVALID_INPUT",
                "requested fields must be non-empty and unique",
            )

        attempt_id = f"run-{secrets.token_hex(16)}"
        opened.store.record_attempt(attempt_id, {"status": "started"})
        terminal_store = opened.store
        attempt = AttemptWorkspace._from_manager(
            opened.case,
            attempt_id,
            opened.case.temporary_root / "attempts" / attempt_id,
        )
        snapshot = opened.store.issue_intent_snapshot()
        completeness = assess_authoritative_completeness(snapshot)
        if completeness.state != IntentState.BOUND.value:
            raise _RunCommandError(
                "ASK_AND_BLOCK",
                "required physical conditions are not authoritatively bound",
            )
        destination_relative = Path("90_Temporary") / "attempts" / attempt_id / input_name
        with opened.case._exact_transaction() as exact:
            staged_input = exact.copy_create_new(
                source_relative,
                destination_relative,
                expected_sha256,
            )

        inspection = inspect_feb_file(staged_input)
        if inspection.sha256 != expected_sha256:
            raise _RunCommandError(
                "EVIDENCE_INTEGRITY_FAILURE",
                "staged FEB input digest changed",
            )
        preflight = run_preflight(
            feb=inspection,
            completeness=completeness,
            snapshot=snapshot,
        )
        if not preflight.ready:
            codes = sorted({item.code for item in preflight.blocking_diagnostics})
            detail = ",".join(codes) if codes else "UNKNOWN"
            raise _RunCommandError(
                "PREFLIGHT_BLOCKED",
                f"FEB preflight is blocked: {detail}",
            )

        runtime = probe_febio(arguments.runtime_probe)
        execution = issue_execution_authority(
            attempt,
            snapshot,
            runtime,
            staged_input,
            requested_fields=requested_fields,
            expected_steps=arguments.expected_steps,
            expected_final_time=arguments.expected_final_time,
            timeout_seconds=arguments.timeout_seconds,
        )
        if execution.input_sha256 != inspection.sha256:
            raise _RunCommandError(
                "EVIDENCE_INTEGRITY_FAILURE",
                "execution input changed after preflight",
            )
        diagnostic = run_headless_febio(
            attempt,
            snapshot,
            runtime,
            staged_input,
            expected_steps=arguments.expected_steps,
            expected_final_time=arguments.expected_final_time,
            timeout_seconds=arguments.timeout_seconds,
            requested_fields=requested_fields,
        )
        solver = {
            "classification": diagnostic.classification.value,
            "return_code": diagnostic.return_code,
            "state": diagnostic.state.value,
        }
        attempt_payload = {
            "attempt_id": attempt_id,
            "official_fbs": False,
            "status": diagnostic.classification.value,
        }
        if (
            diagnostic.state is not SolverState.NORMAL_EXIT
            or diagnostic.classification is not SolverClassification.FBS_UNVERIFIED
            or diagnostic.return_code != 0
            or diagnostic.log_path != execution.log_path
            or diagnostic.xplt_path != execution.xplt_path
        ):
            if not _append_run_terminal(
                opened.store,
                attempt_id,
                "SOLVER_FAILED",
                solver=solver,
            ):
                return _fail_run(
                    _RunCommandError(
                        "EVIDENCE_INTEGRITY_FAILURE",
                        "run terminal evidence could not be recorded",
                    )
                )
            _emit_context_json(
                _run_failure(
                    "SOLVER_FAILED",
                    "headless FEBio did not produce one claimable normal result",
                    attempt=attempt_payload,
                    solver=solver,
                ),
                error=True,
            )
            return _RUN_FAILURE_EXIT

        execution_record = execution.record_path
        execution_input_sha256 = execution.input_sha256
        with claim_execution_outputs(execution) as outputs:
            record_execution_output_artifacts(outputs, opened.store)
        opened.store.record_artifact(
            staged_input,
            attempt_id=attempt_id,
            expected_sha256=execution_input_sha256,
        )
        opened.store.record_artifact(
            execution_record,
            attempt_id=attempt_id,
        )

        if not _append_run_terminal(
            opened.store,
            attempt_id,
            "FBS_UNAVAILABLE",
            solver=solver,
        ):
            raise _RunCommandError(
                "EVIDENCE_INTEGRITY_FAILURE",
                "run terminal evidence could not be recorded",
            )

        _emit_context_json(
            _run_failure(
                "FBS_UNAVAILABLE",
                "official FBS validation is unavailable; solver outputs remain unverified",
                attempt=attempt_payload,
                solver=solver,
            ),
            error=True,
        )
        return _FBS_UNAVAILABLE_EXIT
    except _RunCommandError as error:
        return _fail_run_after_terminal(
            terminal_store,
            attempt_id,
            error.code,
            error,
        )
    except CaseContextError as error:
        if (
            terminal_store is not None
            and attempt_id is not None
            and not _append_run_terminal(terminal_store, attempt_id, error.code)
        ):
            return _fail_run(
                _RunCommandError(
                    "EVIDENCE_INTEGRITY_FAILURE",
                    "run terminal evidence could not be recorded",
                )
            )
        _emit_context_json(cli_failure("run-febio", error), error=True)
        return _CONTEXT_EXIT_CODES.get(error.code, _RUN_FAILURE_EXIT)
    except RuntimeProbeError:
        return _fail_run_after_terminal(
            terminal_store,
            attempt_id,
            "RUNTIME_PROBE_FAILED",
            _RunCommandError("RUNTIME_PROBE_FAILED", "FEBio runtime probe failed"),
        )
    except (
        EvidenceIntegrityError,
        ExecutionAuthorityError,
        HeadlessConfigurationError,
        OSError,
        ValueError,
        WorkspaceBoundaryError,
    ):
        return _fail_run_after_terminal(
            terminal_store,
            attempt_id,
            "EXECUTION_FAILED",
            _RunCommandError("EXECUTION_FAILED", "headless execution authority failed"),
        )
    except Exception:
        return _fail_run_after_terminal(
            terminal_store,
            attempt_id,
            "INTERNAL_ERROR",
            _RunCommandError("INTERNAL_ERROR", "unexpected headless execution failure"),
        )


def _reconnect_febio_command(arguments: argparse.Namespace) -> int:
    command = "reconnect-febio"
    terminal_store: EvidenceStore | None = None
    attempt_id = arguments.attempt_id
    try:
        service = _case_service()
        root_capability = _read_root_capability_stdin()
        opened = service._open_context(root_capability, arguments.case_id)
        if opened.result.state is not IntentState.BOUND or opened.result.question is not None:
            raise _RunCommandError("ASK_AND_BLOCK", "case intent is not authoritatively bound")
        snapshot = opened.store.issue_intent_snapshot()
        completeness = assess_authoritative_completeness(snapshot)
        if completeness.state != IntentState.BOUND.value:
            raise _RunCommandError(
                "ASK_AND_BLOCK",
                "required physical conditions are not authoritatively bound",
            )
        if not arguments.runtime_probe.is_absolute():
            raise _RunCommandError(
                "INVALID_INPUT",
                "runtime_probe must be one absolute executable path",
            )
        runtime = probe_febio(arguments.runtime_probe)
        session = recover_headless_febio(
            opened.store,
            snapshot,
            runtime,
            attempt_id,
        )
        terminal_store = opened.store
        result = session.supervisor.wait()
        solver = {
            "classification": result.classification.value,
            "return_code": result.return_code,
            "state": result.state.value,
        }
        attempt_payload = {
            "attempt_id": attempt_id,
            "official_fbs": False,
            "status": result.classification.value,
        }
        execution = session.execution
        if (
            result.state is not SolverState.NORMAL_EXIT
            or result.classification is not SolverClassification.FBS_UNVERIFIED
            or result.return_code != 0
            or result.log_path != execution.log_path
            or result.xplt_path != execution.xplt_path
        ):
            state_authority = transition_intent(snapshot)
            retry = decide_retry(
                result.classification.value,
                RetryLedger.from_store(state_authority, opened.store),
                intent=state_authority,
                supervisor=session.supervisor,
                result=result,
            )
            retry_reserved = retry.reservation_required
            if retry_reserved:
                retry.persist(opened.store, state_authority)
            if not retry_reserved and not _append_run_terminal(
                opened.store, attempt_id, "SOLVER_FAILED", solver=solver
            ):
                return _fail_run(
                    _RunCommandError(
                        "EVIDENCE_INTEGRITY_FAILURE",
                        "reconnect terminal evidence could not be recorded",
                    ),
                    command=command,
                )
            _emit_context_json(
                _run_failure(
                    "SOLVER_FAILED",
                    "reconnected FEBio did not produce one claimable normal result",
                    command=command,
                    attempt=attempt_payload,
                    solver=solver,
                ),
                error=True,
            )
            return _RUN_FAILURE_EXIT

        execution_input = execution.input_path
        execution_input_sha256 = execution.input_sha256
        execution_record = execution.record_path
        with claim_execution_outputs(execution) as outputs:
            record_execution_output_artifacts(outputs, opened.store)
        opened.store.record_artifact(
            execution_input,
            attempt_id=attempt_id,
            expected_sha256=execution_input_sha256,
        )
        opened.store.record_artifact(
            execution_record,
            attempt_id=attempt_id,
        )
        if not _append_run_terminal(
            opened.store,
            attempt_id,
            "FBS_UNAVAILABLE",
            solver=solver,
        ):
            return _fail_run(
                _RunCommandError(
                    "EVIDENCE_INTEGRITY_FAILURE",
                    "reconnect terminal evidence could not be recorded",
                ),
                command=command,
            )
        _emit_context_json(
            _run_failure(
                "FBS_UNAVAILABLE",
                "official FBS validation is unavailable; solver outputs remain unverified",
                command=command,
                attempt=attempt_payload,
                solver=solver,
            ),
            error=True,
        )
        return _FBS_UNAVAILABLE_EXIT
    except _RunCommandError as error:
        return _fail_run_after_terminal(
            terminal_store,
            attempt_id,
            error.code,
            error,
            command=command,
        )
    except CaseContextError as error:
        if terminal_store is not None and not _append_run_terminal(
            terminal_store, attempt_id, error.code
        ):
            return _fail_run(
                _RunCommandError(
                    "EVIDENCE_INTEGRITY_FAILURE",
                    "reconnect terminal evidence could not be recorded",
                ),
                command=command,
            )
        _emit_context_json(cli_failure(command, error), error=True)
        return _CONTEXT_EXIT_CODES.get(error.code, _RUN_FAILURE_EXIT)
    except RuntimeProbeError:
        return _fail_run_after_terminal(
            terminal_store,
            attempt_id,
            "RUNTIME_PROBE_FAILED",
            _RunCommandError("RUNTIME_PROBE_FAILED", "FEBio runtime probe failed"),
            command=command,
        )
    except (
        EvidenceIntegrityError,
        ExecutionAuthorityError,
        HeadlessConfigurationError,
        SolverConfigurationError,
        SolverOwnershipError,
        OSError,
        ValueError,
        WorkspaceBoundaryError,
    ):
        return _fail_run_after_terminal(
            terminal_store,
            attempt_id,
            "RECONNECT_FAILED",
            _RunCommandError(
                "RECONNECT_FAILED",
                "recorded headless execution could not be authenticated",
            ),
            command=command,
        )
    except Exception:
        return _fail_run_after_terminal(
            terminal_store,
            attempt_id,
            "INTERNAL_ERROR",
            _RunCommandError("INTERNAL_ERROR", "unexpected reconnect failure"),
            command=command,
        )


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
        return _run_febio_command(arguments)
    if arguments.command == "reconnect-febio":
        return _reconnect_febio_command(arguments)
    return 0
