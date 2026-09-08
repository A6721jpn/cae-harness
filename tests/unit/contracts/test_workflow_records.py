# mypy: disable-error-code="no-redef,assignment,misc"

from __future__ import annotations

import hashlib
import inspect
from typing import Any

import pytest
from workflow_fixtures import case_revision, evidence

from febio_cae.domain import CaseRevision, FrameId

try:
    from febio_cae.domain.artifacts import (
        FileEntry,
        GeometryBodyFact,
        GeometryInspection,
        GeometryInspectionRequest,
        MeshArtifact,
        MeshElement,
        MeshFace,
        MeshNode,
        MeshProvenance,
        MeshQualityRecord,
        MeshSet,
        SourceAssetRef,
    )
    from febio_cae.domain.comparison import ComparisonAxis, ComparisonInterval, ComparisonSpec
    from febio_cae.domain.compatibility import (
        CapabilityRef,
        CapabilityStatus,
        CompatibilityProfile,
        OutputMapping,
        ToolIdentity,
    )
    from febio_cae.domain.execution import (
        AttemptRecord,
        ExecutionBundle,
        ExecutionSetting,
        ProcessIdentity,
    )
    from febio_cae.domain.lifecycle import (
        CasePreparationState,
        PreviewStatus,
        RunState,
        ServiceExitCode,
        TaskStatus,
        transition_allowed,
    )
    from febio_cae.domain.preview import PreviewReceipt, PreviewRequest
    from febio_cae.domain.results import (
        AssessmentStatus,
        CriterionAssessment,
        MeasuredValue,
        NumericResultData,
        OutputObservation,
        QualityAssessment,
        ReadResult,
        ReadStatus,
        ResultManifest,
    )

    ARTIFACTS_API: Any = True
    COMPATIBILITY_API: Any = True
    COMPARISON_API: Any = True
    EXECUTION_API: Any = True
    LIFECYCLE_API: Any = True
    PREVIEW_API: Any = True
    RESULTS_API: Any = True
except ImportError:
    FileEntry = None
    GeometryInspection = None
    GeometryInspectionRequest = None
    MeshArtifact = None
    MeshElement = None
    MeshFace = None
    MeshNode = None
    MeshProvenance = None
    MeshQualityRecord = None
    MeshSet = None
    SourceAssetRef = None
    CapabilityRef = None
    CapabilityStatus = None
    CompatibilityProfile = None
    OutputMapping = None
    ToolIdentity = None
    ComparisonAxis = None
    ComparisonSpec = None
    AttemptRecord = None
    ExecutionBundle = None
    ExecutionSetting = None
    ProcessIdentity = None
    CasePreparationState = None
    PreviewStatus = None
    RunState = None
    ServiceExitCode = None
    TaskStatus = None
    transition_allowed = None
    PreviewReceipt = None
    PreviewRequest = None
    AssessmentStatus = None
    CriterionAssessment = None
    MeasuredValue = None
    OutputObservation = None
    QualityAssessment = None
    ReadResult = None
    ReadStatus = None
    ResultManifest = None
    ARTIFACTS_API: Any = None
    COMPATIBILITY_API: Any = None
    COMPARISON_API: Any = None
    EXECUTION_API: Any = None
    LIFECYCLE_API: Any = None
    PREVIEW_API: Any = None
    RESULTS_API: Any = None

try:
    from febio_cae.domain.artifacts import (
        TET10_CORNER_NODE_POSITIONS,
        TET10_EDGE_NODE_POSITIONS,
        TET10_FACE_NODE_POSITIONS,
        GeometrySelectionRequest,
        SourceAssetContent,
    )
    from febio_cae.domain.results import NumericResultData, ResultDataRef

    REPAIR_ARTIFACTS_API: Any = True
    REPAIR_RESULTS_API: Any = True
except ImportError:
    GeometrySelectionRequest = None
    SourceAssetContent = None
    TET10_CORNER_NODE_POSITIONS = None
    TET10_EDGE_NODE_POSITIONS = None
    TET10_FACE_NODE_POSITIONS = None
    NumericResultData = None
    ResultDataRef = None
    REPAIR_ARTIFACTS_API: Any = None
    REPAIR_RESULTS_API: Any = None


def _require_api() -> None:
    if any(
        api is None
        for api in (
            globals().get("ARTIFACTS_API"),
            globals().get("COMPATIBILITY_API"),
            globals().get("COMPARISON_API"),
            globals().get("EXECUTION_API"),
            globals().get("LIFECYCLE_API"),
            globals().get("PREVIEW_API"),
            globals().get("RESULTS_API"),
        )
    ):
        pytest.skip("P1 common workflow record API is not implemented")


