from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from febio_cae.domain import (
    AsPlaced,
    BodyId,
    Budget,
    CaseRevision,
    CaseSpec,
    CapabilityRef,
    CapabilityStatus,
    CompatibilityProfile,
    ContactId,
    ContactIntent,
    DofState,
    EvaluationRequest,
    EvidenceRef,
    ExecutionBundle,
    ExecutionSetting,
    FrameId,
    GeometryIntent,
    IsotropicLinearElastic,
    MaterialApplicability,
    MeshArtifact,
    MeshElement,
    MeshFace,
    MeshNode,
    MeshPolicy,
    MeshProvenance,
    MeshQualityRecord,
    MeshSet,
    MotionApplicability,
    MotionProfile,
    MotionSample,
    NumericalProfileRef,
    OutputMapping,
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
    SourceAssetRef,
    SupportComponent,
    SupportId,
    SupportSet,
    TimeIncrementPolicy,
    ToolIdentity,
    Translation3,
    UnitDirection,
    WholeBodyRule,
)
from febio_cae.domain.artifacts import FileEntry
from febio_cae.domain.spatial import ProperRotation


WORLD = FrameId("World")
PART_BODY = BodyId("part-body")
TOOL_BODY = BodyId("tool-body")
PART_DIGEST = "a" * 64
TOOL_DIGEST = "b" * 64


def evidence(target_field: str, seed: str) -> EvidenceRef:
    return EvidenceRef(
        schema_version="1",
        source_kind="registered_document",
        reference="P3SyntheticFixture:1",
        target_field=target_field,
        content_digest=hashlib.sha256(seed.encode("utf-8")).hexdigest(),
    )


def identity_transform(source: FrameId, target: FrameId) -> RigidTransform:
    return RigidTransform(
        source_frame=source,
        target_frame=target,
        translation=Translation3(target, Quantity(0, "m"), Quantity(0, "m"), Quantity(0, "m")),
        rotation=ProperRotation(((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))),
    )


def selection(name: str, role: str, digest: str, body: BodyId, seed: str) -> SelectionRef:
    return SelectionRef(
        name=name,
        role=role,
        role_evidence=evidence("selection.role", seed),
        geometry_digest=digest,
        body_id=body,
        frame=WORLD,
        rule=WholeBodyRule(body),
    )


def selection_digest(value: SelectionRef) -> str:
    return hashlib.sha256(value.to_bytes()).hexdigest()


