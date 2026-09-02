from __future__ import annotations

from dataclasses import FrozenInstanceError
from math import inf, nan
from pathlib import Path

import pytest

from febio_cae_harness.autonomy import (
    NegativeJacobianDiagnostic,
    NegativeJacobianObservation,
    diagnose_negative_jacobian,
    observe_negative_jacobian_log,
)
from febio_cae_harness.solver import validate_log


def make_observation(**overrides: object) -> NegativeJacobianObservation:
    values: dict[str, object] = {
        "attempt_id": "attempt-3",
        "log_evidence_digest": "sha256:log-3",
        "initial_mesh_valid": True,
        "failure_step": 2,
        "failure_time": 0.25,
        "element_id": 17,
        "integration_point": 3,
        "log_line_number": 8,
        "integration_point_jacobian": -0.125,
        "surrounding_mesh_metrics": {"aspect_ratio": 1.4, "scaled_jacobian": 0.71},
        "roi_relation_evidence": {
            "relation": "explicitly supplied",
            "authoritative": True,
        },
        "contact_relation_evidence": {
            "relation": "explicitly supplied",
            "authoritative": True,
        },
        "constraint_relation_evidence": {
            "relation": "explicitly supplied",
            "authoritative": True,
        },
    }
    values.update(overrides)
    return NegativeJacobianObservation(**values)  # type: ignore[arg-type]


def test_observation_binds_explicit_context_and_detaches_inputs() -> None:
    metrics = {"aspect_ratio": 1.4}
    roi = {"relation": "supplied", "authoritative": True}
    observation = make_observation(
        surrounding_mesh_metrics=metrics,
        roi_relation_evidence=roi,
    )
    metrics["aspect_ratio"] = 99.0
    roi["relation"] = "changed"

    assert observation.attempt_id == "attempt-3"
    assert observation.log_evidence_digest == "sha256:log-3"
    assert observation.initial_mesh_valid is True
    assert observation.failure_step == 2
    assert observation.failure_time == 0.25
    assert observation.element_id == 17
    assert observation.integration_point == 3
    assert observation.log_line_number == 8
    assert observation.integration_point_jacobian == -0.125
    assert observation.surrounding_mesh_metrics["aspect_ratio"] == 1.4
    assert observation.roi_relation_evidence["relation"] == "supplied"  # type: ignore[index]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("failure_time", nan),
        ("integration_point_jacobian", inf),
        ("surrounding_mesh_metrics", {"aspect_ratio": nan}),
        ("roi_relation_evidence", {"metric": inf}),
    ],
)
def test_observation_rejects_non_finite_numeric_values(field: str, value: object) -> None:
    with pytest.raises(ValueError, match="finite"):
        make_observation(**{field: value})


def test_observation_requires_identity_and_explicit_mesh_status() -> None:
    with pytest.raises(ValueError, match="attempt_id"):
        make_observation(attempt_id=" ")
    with pytest.raises(ValueError, match="digest"):
        make_observation(log_evidence_digest="")
    with pytest.raises(TypeError, match="initial_mesh_valid"):
        make_observation(initial_mesh_valid=1)
    with pytest.raises(TypeError):
        make_observation(official=True)


def test_diagnosis_separates_observed_technical_and_authority_evidence() -> None:
    diagnostic = diagnose_negative_jacobian(
        make_observation(
            surrounding_mesh_metrics={},
            contact_relation_evidence={"relation": "supplied", "authoritative": False},
            constraint_relation_evidence=None,
        )
    )

    assert diagnostic.observed_fields["attempt_id"] == "attempt-3"
    assert diagnostic.observed_fields["element_id"] == 17
    assert "roi_relation_evidence" in diagnostic.observed_fields
    assert "contact_relation_evidence" in diagnostic.observed_fields
    assert "constraint_relation_evidence" not in diagnostic.observed_fields
    assert diagnostic.missing_technical_evidence == ("surrounding_mesh_metrics",)
    assert diagnostic.missing_physical_authority_evidence == (
        "roi_relation_evidence",
        "contact_relation_evidence",
        "constraint_relation_evidence",
    )


