from __future__ import annotations

import hashlib
import importlib
from dataclasses import FrozenInstanceError
from types import ModuleType
from typing import Any

import pytest

from febio_cae.domain import (
    AsPlaced,
    BodyId,
    Budget,
    CompressibleNeoHookean,
    ContactId,
    ContactIntent,
    DofState,
    EvaluationRequest,
    EvidenceRef,
    FaceId,
    FaceMeasurement,
    FaceSetRule,
    FrameId,
    GeometryIntent,
    IsotropicLinearElastic,
    LocalRefinement,
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
    canonical_bytes,
)
from febio_cae.domain.contact import Frictionless
from febio_cae.domain.rigid_kinematics import RigidDofState
from febio_cae.domain.spatial import ProperRotation


def _optional_module(name: str) -> ModuleType | None:
    try:
        return importlib.import_module(name)
    except (ImportError, ModuleNotFoundError):
        return None


CASE_MODULE = _optional_module("febio_cae.domain.case_spec")


def _case() -> ModuleType:
    if CASE_MODULE is None:
        pytest.skip("CaseSpec API availability is covered by the dedicated assertion")
    return CASE_MODULE


def _evidence(
    target_field: str,
    seed: str = "a",
    *,
    source_kind: str = "registered_document",
    reference: str = "SyntheticCaseSource:1",
) -> EvidenceRef:
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return EvidenceRef(
        schema_version="1",
        source_kind=source_kind,
        reference=reference,
        target_field=target_field,
        content_digest=digest,
    )


WORLD = FrameId("World")
PART_BODY = BodyId("part-body")
TOOL_BODY = BodyId("tool-body")
PART_DIGEST = "a" * 64
TOOL_DIGEST = "b" * 64


def _identity_transform(source: FrameId, target: FrameId) -> RigidTransform:
    return RigidTransform(
        source_frame=source,
        target_frame=target,
        translation=Translation3(target, Quantity(0, "m"), Quantity(0, "m"), Quantity(0, "m")),
        rotation=ProperRotation(((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))),
    )


def _part_selection(
    *,
    geometry_digest: str = PART_DIGEST,
    body_id: BodyId = PART_BODY,
    frame: FrameId = WORLD,
    name: str = "part-region",
    rule: Any | None = None,
    resolution: Any | None = None,
) -> SelectionRef:
    return SelectionRef(
        name=name,
        role="part_contact_surface",
        role_evidence=_evidence("selection.role", "a"),
        geometry_digest=geometry_digest,
        body_id=body_id,
        frame=frame,
        rule=WholeBodyRule(body_id) if rule is None else rule,
        resolution=resolution,
    )


def _support_selection(
    *,
    geometry_digest: str = PART_DIGEST,
    body_id: BodyId = PART_BODY,
    frame: FrameId = WORLD,
    name: str = "support-region",
) -> SelectionRef:
    return SelectionRef(
        name=name,
        role="support_surface",
        role_evidence=_evidence("selection.role", "s"),
        geometry_digest=geometry_digest,
        body_id=body_id,
        frame=frame,
        rule=WholeBodyRule(body_id),
    )


def _tool_selection(
    *,
    geometry_digest: str = TOOL_DIGEST,
    body_id: BodyId = TOOL_BODY,
    frame: FrameId = WORLD,
    name: str = "tool-region",
    rule: Any | None = None,
    resolution: Any | None = None,
) -> SelectionRef:
    return SelectionRef(
        name=name,
        role="tool_contact_surface",
        role_evidence=_evidence("selection.role", "b"),
        geometry_digest=geometry_digest,
        body_id=body_id,
        frame=frame,
        rule=WholeBodyRule(body_id) if rule is None else rule,
        resolution=resolution,
    )


def _output_selection(
    *,
    geometry_digest: str = PART_DIGEST,
    body_id: BodyId = PART_BODY,
    frame: FrameId = WORLD,
    name: str = "output-region",
) -> SelectionRef:
    return SelectionRef(
        name=name,
        role="output_region",
        role_evidence=_evidence("selection.role", "c"),
        geometry_digest=geometry_digest,
        body_id=body_id,
        frame=frame,
        rule=WholeBodyRule(body_id),
    )