def make_case_spec() -> CaseSpec:
    part_contact = selection("part-contact", "part_contact_surface", PART_DIGEST, PART_BODY, "part")
    tool_contact = selection("tool-contact", "tool_contact_surface", TOOL_DIGEST, TOOL_BODY, "tool")
    support_selection = selection("support-region", "support_surface", PART_DIGEST, PART_BODY, "support")
    output_part = selection("part-output", "output_region", PART_DIGEST, PART_BODY, "output-part")
    output_tool = selection("tool-output", "output_region", TOOL_DIGEST, TOOL_BODY, "output-tool")

    geometry = GeometryIntent(
        source_step_digest="1" * 64,
        geometry_digest=PART_DIGEST,
        inspection_digest="2" * 64,
        body_id=PART_BODY,
        step_unit="mm",
        placement=identity_transform(FrameId("PartLocal"), WORLD),
        body_evidence=evidence("geometry.body_id", "geometry-body"),
        unit_evidence=evidence("geometry.step_unit", "geometry-unit"),
        placement_evidence=evidence("geometry.placement", "geometry-placement"),
    )
    material = IsotropicLinearElastic(
        youngs_modulus=Quantity(1.0, "MPa"),
        poisson_ratio=Quantity(0.3, "1"),
        model_evidence=evidence("material.model", "material-model"),
        youngs_modulus_evidence=evidence("material.youngs_modulus", "material-E"),
        poisson_ratio_evidence=evidence("material.poisson_ratio", "material-nu"),
        applicability=MaterialApplicability(
            strain_statement="synthetic small-strain profile",
            strain_evidence=evidence("material.strain_applicability", "material-strain"),
            rate_statement="synthetic rate-independent profile",
            rate_evidence=evidence("material.rate_applicability", "material-rate"),
        ),
    )
    support = SupportSet(
        (
            SolidSupport(
                support_id=SupportId("support-main"),
                selection=support_selection,
                frame=WORLD,
                x=SupportComponent("fixed", evidence("support.x", "support-x")),
                y=SupportComponent("fixed", evidence("support.y", "support-y")),
                z=SupportComponent("fixed", evidence("support.z", "support-z")),
                frame_evidence=evidence("support.frame", "support-frame"),
            ),
        )
    )
    primitive = RigidPrimitive(
        kind="box",
        body_id=TOOL_BODY,
        local_frame=FrameId("ToolLocal"),
        placement=identity_transform(FrameId("ToolLocal"), WORLD),
        dimensions={
            "length": Quantity(2.0, "mm"),
            "width": Quantity(2.0, "mm"),
            "height": Quantity(2.0, "mm"),
        },
        dimension_evidence={
            "length": evidence("rigid_tool.length", "tool-length"),
            "width": evidence("rigid_tool.width", "tool-width"),
            "height": evidence("rigid_tool.height", "tool-height"),
        },
        model_evidence=evidence("rigid_tool.model", "tool-model"),
        placement_evidence=evidence("rigid_tool.placement", "tool-placement"),
    )
    dofs = RigidDofSpecification(
        frame=WORLD,
        x=RigidDofComponent(DofState.FIXED, evidence("rigid_tool.x", "dof-x")),
        y=RigidDofComponent(DofState.FIXED, evidence("rigid_tool.y", "dof-y")),
        z=RigidDofComponent(DofState.PRESCRIBED, evidence("rigid_tool.z", "dof-z")),
        rx=RigidDofComponent(DofState.FIXED, evidence("rigid_tool.rx", "dof-rx")),
        ry=RigidDofComponent(DofState.FIXED, evidence("rigid_tool.ry", "dof-ry")),
        rz=RigidDofComponent(DofState.FIXED, evidence("rigid_tool.rz", "dof-rz")),
        frame_evidence=evidence("rigid_tool.frame", "dof-frame"),
    )
    rigid_tool = RigidToolIntent(
        primitive=primitive,
        contact_surface=tool_contact,
        dofs=dofs,
        contact_surface_evidence=evidence("rigid_tool.contact_surface", "tool-contact-evidence"),
    )
    motion = MotionProfile(
        direction=UnitDirection(WORLD, 0.0, 0.0, 1.0),
        initial_reference_point=Point3(
            WORLD, Quantity(0.0, "mm"), Quantity(0.0, "mm"), Quantity(0.0, "mm")
        ),
        samples=(
            MotionSample(Quantity(0.0, "s"), Quantity(0.0, "mm")),
            MotionSample(Quantity(1.0, "s"), Quantity(0.1, "mm")),
        ),
        applicability=MotionApplicability(
            quasi_static_statement="synthetic quasi-static profile",
            quasi_static_evidence=evidence("motion.quasi_static_applicability", "motion-qs"),
            rate_independent_statement="synthetic rate-independent profile",
            rate_independent_evidence=evidence("motion.rate_independent_applicability", "motion-rate"),
        ),
        direction_evidence=evidence("motion.direction", "motion-direction"),
        initial_reference_point_evidence=evidence(
            "motion.initial_reference_point", "motion-reference"
        ),
        history_evidence=evidence("motion.history", "motion-history"),
    )
    contact = ContactIntent(
        contact_id=ContactId("contact-main"),
        part_surface=part_contact,
        tool_surface=tool_contact,
        pair_frame=WORLD,
        part_surface_evidence=evidence("contact.part_surface", "contact-part"),
        tool_surface_evidence=evidence("contact.tool_surface", "contact-tool"),
        pair_frame_evidence=evidence("contact.frame", "contact-frame"),
        friction=__import__("febio_cae.domain", fromlist=["Frictionless"]).Frictionless(
            evidence("contact.friction_model", "contact-friction")
        ),
        arrangement=AsPlaced(evidence("contact.arrangement", "contact-arrangement")),
    )
    mesh_policy = MeshPolicy(
        element_type="tet10",
        global_size=Quantity(1.0, "mm"),
        local_refinements=(),
        quality_profile=NumericalProfileRef("mesh-profile", "mesh_quality", "c" * 64),
        max_refinements=0,
    )
    solver_policy = SolverPolicy(
        profile=NumericalProfileRef("solver-profile", "solver", "d" * 64),
        controls=(SolverControl("alpha", 1),),
        increments=TimeIncrementPolicy(
            initial_step=Quantity(0.1, "s"),
            minimum_step=Quantity(0.01, "s"),
            maximum_step=Quantity(1.0, "s"),
            adaptive=True,
            max_steps=10,
            max_step_retries=1,
            must_points=(),
        ),
        retry_recipe_ids=("retry_primary",),
    )
    outputs = OutputPolicy(
        profile=NumericalProfileRef("outputs-profile", "outputs", "e" * 64),
        requests=(
            OutputRequest(
                request_id="request_part",
                quantity_id="displacement",
                measure_id="value",
                component_id="z",
                location="node",
                selection=output_part,
                frame=WORLD,
                display_unit="m",
                evidence=evidence("outputs.requests.request_part", "output-part-request"),
            ),
            OutputRequest(
                request_id="request_tool",
                quantity_id="contact_force",
                measure_id="value",
                component_id="z",
                location="rigid_body",
                selection=output_tool,
                frame=WORLD,
                display_unit="N",
                evidence=evidence("outputs.requests.request_tool", "output-tool-request"),
            ),
        ),
        saved_times=(Quantity(0.0, "s"), Quantity(1.0, "s")),
        evaluations=(
            EvaluationRequest(
                evaluation_id="evaluation_main",
                output_request_id="request_part",
                aggregation_id="peak",
                selection=output_part,
                state_times=(Quantity(0.0, "s"), Quantity(1.0, "s")),
                evidence=evidence("outputs.evaluations.evaluation_main", "evaluation-main"),
            ),
        ),
    )
    quality = QualityPolicy(
        profile=NumericalProfileRef("quality-profile", "quality", "f" * 64),
        criteria=(
            QualityCriterion(
                criterion_id="criterion_displacement",
                metric_id="peak_abs_value",
                evaluation_ids=("evaluation_main",),
                thresholds=(QualityThreshold("max_value", Quantity(1.0, "m")),),
                applicability_reason="synthetic numeric profile only",
                evidence=evidence("quality_policy.criteria.criterion_displacement", "quality"),
            ),
        ),
    )
    return CaseSpec(
        geometry=geometry,
        material=material,
        support=support,
        rigid_tool=rigid_tool,
        motion=motion,
        contact=contact,
        mesh_policy=mesh_policy,
        solver_policy=solver_policy,
        outputs=outputs,
        quality_policy=quality,
        budget=Budget(Quantity(10.0, "s"), 2, 1, 0, 0),
    )


