"""Public-adapter regressions for synthetic native curved primitives."""

from __future__ import annotations

import hashlib
import math
from dataclasses import replace
from typing import Any, cast

import pytest

from febio_cae.adapters.geometry import (
    BACKEND_TET10_ORDER_ID,
    BackendBody,
    BackendElement,
    BackendError,
    BackendErrorCategory,
    BackendFace,
    BackendInspection,
    BackendLocalRefinement,
    BackendMesh,
    BackendMeshFace,
    BackendNode,
    StepGeometryMeshAdapter,
)
from febio_cae.domain import (
    TET10_FACE_NODE_POSITIONS,
    CaseRevision,
    FaceId,
    FaceSetRule,
    FrameId,
    LocalRefinement,
    Point3,
    PortError,
    PortErrorCategory,
    ProperRotation,
    Quantity,
    RigidPrimitive,
    RigidTransform,
    SourceAssetContent,
    SourceLocalRefinementBall,
    Translation3,
)
from febio_cae.domain.canonical import canonical_bytes

from .conftest import (
    PART_BODY,
    PART_GEOMETRY_DIGEST,
    TOOL_BODY,
    TOOL_GEOMETRY_DIGEST,
    SyntheticBackend,
    SyntheticSourceResolver,
    _evidence,
)

NATIVE_ALGORITHM = "native-quadratic-primitive-bernstein-v1"
AFFINE_ALGORITHM = "radial-polyhedron-affine-tet10-v1"
WORLD = FrameId("World")
TOOL_LOCAL = FrameId("ToolLocal")
RADIUS = 0.002
EDGE_POSITIONS = ((0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3))
CAD_FACE_IDS = tuple(f"{TOOL_BODY.value}:cad-face-{index}" for index in range(8))


def _nonidentity_placement() -> RigidTransform:
    return RigidTransform(
        source_frame=TOOL_LOCAL,
        target_frame=WORLD,
        translation=Translation3(
            WORLD,
            Quantity(31, "mm"),
            Quantity(-47, "mm"),
            Quantity(83, "mm"),
        ),
        rotation=ProperRotation(
            (
                (1.0, 0.0, 0.0),
                (0.0, 0.0, -1.0),
                (0.0, 1.0, 0.0),
            )
        ),
    )


def _sphere_corners(radius: float) -> dict[int, tuple[float, float, float]]:
    return {
        0: (0.0, 0.0, 0.0),
        1: (radius, 0.0, 0.0),
        2: (-radius, 0.0, 0.0),
        3: (0.0, radius, 0.0),
        4: (0.0, -radius, 0.0),
        5: (0.0, 0.0, radius),
        6: (0.0, 0.0, -radius),
    }


def _oriented_sphere_tets() -> tuple[tuple[int, int, int, int], ...]:
    faces = (
        (1, 3, 5),
        (1, 5, 4),
        (1, 4, 6),
        (1, 6, 3),
        (2, 5, 3),
        (2, 4, 5),
        (2, 6, 4),
        (2, 3, 6),
    )
    points = _sphere_corners(RADIUS)
    oriented: list[tuple[int, int, int, int]] = []
    for first, second, third in faces:
        a, b, c = (points[index] for index in (first, second, third))
        cross = (
            b[1] * c[2] - b[2] * c[1],
            b[2] * c[0] - b[0] * c[2],
            b[0] * c[1] - b[1] * c[0],
        )
        volume = a[0] * cross[0] + a[1] * cross[1] + a[2] * cross[2]
        oriented.append((0, first, second, third) if volume > 0 else (0, first, third, second))
    return tuple(oriented)


