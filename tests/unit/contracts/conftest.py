from __future__ import annotations

import hashlib

import pytest

from febio_cae.domain import (
    AsPlaced,
    BodyId,
    Budget,
    CaseSpec,
    ContactId,
    ContactIntent,
    DofState,
    EvaluationRequest,
    EvidenceRef,
    FrameId,
    GeometryIntent,
    IsotropicLinearElastic,
    MaterialApplicability,
    MeshPolicy,
    MotionApplicability,
    MotionProfile,
    MotionSample,
    NumericalProfileRef,
    OutputPolicy,
    OutputRequest,
    Point3,
    QualityCriterion,
    QualityPolicy,
    QualityThreshold,
    Quantity,
    RigidDofComponent,
    RigidDofSpecification,
    RigidPrimitive,
    RigidToolIntent,
    RigidTransform,
    SelectionRef,
    SolidSupport,
    SolverControl,
    SolverPolicy,
    SupportComponent,
    SupportId,
    SupportSet,
    TimeIncrementPolicy,
    Translation3,
    UnitDirection,
    WholeBodyRule,
)
from febio_cae.domain.contact import Frictionless
from febio_cae.domain.spatial import ProperRotation

WORLD = FrameId("World")
PART_BODY = BodyId("part-body")
TOOL_BODY = BodyId("tool-body")
PART_DIGEST = "a" * 64
TOOL_DIGEST = "b" * 64


def _evidence(target_field: str, seed: str) -> EvidenceRef:
    return EvidenceRef(
        schema_version="1",
        source_kind="registered_document",
        reference="SyntheticCaseRevisionFixture:1",
        target_field=target_field,
        content_digest=hashlib.sha256(seed.encode("utf-8")).hexdigest(),
    )


def _identity_transform(source: FrameId, target: FrameId) -> RigidTransform:
    return RigidTransform(
        source_frame=source,
        target_frame=target,
        translation=Translation3(target, Quantity(0, "m"), Quantity(0, "m"), Quantity(0, "m")),
        rotation=ProperRotation(((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))),
    )


