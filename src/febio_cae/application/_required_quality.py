"""Derived mandatory coverage for public paths without qualified obligation verifiers."""

from __future__ import annotations

import hashlib

from febio_cae.adapters.febio.quality import QualityAdapter
from febio_cae.adapters.febio.reported_norms import assess_reported_norms
from febio_cae.domain import (
    AssessmentStatus,
    AttemptRecord,
    CaseRevision,
    CompatibilityProfile,
    CriterionAssessment,
    ExecutionBundle,
    MeshArtifact,
    NumericResultData,
    QualityAssessment,
    ReadStatus,
    ResolvedFileContent,
    ResultManifest,
    RunState,
    Quantity,
)
from febio_cae.domain.ports import PortError, PortErrorCategory
from febio_cae.domain.results import numeric_state_indices
from febio_cae.storage import CaseStorage

# This is the formal inventory, not a mapping from caller-selected criterion names.
_NUMERICAL = (
    (
        "execution_result_completeness",
        "execution",
        "solver/read success does not verify the complete requested endpoint and output obligation",
    ),
    (
        "contact_quality",
        "numeric",
        "qualified contact interval, establishment, interference and penetration verification is unavailable",
    ),
    (
        "motion_support_contact_fidelity",
        "numeric",
        "complete observed motion, support and contact-set fidelity verification is unavailable",
    ),
    (
        "quasistatic_equilibrium",
        "numeric",
        "qualified complete force-system, applicability, side and sign verification is unavailable",
    ),
    (
        "solver_residual",
        "numeric",
        "qualified residual definitions, extraction, increment coverage and acceptance verification is unavailable",
    ),
    (
        "mesh_dependence",
        "numeric",
        "qualified refinement-study, evaluation scope and convergence verification is unavailable",
    ),
)


def _execution_result_completeness(
    manifest: ResultManifest,
    revision: CaseRevision,
    mesh: MeshArtifact,
    profile: CompatibilityProfile,
    storage: CaseStorage,
    context: tuple[
        AttemptRecord, ExecutionBundle, ResolvedFileContent | None, ResolvedFileContent | None
    ],
) -> CriterionAssessment:
    """Verify the registered result inventory and its complete requested state scope."""
    try:
        attempt, bundle, _, _ = context
        registered_revision = storage.get_revision(attempt.case_id, attempt.revision_id)
        profile_digest = hashlib.sha256(profile.to_bytes()).hexdigest()
        solver_profile = revision.spec.solver_policy.profile
        if (
            registered_revision.to_bytes() != revision.to_bytes()
            or attempt.case_id != revision.case_id
            or attempt.revision_id != revision.revision_id
            or attempt.bundle_digest != bundle.bundle_digest
            or manifest.attempt_id != attempt.attempt_id
            or manifest.bundle_digest != bundle.bundle_digest
            or bundle.case_id != revision.case_id
            or bundle.revision_id != revision.revision_id
            or bundle.spec_digest != revision.spec_digest
            or bundle.mesh_digest != mesh.artifact_digest
            or bundle.profile_id != profile.profile_id
            or bundle.profile_id != solver_profile.profile_id
            or profile_digest != solver_profile.record_digest
            or bundle.tool != profile.solver
            or manifest.read_result.reader != profile.reader
        ):
            raise PortError(
                PortErrorCategory.INTEGRITY,
                "registered execution/result context differs from the supplied revision, mesh, or profile",
            )
        if attempt.state is not RunState.SUCCEEDED:
            raise ValueError("registered attempt is not in the successful terminal state")
        if manifest.read_result.status is not ReadStatus.VALIDATED:
            raise PortError(PortErrorCategory.INTEGRITY, "registered result read is not validated")

        observations = {item.output_id: item for item in manifest.read_result.observations}
        requests = revision.spec.outputs.requests
        request_ids = {item.request_id for item in requests}
        if set(observations) != request_ids:
            raise PortError(
                PortErrorCategory.INTEGRITY,
                "registered result inventory differs from the revision",
            )

        expected_times = tuple(
            sorted(
                {
                    float(item.to_si().value)
                    for item in revision.spec.outputs.saved_times
                }
                | {float(revision.spec.motion.samples[-1].time.to_si().value)}
            )
        )
        manifest_entries = {entry.logical_path: entry for entry in manifest.files}
        for request in requests:
            mapping = profile.mapping_for(request.quantity_id)
            observation = observations[request.request_id]
            numeric = storage.resolve_manifest_output(manifest.manifest_id, request.request_id)
            if not isinstance(numeric, NumericResultData):
                raise PortError(
                    PortErrorCategory.INTEGRITY,
                    "registered result resolver returned an invalid record",
                )
            if (
                observation.data_ref is None
                or numeric.reference != observation.data_ref
                or numeric.reference.attempt_id != attempt.attempt_id
                or numeric.reference.bundle_digest != bundle.bundle_digest
                or numeric.mapping != mapping
            ):
                raise PortError(
                    PortErrorCategory.INTEGRITY,
                    f"registered numeric result is not bound to output request {request.request_id}",
                )
            entry = manifest_entries.get(numeric.reference.logical_path)
            if entry is None or entry.role not in {"result", request.request_id}:
                raise PortError(
                    PortErrorCategory.INTEGRITY,
                    f"registered numeric result source is not an output for request {request.request_id}",
                )
            if numeric.reference.codec_id != "numeric-result-v1":
                raise ValueError(
                    f"numeric result for {request.request_id} uses an unsupported codec"
                )
            expected_components = {
                "VEC3F": ("x", "y", "z"),
                "MAT3FS": ("xx", "yy", "zz", "xy", "yz", "xz"),
            }.get(numeric.mapping.value_type)
            if (
                expected_components is not None
                and tuple(numeric.component_ids) != expected_components
            ):
                raise ValueError(
                    f"numeric result for {request.request_id} has an unsupported component layout"
                )
            if (
                observation.location != request.location
                or observation.value_type != mapping.value_type
                or observation.unit != mapping.unit
                or observation.frame != request.frame
                or observation.measure_id != request.measure_id
                or mapping.location != request.location
                or mapping.measure_id != request.measure_id
                or mapping.frame != request.frame
                or observation.state_count != len(numeric.axis_values)
                or mapping.frame != mesh.frame
                or request.frame != mesh.frame
                or Quantity(0, request.display_unit).dimension
                != Quantity(0, mapping.unit).dimension
            ):
                raise ValueError(
                    f"registered output request {request.request_id} has incomplete semantic bindings"
                )
            if request.component_id not in numeric.component_ids:
                raise ValueError(
                    f"requested component is absent from output {request.request_id}: {request.component_id}"
                )
            required_entities = QualityAdapter._entity_ids(
                request.selection, request.location, mesh
            )
            QualityAdapter._entity_indices(numeric, required_entities, mesh)
            if numeric.axis_id not in {"time", "state_time"}:
                raise ValueError("numeric result axis is not a supported state-time axis")
            numeric_state_indices(numeric, expected_times)
    except (ValueError, TypeError, KeyError, OverflowError) as error:
        return CriterionAssessment(
            "execution_result_completeness",
            "execution",
            AssessmentStatus.UNVERIFIED,
            (),
            f"registered execution/result completeness is unavailable: {error}",
        )
    return CriterionAssessment(
        "execution_result_completeness",
        "execution",
        AssessmentStatus.PASS,
        (),
        "registered execution contains every declared output with bound finite state data through the declared endpoint",
    )