def make_revision() -> CaseRevision:
    return CaseRevision(
        case_id="case-p3",
        revision_id="revision-p3",
        parent_revision_id=None,
        parent_spec_digest=None,
        spec=make_case_spec(),
        evidence=(evidence("case_revision.spec", "revision"),),
    )


def make_mesh(spec: CaseSpec) -> MeshArtifact:
    selections = (
        spec.contact.part_surface,
        spec.contact.tool_surface,
        spec.support.supports[0].selection,
        spec.outputs.requests[0].selection,
        spec.outputs.requests[1].selection,
    )
    selection_digests = tuple(selection_digest(item) for item in selections)
    nodes = tuple(
        MeshNode(index, (float((index - 1) % 10) * 0.001, 0.0, 0.001))
        for index in range(1, 11)
    ) + tuple(
        MeshNode(index, (float((index - 11) % 10) * 0.001, 0.0, 0.002))
        for index in range(11, 21)
    )
    elements = (
        MeshElement(1, "tet10", tuple(range(1, 11)), PART_BODY.value),
        MeshElement(2, "tet10", tuple(range(11, 21)), TOOL_BODY.value),
    )
    faces = (
        MeshFace("part-face", PART_BODY.value, (1, 3, 2, 7, 6, 5), (1,), (0,)),
        MeshFace("tool-face", TOOL_BODY.value, (11, 13, 12, 17, 16, 15), (2,), (0,)),
    )
    source_for = {selection.name: selection_digest(selection) for selection in selections}
    sets = (
        MeshSet("part-body", "body", PART_BODY.value, (PART_BODY.value,), source_for["part-contact"]),
        MeshSet("tool-body", "body", TOOL_BODY.value, (TOOL_BODY.value,), source_for["tool-contact"]),
        MeshSet("part-elements", "element", PART_BODY.value, (1,), source_for["part-contact"]),
        MeshSet("tool-elements", "element", TOOL_BODY.value, (2,), source_for["tool-contact"]),
        MeshSet("part-contact", "face", PART_BODY.value, ("part-face",), source_for["part-contact"]),
        MeshSet("tool-contact", "face", TOOL_BODY.value, ("tool-face",), source_for["tool-contact"]),
        MeshSet("support-region", "face", PART_BODY.value, ("part-face",), source_for["support-region"]),
        MeshSet("part-output", "node", PART_BODY.value, tuple(range(1, 11)), source_for["part-output"]),
        MeshSet("tool-output", "node", TOOL_BODY.value, tuple(range(11, 21)), source_for["tool-output"]),
    )
    return MeshArtifact(
        artifact_id="mesh-p3",
        frame=WORLD,
        provenance=MeshProvenance(
            source_geometry_digest=spec.geometry.geometry_digest,
            source_body_ids=(PART_BODY.value, TOOL_BODY.value),
            source_selection_digests=selection_digests,
            mesh_recipe_digest="9" * 64,
            tool_id="synthetic-mesher",
            tool_version="1",
            mapping_id="tet10-canonical-v1",
            node_ordering_id="tet10-canonical-v1",
            face_ordering_id="tet10-face-canonical-v1",
        ),
        nodes=nodes,
        elements=elements,
        faces=faces,
        sets=sets,
        quality_records=(MeshQualityRecord("jacobian", 0.5, "1", 0.1, "PASS", "synthetic"),),
    )