def _recipe_digest(
    primitive: RigidPrimitive,
    geometry_digest: str,
    global_size_si: float | None,
    local_refinements: tuple[BackendLocalRefinement, ...] = (),
) -> str:
    payload: dict[str, object] = {
        "primitive": primitive.to_dict(),
        "geometry_digest": geometry_digest,
        "global_size_si": global_size_si,
        "local_refinements": [item.to_dict() for item in local_refinements],
    }
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def _synthetic_curved_mesh(
    primitive: RigidPrimitive,
    geometry_digest: str,
    global_size_si: float,
    local_refinements: tuple[BackendLocalRefinement, ...],
) -> BackendMesh:
    """Build one conformal eight-Tet sphere with curved boundary midsides."""

    points = _sphere_corners(RADIUS)
    tets = _oriented_sphere_tets()
    node_coordinates = {point_id + 1: point for point_id, point in points.items()}
    edge_node_ids: dict[tuple[int, int], int] = {}
    next_node_id = len(node_coordinates) + 1

    def edge_point(first: int, second: int) -> tuple[float, float, float]:
        left, right = points[first], points[second]
        midpoint = (
            (left[0] + right[0]) / 2.0,
            (left[1] + right[1]) / 2.0,
            (left[2] + right[2]) / 2.0,
        )
        if first == 0 or second == 0:
            return midpoint
        norm = math.sqrt(sum(value * value for value in midpoint))
        return midpoint[0] * RADIUS / norm, midpoint[1] * RADIUS / norm, midpoint[2] * RADIUS / norm

    element_node_ids: dict[int, tuple[int, ...]] = {}
    for element_id, tetrahedron in enumerate(tets, start=1):
        corners = tuple(point_id + 1 for point_id in tetrahedron)
        midsides: list[int] = []
        for first_position, second_position in EDGE_POSITIONS:
            first, second = tetrahedron[first_position], tetrahedron[second_position]
            edge = (min(first, second), max(first, second))
            if edge not in edge_node_ids:
                edge_node_ids[edge] = next_node_id
                node_coordinates[next_node_id] = edge_point(first, second)
                next_node_id += 1
            midsides.append(edge_node_ids[edge])
        canonical_ids = corners + tuple(midsides)
        element_node_ids[element_id] = canonical_ids

    adjacency: dict[tuple[int, int, int], list[tuple[int, int]]] = {}
    boundary_face_for_key: dict[tuple[int, int, int], str] = {}
    for element_id, tetrahedron in enumerate(tets, start=1):
        for local_face_id, positions in enumerate(TET10_FACE_NODE_POSITIONS):
            key_first, key_second, key_third = sorted(
                tetrahedron[position] for position in positions[:3]
            )
            corner_key = key_first, key_second, key_third
            adjacency.setdefault(corner_key, []).append((element_id, local_face_id))
            if 0 not in corner_key:
                boundary_face_for_key[corner_key] = CAD_FACE_IDS[element_id - 1]

    mesh_faces: list[BackendMeshFace] = []
    for index, corner_key in enumerate(sorted(adjacency)):
        neighbours = tuple(sorted(adjacency[corner_key]))
        cad_face_id = boundary_face_for_key.get(corner_key)
        face_id = (
            f"{cad_face_id}:facet-0"
            if cad_face_id is not None
            else f"{primitive.body_id.value}:mesh-face-{index}"
        )
        first_element_id, first_local_face_id = neighbours[0]
        first_element = element_node_ids[first_element_id]
        face_positions = TET10_FACE_NODE_POSITIONS[first_local_face_id]
        boundary_points = tuple(
            node_coordinates[first_element[position]] for position in face_positions[:3]
        )
        point_first, point_second, point_third = boundary_points
        u = tuple(point_second[axis] - point_first[axis] for axis in range(3))
        v = tuple(point_third[axis] - point_first[axis] for axis in range(3))
        cross = (
            u[1] * v[2] - u[2] * v[1],
            u[2] * v[0] - u[0] * v[2],
            u[0] * v[1] - u[1] * v[0],
        )
        area = 0.5 * math.sqrt(sum(value * value for value in cross))
        centroid = tuple(
            (point_first[axis] + point_second[axis] + point_third[axis]) / 3.0 for axis in range(3)
        )
        mesh_faces.append(
            BackendMeshFace(
                face_id=face_id,
                adjacent_element_ids=tuple(item[0] for item in neighbours),
                local_face_ids=tuple(item[1] for item in neighbours),
                area_si=area,
                centroid_si=centroid,
                boundary_points_si=boundary_points,
                source_face_id=cad_face_id,
            )
        )

    backend_elements = tuple(
        BackendElement(
            element_id=element_id,
            element_type="tet10",
            node_ids=(
                *canonical_ids[:8],
                canonical_ids[9],
                canonical_ids[8],
            ),
            body_id=primitive.body_id.value,
            ordering_id=BACKEND_TET10_ORDER_ID,
        )
        for element_id, canonical_ids in sorted(element_node_ids.items())
    )
    return BackendMesh(
        source_digest=_recipe_digest(primitive, geometry_digest, global_size_si, local_refinements),
        geometry_digest=geometry_digest,
        frame=primitive.local_frame,
        body_id=primitive.body_id.value,
        nodes=tuple(
            BackendNode(node_id, node_coordinates[node_id]) for node_id in sorted(node_coordinates)
        ),
        elements=backend_elements,
        faces=tuple(mesh_faces),
        ordering_id=BACKEND_TET10_ORDER_ID,
    )


