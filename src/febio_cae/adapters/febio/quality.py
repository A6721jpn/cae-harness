"""Data-driven quality evaluation over validated numeric result data."""

from __future__ import annotations

import hashlib
import struct
from dataclasses import replace

from febio_cae.domain import (
    AssessmentStatus,
    CaseRevision,
    CompatibilityProfile,
    CriterionAssessment,
    MeasuredValue,
    MeshArtifact,
    NumericResultData,
    OutputMapping,
    OutputObservation,
    OutputRequest,
    PortError,
    PortErrorCategory,
    QualityAssessment,
    QualityCriterion,
    Quantity,
    ResultDataPort,
    ResultManifest,
    SelectionRef,
)
from febio_cae.domain.canonical import canonical_bytes


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
                self._criterion(criterion, revision, manifest, mesh, profile, data)
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
                b"|".join(
                    (
                        manifest.to_bytes(),
                        revision.spec_digest.encode(),
                        mesh.artifact_digest.encode(),
                        profile.to_bytes(),
                        *(canonical_bytes(criterion.to_dict()) for criterion in criteria),
                    )
                )
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
        mesh: MeshArtifact,
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
            if not criterion.evaluation_ids:
                raise ValueError("numeric criterion has no declared evaluations")
            if manifest.read_result.reader != profile.reader:
                raise ValueError("manifest reader differs from the compatibility profile")
            for evaluation_id in criterion.evaluation_ids:
                evaluation = evaluations.get(evaluation_id)
                if evaluation is None:
                    raise PortError(
                        PortErrorCategory.QUALITY,
                        f"declared evaluation is missing: {evaluation_id}",
                    )
                if evaluation.aggregation_id != "peak":
                    raise ValueError(f"unsupported aggregation: {evaluation.aggregation_id}")
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
                observation = next(
                    (
                        item
                        for item in manifest.read_result.observations
                        if item.output_id == mapping.canonical_id
                    ),
                    None,
                )
                if observation is None:
                    raise PortError(
                        PortErrorCategory.QUALITY,
                        f"validated manifest does not contain output {mapping.canonical_id}",
                    )
                if (
                    observation.location != request.location
                    or observation.measure_id != request.measure_id
                    or observation.frame != request.frame
                    or request.frame != mesh.frame
                ):
                    raise PortError(
                        PortErrorCategory.QUALITY,
                        f"manifest output does not match request semantics: {request.request_id}",
                    )
                numeric = data.resolve_manifest_output(manifest.manifest_id, mapping.canonical_id)
                if not isinstance(numeric, NumericResultData):
                    raise PortError(
                        PortErrorCategory.QUALITY, "result data resolver returned an invalid record"
                    )
                self._validate_binding(numeric, manifest, mapping, request, observation, data)
                numeric.verify_content_digest()
                component_index = self._component_index(numeric, request.component_id)
                wanted = self._entity_ids(evaluation.selection, request.location, mesh)
                requested = self._entity_ids(request.selection, request.location, mesh)
                if not wanted <= requested:
                    raise ValueError("evaluation ROI is outside the declared output selection")
                entity_indices = self._entity_indices(numeric, wanted, mesh)
                requested_times = tuple(
                    float(item.to_si().value) for item in evaluation.state_times
                )
                # Full declared saved coverage remains required even when this
                # criterion evaluates only an earlier subset of those states.
                self._state_indices(
                    numeric,
                    tuple(float(item.to_si().value) for item in revision.spec.outputs.saved_times),
                )
                state_indices = self._state_indices(numeric, requested_times)
                if not state_indices or not entity_indices:
                    raise PortError(
                        PortErrorCategory.QUALITY,
                        f"required result scope is absent for evaluation {evaluation_id}",
                    )
                units.add(Quantity(0.0, numeric.mapping.unit).to_si().unit)
                width = len(numeric.component_ids)
                for state_index in state_indices:
                    row = numeric.values[state_index]
                    values.extend(
                        abs(
                            Quantity(
                                row[entity_index * width + component_index], numeric.mapping.unit
                            )
                            .to_si()
                            .value
                        )
                        for entity_index in entity_indices
                    )
            if len(units) != 1 or not values:
                raise ValueError("numeric evaluations must contain one comparable SI dimension")
            thresholds = {item.parameter_id: item.value.to_si() for item in criterion.thresholds}
            if set(thresholds) != {"max_value"}:
                raise ValueError("peak_abs_value requires exactly a max_value threshold")
            unit = next(iter(units))
            if thresholds["max_value"].unit != unit:
                raise ValueError("threshold and numeric output dimensions differ")
            limit = float(thresholds["max_value"].value)
        except (PortError, ValueError, TypeError, OverflowError, struct.error) as error:
            return CriterionAssessment(
                criterion_id,
                "numeric",
                AssessmentStatus.UNVERIFIED,
                (),
                f"required numeric evidence is unavailable: {error}; {reason_prefix}",
            )
        measured_value = max(values)
        measured = (MeasuredValue(f"{metric_id}.measured", measured_value, unit),)
        status = AssessmentStatus.PASS if measured_value <= limit else AssessmentStatus.FAIL
        reason = f"measured {measured_value:.9g} {unit} against max_value {limit:.9g} {unit}; {reason_prefix}"
        return CriterionAssessment(criterion_id, "numeric", status, measured, reason)

    @staticmethod
    def _validate_binding(
        numeric: NumericResultData,
        manifest: ResultManifest,
        mapping: OutputMapping,
        request: OutputRequest,
        observation: OutputObservation,
        data: ResultDataPort,
    ) -> None:
        if numeric.reference.codec_id != "numeric-result-v1":
            raise PortError(PortErrorCategory.QUALITY, "numeric result uses an unsupported codec")
        if (
            numeric.reference.bundle_digest != manifest.bundle_digest
            or numeric.reference.attempt_id != manifest.attempt_id
            or numeric.reference != observation.data_ref
            or replace(numeric.mapping, unit=mapping.unit) != mapping
            or Quantity(0, numeric.mapping.unit).dimension != Quantity(0, mapping.unit).dimension
            or Quantity(0, request.display_unit).dimension != Quantity(0, mapping.unit).dimension
            or numeric.mapping.location != request.location
            or numeric.mapping.measure_id != request.measure_id
            or numeric.mapping.frame != request.frame
        ):
            raise PortError(PortErrorCategory.QUALITY, "numeric result is bound to another output")
        if (
            observation.value_type != numeric.mapping.value_type
            or observation.unit != numeric.mapping.unit
            or observation.state_count != len(numeric.axis_values)
            or data.resolve(numeric.reference) != numeric
        ):
            raise PortError(PortErrorCategory.QUALITY, "numeric observation/reference disagrees")
        manifest_paths = {entry.logical_path for entry in manifest.files if entry.role == "result"}
        if numeric.reference.logical_path not in manifest_paths:
            raise PortError(PortErrorCategory.QUALITY, "numeric result path is not in the manifest")

    @staticmethod
    def _component_index(numeric: NumericResultData, component_id: str) -> int:
        try:
            return numeric.component_ids.index(component_id)
        except ValueError as error:
            raise PortError(
                PortErrorCategory.QUALITY,
                f"requested component is absent: {component_id}",
            ) from error

    @staticmethod
    def _state_indices(
        numeric: NumericResultData, requested_times: tuple[float, ...]
    ) -> tuple[int, ...]:
        if (
            numeric.axis_id != "state_time"
            or Quantity(0, numeric.axis_unit).dimension != Quantity(0, "s").dimension
        ):
            raise ValueError("numeric axis must represent state time")
        indices: list[int] = []
        for target in requested_times:
            # Supported FEBio XPLT saves float32 time. Accept that exact
            # representation, not a broad tolerance that could hide a missing endpoint.
            native_target = struct.unpack("<f", struct.pack("<f", target))[0]
            if target != 0 and native_target == 0:
                raise ValueError("requested time underflows the supported native representation")
            # Compare in the stored axis unit to avoid an extra conversion's
            # roundoff. No proximity tolerance may turn an earlier state into an endpoint.
            targets = {
                float(Quantity(value, "s").convert_to(numeric.axis_unit).value)
                for value in (target, native_target)
            }
            matches = [index for index, axis in enumerate(numeric.axis_values) if axis in targets]
            if len(matches) != 1:
                raise PortError(PortErrorCategory.QUALITY, "requested result state is absent")
            indices.append(matches[0])
        if len(set(indices)) != len(indices):
            raise ValueError("distinct requested times collapse onto one numeric state")
        return tuple(indices)

    @staticmethod
    def _entity_ids(selection: SelectionRef, location: str, mesh: MeshArtifact) -> set[str]:
        digest = hashlib.sha256(selection.to_bytes()).hexdigest()
        candidates = [item for item in mesh.sets if item.source_selection_digest == digest]
        body = selection.body_id.value
        if not candidates or any(item.body_id != body for item in candidates):
            raise ValueError("ROI binding is missing or crosses body ownership")
        if location == "rigid_body":
            if any(
                item.kind not in {"body", "node", "face"}
                or (item.kind == "body" and tuple(item.member_ids) != (body,))
                for item in candidates
            ):
                raise ValueError("rigid ROI must identify one body or its node/face projection")
            return {body}
        matching = [item for item in candidates if item.kind == location]
        if not matching and location == "node":
            faces = [item for item in candidates if item.kind == "face"]
            if len(faces) == 1:
                return {
                    str(node)
                    for face in mesh.faces
                    if face.face_id in faces[0].member_ids
                    for node in face.node_ids
                }
        if location not in {"node", "element"} or len(matching) != 1:
            raise ValueError(f"ROI does not resolve to one supported {location} set")
        return {str(member) for member in matching[0].member_ids}

    @staticmethod
    def _entity_indices(
        numeric: NumericResultData, wanted: set[str], mesh: MeshArtifact
    ) -> tuple[int, ...]:
        allowed = {
            "node": {str(item.node_id) for item in mesh.nodes},
            "element": {str(item.element_id) for item in mesh.elements},
            "rigid_body": {item.body_id for item in mesh.elements},
        }.get(numeric.mapping.location, set())
        actual = set(numeric.entity_ids)
        if not wanted or not wanted <= actual or not actual <= allowed:
            raise ValueError("requested ROI is undercovered or numeric entities are foreign")
        return tuple(
            index for index, entity_id in enumerate(numeric.entity_ids) if entity_id in wanted
        )


__all__ = ["QualityAdapter"]