REQUIRED_CAPABILITIES = (
    "febio.material.isotropic_linear_elastic",
    "febio.mesh.tet10",
    "febio.contact.sliding_elastic",
    "febio.contact.primary_tool_secondary_part",
    "febio.rigid_body",
    "febio.support",
    "febio.motion",
    "febio.output.xplt",
)


def make_profile(executable: str | Path | None = None) -> CompatibilityProfile:
    executable_path = Path(executable or sys.executable)
    digest = hashlib.sha256(executable_path.read_bytes()).hexdigest()
    solver = ToolIdentity("febio-synthetic", "4.12.0", digest)
    reader = ToolIdentity("febio-xplt-reader", "xplt-0x35-v1", "e" * 64)
    capabilities = tuple(
        CapabilityRef(
            capability_id=capability_id,
            status=CapabilityStatus.SUPPORTED,
            version="4.12.0/0x35",
            compression="none",
            ordering_id="tet10-canonical-v1",
            sign_mapping_id="reaction-explicit-v1",
            evidence=(evidence("compatibility.profile", capability_id),),
        )
        for capability_id in REQUIRED_CAPABILITIES
    )
    mappings = (
        OutputMapping(
            "displacement", "displacement", "node", "VEC3F", "m", WORLD, 1, 1, "value"
        ),
        OutputMapping(
            "contact_force", "reaction forces", "rigid_body", "VEC3F", "N", WORLD, -1, 1, "value"
        ),
    )
    return CompatibilityProfile(
        profile_id="profile-p3-xplt-0x35",
        solver=solver,
        reader=reader,
        capabilities=capabilities,
        output_mappings=mappings,
        evidence=(evidence("compatibility.profile", "profile"),),
    )


