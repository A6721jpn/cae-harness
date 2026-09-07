from __future__ import annotations

import importlib
from dataclasses import FrozenInstanceError
from types import ModuleType
from typing import Any

import pytest

from febio_cae.domain import EvidenceRef, NumericalProfileRef, Quantity, canonical_bytes


def _optional_module(name: str) -> ModuleType | None:
    try:
        return importlib.import_module(name)
    except (ImportError, ModuleNotFoundError):
        return None


OUTPUT_MODULE = _optional_module("febio_cae.domain.output_policy")
SELECTION_MODULE = _optional_module("febio_cae.domain.selection")
SPATIAL_MODULE = _optional_module("febio_cae.domain.spatial")


def _output() -> ModuleType:
    if OUTPUT_MODULE is None:
        pytest.skip("output policy API availability is covered by the dedicated assertion")
    return OUTPUT_MODULE


def _selection() -> ModuleType:
    if SELECTION_MODULE is None or SPATIAL_MODULE is None:
        pytest.skip("selection dependencies are covered by the dedicated assertion")
    return SELECTION_MODULE


def _spatial() -> ModuleType:
    if SPATIAL_MODULE is None:
        pytest.skip("spatial dependency is covered by the dedicated assertion")
    return SPATIAL_MODULE


def _evidence(
    seed: str = "a",
    *,
    target_field: str,
    reference: str = "SyntheticOutputSource:1",
) -> EvidenceRef:
    return EvidenceRef(
        schema_version="1",
        source_kind="registered_document",
        reference=reference,
        target_field=target_field,
        content_digest=seed * 64,
    )


def _context() -> tuple[Any, Any, Any, str]:
    spatial = _spatial()
    return (
        spatial.GeometryId("geometry-v1"),
        spatial.BodyId("body-A"),
        spatial.FrameId("Frame-World"),
        "b" * 64,
    )


def _selection_ref(
    *,
    geometry_digest: str | None = None,
    body_id: Any | None = None,
    frame: Any | None = None,
    name: str = "output-region",
    rule: Any | None = None,
    resolution: Any | None = None,
) -> Any:
    selection = _selection()
    geometry, body, default_frame, digest = _context()
    chosen_geometry = digest if geometry_digest is None else geometry_digest
    chosen_body = body if body_id is None else body_id
    chosen_frame = default_frame if frame is None else frame
    chosen_rule = selection.NamedAttributeRule("synthetic-output-region") if rule is None else rule
    assert geometry.value == "geometry-v1"
    return selection.SelectionRef(
        name=name,
        role="output_region",
        role_evidence=_evidence(target_field="selection.role"),
        geometry_digest=chosen_geometry,
        body_id=chosen_body,
        frame=chosen_frame,
        rule=chosen_rule,
        resolution=resolution,
    )


def _face_selection(face_names: list[str], *, with_resolution: bool = False) -> Any:
    selection = _selection()
    spatial = _spatial()
    _, body, frame, digest = _context()
    face_ids = [spatial.FaceId(name) for name in face_names]
    rule = selection.FaceSetRule(
        geometry_digest=digest,
        body_id=body,
        frame=frame,
        face_ids=face_ids,
        provenance=_evidence("c", target_field="selection.face_set"),
    )
    resolution = None
    if with_resolution:
        resolution = selection.ResolutionSnapshot(
            geometry_digest=digest,
            body_id=body,
            frame=frame,
            faces=[
                selection.FaceMeasurement(
                    face_id=spatial.FaceId(name),
                    area=Quantity(1, "mm2"),
                    centroid=spatial.Point3(
                        frame,
                        Quantity(0, "mm"),
                        Quantity(0, "mm"),
                        Quantity(0, "mm"),
                    ),
                )
                for name in face_names
            ],
        )
    return _selection_ref(rule=rule, resolution=resolution)


def _profile() -> NumericalProfileRef:
    return NumericalProfileRef(
        profile_id="synthetic-output-profile",
        purpose="outputs",
        record_digest="d" * 64,
    )


