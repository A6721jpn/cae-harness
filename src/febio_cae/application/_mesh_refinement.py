"""Declared global and source-local tet10 studies over sealed case-local histories."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from itertools import combinations, pairwise

from febio_cae.adapters.febio._native_qualification import has_qualified_runtime
from febio_cae.adapters.febio.planar_quality import assess_planar_requirements
from febio_cae.adapters.febio.reported_norms import assess_reported_norms, assess_reported_residual
from febio_cae.adapters.geometry.adapter import _point_values, _transform_point
from febio_cae.domain import (
    TET10_CORNER_NODE_POSITIONS,
    TET10_EDGE_NODE_POSITIONS,
    AssessmentStatus,
    CaseRevision,
    CriterionAssessment,
    FrameId,
    IsotropicLinearElastic,
    LocalRefinement,
    MeasuredValue,
    MeshArtifact,
    MeshPolicy,
    NumericalProfileRef,
    QualityCriterion,
    Quantity,
    ResultManifest,
    RigidTransform,
    SourceLocalRefinementBall,
)
from febio_cae.domain.canonical import canonical_bytes
from febio_cae.domain.results import numeric_state_indices
from febio_cae.storage.comparison import ComparisonTarget, successful_targets
from febio_cae.storage.mesh_quality import (
    CurrentPreparationRegistration,
    MeshQualityRegistration,
    PlanarDemoRegistration,
)
from febio_cae.storage.preparation import PreparationStore
from febio_cae.storage.registry import CaseStorage, StorageIntegrityError

from ._comparison import _curve

_MESH_METRICS = frozenset({"mesh_dependence", "source_local_mesh_dependence"})
_MESH_THRESHOLD_NAMES = frozenset(
    {"coarse_size", "refined_size", "fine_size", "relative_max", "absolute_floor"}
)
_MATERIAL_SCALING_THRESHOLD = "material_scaling_relative_max"


@dataclass(frozen=True, slots=True)
class _MeshDependenceDeclaration:
    criterion: QualityCriterion
    metric_id: str
    sizes: tuple[float, float, float]
    relative_max: float
    absolute_floor: float
    material_scaling_relative_max: float | None


@dataclass(frozen=True, slots=True)
class _LocalBallMeasurement:
    corner_edge_count: int
    maximum_edge_m: float


@dataclass(frozen=True, slots=True)
class _LocalMeshMeasurement:
    balls: dict[str, _LocalBallMeasurement]
    body_element_counts: dict[str, int]


@dataclass(frozen=True, slots=True)
class _LocalCornerEdge:
    midpoint: tuple[float, float, float]
    length_m: float


@dataclass(frozen=True, slots=True)
class _LocalMeshIndex:
    frame: FrameId
    edges_by_body: dict[str, tuple[_LocalCornerEdge, ...]]
    body_element_counts: dict[str, int]


def _parse_mesh_dependence_declaration(
    criteria: Sequence[QualityCriterion],
) -> _MeshDependenceDeclaration:
    """Parse either mesh metric through one shared declaration contract."""

    if isinstance(criteria, (str, bytes)):
        raise ValueError("mesh study criteria are invalid")  # noqa: TRY004 - validation contract
    try:
        criterion_items = tuple(criteria)
    except TypeError as error:
        raise ValueError("mesh study criteria are invalid") from error
    if any(not isinstance(item, QualityCriterion) for item in criterion_items):
        raise ValueError("mesh study criteria are invalid")
    matching = tuple(item for item in criterion_items if item.metric_id in _MESH_METRICS)
    if len(matching) != 1:
        raise ValueError("exactly one mesh-dependence metric is required")
    criterion = matching[0]
    thresholds = {item.parameter_id: item.value for item in criterion.thresholds}
    if set(thresholds) not in (
        set(_MESH_THRESHOLD_NAMES),
        set(_MESH_THRESHOLD_NAMES) | {_MATERIAL_SCALING_THRESHOLD},
    ):
        raise ValueError("mesh study requires declared sizes, relative bound and force floor")

    values: dict[str, float] = {}
    for name, quantity in thresholds.items():
        unit = "m" if name.endswith("_size") else "N" if name == "absolute_floor" else "1"
        if quantity.dimension != Quantity(1, unit).dimension:
            raise ValueError("mesh study threshold has an incompatible unit")
        try:
            value = float(quantity.to_si().value)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("mesh study threshold is not SI-representable") from error
        if (
            not math.isfinite(value)
            or value < 0
            or (value == 0 and (name.endswith("_size") or name == "absolute_floor"))
        ):
            raise ValueError(
                "mesh study needs positive sizes/floor and nonnegative relative bounds"
            )
        values[name] = value

    sizes = (
        values["coarse_size"],
        values["refined_size"],
        values["fine_size"],
    )
    if not sizes[0] > sizes[1] > sizes[2] > 0:
        raise ValueError("refinement sizes must be positive and strictly decreasing")
    if (
        criterion.metric_id == "source_local_mesh_dependence"
        and _MATERIAL_SCALING_THRESHOLD in values
    ):
        raise ValueError("source-local mesh studies do not support material scaling")
    if len(criterion.evaluation_ids) != 1:
        raise ValueError("mesh study requires one explicit whole-tool force evaluation")
    return _MeshDependenceDeclaration(
        criterion,
        criterion.metric_id,
        sizes,
        values["relative_max"],
        values["absolute_floor"],
        values.get(_MATERIAL_SCALING_THRESHOLD),
    )


def _mesh_evaluation_times(
    revision: CaseRevision, declaration: _MeshDependenceDeclaration
) -> tuple[float, ...]:
    criterion = declaration.criterion
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
    return times


def _global_stage_size(policy: MeshPolicy, declaration: _MeshDependenceDeclaration) -> float:
    try:
        value = float(policy.global_size.to_si().value)
    except (AttributeError, TypeError, ValueError, OverflowError) as error:
        raise ValueError("global mesh size is invalid") from error
    if not math.isfinite(value) or value <= 0:
        raise ValueError("global mesh size is invalid")
    if value not in declaration.sizes:
        raise ValueError("mesh size is outside the declared refinement study")
    return value


def _local_stage_size(policy: MeshPolicy, declaration: _MeshDependenceDeclaration) -> float:
    if not policy.local_refinements:
        raise ValueError("source-local refinement requires nonempty explicit balls")
    sizes: list[float] = []
    for item in policy.local_refinements:
        if not isinstance(item, LocalRefinement) or not isinstance(
            item.region, SourceLocalRefinementBall
        ):
            raise ValueError("source-local refinement requires explicit balls for every entry")  # noqa: TRY004 - validation contract
        try:
            size = float(item.size.to_si().value)
        except (AttributeError, TypeError, ValueError, OverflowError) as error:
            raise ValueError("source-local refinement target size is invalid") from error
        if not math.isfinite(size) or size <= 0:
            raise ValueError("source-local refinement target size is invalid")
        sizes.append(size)
    if len(set(sizes)) != 1:
        raise ValueError("source-local refinement entries must share one staged target size")
    value = sizes[0]
    if value not in declaration.sizes:
        raise ValueError("source-local refinement target is outside the declared study")
    return value


def _next_stage(
    previous: float, requested: float, declaration: _MeshDependenceDeclaration, ceiling: int
) -> None:
    try:
        index = declaration.sizes.index(previous)
    except ValueError as error:
        raise ValueError("parent mesh size is outside the declared refinement study") from error
    next_index = index + 1
    if (
        next_index >= len(declaration.sizes)
        or next_index > ceiling
        or requested != declaration.sizes[next_index]
    ):
        raise ValueError("refinement must use the next declared mesh size")


def _normalised_local_policy(policy: MeshPolicy) -> MeshPolicy:
    """Return a policy with only local target sizes projected to one neutral value."""

    neutral_size = Quantity(float(policy.global_size.to_si().value), "m")
    return replace(
        policy,
        local_refinements=tuple(
            replace(item, size=neutral_size) for item in policy.local_refinements
        ),
    )


def _normalised_admission_revision(
    revision: CaseRevision, registration: CurrentPreparationRegistration
) -> CaseRevision:
    """Project a verified preparation admission back to its shared generator."""

    return replace(
        revision,
        spec=replace(
            revision.spec,
            mesh_policy=replace(
                revision.spec.mesh_policy,
                quality_profile=registration.generation_profile,
            ),
        ),
    )


def _authenticated_prepared_revision(
    storage: CaseStorage,
    revision: CaseRevision,
    mesh: MeshArtifact | None = None,
    *,
    common_generation_profile: NumericalProfileRef | None = None,
) -> tuple[CaseRevision, CurrentPreparationRegistration]:
    """Authenticate current-preparation mesh provenance before comparing physics."""

    registration = storage.resolve_revision_mesh_quality(revision)
    if not isinstance(registration, CurrentPreparationRegistration):
        raise ValueError("refinement requires a current preparation admission")  # noqa: TRY004 - validation contract
    if registration.reference != revision.spec.mesh_policy.quality_profile:
        raise ValueError("revision mesh quality admission reference is not registered")
    registration.check_spec(revision.spec)
    generation = storage.resolve_mesh_quality(registration.generation_profile)
    if not isinstance(generation, MeshQualityRegistration):
        raise ValueError("preparation admission does not preserve a generation profile")  # noqa: TRY004 - validation contract
    if revision.spec.rigid_tool.primitive.kind not in generation.primitive_kinds:
        raise ValueError("preparation generation profile does not cover the declared primitive")
    expected = PreparationStore(storage).mesh(registration, revision)
    if mesh is not None and expected.to_bytes() != mesh.to_bytes():
        raise ValueError("registered preparation mesh differs from the comparison mesh")
    if (
        common_generation_profile is not None
        and registration.generation_profile != common_generation_profile
    ):
        raise ValueError("refinement stages do not share one generation profile")
    return _normalised_admission_revision(revision, registration), registration


def _global_comparison_revision(
    storage: CaseStorage,
    revision: CaseRevision,
    mesh: MeshArtifact | None = None,
    *,
    common_generation_profile: NumericalProfileRef | None = None,
) -> tuple[
    CaseRevision,
    MeshQualityRegistration | PlanarDemoRegistration | CurrentPreparationRegistration,
]:
    """Resolve one global candidate without weakening its admission identity."""

    registration = storage.resolve_revision_mesh_quality(revision)
    if isinstance(registration, CurrentPreparationRegistration):
        return _authenticated_prepared_revision(
            storage,
            revision,
            mesh,
            common_generation_profile=common_generation_profile,
        )
    if not isinstance(registration, (MeshQualityRegistration, PlanarDemoRegistration)):
        raise ValueError("global refinement requires a registered mesh-quality admission")  # noqa: TRY004 - validation contract
    if registration.reference != revision.spec.mesh_policy.quality_profile:
        raise ValueError("revision mesh quality admission reference is not registered")
    if isinstance(registration, PlanarDemoRegistration):
        registration.check_spec(revision.spec)
    return revision, registration


def _same_local_physics(candidate: CaseRevision, current: CaseRevision) -> bool:
    """Compare local candidates after verified admission projection and size normalisation."""

    if (
        candidate.case_id != current.case_id
        or not isinstance(candidate.spec.material, IsotropicLinearElastic)
        or not isinstance(current.spec.material, IsotropicLinearElastic)
    ):
        return False
    try:
        if (
            float(candidate.spec.mesh_policy.global_size.to_si().value)
            != float(current.spec.mesh_policy.global_size.to_si().value)
            or _normalised_local_policy(candidate.spec.mesh_policy).to_bytes()
            != _normalised_local_policy(current.spec.mesh_policy).to_bytes()
        ):
            return False
        return (
            replace(
                candidate.spec,
                mesh_policy=_normalised_local_policy(candidate.spec.mesh_policy),
            ).to_bytes()
            == replace(
                current.spec,
                mesh_policy=_normalised_local_policy(current.spec.mesh_policy),
            ).to_bytes()
        )
    except (AttributeError, TypeError, ValueError, OverflowError):
        return False


def validate_next_refinement(
    parent: MeshPolicy,
    requested: MeshPolicy,
    criteria: Sequence[QualityCriterion],
) -> None:
    """Admit exactly one adjacent declared global or source-local refinement step."""

    if not isinstance(parent, MeshPolicy) or not isinstance(requested, MeshPolicy):
        raise ValueError("refinement policies are invalid")  # noqa: TRY004 - validation contract
    declaration = _parse_mesh_dependence_declaration(criteria)
    if declaration.metric_id == "mesh_dependence":
        previous = _global_stage_size(parent, declaration)
        requested_size = _global_stage_size(requested, declaration)
        if replace(parent, global_size=requested.global_size).to_bytes() != requested.to_bytes():
            raise ValueError("global refinement changed an unrelated mesh-policy field")
    else:
        previous = _local_stage_size(parent, declaration)
        requested_size = _local_stage_size(requested, declaration)
        if (
            float(parent.global_size.to_si().value) != float(requested.global_size.to_si().value)
            or _normalised_local_policy(parent).to_bytes()
            != _normalised_local_policy(requested).to_bytes()
        ):
            raise ValueError("source-local refinement changed an unrelated mesh-policy field")
    _next_stage(previous, requested_size, declaration, parent.max_refinements)


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
    coordinates = {node.node_id: list(node.coordinates_si) for node in mesh.nodes}
    elements = tuple(
        element for element in mesh.elements if body is None or element.body_id == body
    )
    if not elements:
        raise ValueError("refinement mesh has no requested body elements")
    geometry = sorted(
        [element.body_id, sorted(coordinates[node] for node in element.node_ids)]
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


def _qualified_force_curve(
    storage: CaseStorage, candidate: ComparisonTarget, times: tuple[float, ...]
) -> tuple[float, ...]:
    context = storage.resolve_reported_norms_context(candidate.manifest)
    report = assess_reported_norms(
        candidate.manifest,
        candidate.revision,
        candidate.mesh,
        candidate.profile,
        context,
    ).to_dict()
    residual = assess_reported_residual(report, candidate.revision.spec.solver_policy)
    planar = assess_planar_requirements(
        candidate.manifest,
        candidate.revision,
        candidate.mesh,
        candidate.profile,
        storage,
    )
    if (
        report["final_status"] != "PASS"
        or residual.status is not AssessmentStatus.PASS
        or any(row.status is not AssessmentStatus.PASS for row in planar)
    ):
        raise ValueError("a matching planned study lacks complete qualified numerical evidence")
    data, force = _curve(storage, candidate, "contact_force")
    return tuple(force[index] for index in numeric_state_indices(data, times))


def _local_transform(candidate: CaseRevision, body_id: str) -> RigidTransform:
    geometry = candidate.spec.geometry
    primitive = candidate.spec.rigid_tool.primitive
    if body_id == geometry.body_id.value:
        return geometry.placement
    if body_id == primitive.body_id.value:
        return primitive.placement
    raise ValueError("source-local refinement identifies an undeclared body")


def _local_mesh_index(mesh: MeshArtifact, bodies: set[str]) -> _LocalMeshIndex:
    """Build one reusable coordinate, body-count and corner-edge index per mesh."""

    coordinates = {node.node_id: tuple(node.coordinates_si) for node in mesh.nodes}
    body_element_counts = {body: 0 for body in bodies}
    edges_by_body: dict[str, dict[tuple[int, int], _LocalCornerEdge]] = {
        body: {} for body in bodies
    }
    for element in mesh.elements:
        body_id = element.body_id
        if body_id not in bodies:
            continue
        body_element_counts[body_id] += 1
        corners = tuple(element.node_ids[index] for index in TET10_CORNER_NODE_POSITIONS)
        edges = edges_by_body[body_id]
        for left_index, right_index in TET10_EDGE_NODE_POSITIONS:
            edge = (
                min(corners[left_index], corners[right_index]),
                max(corners[left_index], corners[right_index]),
            )
            if edge in edges:
                continue
            try:
                left = coordinates[edge[0]]
                right = coordinates[edge[1]]
            except KeyError as error:
                raise ValueError("source-local mesh edge references an unknown node") from error
            midpoint = (
                (left[0] + right[0]) / 2,
                (left[1] + right[1]) / 2,
                (left[2] + right[2]) / 2,
            )
            length = math.dist(left, right)
            if not math.isfinite(length) or length <= 0:
                raise ValueError("source-local mesh contains an invalid corner edge")
            edges[edge] = _LocalCornerEdge(midpoint, length)
    if any(count <= 0 for count in body_element_counts.values()):
        raise ValueError("source-local mesh evidence lacks a refined body")
    return _LocalMeshIndex(
        mesh.frame,
        {
            body: tuple(record for _, record in sorted(edges.items()))
            for body, edges in edges_by_body.items()
        },
        body_element_counts,
    )


def _local_ball_measurement(
    revision: CaseRevision,
    refinement: LocalRefinement,
    index: _LocalMeshIndex,
) -> _LocalBallMeasurement:
    region = refinement.region
    if not isinstance(region, SourceLocalRefinementBall):
        raise ValueError("source-local refinement evidence requires an explicit ball")  # noqa: TRY004 - validation contract
    body_id = refinement.selection.body_id.value
    transform = _local_transform(revision, body_id)
    if index.frame != transform.target_frame or region.center.frame != transform.source_frame:
        raise ValueError("source-local ball and mesh are not in compatible physical frames")
    center = _transform_point(_point_values(region.center), transform)
    radius = float(region.radius.to_si().value)
    if not math.isfinite(radius) or radius <= 0:
        raise ValueError("source-local ball radius is invalid")

    in_ball = tuple(
        edge
        for edge in index.edges_by_body.get(body_id, ())
        if math.dist(edge.midpoint, center) <= radius
    )
    if len(in_ball) < 3:
        raise ValueError(
            f"source-local ball {refinement.refinement_id!r} has fewer than three in-ball edges"
        )
    return _LocalBallMeasurement(len(in_ball), max(edge.length_m for edge in in_ball))


def _local_mesh_measurement(
    revision: CaseRevision, mesh: MeshArtifact, refinement: LocalRefinement
) -> _LocalBallMeasurement:
    """Measure one ball while retaining the historical private helper boundary."""

    return _local_ball_measurement(
        revision,
        refinement,
        _local_mesh_index(mesh, {refinement.selection.body_id.value}),
    )


def _local_mesh_measurements(revision: CaseRevision, mesh: MeshArtifact) -> _LocalMeshMeasurement:
    refinements = tuple(revision.spec.mesh_policy.local_refinements)
    if not refinements or not any(
        item.selection.body_id == revision.spec.geometry.body_id for item in refinements
    ):
        raise ValueError("source-local mesh evidence requires a deformable-part ball")
    bodies = {item.selection.body_id.value for item in refinements}
    index = _local_mesh_index(mesh, bodies)
    balls = {
        item.refinement_id: _local_ball_measurement(revision, item, index) for item in refinements
    }
    return _LocalMeshMeasurement(balls, index.body_element_counts)


def _assess_source_local_mesh_refinement(
    manifest: ResultManifest,
    revision: CaseRevision,
    storage: CaseStorage,
    declaration: _MeshDependenceDeclaration,
    evidence: dict[str, object],
) -> tuple[CriterionAssessment, dict[str, object]]:
    evidence["scope"] = "three_declared_source_local_tet10_sizes"
    current_size = _local_stage_size(revision.spec.mesh_policy, declaration)
    if current_size != declaration.sizes[2]:
        raise ValueError("current revision must use the finest local declared size")
    times = _mesh_evaluation_times(revision, declaration)
    groups: dict[float, tuple[ComparisonTarget, tuple[float, ...], _LocalMeshMeasurement]] = {}
    current: ComparisonTarget | None = None
    current_curve: tuple[float, ...] | None = None
    targets = successful_targets(storage)
    current_targets = tuple(
        candidate for candidate in targets if candidate.manifest.manifest_id == manifest.manifest_id
    )
    if len(current_targets) != 1 or current_targets[0].revision.to_bytes() != revision.to_bytes():
        raise ValueError("current manifest is not an eligible registered local study result")
    current_target = current_targets[0]
    current_revision, current_registration = _authenticated_prepared_revision(
        storage, current_target.revision, current_target.mesh
    )
    for candidate in targets:
        try:
            stage_size = _local_stage_size(candidate.revision.spec.mesh_policy, declaration)
            comparable_revision = (
                current_revision
                if candidate.manifest.manifest_id == current_target.manifest.manifest_id
                else _authenticated_prepared_revision(
                    storage,
                    candidate.revision,
                    candidate.mesh,
                    common_generation_profile=current_registration.generation_profile,
                )[0]
            )
        except (AttributeError, TypeError, ValueError, OverflowError):
            if candidate.manifest.manifest_id == current_target.manifest.manifest_id:
                raise
            continue
        if not _same_local_physics(
            comparable_revision, current_revision
        ) or not has_qualified_runtime(candidate.bundle):
            continue
        curve = _qualified_force_curve(storage, candidate, times)
        measurements = _local_mesh_measurements(candidate.revision, candidate.mesh)
        previous = groups.get(stage_size)
        if previous is not None and (
            _geometry(previous[0].mesh) != _geometry(candidate.mesh)
            or previous[1] != curve
            or previous[2] != measurements
        ):
            raise ValueError("repeated planned local size has conflicting mesh or force evidence")
        groups[stage_size] = candidate, curve, measurements
        if candidate.manifest.manifest_id == manifest.manifest_id:
            current = candidate
            current_curve = curve
    if current is None or current_curve is None:
        raise ValueError("current manifest is not an eligible registered local study result")
    if set(groups) != set(declaration.sizes):
        raise ValueError("a complete current three-size local study is unavailable")

    study = tuple(groups[size] for size in declaration.sizes)
    if _geometry(current.mesh) != _geometry(study[-1][0].mesh):
        raise ValueError("current finest local mesh differs geometrically from the qualified study")
    local_refinements = tuple(revision.spec.mesh_policy.local_refinements)
    if not any(
        item.selection.body_id == revision.spec.geometry.body_id for item in local_refinements
    ):
        raise ValueError("source-local mesh evidence requires a deformable-part ball")

    for refinement in local_refinements:
        ball_values = tuple(
            measurements.balls[refinement.refinement_id] for _, _, measurements in study
        )
        if not all(item.corner_edge_count >= 3 for item in ball_values):
            raise ValueError("source-local ball has insufficient in-ball edge evidence")
    refined_bodies = sorted({item.selection.body_id.value for item in local_refinements})
    body_growth = {
        body: tuple(measurements.body_element_counts[body] for _, _, measurements in study)
        for body in refined_bodies
    }
    observed_growth = {
        refinement.refinement_id: tuple(
            measurements.balls[refinement.refinement_id] for _, _, measurements in study
        )
        for refinement in local_refinements
    }
    error = _refinement_error(tuple(curve for _, curve, _ in study), declaration.absolute_floor)
    force_passed = error <= declaration.relative_max
    local_passed = all(
        ball_values[0].corner_edge_count
        < ball_values[1].corner_edge_count
        < ball_values[2].corner_edge_count
        and ball_values[0].maximum_edge_m
        > ball_values[1].maximum_edge_m
        > ball_values[2].maximum_edge_m
        for ball_values in observed_growth.values()
    )
    body_passed = all(
        element_counts[0] < element_counts[1] < element_counts[2]
        for element_counts in body_growth.values()
    )
    evidence["inputs"] = [
        {
            "manifest_id": item.manifest.manifest_id,
            "manifest_digest": hashlib.sha256(item.manifest.to_bytes()).hexdigest(),
            "mesh_digest": item.mesh.artifact_digest,
            "global_size_m": float(item.revision.spec.mesh_policy.global_size.to_si().value),
            "local_target_size_m": size,
            "body_element_counts": dict(measurements.body_element_counts),
            "local_balls": {
                identifier: {
                    "corner_edge_count": measurement.corner_edge_count,
                    "maximum_edge_m": measurement.maximum_edge_m,
                }
                for identifier, measurement in measurements.balls.items()
            },
        }
        for (item, _, measurements), size in zip(study, declaration.sizes, strict=True)
    ]
    evidence["evaluation_times_s"] = list(times)
    measured = [
        MeasuredValue("maximum_successive_relative_force_change", error, "1"),
    ]
    for identifier, values in observed_growth.items():
        measured.extend(
            (
                MeasuredValue(
                    f"{identifier}.coarse_corner_edge_count",
                    values[0].corner_edge_count,
                    "1",
                ),
                MeasuredValue(
                    f"{identifier}.refined_corner_edge_count",
                    values[1].corner_edge_count,
                    "1",
                ),
                MeasuredValue(
                    f"{identifier}.fine_corner_edge_count",
                    values[2].corner_edge_count,
                    "1",
                ),
                MeasuredValue(f"{identifier}.coarse_maximum_edge", values[0].maximum_edge_m, "m"),
                MeasuredValue(f"{identifier}.refined_maximum_edge", values[1].maximum_edge_m, "m"),
                MeasuredValue(f"{identifier}.fine_maximum_edge", values[2].maximum_edge_m, "m"),
            )
        )
    for body, element_counts in body_growth.items():
        measured.extend(
            MeasuredValue(f"{body}.{stage}_element_count", value, "1")
            for stage, value in zip(("coarse", "refined", "fine"), element_counts, strict=True)
        )
    status = (
        AssessmentStatus.PASS
        if force_passed and local_passed and body_passed
        else AssessmentStatus.FAIL
    )
    return (
        CriterionAssessment(
            declaration.criterion.criterion_id,
            "numeric",
            status,
            tuple(measured),
            "two successive registered source-local refinements evaluated over the declared force states",
        ),
        evidence,
    )


def assess_mesh_refinement(
    manifest: ResultManifest, revision: CaseRevision, storage: CaseStorage
) -> tuple[CriterionAssessment, dict[str, object]]:
    evidence: dict[str, object] = {"scope": "three_declared_global_tet10_sizes"}
    criterion_id = "mesh_dependence"
    try:
        declaration = _parse_mesh_dependence_declaration(revision.spec.quality_policy.criteria)
        if declaration.metric_id == "source_local_mesh_dependence":
            criterion_id = declaration.criterion.criterion_id
            return _assess_source_local_mesh_refinement(
                manifest, revision, storage, declaration, evidence
            )
        values = {
            "coarse_size": declaration.sizes[0],
            "refined_size": declaration.sizes[1],
            "fine_size": declaration.sizes[2],
            "relative_max": declaration.relative_max,
            "absolute_floor": declaration.absolute_floor,
        }
        if declaration.material_scaling_relative_max is not None:
            values[_MATERIAL_SCALING_THRESHOLD] = declaration.material_scaling_relative_max
        sizes = declaration.sizes
        if (
            not sizes[0] > sizes[1] > sizes[2]
            or revision.spec.mesh_policy.global_size.to_si().value != sizes[2]
        ):
            raise ValueError(
                "current revision must be the finest of three decreasing declared sizes"
            )
        times = _mesh_evaluation_times(revision, declaration)
        groups: dict[float, dict[float, tuple[ComparisonTarget, tuple[float, ...]]]] = {}
        current: ComparisonTarget | None = None
        current_curve: tuple[float, ...] | None = None
        targets = successful_targets(storage)
        current_targets = tuple(
            candidate
            for candidate in targets
            if candidate.manifest.manifest_id == manifest.manifest_id
        )
        if (
            len(current_targets) != 1
            or current_targets[0].revision.to_bytes() != revision.to_bytes()
        ):
            raise ValueError("current manifest is not an eligible registered study result")
        current_target = current_targets[0]
        current_revision, current_registration = _global_comparison_revision(
            storage, current_target.revision, current_target.mesh
        )
        common_generation_profile = (
            current_registration.generation_profile
            if isinstance(current_registration, CurrentPreparationRegistration)
            else None
        )
        for candidate in targets:
            try:
                comparable_revision = (
                    current_revision
                    if candidate.manifest.manifest_id == current_target.manifest.manifest_id
                    else _global_comparison_revision(
                        storage,
                        candidate.revision,
                        candidate.mesh,
                        common_generation_profile=common_generation_profile,
                    )[0]
                )
            except (AttributeError, TypeError, ValueError, OverflowError):
                if candidate.manifest.manifest_id == current_target.manifest.manifest_id:
                    raise
                continue
            if not _same_physics(
                comparable_revision, current_revision
            ) or not has_qualified_runtime(candidate.bundle):
                continue
            size = float(candidate.revision.spec.mesh_policy.global_size.to_si().value)
            if size not in sizes:
                continue
            curve = _qualified_force_curve(storage, candidate, times)
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
            try:
                comparable_ancestor, _ = _global_comparison_revision(
                    storage,
                    ancestor,
                    common_generation_profile=common_generation_profile,
                )
            except (AttributeError, TypeError, ValueError, OverflowError):
                break
            if (
                not _same_physics(comparable_ancestor, current_revision)
                or comparable_ancestor.spec.mesh_policy.global_size.to_si().value != sizes[2]
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
            if _MATERIAL_SCALING_THRESHOLD not in values:
                raise ValueError("material-only study transfer requires an explicit scaling bound")
            normalized = tuple(value * baseline_modulus / modulus for value in current_curve)
            transfer_error = _refinement_error(
                (study[-1][1], normalized, normalized), values["absolute_floor"]
            )
            measured.append(
                MeasuredValue("material_only_relative_force_scaling_error", transfer_error, "1")
            )
            evidence["material_modulus_ratio"] = modulus / baseline_modulus
            passed = passed and transfer_error <= values[_MATERIAL_SCALING_THRESHOLD]
        status = AssessmentStatus.PASS if passed else AssessmentStatus.FAIL
        return CriterionAssessment(
            "mesh_dependence",
            "numeric",
            status,
            tuple(measured),
            "two successive registered global refinements evaluated over the declared force states",
        ), evidence
    except (StorageIntegrityError, ValueError, TypeError, KeyError, OverflowError) as error:
        return CriterionAssessment(
            criterion_id,
            "numeric",
            AssessmentStatus.UNVERIFIED,
            (),
            f"registered refinement study is unavailable: {error}",
        ), evidence
