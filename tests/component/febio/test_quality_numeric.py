"""Semantic quality decisions, not native FEBio or physical qualification."""

from dataclasses import replace
from pathlib import Path

import pytest

from febio_cae.adapters.febio.quality import QualityAdapter
from febio_cae.domain import AssessmentStatus, QualityAssessment, QualityThreshold, Quantity

from .quality_fixture import QualityCase, quality_case


def assess(case: QualityCase) -> QualityAssessment:
    return QualityAdapter().assess(
        case.manifest, case.revision, case.mesh, case.profile, case.store
    )


def controlled_case(tmp_path: Path) -> QualityCase:
    case = quality_case(tmp_path)
    numeric = case.numeric()
    case.replace_numeric(
        replace(
            numeric,
            values=tuple(
                tuple(
                    value
                    for entity in numeric.entity_ids
                    for value in (3.0, 0.0, 0.2 if entity in {"1", "2"} else 2.0)
                )
                for _ in numeric.axis_values
            ),
        )
    )
    return case


def test_reader_codec_consumer_respects_component_and_roi(tmp_path: Path) -> None:
    case = quality_case(tmp_path)
    result = assess(case)
    assert result.overall_status is AssessmentStatus.PASS
    assert result.criteria[0].measured[0].value == pytest.approx(0.02)
    case = controlled_case(tmp_path / "controlled")
    result = assess(case)
    assert result.overall_status is AssessmentStatus.PASS
    assert result.criteria[0].measured[0].value == pytest.approx(0.2)


def test_above_declared_threshold_fails(tmp_path: Path) -> None:
    case = controlled_case(tmp_path)
    criterion = case.revision.spec.quality_policy.criteria[0]
    case.revision = replace(
        case.revision,
        spec=replace(
            case.revision.spec,
            quality_policy=replace(
                case.revision.spec.quality_policy,
                criteria=(
                    replace(
                        criterion, thresholds=(QualityThreshold("max_value", Quantity(0.1, "m")),)
                    ),
                ),
            ),
        ),
    )
    assert assess(case).overall_status is AssessmentStatus.FAIL


def test_equivalent_numeric_units_are_compared_in_si(tmp_path: Path) -> None:
    case = controlled_case(tmp_path)
    numeric = case.numeric()
    case.replace_numeric(
        replace(
            numeric,
            mapping=replace(numeric.mapping, unit="mm"),
            values=tuple(tuple(value * 1000 for value in row) for row in numeric.values),
        )
    )
    result = assess(case)
    assert result.overall_status is AssessmentStatus.PASS
    assert result.criteria[0].measured[0].value == pytest.approx(0.2)
    assert result.criteria[0].measured[0].unit == "m"


def test_partial_roi_coverage_cannot_pass(tmp_path: Path) -> None:
    case = controlled_case(tmp_path)
    numeric = case.numeric()
    case.replace_numeric(
        replace(numeric, entity_ids=("1",), values=tuple(row[:3] for row in numeric.values))
    )
    assert assess(case).overall_status is AssessmentStatus.UNVERIFIED


def test_different_hashed_data_with_same_bundle_attempt_cannot_replace_observation(
    tmp_path: Path,
) -> None:
    case = controlled_case(tmp_path)
    numeric = case.numeric()
    case.replace_numeric(
        replace(numeric, reference=replace(numeric.reference, data_id="foreign-data")), bind=False
    )
    assert assess(case).overall_status is AssessmentStatus.UNVERIFIED


@pytest.mark.parametrize(
    "field,value", [("unit", "N"), ("value_type", "FLOAT"), ("state_count", 99)]
)
def test_observation_metadata_must_match_numeric_record(
    tmp_path: Path, field: str, value: object
) -> None:
    case = controlled_case(tmp_path)
    case.manifest = replace(
        case.manifest,
        read_result=replace(
            case.manifest.read_result,
            observations=tuple(
                replace(item, **{field: value}) if item.output_id == "displacement" else item
                for item in case.manifest.read_result.observations
            ),
        ),
    )
    assert assess(case).overall_status is AssessmentStatus.UNVERIFIED


def test_threshold_dimension_mismatch_cannot_pass(tmp_path: Path) -> None:
    case = controlled_case(tmp_path)
    criterion = case.revision.spec.quality_policy.criteria[0]
    case.revision = replace(
        case.revision,
        spec=replace(
            case.revision.spec,
            quality_policy=replace(
                case.revision.spec.quality_policy,
                criteria=(
                    replace(
                        criterion, thresholds=(QualityThreshold("max_value", Quantity(1, "N")),)
                    ),
                ),
            ),
        ),
    )
    assert assess(case).overall_status is AssessmentStatus.UNVERIFIED