def _request_kwargs(
    *,
    request_id: str = "displacement_top",
    quantity_id: str = "displacement",
    measure_id: str = "component",
    component_id: str = "z",
    location: str = "node",
    selection: Any | None = None,
    frame: Any | None = None,
    display_unit: str = "mm",
    evidence: EvidenceRef | None = None,
) -> dict[str, object]:
    _, _, default_frame, _ = _context()
    return {
        "request_id": request_id,
        "quantity_id": quantity_id,
        "measure_id": measure_id,
        "component_id": component_id,
        "location": location,
        "selection": _selection_ref() if selection is None else selection,
        "frame": default_frame if frame is None else frame,
        "display_unit": display_unit,
        "evidence": (
            _evidence(target_field=f"outputs.requests.{request_id}")
            if evidence is None
            else evidence
        ),
    }


def _request(**kwargs: object) -> Any:
    return _output().OutputRequest(**_request_kwargs(**kwargs))


def _evaluation_kwargs(
    *,
    evaluation_id: str = "peak_displacement",
    output_request_id: str = "displacement_top",
    aggregation_id: str = "maximum",
    selection: Any | None = None,
    state_times: object = (Quantity(0, "s"), Quantity(1, "s")),
    evidence: EvidenceRef | None = None,
) -> dict[str, object]:
    return {
        "evaluation_id": evaluation_id,
        "output_request_id": output_request_id,
        "aggregation_id": aggregation_id,
        "selection": _selection_ref() if selection is None else selection,
        "state_times": state_times,
        "evidence": (
            _evidence(target_field=f"outputs.evaluations.{evaluation_id}")
            if evidence is None
            else evidence
        ),
    }


def _evaluation(**kwargs: object) -> Any:
    return _output().EvaluationRequest(**_evaluation_kwargs(**kwargs))


def _policy(
    *,
    profile: object = None,
    requests: object = None,
    saved_times: object = None,
    evaluations: object = None,
) -> Any:
    return _output().OutputPolicy(
        profile=_profile() if profile is None else profile,
        requests=[_request()] if requests is None else requests,
        saved_times=[Quantity(0, "s"), Quantity(1, "s")] if saved_times is None else saved_times,
        evaluations=() if evaluations is None else evaluations,
    )


def test_output_policy_api_is_available() -> None:
    assert OUTPUT_MODULE is not None, "P1-B10 output policy module is not available"
    assert SELECTION_MODULE is not None, "P1-B1 selection module is not available"
    assert SPATIAL_MODULE is not None, "P1-B1 spatial module is not available"
    for name in (
        "SCHEMA_VERSION",
        "OutputRequest",
        "EvaluationRequest",
        "OutputPolicy",
        "OutputPolicyValidationError",
    ):
        assert getattr(OUTPUT_MODULE, name, None) is not None, name


def test_output_request_retains_explicit_semantic_fields_and_canonical_projection() -> None:
    request = _request(
        request_id="velocity_A",
        quantity_id="velocity",
        measure_id="magnitude",
        component_id="scalar",
        location="integration_point",
        display_unit="MPa",
    )

    assert request.to_dict() == {
        "schema_version": "1",
        "request_id": "velocity_A",
        "quantity_id": "velocity",
        "measure_id": "magnitude",
        "component_id": "scalar",
        "location": "integration_point",
        "selection": request.selection.to_dict(),
        "frame": "Frame-World",
        "display_unit": "MPa",
        "evidence": {
            "schema_version": "1",
            "source_kind": "registered_document",
            "reference": "SyntheticOutputSource:1",
            "target_field": "outputs.requests.velocity_A",
            "content_digest": "a" * 64,
        },
    }
    assert request.to_bytes() == canonical_bytes(request.to_dict())


@pytest.mark.parametrize(
    "field",
    ["request_id", "quantity_id", "measure_id", "component_id"],
)
@pytest.mark.parametrize("value", ["", "9starts_with_digit", "has-hyphen", "has space", "é"])
def test_output_request_rejects_non_ascii_or_invalid_identifiers(field: str, value: str) -> None:
    evidence = _evidence(target_field="outputs.requests.valid_request")
    with pytest.raises(_output().OutputPolicyValidationError, match=field):
        _request(**{field: value, "evidence": evidence})