def required_quality_summary(
    manifest: ResultManifest,
    revision: CaseRevision,
    mesh: MeshArtifact,
    profile: CompatibilityProfile,
    quality: QualityAssessment,
    storage: CaseStorage,
) -> tuple[str, dict[str, object]]:
    """Preserve arithmetic evidence while deriving registered execution completeness.

    Inputs come from the existing resolved public result context. No caller flags,
    criterion labels or arbitrary evidence references can qualify these obligations.
    Only execution/result completeness has a producer in this bounded gate.
    """
    context = storage.resolve_reported_norms_context(manifest)
    execution_row = _execution_result_completeness(
        manifest, revision, mesh, profile, storage, context
    )
    numerical = (execution_row,) + tuple(
        CriterionAssessment(identifier, dimension, AssessmentStatus.UNVERIFIED, (), reason)
        for identifier, dimension, reason in _NUMERICAL[1:]
    )
    physical = CriterionAssessment(
        "physical_applicability_validation",
        "applicability",
        AssessmentStatus.UNVERIFIED,
        (),
        "registered case grounds are retained; complete applicability and experimental verification is not established by this gate",
    )
    coverage: dict[str, object] = {
        "bindings": {
            "case_id": revision.case_id,
            "revision_id": revision.revision_id,
            "spec_digest": revision.spec_digest,
            "mesh_digest": mesh.artifact_digest,
            "profile_id": profile.profile_id,
            "profile_digest": hashlib.sha256(profile.to_bytes()).hexdigest(),
            "attempt_id": manifest.attempt_id,
            "bundle_digest": manifest.bundle_digest,
            "manifest_id": manifest.manifest_id,
            "manifest_digest": hashlib.sha256(manifest.to_bytes()).hexdigest(),
            "assessment_id": quality.assessment_id,
            "policy_digest": quality.policy_digest,
        },
        "numerical": [row.to_dict() for row in numerical],
        # Physical corroboration is deliberately outside numerical aggregation.
        "physical_applicability": physical.to_dict(),
    }
    report = assess_reported_norms(manifest, revision, mesh, profile, context).to_dict()
    coverage["reported_solver_norms"] = report
    status = (
        "FAIL"
        if quality.overall_status is AssessmentStatus.FAIL or report["final_status"] == "FAIL"
        else "PASS"
        if (
            quality.overall_status is AssessmentStatus.PASS
            and report["final_status"] == "PASS"
            and all(
                row.status in {AssessmentStatus.PASS, AssessmentStatus.NOT_APPLICABLE}
                for row in numerical
            )
        )
        else "UNVERIFIED"
    )
    return status, coverage
