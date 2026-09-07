# mypy: disable-error-code="no-redef,assignment,misc"

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, cast

import pytest
from workflow_fixtures import case_revision, evidence

from febio_cae.domain import (
    CaseDraft,
    CasePatch,
    CasePatchEdit,
    CaseRevision,
    FileEntry,
    FrameId,
    IssuedQuestion,
    NumericResultData,
    OperationStatus,
    OutputMapping,
    PartialCaseSpec,
    PreviewRequest,
    Quantity,
    ResultDataRef,
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
        assert cast(Any, restored).to_bytes() == record.to_bytes()


def _numeric_data() -> NumericResultData:
    seed = NumericResultData(
        reference=ResultDataRef(
            "numeric-data",
            "0" * 64,
            "numeric-result-v1",
            "output/case.xplt",
            "b" * 64,
            "attempt-interface",
        ),
        mapping=OutputMapping(
            "displacement",
            "displacement",
            "node",
            "VEC3F",
            "m",
            FrameId("World"),
            -1,
            1,
            "value",
        ),
        axis_id="time",
        axis_unit="s",
        axis_values=(0.0, 1.0),
        entity_ids=("node-1", "node-2"),
        component_ids=("x", "y", "z"),
        values=((0.0, 0.0, 0.0, 1.0, 1.0, 1.0), (0.2, 0.0, 0.0, 1.2, 0.0, 0.0)),
    )
    reference = ResultDataRef(
        "numeric-data",
        seed.expected_content_digest,
        "numeric-result-v1",
        "output/case.xplt",
        "b" * 64,
        "attempt-interface",
    )
    return NumericResultData(
        reference,
        seed.mapping,
        seed.axis_id,
        seed.axis_unit,
        seed.axis_values,
        seed.entity_ids,
        seed.component_ids,
        seed.values,
    )


def test_numeric_result_and_reference_have_registered_strict_codecs() -> None:
    _require_codec()
    numeric = _numeric_data()
    reference = numeric.reference
    restored_reference = decode_record(encode_record(reference), ResultDataRef)
    restored_numeric = decode_record(encode_record(numeric), NumericResultData)
    assert restored_reference.to_bytes() == reference.to_bytes()
    assert restored_numeric.to_bytes() == numeric.to_bytes()
    assert restored_numeric.reference.bundle_digest == "b" * 64
    assert restored_numeric.reference.attempt_id == "attempt-interface"
    assert restored_numeric.mapping.raw_sign == -1
    assert restored_numeric.values == numeric.values
    numeric.verify_content_digest()


def test_numeric_codec_rejects_wrong_schema_codec_digest_shape_and_nonfinite() -> None:
    _require_codec()
    numeric = _numeric_data()
    encoded = json.loads(encode_record(numeric))
    bad_schema = dict(encoded)
    bad_schema["schema_version"] = "2"
    with pytest.raises(CodecError, match="schema"):
        decode_record(json.dumps(bad_schema), NumericResultData)
    bad_codec = json.loads(json.dumps(encoded))
    bad_codec["reference"]["codec_id"] = "other-v1"
    with pytest.raises(CodecError, match="codec"):
        decode_record(json.dumps(bad_codec), NumericResultData)
    bad_digest = json.loads(json.dumps(encoded))
    bad_digest["content_digest"] = "f" * 64
    with pytest.raises(CodecError, match="digest"):
        decode_record(json.dumps(bad_digest), NumericResultData)
    bad_shape = json.loads(json.dumps(encoded))
    bad_shape["values"][0].pop()
    with pytest.raises(CodecError, match="width|mapping|numeric"):
        decode_record(json.dumps(bad_shape), NumericResultData)
    bad_number = json.loads(json.dumps(encoded))
    bad_number["values"][0][0] = float("nan")
    with pytest.raises(CodecError, match="finite|NaN"):
        decode_record(json.dumps(bad_number), NumericResultData)


def test_single_state_numeric_codec_round_trips_for_complete_single_state_output_policy(
    synthetic_case_spec: Any,
) -> None:
    _require_codec()
    single_state = Quantity(2, "s")
    outputs = replace(
        synthetic_case_spec.outputs,
        saved_times=(single_state,),
        evaluations=tuple(
            replace(evaluation, state_times=(single_state,))
            for evaluation in synthetic_case_spec.outputs.evaluations
        ),
    )
    complete_case = replace(synthetic_case_spec, outputs=outputs)
    assert complete_case.outputs.saved_times == (single_state,)
    assert all(
        evaluation.state_times == (single_state,)
        for evaluation in complete_case.outputs.evaluations
    )

    seed = NumericResultData(
        reference=ResultDataRef(
            "single-state-data",
            "0" * 64,
            "numeric-result-v1",
            "output/case.xplt",
            "b" * 64,
            "attempt-interface",
        ),
        mapping=_numeric_data().mapping,
        axis_id="time",
        axis_unit="s",
        axis_values=(2.0,),
        entity_ids=("node-1", "node-2"),
        component_ids=("x", "y", "z"),
        values=((0.2, 0.0, 0.0, 1.2, 0.0, 0.0),),
    )
    numeric = replace(
        seed,
        reference=ResultDataRef(
            "single-state-data",
            seed.expected_content_digest,
            "numeric-result-v1",
            "output/case.xplt",
            "b" * 64,
            "attempt-interface",
        ),
    )
    restored = decode_record(encode_record(numeric), NumericResultData)
    assert restored.to_bytes() == numeric.to_bytes()
    assert restored.axis_values == (2.0,)
    assert restored.values == ((0.2, 0.0, 0.0, 1.2, 0.0, 0.0),)
    restored.verify_content_digest()


def test_manifest_codec_reuses_strong_result_reference_path_rule() -> None:
    _require_codec()
    from febio_cae.domain.results import (
        OutputObservation,
        ReadResult,
        ReadStatus,
        ResultManifest,
    )

    manifest = ResultManifest(
        "manifest-interface",
        "attempt-interface",
        "b" * 64,
        (FileEntry("output/case.xplt", "a" * 64, 1, "xplt"),),
        ReadResult(
            ReadStatus.VALIDATED,
            _reader := __import__("febio_cae.domain", fromlist=["ToolIdentity"]).ToolIdentity(
                "reader", "1", "c" * 64
            ),
            (
                OutputObservation(
                    "displacement",
                    "node",
                    "VEC3F",
                    "m",
                    FrameId("World"),
                    "value",
                    2,
                    ResultDataRef(
                        "numeric-data",
                        "d" * 64,
                        "numeric-result-v1",
                        "output/case.xplt",
                        "b" * 64,
                        "attempt-interface",
                    ),
                ),
            ),
            (),
        ),
    )
    payload = json.loads(encode_record(manifest))
    payload["read_result"]["observations"][0]["data_ref"]["logical_path"] = "output/NUL.xplt"
    with pytest.raises(CodecError, match="logical_path|ambiguous|reserved"):
        decode_record(json.dumps(payload), type(manifest))