@pytest.mark.parametrize(
    "marker",
    [
        {"authoritative": True},
        {"official": True},
        {"validated": True},
        {"source": "user"},
        {"is_authoritative": True},
        {"authority": "canonical"},
    ],
    ids=(
        "authoritative",
        "official",
        "validated",
        "user-source",
        "equivalent-boolean",
        "equivalent-label",
    ),
)
def test_diagnosis_never_treats_caller_markers_as_physical_authority(
    marker: dict[str, object],
) -> None:
    relation = {"relation": "caller supplied", **marker}
    diagnostic = diagnose_negative_jacobian(
        make_observation(
            roi_relation_evidence=relation,
            contact_relation_evidence=relation,
            constraint_relation_evidence=relation,
        )
    )

    assert diagnostic.missing_physical_authority_evidence == (
        "roi_relation_evidence",
        "contact_relation_evidence",
        "constraint_relation_evidence",
    )


def test_diagnosis_requires_explicit_comparison_checklist_without_success_flags() -> None:
    diagnostic = diagnose_negative_jacobian(make_observation())

    assert diagnostic.repair_comparison_checklist == (
        "geometry_delta",
        "mesh_quality",
        "load_path",
        "reactions",
        "displacement",
        "contact",
        "evaluation_quantities",
    )
    assert "official" not in diagnostic.observed_fields
    assert "success" not in diagnostic.observed_fields
    assert not hasattr(diagnostic, "success")


def test_diagnostic_rejects_incomplete_checklist_and_is_frozen() -> None:
    observation = make_observation()
    with pytest.raises(ValueError, match="checklist"):
        NegativeJacobianDiagnostic(
            observation=observation,
            observed_fields={},
            missing_technical_evidence=(),
            missing_physical_authority_evidence=(),
            repair_comparison_checklist=("geometry_delta",),
        )

    diagnostic = diagnose_negative_jacobian(observation)
    with pytest.raises(FrozenInstanceError):
        diagnostic.observation = observation  # type: ignore[misc]


def test_diagnosis_is_pure_and_rejects_non_observations() -> None:
    observation = make_observation()
    first = diagnose_negative_jacobian(observation)
    second = diagnose_negative_jacobian(observation)

    assert first == second
    assert observation.surrounding_mesh_metrics["aspect_ratio"] == 1.4
    with pytest.raises(TypeError, match="observation"):
        diagnose_negative_jacobian({})  # type: ignore[arg-type]


def test_observation_is_derived_from_explicit_log_location(tmp_path: Path) -> None:
    path = tmp_path / "attempt.log"
    payload = (
        b"time step = 2\n"
        b"time = 0.25\n"
        b"Negative Jacobian determinant = -0.125 at element 17, integration point 3\n"
    )
    path.write_bytes(payload)
    validation = validate_log(path)

    observation = observe_negative_jacobian_log(
        validation,
        attempt_id="attempt-3",
        initial_mesh_valid=True,
        surrounding_mesh_metrics={"aspect_ratio": 1.4},
        roi_relation_evidence=None,
        contact_relation_evidence=None,
        constraint_relation_evidence=None,
    )

    assert observation.failure_step == 2
    assert observation.failure_time == 0.25
    assert observation.element_id == 17
    assert observation.integration_point == 3
    assert observation.log_line_number == 3
    assert observation.integration_point_jacobian == -0.125


def test_observation_rejects_log_without_complete_explicit_location(tmp_path: Path) -> None:
    path = tmp_path / "attempt.log"
    path.write_text("Negative Jacobian determinant at element 17", encoding="utf-8")

    with pytest.raises(ValueError, match="explicit"):
        observe_negative_jacobian_log(
            validate_log(path),
            attempt_id="attempt-3",
            initial_mesh_valid=True,
            surrounding_mesh_metrics={},
            roi_relation_evidence=None,
            contact_relation_evidence=None,
            constraint_relation_evidence=None,
        )
