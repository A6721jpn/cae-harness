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


QUALITY_MODULE = _optional_module("febio_cae.domain.quality_policy")


def _quality() -> ModuleType:
    if QUALITY_MODULE is None:
        pytest.skip("quality policy API availability is covered by the dedicated assertion")
    return QUALITY_MODULE


def _evidence(
    seed: str = "a",
    *,
    target_field: str,
    reference: str = "SyntheticQualitySource:1",
) -> EvidenceRef:
    return EvidenceRef(
        schema_version="1",
        source_kind="registered_document",
        reference=reference,
        target_field=target_field,
        content_digest=seed * 64,
    )


def _profile(*, purpose: str = "quality") -> NumericalProfileRef:
    return NumericalProfileRef(
        profile_id="synthetic-quality-profile",
        purpose=purpose,
        record_digest="d" * 64,
    )


def _threshold_kwargs(
    *,
    parameter_id: object = "absolute_tolerance",
    value: object = Quantity(1, "MPa"),
) -> dict[str, object]:
    return {"parameter_id": parameter_id, "value": value}


def _threshold(**kwargs: object) -> Any:
    return _quality().QualityThreshold(**_threshold_kwargs(**kwargs))


def _criterion_kwargs(
    *,
    criterion_id: object = "equilibrium",
    metric_id: object = "residual_norm",
    evaluation_ids: object = ("force_balance", "displacement_limit"),
    thresholds: object = None,
    applicability_reason: object = "Required for the synthetic quality profile",
    evidence: object = None,
) -> dict[str, object]:
    return {
        "criterion_id": criterion_id,
        "metric_id": metric_id,
        "evaluation_ids": evaluation_ids,
        "thresholds": [_threshold()] if thresholds is None else thresholds,
        "applicability_reason": applicability_reason,
        "evidence": (
            _evidence(target_field=f"quality_policy.criteria.{criterion_id}")
            if evidence is None
            else evidence
        ),
    }


def _criterion(**kwargs: object) -> Any:
    return _quality().QualityCriterion(**_criterion_kwargs(**kwargs))


def _policy_kwargs(*, profile: object = None, criteria: object = None) -> dict[str, object]:
    return {
        "profile": _profile() if profile is None else profile,
        "criteria": [_criterion()] if criteria is None else criteria,
    }


def _policy(**kwargs: object) -> Any:
    return _quality().QualityPolicy(**_policy_kwargs(**kwargs))


def test_quality_policy_api_is_available() -> None:
    assert QUALITY_MODULE is not None, "P1-B11 quality policy module is not available"
    for name in (
        "SCHEMA_VERSION",
        "QualityThreshold",
        "QualityCriterion",
        "QualityPolicy",
        "QualityPolicyValidationError",
    ):
        assert getattr(QUALITY_MODULE, name, None) is not None, name


def test_quality_threshold_retains_typed_quantity_and_canonicalizes_to_si() -> None:
    threshold = _threshold(value=Quantity(1000, "ms"))

    assert isinstance(threshold.value, Quantity)
    assert threshold.value == Quantity(1000, "ms")
    assert threshold.to_dict() == {
        "schema_version": "1",
        "parameter_id": "absolute_tolerance",
        "value": {"value": 1.0, "unit": "s"},
    }
    assert threshold.to_bytes() == canonical_bytes(threshold.to_dict())


def test_quality_threshold_si_equivalent_values_have_identical_projection() -> None:
    seconds = _threshold(value=Quantity(1, "s"))
    milliseconds = _threshold(value=Quantity(1000, "ms"))

    assert seconds.value.unit == "s"
    assert milliseconds.value.unit == "ms"
    assert seconds.to_bytes() == milliseconds.to_bytes()


@pytest.mark.parametrize("value", [Quantity(-1, "MPa"), Quantity(0, "MPa")])
def test_quality_threshold_accepts_signed_and_zero_finite_values(value: Quantity) -> None:
    threshold = _threshold(value=value)

    assert threshold.value == value


@pytest.mark.parametrize(
    "parameter_id",
    ["", "1limit", "bad-id", "bad.id", "é", "with space", None, 1],
)
def test_quality_threshold_rejects_invalid_parameter_ids(parameter_id: object) -> None:
    with pytest.raises(_quality().QualityPolicyValidationError, match="parameter_id"):
        _threshold(parameter_id=parameter_id)


@pytest.mark.parametrize(
    "value",
    [object(), "1 MPa", 1, Quantity(10**309, "s"), Quantity(1e-321, "ms")],
)
def test_quality_threshold_requires_finite_si_representable_quantity(value: object) -> None:
    with pytest.raises(_quality().QualityPolicyValidationError, match="value"):
        _threshold(value=value)


def test_quality_threshold_rejects_missing_and_extra_fields() -> None:
    with pytest.raises(TypeError):
        _quality().QualityThreshold(value=Quantity(1, "MPa"))
    with pytest.raises(TypeError):
        _quality().QualityThreshold(
            parameter_id="limit",
            value=Quantity(1, "MPa"),
            unexpected=True,
        )