def _mesh() -> Any:
    return MeshArtifact(
        artifact_id="mesh-interface",
        frame=FrameId("World"),
        provenance=MeshProvenance(
            source_geometry_digest="a" * 64,
            source_body_ids=("part-body",),
            source_selection_digests=("b" * 64,),
            mesh_recipe_digest="c" * 64,
            tool_id="gmsh",
            tool_version="4.12.0",
            mapping_id="tet10-gmsh-febio-v1",
            node_ordering_id="tet10-canonical-v1",
            face_ordering_id="tet10-face-canonical-v1",
        ),
        nodes=tuple(MeshNode(index, (float(index), 0.0, 0.0)) for index in range(1, 11)),
        elements=(MeshElement(1, "tet10", tuple(range(1, 11)), "part-body"),),
        faces=(MeshFace("face-1", "part-body", (1, 3, 2, 7, 6, 5), (1,), (0,)),),
        sets=(
            MeshSet("body-set", "body", "part-body", ("part-body",), "b" * 64),
            MeshSet("face-set", "face", "part-body", ("face-1",), "b" * 64),
        ),
        quality_records=(
            MeshQualityRecord("jacobian", 0.5, "1", 0.0, AssessmentStatus.PASS, "synthetic"),
        ),
    )


def _profile() -> Any:
    solver = ToolIdentity("febio", "4.12.0", "d" * 64)
    reader = ToolIdentity("xplt-reader", "1.0", "e" * 64)
    capability = CapabilityRef(
        "xplt-v1",
        CapabilityStatus.UNVERIFIED,
        "4.12.0",
        "none",
        "tet10-canonical-v1",
        "reaction-sign-v1",
        (evidence("compatibility.xplt", "profile"),),
    )
    mapping = OutputMapping(
        "displacement",
        "displacement",
        "node",
        "VEC3F",
        "m",
        FrameId("World"),
        1,
        1,
        "value",
    )
    return CompatibilityProfile(
        profile_id="febio-profile",
        solver=solver,
        reader=reader,
        capabilities=(capability,),
        output_mappings=(mapping,),
        evidence=(evidence("compatibility.profile", "profile-evidence"),),
    )


def _bundle(revision: CaseRevision, mesh: Any, profile: Any) -> Any:
    return ExecutionBundle(
        bundle_id="bundle-interface",
        case_id=revision.case_id,
        revision_id=revision.revision_id,
        spec_digest=revision.spec_digest,
        mesh_digest=mesh.artifact_digest,
        profile_id=profile.profile_id,
        tool=profile.solver,
        files=(FileEntry("input/case.feb", "f" * 64, 128, "input"),),
        argv=("febio4.exe", "-i", "case.feb", "-o", "case.xplt"),
        cwd="C:/registered-case/runs/attempt-interface",
        thread_count=1,
        settings=(ExecutionSetting("solver_threads", 1),),
    )


def _attempt(bundle: Any, revision: CaseRevision) -> Any:
    process = ProcessIdentity(
        executable="febio4.exe",
        executable_digest="6" * 64,
        argv=bundle.argv,
        cwd=bundle.cwd,
        thread_count=bundle.thread_count,
        start_marker="process-start-interface",
    )
    return AttemptRecord(
        attempt_id="attempt-interface",
        run_id="run-interface",
        case_id=revision.case_id,
        revision_id=revision.revision_id,
        owner_generation=4,
        bundle_digest=bundle.bundle_digest,
        state=RunState.CREATED,
        process=process,
        settings=bundle.settings,
    )


def _manifest(attempt: Any, bundle: Any, profile: Any) -> Any:
    data_ref: Any = None
    if ResultDataRef is not None:
        data_ref = ResultDataRef(
            "displacement-data",
            "a" * 64,
            "numeric-result-v1",
            "output/case.xplt",
            bundle.bundle_digest,
            attempt.attempt_id,
        )
    observation = OutputObservation(
        output_id="displacement",
        location="node",
        value_type="VEC3F",
        unit="m",
        frame=FrameId("World"),
        measure_id="value",
        state_count=2,
        data_ref=data_ref,
    )
    read_result = ReadResult(
        status=ReadStatus.VALIDATED,
        reader=profile.reader,
        observations=(observation,),
        diagnostics=(),
    )
    return ResultManifest(
        manifest_id="manifest-interface",
        attempt_id=attempt.attempt_id,
        bundle_digest=bundle.bundle_digest,
        files=(FileEntry("output/case.xplt", "9" * 64, 256, "xplt"),),
        read_result=read_result,
    )


def test_workflow_record_api_is_available() -> None:
    required = (
        MeshArtifact,
        ExecutionBundle,
        AttemptRecord,
        ResultManifest,
        QualityAssessment,
        PreviewReceipt,
        ComparisonSpec,
        GeometryInspectionRequest,
    )
    assert all(required), "P1 common workflow record API is not available"


def test_geometry_inspection_has_no_case_revision_dependency() -> None:
    _require_api()
    request = GeometryInspectionRequest(
        SourceAssetRef("step-source", "1" * 64, "model/step"),
        requested_body_ids=(),
    )
    inspection = GeometryInspection(
        request.source_asset,
        "2" * 64,
        "mm",
        ("part-body",),
        ("part-body",),
        (GeometryBodyFact("part-body", 4, 1.0e-9),),
    )
    assert "revision" not in inspect.signature(type(request)).parameters
    assert inspection.source_asset == request.source_asset
    assert inspection.body_facts[0].face_count == 4