def test_output_request_rejects_missing_and_extra_fields() -> None:
    output = _output()
    values = _request_kwargs()
    missing = dict(values)
    del missing["display_unit"]
    with pytest.raises(TypeError):
        output.OutputRequest(**missing)
    with pytest.raises(TypeError):
        output.OutputRequest(**values, unexpected=True)


@pytest.mark.parametrize(
    "location",
    ["node", "element", "integration_point", "face", "surface", "rigid_body"],
)
def test_output_request_accepts_only_explicit_supported_locations(location: str) -> None:
    assert _request(location=location).location == location


@pytest.mark.parametrize("location", ["", "nodal", "gauss_point", 1, True, None])
def test_output_request_rejects_unknown_or_non_text_locations(location: object) -> None:
    with pytest.raises(_output().OutputPolicyValidationError, match="location"):
        _request(location=location)


def test_output_request_requires_known_display_unit_and_retains_spelling() -> None:
    assert _request(display_unit="MPa").display_unit == "MPa"
    assert _request(display_unit="Pa").display_unit == "Pa"
    with pytest.raises(_output().OutputPolicyValidationError, match="display_unit"):
        _request(display_unit="kPa")
    with pytest.raises(_output().OutputPolicyValidationError, match="display_unit"):
        _request(display_unit=object())


def test_output_request_requires_typed_selection_and_frame() -> None:
    with pytest.raises(_output().OutputPolicyValidationError, match="selection"):
        _request(selection=object())
    with pytest.raises(_output().OutputPolicyValidationError, match="frame"):
        _request(frame=object())


def test_output_request_requires_exact_evidence_target_built_outside_raises() -> None:
    wrong_target = _evidence(target_field="outputs.requests.other_request")
    with pytest.raises(_output().OutputPolicyValidationError, match="evidence"):
        _request(evidence=wrong_target)

    valid_target = _evidence(target_field="outputs.requests.displacement_top")
    assert _request(evidence=valid_target).evidence is valid_target


def test_output_request_retains_a_different_explicit_result_frame() -> None:
    spatial = _spatial()
    selection_frame = spatial.FrameId("Selection-Frame")
    result_frame = spatial.FrameId("Result-Frame")
    request = _request(selection=_selection_ref(frame=selection_frame), frame=result_frame)

    assert request.selection.frame == selection_frame
    assert request.frame == result_frame
    assert request.to_dict()["frame"] == "Result-Frame"


def test_output_request_distinguishes_display_unit_presentation_intent() -> None:
    pascal = _request(display_unit="Pa")
    megapascal = _request(display_unit="MPa")

    assert pascal.to_bytes() != megapascal.to_bytes()


def test_output_request_canonicalizes_nested_face_rule_order() -> None:
    first = _request(selection=_face_selection(["face-A", "face-B"]))
    second = _request(selection=_face_selection(["face-B", "face-A"]))

    assert first.to_bytes() == second.to_bytes()


def test_output_request_canonicalizes_nested_resolution_order() -> None:
    first = _request(selection=_face_selection(["face-A", "face-B"], with_resolution=True))
    second = _request(selection=_face_selection(["face-B", "face-A"], with_resolution=True))

    assert first.to_bytes() == second.to_bytes()


@pytest.mark.parametrize(
    "nested",
    [
        "evidence",
        "selection",
        "frame",
    ],
)
def test_output_request_rejects_surrogate_nested_values_at_canonical_boundary(
    nested: str,
) -> None:
    spatial = _spatial()
    if nested == "evidence":
        # EvidenceRef intentionally validates syntax here; UTF-8 canonicalization belongs to
        # the owning output value and must reject the valid-but-unencodable fixture.
        evidence = _evidence(
            target_field="outputs.requests.displacement_top", reference="surrogate-\ud800"
        )
        values = {"evidence": evidence}
    elif nested == "selection":
        values = {"selection": _selection_ref(name="surrogate-\ud801")}
    else:
        values = {"frame": spatial.FrameId("surrogate-\ud802")}

    with pytest.raises(_output().OutputPolicyValidationError, match="canonical"):
        _request(**values)


def test_output_request_is_immutable_and_projection_isolated() -> None:
    request = _request()
    payload = request.to_dict()
    payload["request_id"] = "changed"
    payload["selection"]["rule"]["kind"] = "changed"

    with pytest.raises(FrozenInstanceError):
        request.request_id = "changed"
    assert request.request_id == "displacement_top"
    assert request.to_dict()["selection"]["rule"]["kind"] == "named_attribute"


