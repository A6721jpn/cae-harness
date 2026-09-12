"""Mandatory data-driven numerical checks for the explicit planar scope.

This module deliberately has no native-runtime authority.  It consumes only the
registered revision, mesh, compatibility mappings, manifest, and decoded result
port.  Native qualification/admission is an application concern.
"""

from __future__ import annotations

import math
import struct
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import chain, pairwise
from typing import NoReturn

from febio_cae.domain import (
    AsPlaced,
    AssessmentStatus,
    CaseRevision,
    CompatibilityProfile,
    CriterionAssessment,
    Frictionless,
    IsotropicLinearElastic,
    MeshArtifact,
    MeshFace,
    NumericResultData,
    OutputMapping,
    OutputObservation,
    OutputRequest,
    ProperRotation,
    QualityCriterion,
    Quantity,
    ResultDataPort,
    ResultManifest,
    SpecifiedGap,
)
from febio_cae.domain.artifacts import TET10_NODE_ORDER_ID
from febio_cae.domain.results import numeric_state_indices
from febio_cae.domain.units import Dimension

from .quality import QualityAdapter

_REQUIRED_IDS = (
    "contact_quality",
    "motion_support_contact_fidelity",
    "quasistatic_equilibrium",
)
_CONTACT = "planar_contact"
_FIDELITY = "motion_support_contact_fidelity"
_EQUILIBRIUM = "quasistatic_equilibrium"
_PARAM_DIMENSIONS: dict[str, Dimension] = {
    "initial_interference_max": Dimension(length=1),
    "contact_gap_max": Dimension(length=1),
    "penetration_max": Dimension(length=1),
    "force_absolute_floor": Dimension(length=1, mass=1, time=-2),
    "contact_start": Dimension(time=1),
    "contact_end": Dimension(time=1),
    "motion_error_max": Dimension(length=1),
    "support_displacement_max": Dimension(length=1),
    "relative_max": Dimension(),
    "absolute_floor": Dimension(length=1, mass=1, time=-2),
}


class _Unavailable(ValueError):
    """Internal fail-closed signal for one quality metric."""


def _fail(message: str) -> NoReturn:
    raise _Unavailable(message)


def _finite(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(f"{field} is not finite numeric data")
    result = float(value)
    if not math.isfinite(result):
        _fail(f"{field} is not finite numeric data")
    return result


def _vector(value: Sequence[object], field: str) -> tuple[float, float, float]:
    if len(value) != 3:
        _fail(f"{field} is not a VEC3F")
    return (_finite(value[0], field), _finite(value[1], field), _finite(value[2], field))


def _sub(a: Sequence[float], b: Sequence[float]) -> tuple[float, float, float]:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a: Sequence[float], b: Sequence[float]) -> tuple[float, float, float]:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(a: Sequence[float], factor: float) -> tuple[float, float, float]:
    return (a[0] * factor, a[1] * factor, a[2] * factor)


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    result = math.fsum(a[index] * b[index] for index in range(3))
    return _finite(result, "vector dot product")


def _cross(a: Sequence[float], b: Sequence[float]) -> tuple[float, float, float]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _norm(a: Sequence[float]) -> float:
    return _finite(math.sqrt(math.fsum(item * item for item in a)), "vector norm")


def _float32(value: float) -> float:
    try:
        return struct.unpack("<f", struct.pack("<f", value))[0]
    except (OverflowError, struct.error) as error:
        _fail(f"value cannot be represented by native float32: {error}")


def _quantity(value: Quantity, field: str, dimension: Dimension) -> float:
    if not isinstance(value, Quantity) or value.dimension != dimension:
        _fail(f"{field} has an invalid dimension")
    try:
        result = float(value.to_si().value)
    except (TypeError, ValueError, OverflowError) as error:
        _fail(f"{field} is not SI-representable: {error}")
    return _finite(result, field)


def _mapping_name(mapping: OutputMapping) -> str:
    return mapping.native_name.strip().casefold()


def _expected_thresholds(criterion: QualityCriterion, expected: set[str]) -> dict[str, float]:
    if not isinstance(criterion, QualityCriterion):
        _fail("quality criterion is invalid")
    result: dict[str, float] = {}
    for item in criterion.thresholds:
        parameter = item.parameter_id
        if not isinstance(parameter, str) or parameter in result:
            _fail("quality criterion has missing or duplicate threshold parameters")
        dimension = _PARAM_DIMENSIONS.get(parameter)
        if dimension is None or parameter not in expected:
            _fail(f"quality criterion has an unsupported threshold parameter: {parameter!r}")
        value = item.value
        result[parameter] = _quantity(value, f"threshold {parameter}", dimension)
    if set(result) != expected:
        _fail("quality criterion threshold set is incomplete or unexpected")
    for parameter, threshold_value in result.items():
        if not math.isfinite(threshold_value) or threshold_value < 0.0:
            _fail(f"threshold {parameter} must be finite and nonnegative")
    if "contact_end" in result and result["contact_end"] <= result["contact_start"]:
        _fail("contact interval must have a positive duration")
    return result


