"""Declared three-size global tet10 studies over sealed case-local force histories."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import replace
from itertools import combinations, pairwise

from febio_cae.adapters.febio._native_qualification import has_qualified_runtime
from febio_cae.adapters.febio.planar_quality import assess_planar_requirements
from febio_cae.adapters.febio.reported_norms import assess_reported_norms, assess_reported_residual
from febio_cae.domain import (
    AssessmentStatus,
    CaseRevision,
    CriterionAssessment,
    IsotropicLinearElastic,
    MeasuredValue,
    MeshArtifact,
    Quantity,
    ResultManifest,
)
from febio_cae.domain.canonical import canonical_bytes
from febio_cae.domain.results import numeric_state_indices
from febio_cae.storage.comparison import ComparisonTarget, successful_targets
from febio_cae.storage.registry import CaseStorage

from ._comparison import _curve


def _refinement_error(curves: Sequence[Sequence[float]], floor: float) -> float:
    if (
        len(curves) != 3
        or not curves[0]
        or any(len(curve) != len(curves[0]) for curve in curves)
        or not math.isfinite(floor)
        or floor <= 0
        or any(not math.isfinite(value) for curve in curves for value in curve)
        or max(abs(value) for curve in curves for value in curve) <= floor
    ):
        raise ValueError("refinement needs three complete finite curves with resolved force signal")
    return max(
        abs(after - before) / max(abs(after), floor)
        for first, second in pairwise(curves)
        for before, after in zip(first, second, strict=True)
    )


def _geometry(mesh: MeshArtifact, body: str | None = None) -> tuple[str, int, float]:
    coordinates = {node.node_id: tuple(node.coordinates_si) for node in mesh.nodes}
    elements = tuple(
        element for element in mesh.elements if body is None or element.body_id == body
    )
    if not elements:
        raise ValueError("refinement mesh has no requested body elements")
    geometry = sorted(
        (element.body_id, sorted(coordinates[node] for node in element.node_ids))
        for element in elements
    )
    longest_edge = max(
        math.dist(coordinates[left], coordinates[right])
        for element in elements
        for left, right in combinations(element.node_ids[:4], 2)
    )
    return hashlib.sha256(canonical_bytes(geometry)).hexdigest(), len(elements), longest_edge


def _same_physics(candidate: CaseRevision, current: CaseRevision) -> bool:
    before, after = candidate.spec, current.spec
    if (
        candidate.case_id != current.case_id
        or not isinstance(before.material, IsotropicLinearElastic)
        or not isinstance(after.material, IsotropicLinearElastic)
    ):
        return False
    restored_mesh = replace(
        before.mesh_policy,
        global_size=after.mesh_policy.global_size,
        quality_profile=after.mesh_policy.quality_profile,
    )
    if restored_mesh.to_bytes() != after.mesh_policy.to_bytes():
        return False
    restored_material = replace(
        before.material,
        youngs_modulus=after.material.youngs_modulus,
        youngs_modulus_evidence=after.material.youngs_modulus_evidence,
    )
    return (
        replace(before, material=restored_material, mesh_policy=after.mesh_policy).to_bytes()
        == after.to_bytes()
    )


def assess_mesh_refinement(
    manifest: ResultManifest, revision: CaseRevision, storage: CaseStorage
) -> tuple[CriterionAssessment, dict[str, object]]:
    evidence: dict[str, object] = {"scope": "three_declared_global_tet10_sizes"}
    try:
        criteria = [
            item
            for item in revision.spec.quality_policy.criteria
            if item.metric_id == "mesh_dependence"
        ]
        if len(criteria) != 1:
            raise ValueError("one explicit mesh_dependence criterion is required")
        criterion = criteria[0]
        thresholds = {item.parameter_id: item.value for item in criterion.thresholds}
        required = {"coarse_size", "refined_size", "fine_size", "relative_max", "absolute_floor"}
        if set(thresholds) not in (required, required | {"material_scaling_relative_max"}):
            raise ValueError("mesh study requires declared sizes, relative bound and force floor")
        values = {}
        for name, quantity in thresholds.items():
            unit = "m" if name.endswith("_size") else "N" if name == "absolute_floor" else "1"
            if quantity.dimension != Quantity(1, unit).dimension:
                raise ValueError("mesh study threshold has an incompatible unit")
            value = float(quantity.to_si().value)
            if (
                not math.isfinite(value)
                or value < 0
                or (value == 0 and (name.endswith("_size") or name == "absolute_floor"))
            ):
                raise ValueError(
                    "mesh study needs positive sizes/floor and nonnegative relative bounds"
                )
            values[name] = value
        sizes = tuple(values[name] for name in ("coarse_size", "refined_size", "fine_size"))
        if (
            not sizes[0] > sizes[1] > sizes[2]
            or revision.spec.mesh_policy.global_size.to_si().value != sizes[2]
        ):
            raise ValueError(
                "current revision must be the finest of three decreasing declared sizes"
            )
        if len(criterion.evaluation_ids) != 1:
            raise ValueError("mesh study requires one explicit whole-tool force evaluation")
        evaluations = [
            item
            for item in revision.spec.outputs.evaluations
            if item.evaluation_id == criterion.evaluation_ids[0]
        ]
        if len(evaluations) != 1:
            raise ValueError("mesh evaluation is unavailable")
        evaluation = evaluations[0]
        requests = [
            item
            for item in revision.spec.outputs.requests
            if item.request_id == evaluation.output_request_id
        ]
        if (
            len(requests) != 1
            or requests[0].quantity_id != "contact_force"
            or evaluation.aggregation_id != "identity"
            or evaluation.selection != requests[0].selection
        ):
            raise ValueError("mesh evaluation must preserve the whole-tool contact force")
        times = tuple(float(time.to_si().value) for time in evaluation.state_times)
        endpoint = float(revision.spec.motion.samples[-1].time.to_si().value)
        if not times or endpoint not in times:
            raise ValueError("mesh evaluation must include the declared endpoint")
        groups: dict[float, dict[float, tuple[ComparisonTarget, tuple[float, ...]]]] = {}
        current: ComparisonTarget | None = None
        current_curve: tuple[float, ...] | None = None
        for candidate in successful_targets(storage):
            if not _same_physics(candidate.revision, revision) or not has_qualified_runtime(
                candidate.bundle
            ):
                continue
            size = float(candidate.revision.spec.mesh_policy.global_size.to_si().value)
            if size not in sizes:
                continue
            context = storage.resolve_reported_norms_context(candidate.manifest)
            report = assess_reported_norms(
                candidate.manifest, candidate.revision, candidate.mesh, candidate.profile, context
            ).to_dict()
            residual = assess_reported_residual(report, candidate.revision.spec.solver_policy)
            planar = assess_planar_requirements(
                candidate.manifest, candidate.revision, candidate.mesh, candidate.profile, storage
            )
            if (
                report["final_status"] != "PASS"
                or residual.status is not AssessmentStatus.PASS
                or any(row.status is not AssessmentStatus.PASS for row in planar)
            ):
                raise ValueError(
                    "a matching planned study lacks complete qualified numerical evidence"
                )
            data, force = _curve(storage, candidate, "contact_force")
            curve = tuple(force[index] for index in numeric_state_indices(data, times))
            material = candidate.revision.spec.material
            assert isinstance(material, IsotropicLinearElastic)
            modulus = float(material.youngs_modulus.to_si().value)
            group = groups.setdefault(modulus, {})
            previous = group.get(size)
            if previous is not None and (
                _geometry(previous[0].mesh) != _geometry(candidate.mesh) or previous[1] != curve
            ):
                raise ValueError("repeated planned size has conflicting mesh or force evidence")
            group[size] = candidate, curve
            if candidate.manifest.manifest_id == manifest.manifest_id:
                current, current_curve = candidate, curve
        if current is None or current_curve is None:
            raise ValueError("current manifest is not an eligible registered study result")
        material = revision.spec.material
        assert isinstance(material, IsotropicLinearElastic)
        modulus = float(material.youngs_modulus.to_si().value)
        complete = {youngs: group for youngs, group in groups.items() if set(group) == set(sizes)}
        baseline_modulus = modulus if modulus in complete else None
        ancestor_id = revision.parent_revision_id
        while baseline_modulus is None and ancestor_id is not None:
            ancestor = storage.get_revision(revision.case_id, ancestor_id)
            if (
                not _same_physics(ancestor, revision)
                or ancestor.spec.mesh_policy.global_size.to_si().value != sizes[2]
            ):
                break
            ancestor_material = ancestor.spec.material
            assert isinstance(ancestor_material, IsotropicLinearElastic)
            ancestor_modulus = float(ancestor_material.youngs_modulus.to_si().value)
            if ancestor_modulus in complete:
                baseline_modulus = ancestor_modulus
            ancestor_id = ancestor.parent_revision_id
        if baseline_modulus is None:
            raise ValueError(
                "a complete current or material-ancestor three-size study is unavailable"
            )
        study = tuple(complete[baseline_modulus][size] for size in sizes)
        geometry = tuple(
            _geometry(item.mesh, revision.spec.geometry.body_id.value) for item, _ in study
        )
        if (
            len({item[0] for item in geometry}) != 3
            or not geometry[0][1] < geometry[1][1] < geometry[2][1]
            or not geometry[0][2] > geometry[1][2] > geometry[2][2]
        ):
            raise ValueError(
                "both refinements must increase part elements and decrease observed maximum part edge"
            )
        if _geometry(current.mesh) != _geometry(study[-1][0].mesh):
            raise ValueError("current finest mesh differs geometrically from the qualified study")
        error = _refinement_error(tuple(curve for _, curve in study), values["absolute_floor"])
        evidence["inputs"] = [
            {
                "manifest_id": item.manifest.manifest_id,
                "manifest_digest": hashlib.sha256(item.manifest.to_bytes()).hexdigest(),
                "mesh_digest": item.mesh.artifact_digest,
                "global_size_m": size,
                "part_elements": shape[1],
                "maximum_part_edge_m": shape[2],
            }
            for (item, _), size, shape in zip(study, sizes, geometry, strict=True)
        ]
        evidence["evaluation_times_s"] = list(times)
        measured = [MeasuredValue("maximum_successive_relative_force_change", error, "1")]
        passed = error <= values["relative_max"]
        if modulus != baseline_modulus:
            if "material_scaling_relative_max" not in values:
                raise ValueError("material-only study transfer requires an explicit scaling bound")
            normalized = tuple(value * baseline_modulus / modulus for value in current_curve)
            transfer_error = _refinement_error(
                (study[-1][1], normalized, normalized), values["absolute_floor"]
            )
            measured.append(
                MeasuredValue("material_only_relative_force_scaling_error", transfer_error, "1")
            )
            evidence["material_modulus_ratio"] = modulus / baseline_modulus
            passed = passed and transfer_error <= values["material_scaling_relative_max"]
        status = AssessmentStatus.PASS if passed else AssessmentStatus.FAIL
        return CriterionAssessment(
            "mesh_dependence",
            "numeric",
            status,
            tuple(measured),
            "two successive registered global refinements evaluated over the declared force states",
        ), evidence
    except (ValueError, TypeError, KeyError, OverflowError) as error:
        return CriterionAssessment(
            "mesh_dependence",
            "numeric",
            AssessmentStatus.UNVERIFIED,
            (),
            f"registered refinement study is unavailable: {error}",
        ), evidence
