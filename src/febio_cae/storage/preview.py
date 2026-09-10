"""Durable issued-preview records; never restores a live adapter's authority."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from febio_cae.domain import (
    AttemptRecord,
    CaseRevision,
    CompatibilityProfile,
    ExecutionBundle,
    FileEntry,
    MeshArtifact,
    PreviewReceipt,
    PreviewStatus,
    ResultManifest,
    RunState,
)
from febio_cae.domain.canonical import canonical_bytes
from febio_cae.domain.codec import decode_record, encode_record
from febio_cae.domain.ports import PortError, PortErrorCategory
from febio_cae.domain.results import numeric_state_indices

from ._sqlite import connect
from .registry import CaseStorage, StorageConflictError, _safe_identifier


@dataclass(frozen=True)
class PreviewTarget:
    storage: CaseStorage
    manifest: ResultManifest
    attempt: AttemptRecord
    bundle: ExecutionBundle
    revision: CaseRevision
    mesh: MeshArtifact
    profile: CompatibilityProfile
    entry: FileEntry
    path: Path
    final_state_id: int
    final_time: float
    variable: str
    component: str
    unit: str

    def read(self) -> bytes:
        self.storage.get_manifest(self.manifest.manifest_id)
        return self.storage.resolve_file(self.entry, self.bundle, self.attempt).content


class RegisteredPreviewStore:
    def __init__(self, storage: CaseStorage) -> None:
        self.storage = storage
        with storage.transaction(), connect(storage.registry_path) as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS registered_previews (
                    preview_id TEXT PRIMARY KEY, manifest_id TEXT NOT NULL,
                    binding BLOB NOT NULL, issued_receipt BLOB NOT NULL,
                    current_receipt BLOB NOT NULL, issued_ns INTEGER NOT NULL
                )"""
            )

    def target(self, manifest_id: str) -> PreviewTarget:
        storage = self.storage
        with storage.transaction():
            manifest = storage.get_manifest(manifest_id)
            with connect(storage.registry_path) as connection:
                owner = connection.execute(
                    "SELECT payload FROM owners WHERE attempt_id=?", (manifest.attempt_id,)
                ).fetchone()
                lineage = connection.execute(
                    "SELECT * FROM execution_lineage WHERE attempt_id=?", (manifest.attempt_id,)
                ).fetchone()
            if owner is None or lineage is None:
                raise StorageConflictError("preview execution is not registered")
            attempt = decode_record(bytes(owner["payload"]), AttemptRecord)
            if attempt.state is not RunState.SUCCEEDED:
                raise StorageConflictError("preview requires completed registered execution")
            bundle = decode_record(bytes(lineage["bundle"]), ExecutionBundle)
            revision = storage.get_revision(attempt.case_id, attempt.revision_id)
            outputs = [r for r in revision.spec.outputs.requests if r.quantity_id == "displacement"]
            if len(outputs) != 1:
                raise ValueError("preview requires one registered displacement request")
            output = outputs[0]
            numeric = storage.resolve_manifest_output(manifest_id, output.request_id)
            final_time = max(t.to_si().value for t in revision.spec.outputs.saved_times)
            if numeric.axis_id != "state_time" or numeric.axis_unit != "s":
                raise ValueError("preview state mapping requires registered seconds axis")
            final_id = numeric_state_indices(numeric, (float(final_time),))[0]
            if (
                output.component_id != "z"
                or "z" not in numeric.component_ids
                or numeric.mapping.frame.value != "World"
            ):
                raise ValueError("preview requires explicit registered World-z component")
            entries = [
                e for e in manifest.files if e.role == "result" and e.logical_path.endswith(".xplt")
            ]
            if len(entries) != 1:
                raise ValueError("preview requires one registered XPLT")
            entry = entries[0]
            path = (
                storage.root
                / f"cases/{attempt.case_id}/runs/{attempt.run_id}/attempts/{attempt.attempt_id}/{entry.logical_path}"
            )
            return PreviewTarget(
                storage,
                manifest,
                attempt,
                bundle,
                revision,
                decode_record(bytes(lineage["mesh"]), MeshArtifact),
                decode_record(bytes(lineage["profile"]), CompatibilityProfile),
                entry,
                path,
                final_id,
                final_time,
                numeric.mapping.native_name,
                "z",
                numeric.mapping.unit,
            )

    def issue(self, receipt: PreviewReceipt, binding: dict[str, object]) -> dict[str, Any]:
        _safe_identifier(receipt.receipt_id, "preview_id")
        with self.storage.transaction():
            target = self.target(receipt.manifest_id)
            if (
                receipt.status is not PreviewStatus.REQUESTED
                or receipt.xplt_digest != target.entry.digest
                or tuple(receipt.requested_state_ids) != (target.final_state_id,)
                or tuple(receipt.requested_variables) != (target.variable,)
                or receipt.confirmation_evidence
                or receipt.observed_state_ids
                or receipt.observed_variables
                or binding.get("source_path") != str(target.path)
                or binding.get("manifest_id") != receipt.manifest_id
                or binding.get("xplt_digest") != receipt.xplt_digest
                or not isinstance(binding.get("nonce"), str)
                or not binding["nonce"]
            ):
                raise StorageConflictError("preview issue differs from registered target")
            with connect(self.storage.registry_path) as connection:
                connection.execute("BEGIN IMMEDIATE")
                if connection.execute(
                    "SELECT 1 FROM registered_previews WHERE preview_id=?", (receipt.receipt_id,)
                ).fetchone():
                    raise StorageConflictError("preview identity was already issued")
                connection.execute(
                    "INSERT INTO registered_previews VALUES(?,?,?,?,?,?)",
                    (
                        receipt.receipt_id,
                        receipt.manifest_id,
                        canonical_bytes(binding),
                        encode_record(receipt),
                        encode_record(receipt),
                        time.time_ns(),
                    ),
                )
                connection.commit()
            return self.get(receipt.receipt_id)

    def get(self, preview_id: str) -> dict[str, Any]:
        with self.storage.transaction(), connect(self.storage.registry_path) as connection:
            row = connection.execute(
                "SELECT * FROM registered_previews WHERE preview_id=?", (preview_id,)
            ).fetchone()
            if row is None:
                raise StorageConflictError("preview was not issued")
            target = self.target(row["manifest_id"])
            receipt = decode_record(bytes(row["current_receipt"]), PreviewReceipt)
            if receipt.xplt_digest != target.entry.digest:
                raise StorageConflictError("preview result binding changed")
            self._check_evidence(receipt)
            return {
                "receipt": receipt.to_dict(),
                "binding": json.loads(bytes(row["binding"])),
                "issued_ns": row["issued_ns"],
            }

    def _check_evidence(self, receipt: PreviewReceipt) -> None:
        for evidence in receipt.confirmation_evidence:
            source = self.storage.source_asset(evidence.reference)
            self.storage.resolve_source(source)
            if (
                source.content_digest != evidence.content_digest
                or evidence.target_field != "preview.confirmation"
            ):
                raise PortError(PortErrorCategory.INTEGRITY, "preview evidence is not registered")

    def finish(self, receipt: PreviewReceipt, nonce: str) -> None:
        with self.storage.transaction(), connect(self.storage.registry_path) as connection:
            current = self.get(receipt.receipt_id)
            issued = decode_record(canonical_bytes(current["receipt"]), PreviewReceipt)
            if (
                current["binding"]["nonce"] != nonce
                or issued.status is not PreviewStatus.REQUESTED
                or receipt.status not in {PreviewStatus.CONFIRMED, PreviewStatus.FAILED}
                or replace(
                    receipt,
                    status=issued.status,
                    observed_state_ids=(),
                    observed_variables=(),
                    confirmation_evidence=(),
                )
                != issued
            ):
                raise StorageConflictError("preview completion is not the issued request")
            if receipt.status is PreviewStatus.CONFIRMED and (
                not set(issued.requested_state_ids) <= set(receipt.observed_state_ids)
                or not set(issued.requested_variables) <= set(receipt.observed_variables)
            ):
                raise StorageConflictError("preview observation misses requested data")
            self._check_evidence(receipt)
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE registered_previews SET current_receipt=? WHERE preview_id=?",
                (encode_record(receipt), receipt.receipt_id),
            )
            connection.commit()