def _synthetic_curved_inspection(
    primitive: RigidPrimitive, geometry_digest: str
) -> BackendInspection:
    points = _sphere_corners(RADIUS)
    tets = _oriented_sphere_tets()
    faces: list[BackendFace] = []
    for index, tetrahedron in enumerate(tets):
        corners = tuple(points[point_id] for point_id in tetrahedron[1:])
        first, second, third = corners
        u = tuple(second[axis] - first[axis] for axis in range(3))
        v = tuple(third[axis] - first[axis] for axis in range(3))
        cross = (
            u[1] * v[2] - u[2] * v[1],
            u[2] * v[0] - u[0] * v[2],
            u[0] * v[1] - u[1] * v[0],
        )
        area = 0.5 * math.sqrt(sum(value * value for value in cross))
        centroid = tuple((first[axis] + second[axis] + third[axis]) / 3.0 for axis in range(3))
        faces.append(
            BackendFace(
                face_id=CAD_FACE_IDS[index],
                body_id=primitive.body_id.value,
                frame=primitive.local_frame,
                area_si=area,
                centroid_si=centroid,
                boundary_points_si=corners,
            )
        )
    body = BackendBody(
        body_id=primitive.body_id.value,
        closed_solid=True,
        volume_si=4.0 * math.pi * RADIUS**3 / 3.0,
        faces=tuple(faces),
    )
    return BackendInspection(
        source_digest=_recipe_digest(primitive, geometry_digest, None),
        geometry_digest=geometry_digest,
        declared_units=("m",),
        frame=primitive.local_frame,
        bodies=(body,),
    )


class SyntheticCurvedBackend(SyntheticBackend):
    """Test-only backend exposing the concrete native-shaped methods."""

    backend_id = "synthetic-curved-fixture"
    backend_version = "test-fixture-1"

    def __init__(self) -> None:
        super().__init__()
        self.curved_inspection_calls = 0
        self.curved_mesh_calls = 0

    def inspect_rigid_primitive(
        self, primitive: RigidPrimitive, *, geometry_digest: str
    ) -> BackendInspection:
        self.curved_inspection_calls += 1
        if primitive.kind != "sphere":
            raise BackendError(
                BackendErrorCategory.UNSUPPORTED_CAPABILITY,
                "synthetic curved fixture supports only a sphere",
            )
        return _synthetic_curved_inspection(primitive, geometry_digest)

    def mesh_rigid_primitive(
        self,
        primitive: RigidPrimitive,
        *,
        geometry_digest: str,
        global_size_si: float,
        local_refinements: tuple[BackendLocalRefinement, ...] = (),
    ) -> BackendMesh:
        self.curved_mesh_calls += 1
        if primitive.kind != "sphere":
            raise BackendError(
                BackendErrorCategory.UNSUPPORTED_CAPABILITY,
                "synthetic curved fixture supports only a sphere",
            )
        if any(
            item.body_id != primitive.body_id.value or item.frame != primitive.local_frame
            for item in local_refinements
        ):
            raise BackendError(
                BackendErrorCategory.INVALID_INPUT,
                "synthetic curved fixture requires source-local tool refinement context",
            )
        return _synthetic_curved_mesh(primitive, geometry_digest, global_size_si, local_refinements)


def _curved_revision(
    revision: CaseRevision,
    *,
    local_refinement: LocalRefinement | None = None,
) -> CaseRevision:
    primitive = replace(
        revision.spec.rigid_tool.primitive,
        kind="sphere",
        placement=_nonidentity_placement(),
        dimensions={"radius": Quantity(RADIUS, "m")},
        dimension_evidence={"radius": _evidence("rigid_tool.radius", "curved-radius")},
    )
    local_refinements = () if local_refinement is None else (local_refinement,)
    policy = replace(
        revision.spec.mesh_policy,
        global_size=Quantity(0.004, "m"),
        local_refinements=local_refinements,
    )
    return replace(
        revision,
        spec=replace(
            revision.spec,
            rigid_tool=replace(revision.spec.rigid_tool, primitive=primitive),
            mesh_policy=policy,
        ),
    )


