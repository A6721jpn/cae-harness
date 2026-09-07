from __future__ import annotations

import hashlib
import importlib
from dataclasses import FrozenInstanceError
from types import ModuleType
from typing import Any

import pytest

from febio_cae.domain import EvidenceRef, canonical_bytes


def _optional_module(name: str) -> ModuleType | None:
    try:
        return importlib.import_module(name)
    except (ImportError, ModuleNotFoundError):
        return None


DRAFT_MODULE = _optional_module("febio_cae.domain.case_draft")
PARTIAL_MODULE = _optional_module("febio_cae.domain.partial_case_spec")
FIELD_NAMES = (
    "geometry",
    "material",
    "support",
    "rigid_tool",
    "motion",
    "contact",
    "mesh_policy",
    "solver_policy",
    "outputs",
    "quality_policy",
    "budget",
)


def _draft_module() -> ModuleType:
    if DRAFT_MODULE is None:
        pytest.skip("CaseDraft API availability is covered by the dedicated assertion")
    return DRAFT_MODULE


def _partial_module() -> ModuleType:
    if PARTIAL_MODULE is None:
        pytest.skip("PartialCaseSpec API availability is covered by the dedicated assertion")
    return PARTIAL_MODULE


def _evidence(
    target_field: str,
    seed: str,
    *,
    reference: str = "SyntheticCaseDraftSource:1",
) -> EvidenceRef:
    return EvidenceRef(
        schema_version="1",
        source_kind="registered_document",
        reference=reference,
        target_field=target_field,
        content_digest=hashlib.sha256(seed.encode("utf-8")).hexdigest(),
    )


def _values(case_spec: Any, **overrides: object) -> Any:
    values = {field: getattr(case_spec, field) for field in FIELD_NAMES}
    values.update(overrides)
    return _partial_module().PartialCaseSpec(**values)


def _draft_kwargs(partial: Any, **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "case_id": "case_alpha",
        "draft_id": "draft_001",
        "generation": 0,
        "input_intent": "",
        "parent_revision_id": None,
        "parent_spec_digest": None,
        "values": partial,
        "evidence": [],
    }
    values.update(overrides)
    return values


def _draft(partial: Any, **overrides: object) -> Any:
    return _draft_module().CaseDraft(**_draft_kwargs(partial, **overrides))


def test_case_draft_api_is_available() -> None:
    assert DRAFT_MODULE is not None, "P1-B14 CaseDraft module is not available"
    for name in ("SCHEMA_VERSION", "CaseDraft", "CaseDraftValidationError"):
        assert getattr(DRAFT_MODULE, name, None) is not None, name


def test_case_draft_initial_snapshot_allows_empty_intent_and_evidence() -> None:
    draft = _draft_module().CaseDraft(
        case_id="case_alpha",
        draft_id="draft_001",
        generation=0,
        input_intent="",
        parent_revision_id=None,
        parent_spec_digest=None,
        values=_partial_module().PartialCaseSpec(),
        evidence=[],
    )
    assert draft.evidence == ()
    assert draft.unresolved_fields == tuple(sorted(FIELD_NAMES))
    projection = draft.to_dict()
    assert set(projection) == {
        "schema_version",
        "case_id",
        "draft_id",
        "generation",
        "input_intent",
        "parent_revision_id",
        "parent_spec_digest",
        "values",
        "evidence",
        "unresolved_fields",
    }
    assert "spec_digest" not in projection
    assert "ready" not in projection
    assert draft.to_bytes() == canonical_bytes(projection)


def test_case_draft_full_values_convert_to_existing_case_spec(synthetic_case_spec: Any) -> None:
    draft = _draft(_values(synthetic_case_spec), evidence=[_evidence("draft.source", "a")])
    assert draft.unresolved_fields == ()
    assert draft.values.to_case_spec() == synthetic_case_spec


