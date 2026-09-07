from __future__ import annotations

import hashlib
import importlib
import json
from dataclasses import FrozenInstanceError, replace
from types import ModuleType
from typing import Any

import pytest

from febio_cae.domain import EvidenceRef, Quantity, canonical_bytes


def _optional_module(name: str) -> ModuleType | None:
    try:
        return importlib.import_module(name)
    except (ImportError, ModuleNotFoundError):
        return None


REVISION_MODULE = _optional_module("febio_cae.domain.case_revision")


def _revision() -> ModuleType:
    if REVISION_MODULE is None:
        pytest.skip("CaseRevision API availability is covered by the dedicated assertion")
    return REVISION_MODULE


def _evidence(
    target_field: str,
    seed: str,
    *,
    reference: str = "SyntheticRevisionSource:1",
) -> EvidenceRef:
    return EvidenceRef(
        schema_version="1",
        source_kind="registered_document",
        reference=reference,
        target_field=target_field,
        content_digest=hashlib.sha256(seed.encode("utf-8")).hexdigest(),
    )


def _revision_kwargs(spec: Any, **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "case_id": "case_alpha",
        "revision_id": "revision_001",
        "parent_revision_id": None,
        "parent_spec_digest": None,
        "spec": spec,
        "evidence": [
            _evidence("case_revision.source_a", "source-a"),
            _evidence("case_revision.source_b", "source-b"),
        ],
    }
    values.update(overrides)
    return values


def _value(spec: Any, **overrides: object) -> Any:
    return _revision().CaseRevision(**_revision_kwargs(spec, **overrides))


def test_case_revision_api_is_available() -> None:
    assert REVISION_MODULE is not None, "P1-B13 CaseRevision module is not available"
    for name in ("SCHEMA_VERSION", "CaseRevision", "CaseRevisionValidationError"):
        assert getattr(REVISION_MODULE, name, None) is not None, name


@pytest.mark.parametrize(
    "field",
    [
        "case_id",
        "revision_id",
        "parent_revision_id",
        "parent_spec_digest",
        "spec",
        "evidence",
    ],
)
def test_case_revision_requires_every_constructor_field(
    field: str, synthetic_case_spec: Any
) -> None:
    values = _revision_kwargs(synthetic_case_spec)
    del values[field]
    with pytest.raises(TypeError, match=field):
        _revision().CaseRevision(**values)


@pytest.mark.parametrize(
    "bad_id",
    ["", " leading", "trailing ", "bad\x7f", "bad\u0085", "bad\ud800", 1, True, None],
    ids=[
        "empty",
        "leading-space",
        "trailing-space",
        "del",
        "c1",
        "surrogate",
        "int",
        "bool",
        "none",
    ],
)
@pytest.mark.parametrize("field", ["case_id", "revision_id"])
def test_case_revision_rejects_invalid_revision_identifiers(
    field: str, bad_id: object, synthetic_case_spec: Any
) -> None:
    with pytest.raises(_revision().CaseRevisionValidationError, match=field):
        _value(synthetic_case_spec, **{field: bad_id})


@pytest.mark.parametrize(
    "bad_id",
    ["", " leading", "trailing ", "bad\x7f", "bad\u0085", "bad\ud800", 1, True],
    ids=["empty", "leading-space", "trailing-space", "del", "c1", "surrogate", "int", "bool"],
)
def test_case_revision_rejects_invalid_parent_revision_identifier(
    bad_id: object, synthetic_case_spec: Any
) -> None:
    with pytest.raises(_revision().CaseRevisionValidationError, match="parent_revision_id"):
        _value(
            synthetic_case_spec,
            parent_revision_id=bad_id,
            parent_spec_digest="a" * 64,
        )


def test_case_revision_accepts_ordinary_unicode_identifiers(synthetic_case_spec: Any) -> None:
    revision = _value(
        synthetic_case_spec,
        case_id="ケース α",
        revision_id="版-é",
    )
    assert revision.case_id == "ケース α"
    assert revision.revision_id == "版-é"


@pytest.mark.parametrize(
    "bad_digest",
    [None, "", "a" * 63, "a" * 65, "A" * 64, "g" * 64, 1, True],
    ids=["none", "empty", "short", "long", "uppercase", "nonhex", "int", "bool"],
)
def test_case_revision_rejects_invalid_parent_spec_digest(
    bad_digest: object, synthetic_case_spec: Any
) -> None:
    with pytest.raises(_revision().CaseRevisionValidationError, match="parent_spec_digest"):
        _value(
            synthetic_case_spec,
            parent_revision_id="revision_parent",
            parent_spec_digest=bad_digest,
        )