def _criteria(
    revision: CaseRevision,
    *,
    limit: float,
    algorithm_id: str = NATIVE_ALGORITHM,
    supported_kinds: tuple[str, ...] = ("sphere", "cylinder"),
) -> Any:
    # Keep the feature import at execution time so this test file collects while
    # the native criteria leaf is still being implemented.
    from febio_cae.adapters.meshing.approximation import ApproximationCriteria

    return ApproximationCriteria(
        profile=revision.spec.mesh_policy.quality_profile,
        max_boundary_deviation=Quantity(limit, "m"),
        supported_kinds=supported_kinds,
        algorithm_id=algorithm_id,
        evidence_scope="synthetic-component-only",
        max_elements=1000,
    )


def _adapter(
    backend: SyntheticCurvedBackend,
    source: SourceAssetContent,
    criteria: Any,
) -> StepGeometryMeshAdapter:
    return StepGeometryMeshAdapter(
        backend,
        source_resolver=SyntheticSourceResolver(source),
        source_asset=source.source_asset,
        resolve_mesh_quality=lambda _profile: criteria,
    )


def _inverse_pose(point: tuple[float, float, float]) -> tuple[float, float, float]:
    placement = _nonidentity_placement()
    translation = tuple(
        float(getattr(placement.translation, axis).to_si().value) for axis in ("x", "y", "z")
    )
    shifted = tuple(point[index] - translation[index] for index in range(3))
    assert isinstance(placement.rotation, ProperRotation)
    matrix = cast(tuple[tuple[float, ...], ...], placement.rotation.matrix)
    result = tuple(
        sum(matrix[row][column] * shifted[row] for row in range(3)) for column in range(3)
    )
    return result[0], result[1], result[2]


def _quadratic_face_residual() -> float:
    corners = ((RADIUS, 0.0, 0.0), (0.0, RADIUS, 0.0), (0.0, 0.0, RADIUS))
    midsides = []
    for first, second in ((0, 1), (1, 2), (2, 0)):
        midpoint = tuple((corners[first][axis] + corners[second][axis]) / 2.0 for axis in range(3))
        norm = math.sqrt(sum(value * value for value in midpoint))
        midsides.append(tuple(value * RADIUS / norm for value in midpoint))
    face_center = tuple(
        -sum(point[axis] for point in corners) / 9.0
        + 4.0 * sum(point[axis] for point in midsides) / 9.0
        for axis in range(3)
    )
    return RADIUS - math.sqrt(sum(value * value for value in face_center))


def _tool_faces_and_nodes(
    artifact: Any,
) -> tuple[dict[int, tuple[float, float, float]], list[Any]]:
    elements = [element for element in artifact.elements if element.body_id == TOOL_BODY.value]
    node_ids = {node_id for element in elements for node_id in element.node_ids}
    nodes = {
        node.node_id: _inverse_pose(tuple(node.coordinates_si))
        for node in artifact.nodes
        if node.node_id in node_ids
    }
    faces = [
        face
        for face in artifact.faces
        if face.body_id == TOOL_BODY.value and len(face.adjacent_element_ids) == 1
    ]
    return nodes, faces


