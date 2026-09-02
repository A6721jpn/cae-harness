from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import MappingProxyType

import pytest

from febio_cae_harness.model import (
    ASK_AND_BLOCK,
    EvidenceProvenance,
    STEPInspectionError,
    StepMeshingPlan,
    StepMeshingRequest,
    inspect_step,
    plan_step_meshing,
)

AUTH = EvidenceProvenance("synthetic-intent", "intent.json", authoritative=True)
UNAUTH = EvidenceProvenance("synthetic-note", "note.txt")
STEP = b"""ISO-10303-21;
HEADER;
FILE_SCHEMA(('AUTOMOTIVE_DESIGN_CC2'));
ENDSEC;
DATA;
#10 = ( NAMED_UNIT(*) SI_UNIT(.MILLI., .METRE.) LENGTH_UNIT() );
ENDSEC;
END-ISO-10303-21;
"""


def test_ready_plan_is_digest_bound_immutable_and_deterministic() -> None:
    inspection = inspect_step(STEP, source_name="synthetic.step")
    criteria = {"min_jacobian": 0.2, "max_aspect_ratio": 4.0}
    request = StepMeshingRequest(
        element_family="tet10",
        length_unit="mm",
        target_size=1.25,
        quality_criteria=criteria,
        evidence=(AUTH, UNAUTH),
    )

    plan = plan_step_meshing(inspection, request)

    assert isinstance(plan, StepMeshingPlan)
    assert plan.status == "READY"
    assert plan.step_sha256 == inspection.sha256
    assert plan.element_family == "tet10"
    assert plan.length_unit == "mm"
    assert plan.target_size == 1.25
    assert isinstance(plan.quality_criteria, MappingProxyType)
    assert plan.evidence == (AUTH, UNAUTH)
    assert plan.questions == ()
    assert plan.to_dict() == {
        "step_sha256": inspection.sha256,
        "status": "READY",
        "element_family": "tet10",
        "length_unit": "mm",
        "target_size": 1.25,
        "quality_criteria": {"max_aspect_ratio": 4.0, "min_jacobian": 0.2},
        "evidence": [AUTH.to_dict(), UNAUTH.to_dict()],
        "questions": [],
    }
    criteria["max_aspect_ratio"] = 99.0
    assert plan.quality_criteria["max_aspect_ratio"] == 4.0
    with pytest.raises(TypeError):
        plan.quality_criteria["new"] = 1.0  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        plan.status = ASK_AND_BLOCK  # type: ignore[misc]


def test_missing_or_non_authoritative_settings_ask_without_geometry_inference() -> None:
    inspection = inspect_step(STEP)

    absent = plan_step_meshing(inspection, StepMeshingRequest(evidence=(AUTH,)))
    assert absent.status == ASK_AND_BLOCK
    assert absent.questions == (
        "Which element family should be used for meshing?",
        "Which length unit should target_size use?",
        "What target element size should be used?",
        "Which mesh quality criteria must be enforced?",
    )

    non_authoritative = plan_step_meshing(
        inspection,
        StepMeshingRequest(
            element_family="tet4",
            length_unit="mm",
            target_size=1.0,
            quality_criteria={"max_aspect_ratio": 5.0},
            evidence=(UNAUTH,),
        ),
    )
    assert non_authoritative.status == ASK_AND_BLOCK
    assert len(non_authoritative.questions) == 4
    assert all("geometry" not in question.lower() for question in non_authoritative.questions)


@pytest.mark.parametrize("size", [0.0, -1.0, float("nan"), float("inf"), -float("inf")])
def test_request_rejects_nonfinite_or_nonpositive_target_size(size: float) -> None:
    with pytest.raises(ValueError):
        StepMeshingRequest(target_size=size, evidence=(AUTH,))


@pytest.mark.parametrize("element_family", ["hex8", "Tet10", "tet15", " tet4"])
def test_request_rejects_unsupported_or_noncanonical_element_family(
    element_family: str,
) -> None:
    with pytest.raises(ValueError, match="element_family"):
        StepMeshingRequest(element_family=element_family, evidence=(AUTH,))


@pytest.mark.parametrize(
    "criteria",
    [
        {"minimum_jacobian": 0.1, "max_aspect_ratio": 4.0},
        {"min_jacobian": 0.0, "max_aspect_ratio": 4.0},
        {"min_jacobian": -0.1, "max_aspect_ratio": 4.0},
        {"min_jacobian": float("nan"), "max_aspect_ratio": 4.0},
        {"min_jacobian": 0.1, "max_aspect_ratio": 0.99},
        {"min_jacobian": 0.1, "max_aspect_ratio": float("inf")},
        {"min_jacobian": True, "max_aspect_ratio": 4.0},
    ],
)
def test_request_rejects_quality_criteria_that_cannot_be_verified(
    criteria: dict[str, object],
) -> None:
    with pytest.raises((TypeError, ValueError), match="quality_criteria"):
        StepMeshingRequest(quality_criteria=criteria, evidence=(AUTH,))


def test_request_rejects_empty_evidence_and_planner_separates_step_errors() -> None:
    with pytest.raises(ValueError, match="evidence"):
        StepMeshingRequest(evidence=())

    conflicting_step = inspect_step(
        STEP.replace(
            b".MILLI., .METRE.",
            b".METRE., .METRE.",
        )
    )
    with pytest.raises(STEPInspectionError, match="unit"):
        plan_step_meshing(
            conflicting_step,
            StepMeshingRequest(
                element_family="tet10",
                length_unit="mm",
                target_size=1.0,
                quality_criteria={"max_aspect_ratio": 4.0},
                evidence=(AUTH,),
            ),
        )

    empty_step = inspect_step(b"ISO-10303-21;HEADER;ENDSEC;DATA;ENDSEC;END-ISO-10303-21;")
    with pytest.raises(STEPInspectionError, match="entity"):
        plan_step_meshing(
            empty_step,
            StepMeshingRequest(
                element_family="tet10",
                length_unit="mm",
                target_size=1.0,
                quality_criteria={"max_aspect_ratio": 4.0},
                evidence=(AUTH,),
            ),
        )


def test_step_unit_mismatch_is_rejected_without_overriding_request() -> None:
    inspection = inspect_step(STEP.replace(b".MILLI., .METRE.", b".METRE., .METRE."))
    request = StepMeshingRequest(
        element_family="tet10",
        length_unit="mm",
        target_size=1.0,
        quality_criteria={"max_aspect_ratio": 4.0},
        evidence=(AUTH,),
    )
    with pytest.raises(STEPInspectionError, match="conflicting"):
        plan_step_meshing(inspection, request)
