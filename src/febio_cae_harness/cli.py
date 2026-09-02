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
from febio_cae_harness.autonomy import (
    Proposal,
    ProposalAuthorityManager,
    ProposalClass,
    RetryLedger,
    bind_retry_proposal,
    decide_retry,
    diagnose_negative_jacobian,
    observe_negative_jacobian_log,
    transition_intent,
)
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
from febio_cae_harness.contracts import IntentState, JSONValue
from febio_cae_harness.evidence import (
    EvidenceIntegrityError,
    EvidenceStore,
    IntentSnapshotAuthority,
)
from febio_cae_harness.model.completeness import assess_authoritative_completeness
from febio_cae_harness.model.derived import FebPatch, write_derived_feb
from febio_cae_harness.model.feb import inspect_feb_file, inspect_feb_xml
from febio_cae_harness.model.incomplete import inspect_incomplete_feb
from febio_cae_harness.model.plan import OriginalModel
from febio_cae_harness.model.preflight import PreflightResult, run_preflight
from febio_cae_harness.model.step import inspect_step, inspect_step_file
from febio_cae_harness.model.step_plan import plan_authoritative_step_meshing
from febio_cae_harness.reporting import (
    AttemptIdentity,
    EvidenceKind,
    PhysicalEvidenceAuthority,
    ReportAuthorityManager,
    ResultReport,
    assemble_report,
    issue_physical_evidence,
)
from febio_cae_harness.solver.execution import (
    ExecutionAuthority,
    ExecutionAuthorityError,
    claim_execution_outputs,
    issue_execution_authority,
    record_execution_output_artifacts,
    reopen_execution_authority,
)
from febio_cae_harness.solver.headless import (
    HeadlessConfigurationError,
    HeadlessReconnectSession,
    HeadlessRunSession,
    recover_headless_febio,
    run_headless_febio_session,
)
from febio_cae_harness.solver.log import LogValidation
from febio_cae_harness.solver.official_fbs import (
    OfficialFbsResultReceipt,
    OfficialFbsRuntimeError,
    open_official_fbs_manager,
    probe_official_fbs_runtime,
    require_official_fbs_result,
)
from febio_cae_harness.solver.runtime import RuntimeProbeError, probe_febio
from febio_cae_harness.solver.supervisor import SolverSupervisor
from febio_cae_harness.solver.types import (
    SolverClassification,
    SolverConfigurationError,
    SolverOwnershipError,
    SolverRunResult,
    SolverState,
)
from febio_cae_harness.workspace import (
    AttemptWorkspace,
    CaseWorkspace,
    WorkspaceBoundaryError,
    _identity_stamp,
)

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
_REPORT_BLOCKED_EXIT = 6


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


def _official_fbs_paths(arguments: argparse.Namespace) -> tuple[Path, Path, Path] | None:
    paths = (
        getattr(arguments, "fbs_python", None),
        getattr(arguments, "fbs_module", None),
        getattr(arguments, "fbs_zlib", None),
    )
    if any(path is not None for path in paths) and not all(path is not None for path in paths):
        raise _RunCommandError(
            "INVALID_INPUT",
            "fbs_python, fbs_module, and fbs_zlib must be supplied together",
        )
    if not all(path is not None for path in paths):
        return None
    checked = cast(tuple[Path, Path, Path], paths)
    if any(not path.is_absolute() for path in checked):
        raise _RunCommandError(
            "INVALID_INPUT",
            "official FBS paths must be absolute",
        )
    return checked


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="febio-cae")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command")

    inspect_feb = commands.add_parser(
        "inspect-feb", help="inspect FEB XML structure and explicit references"
    )
    inspect_feb.add_argument("path", type=Path, metavar="PATH")

    inspect_incomplete_feb_command = commands.add_parser(
        "inspect-incomplete-feb",
        help="inspect one registered FEB against its current authoritative case intent",
    )
    inspect_incomplete_feb_command.add_argument(
        "--capability-stdin", required=True, action="store_true"
    )
    inspect_incomplete_feb_command.add_argument("--case-id", required=True)
    inspect_incomplete_feb_command.add_argument("--input-name")

    plan_step_mesh = commands.add_parser(
        "plan-step-mesh",
        help="plan one registered STEP mesh from its current authoritative case intent",
    )
    plan_step_mesh.add_argument("--capability-stdin", required=True, action="store_true")
    plan_step_mesh.add_argument("--case-id", required=True)
    plan_step_mesh.add_argument("--input-name")

    preflight_feb = commands.add_parser(
        "preflight-feb", help="preflight FEB XML structure and explicit references"
    )
    preflight_feb.add_argument("path", type=Path, metavar="PATH")

    preflight_step = commands.add_parser(
        "preflight-step", help="preflight STEP structure and explicit unit evidence"
    )
    preflight_step.add_argument("path", type=Path, metavar="PATH")

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
    run_febio.add_argument("--fbs-python", type=Path, metavar="PATH")
    run_febio.add_argument("--fbs-module", type=Path, metavar="PATH")
    run_febio.add_argument("--fbs-zlib", type=Path, metavar="PATH")
    run_febio.add_argument(
        "--apply-declared-mesh-patches",
        action="store_true",
        help="derive the attempt input from exact mesh patches in the current intent",
    )

    reconnect_febio = commands.add_parser(
        "reconnect-febio",
        help="recover one recorded running FEBio attempt through exact authorities",
    )
    reconnect_febio.add_argument("--capability-stdin", required=True, action="store_true")
    reconnect_febio.add_argument("--case-id", required=True)
    reconnect_febio.add_argument("--attempt-id", required=True)
    reconnect_febio.add_argument("--runtime-probe", required=True, type=Path, metavar="PATH")
    reconnect_febio.add_argument("--fbs-python", type=Path, metavar="PATH")
    reconnect_febio.add_argument("--fbs-module", type=Path, metavar="PATH")
    reconnect_febio.add_argument("--fbs-zlib", type=Path, metavar="PATH")

    retry_febio = commands.add_parser(
        "retry-febio",
        help="claim one durable retry reservation and reuse its recorded launch contract",
    )
    retry_febio.add_argument("--capability-stdin", required=True, action="store_true")
    retry_febio.add_argument("--case-id", required=True)
    retry_febio.add_argument("--reservation-id", required=True)
    retry_febio.add_argument("--runtime-probe", required=True, type=Path, metavar="PATH")
    retry_febio.add_argument("--fbs-python", type=Path, metavar="PATH")
    retry_febio.add_argument("--fbs-module", type=Path, metavar="PATH")
    retry_febio.add_argument("--fbs-zlib", type=Path, metavar="PATH")
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