def test_mesh_artifact_validates_tet10_structure_and_detaches_sequences() -> None:
    _require_api()
    mesh = _mesh()
    assert mesh.elements[0].node_ids == tuple(range(1, 11))
    assert mesh.provenance.mapping_id == "tet10-gmsh-febio-v1"
    assert mesh.to_bytes() == mesh.to_bytes()
    with pytest.raises(ValueError, match="duplicate"):
        MeshArtifact(
            artifact_id="bad-mesh",
            frame=FrameId("World"),
            provenance=mesh.provenance,
            nodes=mesh.nodes,
            elements=(
                MeshElement(1, "tet10", tuple(range(1, 11)), "part-body"),
                MeshElement(1, "tet10", tuple(range(1, 11)), "part-body"),
            ),
            faces=mesh.faces,
            sets=mesh.sets,
            quality_records=mesh.quality_records,
        )


def test_file_entries_reject_absolute_escape_and_ambiguous_paths() -> None:
    _require_api()
    with pytest.raises(ValueError):
        FileEntry("../case.feb", "a" * 64, 1, "input")
    with pytest.raises(ValueError):
        FileEntry("C:/case.feb", "a" * 64, 1, "input")
    with pytest.raises(ValueError):
        FileEntry("input\\case.feb", "a" * 64, 1, "input")


def test_bundle_attempt_manifest_chain_binds_exact_ids(synthetic_case_spec: Any) -> None:
    _require_api()
    revision = case_revision(synthetic_case_spec)
    mesh = _mesh()
    profile = _profile()
    bundle = _bundle(revision, mesh, profile)
    attempt = _attempt(bundle, revision)
    manifest = _manifest(attempt, bundle, profile)
    assert bundle.spec_digest == revision.spec_digest
    assert attempt.bundle_digest == bundle.bundle_digest
    assert manifest.attempt_id == attempt.attempt_id
    assert manifest.bundle_digest == bundle.bundle_digest
    assert manifest.read_result.status is ReadStatus.VALIDATED


def test_attempt_transitions_are_guarded_and_retry_is_a_new_record() -> None:
    _require_api()
    assert transition_allowed(RunState.CREATED, RunState.PREPARING)
    assert transition_allowed(RunState.CREATED, RunState.CANCELLED)
    assert transition_allowed(RunState.VALIDATING, RunState.INTERRUPTED)
    assert not transition_allowed(RunState.SUCCEEDED, RunState.RUNNING)
    assert CasePreparationState.READY.value == "READY"
    assert TaskStatus.NEEDS_PREVIEW.value == "NEEDS_PREVIEW"
    assert ServiceExitCode.CONFLICT == 8
    assert PreviewStatus.CONFIRMED.value == "CONFIRMED"


def test_quality_keeps_dimensions_and_typed_measurements(synthetic_case_spec: Any) -> None:
    _require_api()
    revision = case_revision(synthetic_case_spec)
    mesh = _mesh()
    profile = _profile()
    bundle = _bundle(revision, mesh, profile)
    attempt = _attempt(bundle, revision).transition_to(RunState.PREPARING)
    manifest = _manifest(attempt, bundle, profile)
    assessment = QualityAssessment(
        assessment_id="assessment-interface",
        manifest_id=manifest.manifest_id,
        policy_digest=hashlib.sha256(revision.spec.quality_policy.to_bytes()).hexdigest(),
        criteria=(
            CriterionAssessment(
                criterion_id="execution-complete",
                dimension="execution",
                status=AssessmentStatus.PASS,
                measured=(MeasuredValue("state-count", 2.0, "1"),),
                reason="synthetic complete read",
            ),
        ),
        overall_status=AssessmentStatus.PASS,
    )
    assert assessment.manifest_id == manifest.manifest_id
    assert assessment.criteria[0].measured[0].unit == "1"


def test_preview_requires_confirmation_evidence_for_confirmed_state() -> None:
    _require_api()
    request = PreviewRequest("preview-interface", "manifest-interface", (2,), ("displacement",))
    receipt = PreviewReceipt(
        receipt_id="receipt-interface",
        manifest_id=request.manifest_id,
        xplt_digest="8" * 64,
        studio=ToolIdentity("febio-studio", "3.1.0", "b" * 64),
        status=PreviewStatus.REQUESTED,
        requested_state_ids=request.state_ids,
        requested_variables=request.variables,
        observed_state_ids=(),
        observed_variables=(),
        confirmation_evidence=(),
    )
    assert receipt.status is PreviewStatus.REQUESTED
    with pytest.raises(ValueError, match="evidence"):
        receipt.confirmed(evidence=())


