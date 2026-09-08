"""Directed placement for explicitly represented closed triangular boundaries.

The planar-triangle-v1 attribute is a backend assertion that boundary_points_si
are the complete affine triangle, not samples of a curved CAD face. Every face
must assert that representation. Unsupported native reports fail closed.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from typing import NoReturn

from febio_cae.domain import PortError, PortErrorCategory, UnitDirection

from .backend import BackendBody, BackendFace, BackendInspection

type Point = tuple[float, float, float]


def _fail(reason: str) -> NoReturn:
    raise PortError(PortErrorCategory.UNSUPPORTED_CAPABILITY, f"unsupported directed gap: {reason}")


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    value = sum(x * y for x, y in zip(a, b, strict=True))
    if not math.isfinite(value):
        _fail("non-finite geometric arithmetic")
    return value


def _sub(a: Sequence[float], b: Sequence[float]) -> Point:
    result = a[0] - b[0], a[1] - b[1], a[2] - b[2]
    if not all(math.isfinite(v) for v in result):
        _fail("non-finite geometric arithmetic")
    return result


def _cross(a: Sequence[float], b: Sequence[float]) -> Point:
    return a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]


def _triangles(body: BackendBody) -> list[Point]:
    edges: Counter[tuple[Point, Point]] = Counter()
    points: list[Point] = []
    for face in body.faces:
        if "planar-triangle-v1" not in face.attributes or len(face.boundary_points_si) != 3:
            _fail("complete planar triangular body boundaries are required")
        a, b, c = ((p[0], p[1], p[2]) for p in face.boundary_points_si)
        if math.sqrt(_dot(_cross(_sub(b, a), _sub(c, a)), _cross(_sub(b, a), _sub(c, a)))) == 0:
            _fail("degenerate triangular boundary")
        for first, second in ((a, b), (b, c), (c, a)):
            edges[first, second] += 1
        points.extend((a, b, c))
    if not edges or any(
        count != 1 or edges[second, first] != 1 for (first, second), count in edges.items()
    ):
        _fail("body boundary is not a consistently oriented closed triangulation")
    return points


def _positive_overlap(part: BackendFace, tool: BackendFace, direction: Point, tol: float) -> None:
    # Separating-axis theorem on the two convex projected triangles. Axes are
    # perpendicular to each edge in the common plane; no raster or point sample.
    origin = part.boundary_points_si[0]
    part_points = [_sub(p, origin) for p in part.boundary_points_si]
    tool_points = [_sub(p, origin) for p in tool.boundary_points_si]
    for triangle in (part_points, tool_points):
        for i in range(3):
            axis = _cross(direction, _sub(triangle[(i + 1) % 3], triangle[i]))
            norm = math.sqrt(_dot(axis, axis))
            if norm == 0:
                _fail("degenerate projected edge")
            axis = (axis[0] / norm, axis[1] / norm, axis[2] / norm)
            a = [_dot(p, axis) for p in part_points]
            b = [_dot(p, axis) for p in tool_points]
            if min(max(a), max(b)) - max(min(a), min(b)) <= tol:
                _fail("selected triangles lack positive-area projected overlap")


def directed_planar_gap(
    part_report: BackendInspection,
    tool_report: BackendInspection,
    part_body_id: str,
    tool_body_id: str,
    part_face_id: str,
    tool_face_id: str,
    direction: UnitDirection,
    requested_gap: float,
) -> float:
    if part_report.frame != direction.frame or tool_report.frame != direction.frame:
        _fail("direction and geometry frames disagree")
    if requested_gap < 0:
        _fail("negative overlap requests are not qualified")
    part_body = next(b for b in part_report.bodies if b.body_id == part_body_id)
    tool_body = next(b for b in tool_report.bodies if b.body_id == tool_body_id)
    part_face = next(f for f in part_body.faces if f.face_id == part_face_id)
    tool_face = next(f for f in tool_body.faces if f.face_id == tool_face_id)
    part_points, tool_points = _triangles(part_body), _triangles(tool_body)
    d = (direction.x, direction.y, direction.z)
    origin = part_face.boundary_points_si[0]
    # Geometric comparison tolerance only: does not authorize a surface error.
    scale = max(math.dist(p, origin) for p in part_points + tool_points)
    if not math.isfinite(scale):
        _fail("non-finite geometric extent")
    tol = max(1e-12, scale * 1e-12)
    heights: list[float] = []
    for face, sense in ((part_face, 1), (tool_face, -1)):
        a, b, c = face.boundary_points_si
        normal = _cross(_sub(b, a), _sub(c, a))
        norm = math.sqrt(_dot(normal, normal))
        if (
            _dot(normal, d) * sense <= 0
            or math.sqrt(_dot(_cross(normal, d), _cross(normal, d))) > norm * 1e-10
        ):
            _fail("selected planes must face each other perpendicular to direction")
        values = [_dot(_sub(p, origin), d) for p in face.boundary_points_si]
        if max(values) - min(values) > tol:
            _fail("selected face is not perpendicular to direction")
        heights.append(sum(values) / 3)
    part_height, tool_height = heights
    if any(_dot(_sub(p, origin), d) > part_height + tol for p in part_points):
        _fail("part protrudes beyond the selected support plane")
    if any(_dot(_sub(p, origin), d) < tool_height - tol for p in tool_points):
        _fail("tool protrudes beyond the selected support plane")
    _positive_overlap(part_face, tool_face, d, tol)
    # Translation only along d preserves projected overlap. The two enclosing
    # half-spaces prove no final body interference for any nonnegative gap.
    return tool_height - part_height