def test_case_revision_requires_paired_parent_fields(synthetic_case_spec: Any) -> None:
    with pytest.raises(_revision().CaseRevisionValidationError, match="parent"):
        _value(synthetic_case_spec, parent_revision_id="revision_parent")
    with pytest.raises(_revision().CaseRevisionValidationError, match="parent"):
        _value(synthetic_case_spec, parent_spec_digest="a" * 64)


def test_case_revision_rejects_self_parent(synthetic_case_spec: Any) -> None:
    with pytest.raises(_revision().CaseRevisionValidationError, match="parent_revision_id"):
        _value(
            synthetic_case_spec,
            parent_revision_id="revision_001",
            parent_spec_digest="a" * 64,
        )


@pytest.mark.parametrize("bad_spec", [None, {}, "spec", True, 1])
def test_case_revision_rejects_wrong_spec_type(bad_spec: object, synthetic_case_spec: Any) -> None:
    with pytest.raises(_revision().CaseRevisionValidationError, match="spec"):
        _value(synthetic_case_spec, spec=bad_spec)


@pytest.mark.parametrize(
    "bad_evidence",
    [None, "evidence", b"evidence", {}, set(), frozenset(), []],
    ids=["none", "string", "bytes", "mapping", "set", "frozenset", "empty"],
)
def test_case_revision_rejects_wrong_or_empty_evidence_collection(
    bad_evidence: object, synthetic_case_spec: Any
) -> None:
    with pytest.raises(_revision().CaseRevisionValidationError, match="evidence"):
        _value(synthetic_case_spec, evidence=bad_evidence)


@pytest.mark.parametrize("bad_item", [None, {}, "evidence", 1, True])
def test_case_revision_rejects_wrong_evidence_item(
    bad_item: object, synthetic_case_spec: Any
) -> None:
    with pytest.raises(_revision().CaseRevisionValidationError, match="evidence"):
        _value(synthetic_case_spec, evidence=[bad_item])


def test_case_revision_accepts_distinct_sources_for_one_target(synthetic_case_spec: Any) -> None:
    first = _evidence("same.target", "source-a", reference="Source:A")
    second = _evidence("same.target", "source-b", reference="Source:B")
    revision = _value(synthetic_case_spec, evidence=[first, second])
    assert len(revision.evidence) == 2


def test_case_revision_rejects_duplicate_complete_evidence(synthetic_case_spec: Any) -> None:
    first = _evidence("same.target", "source-a", reference="Source:A")
    with pytest.raises(_revision().CaseRevisionValidationError, match="evidence"):
        _value(synthetic_case_spec, evidence=[first, first])


def test_case_revision_evidence_order_is_canonical_and_semantically_irrelevant(
    synthetic_case_spec: Any,
) -> None:
    first = _evidence("same.target", "source-a", reference="Source:A")
    second = _evidence("same.target", "source-b", reference="Source:B")
    forward = _value(synthetic_case_spec, evidence=[first, second])
    reverse = _value(synthetic_case_spec, evidence=[second, first])
    assert forward.evidence == reverse.evidence
    assert forward.content_bytes() == reverse.content_bytes()
    assert forward.spec_digest == reverse.spec_digest


def test_case_revision_rejects_surrogate_evidence_at_canonical_boundary(
    synthetic_case_spec: Any,
) -> None:
    bad = EvidenceRef(
        schema_version="1",
        source_kind="registered_document",
        reference="bad\ud800",
        target_field="case_revision.source",
        content_digest="a" * 64,
    )
    with pytest.raises(_revision().CaseRevisionValidationError, match="evidence"):
        _value(synthetic_case_spec, evidence=[bad])


def test_case_revision_content_projection_and_hash_match_shared_canonical_bytes(
    synthetic_case_spec: Any,
) -> None:
    revision = _value(synthetic_case_spec)
    content = json.loads(revision.content_bytes().decode("utf-8"))
    assert content == {
        "schema_version": "1",
        "spec": json.loads(synthetic_case_spec.to_bytes().decode("utf-8")),
        "evidence": [item.to_dict() for item in revision.evidence],
    }
    assert revision.content_bytes() == canonical_bytes(content)
    assert revision.spec_digest == hashlib.sha256(revision.content_bytes()).hexdigest()


def test_case_revision_content_excludes_record_ids_parent_linkage_and_self_hash(
    synthetic_case_spec: Any,
) -> None:
    first = _value(synthetic_case_spec)
    second = _value(
        synthetic_case_spec,
        case_id="case_beta",
        revision_id="revision_002",
        parent_revision_id="revision_parent",
        parent_spec_digest="c" * 64,
    )
    assert first.content_bytes() == second.content_bytes()
    assert first.spec_digest == second.spec_digest
    assert first.to_bytes() != second.to_bytes()
    content = json.loads(first.content_bytes().decode("utf-8"))
    assert set(content) == {"schema_version", "spec", "evidence"}
    assert b"case_alpha" not in first.content_bytes()
    assert b"revision_001" not in first.content_bytes()
    assert b"spec_digest" not in first.content_bytes()


