"""Private registered comparison inputs and immutable atomic result files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from febio_cae.domain import (
    AttemptRecord,
    CaseRevision,
    CompatibilityProfile,
    ExecutionBundle,
    MeshArtifact,
    ResultManifest,
    RunState,
)
from febio_cae.domain.codec import decode_record, encode_record
from febio_cae.domain.ports import PortError, PortErrorCategory

from ._sqlite import connect
from .registry import (
    CaseStorage,
    StorageConflictError,
    _owned_path,
    _read_owned,
    _safe_identifier,
    _write_atomic,
)


@dataclass(frozen=True)
class ComparisonTarget:
    manifest: ResultManifest
    attempt: AttemptRecord
    bundle: ExecutionBundle
    revision: CaseRevision
    mesh: MeshArtifact
    profile: CompatibilityProfile


def target(storage: CaseStorage, manifest_id: str, run_id: str) -> ComparisonTarget:
    """Resolve sealed bytes, reader payload and successful registered lineage."""
    manifest = storage.get_manifest(manifest_id)
    with connect(storage.registry_path) as connection:
        row = connection.execute(
            "SELECT payload FROM owners WHERE attempt_id=?", (manifest.attempt_id,)
        ).fetchone()
    if row is None:
        raise PortError(PortErrorCategory.INTEGRITY, "comparison attempt is not registered")
    attempt = decode_record(bytes(row["payload"]), AttemptRecord)
    bundle, lineage = storage._lineage(attempt)
    if attempt.run_id != run_id:
        raise ValueError("comparison run ID differs from the manifest's registered run")
    if (
        attempt.state is not RunState.SUCCEEDED
        or manifest.read_result.status.value != "VALIDATED"
        or manifest.manifest_id != manifest_id
        or manifest.attempt_id != attempt.attempt_id
        or manifest.bundle_digest != bundle.bundle_digest
        or lineage["read_candidate"] is None
        or encode_record(manifest) != bytes(lineage["read_candidate"])
    ):
        raise PortError(
            PortErrorCategory.INTEGRITY, "comparison requires complete validated results"
        )
    return ComparisonTarget(
        manifest,
        attempt,
        bundle,
        storage.get_revision(attempt.case_id, attempt.revision_id),
        decode_record(bytes(lineage["mesh"]), MeshArtifact),
        decode_record(bytes(lineage["profile"]), CompatibilityProfile),
    )


def publish(storage: CaseStorage, comparison_id: str, payload: bytes) -> Path:
    """Caller holds the case transaction; identical replay is idempotent."""
    _safe_identifier(comparison_id, "comparison_id")
    relative = f"cases/{storage._case_id()}/comparisons/{comparison_id}/comparison.json"
    path = _owned_path(storage.root, relative)
    if path.exists():
        if _read_owned(storage.root, relative) != payload:
            raise StorageConflictError("comparison ID already records different inputs or results")
    else:
        _write_atomic(storage.root, relative, payload)
    return path