def _axis(direction: Sequence[float]) -> int:
    nonzero = [index for index, value in enumerate(direction) if value != 0.0]
    if len(nonzero) != 1 or abs(direction[nonzero[0]]) != 1.0:
        _fail("planar scope requires an axis-aligned unit motion direction")
    return nonzero[0]


def _axis_aligned_rotation(matrix: Sequence[Sequence[object]]) -> bool:
    if len(matrix) != 3 or any(len(row) != 3 for row in matrix):
        return False
    rows = [_vector(row, "rigid placement rotation") for row in matrix]
    for row in rows:
        if sum(abs(value) > 1.0e-12 for value in row) != 1:
            return False
        if not any(math.isclose(abs(value), 1.0, rel_tol=0.0, abs_tol=1.0e-12) for value in row):
            return False
    return all(
        sum(abs(rows[row][column]) > 1.0e-12 for row in range(3)) == 1 for column in range(3)
    )


@dataclass(frozen=True, slots=True)
class _Resolved:
    numeric: NumericResultData
    entity_ids: frozenset[str]
    state_indices: tuple[int, ...]
    entity_offsets: dict[str, int]
    scale_to_si: float

    def row_vector(self, state_index: int, entity_id: str) -> tuple[float, float, float]:
        try:
            start = self.entity_offsets[entity_id]
        except KeyError:
            _fail(f"result entity is absent: {entity_id}")
        row = self.numeric.values[state_index]
        name = self.numeric.mapping.native_name
        return (
            _finite(row[start] * self.scale_to_si, name),
            _finite(row[start + 1] * self.scale_to_si, name),
            _finite(row[start + 2] * self.scale_to_si, name),
        )


@dataclass(frozen=True, slots=True)
class _Context:
    revision: CaseRevision
    mesh: MeshArtifact
    profile: CompatibilityProfile
    manifest: ResultManifest
    saved_times: tuple[float, ...]
    direction: tuple[float, float, float]
    direction_axis: int
    part_body: str
    tool_body: str
    part_nodes: frozenset[str]
    tool_nodes: frozenset[str]
    evaluations: dict[tuple[str, str], _Resolved]


def _observation(
    manifest: ResultManifest, mapping: OutputMapping, request: OutputRequest
) -> OutputObservation:
    matches = [
        item for item in manifest.read_result.observations if item.output_id == request.request_id
    ]
    if len(matches) != 1:
        _fail(f"validated manifest has missing or duplicate output {request.request_id}")
    observation = matches[0]
    if (
        observation.location != request.location
        or observation.value_type != mapping.value_type
        or observation.unit != mapping.unit
        or observation.frame != request.frame
        or observation.measure_id != request.measure_id
        or observation.data_ref is None
    ):
        _fail(f"manifest output does not match request semantics: {request.request_id}")
    return observation


def _resolve_numeric(
    manifest: ResultManifest,
    request: OutputRequest,
    mapping: OutputMapping,
    observation: OutputObservation,
    data: ResultDataPort,
) -> NumericResultData:
    try:
        numeric = data.resolve_manifest_output(manifest.manifest_id, request.request_id)
    except (ValueError, TypeError, KeyError, OverflowError) as error:
        _fail(f"result output cannot be resolved: {error}")
    if not isinstance(numeric, NumericResultData):
        _fail("result data resolver returned an invalid record")
    if (
        numeric.reference.codec_id != "numeric-result-v1"
        or numeric.reference.bundle_digest != manifest.bundle_digest
        or numeric.reference.attempt_id != manifest.attempt_id
        or numeric.reference != observation.data_ref
        or numeric.mapping != mapping
        or numeric.mapping.location != request.location
        or numeric.mapping.measure_id != request.measure_id
        or numeric.mapping.frame != request.frame
        or numeric.mapping.value_type != "VEC3F"
        or tuple(numeric.component_ids) != ("x", "y", "z")
        or numeric.axis_id != "state_time"
        or numeric.axis_unit not in {"s", "ms"}
    ):
        _fail("numeric result is bound to another output or unsupported field")
    try:
        numeric.verify_content_digest()
        if data.resolve(numeric.reference) != numeric:
            _fail("numeric observation/reference disagrees")
    except (ValueError, TypeError, KeyError, OverflowError) as error:
        _fail(f"numeric result integrity cannot be verified: {error}")
    manifest_paths = {entry.logical_path for entry in manifest.files if entry.role == "result"}
    if numeric.reference.logical_path not in manifest_paths:
        _fail("numeric result path is not in the manifest")
    if len(numeric.component_ids) != 3 or tuple(numeric.component_ids) != ("x", "y", "z"):
        _fail("required numeric output is not a complete VEC3F")
    return numeric


