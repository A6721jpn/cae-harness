"""Private trusted-composition criteria and bounded affine primitive generation.

No record lookup or qualification authority is established here. A registered
provider must be supplied by composition; test providers are only synthetic.
The algorithm represents inscribed radial polyhedra, not curved Tet10 faces.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from itertools import combinations

from febio_cae.domain import (
    MeshPolicy,
    NumericalProfileRef,
    PortError,
    PortErrorCategory,
    Quantity,
    RigidPrimitive,
)
from febio_cae.domain.units import Dimension

type Point = tuple[float, float, float]
type Tet = tuple[int, int, int, int]
ALGORITHM = "radial-polyhedron-affine-tet10-v1"


@dataclass(frozen=True, slots=True)
class ApproximationCriteria:
    profile: NumericalProfileRef
    max_boundary_deviation: Quantity
    supported_kinds: tuple[str, ...]
    algorithm_id: str
    evidence_scope: str
    max_elements: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.profile, NumericalProfileRef)
            or self.profile.purpose != "mesh_quality"
        ):
            raise ValueError("criteria require a mesh_quality profile")
        if (
            not isinstance(self.max_boundary_deviation, Quantity)
            or self.max_boundary_deviation.dimension != Dimension(length=1)
            or self.max_boundary_deviation.to_si().value <= 0
        ):
            raise ValueError("criteria require a positive finite length error limit")
        if (
            not isinstance(self.supported_kinds, tuple)
            or not self.supported_kinds
            or any(k not in {"sphere", "cylinder"} for k in self.supported_kinds)
            or len(set(self.supported_kinds)) != len(self.supported_kinds)
        ):
            raise ValueError("criteria require immutable supported curved kinds")
        for value in (self.algorithm_id, self.evidence_scope):
            if not isinstance(value, str) or not value or value != value.strip():
                raise ValueError("criteria algorithm and evidence scope must be explicit")
        if (
            isinstance(self.max_elements, bool)
            or not isinstance(self.max_elements, int)
            or self.max_elements <= 0
        ):
            raise ValueError("criteria require a positive element budget")

    def to_dict(self) -> dict[str, object]:
        return {
            "profile": self.profile.to_dict(),
            "max_boundary_deviation_si": self.max_boundary_deviation.to_si().value,
            "supported_kinds": list(self.supported_kinds),
            "algorithm_id": self.algorithm_id,
            "evidence_scope": self.evidence_scope,
            "max_elements": self.max_elements,
        }


type CriteriaProvider = Callable[[NumericalProfileRef], ApproximationCriteria | None]


def resolve_criteria(
    provider: CriteriaProvider | None, policy: MeshPolicy, kind: str
) -> ApproximationCriteria:
    if provider is None:
        raise PortError(
            PortErrorCategory.UNSUPPORTED_CAPABILITY,
            "curved primitive surface quality requires trusted approximation criteria",
        )
    try:
        criteria = provider(policy.quality_profile)
    except (LookupError, OSError, RuntimeError, TypeError, ValueError) as error:
        raise PortError(
            PortErrorCategory.INTEGRITY, f"approximation criteria unavailable: {error}"
        ) from error
    if not isinstance(criteria, ApproximationCriteria):
        raise PortError(
            PortErrorCategory.UNSUPPORTED_CAPABILITY, "approximation criteria unavailable"
        )
    if criteria.profile != policy.quality_profile:
        raise PortError(PortErrorCategory.INTEGRITY, "approximation criteria foreign or stale")
    if criteria.algorithm_id != ALGORITHM or kind not in criteria.supported_kinds:
        raise PortError(
            PortErrorCategory.UNSUPPORTED_CAPABILITY,
            "approximation criteria do not qualify this algorithm/kind",
        )
    return criteria


def check_deadline(deadline: float) -> None:
    if not math.isfinite(deadline) or time.monotonic() >= deadline:
        raise PortError(PortErrorCategory.QUALITY, "case elapsed budget exhausted during meshing")


def _element_limit(count: int, criteria: ApproximationCriteria) -> None:
    if count > criteria.max_elements:
        raise PortError(PortErrorCategory.QUALITY, "primitive element budget exhausted")


def _split_tets(
    points: dict[int, Point], tets: list[Tet], deadline: float
) -> tuple[dict[int, Point], list[Tet]]:
    points = dict(points)
    mids: dict[tuple[int, int], int] = {}
    result: list[Tet] = []
    for a, b, c, d in tets:
        check_deadline(deadline)
        edge_nodes = []
        for u, v in ((a, b), (a, c), (a, d), (b, c), (b, d), (c, d)):
            edge = (min(u, v), max(u, v))
            if edge not in mids:
                mids[edge] = len(points)
                points[mids[edge]] = tuple((points[u][j] + points[v][j]) / 2 for j in range(3))  # type: ignore[assignment]
            edge_nodes.append(mids[edge])
        ab, ac, ad, bc, bd, cd = edge_nodes
        # Four corner tetrahedra and a split central octahedron (ab--cd).
        result.extend(
            (
                (a, ab, ac, ad),
                (b, ab, bc, bd),
                (c, ac, bc, cd),
                (d, ad, bd, cd),
                (ab, ac, ad, cd),
                (ab, ad, bd, cd),
                (ab, bd, bc, cd),
                (ab, bc, ac, cd),
            )
        )
    return points, result


def _sphere(
    radius: float, level: int, deadline: float
) -> tuple[dict[int, Point], list[Tet], float]:
    from .primitives import _sphere_decomposition

    points, tets = _sphere_decomposition(radius)
    faces = [(t[1], t[2], t[3]) for t in tets]
    for _ in range(level):
        mids: dict[tuple[int, int], int] = {}
        refined: list[tuple[int, int, int]] = []
        for a, b, c in faces:
            check_deadline(deadline)
            ids = []
            for u, v in ((a, b), (b, c), (c, a)):
                edge = (min(u, v), max(u, v))
                if edge not in mids:
                    p = tuple((points[u][j] + points[v][j]) / 2 for j in range(3))
                    norm = math.sqrt(sum(x * x for x in p))
                    mids[edge] = len(points)
                    points[mids[edge]] = tuple(x * radius / norm for x in p)  # type: ignore[assignment]
                ids.append(mids[edge])
            ab, bc, ca = ids
            refined.extend(((a, ab, ca), (ab, b, bc), (ca, bc, c), (ab, bc, ca)))
        faces = refined
    # Every spherical direction intersects its radial triangle. Its radius is
    # >= distance to that triangle's plane and <= R. Radial correspondence in
    # both directions therefore bounds the entire surfaces, including contacts.
    distances = []
    for a, b, c in faces:
        check_deadline(deadline)
        p, q, s = points[a], points[b], points[c]
        edge_u = tuple(q[j] - p[j] for j in range(3))
        edge_v = tuple(s[j] - p[j] for j in range(3))
        n = (
            edge_u[1] * edge_v[2] - edge_u[2] * edge_v[1],
            edge_u[2] * edge_v[0] - edge_u[0] * edge_v[2],
            edge_u[0] * edge_v[1] - edge_u[1] * edge_v[0],
        )
        distances.append(abs(sum(n[j] * p[j] for j in range(3))) / math.sqrt(sum(x * x for x in n)))
    return points, [(0, a, b, c) for a, b, c in faces], radius - min(distances)


def controlled_decomposition(
    primitive: RigidPrimitive, policy: MeshPolicy, criteria: ApproximationCriteria, deadline: float
) -> tuple[dict[int, Point], list[Tet], dict[str, object]]:
    from .primitives import _cylinder_decomposition

    radius = float(primitive.dimensions["radius"].to_si().value)
    error_limit = float(criteria.max_boundary_deviation.to_si().value)
    size = float(policy.global_size.to_si().value)
    if (
        criteria.profile != policy.quality_profile
        or criteria.algorithm_id != ALGORITHM
        or primitive.kind not in criteria.supported_kinds
    ):
        raise PortError(PortErrorCategory.INTEGRITY, "approximation criteria binding mismatch")
    level = 0
    while True:
        check_deadline(deadline)
        _element_limit(8 * 4**level if primitive.kind == "sphere" else 24 * 2**level, criteria)
        if primitive.kind == "sphere":
            points, tets, deviation = _sphere(radius, level, deadline)
        else:
            segments = 8 * 2**level
            points, tets = _cylinder_decomposition(
                radius, float(primitive.dimensions["height"].to_si().value), segments, deadline
            )
            # Inscribed regular polygon: radial sagitta bounds sides and the
            # omitted annulus on both end caps in both surface directions.
            deviation = radius * (1 - math.cos(math.pi / segments))
        # Conservative floating arithmetic margin, not a physical tolerance.
        deviation = math.nextafter(deviation + 64 * math.ulp(radius), math.inf)
        if deviation <= error_limit:
            break
        if level >= policy.max_refinements:
            raise PortError(
                PortErrorCategory.QUALITY,
                "primitive refinement budget exhausted before boundary criterion",
            )
        level += 1
    refinements = level
    while True:
        check_deadline(deadline)
        maximum = max(
            math.dist(points[a], points[b]) for tet in tets for a, b in combinations(tet, 2)
        )
        if not math.isfinite(maximum) or not math.isfinite(deviation):
            raise PortError(PortErrorCategory.QUALITY, "non-finite primitive quality")
        if maximum <= size:
            break
        if refinements >= policy.max_refinements:
            raise PortError(
                PortErrorCategory.QUALITY,
                "primitive refinement budget exhausted before size criterion",
            )
        _element_limit(len(tets) * 8, criteria)
        points, tets = _split_tets(points, tets, deadline)
        refinements += 1
    return (
        points,
        tets,
        {
            "criteria": criteria.to_dict(),
            "deviation_upper_bound_si": deviation,
            "max_corner_edge_si": maximum,
            "refinements": refinements,
            "geometry_refinements": level,
            "element_count": len(tets),
        },
    )
