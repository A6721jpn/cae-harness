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


@pytest.mark.parametrize("field", ["unit", "value_type", "state_count"])
def test_observation_metadata_must_match_numeric_record(tmp_path: Path, field: str) -> None:
    case = controlled_case(tmp_path)
    case.manifest = replace(
        case.manifest,
        read_result=replace(
            case.manifest.read_result,
            observations=tuple(
                (
                    replace(item, unit="N")
                    if field == "unit"
                    else replace(item, value_type="FLOAT")
                    if field == "value_type"
                    else replace(item, state_count=99)
                )
                if item.output_id == "displacement"
                else item
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
    case = quality_case(tmp_path, output_request_id=request_id, limit=Quantity(expected + 1, unit))
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


def test_nonzero_required_time_cannot_underflow_to_initial_state(tmp_path: Path) -> None:
    case = controlled_case(tmp_path)
    evaluation = case.revision.spec.outputs.evaluations[0]
    times = (Quantity(1e-50, "s"),)
    case.revision = replace(
        case.revision,
        spec=replace(
            case.revision.spec,
            outputs=replace(
                case.revision.spec.outputs,
                saved_times=times,
                evaluations=(replace(evaluation, state_times=times),),
            ),
        ),
    )
    assert assess(case).overall_status is AssessmentStatus.UNVERIFIED


def test_nearby_but_distinct_state_is_not_the_required_endpoint(tmp_path: Path) -> None:
    case = controlled_case(tmp_path)
    case.replace_numeric(replace(case.numeric(), axis_values=(0.0, 1.0 - 1e-13)))
    assert assess(case).overall_status is AssessmentStatus.UNVERIFIED


def test_mesh_geometry_provenance_must_match_the_supplied_revision(tmp_path: Path) -> None:
    case = quality_case(tmp_path)
    assert (
        case.mesh.provenance.source_geometry_digest == case.revision.spec.geometry.geometry_digest
    )
    assert assess(case).overall_status is AssessmentStatus.PASS
    foreign_digest = "f" * 64
    assert foreign_digest != case.revision.spec.geometry.geometry_digest
    case.mesh = replace(
        case.mesh,
        provenance=replace(
            case.mesh.provenance,
            source_geometry_digest=foreign_digest,
        ),
    )
    result = assess(case)
    assert result.overall_status is AssessmentStatus.UNVERIFIED
    assert not result.criteria[0].measured
    assert "geometry" in result.criteria[0].reason


@pytest.mark.parametrize(
    "forces,residual,status",
    [
        ((-2.0, -2.0, -2.0), 0.0, AssessmentStatus.PASS),
        ((2.0, 2.0, 2.0), 4.0, AssessmentStatus.FAIL),
        ((-2.0, -1.0, -3.0), 1.0, AssessmentStatus.FAIL),
    ],
)
def test_signed_force_sum_preserves_sign_and_each_state(
    tmp_path: Path, forces: tuple[float, ...], residual: float, status: AssessmentStatus
) -> None:
    from .quality_fixture import signed_force_case

    result = assess(signed_force_case(tmp_path, forces))
    assert result.overall_status is status
    assert result.criteria[0].status is status
    assert result.criteria[0].measured[0].value == pytest.approx(residual)
    assert result.criteria[0].measured[0].unit == "N"


@pytest.mark.parametrize(
    "defect",
    [
        "missing-state",
        "subset",
        "foreign",
        "alias-overlap",
        "component",
        "dimension",
        "negative-limit",
    ],
)
def test_signed_force_sum_invalid_evidence_is_unverified(tmp_path: Path, defect: str) -> None:
    from .fixtures import evidence
    from .quality_fixture import signed_force_case

    case = signed_force_case(tmp_path, (-2.0, -2.0, -2.0))
    outputs = case.revision.spec.outputs
    first, second = outputs.evaluations
    if defect == "missing-state":
        numeric = case.numeric("contact_force")
        case.replace_numeric(replace(numeric, axis_values=(0.0, 0.4, 1.0)))
    elif defect == "foreign":
        numeric = case.numeric("contact_force")
        case.replace_numeric(
            replace(numeric, reference=replace(numeric.reference, attempt_id="other"))
        )
    elif defect == "subset":
        outputs = replace(
            outputs, evaluations=(first, replace(second, state_times=(Quantity(1, "s"),)))
        )
    elif defect == "alias-overlap":
        original = outputs.requests[0]
        alias = replace(
            original,
            request_id="alias",
            evidence=evidence("outputs.requests.alias", "explicit-alias"),
        )
        outputs = replace(
            outputs,
            requests=(*outputs.requests, alias),
            evaluations=(
                first,
                replace(second, output_request_id="alias", selection=first.selection),
            ),
        )
    elif defect == "component":
        outputs = replace(
            outputs,
            requests=tuple(
                replace(r, component_id="x") if r.request_id == "request_tool" else r
                for r in outputs.requests
            ),
        )
    elif defect == "dimension":
        stress = next(r for r in outputs.requests if r.request_id == "request_stress")
        outputs = replace(
            outputs,
            evaluations=(
                replace(first, output_request_id=stress.request_id, selection=stress.selection),
                second,
            ),
        )
    else:
        policy = case.revision.spec.quality_policy
        case.revision = replace(
            case.revision,
            spec=replace(
                case.revision.spec,
                quality_policy=replace(
                    policy,
                    criteria=(
                        replace(
                            policy.criteria[0],
                            thresholds=(QualityThreshold("max_value", Quantity(-1, "N")),),
                        ),
                    ),
                ),
            ),
        )
    case.revision = replace(case.revision, spec=replace(case.revision.spec, outputs=outputs))
    result = assess(case)
    assert result.overall_status is AssessmentStatus.UNVERIFIED
    assert result.criteria[0].status is AssessmentStatus.UNVERIFIED
    assert not result.criteria[0].measured
