"""Deterministic Tet10 meshes for the supported rigid primitives."""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from dataclasses import dataclass

from febio_cae.adapters.geometry.backend import (
    BACKEND_TET10_ORDER_ID,
    BackendElement,
    BackendFace,
    BackendMesh,
    BackendMeshFace,
    BackendNode,
)
from febio_cae.domain import RigidPrimitive
from febio_cae.domain.artifacts import TET10_FACE_NODE_POSITIONS
from febio_cae.domain.canonical import canonical_bytes

type Coordinates = tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class GeneratedPrimitiveMesh:
    """Generated tool mesh plus its boundary inspection facts."""

    mesh: BackendMesh
    boundary_faces: tuple[BackendFace, ...]


def _add(left: Coordinates, right: Coordinates) -> Coordinates:
    return (left[0] + right[0], left[1] + right[1], left[2] + right[2])


def _scale(value: Coordinates, factor: float) -> Coordinates:
    return (value[0] * factor, value[1] * factor, value[2] * factor)


def _sub(left: Coordinates, right: Coordinates) -> Coordinates:
    return (left[0] - right[0], left[1] - right[1], left[2] - right[2])


def _cross(left: Coordinates, right: Coordinates) -> Coordinates:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _dot(left: Coordinates, right: Coordinates) -> float:
    return left[0] * right[0] + left[1] * right[1] + left[2] * right[2]


def _signed_volume(points: dict[int, Coordinates], tetrahedron: tuple[int, int, int, int]) -> float:
    first, second, third, fourth = (points[item] for item in tetrahedron)
    return _dot(_sub(second, first), _cross(_sub(third, first), _sub(fourth, first))) / 6.0


def _orient_tetrahedron(
    points: dict[int, Coordinates], tetrahedron: tuple[int, int, int, int]
) -> tuple[int, int, int, int]:
    volume = _signed_volume(points, tetrahedron)
    if volume == 0.0:
        raise ValueError("rigid primitive decomposition contains a degenerate tetrahedron")
    if volume < 0.0:
        return (tetrahedron[1], tetrahedron[0], tetrahedron[2], tetrahedron[3])
    return tetrahedron


def _box_decomposition(
    length: float, width: float, height: float
) -> tuple[dict[int, Coordinates], list[tuple[int, int, int, int]]]:
    half_length = length / 2.0
    half_width = width / 2.0
    half_height = height / 2.0
    points = {
        0: (-half_length, -half_width, -half_height),
        1: (half_length, -half_width, -half_height),
        2: (half_length, half_width, -half_height),
        3: (-half_length, half_width, -half_height),
        4: (-half_length, -half_width, half_height),
        5: (half_length, -half_width, half_height),
        6: (half_length, half_width, half_height),
        7: (-half_length, half_width, half_height),
    }
    # Five tetrahedra around the body diagonal 0--6 fill the rectangular box.
    tets = [
        (0, 1, 2, 6),
        (0, 2, 3, 6),
        (0, 3, 7, 6),
        (0, 7, 4, 6),
        (0, 4, 5, 6),
    ]
    return points, tets


def _cylinder_decomposition(
    radius: float, height: float, segments: int = 8
) -> tuple[dict[int, Coordinates], list[tuple[int, int, int, int]]]:
    points: dict[int, Coordinates] = {
        0: (0.0, 0.0, -height / 2.0),
        1: (0.0, 0.0, height / 2.0),
    }
    for index in range(segments):
        angle = 2.0 * math.pi * index / segments
        points[2 + index] = (radius * math.cos(angle), radius * math.sin(angle), -height / 2.0)
        points[2 + segments + index] = (
            radius * math.cos(angle),
            radius * math.sin(angle),
            height / 2.0,
        )

    tets: list[tuple[int, int, int, int]] = []
    for index in range(segments):
        next_index = (index + 1) % segments
        bottom = 2 + index
        bottom_next = 2 + next_index
        top = 2 + segments + index
        top_next = 2 + segments + next_index
        # Three tetrahedra exactly decompose each triangular-prism sector.
        tets.extend(
            (
                (0, bottom, bottom_next, 1),
                (bottom, top, bottom_next, 1),
                (bottom_next, top, top_next, 1),
            )
        )
    return points, tets


def _sphere_decomposition(
    radius: float,
) -> tuple[dict[int, Coordinates], list[tuple[int, int, int, int]]]:
    points = {
        0: (0.0, 0.0, 0.0),
        1: (radius, 0.0, 0.0),
        2: (-radius, 0.0, 0.0),
        3: (0.0, radius, 0.0),
        4: (0.0, -radius, 0.0),
        5: (0.0, 0.0, radius),
        6: (0.0, 0.0, -radius),
    }
    octahedron_faces = (
        (1, 3, 5),
        (1, 5, 4),
        (1, 4, 6),
        (1, 6, 3),
        (2, 5, 3),
        (2, 4, 5),
        (2, 6, 4),
        (2, 3, 6),
    )
    return points, [(0, first, second, third) for first, second, third in octahedron_faces]


def _primitive_decomposition(
    primitive: RigidPrimitive,
) -> tuple[dict[int, Coordinates], list[tuple[int, int, int, int]]]:
    dimensions = primitive.dimensions
    if primitive.kind == "sphere":
        return _sphere_decomposition(float(dimensions["radius"].to_si().value))
    if primitive.kind == "cylinder":
        return _cylinder_decomposition(
            float(dimensions["radius"].to_si().value),
            float(dimensions["height"].to_si().value),
        )
    if primitive.kind == "box":
        return _box_decomposition(
            float(dimensions["length"].to_si().value),
            float(dimensions["width"].to_si().value),
            float(dimensions["height"].to_si().value),
        )
    raise ValueError(f"unsupported rigid primitive kind: {primitive.kind!r}")


