# mypy: disable-error-code="no-redef,assignment,misc"

from __future__ import annotations

from typing import Any

import pytest
from workflow_fixtures import case_revision

from febio_cae.domain import CaseDraft, CaseRevision, PartialCaseSpec

try:
    from febio_cae.domain.codec import CodecError, decode_record, encode_record
except ImportError:
    CodecError: Any = None
    decode_record: Any = None
    encode_record: Any = None


def _require_codec() -> None:
    if CodecError is None:
        pytest.skip("P1 strict contract codec is not implemented")


def test_contract_codec_api_is_available() -> None:
    assert CodecError is not None, "P1 strict contract codec is not available"
    assert callable(decode_record)
    assert callable(encode_record)


def test_minimal_initial_draft_round_trips() -> None:
    _require_codec()
    draft = CaseDraft(
        case_id="case-interface",
        draft_id="draft-initial",
        generation=0,
        input_intent="",
        parent_revision_id=None,
        parent_spec_digest=None,
        values=PartialCaseSpec(),
        evidence=(),
    )
    restored = decode_record(encode_record(draft), CaseDraft)
    assert restored.to_bytes() == draft.to_bytes()
    assert restored.values.unresolved_fields == draft.values.unresolved_fields


def test_complete_case_revision_round_trips(synthetic_case_spec: Any) -> None:
    _require_codec()
    revision = case_revision(synthetic_case_spec)
    restored = decode_record(encode_record(revision), CaseRevision)
    assert restored.to_bytes() == revision.to_bytes()
    assert restored.spec.to_bytes() == revision.spec.to_bytes()


def test_codec_rejects_unknown_keys_duplicate_keys_and_nonfinite_numbers() -> None:
    _require_codec()
    with pytest.raises(CodecError, match="unknown|missing"):
        decode_record(b'{"schema_version":"1","unknown":1}', PartialCaseSpec)
    with pytest.raises(CodecError, match="duplicate"):
        decode_record(b'{"schema_version":"1","schema_version":"1"}', PartialCaseSpec)
    with pytest.raises(CodecError, match="finite|NaN|infinity"):
        decode_record(b'{"schema_version":"1","geometry":NaN}', PartialCaseSpec)


def test_codec_rejects_arbitrary_object_dispatch_and_wrong_schema() -> None:
    _require_codec()
    with pytest.raises(CodecError, match="supported|record"):
        decode_record(b'{"schema_version":"1"}', "builtins.object")
    with pytest.raises(CodecError, match="schema"):
        decode_record(b'{"schema_version":"2"}', PartialCaseSpec)


def test_codec_encode_rejects_mappings() -> None:
    _require_codec()
    with pytest.raises(CodecError, match="record|supported"):
        encode_record({"schema_version": "1"})