def _face_selection(
    *,
    geometry_digest: str,
    body_id: BodyId,
    frame: FrameId,
    face_ids: tuple[str, ...],
    reverse_resolution: bool = False,
) -> SelectionRef:
    ids = tuple(FaceId(value) for value in face_ids)
    rule = FaceSetRule(
        geometry_digest=geometry_digest,
        body_id=body_id,
        frame=frame,
        face_ids=ids,
        provenance=_evidence("selection.faces", "d"),
    )
    measurements = [
        FaceMeasurement(
            face_id=face_id,
            area=Quantity(1, "mm2"),
            centroid=Point3(
                frame,
                Quantity(0, "mm"),
                Quantity(0, "mm"),
                Quantity(0, "mm"),
            ),
        )
        for face_id in ids
    ]
    if reverse_resolution:
        measurements.reverse()
    from febio_cae.domain import ResolutionSnapshot

    return SelectionRef(
        name="face-tool-region",
        role="tool_contact_surface",
        role_evidence=_evidence("selection.role", "e"),
        geometry_digest=geometry_digest,
        body_id=body_id,
        frame=frame,
        rule=rule,
        resolution=ResolutionSnapshot(
            geometry_digest=geometry_digest,
            body_id=body_id,
            frame=frame,
            faces=measurements,
        ),
    )


def _geometry(
    *,
    body_id: BodyId = PART_BODY,
    target_frame: FrameId = WORLD,
    source_step_digest: str = "1" * 64,
) -> GeometryIntent:
    return GeometryIntent(
        source_step_digest=source_step_digest,
        geometry_digest=PART_DIGEST,
        inspection_digest="2" * 64,
        body_id=body_id,
        step_unit="mm",
        placement=_identity_transform(FrameId("PartLocal"), target_frame),
        body_evidence=_evidence("geometry.body_id", "f"),
        unit_evidence=_evidence("geometry.step_unit", "g"),
        placement_evidence=_evidence("geometry.placement", "h"),
    )


def _material(material_type: type[Any] = IsotropicLinearElastic) -> Any:
    applicability = MaterialApplicability(
        strain_statement="The supplied strain range is applicable.",
        strain_evidence=_evidence("material.strain_applicability", "i"),
        rate_statement="The supplied rate range is applicable.",
        rate_evidence=_evidence("material.rate_applicability", "j"),
    )
    return material_type(
        youngs_modulus=Quantity(1, "MPa"),
        poisson_ratio=Quantity(0.3, "1"),
        model_evidence=_evidence("material.model", "k"),
        youngs_modulus_evidence=_evidence("material.youngs_modulus", "l"),
        poisson_ratio_evidence=_evidence("material.poisson_ratio", "m"),
        applicability=applicability,
    )


def _support(
    support_id: str,
    *,
    frame: FrameId = WORLD,
    transform: RigidTransform | None = None,
) -> SolidSupport:
    return SolidSupport(
        support_id=SupportId(support_id),
        selection=_support_selection(name=f"support-{support_id}"),
        frame=frame,
        x=SupportComponent("fixed", _evidence("support.x", "n")),
        y=SupportComponent("free", _evidence("support.y", "o")),
        z=SupportComponent("fixed", _evidence("support.z", "p")),
        transform=transform,
        frame_evidence=_evidence("support.frame", "q"),
        transform_evidence=(None if transform is None else _evidence("support.transform", "r")),
    )


def _supports() -> SupportSet:
    return SupportSet(
        [
            _support("support-z"),
            _support(
                "support-a",
                frame=FrameId("SupportFrame"),
                transform=_identity_transform(WORLD, FrameId("SupportFrame")),
            ),
        ]
    )


