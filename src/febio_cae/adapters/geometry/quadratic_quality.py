"""Sufficient whole-reference-element positivity certificate for quadratic Tet10.

Write J(x)=A(I+D(x)), where A is the positive corner affine Jacobian.
J and D are affine in reference coordinates. If max_vertex ||D||_inf < 1,
convexity bounds the norm everywhere on the reference tetrahedron. The path
I+tD is nonsingular for 0<=t<=1 (Neumann criterion), hence det J stays positive.
This is sufficient, not necessary: valid strongly curved elements can be refused.
The margin is numerical, not a physical surface tolerance or native qualification.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def require_positive_quadratic_mapping(points: Sequence[Sequence[float]]) -> None:
    if len(points) != 10:
        raise ValueError("quadratic Tet10 requires ten points")
    columns = [[points[i][j] - points[0][j] for j in range(3)] for i in (1, 2, 3)]
    a, b, c = columns

    def cross(u: list[float], v: list[float]) -> list[float]:
        return [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]]

    cofactors = [cross(b, c), cross(c, a), cross(a, b)]
    determinant = sum(a[i] * cofactors[0][i] for i in range(3))
    if not math.isfinite(determinant) or determinant <= 0:
        raise ValueError("quadratic mapping has degenerate or inverted corners")
    inverse = [[v / determinant for v in row] for row in cofactors]
    gradients = ((-1, -1, -1), (1, 0, 0), (0, 1, 0), (0, 0, 1))
    edges = ((0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3))
    deviations = [
        [points[4 + k][j] - (points[u][j] + points[v][j]) / 2 for j in range(3)]
        for k, (u, v) in enumerate(edges)
    ]
    for vertex in range(4):
        # Non-affine displacement is sum 4*L_u*L_v*edge_deviation.
        perturbation = [
            [
                sum(
                    4
                    * ((vertex == u) * gradients[v][j] + (vertex == v) * gradients[u][j])
                    * deviations[k][i]
                    for k, (u, v) in enumerate(edges)
                )
                for j in range(3)
            ]
            for i in range(3)
        ]
        relative = [
            [sum(inverse[i][k] * perturbation[k][j] for k in range(3)) for j in range(3)]
            for i in range(3)
        ]
        if any(not math.isfinite(v) for row in relative for v in row):
            raise ValueError("quadratic mapping quality arithmetic is non-finite")
        bound = max(sum(abs(v) for v in row) for row in relative)
        if not math.isfinite(bound) or bound >= 1 - 1e-10:
            raise ValueError("quadratic mapping positivity cannot be certified over the element")