def test_quality_threshold_is_frozen_and_projection_is_mutation_isolated() -> None:
    threshold = _threshold()
    payload = threshold.to_dict()
    payload["value"]["value"] = 99.0  # type: ignore[index]

    assert threshold.to_dict()["value"] == {"value": 1_000_000.0, "unit": "Pa"}
    with pytest.raises(FrozenInstanceError):
        threshold.parameter_id = "changed"


def test_quality_criterion_accepts_empty_evaluations_and_thresholds() -> None:
    criterion = _criterion(evaluation_ids=(), thresholds=())

    assert criterion.evaluation_ids == ()
    assert criterion.thresholds == ()
    assert criterion.to_bytes() == canonical_bytes(criterion.to_dict())


@pytest.mark.parametrize(
    "field",
    ["criterion_id", "metric_id"],
)
@pytest.mark.parametrize("value", ["", "1metric", "bad-id", "bad.id", "é", None, 1])
def test_quality_criterion_rejects_invalid_identity_fields(field: str, value: object) -> None:
    with pytest.raises(_quality().QualityPolicyValidationError, match=field):
        _criterion(**{field: value})


@pytest.mark.parametrize("evaluation_id", ["", "1eval", "bad-id", "bad.id", "é", None, 1])
def test_quality_criterion_rejects_invalid_evaluation_ids(evaluation_id: object) -> None:
    with pytest.raises(_quality().QualityPolicyValidationError, match="evaluation_ids"):
        _criterion(evaluation_ids=[evaluation_id])


def test_quality_criterion_copies_and_sorts_evaluation_semantic_set() -> None:
    evaluation_ids = ["z_eval", "a_eval"]
    criterion = _criterion(evaluation_ids=evaluation_ids)
    evaluation_ids.clear()

    assert criterion.evaluation_ids == ("a_eval", "z_eval")


@pytest.mark.parametrize("evaluation_ids", ["evaluation", b"evaluation", {"a_eval", "b_eval"}])
def test_quality_criterion_rejects_non_sequence_evaluation_ids(evaluation_ids: object) -> None:
    with pytest.raises(_quality().QualityPolicyValidationError, match="evaluation_ids"):
        _criterion(evaluation_ids=evaluation_ids)


def test_quality_criterion_rejects_duplicate_evaluation_ids_even_for_equal_content() -> None:
    with pytest.raises(_quality().QualityPolicyValidationError, match="duplicate evaluation_id"):
        _criterion(evaluation_ids=["same", "same"])


def test_quality_criterion_copies_and_sorts_threshold_semantic_set() -> None:
    thresholds = [_threshold(parameter_id="z_limit"), _threshold(parameter_id="a_limit")]
    criterion = _criterion(thresholds=thresholds)
    thresholds.clear()

    assert [threshold.parameter_id for threshold in criterion.thresholds] == ["a_limit", "z_limit"]


@pytest.mark.parametrize("thresholds", ["threshold", b"threshold", {"a": object()}])
def test_quality_criterion_rejects_non_sequence_thresholds(thresholds: object) -> None:
    with pytest.raises(_quality().QualityPolicyValidationError, match="thresholds"):
        _criterion(thresholds=thresholds)


def test_quality_criterion_rejects_invalid_threshold_members_and_duplicate_ids() -> None:
    with pytest.raises(_quality().QualityPolicyValidationError, match="thresholds"):
        _criterion(thresholds=[object()])
    with pytest.raises(_quality().QualityPolicyValidationError, match="duplicate parameter_id"):
        _criterion(
            thresholds=[
                _threshold(parameter_id="same"),
                _threshold(parameter_id="same", value=Quantity(2, "MPa")),
            ]
        )


@pytest.mark.parametrize(
    "reason",
    ["", " leading", "trailing ", "line\nbreak", "tab\tbreak"],
)
def test_quality_criterion_requires_explicit_clean_applicability_reason(reason: str) -> None:
    with pytest.raises(_quality().QualityPolicyValidationError, match="applicability_reason"):
        _criterion(applicability_reason=reason)


def test_quality_criterion_requires_exact_dynamic_evidence_target() -> None:
    unrelated = _evidence(target_field="quality_policy.criteria.other_criterion")

    with pytest.raises(_quality().QualityPolicyValidationError, match="quality_policy.criteria"):
        _criterion(criterion_id="equilibrium", evidence=unrelated)
    with pytest.raises(_quality().QualityPolicyValidationError, match="evidence"):
        _criterion(evidence=object())


def test_quality_criterion_rejects_missing_and_extra_fields() -> None:
    with pytest.raises(TypeError):
        _quality().QualityCriterion(
            criterion_id="criterion",
            metric_id="metric",
            evaluation_ids=(),
            thresholds=(),
            applicability_reason="reason",
        )
    with pytest.raises(TypeError):
        _quality().QualityCriterion(
            **_criterion_kwargs(),
            unexpected=True,
        )