def test_comparison_freezes_identity_and_conditions() -> None:
    _require_api()
    comparison = ComparisonSpec(
        comparison_id="comparison-interface",
        baseline_manifest_id="manifest-baseline",
        candidate_manifest_id="manifest-interface",
        intended_changes=("mesh refinement",),
        fixed_conditions=("material", "support", "motion"),
        axes=(
            ComparisonAxis(
                "force",
                "N",
                "part",
                "peak",
                "maximum",
                interval=ComparisonInterval("N", 0.0, 1.0),
                interpolation="linear",
            ),
        ),
    )
    assert comparison.axes[0].unit == "N"


def _require_repair_api() -> None:
    assert REPAIR_ARTIFACTS_API is not None, "P1 repair artifact API is not available"
    assert REPAIR_RESULTS_API is not None, "P1 repair result-data API is not available"


def test_mesh_contract_exposes_canonical_tet10_tables_and_nonempty_typed_sets() -> None:
    _require_repair_api()
    assert TET10_CORNER_NODE_POSITIONS == (0, 1, 2, 3)
    assert TET10_EDGE_NODE_POSITIONS == (
        (0, 1),
        (1, 2),
        (2, 0),
        (0, 3),
        (1, 3),
        (2, 3),
    )
    assert TET10_FACE_NODE_POSITIONS == (
        (0, 2, 1, 6, 5, 4),
        (0, 1, 3, 4, 8, 7),
        (1, 2, 3, 5, 9, 8),
        (0, 3, 2, 7, 9, 6),
    )
    mesh = _mesh()
    assert any(item.kind == "face" and item.member_ids == ("face-1",) for item in mesh.sets)
    assert all(item.source_selection_digest == "b" * 64 for item in mesh.sets)


def test_mesh_rejects_cross_body_and_bad_oriented_face_references() -> None:
    _require_repair_api()
    mesh = _mesh()
    with pytest.raises(ValueError, match="body"):
        MeshArtifact(
            artifact_id="bad-body",
            frame=mesh.frame,
            provenance=mesh.provenance,
            nodes=mesh.nodes,
            elements=(MeshElement(1, "tet10", tuple(range(1, 11)), "unknown-body"),),
            faces=mesh.faces,
            sets=mesh.sets,
            quality_records=mesh.quality_records,
        )


def test_tet10_face_table_has_outward_tri6_orientation_and_edge_midpoints() -> None:
    _require_repair_api()
    coordinates = (
        (0.0, 0.0, 0.0),
        (2.0, 0.0, 0.0),
        (0.0, 3.0, 0.0),
        (0.0, 0.0, 5.0),
        (1.0, 0.0, 0.0),
        (1.0, 1.5, 0.0),
        (0.0, 1.5, 0.0),
        (0.0, 0.0, 2.5),
        (1.0, 0.0, 2.5),
        (0.0, 1.5, 2.5),
    )
    edges = TET10_EDGE_NODE_POSITIONS

    def subtract(left: tuple[float, ...], right: tuple[float, ...]) -> tuple[float, ...]:
        return tuple(a - b for a, b in zip(left, right, strict=True))

    def cross(left: tuple[float, ...], right: tuple[float, ...]) -> tuple[float, ...]:
        return (
            left[1] * right[2] - left[2] * right[1],
            left[2] * right[0] - left[0] * right[2],
            left[0] * right[1] - left[1] * right[0],
        )

    def dot(left: tuple[float, ...], right: tuple[float, ...]) -> float:
        return sum(a * b for a, b in zip(left, right, strict=True))

    for face in TET10_FACE_NODE_POSITIONS:
        corners = face[:3]
        normal = cross(
            subtract(coordinates[corners[1]], coordinates[corners[0]]),
            subtract(coordinates[corners[2]], coordinates[corners[0]]),
        )
        opposite = next(index for index in range(4) if index not in corners)
        toward_opposite = subtract(coordinates[opposite], coordinates[corners[0]])
        assert dot(normal, toward_opposite) < 0.0
        expected_edges = tuple((corners[index], corners[(index + 1) % 3]) for index in range(3))
        for midpoint_position, edge in zip(face[3:], expected_edges, strict=True):
            midpoint = coordinates[midpoint_position]
            endpoints = (coordinates[edge[0]], coordinates[edge[1]])
            assert midpoint == tuple((a + b) / 2.0 for a, b in zip(*endpoints, strict=True))
            assert edge in edges or (edge[1], edge[0]) in edges


