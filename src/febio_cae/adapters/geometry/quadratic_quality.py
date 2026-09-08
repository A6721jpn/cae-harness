"""Sufficient whole-reference-element positivity certificate for quadratic Tet10.

Write J(x)=A(I+D(x)), where A is the positive corner affine Jacobian.
J and D are affine in reference coordinates. If max_vertex ||D||_inf < 1,
convexity bounds the norm everywhere on the reference tetrahedron. The path
I+tD is nonsingular for 0<=t<=1 (Neumann criterion), hence det J stays positive.
All orientation and relative-norm operations use exact integers representing the
supplied binary coordinates. No rounded determinant, inverse or norm establishes
the hypotheses. The conservative norm threshold is exactly 1 - 1/10**10.
This is sufficient, not necessary: valid strongly curved elements can be refused.
It certifies the represented element, not intended geometry or native qualification.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from fractions import Fraction


def require_positive_quadratic_mapping(points: Sequence[Sequence[float]]) -> float:
    """Certify the whole element and return its representable positive corner volume.

    Binary floats are exact dyadic rationals. Expressing all coordinates over
    their largest denominator yields integer coordinates Q = scale * points.
    With A the integer corner matrix, C its adjugate and d = det(A),
    A^-1 E = C E / d. Compare the absolute row sums of C E directly with d;
    neither a floating inverse nor a conditioning estimate is needed. Twice
    each midpoint displacement is integer, so E is also integer at each vertex.
    """
    if len(points) != 10 or any(len(point) != 3 for point in points):
        raise ValueError("quadratic Tet10 requires ten three-component points")
    if any(isinstance(v, bool) or not isinstance(v, (float, int)) for p in points for v in p):
        raise ValueError("quadratic Tet10 requires finite binary numeric coordinates")
    try:
        ratios = [[v.as_integer_ratio() for v in point] for point in points]
    except (ValueError, OverflowError) as error:
        raise ValueError("quadratic Tet10 requires finite coordinates") from error
    scale = max(denominator for point in ratios for _, denominator in point)
    exact = [
        [numerator * (scale // denominator) for numerator, denominator in point] for point in ratios
    ]
    columns = [[exact[i][j] - exact[0][j] for j in range(3)] for i in (1, 2, 3)]
    a, b, c = columns

    def cross(u: list[int], v: list[int]) -> list[int]:
        return [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]]

    cofactors = [cross(b, c), cross(c, a), cross(a, b)]
    determinant = sum(a[i] * cofactors[0][i] for i in range(3))
    if determinant <= 0:
        raise ValueError("quadratic mapping has degenerate or inverted corners")
    gradients = ((-1, -1, -1), (1, 0, 0), (0, 1, 0), (0, 0, 1))
    edges = ((0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3))
    deviations = [
        [2 * exact[4 + k][j] - exact[u][j] - exact[v][j] for j in range(3)]
        for k, (u, v) in enumerate(edges)
    ]
    for vertex in range(4):
        # Non-affine displacement is sum 2*L_u*L_v*twice_edge_deviation.
        perturbation = [
            [
                sum(
                    2
                    * ((vertex == u) * gradients[v][j] + (vertex == v) * gradients[u][j])
                    * deviations[k][i]
                    for k, (u, v) in enumerate(edges)
                )
                for j in range(3)
            ]
            for i in range(3)
        ]
        relative = [
            [sum(cofactors[i][k] * perturbation[k][j] for k in range(3)) for j in range(3)]
            for i in range(3)
        ]
        bound = max(sum(abs(v) for v in row) for row in relative)
        if bound * 10**10 >= determinant * (10**10 - 1):
            raise ValueError("quadratic mapping positivity cannot be certified over the element")
    # This conversion is only for the public scalar quality measurement; it
    # does not participate in the exact positivity/norm decision above.
    try:
        volume = float(Fraction(determinant, 6 * scale**3))
    except OverflowError as error:
        raise ValueError("positive corner volume is not representable") from error
    if not math.isfinite(volume) or volume <= 0:
        raise ValueError("positive corner volume is not representable")
    return volume