def test_quality_criterion_semantic_set_permutations_have_identical_canonical_bytes() -> None:
    first = _criterion(
        evaluation_ids=["z_eval", "a_eval"],
        thresholds=[_threshold(parameter_id="z_limit"), _threshold(parameter_id="a_limit")],
    )
    second = _criterion(
        evaluation_ids=["a_eval", "z_eval"],
        thresholds=[_threshold(parameter_id="a_limit"), _threshold(parameter_id="z_limit")],
    )

    assert first.to_bytes() == second.to_bytes()


def test_quality_criterion_rejects_surrogate_nested_evidence_and_reason_at_canonical_boundary() -> (
    None
):
    evidence = _evidence(target_field="quality_policy.criteria.equilibrium")
    object.__setattr__(evidence, "reference", "surrogate-\ud803")
    with pytest.raises(_quality().QualityPolicyValidationError, match="canonical"):
        _criterion(evidence=evidence)

    with pytest.raises(_quality().QualityPolicyValidationError, match="canonical"):
        _criterion(applicability_reason="surrogate-\ud803")


def test_quality_criterion_is_frozen_and_returned_projection_is_mutation_isolated() -> None:
    criterion = _criterion()
    payload = criterion.to_dict()
    payload["evaluation_ids"].clear()  # type: ignore[union-attr]
    payload["thresholds"].clear()  # type: ignore[union-attr]

    assert criterion.evaluation_ids == ("displacement_limit", "force_balance")
    assert len(criterion.thresholds) == 1
    with pytest.raises(FrozenInstanceError):
        criterion.metric_id = "changed"


def test_quality_policy_accepts_quality_profile_and_canonical_projection() -> None:
    policy = _policy()

    assert policy.profile.purpose == "quality"
    assert [criterion.criterion_id for criterion in policy.criteria] == ["equilibrium"]
    assert policy.to_bytes() == canonical_bytes(policy.to_dict())


def test_quality_policy_requires_quality_profile_purpose() -> None:
    with pytest.raises(_quality().QualityPolicyValidationError, match="quality"):
        _policy(profile=_profile(purpose="outputs"))
    with pytest.raises(_quality().QualityPolicyValidationError, match="profile"):
        _policy(profile=object())


def test_quality_policy_requires_nonempty_criteria_and_explicit_sequence_types() -> None:
    with pytest.raises(_quality().QualityPolicyValidationError, match="criteria"):
        _policy(criteria=[])
    for invalid in ("criterion", b"criterion", {"criterion": _criterion()}, [object()]):
        with pytest.raises(_quality().QualityPolicyValidationError, match="criteria"):
            _policy(criteria=invalid)


def test_quality_policy_sorts_criteria_and_rejects_duplicate_ids() -> None:
    alpha = _criterion(
        criterion_id="alpha", evidence=_evidence(target_field="quality_policy.criteria.alpha")
    )
    beta = _criterion(
        criterion_id="beta", evidence=_evidence(target_field="quality_policy.criteria.beta")
    )
    policy = _policy(criteria=[beta, alpha])

    assert [criterion.criterion_id for criterion in policy.criteria] == ["alpha", "beta"]
    with pytest.raises(_quality().QualityPolicyValidationError, match="duplicate criterion_id"):
        _policy(
            criteria=[
                alpha,
                _criterion(
                    criterion_id="alpha",
                    evidence=_evidence(target_field="quality_policy.criteria.alpha"),
                ),
            ]
        )


def test_quality_policy_criterion_permutations_have_identical_canonical_bytes() -> None:
    alpha = _criterion(
        criterion_id="alpha", evidence=_evidence(target_field="quality_policy.criteria.alpha")
    )
    beta = _criterion(
        criterion_id="beta", evidence=_evidence(target_field="quality_policy.criteria.beta")
    )

    first = _policy(criteria=[beta, alpha])
    second = _policy(criteria=[alpha, beta])

    assert first.to_bytes() == second.to_bytes()


def test_quality_policy_rejects_missing_and_extra_fields() -> None:
    with pytest.raises(TypeError):
        _quality().QualityPolicy(criteria=[_criterion()])
    with pytest.raises(TypeError):
        _quality().QualityPolicy(**_policy_kwargs(), unexpected=True)


def test_quality_policy_rejects_surrogate_nested_profile_at_canonical_boundary() -> None:
    profile = _profile()
    object.__setattr__(profile, "profile_id", "surrogate-\ud803")

    with pytest.raises(_quality().QualityPolicyValidationError, match="canonical"):
        _policy(profile=profile)


def test_quality_policy_copies_criteria_and_isolates_returned_projection() -> None:
    criteria = [_criterion()]
    policy = _policy(criteria=criteria)
    criteria.clear()
    payload = policy.to_dict()
    payload["criteria"].clear()  # type: ignore[union-attr]

    assert len(policy.criteria) == 1
    with pytest.raises(FrozenInstanceError):
        policy.profile = _profile()