@pytest.mark.parametrize(
    "field",
    [
        "case_id",
        "draft_id",
        "generation",
        "input_intent",
        "parent_revision_id",
        "parent_spec_digest",
        "values",
        "evidence",
    ],
)
def test_case_draft_requires_all_constructor_fields(field: str) -> None:
    values = _draft_kwargs(_partial_module().PartialCaseSpec())
    del values[field]
    with pytest.raises(TypeError, match=field):
        _draft_module().CaseDraft(**values)


@pytest.mark.parametrize("field", ["case_id", "draft_id", "parent_revision_id"])
@pytest.mark.parametrize(
    "bad_id",
    ["", " leading", "trailing ", "bad\x7f", "bad\u0085", "bad\ud800", 1, True],
    ids=["empty", "leading-space", "trailing-space", "del", "c1", "surrogate", "int", "bool"],
)
def test_case_draft_rejects_invalid_opaque_identifiers(
    field: str, bad_id: object, synthetic_case_spec: Any
) -> None:
    overrides: dict[str, object] = {field: bad_id}
    if field == "parent_revision_id":
        overrides["parent_spec_digest"] = "a" * 64
    with pytest.raises(_draft_module().CaseDraftValidationError, match=field):
        _draft(_values(synthetic_case_spec), **overrides)


def test_case_draft_preserves_unicode_and_case_sensitive_identifiers(
    synthetic_case_spec: Any,
) -> None:
    draft = _draft(
        _values(synthetic_case_spec),
        case_id="ケース α",
        draft_id="Draft-A",
    )
    other = _draft(
        _values(synthetic_case_spec),
        case_id="ケース α",
        draft_id="draft-a",
    )
    assert draft.case_id == "ケース α"
    assert draft.draft_id == "Draft-A"
    assert draft.draft_id != other.draft_id


@pytest.mark.parametrize("bad_intent", [None, 1, b"intent", "bad\ud800"])
def test_case_draft_requires_utf8_text_but_allows_natural_language_whitespace(
    bad_intent: object, synthetic_case_spec: Any
) -> None:
    with pytest.raises(_draft_module().CaseDraftValidationError, match="input_intent"):
        _draft(_values(synthetic_case_spec), input_intent=bad_intent)


def test_case_draft_preserves_natural_language_intent_exactly(synthetic_case_spec: Any) -> None:
    intent = "  押込み条件を確認\n次の行\t"
    draft = _draft(_values(synthetic_case_spec), input_intent=intent)
    assert draft.input_intent == intent


def test_case_draft_requires_paired_parent_fields_but_allows_same_namespace_text(
    synthetic_case_spec: Any,
) -> None:
    with pytest.raises(_draft_module().CaseDraftValidationError, match="parent"):
        _draft(_values(synthetic_case_spec), parent_revision_id="draft_001")
    with pytest.raises(_draft_module().CaseDraftValidationError, match="parent"):
        _draft(_values(synthetic_case_spec), parent_spec_digest="a" * 64)
    draft = _draft(
        _values(synthetic_case_spec),
        parent_revision_id="draft_001",
        parent_spec_digest="a" * 64,
    )
    assert draft.parent_revision_id == draft.draft_id


@pytest.mark.parametrize(
    "bad_digest",
    ["", "a" * 63, "a" * 65, "A" * 64, "g" * 64, 1, True],
    ids=["empty", "short", "long", "uppercase", "nonhex", "int", "bool"],
)
def test_case_draft_rejects_invalid_parent_digest(
    bad_digest: object, synthetic_case_spec: Any
) -> None:
    with pytest.raises(_draft_module().CaseDraftValidationError, match="parent_spec_digest"):
        _draft(
            _values(synthetic_case_spec),
            parent_revision_id="revision_parent",
            parent_spec_digest=bad_digest,
        )