def test_axis_unit_conversion_and_native_float32_saved_time(tmp_path: Path) -> None:
    case = quality_case(tmp_path, times=(0.0, 0.35, 1.0))
    assert assess(case).overall_status is AssessmentStatus.PASS
    numeric = case.numeric()
    case.replace_numeric(
        replace(numeric, axis_unit="ms", axis_values=tuple(t * 1000 for t in numeric.axis_values))
    )
    assert assess(case).overall_status is AssessmentStatus.PASS


@pytest.mark.parametrize(
    "defect", ["axis-kind", "missing-state", "saved-endpoint", "foreign-bundle", "aggregation"]
)
def test_unavailable_or_unrepresented_scope_is_unverified(tmp_path: Path, defect: str) -> None:
    case = controlled_case(tmp_path)
    numeric = case.numeric()
    if defect == "axis-kind":
        case.replace_numeric(replace(numeric, axis_id="load_factor"))
    elif defect in {"missing-state", "saved-endpoint"}:
        case.replace_numeric(replace(numeric, axis_values=(0.0, 0.5)))
        if defect == "saved-endpoint":
            evaluation = case.revision.spec.outputs.evaluations[0]
            case.revision = replace(
                case.revision,
                spec=replace(
                    case.revision.spec,
                    outputs=replace(
                        case.revision.spec.outputs,
                        evaluations=(replace(evaluation, state_times=(Quantity(0, "s"),)),),
                    ),
                ),
            )
    elif defect == "foreign-bundle":
        case.replace_numeric(
            replace(numeric, reference=replace(numeric.reference, bundle_digest="f" * 64))
        )
    else:
        evaluation = case.revision.spec.outputs.evaluations[0]
        case.revision = replace(
            case.revision,
            spec=replace(
                case.revision.spec,
                outputs=replace(
                    case.revision.spec.outputs,
                    evaluations=(replace(evaluation, aggregation_id="mean"),),
                ),
            ),
        )
    assert assess(case).overall_status is AssessmentStatus.UNVERIFIED


@pytest.mark.parametrize(
    "request_id,unit,expected", [("request_stress", "Pa", 10.0), ("request_tool", "N", 2.0)]
)
def test_reader_element_and_rigid_body_outputs_use_matching_roi_location(
    tmp_path: Path,
    request_id: str,
    unit: str,
    expected: float,
) -> None:
    case = quality_case(tmp_path)
    request = next(
        item for item in case.revision.spec.outputs.requests if item.request_id == request_id
    )
    evaluation = case.revision.spec.outputs.evaluations[0]
    criterion = case.revision.spec.quality_policy.criteria[0]
    case.revision = replace(
        case.revision,
        spec=replace(
            case.revision.spec,
            outputs=replace(
                case.revision.spec.outputs,
                evaluations=(
                    replace(evaluation, output_request_id=request_id, selection=request.selection),
                ),
            ),
            quality_policy=replace(
                case.revision.spec.quality_policy,
                criteria=(
                    replace(
                        criterion,
                        thresholds=(QualityThreshold("max_value", Quantity(expected + 1, unit)),),
                    ),
                ),
            ),
        ),
    )
    result = assess(case)
    assert result.overall_status is AssessmentStatus.PASS
    assert result.criteria[0].measured[0].value == pytest.approx(expected)


def test_assessment_identity_includes_exact_numeric_manifest(tmp_path: Path) -> None:
    case = controlled_case(tmp_path)
    before = assess(case)
    numeric = case.numeric()
    case.replace_numeric(
        replace(numeric, values=tuple(tuple(value * 10 for value in row) for row in numeric.values))
    )
    after = assess(case)
    assert before.overall_status is AssessmentStatus.PASS
    assert after.overall_status is AssessmentStatus.FAIL
    assert before.assessment_id != after.assessment_id


def test_empty_numeric_obligation_is_unverified_not_an_exception(tmp_path: Path) -> None:
    case = controlled_case(tmp_path)
    criterion = case.revision.spec.quality_policy.criteria[0]
    case.revision = replace(
        case.revision,
        spec=replace(
            case.revision.spec,
            quality_policy=replace(
                case.revision.spec.quality_policy,
                criteria=(replace(criterion, evaluation_ids=()),),
            ),
        ),
    )
    assert assess(case).overall_status is AssessmentStatus.UNVERIFIED
