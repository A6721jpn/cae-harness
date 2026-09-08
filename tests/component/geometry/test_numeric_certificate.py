from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import replace
from fractions import Fraction
from types import SimpleNamespace
from typing import Any

import pytest

from febio_cae.adapters.geometry.backend import BackendError, BackendErrorCategory
from febio_cae.adapters.geometry.gmsh_occ import GmshOCCBackend, GmshOCCConfig
from febio_cae.adapters.geometry.quadratic_quality import require_positive_quadratic_mapping
from febio_cae.domain import PortError, PortErrorCategory

EDGES = ((0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3))


def _midpoints(corners: Sequence[tuple[float, ...]]) -> list[tuple[float, ...]]:
    return list(corners) + [
        tuple((corners[u][j] + corners[v][j]) / 2 for j in range(3)) for u, v in EDGES
    ]


def _coplanar(exponent: int = 0) -> list[tuple[float, ...]]:
    return _midpoints(
        [
            tuple(math.ldexp(x, exponent - 28) for x in row)
            for row in (
                (0, 0, 0),
                (33548835, 26785025, 10796100),
                (22447679, 42496740, 17499811),
                (55996514, 69281765, 28295911),
            )
        ]
    )


def _determinant(points: Any) -> Fraction:
    a, b, c = [
        tuple(Fraction(points[i][j]) - Fraction(points[0][j]) for j in range(3)) for i in (1, 2, 3)
    ]
    return (
        a[0] * (b[1] * c[2] - b[2] * c[1])
        - a[1] * (b[0] * c[2] - b[2] * c[0])
        + a[2] * (b[0] * c[1] - b[1] * c[0])
    )


def _backend_consume(points: Any) -> Any:
    native_mesh = SimpleNamespace(
        getElements=lambda dim, tag: ([11], [[1]], [[1, 2, 3, 4, 5, 6, 7, 8, 10, 9]]),
        getElementProperties=lambda tag: ("Tetrahedron 10", 3, 2, 10, [], 4),
    )
    return GmshOCCBackend(GmshOCCConfig())._tet10_elements(
        SimpleNamespace(model=SimpleNamespace(mesh=native_mesh)),
        1,
        {i + 1: p for i, p in enumerate(points)},
    )


@pytest.mark.parametrize("exponent", [-20, 0, 20])
def test_exactly_coplanar_family_rejected_by_certificate(exponent: int) -> None:
    points = _coplanar(exponent)
    assert _determinant(points) == 0
    with pytest.raises(ValueError, match="degenerate|orientation"):
        require_positive_quadratic_mapping(points)


def test_exactly_coplanar_native_array_path_rejected() -> None:
    with pytest.raises(BackendError) as error:
        _backend_consume(_coplanar())
    assert error.value.category == BackendErrorCategory.QUALITY


def test_exactly_coplanar_connected_consumer_rejected(
    adapter: Any,
    synthetic_backend: Any,
    synthetic_case_revision: Any,
    monkeypatch: Any,
) -> None:
    points = _coplanar()
    original = synthetic_backend.mesh

    def coplanar_mesh(*args: Any) -> Any:
        mesh = original(*args)
        return replace(
            mesh,
            nodes=tuple(
                replace(node, coordinates_si=points[node.node_id - 1]) for node in mesh.nodes
            ),
        )

    monkeypatch.setattr(synthetic_backend, "mesh", coplanar_mesh)
    with pytest.raises(PortError) as error:
        adapter.mesh(synthetic_case_revision)
    assert error.value.category == PortErrorCategory.QUALITY


@pytest.mark.parametrize("exponent", [-100, 0, 100])
@pytest.mark.parametrize("curved", [False, True])
def test_scaled_translated_non_diagonal_supported_neighbors(exponent: int, curved: bool) -> None:
    reference = _midpoints([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)])
    if curved:
        reference[4] = (0.5 + 1 / 128, 0.0, 0.0)
    points = [
        tuple(
            math.ldexp(v, exponent)
            for v in (0.5 + 2 * p[0], -0.25 + p[0] + 3 * p[1], 0.125 + p[1] + 4 * p[2])
        )
        for p in reference
    ]
    assert _determinant(points) == 24 * Fraction(2) ** (3 * exponent)
    require_positive_quadratic_mapping(points)
    assert len(_backend_consume(points)) == 1


