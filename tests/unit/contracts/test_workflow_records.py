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
    from febio_cae.domain.comparison import ComparisonAxis, ComparisonSpec
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
        OutputObservation,
        QualityAssessment,
        ReadResult,
        ReadStatus,
        ResultManifest,
    )
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
        elements=(MeshElement(1, "tet10", tuple(range(1, 11))),),
        faces=(MeshFace("face-1", "part-body", (1, 2, 3, 5, 6, 7), (1,), (0,)),),
        sets=(MeshSet("body-set", "body", "part-body", (1,)),),
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
        executable_digest="g" * 64,
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
    observation = OutputObservation(
        output_id="displacement",
        location="node",
        value_type="VEC3F",
        unit="m",
        frame=FrameId("World"),
        measure_id="value",
        state_count=2,
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
        files=(FileEntry("output/case.xplt", "h" * 64, 256, "xplt"),),
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
        SourceAssetRef("step-source", "i" * 64, "model/step"),
        requested_body_ids=(),
    )
    inspection = GeometryInspection(
        request.source_asset,
        "j" * 64,
        "mm",
        ("part-body",),
        ("part-body",),
    )
    assert "revision" not in inspect.signature(type(request)).parameters
    assert inspection.source_asset == request.source_asset


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
                MeshElement(1, "tet10", tuple(range(1, 11))),
                MeshElement(1, "tet10", tuple(range(1, 11))),
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
        xplt_digest="h" * 64,
        studio=ToolIdentity("febio-studio", "3.1.0", "k" * 64),
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
        axes=(ComparisonAxis("force", "N", "part", "peak", "linear"),),
    )
    assert comparison.axes[0].unit == "N"
