"""Case-local producer records, serialized by the existing storage lease."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from febio_cae.domain import CaseRevision, IsotropicLinearElastic, MeshArtifact
from febio_cae.domain.canonical import canonical_bytes
from febio_cae.domain.codec import decode_record

from .mesh_quality import PlanarPreparationRegistration
from .registry import CaseStorage, _read_owned, _safe_identifier, _write_atomic, nested_evidence


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

    def origin(
        self, registration: PlanarPreparationRegistration, revision: CaseRevision
    ) -> CaseRevision:
        """Resolve registered E-only ancestry without transferring producer authority."""
        with self.storage.evidence_snapshot():
            record = self.read(registration.preparation_id)
            if record["status"] != "PREPARED" or record["case_id"] != revision.case_id:
                raise ValueError("matching PREPARED producer receipt is required")
            current = revision
            seen: set[str] = set()
            while True:
                if current.revision_id in seen:
                    raise ValueError("PREPARED ancestry contains a cycle")
                seen.add(current.revision_id)
                registered = self.storage.get_revision(current.case_id, current.revision_id)
                if registered.to_bytes() != current.to_bytes():
                    raise ValueError("PREPARED ancestry revision is not registered")
                if self.storage.resolve_revision_mesh_quality(current) != registration:
                    raise ValueError("PREPARED ancestry changes the mesh profile")
                for evidence in (*current.evidence, *nested_evidence(current.spec)):
                    asset = self.storage.source_asset(evidence.reference)
                    if (
                        asset.content_digest != evidence.content_digest
                        or self.storage.source_kind(evidence.reference) != evidence.source_kind
                    ):
                        raise ValueError(
                            "PREPARED ancestry evidence differs from registered source"
                        )
                    self.storage.resolve_source(asset)
                if current.revision_id == record["revision_id"]:
                    self.prepared(registration, current)
                    return current
                if current.parent_revision_id is None:
                    raise ValueError("PREPARED producer root is absent from ancestry")
                parent = self.storage.get_revision(current.case_id, current.parent_revision_id)
                if parent.spec_digest != current.parent_spec_digest:
                    raise ValueError("PREPARED ancestry parent digest mismatch")
                before, after = parent.spec.material, current.spec.material
                if not isinstance(before, IsotropicLinearElastic) or not isinstance(
                    after, IsotropicLinearElastic
                ):
                    # A valid but unsupported material value is a public input error.
                    raise ValueError("PREPARED reuse requires an explicit Young modulus edit")  # noqa: TRY004
                restored = replace(
                    after,
                    youngs_modulus=before.youngs_modulus,
                    youngs_modulus_evidence=before.youngs_modulus_evidence,
                )
                fixed_before = tuple(
                    e for e in parent.evidence if e.target_field != "material.youngs_modulus"
                )
                fixed_after = tuple(
                    e for e in current.evidence if e.target_field != "material.youngs_modulus"
                )
                if (
                    replace(current.spec, material=restored).to_bytes() != parent.spec.to_bytes()
                    or before.youngs_modulus.to_si().value == after.youngs_modulus.to_si().value
                    or fixed_before != fixed_after
                ):
                    raise ValueError(
                        "PREPARED reuse requires E-only changes with fixed mesh inputs"
                    )
                current = parent

    def origin_output(
        self, registration: PlanarPreparationRegistration, revision: CaseRevision
    ) -> dict[str, Any]:
        with self.storage.evidence_snapshot():
            return self.prepared(registration, self.origin(registration, revision))

    def mesh(
        self, registration: PlanarPreparationRegistration, revision: CaseRevision
    ) -> MeshArtifact:
        from febio_cae.application.service import RegisteredCaseService

        with self.storage.evidence_snapshot():
            origin = self.origin(registration, revision)
            output = self.prepared(registration, origin)
            original = decode_record(canonical_bytes(output["producer"]["mesh"]), MeshArtifact)
            carrier = decode_record(canonical_bytes(output["producer"]["carrier"]), CaseRevision)
            expected, receipt = RegisteredCaseService._adopt_planar_mesh(
                registration, original, carrier, origin
            )
            if expected.to_dict() != output["mesh"] or receipt != output["adoption"]:
                raise ValueError("PREPARED adoption differs from current producer origin")
            if revision.revision_id == origin.revision_id:
                return expected
            adopted, _ = RegisteredCaseService._adopt_planar_mesh(
                registration, original, carrier, revision
            )
            return adopted