def test_mesh_accepts_two_bodies_all_set_kinds_and_rejects_contradictory_ownership() -> None:
    _require_repair_api()
    provenance = MeshProvenance(
        source_geometry_digest="a" * 64,
        source_body_ids=("body-a", "body-b"),
        source_selection_digests=("b" * 64, "c" * 64),
        mesh_recipe_digest="d" * 64,
        tool_id="gmsh",
        tool_version="4.12.0",
        mapping_id="tet10-gmsh-febio-v1",
        node_ordering_id="tet10-canonical-v1",
        face_ordering_id="tet10-face-canonical-v1",
    )
    nodes = tuple(MeshNode(index, (float(index), 0.0, 0.0)) for index in range(1, 22))
    elements = (
        MeshElement(1, "tet10", tuple(range(1, 11)), "body-a"),
        MeshElement(2, "tet10", tuple(range(11, 21)), "body-b"),
    )
    faces = (
        MeshFace("face-a", "body-a", (1, 3, 2, 7, 6, 5), (1,), (0,)),
        MeshFace("face-b", "body-b", (11, 13, 12, 17, 16, 15), (2,), (0,)),
    )
    sets = (
        MeshSet("nodes-a", "node", "body-a", (1,), "b" * 64),
        MeshSet("elements-a", "element", "body-a", (1,), "b" * 64),
        MeshSet("faces-a", "face", "body-a", ("face-a",), "b" * 64),
        MeshSet("bodies-a", "body", "body-a", ("body-a",), "b" * 64),
        MeshSet("nodes-b", "node", "body-b", (11,), "c" * 64),
        MeshSet("elements-b", "element", "body-b", (2,), "c" * 64),
        MeshSet("faces-b", "face", "body-b", ("face-b",), "c" * 64),
        MeshSet("bodies-b", "body", "body-b", ("body-b",), "c" * 64),
    )
    mesh = MeshArtifact(
        artifact_id="two-body-mesh",
        frame=FrameId("World"),
        provenance=provenance,
        nodes=nodes,
        elements=elements,
        faces=faces,
        sets=sets,
        quality_records=(
            MeshQualityRecord("jacobian", 0.5, "1", 0.0, AssessmentStatus.PASS, "synthetic"),
        ),
    )
    assert {item.kind for item in mesh.sets} == {"node", "element", "face", "body"}
    with pytest.raises(ValueError, match="body set|membership"):
        MeshArtifact(
            artifact_id="contradictory-body-set",
            frame=mesh.frame,
            provenance=mesh.provenance,
            nodes=mesh.nodes,
            elements=mesh.elements,
            faces=mesh.faces,
            sets=tuple(mesh.sets) + (MeshSet("bad-body", "body", "body-a", ("body-b",), "b" * 64),),
            quality_records=mesh.quality_records,
        )
    with pytest.raises(ValueError, match="node set|ownership"):
        MeshArtifact(
            artifact_id="isolated-node-set",
            frame=mesh.frame,
            provenance=mesh.provenance,
            nodes=mesh.nodes,
            elements=mesh.elements,
            faces=mesh.faces,
            sets=tuple(mesh.sets) + (MeshSet("bad-node", "node", "body-a", (21,), "b" * 64),),
            quality_records=mesh.quality_records,
        )


def test_mesh_requires_opposite_oriented_interior_face_pair() -> None:
    _require_repair_api()
    provenance = MeshProvenance(
        "a" * 64,
        ("body-a",),
        ("b" * 64,),
        "c" * 64,
        "gmsh",
        "4.12.0",
        "tet10-gmsh-febio-v1",
        "tet10-canonical-v1",
        "tet10-face-canonical-v1",
    )
    nodes = tuple(MeshNode(index, (float(index), 0.0, 0.0)) for index in range(1, 15))
    first = MeshElement(1, "tet10", tuple(range(1, 11)), "body-a")
    opposite = MeshElement(2, "tet10", (1, 3, 2, 11, 7, 6, 5, 12, 13, 14), "body-a")
    boundary = MeshFace("boundary", "body-a", (1, 2, 4, 5, 9, 8), (1,), (1,))
    interior = MeshFace("interior", "body-a", (1, 3, 2, 7, 6, 5), (1, 2), (0, 0))
    sets = (
        MeshSet("body", "body", "body-a", ("body-a",), "b" * 64),
        MeshSet("element", "element", "body-a", (1,), "b" * 64),
        MeshSet("face", "face", "body-a", ("interior",), "b" * 64),
    )
    mesh = MeshArtifact(
        "interior-pair",
        FrameId("World"),
        provenance,
        nodes,
        (first, opposite),
        (boundary, interior),
        sets,
        (MeshQualityRecord("jacobian", 0.5, "1", 0.0, AssessmentStatus.PASS, "synthetic"),),
    )
    assert mesh.faces[1].adjacent_element_ids == (1, 2)
    same_orientation = MeshElement(2, "tet10", (1, 2, 3, 11, 5, 6, 7, 12, 13, 14), "body-a")
    with pytest.raises(ValueError, match="opposite|orientation"):
        MeshArtifact(
            "same-pair",
            FrameId("World"),
            provenance,
            nodes,
            (first, same_orientation),
            (boundary, interior),
            sets,
            (MeshQualityRecord("jacobian", 0.5, "1", 0.0, AssessmentStatus.PASS, "synthetic"),),
        )
    with pytest.raises(ValueError, match="two|adjacent"):
        MeshArtifact(
            "too-many-adjacent",
            FrameId("World"),
            provenance,
            nodes,
            (first, opposite),
            (MeshFace("overfull", "body-a", (1, 3, 2, 7, 6, 5), (1, 2, 3), (0, 0, 0)),),
            sets,
            (MeshQualityRecord("jacobian", 0.5, "1", 0.0, AssessmentStatus.PASS, "synthetic"),),
        )
    with pytest.raises(ValueError, match="face|local|node"):
        MeshArtifact(
            artifact_id="bad-face",
            frame=mesh.frame,
            provenance=mesh.provenance,
            nodes=mesh.nodes,
            elements=mesh.elements,
            faces=(MeshFace("bad-face", "part-body", (1, 2, 3, 8, 9, 10), (1,), (0,)),),
            sets=mesh.sets,
            quality_records=mesh.quality_records,
        )