def _rigid_tool(*, contact_surface: SelectionRef | None = None) -> RigidToolIntent:
    tool_local = FrameId("ToolLocal")
    primitive = RigidPrimitive(
        kind="sphere",
        body_id=TOOL_BODY,
        local_frame=tool_local,
        placement=_identity_transform(tool_local, WORLD),
        dimensions={"radius": Quantity(2, "mm")},
        dimension_evidence={"radius": _evidence("rigid_tool.radius", "s")},
        model_evidence=_evidence("rigid_tool.model", "t"),
        placement_evidence=_evidence("rigid_tool.placement", "u"),
    )
    dof = RigidDofSpecification(
        frame=WORLD,
        x=RigidDofComponent(RigidDofState.FIXED, _evidence("rigid_tool.x", "v")),
        y=RigidDofComponent(RigidDofState.FIXED, _evidence("rigid_tool.y", "w")),
        z=RigidDofComponent(RigidDofState.PRESCRIBED, _evidence("rigid_tool.z", "x")),
        rx=RigidDofComponent(RigidDofState.FREE, _evidence("rigid_tool.rx", "y")),
        ry=RigidDofComponent(RigidDofState.FREE, _evidence("rigid_tool.ry", "z")),
        rz=RigidDofComponent(RigidDofState.FREE, _evidence("rigid_tool.rz", "a")),
        frame_evidence=_evidence("rigid_tool.frame", "b"),
    )
    selection = _tool_selection() if contact_surface is None else contact_surface
    return RigidToolIntent(
        primitive=primitive,
        contact_surface=selection,
        dofs=dof,
        contact_surface_evidence=_evidence("rigid_tool.contact_surface", "c"),
    )


def _motion(*, samples: list[MotionSample] | None = None) -> MotionProfile:
    if samples is None:
        samples = [
            MotionSample(Quantity(1, "s"), Quantity(0, "mm")),
            MotionSample(Quantity(2, "s"), Quantity(1, "mm")),
            MotionSample(Quantity(3, "s"), Quantity(2, "mm")),
        ]
    return MotionProfile(
        direction=UnitDirection(WORLD, 0.0, 0.0, 1.0),
        initial_reference_point=Point3(
            WORLD, Quantity(0, "mm"), Quantity(0, "mm"), Quantity(0, "mm")
        ),
        samples=samples,
        applicability=MotionApplicability(
            quasi_static_statement="The supplied motion is quasi-static.",
            quasi_static_evidence=_evidence("motion.quasi_static_applicability", "d"),
            rate_independent_statement="The supplied motion is rate-independent.",
            rate_independent_evidence=_evidence("motion.rate_independent_applicability", "e"),
        ),
        direction_evidence=_evidence("motion.direction", "f"),
        initial_reference_point_evidence=_evidence("motion.initial_reference_point", "g"),
        history_evidence=_evidence("motion.history", "h"),
    )


def _contact(
    *, tool_surface: SelectionRef | None = None, part_surface: SelectionRef | None = None
) -> ContactIntent:
    return ContactIntent(
        contact_id=ContactId("contact-main"),
        part_surface=_part_selection() if part_surface is None else part_surface,
        tool_surface=_tool_selection() if tool_surface is None else tool_surface,
        pair_frame=WORLD,
        part_surface_evidence=_evidence("contact.part_surface", "i"),
        tool_surface_evidence=_evidence("contact.tool_surface", "j"),
        pair_frame_evidence=_evidence("contact.frame", "k"),
        friction=Frictionless(_evidence("contact.friction_model", "l")),
        arrangement=AsPlaced(_evidence("contact.arrangement", "m")),
    )


def _mesh(*, local_refinements: list[LocalRefinement] | None = None) -> MeshPolicy:
    if local_refinements is None:
        local_refinements = [
            LocalRefinement("refine-z", _part_selection(name="refine-z"), Quantity(1, "mm")),
            LocalRefinement("refine-a", _tool_selection(name="refine-a"), Quantity(1, "mm")),
        ]
    return MeshPolicy(
        element_type="tet10",
        global_size=Quantity(2, "mm"),
        local_refinements=local_refinements,
        quality_profile=NumericalProfileRef("mesh-quality-profile", "mesh_quality", "c" * 64),
        max_refinements=2,
    )


def _solver(*, controls: list[SolverControl] | None = None) -> SolverPolicy:
    if controls is None:
        controls = [
            SolverControl("zeta", Quantity(2, "s")),
            SolverControl("alpha", 1),
        ]
    return SolverPolicy(
        profile=NumericalProfileRef("solver-profile", "solver", "d" * 64),
        controls=controls,
        increments=TimeIncrementPolicy(
            initial_step=Quantity(100, "ms"),
            minimum_step=Quantity(10, "ms"),
            maximum_step=Quantity(1, "s"),
            adaptive=True,
            max_steps=20,
            max_step_retries=2,
            must_points=[Quantity(0, "s"), Quantity(1500, "ms")],
        ),
        retry_recipe_ids=["retry_primary", "retry_secondary"],
    )


