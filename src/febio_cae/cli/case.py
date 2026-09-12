"""Explicit registered-case CLI commands."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from typing import Any

from febio_cae.application.service import (
    ConcurrentUpdateError,
    CreatedCase,
    RegisteredCaseService,
    ServiceConflictError,
    ServiceResult,
)
from febio_cae.application.specs import SpecInputError, parse_spec_request
from febio_cae.domain.case_patch import CasePatch
from febio_cae.domain.codec import decode_record
from febio_cae.domain.lifecycle import ServiceErrorCategory
from febio_cae.domain.ports import PortError
from febio_cae.storage.registry import StorageConflictError, StorageIntegrityError


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _read_json(path: Path) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_pairs
        )
    except (OSError, UnicodeError, ValueError) as error:
        raise SpecInputError(f"cannot read explicit specification: {error}") from error


def _print(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _created_payload(created: CreatedCase) -> dict[str, object]:
    return {
        "schema_version": "1",
        "status": "REGISTERED",
        "case_id": created.case_id,
        "revision_id": None,
        "run_id": None,
        "diagnostics": [],
        "next_actions": ["inspect the registered case and submit an explicit typed specification"],
        "source_asset": created.source_asset.to_dict(),
        "draft": created.draft.to_dict(),
    }


def _exit_for(result: ServiceResult) -> int:
    if result.status in {"REGISTERED", "INSPECTED", "UPDATED", "VALIDATED", "FROZEN"}:
        return 0
    if result.status == "NEEDS_INPUT":
        return 3
    if result.status == "UNSUPPORTED_ENVIRONMENT":
        return 4
    if any(item.code is ServiceErrorCategory.INTEGRITY for item in result.diagnostics):
        return 6
    if result.status == "CONFLICT":
        return 8
    return 2


def _error_payload(error: Exception) -> tuple[dict[str, object], int]:
    if isinstance(error, OSError):
        from febio_cae.domain.ports import PortErrorCategory

        return _error_payload(PortError(PortErrorCategory.ENVIRONMENT, str(error)))
    if isinstance(error, ConcurrentUpdateError):
        return (
            {
                "schema_version": "1",
                "status": "CONFLICT",
                "case_id": None,
                "revision_id": None,
                "run_id": None,
                "diagnostics": [
                    {
                        "schema_version": "1",
                        "code": "conflict",
                        "message": str(error),
                        "field": None,
                        "retryable": True,
                    }
                ],
                "next_actions": ["re-read the current draft generation"],
            },
            8,
        )
    if isinstance(error, PortError):
        category = error.category.value
        exit_code = (
            4
            if error.category
            in {
                error.category.UNSUPPORTED_CAPABILITY,
                error.category.ENVIRONMENT,
            }
            else 6
            if error.category is error.category.INTEGRITY
            else 2
        )
    else:
        category = (
            "invalid_input"
            if isinstance(error, (SpecInputError, ValueError, TypeError))
            else "conflict"
        )
        exit_code = 2 if category == "invalid_input" else 8
    status = "UNSUPPORTED_ENVIRONMENT" if exit_code == 4 else "INVALID_INPUT"
    if isinstance(error, PortError) and error.category is error.category.CONFLICT:
        status, exit_code = "CONFLICT", 8
    return (
        {
            "schema_version": "1",
            "status": status,
            "case_id": None,
            "revision_id": None,
            "run_id": None,
            "diagnostics": [
                {
                    "schema_version": "1",
                    "code": category,
                    "message": str(error),
                    "field": None,
                    "retryable": False,
                }
            ],
            "next_actions": [],
        },
        exit_code,
    )


def run_case(arguments: Namespace) -> int:
    service = None
    try:
        service = RegisteredCaseService(state_dir=arguments.state_dir)
        if arguments.case_action in {"intent", "answer", "edit"}:
            from febio_cae.application.intent_contracts import strict_json

            settings_path = Path(arguments.llm_settings)
            with settings_path.open("rb") as stream:
                settings = strict_json(stream.read(256 * 1024 + 1))
            payload = service.process_intent(
                arguments.case_id,
                action=arguments.case_action,
                text=arguments.text,
                expected_generation=arguments.expected_generation,
                operation_id=arguments.operation_id,
                settings=settings,
                question_id=getattr(arguments, "question", None),
                base=getattr(arguments, "base", None),
            )
            _print(payload) if arguments.json else print(payload["status"])
            if any(item.get("code") == "integrity" for item in payload.get("diagnostics", [])):
                return 6
            return {
                "UPDATED": 0,
                "NEEDS_INPUT": 3,
                "UNSUPPORTED_ENVIRONMENT": 4,
                "CONFLICT": 8,
            }.get(payload["status"], 2)
        if arguments.case_action == "prepare-planar":
            payload = service.prepare_planar(
                arguments.case_id,
                _read_json(Path(arguments.file)),
                expected_generation=arguments.expected_generation,
                parent_revision_id=arguments.parent_revision_id,
            )
            _print(payload) if arguments.json else print(payload["status"])
            return 0
        if arguments.case_action == "preview":
            from .preview import capture_observation

            payload = service.observe_preview(
                arguments.case_id,
                arguments.manifest_id,
                studio_executable=arguments.studio,
                window_id=arguments.window_id,
                timeout_seconds=arguments.timeout,
                capture=capture_observation,
            )
            _print(payload) if arguments.json else print(payload["task_status"])
            return 0 if payload["task_status"] == "COMPLETE" else 6
        if arguments.case_action == "preview-status":
            payload = service.preview_status(arguments.case_id, arguments.preview_id)
            _print(payload) if arguments.json else print(payload["task_status"])
            return 0
        if arguments.case_action == "run-demo":
            payload = service.run_demo(
                arguments.case_id,
                arguments.revision_id,
                executable=arguments.solver,
                preflight=arguments.preflight,
            )
            _print(payload) if arguments.json else print(payload["status"])
            if payload["status"] in {"PREFLIGHT_PASSED", "NEEDS_PREVIEW"}:
                return 0
            return 6 if payload["status"] == "NEEDS_REVIEW" else 2
        if arguments.case_action == "create":
            created = service.create_case(case_root=arguments.case_root, cad_path=arguments.cad)
            payload = _created_payload(created)
            _print(payload) if arguments.json else print(created.case_id)
            return 0
        if arguments.case_action == "inspect":
            if arguments.native:
                payload = service.inspect_native(
                    arguments.case_id,
                    wall_seconds=arguments.wall_seconds,
                    cpu_workers=arguments.cpu_workers,
                )
                _print(payload) if arguments.json else print(payload["status"])
                if any(item["code"] == "integrity" for item in payload["diagnostics"]):
                    return 6
                return {"INSPECTED": 0, "UNSUPPORTED_ENVIRONMENT": 4, "CONFLICT": 8}.get(
                    payload["status"], 2
                )
            result = service.inspect_case(arguments.case_id)
            _print(result.to_dict()) if arguments.json else print(result.status)
            # Metadata inspection itself succeeded even if an optional native inspection is absent.
            return 0
        if arguments.case_action == "spec":
            request = parse_spec_request(_read_json(Path(arguments.file)))
            draft = service.set_spec(
                arguments.case_id,
                values=request.values,
                expected_generation=arguments.expected_generation,
                evidence=request.evidence,
                source_declarations=request.source_declarations,
                input_intent=request.input_intent,
            )
            result = ServiceResult("UPDATED", arguments.case_id, draft=draft)
            _print(result.to_dict()) if arguments.json else print(draft.generation)
            return 0
        if arguments.case_action == "patch":
            patch = decode_record(
                json.dumps(_read_json(Path(arguments.file)), ensure_ascii=False), CasePatch
            )
            draft = service.apply_patch(
                arguments.case_id, patch, expected_generation=arguments.expected_generation
            )
            result = ServiceResult("UPDATED", arguments.case_id, draft=draft)
            _print(result.to_dict()) if arguments.json else print(draft.generation)
            return 0
        if arguments.case_action == "validate":
            result = service.validate_case(arguments.case_id)
            _print(result.to_dict()) if arguments.json else print(result.status)
            return _exit_for(result)
        if arguments.case_action == "freeze":
            result = service.freeze_case(arguments.case_id)
            _print(result.to_dict()) if arguments.json else print(result.status)
            return _exit_for(result)
        raise SpecInputError("unsupported case command")
    except (
        OSError,
        PortError,
        ServiceConflictError,
        SpecInputError,
        TypeError,
        StorageIntegrityError,
        StorageConflictError,
        ValueError,
    ) as error:
        payload, code = _error_payload(error)
        if arguments.case_action == "preview" and isinstance(error, TimeoutError):
            code = 7
            payload["status"] = "NEEDS_PREVIEW"
            payload["task_status"] = "NEEDS_PREVIEW"
            payload["preview_status"] = "FAILED"
            payload["case_id"] = arguments.case_id
            payload["diagnostics"] = [
                {
                    "schema_version": "1",
                    "code": "cancelled",
                    "message": str(error),
                    "field": None,
                    "retryable": False,
                }
            ]
        if arguments.case_action == "run-demo" and service is not None:
            pending = 1
            for _ in range(3):
                try:
                    pending = service._retry_pending_cleanup()
                except (
                    OSError,
                    PortError,
                    StorageIntegrityError,
                    StorageConflictError,
                ) as cleanup_error:
                    payload["cleanup_error"] = str(cleanup_error)
                    continue
                if pending == 0:
                    break
            payload["pending_cleanup"] = pending
            if pending:
                payload["status"] = "CLEANUP_PENDING"
        _print(payload) if getattr(arguments, "json", False) else print(str(error))
        return code


__all__ = ["run_case"]