def test_evaluation_request_retains_explicit_scope_and_canonical_time_projection() -> None:
    evaluation = _evaluation(
        evaluation_id="peak_A",
        output_request_id="displacement_top",
        aggregation_id="mean_value",
        state_times=[Quantity(0, "s"), Quantity(1000, "ms")],
    )

    assert evaluation.state_times == (Quantity(0, "s"), Quantity(1000, "ms"))
    assert evaluation.to_dict()["state_times"] == [
        {"value": 0.0, "unit": "s"},
        {"value": 1.0, "unit": "s"},
    ]
    assert (
        evaluation.to_bytes()
        == _evaluation(
            evaluation_id="peak_A",
            output_request_id="displacement_top",
            aggregation_id="mean_value",
            state_times=[Quantity(0, "s"), Quantity(1, "s")],
        ).to_bytes()
    )


def test_evaluation_request_requires_exact_evidence_target_built_outside_raises() -> None:
    wrong_target = _evidence(target_field="outputs.evaluations.other_evaluation")
    with pytest.raises(_output().OutputPolicyValidationError, match="evidence"):
        _evaluation(evidence=wrong_target)


@pytest.mark.parametrize("field", ["evaluation_id", "output_request_id", "aggregation_id"])
@pytest.mark.parametrize("value", ["", "9bad", "bad-id", "bad id", "é"])
def test_evaluation_request_rejects_non_ascii_or_invalid_identifiers(
    field: str, value: str
) -> None:
    evidence = _evidence(target_field="outputs.evaluations.valid_evaluation")
    with pytest.raises(_output().OutputPolicyValidationError, match=field):
        _evaluation(**{field: value, "evidence": evidence})


def test_evaluation_request_rejects_missing_and_extra_fields() -> None:
    output = _output()
    values = _evaluation_kwargs()
    missing = dict(values)
    del missing["state_times"]
    with pytest.raises(TypeError):
        output.EvaluationRequest(**missing)
    with pytest.raises(TypeError):
        output.EvaluationRequest(**values, unexpected=True)


@pytest.mark.parametrize(
    "state_times",
    [
        (),
        [Quantity(-1, "s"), Quantity(1, "s")],
        [Quantity(0, "s"), Quantity(0, "s")],
        [Quantity(1, "s"), Quantity(0, "s")],
        [Quantity(1, "s"), Quantity(1000, "ms")],
        [Quantity(0, "m"), Quantity(1, "s")],
        [Quantity(0, "s"), Quantity(10**309, "s")],
        [Quantity(0, "s"), Quantity(1e-321, "ms")],
    ],
)
def test_evaluation_request_rejects_invalid_state_times(state_times: object) -> None:
    with pytest.raises(_output().OutputPolicyValidationError, match="state_times"):
        _evaluation(state_times=state_times)


def test_evaluation_request_rejects_non_sequence_or_invalid_time_members() -> None:
    with pytest.raises(_output().OutputPolicyValidationError, match="state_times"):
        _evaluation(state_times={Quantity(0, "s"), Quantity(1, "s")})
    with pytest.raises(_output().OutputPolicyValidationError, match="state_times"):
        _evaluation(state_times=[Quantity(0, "s"), object()])
    with pytest.raises(_output().OutputPolicyValidationError, match="state_times"):
        _evaluation(state_times="0 s")


def test_evaluation_request_is_immutable_and_copies_state_times() -> None:
    state_times = [Quantity(0, "s"), Quantity(1, "s")]
    evaluation = _evaluation(state_times=state_times)
    state_times.clear()
    payload = evaluation.to_dict()
    payload["state_times"].clear()

    assert len(evaluation.state_times) == 2
    with pytest.raises(FrozenInstanceError):
        evaluation.evaluation_id = "changed"


def test_output_policy_api_accepts_valid_profile_requests_and_empty_evaluations() -> None:
    policy = _policy()

    assert policy.profile.purpose == "outputs"
    assert [request.request_id for request in policy.requests] == ["displacement_top"]
    assert policy.evaluations == ()
    assert policy.to_bytes() == canonical_bytes(policy.to_dict())