def _output(*, requests: list[OutputRequest] | None = None) -> OutputPolicy:
    part = _output_selection(name="output-part")
    tool = _output_selection(
        geometry_digest=TOOL_DIGEST,
        body_id=TOOL_BODY,
        name="output-tool",
    )
    if requests is None:
        requests = [
            OutputRequest(
                request_id="request_tool",
                quantity_id="contact_force",
                measure_id="max",
                component_id="z",
                location="rigid_body",
                selection=tool,
                frame=WORLD,
                display_unit="N",
                evidence=_evidence("outputs.requests.request_tool", "e"),
            ),
            OutputRequest(
                request_id="request_part",
                quantity_id="displacement",
                measure_id="max",
                component_id="z",
                location="node",
                selection=part,
                frame=WORLD,
                display_unit="mm",
                evidence=_evidence("outputs.requests.request_part", "f"),
            ),
        ]
    return OutputPolicy(
        profile=NumericalProfileRef("outputs-profile", "outputs", "e" * 64),
        requests=requests,
        saved_times=[Quantity(1000, "ms"), Quantity(2, "s"), Quantity(3000, "ms")],
        evaluations=[
            EvaluationRequest(
                evaluation_id="evaluation_tool",
                output_request_id="request_tool",
                aggregation_id="peak",
                selection=tool,
                state_times=[Quantity(1, "s"), Quantity(3, "s")],
                evidence=_evidence("outputs.evaluations.evaluation_tool", "g"),
            ),
            EvaluationRequest(
                evaluation_id="evaluation_part",
                output_request_id="request_part",
                aggregation_id="peak",
                selection=part,
                state_times=[Quantity(2, "s")],
                evidence=_evidence("outputs.evaluations.evaluation_part", "h"),
            ),
        ],
    )


def _quality(
    *, evaluation_ids: tuple[str, ...] = ("evaluation_part", "evaluation_tool")
) -> QualityPolicy:
    return QualityPolicy(
        profile=NumericalProfileRef("quality-profile", "quality", "f" * 64),
        criteria=(
            QualityCriterion(
                criterion_id="criterion_main",
                metric_id="residual_norm",
                evaluation_ids=evaluation_ids,
                thresholds=(QualityThreshold("absolute_tolerance", Quantity(1, "MPa")),),
                applicability_reason="解析条件 — convergence étape 1",
                evidence=_evidence("quality_policy.criteria.criterion_main", "i"),
            ),
        ),
    )


def _case_kwargs(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "geometry": _geometry(),
        "material": _material(),
        "support": _supports(),
        "rigid_tool": _rigid_tool(),
        "motion": _motion(),
        "contact": _contact(),
        "mesh_policy": _mesh(),
        "solver_policy": _solver(),
        "outputs": _output(),
        "quality_policy": _quality(),
        "budget": Budget(
            max_elapsed=Quantity(10, "s"),
            max_attempts=2,
            cpu_workers=1,
            max_llm_calls=0,
            max_llm_tokens=0,
        ),
    }
    values.update(overrides)
    return values


def _case_value(**overrides: object) -> Any:
    return _case().CaseSpec(**_case_kwargs(**overrides))


def test_case_spec_api_is_available() -> None:
    assert CASE_MODULE is not None, "P1-B12 CaseSpec module is not available"
    for name in ("SCHEMA_VERSION", "CaseSpec", "CaseSpecValidationError"):
        assert getattr(CASE_MODULE, name, None) is not None, name


@pytest.mark.parametrize(
    "field",
    [
        "geometry",
        "material",
        "support",
        "rigid_tool",
        "motion",
        "contact",
        "mesh_policy",
        "solver_policy",
        "outputs",
        "quality_policy",
        "budget",
    ],
)
def test_case_spec_rejects_each_wrong_child_type_independently(field: str) -> None:
    with pytest.raises(_case().CaseSpecValidationError, match=field):
        _case_value(**{field: None})
    with pytest.raises(_case().CaseSpecValidationError, match=field):
        _case_value(**{field: {"forged": True}})