def _cyclic_neighbor_mesh(
    corner_order: tuple[str, str, str, str],
    shared_local_face: int,
    *,
    second_node_ids: tuple[int, ...] | None = None,
) -> tuple[Any, tuple[int, ...]]:
    coordinates = {
        "A": (0.0, 0.0, 0.0),
        "B": (1.0, 0.0, 0.0),
        "C": (0.0, 1.0, 0.0),
        "D1": (0.0, 0.0, 1.0),
        "D2": (0.0, 0.0, -1.0),
        "AB": (0.5, 0.0, 0.0),
        "BC": (0.5, 0.5, 0.0),
        "CA": (0.0, 0.5, 0.0),
        "AD1": (0.0, 0.0, 0.5),
        "BD1": (0.5, 0.0, 0.5),
        "CD1": (0.0, 0.5, 0.5),
        "AD2": (0.0, 0.0, -0.5),
        "BD2": (0.5, 0.0, -0.5),
        "CD2": (0.0, 0.5, -0.5),
    }
    node_ids = {
        "A": 1,
        "B": 2,
        "C": 3,
        "D1": 4,
        "AB": 5,
        "BC": 6,
        "CA": 7,
        "AD1": 8,
        "BD1": 9,
        "CD1": 10,
        "D2": 11,
        "AD2": 12,
        "BD2": 13,
        "CD2": 14,
    }
    edge_node_names = {
        frozenset(("A", "B")): "AB",
        frozenset(("B", "C")): "BC",
        frozenset(("C", "A")): "CA",
        frozenset(("A", "D1")): "AD1",
        frozenset(("B", "D1")): "BD1",
        frozenset(("C", "D1")): "CD1",
        frozenset(("A", "D2")): "AD2",
        frozenset(("B", "D2")): "BD2",
        frozenset(("C", "D2")): "CD2",
    }

    def element_nodes(corners: tuple[str, str, str, str]) -> tuple[int, ...]:
        mids: list[int] = []
        for left, right in TET10_EDGE_NODE_POSITIONS:
            edge = frozenset((corners[left], corners[right]))
            midpoint_name = edge_node_names.get(edge)
            assert midpoint_name is not None
            mids.append(node_ids[midpoint_name])
        return tuple(node_ids[item] for item in corners) + tuple(mids)

    first_nodes = element_nodes(("A", "B", "C", "D1"))
    second_nodes = second_node_ids or element_nodes(corner_order)
    first = MeshElement(1, "tet10", first_nodes, "body-a")
    second = MeshElement(2, "tet10", second_nodes, "body-a")
    provenance = MeshProvenance(
        "a" * 64,
        ("body-a",),
        ("b" * 64,),
        "c" * 64,
        "gmsh",
        "4.12.0",
        "tet10-gmsh-febio-v1",
        "tet10-canonical-v1",
        "tet10-face-canonical-v1",
    )
    shared = MeshFace(
        "interior",
        "body-a",
        (1, 3, 2, 7, 6, 5),
        (1, 2),
        (0, shared_local_face),
    )
    mesh = MeshArtifact(
        "cyclic-interior-pair",
        FrameId("World"),
        provenance,
        tuple(
            MeshNode(node_id, coordinates[name])
            for name, node_id in sorted(node_ids.items(), key=lambda item: item[1])
        ),
        (first, second),
        (MeshFace("boundary", "body-a", (1, 2, 4, 5, 9, 8), (1,), (1,)), shared),
        (
            MeshSet("body", "body", "body-a", ("body-a",), "b" * 64),
            MeshSet("elements", "element", "body-a", (1, 2), "b" * 64),
            MeshSet("faces", "face", "body-a", ("interior",), "b" * 64),
        ),
        (MeshQualityRecord("jacobian", 0.5, "1", 0.0, AssessmentStatus.PASS, "synthetic"),),
    )
    return mesh, second_nodes