def _resolve_context(
    manifest: ResultManifest,
    revision: CaseRevision,
    mesh: MeshArtifact,
    profile: CompatibilityProfile,
    data: ResultDataPort,
    criteria: Sequence[QualityCriterion],
    evaluation_cache: dict[str, tuple[tuple[str, str], _Resolved]],
) -> _Context:
    if not isinstance(manifest, ResultManifest) or not isinstance(revision, CaseRevision):
        _fail("manifest or revision is invalid")
    if not isinstance(mesh, MeshArtifact) or not isinstance(profile, CompatibilityProfile):
        _fail("mesh or compatibility profile is invalid")
    if manifest.read_result.status.value != "VALIDATED":
        _fail(f"result read status is {manifest.read_result.status.value}")
    if manifest.read_result.reader != profile.reader:
        _fail("manifest reader differs from compatibility profile")

    spec = revision.spec
    frame = spec.geometry.placement.target_frame
    if (
        mesh.frame != frame
        or mesh.provenance.source_geometry_digest != spec.geometry.geometry_digest
        or set(mesh.provenance.source_body_ids)
        != {spec.geometry.body_id.value, spec.rigid_tool.primitive.body_id.value}
        or mesh.provenance.node_ordering_id != TET10_NODE_ORDER_ID
    ):
        _fail("mesh geometry/body/frame provenance does not match the revision")
    if not isinstance(spec.material, IsotropicLinearElastic):
        _fail("planar quality scope requires explicit isotropic linear-elastic material")
    if not isinstance(spec.contact.friction, Frictionless):
        _fail("planar quality scope requires explicit frictionless contact")
    if spec.rigid_tool.primitive.kind != "box":
        _fail("planar quality scope requires an explicit rigid box")
    rotation = spec.rigid_tool.primitive.placement.rotation
    if not isinstance(rotation, ProperRotation) or not _axis_aligned_rotation(rotation.matrix):
        _fail("rigid box placement is not axis-aligned")
    direction = _vector(
        (spec.motion.direction.x, spec.motion.direction.y, spec.motion.direction.z),
        "motion direction",
    )
    direction_axis = _axis(direction)
    if spec.motion.direction.frame != frame or spec.rigid_tool.dofs.frame != frame:
        _fail("motion/tool DOF frame does not match the mesh frame")
    for axis_name in ("x", "y", "z"):
        state = getattr(spec.rigid_tool.dofs, axis_name).state
        expected = "prescribed" if axis_name == ("x", "y", "z")[direction_axis] else "fixed"
        if state != expected:
            _fail(f"rigid-tool {axis_name} DOF is not the declared planar state")
    for axis_name in ("rx", "ry", "rz"):
        if getattr(spec.rigid_tool.dofs, axis_name).state != "fixed":
            _fail(f"rigid-tool {axis_name} rotation is not explicitly fixed")
    if not isinstance(spec.contact.arrangement, (AsPlaced, SpecifiedGap)):
        _fail("contact arrangement is unsupported")
    if (
        isinstance(spec.contact.arrangement, SpecifiedGap)
        and spec.contact.arrangement.gap.to_si().value < 0
    ):
        _fail("negative specified overlap is outside the planar scope")

    saved_times = tuple(
        _quantity(item, "saved state time", Dimension(time=1)) for item in spec.outputs.saved_times
    )
    if not saved_times or any(current <= previous for previous, current in pairwise(saved_times)):
        _fail("saved state times are not strictly increasing")
    motion_times = tuple(
        _quantity(item.time, "motion sample time", Dimension(time=1))
        for item in spec.motion.samples
    )
    if saved_times[0] < motion_times[0] or saved_times[-1] > motion_times[-1]:
        _fail("saved states fall outside the declared motion history")
    if saved_times[-1] != motion_times[-1]:
        _fail("numerical evaluation policy does not cover the declared motion endpoint")

    part_body = spec.geometry.body_id.value
    tool_body = spec.rigid_tool.primitive.body_id.value
    part_nodes = frozenset(
        str(node)
        for element in mesh.elements
        if element.body_id == part_body
        for node in element.node_ids
    )
    tool_nodes = frozenset(
        str(node)
        for element in mesh.elements
        if element.body_id == tool_body
        for node in element.node_ids
    )
    if not part_nodes or not tool_nodes or set(part_nodes) & set(tool_nodes):
        _fail("mesh node/body membership is incomplete or overlapping")

    evaluations_by_id = {item.evaluation_id: item for item in spec.outputs.evaluations}
    requests_by_id = {item.request_id: item for item in spec.outputs.requests}
    evaluation_ids = {
        evaluation_id
        for criterion in criteria
        if criterion.metric_id in {_CONTACT, _FIDELITY, _EQUILIBRIUM}
        for evaluation_id in criterion.evaluation_ids
    }
    resolved: dict[tuple[str, str], _Resolved] = {}
    for evaluation_id in sorted(evaluation_ids):
        cached = evaluation_cache.get(evaluation_id)
        if cached is not None:
            key, value = cached
            if key in resolved:
                _fail(f"duplicate {key[0]} evaluation scope for body {key[1]}")
            resolved[key] = value
            continue
        evaluation = evaluations_by_id.get(evaluation_id)
        if evaluation is None:
            _fail(f"declared evaluation is missing: {evaluation_id}")
        request = requests_by_id.get(evaluation.output_request_id)
        if request is None:
            _fail(f"declared output request is missing: {evaluation.output_request_id}")
        if request.frame != frame or evaluation.selection.frame != frame:
            _fail(f"evaluation frame differs from the case frame: {evaluation_id}")
        if (
            evaluation.selection.geometry_digest != request.selection.geometry_digest
            or evaluation.selection.body_id != request.selection.body_id
        ):
            _fail(f"evaluation selection differs from its output request: {evaluation_id}")
        mapping = profile.mapping_for(request.quantity_id)
        observation = _observation(manifest, mapping, request)
        numeric = _resolve_numeric(manifest, request, mapping, observation, data)
        state_indices = numeric_state_indices(numeric, saved_times)
        requested_times = tuple(
            _quantity(item, f"evaluation {evaluation_id} state time", Dimension(time=1))
            for item in evaluation.state_times
        )
        if requested_times != saved_times:
            _fail(f"evaluation {evaluation_id} does not request all saved states")
        entities = frozenset(
            QualityAdapter._entity_ids(evaluation.selection, request.location, mesh)
        )
        native_name = _mapping_name(mapping)
        if native_name == "displacement":
            kind = "displacement"
            allowed = part_nodes | tool_nodes
            if set(numeric.entity_ids) != allowed:
                _fail("displacement result does not cover every mesh node")
            if request.location != "node":
                _fail("displacement result must be nodal")
        elif native_name == "reaction forces":
            kind = "reaction"
            if not part_nodes <= set(numeric.entity_ids) <= part_nodes | tool_nodes:
                _fail("nodal reaction result is missing part nodes or contains foreign nodes")
            if request.location != "node" or mapping.raw_sign != -1 or mapping.canonical_sign != 1:
                _fail("nodal reaction mapping sign/location is unexpected")
        elif native_name == "rigid force":
            kind = "force"
            if set(numeric.entity_ids) != {tool_body} or request.location != "rigid_body":
                _fail("rigid applied force scope is incomplete")
            if mapping.raw_sign not in {-1, 1} or mapping.canonical_sign != 1:
                _fail("rigid applied force mapping sign is unexpected")
        elif native_name == "rigid position":
            kind = "position"
            if set(numeric.entity_ids) != {tool_body} or request.location != "rigid_body":
                _fail("rigid position scope is incomplete")
            if mapping.raw_sign != 1 or mapping.canonical_sign != 1:
                _fail("rigid position mapping sign is unexpected")
        else:
            _fail(
                f"required planar evaluation uses unsupported native field {mapping.native_name!r}"
            )
        expected_dimension = (
            Dimension(length=1)
            if kind in {"displacement", "position"}
            else Dimension(length=1, mass=1, time=-2)
        )
        try:
            mapping_dimension = Quantity(0.0, mapping.unit).dimension
        except (TypeError, ValueError, OverflowError) as error:
            _fail(f"required mapping unit is invalid: {error}")
        if mapping_dimension != expected_dimension:
            _fail(f"{mapping.native_name} has an unexpected physical unit")
        key = (kind, evaluation.selection.body_id.value)
        if key in resolved:
            _fail(f"duplicate {kind} evaluation scope for body {key[1]}")
        if kind == "displacement" and entities not in {part_nodes, tool_nodes}:
            _fail("displacement evaluation does not identify one complete body")
        scale = float(Quantity(1, mapping.unit).to_si().value)
        if kind == "force":
            # Numeric data is canonical; equilibrium requires FEBio's applied force.
            scale *= mapping.raw_sign * mapping.canonical_sign
        resolved[key] = _Resolved(
            numeric,
            entities,
            state_indices,
            {entity: index * 3 for index, entity in enumerate(numeric.entity_ids)},
            scale,
        )
        evaluation_cache[evaluation_id] = (key, resolved[key])

    return _Context(
        revision,
        mesh,
        profile,
        manifest,
        saved_times,
        direction,
        direction_axis,
        part_body,
        tool_body,
        part_nodes,
        tool_nodes,
        resolved,
    )