def test_case_spec_is_frozen_and_projection_is_detached() -> None:
    case = _case_value()
    with pytest.raises(FrozenInstanceError):
        case.geometry = _geometry()  # type: ignore[misc]

    projected = case.to_dict()
    projected["geometry"]["body_id"] = "mutated"  # type: ignore[index]
    projected["outputs"]["requests"][0]["selection"]["name"] = "mutated"  # type: ignore[index]
    assert case.geometry.body_id == PART_BODY
    assert case.to_dict()["geometry"]["body_id"] == PART_BODY.value


def test_case_spec_accepts_both_material_concrete_types() -> None:
    assert isinstance(
        _case_value(material=_material(IsotropicLinearElastic)).material, IsotropicLinearElastic
    )
    assert isinstance(
        _case_value(material=_material(CompressibleNeoHookean)).material, CompressibleNeoHookean
    )


def test_case_spec_accepts_explicit_support_transform_from_selection_frame() -> None:
    case = _case_value()
    transformed = next(
        item for item in case.support.supports if item.support_id.value == "support-a"
    )
    assert transformed.transform is not None
    assert transformed.selection.frame == WORLD
    assert transformed.frame == FrameId("SupportFrame")


def test_case_spec_rejects_support_selection_part_identity_mismatch() -> None:
    bad_support = _support("support-bad", frame=WORLD)
    bad_support = SolidSupport(
        support_id=bad_support.support_id,
        selection=_support_selection(geometry_digest=TOOL_DIGEST, body_id=TOOL_BODY),
        frame=bad_support.frame,
        x=bad_support.x,
        y=bad_support.y,
        z=bad_support.z,
        frame_evidence=bad_support.frame_evidence,
    )
    with pytest.raises(_case().CaseSpecValidationError, match="support"):
        _case_value(support=SupportSet([_support("support-good"), bad_support]))


def test_case_spec_rejects_nonfirst_support_selection_frame_mismatch() -> None:
    bad_support = SolidSupport(
        support_id=SupportId("support-bad"),
        selection=_support_selection(frame=FrameId("OtherFrame")),
        frame=FrameId("OtherFrame"),
        x=SupportComponent("fixed", _evidence("support.x", "a")),
        y=SupportComponent("free", _evidence("support.y", "b")),
        z=SupportComponent("fixed", _evidence("support.z", "c")),
        frame_evidence=_evidence("support.frame", "d"),
    )
    with pytest.raises(_case().CaseSpecValidationError, match="support"):
        _case_value(support=SupportSet([_support("support-good"), bad_support]))


def test_case_spec_rejects_contact_part_identity_mismatch() -> None:
    with pytest.raises(_case().CaseSpecValidationError, match="contact.part_surface"):
        _case_value(
            contact=_contact(
                part_surface=_part_selection(geometry_digest=TOOL_DIGEST, body_id=TOOL_BODY)
            )
        )


@pytest.mark.parametrize("field", ["rigid_tool", "contact", "motion"])
def test_case_spec_rejects_common_frame_mismatch(field: str) -> None:
    if field == "rigid_tool":
        tool = _rigid_tool()
        primitive = RigidPrimitive(
            kind=tool.primitive.kind,
            body_id=tool.primitive.body_id,
            local_frame=tool.primitive.local_frame,
            placement=_identity_transform(tool.primitive.local_frame, FrameId("OtherFrame")),
            dimensions=tool.primitive.dimensions,
            dimension_evidence=tool.primitive.dimension_evidence,
            model_evidence=tool.primitive.model_evidence,
            placement_evidence=tool.primitive.placement_evidence,
        )
        changed = RigidToolIntent(
            primitive=primitive,
            contact_surface=_tool_selection(frame=FrameId("OtherFrame")),
            dofs=RigidDofSpecification(
                frame=FrameId("OtherFrame"),
                x=tool.dofs.x,
                y=tool.dofs.y,
                z=tool.dofs.z,
                rx=tool.dofs.rx,
                ry=tool.dofs.ry,
                rz=tool.dofs.rz,
                frame_evidence=tool.dofs.frame_evidence,
            ),
            contact_surface_evidence=tool.contact_surface_evidence,
        )
    elif field == "contact":
        changed = ContactIntent(
            contact_id=ContactId("contact-main"),
            part_surface=_part_selection(frame=FrameId("OtherFrame")),
            tool_surface=_tool_selection(frame=FrameId("OtherFrame")),
            pair_frame=FrameId("OtherFrame"),
            part_surface_evidence=_evidence("contact.part_surface", "i"),
            tool_surface_evidence=_evidence("contact.tool_surface", "j"),
            pair_frame_evidence=_evidence("contact.frame", "k"),
            friction=Frictionless(_evidence("contact.friction_model", "l")),
            arrangement=AsPlaced(_evidence("contact.arrangement", "m")),
        )
    else:
        changed = _motion(
            samples=[
                MotionSample(Quantity(1, "s"), Quantity(0, "mm")),
                MotionSample(Quantity(2, "s"), Quantity(1, "mm")),
            ]
        )
        changed = MotionProfile(
            direction=UnitDirection(FrameId("OtherFrame"), 0.0, 0.0, 1.0),
            initial_reference_point=Point3(
                FrameId("OtherFrame"), Quantity(0, "mm"), Quantity(0, "mm"), Quantity(0, "mm")
            ),
            samples=changed.samples,
            applicability=changed.applicability,
            direction_evidence=changed.direction_evidence,
            initial_reference_point_evidence=changed.initial_reference_point_evidence,
            history_evidence=changed.history_evidence,
        )
    with pytest.raises(_case().CaseSpecValidationError, match=field):
        _case_value(**{field: changed})


