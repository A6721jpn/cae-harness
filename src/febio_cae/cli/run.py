"""Public read-only status and narrowly bounded interruption reconciliation."""

from __future__ import annotations

from argparse import Namespace

from febio_cae.application.service import RegisteredCaseService, ServiceConflictError
from febio_cae.domain.ports import PortError, PortErrorCategory
from febio_cae.storage.registry import StorageConflictError, StorageIntegrityError

from .case import _error_payload, _print


def run_lifecycle(arguments: Namespace) -> int:
    try:
        service = RegisteredCaseService(state_dir=arguments.state_dir)
        operation = service.run_status if arguments.command == "status" else service.resume_run
        payload = operation(arguments.case_id, arguments.run_id)
        code = (
            0
            if arguments.command == "status"
            else {
                "UNSUPPORTED_ENVIRONMENT": 4,
                "FAILED": 5,
                "INTERRUPTED": 7,
                "CANCELLED": 7,
            }.get(str(payload["status"]), 0)
        )
    except (
        OSError,
        ValueError,
        PortError,
        StorageConflictError,
        StorageIntegrityError,
        ServiceConflictError,
    ) as error:
        payload, code = _error_payload(error)
        if isinstance(error, (StorageConflictError, ServiceConflictError)) or (
            isinstance(error, PortError) and error.category is PortErrorCategory.CONFLICT
        ):
            code, payload["status"] = 8, "CONFLICT"
        if isinstance(error, StorageIntegrityError):
            code, payload["status"] = 6, "INELIGIBLE"
        payload.update(
            case_id=arguments.case_id,
            run_id=arguments.run_id,
            run_status=None,
            quality_status=None,
            preview_status=None,
            task_status=None,
        )
    _print(payload) if arguments.json else print(payload["status"], payload.get("run_status"))
    return code