def _required(context: _Context, kind: str, body: str) -> _Resolved:
    result = context.evaluations.get((kind, body))
    if result is None:
        _fail(f"required {kind} evaluation is missing for body {body}")
    return result


def _motion_travel(context: _Context, time: float) -> float:
    samples = context.revision.spec.motion.samples
    times = tuple(_quantity(item.time, "motion sample time", Dimension(time=1)) for item in samples)
    values = tuple(
        _quantity(item.displacement, "motion sample displacement", Dimension(length=1))
        for item in samples
    )
    if time < times[0] or time > times[-1]:
        _fail("saved state is outside the supplied motion history")
    index = bisect_right(times, time)
    if index == 0:
        return values[0]
    if index == len(times):
        return values[-1]
    if times[index - 1] == time:
        return values[index - 1]
    span = times[index] - times[index - 1]
    fraction = (time - times[index - 1]) / span
    return _finite(
        values[index - 1] + fraction * (values[index] - values[index - 1]), "interpolated travel"
    )


def _support_nodes(context: _Context) -> dict[str, frozenset[str]]:
    fixed_nodes: dict[str, set[str]] = {"x": set(), "y": set(), "z": set()}
    for support in context.revision.spec.support.supports:
        if (
            support.selection.body_id.value != context.part_body
            or support.frame != context.mesh.frame
        ):
            _fail("support selection/frame/body differs from the part")
        nodes = QualityAdapter._entity_ids(support.selection, "node", context.mesh)
        if not nodes <= context.part_nodes:
            _fail("support selection contains foreign nodes")
        for axis in ("x", "y", "z"):
            if getattr(support, axis).state == "fixed":
                if not nodes.isdisjoint(fixed_nodes[axis]):
                    _fail("fixed support DOF is declared more than once")
                fixed_nodes[axis].update(nodes)
    if not any(fixed_nodes.values()):
        _fail("no explicit fixed support DOF is available")
    return {axis: frozenset(nodes) for axis, nodes in fixed_nodes.items()}