def test_case_spec_rejects_part_and_tool_body_identity_collision() -> None:
    geometry = _geometry(body_id=TOOL_BODY)
    with pytest.raises(_case().CaseSpecValidationError, match="body"):
        _case_value(geometry=geometry)


def test_case_spec_rejects_tool_surface_canonical_selection_mismatch() -> None:
    tool_surface = _tool_selection()
    different = _tool_selection(name="different-tool-surface")
    with pytest.raises(_case().CaseSpecValidationError, match="tool_surface"):
        _case_value(
            rigid_tool=_rigid_tool(contact_surface=tool_surface),
            contact=_contact(tool_surface=different),
        )


def test_case_spec_accepts_equivalent_unordered_tool_surface_selection() -> None:
    first = _face_selection(
        geometry_digest=TOOL_DIGEST,
        body_id=TOOL_BODY,
        frame=WORLD,
        face_ids=("face-2", "face-1"),
    )
    second = _face_selection(
        geometry_digest=TOOL_DIGEST,
        body_id=TOOL_BODY,
        frame=WORLD,
        face_ids=("face-1", "face-2"),
        reverse_resolution=True,
    )
    assert first.to_bytes() == second.to_bytes()
    case = _case_value(
        rigid_tool=_rigid_tool(contact_surface=first), contact=_contact(tool_surface=second)
    )
    assert case.rigid_tool.contact_surface.to_bytes() == case.contact.tool_surface.to_bytes()


@pytest.mark.parametrize("owner", ["mesh", "request", "evaluation"])
def test_case_spec_rejects_unrelated_or_noncommon_frame_selection_in_every_collection(
    owner: str,
) -> None:
    unrelated = _output_selection(geometry_digest="c" * 64, body_id=BodyId("other-body"))
    if owner == "mesh":
        changed = _mesh(local_refinements=[LocalRefinement("bad", unrelated, Quantity(1, "mm"))])
        kwargs = {"mesh_policy": changed}
    elif owner == "request":
        request = OutputRequest(
            request_id="request_bad",
            quantity_id="displacement",
            measure_id="max",
            component_id="z",
            location="node",
            selection=unrelated,
            frame=WORLD,
            display_unit="mm",
            evidence=_evidence("outputs.requests.request_bad", "n"),
        )
        kwargs = {"outputs": _output(requests=[request])}
    else:
        output = _output()
        bad_selection = _output_selection(frame=FrameId("OtherFrame"))
        bad_evaluation = EvaluationRequest(
            evaluation_id="evaluation_bad",
            output_request_id="request_part",
            aggregation_id="peak",
            selection=bad_selection,
            state_times=[Quantity(2, "s")],
            evidence=_evidence("outputs.evaluations.evaluation_bad", "n"),
        )
        kwargs = {
            "outputs": OutputPolicy(
                profile=output.profile,
                requests=output.requests,
                saved_times=output.saved_times,
                evaluations=[*output.evaluations, bad_evaluation],
            )
        }
    with pytest.raises(_case().CaseSpecValidationError, match="selection"):
        _case_value(**kwargs)


