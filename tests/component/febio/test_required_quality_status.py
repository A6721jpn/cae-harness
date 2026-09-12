"""Mandatory producer outcomes, not arithmetic stubs or caller labels, decide status."""

from dataclasses import replace

from febio_cae.domain import AssessmentStatus, CriterionAssessment

from .fixtures import evidence, make_revision


def test_required_producer_replaces_only_its_arithmetic_stub_and_failure_wins() -> None:
    from febio_cae.application._required_quality import _quality_status

    criterion = replace(make_revision().spec.quality_policy.criteria[0], metric_id="planar_contact")
    arithmetic = (
        CriterionAssessment(
            criterion.criterion_id,
            "numeric",
            AssessmentStatus.UNVERIFIED,
            (),
            "unsupported by arithmetic adapter",
        ),
    )
    required = (
        CriterionAssessment(
            "contact_quality", "numeric", AssessmentStatus.PASS, (), "producer evidence"
        ),
    )
    assert _quality_status((criterion,), arithmetic, required, "PASS") == "PASS"
    failed = (replace(required[0], status=AssessmentStatus.FAIL),)
    assert _quality_status((criterion,), arithmetic, failed, "PASS") == "FAIL"


def test_required_looking_criterion_label_cannot_hide_an_unknown_method() -> None:
    from febio_cae.application._required_quality import _quality_status

    criterion = replace(
        make_revision().spec.quality_policy.criteria[0],
        criterion_id="contact_quality",
        metric_id="unimplemented_method",
        evidence=evidence("quality_policy.criteria.contact_quality", "arithmetic"),
    )
    arithmetic = (
        CriterionAssessment(
            "contact_quality", "numeric", AssessmentStatus.UNVERIFIED, (), "unimplemented"
        ),
    )
    required = (
        CriterionAssessment(
            "contact_quality", "numeric", AssessmentStatus.PASS, (), "producer evidence"
        ),
    )
    assert _quality_status((criterion,), arithmetic, required, "PASS") == "UNVERIFIED"