def test_output_policy_requires_outputs_profile() -> None:
    mesh_profile = NumericalProfileRef(
        profile_id="synthetic-mesh-profile",
        purpose="mesh_quality",
        record_digest="e" * 64,
    )
    with pytest.raises(_output().OutputPolicyValidationError, match="outputs"):
        _policy(profile=mesh_profile)
    with pytest.raises(_output().OutputPolicyValidationError, match="profile"):
        _policy(profile=object())


def test_output_policy_requires_nonempty_requests_and_explicit_sequence_types() -> None:
    with pytest.raises(_output().OutputPolicyValidationError, match="requests"):
        _policy(requests=[])
    with pytest.raises(_output().OutputPolicyValidationError, match="requests"):
        _policy(requests={"request": _request()})
    with pytest.raises(_output().OutputPolicyValidationError, match="requests"):
        _policy(requests={"invalid"})
    with pytest.raises(_output().OutputPolicyValidationError, match="requests"):
        _policy(requests=[object()])
    with pytest.raises(_output().OutputPolicyValidationError, match="requests"):
        _policy(requests="request")


def test_output_policy_rejects_invalid_saved_time_collections() -> None:
    with pytest.raises(_output().OutputPolicyValidationError, match="saved_times"):
        _policy(saved_times=[])
    with pytest.raises(_output().OutputPolicyValidationError, match="saved_times"):
        _policy(saved_times={Quantity(0, "s"), Quantity(1, "s")})
    with pytest.raises(_output().OutputPolicyValidationError, match="saved_times"):
        _policy(saved_times="0 s")
    with pytest.raises(_output().OutputPolicyValidationError, match="saved_times"):
        _policy(saved_times=[Quantity(0, "s"), object()])


def test_output_policy_rejects_invalid_evaluation_collections_but_accepts_empty_sequence() -> None:
    assert _policy(evaluations=()).evaluations == ()
    with pytest.raises(_output().OutputPolicyValidationError, match="evaluations"):
        _policy(evaluations={"invalid"})
    with pytest.raises(_output().OutputPolicyValidationError, match="evaluations"):
        _policy(evaluations={"evaluation": _evaluation()})
    with pytest.raises(_output().OutputPolicyValidationError, match="evaluations"):
        _policy(evaluations=[object()])
    with pytest.raises(_output().OutputPolicyValidationError, match="evaluations"):
        _policy(evaluations="evaluation")


def test_output_policy_canonicalizes_semantic_set_permutations_by_id() -> None:
    first_request = _request(request_id="alpha")
    second_request = _request(request_id="beta", display_unit="MPa")
    first_evaluation = _evaluation(evaluation_id="alpha_eval", output_request_id="alpha")
    second_evaluation = _evaluation(evaluation_id="beta_eval", output_request_id="beta")

    first = _policy(
        requests=[second_request, first_request],
        evaluations=[second_evaluation, first_evaluation],
    )
    second = _policy(
        requests=[first_request, second_request],
        evaluations=[first_evaluation, second_evaluation],
    )

    assert [request.request_id for request in first.requests] == ["alpha", "beta"]
    assert [evaluation.evaluation_id for evaluation in first.evaluations] == [
        "alpha_eval",
        "beta_eval",
    ]
    assert first.to_bytes() == second.to_bytes()


def test_output_policy_rejects_duplicate_request_ids_even_for_equal_content() -> None:
    first = _request(request_id="same")
    second = _request(request_id="same")
    with pytest.raises(_output().OutputPolicyValidationError, match="duplicate request_id"):
        _policy(requests=[first, second])


def test_output_policy_rejects_duplicate_evaluation_ids_even_for_equal_content() -> None:
    first = _evaluation(evaluation_id="same")
    second = _evaluation(evaluation_id="same")
    with pytest.raises(_output().OutputPolicyValidationError, match="duplicate evaluation_id"):
        _policy(evaluations=[first, second])


def test_output_policy_rejects_missing_output_request_reference() -> None:
    evaluation = _evaluation(output_request_id="missing_request")
    with pytest.raises(_output().OutputPolicyValidationError, match="output_request_id"):
        _policy(evaluations=[evaluation])