@pytest.fixture
def synthetic_case_spec() -> CaseSpec:
    part_selection = SelectionRef(
        name="part-contact",
        role="part_contact_surface",
        role_evidence=_evidence("selection.role", "a"),
        geometry_digest=PART_DIGEST,
        body_id=PART_BODY,
        frame=WORLD,
        rule=WholeBodyRule(PART_BODY),
    )
    tool_selection = SelectionRef(
        name="tool-contact",
        role="tool_contact_surface",
        role_evidence=_evidence("selection.role", "b"),
        geometry_digest=TOOL_DIGEST,
        body_id=TOOL_BODY,
        frame=WORLD,
        rule=WholeBodyRule(TOOL_BODY),
    )
    output_part = SelectionRef(
        name="part-output",
        role="output_region",
        role_evidence=_evidence("selection.role", "c"),
        geometry_digest=PART_DIGEST,
        body_id=PART_BODY,
        frame=WORLD,
        rule=WholeBodyRule(PART_BODY),
    )
    output_tool = SelectionRef(
        name="tool-output",
        role="output_region",
        role_evidence=_evidence("selection.role", "d"),
        geometry_digest=TOOL_DIGEST,
        body_id=TOOL_BODY,
        frame=WORLD,
        rule=WholeBodyRule(TOOL_BODY),
    )
    geometry = GeometryIntent(
        source_step_digest="1" * 64,
        geometry_digest=PART_DIGEST,
        inspection_digest="2" * 64,
        body_id=PART_BODY,
        step_unit="mm",
        placement=_identity_transform(FrameId("PartLocal"), WORLD),
        body_evidence=_evidence("geometry.body_id", "e"),
        unit_evidence=_evidence("geometry.step_unit", "f"),
        placement_evidence=_evidence("geometry.placement", "g"),
    )
    applicability = MaterialApplicability(
        strain_statement="The supplied strain range is applicable.",
        strain_evidence=_evidence("material.strain_applicability", "h"),
        rate_statement="The supplied rate range is applicable.",
        rate_evidence=_evidence("material.rate_applicability", "i"),
    )
    material = IsotropicLinearElastic(
        youngs_modulus=Quantity(1, "MPa"),
        poisson_ratio=Quantity(0.3, "1"),
        model_evidence=_evidence("material.model", "j"),
        youngs_modulus_evidence=_evidence("material.youngs_modulus", "k"),
        poisson_ratio_evidence=_evidence("material.poisson_ratio", "l"),
        applicability=applicability,
    )
    support = SolidSupport(
        support_id=SupportId("support_main"),
        selection=SelectionRef(
            name="support-region",
            role="support_surface",
            role_evidence=_evidence("selection.role", "m"),
            geometry_digest=PART_DIGEST,
            body_id=PART_BODY,
            frame=WORLD,
            rule=WholeBodyRule(PART_BODY),
        ),
        frame=WORLD,
        x=SupportComponent("fixed", _evidence("support.x", "n")),
        y=SupportComponent("free", _evidence("support.y", "o")),
        z=SupportComponent("fixed", _evidence("support.z", "p")),
        frame_evidence=_evidence("support.frame", "q"),
    )
    supports = SupportSet([support])
    primitive = RigidPrimitive(
        kind="sphere",
        body_id=TOOL_BODY,
        local_frame=FrameId("ToolLocal"),
        placement=_identity_transform(FrameId("ToolLocal"), WORLD),
        dimensions={"radius": Quantity(2, "mm")},
        dimension_evidence={"radius": _evidence("rigid_tool.radius", "r")},
        model_evidence=_evidence("rigid_tool.model", "s"),
        placement_evidence=_evidence("rigid_tool.placement", "t"),
    )
    dofs = RigidDofSpecification(
        frame=WORLD,
        x=RigidDofComponent(DofState.FIXED, _evidence("rigid_tool.x", "u")),
        y=RigidDofComponent(DofState.FIXED, _evidence("rigid_tool.y", "v")),
        z=RigidDofComponent(DofState.PRESCRIBED, _evidence("rigid_tool.z", "w")),
        rx=RigidDofComponent(DofState.FREE, _evidence("rigid_tool.rx", "x")),
        ry=RigidDofComponent(DofState.FREE, _evidence("rigid_tool.ry", "y")),
        rz=RigidDofComponent(DofState.FREE, _evidence("rigid_tool.rz", "z")),
        frame_evidence=_evidence("rigid_tool.frame", "aa"),
    )
    rigid_tool = RigidToolIntent(
        primitive=primitive,
        contact_surface=tool_selection,
        dofs=dofs,
        contact_surface_evidence=_evidence("rigid_tool.contact_surface", "ab"),
    )
    motion = MotionProfile(
        direction=UnitDirection(WORLD, 0.0, 0.0, 1.0),
        initial_reference_point=Point3(
            WORLD, Quantity(0, "mm"), Quantity(0, "mm"), Quantity(0, "mm")
        ),
        samples=[
            MotionSample(Quantity(1, "s"), Quantity(0, "mm")),
            MotionSample(Quantity(2, "s"), Quantity(1, "mm")),
        ],
        applicability=MotionApplicability(
            quasi_static_statement="The supplied motion is quasi-static.",
            quasi_static_evidence=_evidence("motion.quasi_static_applicability", "ac"),
            rate_independent_statement="The supplied motion is rate-independent.",
            rate_independent_evidence=_evidence("motion.rate_independent_applicability", "ad"),
        ),
        direction_evidence=_evidence("motion.direction", "ae"),
        initial_reference_point_evidence=_evidence("motion.initial_reference_point", "af"),
        history_evidence=_evidence("motion.history", "ag"),
    )
    contact = ContactIntent(
        contact_id=ContactId("contact_main"),
        part_surface=part_selection,
        tool_surface=tool_selection,
        pair_frame=WORLD,
        part_surface_evidence=_evidence("contact.part_surface", "ah"),
        tool_surface_evidence=_evidence("contact.tool_surface", "ai"),
        pair_frame_evidence=_evidence("contact.frame", "aj"),
        friction=Frictionless(_evidence("contact.friction_model", "ak")),
        arrangement=AsPlaced(_evidence("contact.arrangement", "al")),
    )
    mesh_policy = MeshPolicy(
        element_type="tet10",
        global_size=Quantity(2, "mm"),
        local_refinements=[],
        quality_profile=NumericalProfileRef("mesh-profile", "mesh_quality", "c" * 64),
        max_refinements=0,
    )
    solver_policy = SolverPolicy(
        profile=NumericalProfileRef("solver-profile", "solver", "d" * 64),
        controls=[SolverControl("alpha", 1)],
        increments=TimeIncrementPolicy(
            initial_step=Quantity(100, "ms"),
            minimum_step=Quantity(10, "ms"),
            maximum_step=Quantity(1, "s"),
            adaptive=True,
            max_steps=20,
            max_step_retries=2,
            must_points=[Quantity(0, "s")],
        ),
        retry_recipe_ids=["retry_primary"],
    )
    output_policy = OutputPolicy(
        profile=NumericalProfileRef("outputs-profile", "outputs", "e" * 64),
        requests=[
            OutputRequest(
                request_id="request_part",
                quantity_id="displacement",
                measure_id="max",
                component_id="z",
                location="node",
                selection=output_part,
                frame=WORLD,
                display_unit="mm",
                evidence=_evidence("outputs.requests.request_part", "am"),
            ),
            OutputRequest(
                request_id="request_tool",
                quantity_id="contact_force",
                measure_id="max",
                component_id="z",
                location="rigid_body",
                selection=output_tool,
                frame=WORLD,
                display_unit="N",
                evidence=_evidence("outputs.requests.request_tool", "an"),
            ),
        ],
        saved_times=[Quantity(1, "s"), Quantity(2, "s")],
        evaluations=[
            EvaluationRequest(
                evaluation_id="evaluation_part",
                output_request_id="request_part",
                aggregation_id="peak",
                selection=output_part,
                state_times=[Quantity(1, "s"), Quantity(2, "s")],
                evidence=_evidence("outputs.evaluations.evaluation_part", "ao"),
            ),
            EvaluationRequest(
                evaluation_id="evaluation_tool",
                output_request_id="request_tool",
                aggregation_id="peak",
                selection=output_tool,
                state_times=[Quantity(1, "s"), Quantity(2, "s")],
                evidence=_evidence("outputs.evaluations.evaluation_tool", "ap"),
            ),
        ],
    )
    quality_policy = QualityPolicy(
        profile=NumericalProfileRef("quality-profile", "quality", "f" * 64),
        criteria=[
            QualityCriterion(
                criterion_id="criterion_main",
                metric_id="residual_norm",
                evaluation_ids=["evaluation_part", "evaluation_tool"],
                thresholds=[QualityThreshold("absolute_tolerance", Quantity(1, "MPa"))],
                applicability_reason="解析条件 — convergence étape 1",
                evidence=_evidence("quality_policy.criteria.criterion_main", "aq"),
            )
        ],
    )
    budget = Budget(
        max_elapsed=Quantity(10, "s"),
        max_attempts=2,
        cpu_workers=1,
        max_llm_calls=0,
        max_llm_tokens=0,
    )
    return CaseSpec(
        geometry=geometry,
        material=material,
        support=supports,
        rigid_tool=rigid_tool,
        motion=motion,
        contact=contact,
        mesh_policy=mesh_policy,
        solver_policy=solver_policy,
        outputs=output_policy,
        quality_policy=quality_policy,
        budget=budget,
    )