@dataclass(frozen=True, slots=True)
class _FaceGeometry:
    face: MeshFace
    points: tuple[tuple[float, float, float], ...]
    scalar: float


def _face_geometry(
    node_by_id: dict[int, tuple[float, float, float]],
    face: MeshFace,
    direction: tuple[float, float, float],
) -> _FaceGeometry:
    if len(face.adjacent_element_ids) != 1:
        _fail(f"selected face {face.face_id} is not a boundary face")
    try:
        points = tuple(node_by_id[node] for node in face.node_ids)
    except KeyError as error:
        _fail(f"selected face {face.face_id} references an unknown node: {error}")
    if len(points) != 6:
        _fail(f"selected face {face.face_id} is not a quadratic face")
    normal = _cross(_sub(points[1], points[0]), _sub(points[2], points[0]))
    normal_norm = _norm(normal)
    if normal_norm == 0.0:
        _fail(f"selected face {face.face_id} is degenerate")
    scale = max(1.0, max(_norm(point) for point in points))
    tolerance = scale * 1.0e-10
    if (
        abs(_dot(normal, direction)) <= normal_norm * 1.0e-12
        or _norm(_cross(normal, direction)) > normal_norm * 1.0e-10
    ):
        _fail(f"selected face {face.face_id} is not an axis-aligned motion plane")
    for index, point in enumerate(points):
        if abs(_dot(_sub(point, points[0]), normal)) > normal_norm * tolerance:
            _fail(f"selected face {face.face_id} is not planar")
        if not all(math.isfinite(value) for value in point):
            _fail(f"selected face {face.face_id} has nonfinite coordinates")
    expected_midpoints = (
        _scale(_add(points[0], points[1]), 0.5),
        _scale(_add(points[1], points[2]), 0.5),
        _scale(_add(points[2], points[0]), 0.5),
    )
    for index, expected in enumerate(expected_midpoints, start=3):
        if _norm(_sub(points[index], expected)) > tolerance:
            _fail(f"selected face {face.face_id} has unsupported quadratic geometry")
    return _FaceGeometry(face, points, _dot(points[0], direction))


def _projected_triangles_overlap(
    first: Sequence[Sequence[float]],
    second: Sequence[Sequence[float]],
    dropped_axis: int,
) -> bool:
    axes = tuple(index for index in range(3) if index != dropped_axis)
    first_2d = [(point[axes[0]], point[axes[1]]) for point in first]
    second_2d = [(point[axes[0]], point[axes[1]]) for point in second]
    for triangle in (first_2d, second_2d):
        for index in range(3):
            start = triangle[index]
            end = triangle[(index + 1) % 3]
            edge = (end[0] - start[0], end[1] - start[1])
            normal = (-edge[1], edge[0])
            normal_norm = math.hypot(normal[0], normal[1])
            if normal_norm == 0.0:
                return False
            first_projection = [point[0] * normal[0] + point[1] * normal[1] for point in first_2d]
            second_projection = [point[0] * normal[0] + point[1] * normal[1] for point in second_2d]
            if (
                min(max(first_projection), max(second_projection))
                - max(min(first_projection), min(second_projection))
                <= normal_norm * 1.0e-12
            ):
                return False
    return True


def _contact_faces(
    context: _Context,
) -> tuple[tuple[_FaceGeometry, ...], tuple[_FaceGeometry, ...], float]:
    part_selection = context.revision.spec.contact.part_surface
    tool_selection = context.revision.spec.contact.tool_surface
    part_ids = QualityAdapter._entity_ids(part_selection, "face", context.mesh)
    tool_ids = QualityAdapter._entity_ids(tool_selection, "face", context.mesh)
    part_mesh_faces = [item for item in context.mesh.faces if item.face_id in part_ids]
    tool_mesh_faces = [item for item in context.mesh.faces if item.face_id in tool_ids]
    if len(part_mesh_faces) != len(part_ids) or len(tool_mesh_faces) != len(tool_ids):
        _fail("contact face selection membership is incomplete")
    if any(item.body_id != context.part_body for item in part_mesh_faces) or any(
        item.body_id != context.tool_body for item in tool_mesh_faces
    ):
        _fail("contact face selection body ownership is unexpected")
    selected_nodes = {
        node for face in chain(part_mesh_faces, tool_mesh_faces) for node in face.node_ids
    }
    node_by_id = {
        node.node_id: _vector(node.coordinates_si, "selected contact coordinates")
        for node in context.mesh.nodes
        if node.node_id in selected_nodes
    }
    part = tuple(_face_geometry(node_by_id, item, context.direction) for item in part_mesh_faces)
    tool = tuple(_face_geometry(node_by_id, item, context.direction) for item in tool_mesh_faces)
    part_scalars = [item.scalar for item in part]
    tool_scalars = [item.scalar for item in tool]
    initial_gap = min(part_scalars) - max(tool_scalars)
    if not math.isfinite(initial_gap):
        _fail("initial contact gap is nonfinite")

    if not any(
        _projected_triangles_overlap(first.points[:3], second.points[:3], context.direction_axis)
        for first in part
        for second in tool
    ):
        _fail("selected contact faces do not have positive projected overlap")
    return part, tool, initial_gap