@pytest.mark.parametrize("difference", ["geometry", "body"])
def test_output_policy_rejects_evaluation_selection_context_mismatch(difference: str) -> None:
    spatial = _spatial()
    if difference == "geometry":
        selection = _selection_ref(geometry_digest="c" * 64)
    else:
        selection = _selection_ref(body_id=spatial.BodyId("body-B"))
    evaluation = _evaluation(selection=selection)

    with pytest.raises(_output().OutputPolicyValidationError, match="selection"):
        _policy(evaluations=[evaluation])


def test_output_policy_accepts_same_geometry_body_with_explicit_subset_and_different_frame() -> (
    None
):
    spatial = _spatial()
    request_selection = _selection_ref(frame=spatial.FrameId("Request-Frame"))
    evaluation_selection = _selection_ref(
        frame=spatial.FrameId("Evaluation-Frame"),
        name="explicit-subset",
        rule=_selection().NamedAttributeRule("subset-region"),
    )
    request = _request(selection=request_selection, frame=spatial.FrameId("Result-Frame"))
    evaluation = _evaluation(selection=evaluation_selection)
    policy = _policy(requests=[request], evaluations=[evaluation])

    assert policy.evaluations[0].selection.frame.value == "Evaluation-Frame"
    assert policy.requests[0].frame.value == "Result-Frame"
    assert policy.evaluations[0].selection.rule.attribute == "subset-region"


def test_output_policy_rejects_evaluation_time_missing_from_saved_states() -> None:
    evaluation = _evaluation(state_times=[Quantity(0, "s"), Quantity(2, "s")])
    with pytest.raises(_output().OutputPolicyValidationError, match="saved_times"):
        _policy(evaluations=[evaluation])


def test_output_policy_accepts_equivalent_time_units_and_preserves_display_intent() -> None:
    request = _request(display_unit="MPa")
    evaluation = _evaluation(state_times=[Quantity(0, "s"), Quantity(1000, "ms")])
    policy = _policy(
        requests=[request],
        saved_times=[Quantity(0, "s"), Quantity(1000, "ms")],
        evaluations=[evaluation],
    )
    equivalent = _policy(
        requests=[_request(display_unit="MPa")],
        saved_times=[Quantity(0, "s"), Quantity(1, "s")],
        evaluations=[_evaluation(state_times=[Quantity(0, "s"), Quantity(1, "s")])],
    )

    assert policy.to_bytes() == equivalent.to_bytes()
    assert policy.requests[0].display_unit == "MPa"


@pytest.mark.parametrize(
    "saved_times",
    [
        [Quantity(-1, "s"), Quantity(1, "s")],
        [Quantity(0, "s"), Quantity(0, "s")],
        [Quantity(1, "s"), Quantity(0, "s")],
        [Quantity(0, "m"), Quantity(1, "s")],
        [Quantity(0, "s"), Quantity(10**309, "s")],
        [Quantity(0, "s"), Quantity(1e-321, "ms")],
    ],
)
def test_output_policy_rejects_invalid_saved_time_values(saved_times: object) -> None:
    with pytest.raises(_output().OutputPolicyValidationError, match="saved_times"):
        _policy(saved_times=saved_times)


def test_output_policy_copies_collections_and_isolates_returned_projection() -> None:
    requests = [_request()]
    saved_times = [Quantity(0, "s"), Quantity(1, "s")]
    evaluations = [_evaluation()]
    policy = _policy(requests=requests, saved_times=saved_times, evaluations=evaluations)
    requests.clear()
    saved_times.clear()
    evaluations.clear()
    payload = policy.to_dict()
    payload["requests"].clear()
    payload["saved_times"].clear()
    payload["evaluations"].clear()

    assert len(policy.requests) == 1
    assert len(policy.saved_times) == 2
    assert len(policy.evaluations) == 1
    with pytest.raises(FrozenInstanceError):
        policy.profile = _profile()


def test_output_policy_rejects_surrogate_nested_values_at_policy_canonical_boundary() -> None:
    request = _request()
    object.__setattr__(request.selection, "name", "surrogate-\ud803")

    with pytest.raises(_output().OutputPolicyValidationError, match="canonical"):
        _policy(requests=[request])