@pytest.mark.parametrize("bad_generation", [True, -1, 1.0, "1", None])
def test_case_draft_rejects_invalid_generation(
    bad_generation: object, synthetic_case_spec: Any
) -> None:
    with pytest.raises(_draft_module().CaseDraftValidationError, match="generation"):
        _draft(_values(synthetic_case_spec), generation=bad_generation)


def test_case_draft_generation_canonical_error_retains_generation_context(
    synthetic_case_spec: Any,
) -> None:
    with pytest.raises(_draft_module().CaseDraftValidationError, match="generation"):
        _draft(_values(synthetic_case_spec), generation=10**5000)


@pytest.mark.parametrize("bad_values", [None, {}, "values", True, 1])
def test_case_draft_rejects_wrong_values_type(bad_values: object) -> None:
    with pytest.raises(_draft_module().CaseDraftValidationError, match="values"):
        _draft_module().CaseDraft(**_draft_kwargs(bad_values))


@pytest.mark.parametrize(
    "bad_evidence",
    ["evidence", b"evidence", bytearray(b"evidence"), {}, set(), frozenset()],
    ids=["string", "bytes", "bytearray", "mapping", "set", "frozenset"],
)
def test_case_draft_rejects_wrong_evidence_collection(
    bad_evidence: object, synthetic_case_spec: Any
) -> None:
    with pytest.raises(_draft_module().CaseDraftValidationError, match="evidence"):
        _draft(_values(synthetic_case_spec), evidence=bad_evidence)


@pytest.mark.parametrize("bad_item", [None, {}, "evidence", 1, True])
def test_case_draft_rejects_wrong_evidence_item(bad_item: object, synthetic_case_spec: Any) -> None:
    with pytest.raises(_draft_module().CaseDraftValidationError, match="evidence"):
        _draft(_values(synthetic_case_spec), evidence=[bad_item])


def test_case_draft_rejects_duplicate_complete_evidence(synthetic_case_spec: Any) -> None:
    item = _evidence("draft.source", "a")
    with pytest.raises(_draft_module().CaseDraftValidationError, match="evidence"):
        _draft(_values(synthetic_case_spec), evidence=[item, item])


def test_case_draft_accepts_distinct_evidence_declarations_for_one_target(
    synthetic_case_spec: Any,
) -> None:
    first = _evidence("same.target", "a", reference="Source:A")
    second = _evidence("same.target", "b", reference="Source:B")
    draft = _draft(_values(synthetic_case_spec), evidence=[first, second])
    assert len(draft.evidence) == 2


def test_case_draft_evidence_order_is_canonical_and_semantically_irrelevant(
    synthetic_case_spec: Any,
) -> None:
    first = _evidence("same.target", "a", reference="Source:A")
    second = _evidence("same.target", "b", reference="Source:B")
    forward = _draft(_values(synthetic_case_spec), evidence=[first, second])
    reverse = _draft(_values(synthetic_case_spec), evidence=[second, first])
    assert forward.evidence == reverse.evidence
    assert forward.to_bytes() == reverse.to_bytes()


def test_case_draft_detaches_caller_evidence_and_returned_projection(
    synthetic_case_spec: Any,
) -> None:
    declared = [_evidence("draft.source", "a"), _evidence("draft.other", "b")]
    draft = _draft(_values(synthetic_case_spec), evidence=declared)
    before = draft.to_bytes()
    declared.clear()
    projection = draft.to_dict()
    projection["evidence"] = []
    projection["values"] = {}
    projection["unresolved_fields"] = ["forged"]
    assert draft.to_bytes() == before
    assert len(draft.evidence) == 2
    assert draft.unresolved_fields == ()


def test_case_draft_is_frozen_and_exposes_values_unresolved_fields(
    synthetic_case_spec: Any,
) -> None:
    draft = _draft(_values(synthetic_case_spec))
    with pytest.raises(FrozenInstanceError):
        draft.case_id = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        draft.evidence = ()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        draft.unresolved_fields = ()  # type: ignore[misc]
    assert draft.unresolved_fields == draft.values.unresolved_fields