def test_native_curved_mesh_preserves_curvature_pose_and_cad_selection(
    source_content: SourceAssetContent,
    synthetic_case_revision: CaseRevision,
) -> None:
    revision = _curved_revision(synthetic_case_revision)
    backend = SyntheticCurvedBackend()
    adapter = _adapter(backend, source_content, _criteria(revision, limit=RADIUS))
    inspection = adapter.inspect_rigid_tool(
        revision.spec.rigid_tool.primitive,
        TOOL_GEOMETRY_DIGEST,
    )
    assert inspection.frame == TOOL_LOCAL
    assert len(inspection.bodies) == 1
    assert inspection.bodies[0].volume_si == pytest.approx(4.0 * math.pi * RADIUS**3 / 3.0)
    assert {face.face_id for face in inspection.bodies[0].faces} == set(CAD_FACE_IDS)

    artifact = adapter.mesh(revision)

    assert artifact.frame == WORLD
    assert artifact.provenance.source_geometry_digest == PART_GEOMETRY_DIGEST
    assert artifact.provenance.source_body_ids == (PART_BODY.value, TOOL_BODY.value)
    tool_elements = [element for element in artifact.elements if element.body_id == TOOL_BODY.value]
    assert len(tool_elements) == 8
    nodes, boundary_faces = _tool_faces_and_nodes(artifact)
    assert len(boundary_faces) == 8

    translation = (0.031, -0.047, 0.083)
    center_ids = [
        node_id for node_id, point in nodes.items() if math.dist(point, (0.0, 0.0, 0.0)) < 1.0e-15
    ]
    assert len(center_ids) == 1
    center_node = next(node for node in artifact.nodes if node.node_id == center_ids[0])
    assert center_node.coordinates_si == pytest.approx(translation)

    curved_midsides = 0
    radial_midsides = 0
    for element in tool_elements:
        corners = [nodes[node_id] for node_id in element.node_ids[:4]]
        for mid_id, (first, second) in zip(element.node_ids[4:], EDGE_POSITIONS, strict=True):
            midpoint = tuple(
                (corners[first][axis] + corners[second][axis]) / 2.0 for axis in range(3)
            )
            midside = nodes[mid_id]
            if (
                math.dist(corners[first], (0.0, 0.0, 0.0)) > RADIUS * 0.9
                and math.dist(corners[second], (0.0, 0.0, 0.0)) > RADIUS * 0.9
            ):
                assert math.dist(midside, (0.0, 0.0, 0.0)) == pytest.approx(RADIUS)
                assert math.dist(midside, midpoint) > 1.0e-6
                curved_midsides += 1
            else:
                assert midside == pytest.approx(midpoint)
                radial_midsides += 1
    assert curved_midsides >= 3
    assert radial_midsides >= 3

    for face in boundary_faces:
        points = [nodes[node_id] for node_id in face.node_ids]
        assert all(math.dist(point, (0.0, 0.0, 0.0)) == pytest.approx(RADIUS) for point in points)
        assert any(
            math.dist(
                points[index],
                tuple((points[first][axis] + points[second][axis]) / 2.0 for axis in range(3)),
            )
            > 1.0e-6
            for index, (first, second) in enumerate(((0, 1), (1, 2), (2, 0)), start=3)
        )

    selection_digest = hashlib.sha256(
        revision.spec.rigid_tool.contact_surface.to_bytes()
    ).hexdigest()
    tool_set = next(
        item
        for item in artifact.sets
        if item.body_id == TOOL_BODY.value
        and item.kind == "face"
        and item.source_selection_digest == selection_digest
    )
    selected_faces = set(tool_set.member_ids)
    assert selected_faces == {face.face_id for face in boundary_faces}


def test_native_boundary_limit_below_whole_face_deviation_is_quality_failure(
    source_content: SourceAssetContent,
    synthetic_case_revision: CaseRevision,
) -> None:
    revision = _curved_revision(synthetic_case_revision)
    backend = SyntheticCurvedBackend()
    limit = _quadratic_face_residual() / 2.0
    assert limit > 0.0

    with pytest.raises(PortError) as error:
        _adapter(backend, source_content, _criteria(revision, limit=limit)).mesh(revision)

    assert error.value.category == PortErrorCategory.QUALITY


@pytest.mark.parametrize(
    ("algorithm_id", "supported_kinds"),
    (
        (AFFINE_ALGORITHM, ("sphere",)),
        (NATIVE_ALGORITHM, ("cylinder",)),
    ),
    ids=("affine-criteria", "unsupported-kind"),
)
def test_native_preflight_rejects_unsupported_criteria_before_curved_effects(
    source_content: SourceAssetContent,
    synthetic_case_revision: CaseRevision,
    algorithm_id: str,
    supported_kinds: tuple[str, ...],
) -> None:
    revision = _curved_revision(synthetic_case_revision)
    backend = SyntheticCurvedBackend()

    with pytest.raises(PortError) as error:
        _adapter(
            backend,
            source_content,
            _criteria(
                revision,
                limit=RADIUS,
                algorithm_id=algorithm_id,
                supported_kinds=supported_kinds,
            ),
        ).mesh(revision)

    assert error.value.category == PortErrorCategory.UNSUPPORTED_CAPABILITY
    assert backend.curved_inspection_calls == 0
    assert backend.curved_mesh_calls == 0
    assert backend.mesh_requests == []


