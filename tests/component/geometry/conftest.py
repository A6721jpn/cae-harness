from __future__ import annotations

import hashlib
from typing import Any

import pytest

from febio_cae.adapters.geometry import (
    BACKEND_TET10_ORDER_ID,
    BackendBody,
    BackendElement,
    BackendFace,
    BackendInspection,
    BackendMesh,
    BackendMeshFace,
    BackendNode,
    GeometryMeshBackend,
    StepGeometryMeshAdapter,
)
from febio_cae.domain import (
    AsPlaced,
    BodyId,
    Budget,
    CaseRevision,
    CaseSpec,
    ContactId,
    ContactIntent,
    DofState,
    EvaluationRequest,
    EvidenceRef,
    FrameId,
    Frictionless,
    GeometryInspectionRequest,
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
    SourceAssetContent,
    SourceAssetRef,
    SupportComponent,
    SupportId,
    SupportSet,
    TimeIncrementPolicy,
    Translation3,
    UnitDirection,
    WholeBodyRule,
)
from febio_cae.domain.ports import MeshingPort
from febio_cae.domain.spatial import ProperRotation

API_AVAILABLE = True


WORLD = FrameId("World")
PART_LOCAL = FrameId("PartLocal")
TOOL_LOCAL = FrameId("ToolLocal")
PART_BODY = BodyId("part-body")
TOOL_BODY = BodyId("tool-body")
PART_GEOMETRY_DIGEST = "a" * 64
TOOL_GEOMETRY_DIGEST = "b" * 64
SYNTHETIC_STEP = (
    b"ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('synthetic'),'2;1');\n"
    b"ENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n"
)


def _evidence(target_field: str, seed: str) -> EvidenceRef:
    return EvidenceRef(
        schema_version="1",
        source_kind="registered_document",
        reference="P2InputSyntheticFixture:1",
        target_field=target_field,
        content_digest=hashlib.sha256(seed.encode("utf-8")).hexdigest(),
    )


def _identity_transform(source: FrameId, target: FrameId, z_mm: float = 0.0) -> RigidTransform:
    return RigidTransform(
        source_frame=source,
        target_frame=target,
        translation=Translation3(
            target,
            Quantity(0, "mm"),
            Quantity(0, "mm"),
            Quantity(z_mm, "mm"),
        ),
        rotation=ProperRotation(((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))),
    )


class SyntheticSourceResolver:
    def __init__(self, content: SourceAssetContent) -> None:
        self.content = content

    def resolve(self, source_asset: SourceAssetRef) -> SourceAssetContent:
        if source_asset != self.content.source_asset:
            raise ValueError("unknown synthetic source")
        return self.content