def make_xplt_fixture(
    *, attempt_id: str, bundle_digest: str, mesh_digest: str, values: dict[str, Any] | None = None
) -> bytes:
    """Build the bounded binary XPLT 0x35 fixture used by component tests.

    The fixture uses the observed FEBio block framing: the file identifier is a
    bare DWORD, followed by identifier/size/payload blocks.  The adapter owns
    the small version-specific payload subset; native qualification remains
    separate.
    """

    import struct

    def block(identifier: int, payload: bytes) -> bytes:
        return struct.pack("<II", identifier, len(payload)) + payload

    def text(value: str) -> bytes:
        raw = value.encode("utf-8")
        return struct.pack("<I", len(raw)) + raw

    def field(identifier: int, payload: bytes) -> bytes:
        return block(identifier, payload)

    header = b"".join(
        (
            field(0x01010001, struct.pack("<I", 0x35)),
            field(0x01010002, struct.pack("<I", 0)),
            field(0x01010004, text("FEBio 4.12.0")),
            field(0x01010005, text("SI")),
            field(0x01010006, text(attempt_id)),
            field(0x01010007, text(bundle_digest)),
            field(0x01010008, text(mesh_digest)),
        )
    )
    dictionary = (
        field(
            0x01020001,
            b"".join(
                (
                    field(0x01020002, struct.pack("<I", 3)),
                    field(0x01020003, struct.pack("<I", 0)),
                    field(0x01020004, text("displacement")),
                    field(0x01020005, text("node")),
                    field(0x01020006, text("VEC3F")),
                    field(0x01020007, text("m")),
                )
            ),
        )
        + field(
            0x01020001,
            b"".join(
                (
                    field(0x01020002, struct.pack("<I", 3)),
                    field(0x01020003, struct.pack("<I", 0)),
                    field(0x01020004, text("reaction forces")),
                    field(0x01020005, text("rigid_body")),
                    field(0x01020006, text("VEC3F")),
                    field(0x01020007, text("N")),
                )
            ),
        )
    )
    root = block(0x01000000, block(0x01010000, header) + block(0x01020000, dictionary))

    node_ids = (1, 2)
    mesh_payload = block(
        0x01041000,
        struct.pack("<II", len(node_ids), 3)
        + b"".join(struct.pack("<Ifff", node_id, float(node_id), 0.0, 0.0) for node_id in node_ids),
    )
    mesh_payload += block(0x01042000, struct.pack("<I", 0))
    mesh = block(0x01040000, mesh_payload)

    displacement = values or {
        "displacement": (
            ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0)),
            ((0.0, 0.0, 0.1), (0.0, 0.0, 0.2)),
        ),
        "reaction forces": (
            ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0)),
            ((0.0, 0.0, 1.0), (0.0, 0.0, 2.0)),
        ),
    }
    states: list[bytes] = []
    for state_index, time_value in enumerate((0.0, 1.0)):
        state_data = b""
        for name in ("displacement", "reaction forces"):
            rows = displacement[name][state_index]
            payload = struct.pack("<II", 0, len(rows) * 3 * 4) + b"".join(
                struct.pack("<fff", *row) for row in rows
            )
            state_data += block(0x02030001 if name == "displacement" else 0x02030002, payload)
        states.append(
            block(
                0x02000000,
                block(0x02010000, struct.pack("<dI", time_value, state_index))
                + block(0x02030000, state_data),
            )
        )
    payload = struct.pack("<I", 0x00464542) + root + mesh + b"".join(states)
    # The FEBio identifier is a bare DWORD and is not a length-prefixed block.
    return payload


def make_bundle(*, bundle_id: str, case_id: str, revision_id: str, spec_digest: str, mesh_digest: str,
                profile_id: str, root: Path, file_entry: FileEntry, argv: tuple[str, ...]) -> ExecutionBundle:
    return ExecutionBundle(
        bundle_id=bundle_id,
        case_id=case_id,
        revision_id=revision_id,
        spec_digest=spec_digest,
        mesh_digest=mesh_digest,
        profile_id=profile_id,
        tool=make_profile().solver,
        files=(file_entry,),
        argv=argv,
        cwd=str(root),
        thread_count=1,
        settings=(ExecutionSetting("solver_threads", 1),),
    )


def write_json(value: object, path: Path) -> None:
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
