# mypy: disable-error-code="no-redef,assignment,misc"

from __future__ import annotations

import json
from typing import Any

import pytest
from workflow_fixtures import case_revision, evidence

from febio_cae.domain import (
    CaseDraft,
    CasePatch,
    CasePatchEdit,
    CaseRevision,
    FileEntry,
    IssuedQuestion,
    OperationStatus,
    PartialCaseSpec,
    PreviewRequest,
)

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


def test_codec_rejects_unknown_nested_contact_keys(synthetic_case_spec: Any) -> None:
    _require_codec()
    revision = case_revision(synthetic_case_spec)
    payload = json.loads(encode_record(revision))
    payload["spec"]["contact"]["friction"]["extra_physical_condition"] = "reject"
    with pytest.raises(CodecError, match="unknown or missing"):
        decode_record(json.dumps(payload), CaseRevision)
    payload = json.loads(encode_record(revision))
    payload["spec"]["contact"]["arrangement"]["extra_physical_condition"] = "reject"
    with pytest.raises(CodecError, match="unknown or missing"):
        decode_record(json.dumps(payload), CaseRevision)


def test_codec_rejects_nonnull_value_on_absent_patch_edit() -> None:
    _require_codec()
    patch = CasePatch(
        "revision-interface",
        "a" * 64,
        (CasePatchEdit("geometry", None, False),),
        (evidence("case_patch.evidence", "patch"),),
    )
    payload = json.loads(encode_record(patch))
    payload["edits"][0]["value"] = {"unexpected": "value"}
    with pytest.raises(CodecError, match="absent|value"):
        decode_record(json.dumps(payload), CasePatch)


def test_codec_round_trips_all_declared_service_and_leaf_top_level_records() -> None:
    _require_codec()
    records = (
        IssuedQuestion(
            "question-interface",
            "case-interface",
            "draft-interface",
            2,
            ("geometry",),
            (evidence("question.geometry", "question"),),
        ),
        OperationStatus("READY"),
        FileEntry("output/case.xplt", "a" * 64, 4, "xplt"),
        PreviewRequest("preview-interface", "manifest-interface", (0, 1), ("displacement",)),
    )
    for record in records:
        restored = decode_record(encode_record(record), type(record))
        assert restored.to_bytes() == record.to_bytes()