def test_case_revision_record_contains_paired_parent_fields_and_computed_digest(
    synthetic_case_spec: Any,
) -> None:
    revision = _value(
        synthetic_case_spec,
        parent_revision_id="revision_parent",
        parent_spec_digest="a" * 64,
    )
    projection = revision.to_dict()
    assert projection["schema_version"] == "1"
    assert projection["case_id"] == "case_alpha"
    assert projection["revision_id"] == "revision_001"
    assert projection["parent_revision_id"] == "revision_parent"
    assert projection["parent_spec_digest"] == "a" * 64
    assert projection["spec_digest"] == revision.spec_digest


def test_case_revision_changed_evidence_changes_content_digest(synthetic_case_spec: Any) -> None:
    first = _value(synthetic_case_spec)
    changed = _evidence("case_revision.source_a", "different-source")
    second = _value(synthetic_case_spec, evidence=[changed, first.evidence[1]])
    assert first.content_bytes() != second.content_bytes()
    assert first.spec_digest != second.spec_digest


def test_case_revision_changed_nested_spec_changes_content_digest(synthetic_case_spec: Any) -> None:
    first = _value(synthetic_case_spec)
    changed_spec = replace(
        synthetic_case_spec,
        budget=replace(synthetic_case_spec.budget, max_attempts=3),
    )
    second = _value(changed_spec)
    assert first.content_bytes() != second.content_bytes()
    assert first.spec_digest != second.spec_digest


def test_case_revision_si_and_child_semantic_set_equivalence_retain_digest(
    synthetic_case_spec: Any,
) -> None:
    motion = replace(
        synthetic_case_spec.motion,
        samples=[
            replace(synthetic_case_spec.motion.samples[0], time=Quantity(1000, "ms")),
            replace(synthetic_case_spec.motion.samples[1], time=Quantity(2000, "ms")),
        ],
    )
    evaluations = [
        replace(
            item,
            state_times=[Quantity(1000, "ms"), Quantity(2000, "ms")],
        )
        for item in reversed(synthetic_case_spec.outputs.evaluations)
    ]
    outputs = replace(
        synthetic_case_spec.outputs,
        requests=list(reversed(synthetic_case_spec.outputs.requests)),
        saved_times=[Quantity(1000, "ms"), Quantity(2000, "ms")],
        evaluations=evaluations,
    )
    quality = replace(
        synthetic_case_spec.quality_policy,
        criteria=[
            replace(
                synthetic_case_spec.quality_policy.criteria[0],
                evaluation_ids=list(
                    reversed(synthetic_case_spec.quality_policy.criteria[0].evaluation_ids)
                ),
            )
        ],
    )
    equivalent_spec = replace(
        synthetic_case_spec,
        motion=motion,
        outputs=outputs,
        quality_policy=quality,
    )
    first = _value(synthetic_case_spec)
    second = _value(equivalent_spec)
    assert first.content_bytes() == second.content_bytes()
    assert first.spec_digest == second.spec_digest


def test_case_revision_is_frozen_and_stores_evidence_as_immutable_content(
    synthetic_case_spec: Any,
) -> None:
    revision = _value(synthetic_case_spec)
    with pytest.raises(FrozenInstanceError):
        revision.case_id = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        revision.evidence = ()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        revision.spec_digest = "a" * 64  # type: ignore[misc]
    assert isinstance(revision.evidence, tuple)


def test_case_revision_detaches_caller_sequence_and_projection(
    synthetic_case_spec: Any,
) -> None:
    declared = [
        _evidence("case_revision.source_a", "source-a"),
        _evidence("case_revision.source_b", "source-b"),
    ]
    revision = _value(synthetic_case_spec, evidence=declared)
    before = revision.to_bytes()
    declared.clear()

    projection = revision.to_dict()
    projection["evidence"] = []
    projection["spec"] = {}
    projection["spec_digest"] = "changed"

    assert revision.to_bytes() == before
    assert len(revision.evidence) == 2
    assert revision.to_dict()["spec_digest"] == revision.spec_digest


def test_case_revision_accepts_equal_parent_and_current_content_digest(
    synthetic_case_spec: Any,
) -> None:
    initial = _value(synthetic_case_spec)
    child = _value(
        synthetic_case_spec,
        revision_id="revision_002",
        parent_revision_id="revision_001",
        parent_spec_digest=initial.spec_digest,
    )
    assert child.spec_digest == initial.spec_digest
