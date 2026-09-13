from __future__ import annotations

import importlib
import math
from collections.abc import Sequence
from typing import Any

import pytest

Point = tuple[float, float, float]


def _native_surface() -> Any:
    """Load the leaf during test execution so its absence is a real RED."""
    return importlib.import_module("febio_cae.adapters.meshing.native_surface")


def _tri6_point(points: Sequence[Point], barycentric: Point) -> Point:
    """Evaluate a quadratic Lagrange Tri6 face at one barycentric point."""
    l0, l1, l2 = barycentric
    weights = (
        2 * l0 * l0 - l0,
        2 * l1 * l1 - l1,
        2 * l2 * l2 - l2,
        4 * l0 * l1,
        4 * l1 * l2,
        4 * l2 * l0,
    )
    return (
        sum(weight * points[index][0] for index, weight in enumerate(weights)),
        sum(weight * points[index][1] for index, weight in enumerate(weights)),
        sum(weight * points[index][2] for index, weight in enumerate(weights)),
    )


def _sphere_radial_error(point: Point, radius: float) -> float:
    return abs(math.sqrt(sum(coordinate * coordinate for coordinate in point)) - radius)


def test_outward_sphere_bound_covers_interior_quadratic_error() -> None:
    surface = _native_surface()
    inverse_sqrt_two = math.sqrt(0.5)
    sphere_face = (
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
        (inverse_sqrt_two, inverse_sqrt_two, 0.0),
        (0.0, inverse_sqrt_two, inverse_sqrt_two),
        (inverse_sqrt_two, 0.0, inverse_sqrt_two),
    )

    interior = _tri6_point(sphere_face, (1 / 3, 1 / 3, 1 / 3))
    interior_error = _sphere_radial_error(interior, 1.0)
    bound = surface.primitive_face_distance_upper_bound(
        sphere_face,
        kind="sphere",
        radius_si=1.0,
    )

    assert interior_error > 0.0
    assert math.isfinite(bound)
    assert bound + 1e-12 >= interior_error
    assert 0.0 < bound < 0.3


def test_reversed_sphere_tri6_orientation_is_rejected() -> None:
    surface = _native_surface()
    inverse_sqrt_two = math.sqrt(0.5)
    sphere_face = (
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
        (inverse_sqrt_two, inverse_sqrt_two, 0.0),
        (0.0, inverse_sqrt_two, inverse_sqrt_two),
        (inverse_sqrt_two, 0.0, inverse_sqrt_two),
    )
    reversed_face = (
        sphere_face[0],
        sphere_face[2],
        sphere_face[1],
        sphere_face[5],
        sphere_face[4],
        sphere_face[3],
    )

    with pytest.raises(ValueError):
        surface.primitive_face_distance_upper_bound(
            reversed_face,
            kind="sphere",
            radius_si=1.0,
        )


def test_cylinder_side_bound_covers_projected_midside_error() -> None:
    surface = _native_surface()
    inverse_sqrt_ten = 1 / math.sqrt(10.0)
    cylinder_side = (
        (1.0, 0.0, -0.5),
        (0.8, 0.6, 0.0),
        (1.0, 0.0, 0.5),
        (3 * inverse_sqrt_ten, inverse_sqrt_ten, -0.25),
        (3 * inverse_sqrt_ten, inverse_sqrt_ten, 0.25),
        (1.0, 0.0, 0.0),
    )

    interior = _tri6_point(cylinder_side, (1 / 3, 1 / 3, 1 / 3))
    interior_error = abs(math.hypot(interior[0], interior[1]) - 1.0)
    bound = surface.primitive_face_distance_upper_bound(
        cylinder_side,
        kind="cylinder",
        radius_si=1.0,
        height_si=2.0,
    )

    assert interior_error > 0.0
    assert math.isfinite(bound)
    assert bound + 1e-12 >= interior_error
    assert 0.0 < bound < 0.3


def test_cylinder_caps_account_for_warped_midside_and_flat_cap_zero() -> None:
    surface = _native_surface()
    warped_cap = (
        (0.0, 0.0, 1.0),
        (0.5, 0.0, 1.0),
        (0.0, 0.5, 1.0),
        (0.25, 0.0, 0.75),
        (0.25, 0.25, 1.0),
        (0.0, 0.25, 1.0),
    )
    flat_cap = (
        (0.0, 0.0, 1.0),
        (0.5, 0.0, 1.0),
        (0.0, 0.5, 1.0),
        (0.25, 0.0, 1.0),
        (0.25, 0.25, 1.0),
        (0.0, 0.25, 1.0),
    )

    warped_bound = surface.primitive_face_distance_upper_bound(
        warped_cap,
        kind="cylinder",
        radius_si=1.0,
        height_si=2.0,
    )
    flat_bound = surface.primitive_face_distance_upper_bound(
        flat_cap,
        kind="cylinder",
        radius_si=1.0,
        height_si=2.0,
    )

    axial_error = abs(warped_cap[3][2] - 1.0)
    assert math.isfinite(warped_bound)
    assert warped_bound + 1e-12 >= axial_error
    assert warped_bound < 0.3
    assert flat_bound == pytest.approx(0.0, abs=1e-12)