class SyntheticBackend:
    backend_id = "synthetic-step-backend"
    backend_version = "synthetic-1"

    def __init__(
        self,
        *,
        declared_units: tuple[str, ...] = ("mm",),
        unsupported_topology: tuple[str, ...] = (),
        defects: tuple[str, ...] = (),
        geometry_digest: str = PART_GEOMETRY_DIGEST,
        changed_measurement: bool = False,
        mesh_ordering_id: str = BACKEND_TET10_ORDER_ID,
        reverse_orientation: bool = False,
    ) -> None:
        self.declared_units = declared_units
        self.unsupported_topology = unsupported_topology
        self.defects = defects
        self.geometry_digest = geometry_digest
        self.changed_measurement = changed_measurement
        self.mesh_ordering_id = mesh_ordering_id
        self.reverse_orientation = reverse_orientation
        self.mesh_requests: list[tuple[str, float]] = []

    def inspect(
        self,
        content: bytes,
        requested_body_ids: tuple[str, ...],
    ) -> BackendInspection:
        centroid_z = 0.011 if self.changed_measurement else 0.01
        faces = (
            BackendFace(
                "bottom-face",
                PART_BODY.value,
                WORLD,
                1.0e-4,
                (0.0, 0.0, 0.0),
                attributes=("bottom", "support"),
                boundary_points_si=((0.0, 0.0, 0.0), (0.01, 0.0, 0.0), (0.0, 0.01, 0.0)),
            ),
            BackendFace(
                "top-face",
                PART_BODY.value,
                WORLD,
                1.0e-4,
                (0.0, 0.0, centroid_z),
                attributes=("top", "contact"),
                boundary_points_si=((0.0, 0.0, 0.01), (0.01, 0.0, 0.0), (0.0, 0.0, 0.01)),
            ),
            BackendFace(
                "side-face",
                PART_BODY.value,
                WORLD,
                1.0e-4,
                (0.005, 0.0, 0.005),
                attributes=("side",),
                boundary_points_si=((0.0, 0.0, 0.0), (0.01, 0.0, 0.0), (0.0, 0.0, 0.01)),
            ),
            BackendFace(
                "other-face",
                PART_BODY.value,
                WORLD,
                1.0e-4,
                (0.0, 0.005, 0.005),
                attributes=("other",),
                boundary_points_si=((0.0, 0.0, 0.0), (0.0, 0.01, 0.0), (0.0, 0.0, 0.01)),
            ),
        )
        body = BackendBody(
            PART_BODY.value,
            True,
            1.0e-6,
            faces,
            defects=self.defects,
        )
        return BackendInspection(
            source_digest=hashlib.sha256(content).hexdigest(),
            geometry_digest=self.geometry_digest,
            declared_units=self.declared_units,
            frame=WORLD,
            bodies=(body,),
            unsupported_topology=self.unsupported_topology,
            defects=self.defects,
        )

    def mesh(self, content: bytes, body_id: str, global_size_si: float) -> BackendMesh:
        self.mesh_requests.append((body_id, global_size_si))
        node_coordinates = (
            (0.0, 0.0, 0.0),
            (0.01, 0.0, 0.0),
            (0.0, 0.01, 0.0),
            (0.0, 0.0, 0.01),
            (0.005, 0.0, 0.0),
            (0.005, 0.005, 0.0),
            (0.0, 0.005, 0.0),
            (0.0, 0.0, 0.005),
            (0.005, 0.0, 0.005),
            (0.0, 0.005, 0.005),
        )
        node_ids = tuple(
            BackendNode(index + 1, coordinates)
            for index, coordinates in enumerate(node_coordinates)
        )
        element_node_ids = (1, 2, 3, 4, 5, 6, 7, 8, 10, 9)
        if self.reverse_orientation:
            element_node_ids = (2, 1, *element_node_ids[2:])
        element = BackendElement(
            element_id=1,
            element_type="tet10",
            node_ids=element_node_ids,
            body_id=body_id,
            ordering_id=self.mesh_ordering_id,
        )
        faces = tuple(
            BackendMeshFace(face_id, (1,), (local_face_id,), source_face_id=face_id)
            for face_id, local_face_id in (
                ("bottom-face", 0),
                ("side-face", 1),
                ("top-face", 2),
                ("other-face", 3),
            )
        )
        return BackendMesh(
            source_digest=hashlib.sha256(content).hexdigest(),
            geometry_digest=self.geometry_digest,
            frame=WORLD,
            body_id=body_id,
            nodes=node_ids,
            elements=(element,),
            faces=faces,
            ordering_id=self.mesh_ordering_id,
        )


@pytest.fixture
def source_content() -> SourceAssetContent:
    source_ref = SourceAssetRef(
        "synthetic-step-registered",
        hashlib.sha256(SYNTHETIC_STEP).hexdigest(),
        "model/step",
    )
    return SourceAssetContent(source_ref, SYNTHETIC_STEP)


@pytest.fixture
def synthetic_backend() -> Any:
    if not API_AVAILABLE:
        pytest.skip("geometry adapter API is not implemented")
    return SyntheticBackend()


@pytest.fixture
def adapter(source_content: SourceAssetContent, synthetic_backend: Any) -> Any:
    assert API_AVAILABLE
    assert isinstance(synthetic_backend, GeometryMeshBackend)
    return StepGeometryMeshAdapter(
        synthetic_backend,
        source_resolver=SyntheticSourceResolver(source_content),
        source_asset=source_content.source_asset,
    )


def _selection(
    name: str,
    role: str,
    geometry_digest: str,
    body: BodyId,
    rule: Any,
) -> SelectionRef:
    return SelectionRef(
        name=name,
        role=role,
        role_evidence=_evidence("selection.role", name),
        geometry_digest=geometry_digest,
        body_id=body,
        frame=WORLD,
        rule=rule,
    )