def test_case_spec_rejects_selection_with_declared_identity_but_wrong_common_frame() -> None:
    wrong_frame = _output_selection(frame=FrameId("OtherFrame"))
    request = OutputRequest(
        request_id="request_bad_frame",
        quantity_id="displacement",
        measure_id="max",
        component_id="z",
        location="node",
        selection=wrong_frame,
        frame=WORLD,
        display_unit="mm",
        evidence=_evidence("outputs.requests.request_bad_frame", "n"),
    )
    with pytest.raises(_case().CaseSpecValidationError, match="selection"):
        _case_value(outputs=_output(requests=[request]))


def test_case_spec_rejects_missing_quality_evaluation_reference() -> None:
    with pytest.raises(_case().CaseSpecValidationError, match="evaluation"):
        _case_value(quality_policy=_quality(evaluation_ids=("missing-evaluation",)))


def test_case_spec_accepts_si_equivalent_saved_times_and_rejects_out_of_interval() -> None:
    case = _case_value()
    assert [time.to_si().value for time in case.outputs.saved_times] == [1.0, 2.0, 3.0]
    with pytest.raises(_case().CaseSpecValidationError, match="saved_times"):
        _case_value(
            outputs=OutputPolicy(
                profile=case.outputs.profile,
                requests=case.outputs.requests,
                saved_times=[Quantity(0, "s"), Quantity(1, "s"), Quantity(2, "s")],
                evaluations=case.outputs.evaluations,
            )
        )
    with pytest.raises(_case().CaseSpecValidationError, match="saved_times"):
        _case_value(
            outputs=OutputPolicy(
                profile=case.outputs.profile,
                requests=case.outputs.requests,
                saved_times=[Quantity(1, "s"), Quantity(2, "s"), Quantity(4, "s")],
                evaluations=[
                    EvaluationRequest(
                        evaluation_id="evaluation_tool",
                        output_request_id="request_tool",
                        aggregation_id="peak",
                        selection=case.outputs.evaluations[1].selection,
                        state_times=[Quantity(1, "s"), Quantity(2, "s")],
                        evidence=_evidence("outputs.evaluations.evaluation_tool", "g"),
                    ),
                    EvaluationRequest(
                        evaluation_id="evaluation_part",
                        output_request_id="request_part",
                        aggregation_id="peak",
                        selection=case.outputs.evaluations[0].selection,
                        state_times=[Quantity(2, "s")],
                        evidence=_evidence("outputs.evaluations.evaluation_part", "h"),
                    ),
                ],
            )
        )


def test_case_spec_does_not_require_kinematic_compatibility() -> None:
    case = _case_value()
    assert case.rigid_tool.dofs.rx.state == DofState.FREE.value


def test_case_spec_canonical_bytes_follow_public_child_canonical_bytes_and_identity() -> None:
    first = _case_value(
        support=SupportSet([_support("support-a"), _support("support-z")]),
        mesh_policy=_mesh(
            local_refinements=[
                LocalRefinement("refine-a", _tool_selection(name="refine-a"), Quantity(1, "mm")),
                LocalRefinement("refine-z", _part_selection(name="refine-z"), Quantity(1, "mm")),
            ]
        ),
        solver_policy=_solver(
            controls=[SolverControl("alpha", 1), SolverControl("zeta", Quantity(2, "s"))]
        ),
    )
    second = _case_value(
        support=SupportSet([_support("support-z"), _support("support-a")]),
        mesh_policy=_mesh(
            local_refinements=[
                LocalRefinement("refine-z", _part_selection(name="refine-z"), Quantity(1, "mm")),
                LocalRefinement("refine-a", _tool_selection(name="refine-a"), Quantity(1, "mm")),
            ]
        ),
        solver_policy=_solver(
            controls=[SolverControl("zeta", Quantity(2, "s")), SolverControl("alpha", 1)]
        ),
    )
    assert first.to_bytes() == second.to_bytes()
    changed = _case_value(geometry=_geometry(source_step_digest="3" * 64))
    assert changed.to_bytes() != first.to_bytes()
    assert first.to_bytes() == canonical_bytes(first.to_dict())
