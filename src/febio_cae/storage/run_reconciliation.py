"""Private run snapshots and one lease-protected synchronous interruption CAS."""

from __future__ import annotations

from dataclasses import dataclass

from febio_cae.domain import AttemptRecord, CompatibilityProfile, ResultManifest, RunState
from febio_cae.domain.canonical import canonical_bytes
from febio_cae.domain.codec import decode_record, encode_record
from febio_cae.domain.lifecycle import ServiceDiagnostic, ServiceErrorCategory
from febio_cae.domain.ports import PortError, PortErrorCategory

from ._sqlite import connect
from .registry import CaseStorage, StorageConflictError, _safe_identifier

_INTERRUPTION = ServiceDiagnostic(
    ServiceErrorCategory.CANCELLED,
    "synchronous result publication was interrupted before completion; exclusive operation lease recovered; no execution restarted",
    "run_status",
)


@dataclass(frozen=True)
class RunSnapshot:
    attempt: AttemptRecord
    native: bool
    manifest: ResultManifest | None
    diagnostic: ServiceDiagnostic | None


def snapshot(storage: CaseStorage, run_id: str) -> RunSnapshot:
    """Caller holds the case lease; read identities without issuing owner authority."""
    _safe_identifier(run_id, "run_id")
    with connect(storage.registry_path) as connection:
        row = connection.execute("SELECT * FROM owners WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise ValueError("run is not registered in this case")
        attempt = decode_record(bytes(row["payload"]), AttemptRecord)
        if (attempt.case_id, attempt.run_id, attempt.attempt_id, attempt.owner_generation) != (
            storage._case_id(),
            row["run_id"],
            row["attempt_id"],
            row["owner_generation"],
        ) or row["case_id"] != attempt.case_id:
            raise PortError(PortErrorCategory.INTEGRITY, "registered run identity changed")
        history = connection.execute(
            "SELECT payload FROM attempt_history WHERE attempt_id=? ORDER BY sequence DESC LIMIT 1",
            (attempt.attempt_id,),
        ).fetchone()
        if history is None or bytes(history["payload"]) != encode_record(attempt):
            raise PortError(
                PortErrorCategory.INTEGRITY, "registered run history differs from state"
            )
        native = (
            attempt.process is not None
            or connection.execute(
                "SELECT 1 FROM native_execution WHERE attempt_id=?", (attempt.attempt_id,)
            ).fetchone()
            is not None
        )
        manifests = connection.execute(
            "SELECT manifest_id FROM manifests WHERE attempt_id=?", (attempt.attempt_id,)
        ).fetchall()
        diagnostic = None
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='run_interruptions'"
        ).fetchone():
            record = connection.execute(
                "SELECT * FROM run_interruptions WHERE attempt_id=?", (attempt.attempt_id,)
            ).fetchone()
            if record is not None:
                previous = decode_record(bytes(record["previous_payload"]), AttemptRecord)
                if (
                    bytes(record["terminal_payload"]) != encode_record(attempt)
                    or previous.state is not RunState.VALIDATING
                    or previous.process is not None
                    or previous.transition_to(RunState.INTERRUPTED) != attempt
                    or bytes(record["diagnostic"]) != canonical_bytes(_INTERRUPTION.to_dict())
                ):
                    raise PortError(PortErrorCategory.INTEGRITY, "interruption record changed")
                diagnostic = _INTERRUPTION
    bundle, lineage = storage._lineage(attempt)
    revision = storage.get_revision(attempt.case_id, attempt.revision_id)
    profile = decode_record(bytes(lineage["profile"]), CompatibilityProfile)
    if (
        profile.profile_id != bundle.profile_id
        or profile.solver != bundle.tool
        or bytes(lineage["required_outputs"]) != canonical_bytes(revision.spec.outputs.to_dict())
    ):
        raise PortError(PortErrorCategory.INTEGRITY, "registered run policy/profile changed")
    if len(manifests) > 1:
        raise PortError(PortErrorCategory.INTEGRITY, "run has multiple committed manifests")
    manifest = storage.get_manifest(manifests[0]["manifest_id"]) if manifests else None
    if manifest is not None and (
        manifest.attempt_id != attempt.attempt_id
        or manifest.bundle_digest != bundle.bundle_digest
        or manifest.read_result.status.value != "VALIDATED"
        or lineage["read_candidate"] is None
        or bytes(lineage["read_candidate"]) != encode_record(manifest)
    ):
        raise PortError(PortErrorCategory.INTEGRITY, "run manifest differs from validated reader")
    return RunSnapshot(attempt, native, manifest, diagnostic)


def interrupt(storage: CaseStorage, current: RunSnapshot) -> RunSnapshot:
    """Caller holds non-reentrant recovery lease; append state/history/reason atomically."""
    attempt = current.attempt
    if current.native or attempt.state is not RunState.VALIDATING or current.manifest is None:
        raise PortError(
            PortErrorCategory.UNSUPPORTED_CAPABILITY, "unsupported run reconciliation boundary"
        )
    # Re-resolve all registered source/output/hash bindings immediately before CAS.
    if snapshot(storage, attempt.run_id) != current:
        raise StorageConflictError("run reconciliation snapshot changed")
    _, lineage = storage._lineage(attempt)
    storage._sealed_entries(lineage)
    updated = attempt.transition_to(RunState.INTERRUPTED)
    diagnostic = _INTERRUPTION
    with connect(storage.registry_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS run_interruptions (attempt_id TEXT PRIMARY KEY, previous_payload BLOB NOT NULL, terminal_payload BLOB NOT NULL, diagnostic BLOB NOT NULL)"
        )
        changed = connection.execute(
            "UPDATE owners SET payload=? WHERE case_id=? AND run_id=? AND attempt_id=? AND owner_generation=? AND payload=?",
            (
                encode_record(updated),
                attempt.case_id,
                attempt.run_id,
                attempt.attempt_id,
                attempt.owner_generation,
                encode_record(attempt),
            ),
        )
        if changed.rowcount != 1:
            raise StorageConflictError("run reconciliation lost the current lifecycle snapshot")
        connection.execute(
            "INSERT INTO attempt_history(attempt_id,payload) VALUES(?,?)",
            (attempt.attempt_id, encode_record(updated)),
        )
        connection.execute(
            "INSERT INTO run_interruptions VALUES(?,?,?,?)",
            (
                attempt.attempt_id,
                encode_record(attempt),
                encode_record(updated),
                canonical_bytes(diagnostic.to_dict()),
            ),
        )
        connection.commit()
    return RunSnapshot(updated, False, current.manifest, diagnostic)
