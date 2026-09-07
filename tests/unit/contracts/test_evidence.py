from __future__ import annotations

import importlib
from dataclasses import FrozenInstanceError
from types import ModuleType
from typing import Any

import pytest


def _optional_module(name: str) -> ModuleType | None:
    try:
        return importlib.import_module(name)
    except (ImportError, ModuleNotFoundError):
        return None


EVIDENCE_MODULE = _optional_module("febio_cae.domain.evidence")


def _evidence_type() -> Any:
    if EVIDENCE_MODULE is None:
        pytest.skip("EvidenceRef API availability is covered by the dedicated assertion")
    evidence_ref = getattr(EVIDENCE_MODULE, "EvidenceRef", None)
    if evidence_ref is None:
        pytest.skip("EvidenceRef API availability is covered by the dedicated assertion")
    return evidence_ref


def test_evidence_api_is_available() -> None:
    assert EVIDENCE_MODULE is not None, "P1-A evidence module is not available"
    assert getattr(EVIDENCE_MODULE, "EvidenceRef", None) is not None


def test_evidence_ref_is_immutable_and_retains_explicit_provenance() -> None:
    EvidenceRef = _evidence_type()
    reference = EvidenceRef(
        schema_version="1",
        source_kind="user_instruction",
        reference="UserInstruction:ABC-Rev-1",
        target_field="material.youngs_modulus",
        content_digest="a" * 64,
    )

    assert reference.to_dict() == {
        "schema_version": "1",
        "source_kind": "user_instruction",
        "reference": "UserInstruction:ABC-Rev-1",
        "target_field": "material.youngs_modulus",
        "content_digest": "a" * 64,
    }
    with pytest.raises(FrozenInstanceError):
        reference.reference = "changed"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "2"),
        ("schema_version", 1),
        ("source_kind", "llm_confidence"),
        ("reference", ""),
        ("target_field", "material..youngs_modulus"),
        ("content_digest", "not-a-sha256-digest"),
        ("content_digest", "A" * 64),
    ],
)
def test_evidence_ref_rejects_unresolved_or_invalid_fields(field: str, value: object) -> None:
    EvidenceRef = _evidence_type()
    values: dict[str, object] = {
        "schema_version": "1",
        "source_kind": "registered_document",
        "reference": "Doc:ABC",
        "target_field": "material.youngs_modulus",
        "content_digest": "b" * 64,
    }
    values[field] = value

    with pytest.raises(ValueError):
        EvidenceRef(**values)


def test_evidence_ref_accepts_registered_source_kinds_but_not_confidence() -> None:
    EvidenceRef = _evidence_type()
    assert EVIDENCE_MODULE is not None
    allowed = set(EVIDENCE_MODULE.SUPPORTED_SOURCE_KINDS)
    assert {"user_instruction", "registered_document", "registered_material"} <= allowed
    for source_kind in allowed:
        reference = EvidenceRef(
            schema_version="1",
            source_kind=source_kind,
            reference="Source:ABC",
            target_field="motion.displacement",
            content_digest="c" * 64,
        )
        assert reference.source_kind == source_kind