@pytest.mark.parametrize(
    ("corner_order", "shared_local_face", "expected_shared_face"),
    (
        (("A", "B", "D2", "C"), 1, (1, 2, 3, 5, 6, 7)),
        (("B", "A", "C", "D2"), 0, (2, 3, 1, 6, 7, 5)),
        (("C", "D2", "B", "A"), 3, (3, 1, 2, 7, 5, 6)),
    ),
)
def test_mesh_accepts_all_cyclically_rotated_opposite_interior_face_cycles(
    corner_order: tuple[str, str, str, str],
    shared_local_face: int,
    expected_shared_face: tuple[int, ...],
) -> None:
    _require_repair_api()
    mesh, second_nodes = _cyclic_neighbor_mesh(corner_order, shared_local_face)
    node_coordinates = {node.node_id: node.coordinates_si for node in mesh.nodes}
    a, b, c, d = (node_coordinates[node_id] for node_id in second_nodes[:4])
    determinant = (
        (b[0] - a[0]) * ((c[1] - a[1]) * (d[2] - a[2]) - (c[2] - a[2]) * (d[1] - a[1]))
        - (b[1] - a[1]) * ((c[0] - a[0]) * (d[2] - a[2]) - (c[2] - a[2]) * (d[0] - a[0]))
        + (b[2] - a[2]) * ((c[0] - a[0]) * (d[1] - a[1]) - (c[1] - a[1]) * (d[0] - a[0]))
    )
    assert determinant > 0.0
    assert (
        tuple(second_nodes[position] for position in TET10_FACE_NODE_POSITIONS[shared_local_face])
        == expected_shared_face
    )
    assert mesh.faces[1].node_ids == (1, 3, 2, 7, 6, 5)


def test_mesh_keeps_interior_face_rejections_for_same_facing_wrong_midside_and_unknown_node() -> (
    None
):
    _require_repair_api()
    valid_mesh, valid_second_nodes = _cyclic_neighbor_mesh(("A", "C", "B", "D2"), 0)
    with pytest.raises(ValueError, match="opposite|orientation"):
        _cyclic_neighbor_mesh(("A", "B", "C", "D2"), 0)
    wrong_midside = list(valid_second_nodes)
    wrong_midside[4], wrong_midside[5] = wrong_midside[5], wrong_midside[4]
    with pytest.raises(ValueError, match="opposite|orientation"):
        _cyclic_neighbor_mesh(
            ("A", "C", "B", "D2"),
            0,
            second_node_ids=tuple(wrong_midside),
        )
    unknown_node = list(valid_second_nodes)
    unknown_node[0] = 99
    with pytest.raises(ValueError, match="unknown node"):
        _cyclic_neighbor_mesh(
            ("B", "A", "C", "D2"),
            0,
            second_node_ids=tuple(unknown_node),
        )
    assert valid_mesh.faces[1].adjacent_element_ids == (1, 2)


def test_file_identity_rejects_windows_lexical_forms_and_casefold_collisions(
    synthetic_case_spec: Any,
) -> None:
    _require_api()
    for path in (
        "output/case.xplt:stream",
        "output/NUL.xplt",
        "output./case.xplt",
        "output /case.xplt",
    ):
        with pytest.raises(ValueError):
            FileEntry(path, "a" * 64, 1, "output")
        with pytest.raises(ValueError):
            ResultDataRef("data-interface", "a" * 64, "numeric-result-v1", path)
    revision = case_revision(synthetic_case_spec)
    mesh = _mesh()
    profile = _profile()
    with pytest.raises(ValueError, match="duplicate|case"):
        _bundle_with_files(
            revision,
            mesh,
            profile,
            (
                FileEntry("input/CASE.feb", "f" * 64, 128, "input"),
                FileEntry("input/case.feb", "e" * 64, 128, "input"),
            ),
        )


def _bundle_with_files(revision: CaseRevision, mesh: Any, profile: Any, files: Any) -> Any:
    return ExecutionBundle(
        bundle_id="bundle-casefold",
        case_id=revision.case_id,
        revision_id=revision.revision_id,
        spec_digest=revision.spec_digest,
        mesh_digest=mesh.artifact_digest,
        profile_id=profile.profile_id,
        tool=profile.solver,
        files=files,
        argv=("febio4.exe", "-i", "case.feb"),
        cwd="C:/registered-case/runs/attempt-interface",
        thread_count=1,
        settings=(ExecutionSetting("solver_threads", 1),),
    )


def test_comparison_axis_records_common_interval_and_interpolation() -> None:
    _require_api()
    comparison = ComparisonSpec(
        comparison_id="comparison-interval",
        baseline_manifest_id="manifest-baseline",
        candidate_manifest_id="manifest-interface",
        intended_changes=("mesh refinement",),
        fixed_conditions=("material", "support", "motion"),
        axes=(
            ComparisonAxis(
                "force",
                "N",
                "part",
                "peak",
                "maximum",
                interval=ComparisonInterval("N", 0.0, 10.0),
                interpolation="linear",
            ),
        ),
    )
    assert comparison.axes[0].interval.lower == 0.0
    assert comparison.axes[0].interval.upper == 10.0
    assert comparison.axes[0].interpolation == "linear"
    with pytest.raises(ValueError):
        ComparisonInterval("N", 10.0, 0.0)