def _quadratic_coefficients(values: Sequence[float]) -> tuple[float, ...]:
    origin = values[0]
    first, second = values[1] - origin, values[2] - origin
    a = 2.0 * (first - 2.0 * (values[3] - origin))
    c = 2.0 * (second - 2.0 * (values[5] - origin))
    d, e = first - a, second - c
    b = 4.0 * (values[4] - origin) - a - c - 2.0 * (d + e)
    return a, b, c, d, e, origin


def _quadratic_value(coefficients: Sequence[float], u: float, v: float) -> float:
    a, b, c, d, e, origin = coefficients
    return _finite(
        math.fsum((a * u * u, b * u * v, c * v * v, d * u, e * v, origin)), "quadratic value"
    )


def _quadratic_range(values: Sequence[float]) -> tuple[float, float]:
    """Extrema of a six-node scalar field on the closed reference triangle."""
    coefficients = _quadratic_coefficients(values)
    candidates = list(values[:3])
    for first, second, middle in ((0, 1, 3), (1, 2, 4), (2, 0, 5)):
        a = 2.0 * (values[first] + values[second] - 2.0 * values[middle])
        b = values[second] - values[first] - a
        if a != 0.0:
            position = -b / (2.0 * a)
            if 0.0 < position < 1.0:
                candidates.append((a * position + b) * position + values[first])
    a, b, c, d, e, _ = coefficients
    determinant = 4.0 * a * c - b * b
    if determinant != 0.0:
        u = (b * e - 2.0 * c * d) / determinant
        v = (b * d - 2.0 * a * e) / determinant
        if u > 0.0 and v > 0.0 and u + v < 1.0:
            candidates.append(_quadratic_value(coefficients, u, v))
    return min(candidates), max(candidates)


def _deformed_projected_overlap(
    part: Sequence[Sequence[float]],
    tools: Sequence[Sequence[Sequence[float]]],
    dropped_axis: int,
) -> bool:
    """Prove positive overlap by a regular curved-face point inside an affine tool face."""
    axes = tuple(axis for axis in range(3) if axis != dropped_axis)
    x = _quadratic_coefficients(tuple(point[axes[0]] for point in part))
    y = _quadratic_coefficients(tuple(point[axes[1]] for point in part))
    for u, v in ((1.0 / 3, 1.0 / 3), (0.25, 0.25), (0.5, 0.25), (0.25, 0.5)):
        xu, xv = 2 * x[0] * u + x[1] * v + x[3], x[1] * u + 2 * x[2] * v + x[4]
        yu, yv = 2 * y[0] * u + y[1] * v + y[3], y[1] * u + 2 * y[2] * v + y[4]
        if xu * yv - xv * yu == 0.0:
            continue
        point = (_quadratic_value(x, u, v), _quadratic_value(y, u, v))
        for tool in tools:
            signs = tuple(
                (tool[(index + 1) % 3][axes[0]] - tool[index][axes[0]])
                * (point[1] - tool[index][axes[1]])
                - (tool[(index + 1) % 3][axes[1]] - tool[index][axes[1]])
                * (point[0] - tool[index][axes[0]])
                for index in range(3)
            )
            if all(value > 0.0 for value in signs) or all(value < 0.0 for value in signs):
                return True
    return False


def _deformed_gap(
    context: _Context,
    part_faces: Sequence[_FaceGeometry],
    tool_faces: Sequence[_FaceGeometry],
    part_displacement: _Resolved,
    tool_displacement: _Resolved,
    saved_index: int,
) -> tuple[float, float]:
    def deformed(
        faces: Sequence[_FaceGeometry], displacement: _Resolved
    ) -> tuple[tuple[tuple[float, float, float], ...], ...]:
        state_index = displacement.state_indices[saved_index]
        return tuple(
            tuple(
                _add(point, displacement.row_vector(state_index, str(node_id)))
                for node_id, point in zip(face.face.node_ids, face.points, strict=True)
            )
            for face in faces
        )

    part = deformed(part_faces, part_displacement)
    tool = deformed(tool_faces, tool_displacement)
    if not part or not tool:
        _fail("contact face geometry is empty")
    axes = tuple(axis for axis in range(3) if axis != context.direction_axis)
    for face in tool:
        for first, second, middle in ((0, 1, 3), (1, 2, 4), (2, 0, 5)):
            for axis in axes:
                expected = 0.5 * (face[first][axis] + face[second][axis])
                tolerance = 32 * max(
                    math.ulp(face[index][axis]) for index in (first, second, middle)
                )
                if abs(face[middle][axis] - expected) > tolerance:
                    _fail("deformed tool contact projection is not affine")
    if not all(_deformed_projected_overlap(face, tool, context.direction_axis) for face in part):
        _fail("positive projected contact overlap is not established for every deformed part face")
    part_ranges = tuple(
        _quadratic_range(tuple(_dot(point, context.direction) for point in face)) for face in part
    )
    tool_ranges = tuple(
        _quadratic_range(tuple(_dot(point, context.direction) for point in face)) for face in tool
    )
    return (
        _finite(
            min(pair[0] for pair in part_ranges) - max(pair[1] for pair in tool_ranges),
            "minimum deformed contact gap",
        ),
        _finite(
            max(pair[1] for pair in part_ranges) - min(pair[0] for pair in tool_ranges),
            "maximum deformed contact gap",
        ),
    )


