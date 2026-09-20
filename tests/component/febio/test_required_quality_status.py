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
    required = _passing_required() + (
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
    required = _passing_required() + (
        CriterionAssessment(
            "contact_quality", "numeric", AssessmentStatus.PASS, (), "producer evidence"
        ),
    )
    assert _quality_status((criterion,), arithmetic, required, "PASS") == "UNVERIFIED"


def test_source_local_metric_routes_its_declared_criterion_to_the_mesh_producer() -> None:
    from febio_cae.application._required_quality import _quality_status

    criterion = replace(
        make_revision().spec.quality_policy.criteria[0],
        metric_id="source_local_mesh_dependence",
    )
    arithmetic = (
        CriterionAssessment(
            criterion.criterion_id,
            "numeric",
            AssessmentStatus.UNVERIFIED,
            (),
            "public arithmetic adapter does not own source-local studies",
        ),
    )
    required = _passing_required() + (
        CriterionAssessment(
            criterion.criterion_id,
            "numeric",
            AssessmentStatus.PASS,
            (),
            "registered source-local producer evidence",
        ),
    )
    assert _quality_status((criterion,), arithmetic, required, "PASS") == "PASS"


def _passing_required() -> tuple[CriterionAssessment, ...]:
    return tuple(
        CriterionAssessment(name, "numeric", AssessmentStatus.PASS, (), "producer evidence")
        for name in (
            "execution_result_completeness",
            "contact_quality",
            "motion_support_contact_fidelity",
            "quasistatic_equilibrium",
            "solver_residual",
            "mesh_dependence",
        )
    )


def test_only_unverified_mesh_is_optional_with_all_five_required_passes() -> None:
    from febio_cae.application._required_quality import _quality_status

    rows = _passing_required()
    optional = (*rows[:-1], replace(rows[-1], status=AssessmentStatus.UNVERIFIED))
    assert _quality_status((), (), optional, "PASS") == "PASS"
    assert (
        _quality_status(
            (), (), (*rows[:-1], replace(rows[-1], status=AssessmentStatus.FAIL)), "PASS"
        )
        == "FAIL"
    )
    for index in range(len(rows)):
        assert (
            _quality_status((), (), optional[:index] + optional[index + 1 :], "PASS")
            == "UNVERIFIED"
        )
    for index in range(5):
        changed = list(optional)
        changed[index] = replace(changed[index], status=AssessmentStatus.UNVERIFIED)
        assert _quality_status((), (), changed, "PASS") == "UNVERIFIED"
    assert _quality_status((), (), (), "PASS") == "UNVERIFIED"
