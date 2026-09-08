"""Read run state or diagnose the narrowly verified synchronous publication gap."""

from __future__ import annotations

from contextlib import ExitStack
from typing import TYPE_CHECKING

from febio_cae.adapters.febio import QualityAdapter
from febio_cae.domain import CompatibilityProfile, MeshArtifact, PreviewStatus, RunState
from febio_cae.domain.codec import decode_record, encode_record
from febio_cae.domain.lifecycle import (
    OperationStatus,
    ServiceDiagnostic,
    ServiceErrorCategory,
    TaskStatus,
)
from febio_cae.domain.ports import PortError, PortErrorCategory
from febio_cae.storage._sqlite import connect
from febio_cae.storage.registry import CaseStorage, StorageConflictError
from febio_cae.storage.run_reconciliation import RunSnapshot, interrupt, snapshot

if TYPE_CHECKING:
    from .service import RegisteredCaseService


def _completion(
    storage: CaseStorage, current: RunSnapshot
) -> tuple[str, PreviewStatus | None, TaskStatus]:
    from febio_cae.storage.preview import RegisteredPreviewStore

    from ._preview import preview_summary

    attempt, manifest = current.attempt, current.manifest
    if manifest is None:
        raise PortError(PortErrorCategory.INTEGRITY, "successful run lacks committed result")
    _, lineage = storage._lineage(attempt)
    revision = storage.get_revision(attempt.case_id, attempt.revision_id)
    quality = QualityAdapter().assess(
        manifest,
        revision,
        decode_record(bytes(lineage["mesh"]), MeshArtifact),
        decode_record(bytes(lineage["profile"]), CompatibilityProfile),
        storage,
    )
    try:
        asset = storage.source_asset("quality-" + quality.assessment_id[:24])
    except StorageConflictError:
        quality_status = "UNVERIFIED"
    else:
        if storage.resolve_source(asset).content != encode_record(quality):
            raise PortError(PortErrorCategory.INTEGRITY, "registered run quality changed")
        quality_status = quality.overall_status.value
    preview = None
    task = TaskStatus.FAILED if quality_status == "FAIL" else TaskStatus.NEEDS_PREVIEW
    with connect(storage.registry_path) as connection:
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='registered_previews'"
        ).fetchone():
            preview = connection.execute(
                "SELECT preview_id FROM registered_previews WHERE manifest_id=? ORDER BY issued_ns DESC LIMIT 1",
                (manifest.manifest_id,),
            ).fetchone()
    if preview is None:
        return quality_status, None, task
    summary = preview_summary(RegisteredPreviewStore(storage), preview["preview_id"])
    if quality_status == "PASS" and summary["task_status"] == "COMPLETE":
        task = TaskStatus.COMPLETE
    return quality_status, PreviewStatus(str(summary["preview_status"])), task


def reconcile(
    service: RegisteredCaseService, case_id: str, run_id: str, *, resume: bool
) -> dict[str, object]:
    storage = service._storage(case_id)
    # recovery=True rejects even a same-thread nested live operation. Null process
    # identity alone is never evidence that the cooperative operation has ended.
    with storage.transaction(blocking=False, recovery=resume) as acquired, ExitStack() as pins:
        if not acquired:
            raise StorageConflictError("run operation is still held by a live exclusive lease")
        pins.enter_context(storage.evidence_snapshot())
        current = snapshot(storage, run_id)
        attempt = current.attempt
        unsupported = resume and (
            current.native
            or attempt.state
            in {
                RunState.CREATED,
                RunState.PREPARING,
                RunState.RUNNING,
                RunState.DRAINING,
            }
            or (attempt.state is RunState.VALIDATING and current.manifest is None)
        )
        if resume and not unsupported and attempt.state is RunState.VALIDATING:
            current = interrupt(storage, current)
            attempt = current.attempt
        diagnostics = [current.diagnostic] if current.diagnostic is not None else []
        quality_status, preview_status = "UNVERIFIED", None
        task = {
            RunState.CREATED: TaskStatus.READY,
            RunState.FAILED: TaskStatus.FAILED,
            RunState.CANCELLED: TaskStatus.INTERRUPTED,
            RunState.INTERRUPTED: TaskStatus.INTERRUPTED,
        }.get(attempt.state, TaskStatus.RUNNING)
        if unsupported:
            diagnostics.append(
                ServiceDiagnostic(
                    ServiceErrorCategory.UNSUPPORTED_CAPABILITY,
                    "resume supports only closed synchronous result publication; native ownership and other run boundaries are not adopted or restarted",
                    "run_status",
                )
            )
        elif attempt.state is RunState.SUCCEEDED:
            quality_status, preview_status, task = _completion(storage, current)
        elif not diagnostics:
            diagnostics.append(
                ServiceDiagnostic(
                    ServiceErrorCategory.EXECUTION,
                    "run is not complete; persistent state is not proof of a live operation or successful execution",
                    "run_status",
                )
            )
        actions = (
            []
            if task is TaskStatus.COMPLETE
            else [
                "inspect retained results and interruption diagnostics; no execution was restarted"
                if attempt.state is RunState.INTERRUPTED
                else "use resume for supported synchronous publication reconciliation; native reconciliation is unsupported"
                if attempt.state is not RunState.SUCCEEDED
                else "verify required quality and registered preview confirmation"
            ]
        )
        return OperationStatus(
            "UNSUPPORTED_ENVIRONMENT"
            if unsupported
            else attempt.state.value
            if resume
            else "STATUS",
            case_id,
            attempt.revision_id,
            run_id,
            diagnostics,
            actions,
            attempt.state,
            quality_status,
            preview_status,
            task,
        ).to_dict()