def _criterion(
    criterion_id: str,
    status: AssessmentStatus,
    measured: Sequence[tuple[str, float, str]],
    reason: str,
) -> CriterionAssessment:
    from febio_cae.domain import MeasuredValue

    return CriterionAssessment(
        criterion_id,
        "numeric",
        status,
        tuple(MeasuredValue(metric, value, unit) for metric, value, unit in measured),
        reason,
    )


def _unverified(criterion_id: str, reason: str) -> CriterionAssessment:
    return _criterion(criterion_id, AssessmentStatus.UNVERIFIED, (), reason)


def _criterion_for_metric(criteria: Sequence[QualityCriterion], metric_id: str) -> QualityCriterion:
    matches = [item for item in criteria if item.metric_id == metric_id]
    if len(matches) != 1:
        _fail(f"exactly one QualityCriterion is required for metric {metric_id}")
    return matches[0]


def _contact_assessment(context: _Context, criterion: QualityCriterion) -> CriterionAssessment:
    thresholds = _expected_thresholds(
        criterion,
        {
            "initial_interference_max",
            "contact_gap_max",
            "penetration_max",
            "force_absolute_floor",
            "contact_start",
            "contact_end",
        },
    )
    tool_disp = _required(context, "displacement", context.tool_body)
    part_disp = _required(context, "displacement", context.part_body)
    force = _required(context, "force", context.tool_body)
    _required(context, "position", context.tool_body)
    part_faces, tool_faces, initial_gap = _contact_faces(context)
    initial_interference = max(0.0, -initial_gap)
    if initial_interference > thresholds["initial_interference_max"]:
        return _criterion(
            "contact_quality",
            AssessmentStatus.FAIL,
            (("planar_contact.initial_interference", initial_interference, "m"),),
            "initial selected-face interference exceeds the declared limit",
        )
    start = thresholds["contact_start"]
    end = thresholds["contact_end"]
    try:
        start_index = context.saved_times.index(start)
        end_index = context.saved_times.index(end)
    except ValueError:
        _fail("contact interval endpoints must be observed saved states")
    if start_index >= end_index:
        _fail("contact interval endpoints are not ordered saved states")
    gaps: list[float] = []
    penetrations: list[float] = []
    compressive_forces: list[float] = []
    for saved_index in range(start_index, end_index + 1):
        minimum_gap, maximum_gap = _deformed_gap(
            context, part_faces, tool_faces, part_disp, tool_disp, saved_index
        )
        force_vector = force.row_vector(force.state_indices[saved_index], context.tool_body)
        compressive = _dot(force_vector, context.direction)
        if compressive <= thresholds["force_absolute_floor"]:
            _fail("declared contact interval has no nonzero compressive applied tool force")
        gaps.append(max(0.0, maximum_gap))
        penetrations.append(max(0.0, -minimum_gap))
        compressive_forces.append(compressive)
    measured_gap = max(gaps)
    measured_penetration = max(penetrations)
    measured_force = min(compressive_forces)
    status = (
        AssessmentStatus.PASS
        if measured_gap <= thresholds["contact_gap_max"]
        and measured_penetration <= thresholds["penetration_max"]
        else AssessmentStatus.FAIL
    )
    return _criterion(
        "contact_quality",
        status,
        (
            ("planar_contact.initial_interference", initial_interference, "m"),
            ("planar_contact.contact_gap", measured_gap, "m"),
            ("planar_contact.penetration", measured_penetration, "m"),
            ("planar_contact.compressive_force_min", measured_force, "N"),
        ),
        f"selected quadratic-face gap/penetration over saved interval [{start:.9g}, {end:.9g}] s",
    )


