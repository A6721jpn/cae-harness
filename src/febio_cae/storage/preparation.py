"""Case-local producer records, serialized by the existing storage lease."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from febio_cae.domain import CaseRevision, MeshArtifact
from febio_cae.domain.canonical import canonical_bytes
from febio_cae.domain.codec import decode_record

from .mesh_quality import PlanarPreparationRegistration
from .registry import CaseStorage, _read_owned, _safe_identifier, _write_atomic


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


class PreparationStore:
    def __init__(self, storage: CaseStorage) -> None:
        self.storage = storage

    def _path(self, preparation_id: str, name: str) -> str:
        _safe_identifier(preparation_id, "preparation_id")
        return f"preparation/{preparation_id}/{name}.json"

    def _write(self, record: dict[str, Any]) -> None:
        _write_atomic(
            self.storage.root,
            self._path(record["preparation_id"], "state"),
            canonical_bytes(record),
        )

    def begin(self, case_id: str, **bindings: Any) -> dict[str, Any]:
        _safe_identifier(case_id, "case_id")
        with self.storage.transaction():
            record = {
                "format_version": 1,
                "preparation_id": uuid.uuid4().hex,
                "case_id": case_id,
                "status": "PREPARING",
                "revision_id": None,
                "started": datetime.now(UTC).isoformat(),
                **bindings,
            }
            self._write(record)
            _write_atomic(
                self.storage.root,
                f"preparation/latest-{case_id}.json",
                canonical_bytes({"preparation_id": record["preparation_id"]}),
            )
            return record

    def latest(self, case_id: str) -> dict[str, Any]:
        _safe_identifier(case_id, "case_id")
        with self.storage.transaction():
            pointer = json.loads(
                _read_owned(self.storage.root, f"preparation/latest-{case_id}.json")
            )
            return self.read(pointer["preparation_id"])

    def read(self, preparation_id: str) -> dict[str, Any]:
        with self.storage.transaction():
            record = json.loads(_read_owned(self.storage.root, self._path(preparation_id, "state")))
            if record["preparation_id"] != preparation_id:
                raise ValueError("preparation record identity mismatch")
            return dict(record)

    def failed(self, record: dict[str, Any], error: BaseException) -> None:
        with self.storage.transaction():
            current = self.read(record["preparation_id"])
            if current["status"] == "PREPARED":
                raise ValueError("cannot replace prepared origin")
            record.update(
                status="FAILED",
                error=f"{type(error).__name__}: {error}",
                ended=datetime.now(UTC).isoformat(),
            )
            self._write(record)

    def publish(
        self, record: dict[str, Any], revision: CaseRevision, output: dict[str, Any]
    ) -> None:
        with self.storage.transaction():
            current = self.read(record["preparation_id"])
            draft = self.storage.current_draft(revision.case_id)
            if (
                current["status"] != "PREPARING"
                or draft.generation != record["generation"]
                or digest(draft.to_dict()) != record["snapshot_digest"]
            ):
                raise ValueError("preparation final generation/snapshot CAS failed")
            if self.storage.get_revision(revision.case_id, revision.revision_id) != revision:
                raise ValueError("preparation revision is not registered")
            _write_atomic(
                self.storage.root,
                self._path(record["preparation_id"], "prepared"),
                canonical_bytes(output),
            )
            record.update(
                status="PREPARED",
                revision_id=revision.revision_id,
                revision_digest=digest(revision.to_dict()),
                spec_digest=revision.spec_digest,
                evidence_digest=digest([e.to_dict() for e in revision.evidence]),
                output_digest=digest(output),
                ended=datetime.now(UTC).isoformat(),
            )
            self._write(record)  # Sole final publication point; earlier bytes are incomplete.

    def prepared(
        self, registration: PlanarPreparationRegistration, revision: CaseRevision
    ) -> dict[str, Any]:
        with self.storage.evidence_snapshot():
            record = self.read(registration.preparation_id)
            if (
                record["status"] != "PREPARED"
                or record["revision_id"] != revision.revision_id
                or record["case_id"] != revision.case_id
                or record["revision_digest"] != digest(revision.to_dict())
                or record["generation"] != self.storage.revision_generation(revision.revision_id)
                or record["source_digest"] != self.storage.source_asset("cad").content_digest
            ):
                raise ValueError("matching PREPARED producer receipt is required")
            output = json.loads(
                _read_owned(self.storage.root, self._path(registration.preparation_id, "prepared"))
            )
            if digest(output) != record["output_digest"]:
                raise ValueError("PREPARED output digest mismatch")
            return dict(output)

    def mesh(
        self, registration: PlanarPreparationRegistration, revision: CaseRevision
    ) -> MeshArtifact:
        from febio_cae.application.service import RegisteredCaseService

        output = self.prepared(registration, revision)
        original = decode_record(canonical_bytes(output["producer"]["mesh"]), MeshArtifact)
        carrier = decode_record(canonical_bytes(output["producer"]["carrier"]), CaseRevision)
        expected, receipt = RegisteredCaseService._adopt_planar_mesh(
            registration, original, carrier, revision
        )
        if expected.to_dict() != output["mesh"] or receipt != output["adoption"]:
            raise ValueError("PREPARED adoption differs from current producer origin")
        return expected