def _emit_preflight(path: Path, command: str) -> int:
    try:
        if command == "preflight-feb":
            result = run_preflight(feb=inspect_feb_file(path))
        else:
            result = run_preflight(step=inspect_step_file(path))
    except (OSError, ValueError) as error:
        return _error(str(error))
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
                sys.stdout.buffer.write(dump_root_capability(registered["capability"]))
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
    report: Mapping[str, object] | None = None,
) -> dict[str, object]:
    payload = cli_failure(
        command,
        CaseContextError(code, message, retryable=retryable),
    )
    if attempt is not None:
        payload["attempt"] = dict(attempt)
    if solver is not None:
        payload["solver"] = dict(solver)
    if report is not None:
        payload["report"] = dict(report)
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
    official_fbs: bool = False,
) -> bool:
    payload: dict[str, object] = {
        "attempt_id": attempt_id,
        "official_fbs": official_fbs,
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


def _official_report_evidence(
    attempt: AttemptWorkspace,
    receipt: OfficialFbsResultReceipt,
    physical_evidence: PhysicalEvidenceAuthority,
) -> dict[EvidenceKind, Path]:
    """Write physical evidence documents from one live issued authority."""

    binding = {
        "element_count": receipt.element_count,
        "model_manifest_sha256": receipt.model_manifest_sha256,
        "node_count": receipt.node_count,
        "requested_fields": list(receipt.requested_fields),
        "state_count": receipt.state_count,
        "state_times": list(receipt.state_times),
        "xplt_sha256": receipt.xplt_sha256,
    }
    paths: dict[EvidenceKind, Path] = {}
    for kind in EvidenceKind:
        payload = physical_evidence.document(kind.value)
        payload["fbs_binding"] = binding
        paths[kind] = attempt.write_text(
            Path("report-evidence") / f"{kind.value}.json",
            json.dumps(
                payload,
                allow_nan=False,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
        )
    return paths


def _complete_official_report(
    *,
    command: str,
    store: EvidenceStore,
    attempt: AttemptWorkspace,
    snapshot: IntentSnapshotAuthority,
    execution: ExecutionAuthority,
    supervisor: SolverSupervisor,
    result: SolverRunResult,
    staged_input: Path,
    attempt_payload: dict[str, object],
    solver: Mapping[str, object],
) -> int:
    """Persist a verified or fail-closed report from one official FBS result."""

    if (
        type(execution) is not ExecutionAuthority
        or type(supervisor) is not SolverSupervisor
        or type(result) is not SolverRunResult
    ):
        raise _RunCommandError(
            "EVIDENCE_INTEGRITY_FAILURE",
            "official result requires exact execution and solver authorities",
        )
    try:
        validation = result.fbs_validation
        if validation is None:
            raise TypeError("official solver result has no FBS validation")
        receipt = require_official_fbs_result(validation)
        identity = AttemptIdentity(
            attempt.case_id,
            snapshot.intent_sha256,
            attempt.attempt_id,
        )
        physical_evidence = issue_physical_evidence(supervisor, result, receipt)
        evidence_paths = _official_report_evidence(attempt, receipt, physical_evidence)
        authority = ReportAuthorityManager(
            supervisor,
            result,
            identity,
            official_fbs_result=receipt,
            physical_evidence=physical_evidence,
        ).issue(evidence_paths)
        report = assemble_report(authority)
        physical_passed = all(physical_evidence.passed.values())
        if (
            type(report) is not ResultReport
            or report.success is not physical_passed
            or report.verified is not physical_passed
        ):
            raise TypeError("physical evidence and report gate decisions differ")
        report_path = attempt.write_text(
            "report.json",
            json.dumps(
                report.to_dict(),
                allow_nan=False,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
        )
        execution_input_sha256 = execution.input_sha256
        execution_record = execution.record_path
        with claim_execution_outputs(execution) as outputs:
            record_execution_output_artifacts(outputs, store)
        store.record_artifact(
            staged_input,
            attempt_id=attempt.attempt_id,
            expected_sha256=execution_input_sha256,
        )
        store.record_artifact(execution_record, attempt_id=attempt.attempt_id)
        for path in evidence_paths.values():
            store.record_artifact(path, attempt_id=attempt.attempt_id)
        store.record_artifact(report_path, attempt_id=attempt.attempt_id)
        attempt_payload["official_fbs"] = True
        attempt_payload["status"] = "SUCCESS" if report.success else "REPORT_BLOCKED"
        if not _append_run_terminal(
            store,
            attempt.attempt_id,
            str(attempt_payload["status"]),
            solver=solver,
            official_fbs=True,
        ):
            raise _RunCommandError(
                "EVIDENCE_INTEGRITY_FAILURE",
                f"{command} terminal evidence could not be recorded",
            )
        report_summary = {
            "failed_checks": list(report.gates.failed_checks),
            "provenance": report.provenance.value,
            "success": report.success,
            "verified": report.verified,
        }
    except _RunCommandError:
        raise
    except (AttributeError, TypeError, ValueError, RuntimeError) as error:
        raise _RunCommandError(
            "EVIDENCE_INTEGRITY_FAILURE",
            "official result report authority is invalid",
        ) from error

    if report_summary["success"] is True:
        payload = cli_success(command)
        payload.update(
            {
                "attempt": dict(attempt_payload),
                "solver": dict(solver),
                "report": report_summary,
            }
        )
        _emit_context_json(payload)
        return 0
    _emit_context_json(
        _run_failure(
            "REPORT_BLOCKED",
            "official FBS passed, but physical evidence does not satisfy the exact approved intent",
            command=command,
            attempt=attempt_payload,
            solver=solver,
            report=report_summary,
        ),
        error=True,
    )
    return _REPORT_BLOCKED_EXIT


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


def _select_step_input(
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
        if path.suffix.casefold() not in {".step", ".stp"}:
            continue
        name = path.name
        if selected_name is None or name == selected_name:
            candidates.append((path, name, digest))
    if not candidates:
        code = "INPUT_NOT_FOUND" if selected_name is not None else "INPUT_SELECTION_REQUIRED"
        raise _RunCommandError(code, "registered STEP input selection failed")
    if len(candidates) != 1:
        raise _RunCommandError(
            "INPUT_SELECTION_REQUIRED",
            "multiple registered STEP inputs require one exact input_name",
        )
    return candidates[0]


def _open_recorded_attempt(case: CaseWorkspace, attempt_id: str) -> AttemptWorkspace:
    attempt_root = case.temporary_root / "attempts" / attempt_id
    attempt_stamp = _identity_stamp(attempt_root, "recorded attempt root")
    return AttemptWorkspace._from_manager(
        case,
        attempt_id,
        attempt_root,
        expected_root_stamp=attempt_stamp,
    )


def _inspect_incomplete_feb_command(arguments: argparse.Namespace) -> int:
    command = "inspect-incomplete-feb"
    try:
        service = _case_service()
        root_capability = _read_root_capability_stdin()
        opened = service._open_context(root_capability, arguments.case_id)
        snapshot = opened.result.snapshot
        completeness = assess_authoritative_completeness(snapshot)
        source_relative, input_name, expected_sha256 = _select_feb_input(
            opened.store.manifest.get("inputs"),
            arguments.input_name,
        )
        with opened.case._exact_transaction() as exact:
            source = exact.read_bytes(source_relative)
        inspection = inspect_feb_xml(source, source_name=input_name)
        if inspection.sha256 != expected_sha256:
            raise CaseContextError(
                "EVIDENCE_INTEGRITY_FAILURE",
                "registered FEB input does not match its manifest digest",
            )
        inventory = inspect_incomplete_feb(
            inspection,
            completeness,
            snapshot=snapshot,
        )
        inventory_payload = inventory.to_dict()
        # Revalidate the live snapshot after consuming every action field.
        assess_authoritative_completeness(snapshot)
        payload = cli_success(
            command,
            case={
                "case_id": snapshot.case_id,
                "intent": {"sha256": snapshot.intent_sha256},
                "state": opened.result.state.value,
            },
        )
        payload["input"] = {"name": input_name, "sha256": expected_sha256}
        payload["inventory"] = inventory_payload
        payload["status"] = "READY" if inventory_payload["ready"] is True else "ASK_AND_BLOCK"
    except _RunCommandError as error:
        context_error = CaseContextError(error.code, str(error))
        _emit_context_json(cli_failure(command, context_error), error=True)
        if error.code == "EVIDENCE_INTEGRITY_FAILURE":
            return _CONTEXT_EXIT_CODES[error.code]
        return _CONTEXT_EXIT_CODES["INVALID_INPUT"]
    except CaseContextError as error:
        _emit_context_json(cli_failure(command, error), error=True)
        return _CONTEXT_EXIT_CODES.get(error.code, _CONTEXT_EXIT_CODES["INTERNAL_ERROR"])
    except (EvidenceIntegrityError, WorkspaceBoundaryError):
        failure = CaseContextError(
            "EVIDENCE_INTEGRITY_FAILURE",
            "registered FEB or intent evidence is invalid",
        )
        _emit_context_json(cli_failure(command, failure), error=True)
        return _CONTEXT_EXIT_CODES[failure.code]
    except ValueError:
        failure = CaseContextError("INVALID_INPUT", "registered FEB inspection failed")
        _emit_context_json(cli_failure(command, failure), error=True)
        return _CONTEXT_EXIT_CODES[failure.code]
    except OSError:
        failure = CaseContextError("IO_OR_LOCK_FAILURE", "registered FEB inspection I/O failed")
        _emit_context_json(cli_failure(command, failure), error=True)
        return _CONTEXT_EXIT_CODES[failure.code]
    except Exception:
        failure = CaseContextError(
            "INTERNAL_ERROR",
            "unexpected registered FEB inspection failure",
        )
        _emit_context_json(cli_failure(command, failure), error=True)
        return _CONTEXT_EXIT_CODES[failure.code]
    _emit_context_json(payload)
    return 0


def _plan_step_mesh_command(arguments: argparse.Namespace) -> int:
    command = "plan-step-mesh"
    try:
        service = _case_service()
        root_capability = _read_root_capability_stdin()
        opened = service._open_context(root_capability, arguments.case_id)
        snapshot = opened.result.snapshot
        source_relative, input_name, expected_sha256 = _select_step_input(
            opened.store.manifest.get("inputs"),
            arguments.input_name,
        )
        with opened.case._exact_transaction() as exact:
            source = exact.read_bytes(source_relative)
        inspection = inspect_step(source, source_name=input_name)
        if inspection.sha256 != expected_sha256:
            raise CaseContextError(
                "EVIDENCE_INTEGRITY_FAILURE",
                "registered STEP input does not match its manifest digest",
            )
        plan = plan_authoritative_step_meshing(inspection, snapshot)
        plan_payload = plan.to_dict()
        payload = cli_success(
            command,
            case={
                "case_id": snapshot.case_id,
                "intent": {"sha256": snapshot.intent_sha256},
                "state": opened.result.state.value,
            },
        )
        payload["input"] = {"name": input_name, "sha256": expected_sha256}
        payload["plan"] = plan_payload
        payload["status"] = plan_payload["status"]
    except _RunCommandError as error:
        context_error = CaseContextError(error.code, str(error))
        _emit_context_json(cli_failure(command, context_error), error=True)
        if error.code == "EVIDENCE_INTEGRITY_FAILURE":
            return _CONTEXT_EXIT_CODES[error.code]
        return _CONTEXT_EXIT_CODES["INVALID_INPUT"]
    except CaseContextError as error:
        _emit_context_json(cli_failure(command, error), error=True)
        return _CONTEXT_EXIT_CODES.get(error.code, _CONTEXT_EXIT_CODES["INTERNAL_ERROR"])
    except (EvidenceIntegrityError, WorkspaceBoundaryError):
        failure = CaseContextError(
            "EVIDENCE_INTEGRITY_FAILURE",
            "registered STEP or intent evidence is invalid",
        )
        _emit_context_json(cli_failure(command, failure), error=True)
        return _CONTEXT_EXIT_CODES[failure.code]
    except (TypeError, ValueError):
        failure = CaseContextError("INVALID_INPUT", "registered STEP meshing plan failed")
        _emit_context_json(cli_failure(command, failure), error=True)
        return _CONTEXT_EXIT_CODES[failure.code]
    except OSError:
        failure = CaseContextError("IO_OR_LOCK_FAILURE", "registered STEP planning I/O failed")
        _emit_context_json(cli_failure(command, failure), error=True)
        return _CONTEXT_EXIT_CODES[failure.code]
    except Exception:
        failure = CaseContextError(
            "INTERNAL_ERROR",
            "unexpected registered STEP planning failure",
        )
        _emit_context_json(cli_failure(command, failure), error=True)
        return _CONTEXT_EXIT_CODES[failure.code]
    _emit_context_json(payload)
    return 0


def _retry_already_started(attempt_id: str) -> int:
    _emit_context_json(
        _run_failure(
            "RETRY_ALREADY_STARTED",
            "durable retry setup already started; the attempt was not relaunched",
            command="retry-febio",
            attempt={
                "attempt_id": attempt_id,
                "official_fbs": False,
                "status": "RETRY_ALREADY_STARTED",
            },
        ),
        error=True,
    )
    return _RUN_FAILURE_EXIT


def _write_declared_mesh_input(
    *,
    snapshot: IntentSnapshotAuthority,
    source_payload: bytes,
    expected_sha256: str,
    input_name: str,
    attempt: AttemptWorkspace,
) -> tuple[Path, str]:
    original = OriginalModel.from_bytes(source_payload, source_name=input_name)
    if original.sha256 != expected_sha256:
        raise _RunCommandError(
            "EVIDENCE_INTEGRITY_FAILURE",
            "registered FEB input digest changed before derivation",
        )
    raw_patches = _declared_mesh_patch_records(snapshot)

    patches: list[FebPatch] = []
    for raw in raw_patches:
        patches.append(
            FebPatch(
                target=cast(str, raw.get("target")),
                mode=cast(str, raw.get("mode")),
                attribute_name=cast(str | None, raw.get("attribute_name")),
                value=cast(str | int | float | bool, raw.get("value")),
                reason="exact current intent mesh patch declaration",
            )
        )

    state_authority = transition_intent(snapshot)
    proposal = Proposal(
        proposal_id=f"declared-mesh-{snapshot.intent_sha256}",
        proposal_class=ProposalClass.INTENT_PRESERVING,
        rationale="apply the exact current intent mesh patch declaration",
        evidence_ids=(snapshot.intent_sha256,),
        changes={"mesh": {"patches": raw_patches}},
        authorized=True,
        within_contract=True,
    )
    manager = ProposalAuthorityManager(state_authority)
    authority = manager.issue(proposal)
    receipt = write_derived_feb(
        original,
        patches,
        attempt.root / input_name,
        attempt,
        state_authority=state_authority,
        proposal=proposal,
        proposal_authority=authority,
    )
    return receipt.destination, receipt.derived_sha256


def _declared_mesh_patch_records(
    snapshot: IntentSnapshotAuthority,
) -> tuple[Mapping[str, JSONValue], ...]:
    declarations = snapshot.intent.allowed_mesh_changes
    if not isinstance(declarations, Mapping) or set(declarations) != {"mesh"}:
        raise _RunCommandError(
            "ASK_AND_BLOCK",
            "current intent does not declare one exact FEB mesh patch set",
        )
    mesh = declarations["mesh"]
    if not isinstance(mesh, Mapping) or set(mesh) not in (
        {"patches"},
        {"negative_jacobian_evidence", "patches"},
    ):
        raise _RunCommandError(
            "ASK_AND_BLOCK",
            "current intent does not declare one exact FEB mesh patch set",
        )
    raw_patches = mesh["patches"]
    if not isinstance(raw_patches, tuple) or not raw_patches:
        raise _RunCommandError(
            "ASK_AND_BLOCK",
            "current intent does not declare one exact FEB mesh patch set",
        )

    records: list[Mapping[str, JSONValue]] = []
    for raw in raw_patches:
        if not isinstance(raw, Mapping):
            raise _RunCommandError(
                "ASK_AND_BLOCK",
                "current intent FEB mesh patches are not exact mappings",
            )
        mode = raw.get("mode")
        required = (
            {"target", "mode", "value"}
            if mode == "TEXT"
            else {"target", "mode", "attribute_name", "value"}
        )
        value = raw.get("value")
        if (
            set(raw) != required
            or type(raw.get("target")) is not str
            or not cast(str, raw["target"]).strip()
            or mode not in {"TEXT", "ATTRIBUTE"}
            or type(value) not in {str, int, float, bool}
            or isinstance(value, float)
            and not math.isfinite(value)
            or mode == "ATTRIBUTE"
            and (
                type(raw.get("attribute_name")) is not str
                or not cast(str, raw["attribute_name"]).strip()
            )
        ):
            raise _RunCommandError(
                "ASK_AND_BLOCK",
                "current intent FEB mesh patch schema is not exact",
            )
        records.append(cast(Mapping[str, JSONValue], raw))
    return tuple(records)


def _negative_jacobian_repair_proposal(
    snapshot: IntentSnapshotAuthority,
    *,
    attempt_id: str,
    validation: LogValidation,
) -> Proposal | None:
    if type(validation) is not LogValidation:
        return None
    try:
        raw_patches = _declared_mesh_patch_records(snapshot)
    except _RunCommandError:
        return None
    declarations = snapshot.intent.allowed_mesh_changes
    assert isinstance(declarations, Mapping)
    mesh = declarations["mesh"]
    assert isinstance(mesh, Mapping)
    repair_evidence = mesh.get("negative_jacobian_evidence")
    required = {
        "initial_mesh_valid",
        "surrounding_mesh_metrics",
        "roi_relation_evidence",
        "contact_relation_evidence",
        "constraint_relation_evidence",
    }
    if not isinstance(repair_evidence, Mapping) or set(repair_evidence) != required:
        return None
    if type(repair_evidence["initial_mesh_valid"]) is not bool:
        return None
    metrics = repair_evidence["surrounding_mesh_metrics"]
    if not isinstance(metrics, Mapping) or not metrics:
        return None
    relations = (
        repair_evidence["roi_relation_evidence"],
        repair_evidence["contact_relation_evidence"],
        repair_evidence["constraint_relation_evidence"],
    )
    if any(not isinstance(value, Mapping) or not value for value in relations):
        return None
    try:
        observation = observe_negative_jacobian_log(
            validation,
            attempt_id=attempt_id,
            initial_mesh_valid=repair_evidence["initial_mesh_valid"],
            surrounding_mesh_metrics=cast(Mapping[str, float], metrics),
            roi_relation_evidence=relations[0],
            contact_relation_evidence=relations[1],
            constraint_relation_evidence=relations[2],
        )
        diagnostic = diagnose_negative_jacobian(observation)
    except (TypeError, ValueError):
        return None
    if diagnostic.missing_technical_evidence:
        return None
    proposal = Proposal(
        proposal_id="pending",
        proposal_class=ProposalClass.INTENT_PRESERVING,
        rationale="repair exact negative-Jacobian evidence within the current mesh contract",
        evidence_ids=(snapshot.intent_sha256, observation.log_evidence_digest),
        changes={"mesh": {"patches": raw_patches}},
        authorized=True,
        within_contract=True,
    )
    return bind_retry_proposal(proposal)


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
        fbs_paths = _official_fbs_paths(arguments)

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
        if arguments.apply_declared_mesh_patches:
            with opened.case._exact_transaction() as exact:
                source_payload = exact.read_bytes(source_relative)
            staged_input, staged_sha256 = _write_declared_mesh_input(
                snapshot=snapshot,
                source_payload=source_payload,
                expected_sha256=expected_sha256,
                input_name=input_name,
                attempt=attempt,
            )
        else:
            with opened.case._exact_transaction() as exact:
                staged_input = exact.copy_create_new(
                    source_relative,
                    destination_relative,
                    expected_sha256,
                )
            staged_sha256 = expected_sha256

        inspection = inspect_feb_file(staged_input)
        if inspection.sha256 != staged_sha256:
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
        official_runtime = None
        if fbs_paths is not None:
            fbs_python, fbs_module, fbs_zlib = fbs_paths
            official_runtime = probe_official_fbs_runtime(fbs_python, fbs_module, fbs_zlib)
        execution = issue_execution_authority(
            attempt,
            snapshot,
            runtime,
            staged_input,
            requested_fields=requested_fields,
            expected_steps=arguments.expected_steps,
            expected_final_time=arguments.expected_final_time,
            timeout_seconds=arguments.timeout_seconds,
            official_fbs_runtime=official_runtime,
        )
        if execution.input_sha256 != inspection.sha256:
            raise _RunCommandError(
                "EVIDENCE_INTEGRITY_FAILURE",
                "execution input changed after preflight",
            )
        if official_runtime is None:
            session = run_headless_febio_session(
                attempt,
                snapshot,
                runtime,
                staged_input,
                expected_steps=arguments.expected_steps,
                expected_final_time=arguments.expected_final_time,
                timeout_seconds=arguments.timeout_seconds,
                requested_fields=requested_fields,
            )
        else:
            with open_official_fbs_manager(official_runtime, attempt.root) as fbs_manager:
                session = run_headless_febio_session(
                    attempt,
                    snapshot,
                    runtime,
                    staged_input,
                    expected_steps=arguments.expected_steps,
                    expected_final_time=arguments.expected_final_time,
                    timeout_seconds=arguments.timeout_seconds,
                    requested_fields=requested_fields,
                    fbs_adapter=fbs_manager.issue_authority(),
                )
                diagnostic = session.diagnostic
                solver = {
                    "classification": diagnostic.classification.value,
                    "return_code": diagnostic.return_code,
                    "state": diagnostic.state.value,
                }
                official_attempt_payload = {
                    "attempt_id": attempt_id,
                    "official_fbs": False,
                    "status": diagnostic.classification.value,
                }
                if (
                    diagnostic.state is SolverState.NORMAL_EXIT
                    and diagnostic.classification is SolverClassification.SUCCESS
                    and diagnostic.return_code == 0
                    and diagnostic.log_path == execution.log_path
                    and diagnostic.xplt_path == execution.xplt_path
                ):
                    if type(session) is not HeadlessRunSession:
                        raise _RunCommandError(
                            "EVIDENCE_INTEGRITY_FAILURE",
                            "official result requires an exact headless run session",
                        )
                    return _complete_official_report(
                        command="run-febio",
                        store=opened.store,
                        attempt=attempt,
                        snapshot=snapshot,
                        execution=execution,
                        supervisor=session.supervisor,
                        result=session.result,
                        staged_input=staged_input,
                        attempt_payload=official_attempt_payload,
                        solver=solver,
                    )
        diagnostic = session.diagnostic
        solver = {
            "classification": diagnostic.classification.value,
            "return_code": diagnostic.return_code,
            "state": diagnostic.state.value,
        }
        attempt_payload = {
            "attempt_id": attempt_id,
            "official_fbs": diagnostic.official_fbs,
            "status": diagnostic.classification.value,
        }
        accepted_classification = (
            diagnostic.classification is SolverClassification.FBS_UNVERIFIED
            and diagnostic.official_fbs is False
        )
        if (
            diagnostic.state is not SolverState.NORMAL_EXIT
            or not accepted_classification
            or diagnostic.return_code != 0
            or diagnostic.log_path != execution.log_path
            or diagnostic.xplt_path != execution.xplt_path
        ):
            state_authority = transition_intent(snapshot)
            repair_proposal = None
            repair_authority = None
            if (
                diagnostic.classification is SolverClassification.NEGATIVE_JACOBIAN
                and type(session) is HeadlessRunSession
                and type(session.result.log_validation) is LogValidation
            ):
                repair_proposal = _negative_jacobian_repair_proposal(
                    snapshot,
                    attempt_id=attempt_id,
                    validation=session.result.log_validation,
                )
                if repair_proposal is not None:
                    repair_authority = ProposalAuthorityManager(state_authority).issue(
                        repair_proposal
                    )
            retry = decide_retry(
                diagnostic.classification.value,
                RetryLedger.from_store(state_authority, opened.store),
                intent=state_authority,
                proposal=repair_proposal,
                proposal_authority=repair_authority,
                supervisor=session.supervisor,
                result=session.result,
            )
            retry_reserved = retry.reservation_required
            if retry_reserved:
                retry.persist(opened.store, state_authority)
                attempt_payload["retry_reservation_id"] = retry.reservation_id
            if not retry_reserved and not _append_run_terminal(
                opened.store, attempt_id, "SOLVER_FAILED", solver=solver
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
    except OfficialFbsRuntimeError:
        return _fail_run_after_terminal(
            terminal_store,
            attempt_id,
            "FBS_RUNTIME_FAILED",
            _RunCommandError("FBS_RUNTIME_FAILED", "official FBS runtime validation failed"),
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


def _retry_febio_command(arguments: argparse.Namespace) -> int:
    command = "retry-febio"
    terminal_store: EvidenceStore | None = None
    attempt_id: str | None = None
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
        reservation_id = arguments.reservation_id
        if (
            not isinstance(reservation_id, str)
            or len(reservation_id) != 64
            or any(character not in "0123456789abcdef" for character in reservation_id)
        ):
            raise _RunCommandError(
                "INVALID_INPUT",
                "reservation_id must be one lowercase SHA-256 identifier",
            )
        if not arguments.runtime_probe.is_absolute():
            raise _RunCommandError(
                "INVALID_INPUT",
                "runtime_probe must be one absolute executable path",
            )
        fbs_paths = _official_fbs_paths(arguments)
        runtime = probe_febio(arguments.runtime_probe)
        official_runtime = None if fbs_paths is None else probe_official_fbs_runtime(*fbs_paths)
        state_authority = transition_intent(snapshot)
        ledger = RetryLedger.from_store(state_authority, opened.store)
        matching_records = [
            record for record in ledger.records if record.reservation_id == reservation_id
        ]
        if len(matching_records) != 1 or matching_records[0].attempt_id is None:
            raise _RunCommandError(
                "RETRY_RESERVATION_INVALID",
                "retry reservation is absent or has no exact parent attempt",
            )
        parent_attempt_id = matching_records[0].attempt_id
        attempt_id = f"retry-{reservation_id}"
        claim = ledger.claim_attempt(
            opened.store,
            state_authority,
            reservation_id,
            attempt_id,
        )
        if claim.get("attempt_id") != attempt_id:
            raise _RunCommandError(
                "EVIDENCE_INTEGRITY_FAILURE",
                "retry claim attempt identity changed",
            )
        snapshot = opened.store.issue_intent_snapshot()
        completeness = assess_authoritative_completeness(snapshot)
        if completeness.state != IntentState.BOUND.value:
            raise _RunCommandError(
                "ASK_AND_BLOCK",
                "required physical conditions are not authoritatively bound",
            )
        parent_attempt = _open_recorded_attempt(opened.case, parent_attempt_id)
        parent_execution = reopen_execution_authority(
            parent_attempt,
            snapshot,
            runtime,
            official_fbs_runtime=official_runtime,
        )
        parent_input = parent_execution.input_path
        input_name = parent_input.name
        expected_sha256 = parent_execution.input_sha256
        expected_steps = parent_execution.expected_steps
        expected_final_time = parent_execution.expected_final_time
        timeout_seconds = parent_execution.timeout_seconds
        requested_fields = parent_execution.requested_fields
        execution_name = parent_execution.record_path.name

        attempt = _open_recorded_attempt(opened.case, attempt_id)
        attempt_relative = Path("90_Temporary") / "attempts" / attempt_id
        execution_relative = attempt_relative / execution_name
        with opened.case._exact_transaction() as exact:
            execution_exists = exact.exists(execution_relative)
        if execution_exists:
            reopen_execution_authority(
                attempt,
                snapshot,
                runtime,
                official_fbs_runtime=official_runtime,
            )
            return _retry_already_started(attempt_id)

        source_relative = parent_input.relative_to(opened.case.root)
        destination_relative = attempt_relative / input_name
        with opened.case._exact_transaction() as exact:
            if exact.exists(destination_relative):
                if exact.digest(destination_relative) != expected_sha256:
                    raise _RunCommandError(
                        "EVIDENCE_INTEGRITY_FAILURE",
                        "retry input differs from its parent execution",
                    )
                staged_input = opened.case.root / destination_relative
            else:
                staged_input = exact.copy_create_new(
                    source_relative,
                    destination_relative,
                    expected_sha256,
                )

        inspection = inspect_feb_file(staged_input)
        if inspection.sha256 != expected_sha256:
            raise _RunCommandError(
                "EVIDENCE_INTEGRITY_FAILURE",
                "retry input changed after exact copy",
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
                f"retry FEB preflight is blocked: {detail}",
            )

        setup_state = transition_intent(snapshot)
        setup_ledger = RetryLedger.from_store(setup_state, opened.store)
        if not setup_ledger.begin_attempt_setup(
            opened.store,
            setup_state,
            reservation_id,
            attempt_id,
        ):
            return _retry_already_started(attempt_id)

        snapshot = opened.store.issue_intent_snapshot()
        completeness = assess_authoritative_completeness(snapshot)
        if completeness.state != IntentState.BOUND.value:
            raise _RunCommandError(
                "ASK_AND_BLOCK",
                "required physical conditions are not authoritatively bound",
            )
        parent_attempt = _open_recorded_attempt(opened.case, parent_attempt_id)
        refreshed_parent_execution = reopen_execution_authority(
            parent_attempt,
            snapshot,
            runtime,
            official_fbs_runtime=official_runtime,
        )
        if (
            refreshed_parent_execution.input_path.name != input_name
            or refreshed_parent_execution.input_sha256 != expected_sha256
            or refreshed_parent_execution.expected_steps != expected_steps
            or refreshed_parent_execution.expected_final_time != expected_final_time
            or refreshed_parent_execution.timeout_seconds != timeout_seconds
            or refreshed_parent_execution.requested_fields != requested_fields
            or refreshed_parent_execution.record_path.name != execution_name
        ):
            raise _RunCommandError(
                "EVIDENCE_INTEGRITY_FAILURE",
                "parent execution contract changed during retry setup",
            )
        attempt = _open_recorded_attempt(opened.case, attempt_id)
        with opened.case._exact_transaction() as exact:
            if (
                not exact.exists(destination_relative)
                or exact.digest(destination_relative) != expected_sha256
            ):
                raise _RunCommandError(
                    "EVIDENCE_INTEGRITY_FAILURE",
                    "retry input changed after setup began",
                )
        staged_input = opened.case.root / destination_relative
        inspection = inspect_feb_file(staged_input)
        if inspection.sha256 != expected_sha256:
            raise _RunCommandError(
                "EVIDENCE_INTEGRITY_FAILURE",
                "retry input changed after setup began",
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
                f"retry FEB preflight is blocked after setup began: {detail}",
            )

        try:
            execution = issue_execution_authority(
                attempt,
                snapshot,
                runtime,
                staged_input,
                requested_fields=requested_fields,
                expected_steps=expected_steps,
                expected_final_time=expected_final_time,
                timeout_seconds=timeout_seconds,
                official_fbs_runtime=official_runtime,
            )
        except ExecutionAuthorityError:
            with opened.case._exact_transaction() as exact:
                execution_exists = exact.exists(execution_relative)
            if execution_exists:
                reopen_execution_authority(
                    attempt,
                    snapshot,
                    runtime,
                    official_fbs_runtime=official_runtime,
                )
                return _retry_already_started(attempt_id)
            raise
        if execution.input_sha256 != expected_sha256:
            raise _RunCommandError(
                "EVIDENCE_INTEGRITY_FAILURE",
                "retry execution input differs from its parent",
            )
        if official_runtime is None:
            session = run_headless_febio_session(
                attempt,
                snapshot,
                runtime,
                staged_input,
                expected_steps=expected_steps,
                expected_final_time=expected_final_time,
                timeout_seconds=timeout_seconds,
                requested_fields=requested_fields,
            )
        else:
            with open_official_fbs_manager(official_runtime, attempt.root) as fbs_manager:
                session = run_headless_febio_session(
                    attempt,
                    snapshot,
                    runtime,
                    staged_input,
                    expected_steps=expected_steps,
                    expected_final_time=expected_final_time,
                    timeout_seconds=timeout_seconds,
                    requested_fields=requested_fields,
                    fbs_adapter=fbs_manager.issue_authority(),
                )
                diagnostic = session.diagnostic
                solver = {
                    "classification": diagnostic.classification.value,
                    "return_code": diagnostic.return_code,
                    "state": diagnostic.state.value,
                }
                official_attempt_payload = {
                    "attempt_id": attempt_id,
                    "official_fbs": False,
                    "status": diagnostic.classification.value,
                }
                if (
                    diagnostic.state is SolverState.NORMAL_EXIT
                    and diagnostic.classification is SolverClassification.SUCCESS
                    and diagnostic.return_code == 0
                    and diagnostic.log_path == execution.log_path
                    and diagnostic.xplt_path == execution.xplt_path
                ):
                    if type(session) is not HeadlessRunSession:
                        raise _RunCommandError(
                            "EVIDENCE_INTEGRITY_FAILURE",
                            "official result requires an exact headless run session",
                        )
                    return _complete_official_report(
                        command=command,
                        store=opened.store,
                        attempt=attempt,
                        snapshot=snapshot,
                        execution=execution,
                        supervisor=session.supervisor,
                        result=session.result,
                        staged_input=staged_input,
                        attempt_payload=official_attempt_payload,
                        solver=solver,
                    )
        diagnostic = session.diagnostic
        solver = {
            "classification": diagnostic.classification.value,
            "return_code": diagnostic.return_code,
            "state": diagnostic.state.value,
        }
        attempt_payload: dict[str, object] = {
            "attempt_id": attempt_id,
            "official_fbs": diagnostic.official_fbs,
            "status": diagnostic.classification.value,
        }
        accepted_classification = (
            diagnostic.classification is SolverClassification.FBS_UNVERIFIED
            and diagnostic.official_fbs is False
        )
        if (
            diagnostic.state is not SolverState.NORMAL_EXIT
            or not accepted_classification
            or diagnostic.return_code != 0
            or diagnostic.log_path != execution.log_path
            or diagnostic.xplt_path != execution.xplt_path
        ):
            retry_state = transition_intent(snapshot)
            retry = decide_retry(
                diagnostic.classification.value,
                RetryLedger.from_store(retry_state, opened.store),
                intent=retry_state,
                supervisor=session.supervisor,
                result=session.result,
            )
            retry_reserved = retry.reservation_required
            if retry_reserved:
                retry.persist(opened.store, retry_state)
                attempt_payload["retry_reservation_id"] = retry.reservation_id
            if not retry_reserved and not _append_run_terminal(
                opened.store,
                attempt_id,
                "SOLVER_FAILED",
                solver=solver,
            ):
                return _fail_run(
                    _RunCommandError(
                        "EVIDENCE_INTEGRITY_FAILURE",
                        "retry terminal evidence could not be recorded",
                    ),
                    command=command,
                )
            _emit_context_json(
                _run_failure(
                    "SOLVER_FAILED",
                    "retried FEBio did not produce one claimable normal result",
                    command=command,
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
        opened.store.record_artifact(execution_record, attempt_id=attempt_id)
        if not _append_run_terminal(
            opened.store,
            attempt_id,
            "FBS_UNAVAILABLE",
            solver=solver,
        ):
            raise _RunCommandError(
                "EVIDENCE_INTEGRITY_FAILURE",
                "retry terminal evidence could not be recorded",
            )
        _emit_context_json(
            _run_failure(
                "FBS_UNAVAILABLE",
                "official FBS validation is unavailable; retry outputs remain unverified",
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
        if (
            terminal_store is not None
            and attempt_id is not None
            and not _append_run_terminal(terminal_store, attempt_id, error.code)
        ):
            return _fail_run(
                _RunCommandError(
                    "EVIDENCE_INTEGRITY_FAILURE",
                    "retry terminal evidence could not be recorded",
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
    except OfficialFbsRuntimeError:
        return _fail_run_after_terminal(
            terminal_store,
            attempt_id,
            "FBS_RUNTIME_FAILED",
            _RunCommandError("FBS_RUNTIME_FAILED", "official FBS runtime validation failed"),
            command=command,
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
            _RunCommandError("EXECUTION_FAILED", "retry execution authority failed"),
            command=command,
        )
    except Exception:
        return _fail_run_after_terminal(
            terminal_store,
            attempt_id,
            "INTERNAL_ERROR",
            _RunCommandError("INTERNAL_ERROR", "unexpected retry execution failure"),
            command=command,
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
        fbs_paths = _official_fbs_paths(arguments)
        runtime = probe_febio(arguments.runtime_probe)
        official_runtime = None if fbs_paths is None else probe_official_fbs_runtime(*fbs_paths)
        if official_runtime is None:
            session = recover_headless_febio(
                opened.store,
                snapshot,
                runtime,
                attempt_id,
            )
            terminal_store = opened.store
            result = session.supervisor.wait()
            official_fbs = False
        else:
            attempt_root = opened.case.temporary_root / "attempts" / attempt_id
            with open_official_fbs_manager(official_runtime, attempt_root) as fbs_manager:
                session = recover_headless_febio(
                    opened.store,
                    snapshot,
                    runtime,
                    attempt_id,
                    official_fbs_runtime=official_runtime,
                    fbs_adapter=fbs_manager.issue_authority(),
                )
                terminal_store = opened.store
                result = session.supervisor.wait()
                solver = {
                    "classification": result.classification.value,
                    "return_code": result.return_code,
                    "state": result.state.value,
                }
                official_attempt_payload = {
                    "attempt_id": attempt_id,
                    "official_fbs": False,
                    "status": result.classification.value,
                }
                if (
                    result.state is SolverState.NORMAL_EXIT
                    and result.classification is SolverClassification.SUCCESS
                    and result.return_code == 0
                ):
                    if type(session) is not HeadlessReconnectSession:
                        raise _RunCommandError(
                            "EVIDENCE_INTEGRITY_FAILURE",
                            "official result requires an exact headless reconnect session",
                        )
                    execution = session.execution
                    if (
                        result.log_path != execution.log_path
                        or result.xplt_path != execution.xplt_path
                    ):
                        raise _RunCommandError(
                            "EVIDENCE_INTEGRITY_FAILURE",
                            "official result paths differ from the recorded execution",
                        )
                    return _complete_official_report(
                        command=command,
                        store=opened.store,
                        attempt=session.attempt,
                        snapshot=snapshot,
                        execution=execution,
                        supervisor=session.supervisor,
                        result=result,
                        staged_input=execution.input_path,
                        attempt_payload=official_attempt_payload,
                        solver=solver,
                    )
                official_fbs = False
        solver = {
            "classification": result.classification.value,
            "return_code": result.return_code,
            "state": result.state.value,
        }
        attempt_payload = {
            "attempt_id": attempt_id,
            "official_fbs": official_fbs,
            "status": result.classification.value,
        }
        execution = session.execution
        accepted_classification = (
            result.classification is SolverClassification.FBS_UNVERIFIED and official_fbs is False
        )
        if (
            result.state is not SolverState.NORMAL_EXIT
            or not accepted_classification
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
                attempt_payload["retry_reservation_id"] = retry.reservation_id
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
    except OfficialFbsRuntimeError:
        return _fail_run_after_terminal(
            terminal_store,
            attempt_id,
            "FBS_RUNTIME_FAILED",
            _RunCommandError("FBS_RUNTIME_FAILED", "official FBS runtime validation failed"),
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
    if arguments.command in {"preflight-feb", "preflight-step"}:
        return _emit_preflight(arguments.path, arguments.command)
    if arguments.command == "probe-febio":
        return _emit_probe(arguments.path)
    if arguments.command == "inspect-incomplete-feb":
        return _inspect_incomplete_feb_command(arguments)
    if arguments.command == "plan-step-mesh":
        return _plan_step_mesh_command(arguments)
    if arguments.command in {"root", "case"}:
        return _run_context_command(arguments)
    if arguments.command == "run-febio":
        return _run_febio_command(arguments)
    if arguments.command == "reconnect-febio":
        return _reconnect_febio_command(arguments)
    if arguments.command == "retry-febio":
        return _retry_febio_command(arguments)
    return 0