def make_case_spec(inspection_digest: str, *, primitive: RigidPrimitive | None = None) -> CaseSpec:
    part_surface = _selection(
        "part-contact",
        "part_contact_surface",
        PART_GEOMETRY_DIGEST,
        PART_BODY,
        WholeBodyRule(PART_BODY),
    )
    tool_surface = _selection(
        "tool-contact",
        "tool_contact_surface",
        TOOL_GEOMETRY_DIGEST,
        TOOL_BODY,
        WholeBodyRule(TOOL_BODY),
    )
    part_output = _selection(
        "part-output",
        "output_region",
        PART_GEOMETRY_DIGEST,
        PART_BODY,
        WholeBodyRule(PART_BODY),
    )
    tool_output = _selection(
        "tool-output",
        "output_region",
        TOOL_GEOMETRY_DIGEST,
        TOOL_BODY,
        WholeBodyRule(TOOL_BODY),
    )
    geometry = GeometryIntent(
        source_step_digest=hashlib.sha256(SYNTHETIC_STEP).hexdigest(),
        geometry_digest=PART_GEOMETRY_DIGEST,
        inspection_digest=inspection_digest,
        body_id=PART_BODY,
        step_unit="mm",
        placement=_identity_transform(WORLD, WORLD),
        body_evidence=_evidence("geometry.body_id", "geometry-body"),
        unit_evidence=_evidence("geometry.step_unit", "geometry-unit"),
        placement_evidence=_evidence("geometry.placement", "geometry-placement"),
    )
    applicability = MaterialApplicability(
        strain_statement="Synthetic strain range is supplied for component testing.",
        strain_evidence=_evidence("material.strain_applicability", "material-strain"),
        rate_statement="Synthetic rate range is supplied for component testing.",
        rate_evidence=_evidence("material.rate_applicability", "material-rate"),
    )
    material = IsotropicLinearElastic(
        youngs_modulus=Quantity(1, "MPa"),
        poisson_ratio=Quantity(0.3, "1"),
        model_evidence=_evidence("material.model", "material-model"),
        youngs_modulus_evidence=_evidence("material.youngs_modulus", "material-youngs"),
        poisson_ratio_evidence=_evidence("material.poisson_ratio", "material-poisson"),
        applicability=applicability,
    )
    support = SolidSupport(
        support_id=SupportId("support-main"),
        selection=_selection(
            "support-region",
            "support_surface",
            PART_GEOMETRY_DIGEST,
            PART_BODY,
            WholeBodyRule(PART_BODY),
        ),
        frame=WORLD,
        x=SupportComponent("fixed", _evidence("support.x", "support-x")),
        y=SupportComponent("free", _evidence("support.y", "support-y")),
        z=SupportComponent("fixed", _evidence("support.z", "support-z")),
        frame_evidence=_evidence("support.frame", "support-frame"),
    )
    primitive = primitive or RigidPrimitive(
        kind="box",
        body_id=TOOL_BODY,
        local_frame=TOOL_LOCAL,
        placement=_identity_transform(TOOL_LOCAL, WORLD, z_mm=20.0),
        dimensions={name: Quantity(4, "mm") for name in ("length", "width", "height")},
        dimension_evidence={
            name: _evidence(f"rigid_tool.{name}", f"tool-{name}")
            for name in ("length", "width", "height")
        },
        model_evidence=_evidence("rigid_tool.model", "tool-model"),
        placement_evidence=_evidence("rigid_tool.placement", "tool-placement"),
    )
    dofs = RigidDofSpecification(
        frame=WORLD,
        x=RigidDofComponent(DofState.FIXED, _evidence("rigid_tool.x", "dof-x")),
        y=RigidDofComponent(DofState.FIXED, _evidence("rigid_tool.y", "dof-y")),
        z=RigidDofComponent(DofState.PRESCRIBED, _evidence("rigid_tool.z", "dof-z")),
        rx=RigidDofComponent(DofState.FIXED, _evidence("rigid_tool.rx", "dof-rx")),
        ry=RigidDofComponent(DofState.FIXED, _evidence("rigid_tool.ry", "dof-ry")),
        rz=RigidDofComponent(DofState.FIXED, _evidence("rigid_tool.rz", "dof-rz")),
        frame_evidence=_evidence("rigid_tool.frame", "dof-frame"),
    )
    rigid_tool = RigidToolIntent(
        primitive=primitive,
        contact_surface=tool_surface,
        dofs=dofs,
        contact_surface_evidence=_evidence("rigid_tool.contact_surface", "tool-surface"),
    )
    motion = MotionProfile(
        direction=UnitDirection(WORLD, 0.0, 0.0, 1.0),
        initial_reference_point=Point3(
            WORLD,
            Quantity(0, "mm"),
            Quantity(0, "mm"),
            Quantity(0, "mm"),
        ),
        samples=(
            MotionSample(Quantity(0, "s"), Quantity(0, "mm")),
            MotionSample(Quantity(1, "s"), Quantity(1, "mm")),
        ),
        applicability=MotionApplicability(
            quasi_static_statement="Synthetic loading is quasi-static.",
            quasi_static_evidence=_evidence("motion.quasi_static_applicability", "motion-qs"),
            rate_independent_statement="Synthetic response is rate-independent.",
            rate_independent_evidence=_evidence(
                "motion.rate_independent_applicability", "motion-ri"
            ),
        ),
        direction_evidence=_evidence("motion.direction", "motion-direction"),
        initial_reference_point_evidence=_evidence(
            "motion.initial_reference_point", "motion-reference"
        ),
        history_evidence=_evidence("motion.history", "motion-history"),
    )
    contact = ContactIntent(
        contact_id=ContactId("contact-main"),
        part_surface=part_surface,
        tool_surface=tool_surface,
        pair_frame=WORLD,
        part_surface_evidence=_evidence("contact.part_surface", "contact-part"),
        tool_surface_evidence=_evidence("contact.tool_surface", "contact-tool"),
        pair_frame_evidence=_evidence("contact.frame", "contact-frame"),
        friction=Frictionless(_evidence("contact.friction_model", "contact-friction")),
        arrangement=AsPlaced(_evidence("contact.arrangement", "contact-arrangement")),
    )
    mesh_policy = MeshPolicy(
        element_type="tet10",
        global_size=Quantity(2, "mm"),
        local_refinements=(),
        quality_profile=NumericalProfileRef("mesh_profile", "mesh_quality", "c" * 64),
        max_refinements=0,
    )
    solver_policy = SolverPolicy(
        profile=NumericalProfileRef("solver_profile", "solver", "d" * 64),
        controls=(SolverControl("alpha", 1),),
        increments=TimeIncrementPolicy(
            initial_step=Quantity(100, "ms"),
            minimum_step=Quantity(10, "ms"),
            maximum_step=Quantity(1, "s"),
            adaptive=True,
            max_steps=20,
            max_step_retries=2,
            must_points=(Quantity(0, "s"),),
        ),
        retry_recipe_ids=("retry_primary",),
    )
    output_policy = OutputPolicy(
        profile=NumericalProfileRef("outputs_profile", "outputs", "e" * 64),
        requests=(
            OutputRequest(
                request_id="request_part",
                quantity_id="displacement",
                measure_id="max",
                component_id="z",
                location="node",
                selection=part_output,
                frame=WORLD,
                display_unit="mm",
                evidence=_evidence("outputs.requests.request_part", "output-part"),
            ),
            OutputRequest(
                request_id="request_tool",
                quantity_id="contact_force",
                measure_id="max",
                component_id="z",
                location="rigid_body",
                selection=tool_output,
                frame=WORLD,
                display_unit="N",
                evidence=_evidence("outputs.requests.request_tool", "output-tool"),
            ),
        ),
        saved_times=(Quantity(0, "s"), Quantity(1, "s")),
        evaluations=(
            EvaluationRequest(
                evaluation_id="evaluation_part",
                output_request_id="request_part",
                aggregation_id="peak",
                selection=part_output,
                state_times=(Quantity(0, "s"), Quantity(1, "s")),
                evidence=_evidence("outputs.evaluations.evaluation_part", "evaluation-part"),
            ),
            EvaluationRequest(
                evaluation_id="evaluation_tool",
                output_request_id="request_tool",
                aggregation_id="peak",
                selection=tool_output,
                state_times=(Quantity(0, "s"), Quantity(1, "s")),
                evidence=_evidence("outputs.evaluations.evaluation_tool", "evaluation-tool"),
            ),
        ),
    )
    quality_policy = QualityPolicy(
        profile=NumericalProfileRef("quality_profile", "quality", "f" * 64),
        criteria=(
            QualityCriterion(
                criterion_id="criterion_main",
                metric_id="residual_norm",
                evaluation_ids=("evaluation_part", "evaluation_tool"),
                thresholds=(QualityThreshold("absolute_tolerance", Quantity(1, "MPa")),),
                applicability_reason="Synthetic structural quality criterion.",
                evidence=_evidence("quality_policy.criteria.criterion_main", "quality"),
            ),
        ),
    )
    return CaseSpec(
        geometry=geometry,
        material=material,
        support=SupportSet((support,)),
        rigid_tool=rigid_tool,
        motion=motion,
        contact=contact,
        mesh_policy=mesh_policy,
        solver_policy=solver_policy,
        outputs=output_policy,
        quality_policy=quality_policy,
        budget=Budget(
            max_elapsed=Quantity(10, "s"),
            max_attempts=2,
            cpu_workers=1,
            max_llm_calls=0,
            max_llm_tokens=0,
        ),
    )


@pytest.fixture
def synthetic_case_revision(adapter: Any, source_content: SourceAssetContent) -> CaseRevision:
    inspection = adapter.inspect(
        GeometryInspectionRequest(source_content.source_asset),
        source_content,
    )
    return CaseRevision(
        case_id="case-input-model",
        revision_id="revision-input-model",
        parent_revision_id=None,
        parent_spec_digest=None,
        spec=make_case_spec(inspection.inspection_digest),
        evidence=(_evidence("case_revision.spec", "case-revision"),),
    )


@pytest.fixture
def mesh_port(adapter: Any) -> Any:
    assert isinstance(adapter, MeshingPort)
    return adapter