def _fidelity_assessment(context: _Context, criterion: QualityCriterion) -> CriterionAssessment:
    thresholds = _expected_thresholds(criterion, {"motion_error_max", "support_displacement_max"})
    part_disp = _required(context, "displacement", context.part_body)
    tool_disp = _required(context, "displacement", context.tool_body)
    position = _required(context, "position", context.tool_body)
    fixed_nodes = _support_nodes(context)
    max_motion_error = 0.0
    for saved_index, time in enumerate(context.saved_times):
        travel = _motion_travel(context, time)
        expected = _scale(context.direction, travel)
        state_index = tool_disp.state_indices[saved_index]
        for node in sorted(context.tool_nodes, key=int):
            actual = tool_disp.row_vector(state_index, node)
            max_motion_error = max(
                max_motion_error,
                max(abs(actual[index] - _float32(expected[index])) for index in range(3)),
            )
        position_actual = position.row_vector(
            position.state_indices[saved_index], context.tool_body
        )
        translation = context.revision.spec.rigid_tool.primitive.placement.translation
        base = (
            _quantity(translation.x, "rigid-tool placement x", Dimension(length=1)),
            _quantity(translation.y, "rigid-tool placement y", Dimension(length=1)),
            _quantity(translation.z, "rigid-tool placement z", Dimension(length=1)),
        )
        expected_position = tuple(_float32(base[index] + expected[index]) for index in range(3))
        max_motion_error = max(
            max_motion_error,
            max(abs(position_actual[index] - expected_position[index]) for index in range(3)),
        )
    max_support_displacement = 0.0
    for axis_name, nodes in fixed_nodes.items():
        axis = ("x", "y", "z").index(axis_name)
        for saved_index in range(len(context.saved_times)):
            state_index = part_disp.state_indices[saved_index]
            for node in nodes:
                value = abs(part_disp.row_vector(state_index, node)[axis])
                max_support_displacement = max(max_support_displacement, value)
    status = (
        AssessmentStatus.PASS
        if max_motion_error <= thresholds["motion_error_max"]
        and max_support_displacement <= thresholds["support_displacement_max"]
        else AssessmentStatus.FAIL
    )
    return _criterion(
        "motion_support_contact_fidelity",
        status,
        (
            ("motion_support_contact_fidelity.motion_error", max_motion_error, "m"),
            (
                "motion_support_contact_fidelity.support_displacement",
                max_support_displacement,
                "m",
            ),
        ),
        "full tool-node XYZ translation and absolute rigid-center motion were compared",
    )


def _equilibrium_assessment(context: _Context, criterion: QualityCriterion) -> CriterionAssessment:
    thresholds = _expected_thresholds(criterion, {"relative_max", "absolute_floor"})
    reaction = _required(context, "reaction", context.part_body)
    force = _required(context, "force", context.tool_body)
    fixed_nodes = _support_nodes(context)
    if any(not nodes <= reaction.entity_ids for nodes in fixed_nodes.values()):
        _fail("reaction evaluation omits explicitly fixed support nodes")
    max_residual = 0.0
    max_excess = 0.0
    for saved_index in range(len(context.saved_times)):
        support_sum = [
            math.fsum(
                reaction.row_vector(reaction.state_indices[saved_index], node)[axis]
                for node in fixed_nodes[axis_name]
            )
            for axis, axis_name in enumerate(("x", "y", "z"))
        ]
        applied = force.row_vector(force.state_indices[saved_index], context.tool_body)
        residual = _add(support_sum, applied)
        residual_norm = _norm(residual)
        applied_norm = _norm(applied)
        support_norm = _norm(support_sum)
        limit = _finite(
            max(
                thresholds["absolute_floor"],
                thresholds["relative_max"] * max(applied_norm, support_norm),
            ),
            "per-state equilibrium limit",
        )
        max_residual = max(max_residual, residual_norm)
        max_excess = max(max_excess, residual_norm - limit)
    status = AssessmentStatus.PASS if max_excess == 0 else AssessmentStatus.FAIL
    return _criterion(
        "quasistatic_equilibrium",
        status,
        (
            ("quasistatic_equilibrium.residual", max_residual, "N"),
            ("quasistatic_equilibrium.limit_excess", max_excess, "N"),
        ),
        "canonical reactions on explicitly fixed support DOFs were summed with raw rigid applied force",
    )


def assess_planar_requirements(
    manifest: ResultManifest,
    revision: CaseRevision,
    mesh: MeshArtifact,
    profile: CompatibilityProfile,
    data: ResultDataPort,
) -> tuple[CriterionAssessment, ...]:
    """Assess the three mandatory numerical obligations for the explicit planar scope.

    The return IDs are fixed by this API.  Thresholds and result scopes are read
    from exactly one criterion for each metric; criterion IDs are not interpreted.
    Any unsupported, missing, mismatched, or incomplete evidence is UNVERIFIED.
    """

    criteria = tuple(revision.spec.quality_policy.criteria)

    evaluation_cache: dict[str, tuple[tuple[str, str], _Resolved]] = {}

    assessments: list[CriterionAssessment] = []
    for output_id, metric_id, evaluator in (
        ("contact_quality", _CONTACT, _contact_assessment),
        ("motion_support_contact_fidelity", _FIDELITY, _fidelity_assessment),
        ("quasistatic_equilibrium", _EQUILIBRIUM, _equilibrium_assessment),
    ):
        try:
            criterion = _criterion_for_metric(criteria, metric_id)
            context = _resolve_context(
                manifest, revision, mesh, profile, data, (criterion,), evaluation_cache
            )
            assessments.append(evaluator(context, criterion))
        except (ValueError, KeyError, OverflowError) as error:
            assessments.append(
                _unverified(
                    output_id, f"required planar numerical evidence is unavailable: {error}"
                )
            )
    return tuple(assessments)


__all__ = ["assess_planar_requirements"]