def test_preview_confirmation_preserves_observed_values_and_requires_explicit_data() -> None:
    _require_api()
    evidence_ref = evidence("preview.confirmation", "preview-observation")
    studio = ToolIdentity("febio-studio", "3.1.0", "b" * 64)
    existing = PreviewReceipt(
        receipt_id="receipt-observed",
        manifest_id="manifest-interface",
        xplt_digest="8" * 64,
        studio=studio,
        status=PreviewStatus.LAUNCHED,
        requested_state_ids=(2,),
        requested_variables=("displacement",),
        observed_state_ids=(4,),
        observed_variables=("reaction_force",),
        confirmation_evidence=(),
    )
    confirmed = existing.confirmed(evidence=(evidence_ref,))
    assert confirmed.observed_state_ids == (4,)
    assert confirmed.observed_variables == ("reaction_force",)
    empty = PreviewReceipt(
        receipt_id="receipt-empty",
        manifest_id="manifest-interface",
        xplt_digest="8" * 64,
        studio=studio,
        status=PreviewStatus.REQUESTED,
        requested_state_ids=(2,),
        requested_variables=("displacement",),
        observed_state_ids=(),
        observed_variables=(),
        confirmation_evidence=(),
    )
    with pytest.raises(ValueError, match="observed"):
        empty.confirmed(evidence=(evidence_ref,))


def test_source_and_numeric_data_contracts_carry_actual_resolved_values() -> None:
    _require_repair_api()
    content = b"synthetic-step-content"
    source = SourceAssetRef(
        "source-interface",
        hashlib.sha256(content).hexdigest(),
        "model/step",
    )
    resolved = SourceAssetContent(source, content)
    assert resolved.source_asset == source
    reference = ResultDataRef(
        "displacement-data",
        "a" * 64,
        "numeric-result-v1",
        "output/case.xplt",
    )
    data = NumericResultData(
        reference=reference,
        mapping=OutputMapping(
            "displacement",
            "displacement",
            "node",
            "VEC3F",
            "m",
            FrameId("World"),
            1,
            1,
            "value",
        ),
        axis_id="time",
        axis_unit="s",
        axis_values=(0.0, 1.0),
        entity_ids=("node-1", "node-2"),
        component_ids=("x", "y", "z"),
        values=((0.0, 0.0, 0.0, 1.0, 1.0, 1.0), (0.1, 0.0, 0.0, 1.1, 0.0, 0.0)),
    )
    assert data.values[1][3] == 1.1
    assert data.axis_values == (0.0, 1.0)


def test_connected_artifact_to_preview_chain_round_trips_through_common_codec(
    synthetic_case_spec: Any,
) -> None:
    _require_api()
    from febio_cae.domain.codec import decode_record, encode_record

    revision = case_revision(synthetic_case_spec)
    mesh = _mesh()
    profile = _profile()
    bundle = _bundle(revision, mesh, profile)
    attempt = _attempt(bundle, revision)
    manifest = _manifest(attempt, bundle, profile)
    assessment = QualityAssessment(
        assessment_id="assessment-chain",
        manifest_id=manifest.manifest_id,
        policy_digest=hashlib.sha256(revision.spec.quality_policy.to_bytes()).hexdigest(),
        criteria=(
            CriterionAssessment(
                criterion_id="numeric-data",
                dimension="numeric",
                status=AssessmentStatus.UNVERIFIED,
                measured=(MeasuredValue("state-count", 2.0, "1"),),
                reason="synthetic numeric data is structural only",
            ),
        ),
        overall_status=AssessmentStatus.UNVERIFIED,
    )
    request = PreviewRequest("preview-chain", manifest.manifest_id, (0,), ("displacement",))
    receipt = PreviewReceipt(
        receipt_id="receipt-chain",
        manifest_id=manifest.manifest_id,
        xplt_digest="8" * 64,
        studio=profile.reader,
        status=PreviewStatus.REQUESTED,
        requested_state_ids=request.state_ids,
        requested_variables=request.variables,
        observed_state_ids=(),
        observed_variables=(),
        confirmation_evidence=(),
    )
    comparison = ComparisonSpec(
        comparison_id="comparison-chain",
        baseline_manifest_id=manifest.manifest_id,
        candidate_manifest_id=manifest.manifest_id,
        intended_changes=("none",),
        fixed_conditions=("material",),
        axes=(
            ComparisonAxis(
                "time",
                "s",
                "part",
                "value",
                "maximum",
                interval=ComparisonInterval("s", 0.0, 1.0),
                interpolation="linear",
            ),
        ),
    )
    for record in (mesh, bundle, attempt, manifest, assessment, request, receipt, comparison):
        restored = decode_record(encode_record(record), type(record))
        assert restored.to_bytes() == record.to_bytes()