def test_native_legacy_regionless_refinement_fails_closed_before_curved_effects(
    source_content: SourceAssetContent,
    synthetic_case_revision: CaseRevision,
) -> None:
    base = _curved_revision(synthetic_case_revision)
    selection = replace(
        base.spec.rigid_tool.contact_surface,
        name="legacy-tool-refinement",
        role="mesh_refinement",
        role_evidence=_evidence("selection.role", "legacy-tool-refinement"),
    )
    refinement = LocalRefinement(
        refinement_id="legacy-regionless",
        selection=selection,
        size=Quantity(0.001, "m"),
    )
    revision = _curved_revision(synthetic_case_revision, local_refinement=refinement)
    backend = SyntheticCurvedBackend()

    with pytest.raises(PortError) as error:
        _adapter(backend, source_content, _criteria(base, limit=RADIUS)).mesh(revision)

    assert error.value.category == PortErrorCategory.UNSUPPORTED_CAPABILITY
    assert backend.curved_inspection_calls == 0
    assert backend.curved_mesh_calls == 0
    assert backend.mesh_requests == []


def test_unknown_native_contact_face_is_rejected_before_any_mesh_generation(
    source_content: SourceAssetContent,
    synthetic_case_revision: CaseRevision,
) -> None:
    revision = _curved_revision(synthetic_case_revision)
    tool = revision.spec.rigid_tool
    selection = replace(
        tool.contact_surface,
        rule=FaceSetRule(
            tool.contact_surface.geometry_digest,
            tool.contact_surface.body_id,
            tool.contact_surface.frame,
            (FaceId("unknown-native-face"),),
            _evidence("selection.faces", "unknown-native-face"),
        ),
    )
    revision = replace(
        revision,
        spec=replace(
            revision.spec,
            rigid_tool=replace(tool, contact_surface=selection),
            contact=replace(revision.spec.contact, tool_surface=selection),
        ),
    )
    backend = SyntheticCurvedBackend()
    with pytest.raises(PortError) as error:
        _adapter(backend, source_content, _criteria(revision, limit=RADIUS)).mesh(revision)
    assert error.value.category == PortErrorCategory.INVALID_INPUT
    assert backend.curved_mesh_calls == 0
    assert backend.mesh_requests == []


def test_native_source_local_ball_passes_and_changes_mesh_identity(
    source_content: SourceAssetContent,
    synthetic_case_revision: CaseRevision,
) -> None:
    base_revision = _curved_revision(synthetic_case_revision)
    selection = replace(
        base_revision.spec.rigid_tool.contact_surface,
        name="bounded-tool-refinement",
        role="mesh_refinement",
        role_evidence=_evidence("selection.role", "bounded-tool-refinement"),
    )
    refinement = LocalRefinement(
        refinement_id="bounded-tool-ball",
        selection=selection,
        size=Quantity(0.001, "m"),
        region=SourceLocalRefinementBall(
            center=Point3(
                TOOL_LOCAL,
                Quantity(0, "m"),
                Quantity(0, "m"),
                Quantity(0, "m"),
            ),
            radius=Quantity(0.0005, "m"),
        ),
    )
    refined_revision = _curved_revision(synthetic_case_revision, local_refinement=refinement)

    baseline = _adapter(
        SyntheticCurvedBackend(), source_content, _criteria(base_revision, limit=RADIUS)
    ).mesh(base_revision)
    refined = _adapter(
        SyntheticCurvedBackend(),
        source_content,
        _criteria(refined_revision, limit=RADIUS),
    ).mesh(refined_revision)

    assert refined.nodes and refined.elements and refined.sets
    assert refined.provenance.source_geometry_digest == baseline.provenance.source_geometry_digest
    assert refined.provenance.source_body_ids == baseline.provenance.source_body_ids
    assert refined.provenance.mesh_recipe_digest != baseline.provenance.mesh_recipe_digest
    assert refined.artifact_id != baseline.artifact_id
    assert set(baseline.provenance.source_selection_digests) < set(
        refined.provenance.source_selection_digests
    )