def test_near_degenerate_but_exact_affine_neighbor() -> None:
    points = _midpoints([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (1.0, 1.0, 2**-48)])
    assert _determinant(points) == Fraction(2) ** -48
    require_positive_quadratic_mapping(points)
    assert len(_backend_consume(points)) == 1


def _rational_certificate(points: Any) -> bool:
    """Independent Fraction/Gauss-Jordan oracle using full shape derivatives."""
    p: list[list[Fraction]] = [[Fraction(v) for v in row] for row in points]
    if _determinant(points) <= 0:
        return False
    matrix: list[list[Fraction]] = [
        [p[j + 1][i] - p[0][i] for j in range(3)] + [Fraction(i == j) for j in range(3)]
        for i in range(3)
    ]
    for j in range(3):
        pivot = next(i for i in range(j, 3) if matrix[i][j])
        matrix[j], matrix[pivot] = matrix[pivot], matrix[j]
        divisor = matrix[j][j]
        matrix[j] = [v / divisor for v in matrix[j]]
        for i in range(3):
            if i != j:
                factor = matrix[i][j]
                matrix[i] = [a - factor * b for a, b in zip(matrix[i], matrix[j], strict=True)]
    inverse = [row[3:] for row in matrix]
    gradients = ((-1, -1, -1), (1, 0, 0), (0, 1, 0), (0, 0, 1))
    for vertex in range(4):
        derivatives = [[(4 * int(vertex == i) - 1) * v for v in gradients[i]] for i in range(4)]
        derivatives += [
            [
                4 * (int(vertex == u) * gradients[v][j] + int(vertex == v) * gradients[u][j])
                for j in range(3)
            ]
            for u, v in EDGES
        ]
        jacobian = [
            [sum((p[n][i] * derivatives[n][j] for n in range(10)), Fraction(0)) for j in range(3)]
            for i in range(3)
        ]
        relative = [
            [
                sum((inverse[i][k] * jacobian[k][j] for k in range(3)), Fraction(0)) - int(i == j)
                for j in range(3)
            ]
            for i in range(3)
        ]
        if max(sum((abs(v) for v in row), Fraction(0)) for row in relative) >= Fraction(
            9999999999, 10000000000
        ):
            return False
    return True


@pytest.mark.parametrize(
    "delta", [0.0, 1 / 64, 1 / 16, (1 - 2e-10) / 12, (1 - 0.5e-10) / 12, 1 / 8]
)
def test_exact_relative_norm_matches_independent_shape_derivative_oracle(delta: float) -> None:
    reference = _midpoints([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)])
    reference[4] = (0.5 + delta, 0.0, 0.0)
    # Sheared, ill-conditioned but nonsingular affine change of coordinates.
    points = [(p[0] + p[1] + p[2], p[1] + p[2], math.ldexp(p[2], -42)) for p in reference]
    if _rational_certificate(points):
        volume = require_positive_quadratic_mapping(points)
        assert volume == float(_determinant(points) / 6)
    else:
        with pytest.raises(ValueError, match="cannot be certified"):
            require_positive_quadratic_mapping(points)


@pytest.mark.parametrize("exponent", [-400, 400])
def test_unrepresentable_public_volume_is_refused(exponent: int) -> None:
    points = _midpoints(
        [
            (0.0, 0.0, 0.0),
            (math.ldexp(1.0, exponent), 0.0, 0.0),
            (0.0, math.ldexp(1.0, exponent), 0.0),
            (0.0, 0.0, math.ldexp(1.0, exponent)),
        ]
    )
    with pytest.raises(ValueError, match="not representable"):
        require_positive_quadratic_mapping(points)


@pytest.mark.parametrize("bad", ["flat", "inverted", "folded"])
def test_ordinary_invalid_neighbors(bad: str) -> None:
    corners = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)]
    if bad == "flat":
        corners[3] = (1.0, 1.0, 0.0)
    if bad == "inverted":
        corners[1], corners[2] = corners[2], corners[1]
    points = _midpoints(corners)
    if bad == "folded":
        points[4] = (-4.0, 0.0, 0.0)
    with pytest.raises(ValueError):
        require_positive_quadratic_mapping(points)