def _midpoint(first: Coordinates, second: Coordinates) -> Coordinates:
    return _scale(_add(first, second), 0.5)


def _sorted_triple(values: tuple[int, int, int]) -> tuple[int, int, int]:
    first, second, third = sorted(values)
    return first, second, third


def generate_primitive_mesh(
    primitive: RigidPrimitive,
    *,
    geometry_digest: str,
) -> GeneratedPrimitiveMesh:
    """Generate a deterministic, independent local Tet10 mesh for one primitive."""

    points, raw_tets = _primitive_decomposition(primitive)
    tets: list[tuple[int, int, int, int]] = [
        _orient_tetrahedron(points, (tet[0], tet[1], tet[2], tet[3])) for tet in raw_tets
    ]

    point_ids = sorted(points)
    point_id_to_node_id = {point_id: index + 1 for index, point_id in enumerate(point_ids)}
    coordinates: dict[int, Coordinates] = {
        node_id: points[point_id] for point_id, node_id in point_id_to_node_id.items()
    }
    edge_to_node_id: dict[tuple[int, int], int] = {}
    next_node_id = len(coordinates) + 1
    element_node_ids: dict[int, tuple[int, ...]] = {}
    for element_id, tetrahedron in enumerate(tets, start=1):
        corners: tuple[int, int, int, int] = (
            point_id_to_node_id[tetrahedron[0]],
            point_id_to_node_id[tetrahedron[1]],
            point_id_to_node_id[tetrahedron[2]],
            point_id_to_node_id[tetrahedron[3]],
        )
        edge_nodes: list[int] = []
        for first, second in (
            (corners[a], corners[b]) for a, b in ((0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3))
        ):
            edge = (min(first, second), max(first, second))
            if edge not in edge_to_node_id:
                edge_to_node_id[edge] = next_node_id
                coordinates[next_node_id] = _midpoint(coordinates[first], coordinates[second])
                next_node_id += 1
            edge_nodes.append(edge_to_node_id[edge])
        element_node_ids[element_id] = corners + tuple(edge_nodes)

    adjacency: dict[tuple[int, int, int], list[tuple[int, int]]] = defaultdict(list)
    face_geometry: dict[
        tuple[int, int, int], tuple[float, Coordinates, tuple[Coordinates, ...]]
    ] = {}
    for element_id, node_ids in element_node_ids.items():
        for local_face_id, positions in enumerate(TET10_FACE_NODE_POSITIONS):
            corner_node_ids = (
                node_ids[positions[0]],
                node_ids[positions[1]],
                node_ids[positions[2]],
            )
            key = _sorted_triple(corner_node_ids)
            adjacency[key].append((element_id, local_face_id))
            face_first, face_second, face_third = (
                coordinates[node_ids[positions[0]]],
                coordinates[node_ids[positions[1]]],
                coordinates[node_ids[positions[2]]],
            )
            area = 0.5 * math.sqrt(
                _dot(
                    _cross(_sub(face_second, face_first), _sub(face_third, face_first)),
                    _cross(_sub(face_second, face_first), _sub(face_third, face_first)),
                )
            )
            centroid = _scale(_add(_add(face_first, face_second), face_third), 1.0 / 3.0)
            face_geometry[key] = (area, centroid, (face_first, face_second, face_third))

    mesh_faces: list[BackendMeshFace] = []
    boundary_faces: list[BackendFace] = []
    for key in sorted(adjacency):
        neighbours = tuple(sorted(adjacency[key]))
        if len(neighbours) > 2:
            raise ValueError("rigid primitive decomposition created a non-manifold face")
        area, centroid, boundary_points = face_geometry[key]
        face_id = f"{primitive.body_id.value}:face:{'-'.join(str(item) for item in key)}"
        mesh_face = BackendMeshFace(
            face_id,
            tuple(item[0] for item in neighbours),
            tuple(item[1] for item in neighbours),
            area_si=area,
            centroid_si=centroid,
            boundary_points_si=boundary_points,
        )
        mesh_faces.append(mesh_face)
        if len(neighbours) == 1:
            boundary_faces.append(
                BackendFace(
                    face_id,
                    primitive.body_id.value,
                    primitive.local_frame,
                    area,
                    centroid,
                    boundary_points,
                )
            )

    recipe_digest = hashlib.sha256(
        canonical_bytes(
            {
                "primitive": primitive.to_dict(),
                "geometry_digest": geometry_digest,
                "ordering_id": BACKEND_TET10_ORDER_ID,
            }
        )
    ).hexdigest()
    mesh = BackendMesh(
        source_digest=recipe_digest,
        geometry_digest=geometry_digest,
        frame=primitive.local_frame,
        body_id=primitive.body_id.value,
        nodes=tuple(BackendNode(node_id, coordinates[node_id]) for node_id in sorted(coordinates)),
        elements=tuple(
            BackendElement(
                element_id,
                "tet10",
                _backend_node_order(element_node_ids[element_id]),
                primitive.body_id.value,
                BACKEND_TET10_ORDER_ID,
            )
            for element_id in sorted(element_node_ids)
        ),
        faces=tuple(mesh_faces),
        ordering_id=BACKEND_TET10_ORDER_ID,
    )
    return GeneratedPrimitiveMesh(mesh=mesh, boundary_faces=tuple(boundary_faces))


def _backend_node_order(node_ids: tuple[int, ...]) -> tuple[int, ...]:
    """Emit the backend order consumed by the explicit adapter permutation."""

    return (*node_ids[:8], node_ids[9], node_ids[8])


__all__ = ["GeneratedPrimitiveMesh", "generate_primitive_mesh"]
