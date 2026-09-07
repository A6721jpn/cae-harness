from __future__ import annotations

import hashlib

from febio_cae.domain import CaseRevision, CaseSpec, EvidenceRef


def evidence(target_field: str, seed: str) -> EvidenceRef:
    return EvidenceRef(
        schema_version="1",
        source_kind="registered_document",
        reference="P1InterfaceSyntheticFixture:1",
        target_field=target_field,
        content_digest=hashlib.sha256(seed.encode("utf-8")).hexdigest(),
    )


def case_revision(spec: CaseSpec) -> CaseRevision:
    return CaseRevision(
        case_id="case-interface",
        revision_id="revision-interface",
        parent_revision_id=None,
        parent_spec_digest=None,
        spec=spec,
        evidence=(evidence("case_revision.spec", "revision"),),
    )
