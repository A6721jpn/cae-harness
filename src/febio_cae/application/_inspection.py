"""Read-only initial registered-source observation with short before/after snapshots."""

from __future__ import annotations

import math
import os
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from febio_cae.adapters.geometry import preparation
from febio_cae.adapters.geometry.inspection import remaining as _remaining
from febio_cae.adapters.geometry.inspection import run_inspection
from febio_cae.adapters.geometry.preparation import resource_snapshot
from febio_cae.domain.ports import PortError, PortErrorCategory
from febio_cae.storage.registry import CaseStorage, StorageConflictError, StorageIntegrityError

if TYPE_CHECKING:
    from .service import RegisteredCaseService


def remaining(deadline: float) -> float:
    # Storage transactions translate OSError; classify our deadline before crossing them.
    try:
        return _remaining(deadline)
    except TimeoutError as error:
        raise PortError(PortErrorCategory.ENVIRONMENT, str(error)) from error


@contextmanager
def _snapshot(storage: CaseStorage, deadline: float) -> Iterator[None]:
    remaining(deadline)
    # Do not wait behind another operation's long-running transaction.
    with storage.transaction(blocking=False) as acquired:
        if not acquired:
            raise PortError(PortErrorCategory.CONFLICT, "inspection source snapshot is busy")
        with storage.evidence_snapshot():
            remaining(deadline)
            yield


@dataclass(frozen=True, slots=True)
class InspectionPolicy:
    wall_seconds: float = 600
    cpu_workers: int | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.wall_seconds, bool)
            or not isinstance(self.wall_seconds, (int, float))
            or not 0 < self.wall_seconds <= 600
            or not math.isfinite(self.wall_seconds)
        ):
            raise ValueError("inspection wall_seconds must be finite positive and at most 600")
        if self.cpu_workers is not None and (
            type(self.cpu_workers) is not int or self.cpu_workers <= 0
        ):
            raise ValueError("inspection cpu_workers must be a positive integer")


def inspect_native(
    service: RegisteredCaseService,
    case_id: str,
    *,
    wall_seconds: float = 600,
    cpu_workers: int | None = None,
) -> dict[str, Any]:
    from .service import ServiceConflictError

    started = time.monotonic()
    generation: int | None = None
    result: dict[str, Any] = {
        "schema_version": "1",
        "case_id": case_id,
        "revision_id": None,
        "run_id": None,
        "generation": None,
        "diagnostics": [],
        "next_actions": [],
        "native_qualification": "UNVERIFIED",
    }
    try:
        policy = InspectionPolicy(wall_seconds, cpu_workers)
        deadline = started + policy.wall_seconds
        resources = resource_snapshot(policy.cpu_workers or os.cpu_count() or 1)
        if policy.cpu_workers is not None and policy.cpu_workers > resources["available_cpus"]:
            raise ValueError("inspection cpu_workers exceeds available CPUs")
        limits = {
            **resources,
            "wall_seconds": policy.wall_seconds,
            "mesh_generations": 0,
            "max_response_bytes": min(16 * 1024 * 1024, resources["memory_bytes"] // 16),
        }
        result["limits"] = limits
        remaining(deadline)
        storage = service._storage(case_id)
        with _snapshot(storage, deadline):
            current = storage.current_draft(case_id)
            generation = current.generation
            result["generation"] = generation
            source_ref = storage.source_asset("cad")
            source = storage.resolve_source(source_ref)
            directory = Path(tempfile.mkdtemp(prefix="inspection-", dir=storage.root))
        result["operation_directory"] = str(directory)

        def check_snapshot() -> None:
            with _snapshot(storage, deadline):
                if storage.current_draft(case_id).generation != generation:
                    raise PortError(
                        PortErrorCategory.CONFLICT, "inspection draft generation changed"
                    )
                current_ref = storage.source_asset("cad")
                current_source = storage.resolve_source(current_ref)
                if current_ref != source_ref or current_source.content != source.content:
                    raise PortError(
                        PortErrorCategory.INTEGRITY, "inspection registered source changed"
                    )
                remaining(deadline)

        observed = run_inspection(source, limits, directory, deadline, check_snapshot)
        check_snapshot()
        result.update(observed)
        result.update(
            status="INSPECTED",
            next_actions=[
                "explicitly select body, placement and physical conditions using these source-scoped observations"
            ],
        )
    except (
        PortError,
        OSError,
        ValueError,
        TypeError,
        StorageConflictError,
        StorageIntegrityError,
        ServiceConflictError,
    ) as error:
        category = (
            error.category
            if isinstance(error, PortError)
            else PortErrorCategory.INTEGRITY
            if isinstance(error, StorageIntegrityError)
            else PortErrorCategory.CONFLICT
            if isinstance(error, (StorageConflictError, ServiceConflictError))
            else PortErrorCategory.ENVIRONMENT
            if isinstance(error, OSError)
            else PortErrorCategory.INVALID_INPUT
        )
        result.update(
            status="CONFLICT"
            if category is PortErrorCategory.CONFLICT
            else "UNSUPPORTED_ENVIRONMENT"
            if category in {PortErrorCategory.ENVIRONMENT, PortErrorCategory.UNSUPPORTED_CAPABILITY}
            else "INVALID_INPUT",
            diagnostics=[
                {
                    "schema_version": "1",
                    "code": category.value,
                    "message": str(error),
                    "field": "geometry",
                    "retryable": category is PortErrorCategory.CONFLICT,
                }
            ],
        )
    result["pending_cleanup"] = len(preparation._pending)
    return result
