"""Data-driven quality evaluation over validated numeric result data."""

from __future__ import annotations

import hashlib
import math

from febio_cae.domain import (
    AssessmentStatus,
    CaseRevision,
    CompatibilityProfile,
    CriterionAssessment,
    MeasuredValue,
    MeshArtifact,
    NumericResultData,
    PortError,
    PortErrorCategory,
    QualityAssessment,
    QualityCriterion,
    ResultDataPort,
    ResultManifest,
)


class QualityAdapter:
    """Evaluate only declared criteria; absent physical data remains unverified."""

    def assess(
        self,
        manifest: ResultManifest,
        revision: CaseRevision,
        mesh: MeshArtifact,
        profile: CompatibilityProfile,
        data: ResultDataPort,
    ) -> QualityAssessment:
        if manifest.read_result.status.value != "VALIDATED":
            criteria = tuple(
                CriterionAssessment(
                    criterion.criterion_id,
                    "execution",
                    AssessmentStatus.UNVERIFIED,
                    (),
                    f"result read status is {manifest.read_result.status.value}; applicability: {criterion.applicability_reason}",
                )
                for criterion in revision.spec.quality_policy.criteria
            )
        else:
            criteria = tuple(
                self._criterion(criterion, revision, manifest, profile, data)
                for criterion in revision.spec.quality_policy.criteria
            )
        statuses = {criterion.status for criterion in criteria}
        if AssessmentStatus.FAIL in statuses:
            overall = AssessmentStatus.FAIL
        elif AssessmentStatus.UNVERIFIED in statuses:
            overall = AssessmentStatus.UNVERIFIED
        elif statuses and statuses <= {AssessmentStatus.NOT_APPLICABLE}:
            overall = AssessmentStatus.NOT_APPLICABLE
        else:
            overall = AssessmentStatus.PASS
        return QualityAssessment(
            assessment_id=hashlib.sha256(
                f"{manifest.manifest_id}|{revision.spec_digest}|{revision.spec.quality_policy.to_bytes().hex()}".encode()
            ).hexdigest(),
            manifest_id=manifest.manifest_id,
            policy_digest=hashlib.sha256(revision.spec.quality_policy.to_bytes()).hexdigest(),
            criteria=criteria,
            overall_status=overall,
        )

    def _criterion(
        self,
        criterion: QualityCriterion,
        revision: CaseRevision,
        manifest: ResultManifest,
        profile: CompatibilityProfile,
        data: ResultDataPort,
    ) -> CriterionAssessment:
        # The public policy record is intentionally inspected through its typed fields only.
        criterion_id = criterion.criterion_id
        metric_id = criterion.metric_id
        reason_prefix = f"applicability: {criterion.applicability_reason}"
        if metric_id == "not_applicable":
            return CriterionAssessment(
                criterion_id, "applicability", AssessmentStatus.NOT_APPLICABLE, (), reason_prefix
            )
        if metric_id != "peak_abs_value":
            return CriterionAssessment(
                criterion_id,
                "numeric",
                AssessmentStatus.UNVERIFIED,
                (),
                f"quality metric {metric_id!r} is not implemented by this adapter; {reason_prefix}",
            )
        evaluations = {item.evaluation_id: item for item in revision.spec.outputs.evaluations}
        values: list[float] = []
        units: set[str] = set()
        try:
            for evaluation_id in criterion.evaluation_ids:
                evaluation = evaluations.get(evaluation_id)
                if evaluation is None:
                    raise PortError(
                        PortErrorCategory.QUALITY,
                        f"declared evaluation is missing: {evaluation_id}",
                    )
                request = next(
                    (
                        item
                        for item in revision.spec.outputs.requests
                        if item.request_id == evaluation.output_request_id
                    ),
                    None,
                )
                if request is None:
                    raise PortError(
                        PortErrorCategory.QUALITY,
                        f"declared output request is missing: {evaluation.output_request_id}",
                    )
                mapping = profile.mapping_for(request.quantity_id)
                if not any(
                    observation.output_id == mapping.canonical_id
                    for observation in manifest.read_result.observations
                ):
                    raise PortError(
                        PortErrorCategory.QUALITY,
                        f"validated manifest does not contain output {mapping.canonical_id}",
                    )
                numeric = data.resolve_manifest_output(manifest.manifest_id, mapping.canonical_id)
                if not isinstance(numeric, NumericResultData):
                    raise PortError(
                        PortErrorCategory.QUALITY, "result data resolver returned an invalid record"
                    )
                units.add(numeric.mapping.unit)
                requested_times = {float(item.to_si().value) for item in evaluation.state_times}
                selected_rows = [
                    row
                    for axis, row in zip(numeric.axis_values, numeric.values, strict=True)
                    if any(
                        math.isclose(axis, target, rel_tol=0.0, abs_tol=1e-9)
                        for target in requested_times
                    )
                ]
                if not selected_rows:
                    raise PortError(
                        PortErrorCategory.QUALITY,
                        f"no result state matches evaluation {evaluation_id}",
                    )
                values.extend(abs(value) for row in selected_rows for value in row)
        except (PortError, ValueError, TypeError) as error:
            return CriterionAssessment(
                criterion_id,
                "numeric",
                AssessmentStatus.UNVERIFIED,
                (),
                f"required numeric evidence is unavailable: {error}; {reason_prefix}",
            )
        measured_value = max(values)
        unit = next(iter(units)) if len(units) == 1 else "mixed"
        measured = (MeasuredValue(f"{metric_id}.measured", measured_value, unit),)
        thresholds = {
            item.parameter_id: float(item.value.to_si().value) for item in criterion.thresholds
        }
        if "max_value" in thresholds:
            limit = thresholds["max_value"]
            status = AssessmentStatus.PASS if measured_value <= limit else AssessmentStatus.FAIL
            reason = f"measured {measured_value:.9g} {unit} against max_value {limit:.9g} {unit}; {reason_prefix}"
        else:
            status = AssessmentStatus.UNVERIFIED
            reason = f"peak_abs_value requires a max_value threshold; {reason_prefix}"
        return CriterionAssessment(criterion_id, "numeric", status, measured, reason)


__all__ = ["QualityAdapter"]
