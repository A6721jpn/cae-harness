"""Derived mandatory coverage for public paths without qualified obligation verifiers."""

from __future__ import annotations

import hashlib

from febio_cae.adapters.febio.reported_norms import assess_reported_norms
from febio_cae.domain import (
    AssessmentStatus,
    CaseRevision,
    CompatibilityProfile,
    CriterionAssessment,
    MeshArtifact,
    QualityAssessment,
    ResultManifest,
)
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


def required_quality_summary(
    manifest: ResultManifest,
    revision: CaseRevision,
    mesh: MeshArtifact,
    profile: CompatibilityProfile,
    quality: QualityAssessment,
    storage: CaseStorage,
) -> tuple[str, dict[str, object]]:
    """Preserve arithmetic evidence while refusing unsupported mandatory completion.

    Inputs come from the existing resolved public result context. No caller flags,
    criterion labels or arbitrary evidence references can qualify these obligations.
    This bounded gate has no implemented PASS or NOT_APPLICABLE producer.
    """
    numerical = tuple(
        CriterionAssessment(identifier, dimension, AssessmentStatus.UNVERIFIED, (), reason)
        for identifier, dimension, reason in _NUMERICAL
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
    report = assess_reported_norms(
        manifest, revision, mesh, profile, storage.resolve_reported_norms_context(manifest)
    ).to_dict()
    coverage["reported_solver_norms"] = report
    status = (
        "FAIL"
        if quality.overall_status is AssessmentStatus.FAIL or report["final_status"] == "FAIL"
        else "UNVERIFIED"
    )
    return status, coverage
