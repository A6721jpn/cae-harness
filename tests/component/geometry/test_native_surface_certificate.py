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
    assert bound >= interior_error
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
    assert bound >= interior_error
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
    assert warped_bound >= axial_error
    assert warped_bound < 0.3
    assert flat_bound == 0.0


@pytest.mark.parametrize(
    "scale",
    (math.ldexp(1.0, -1072), math.ldexp(1.0, 1020)),
    ids=("subnormal", "squared-norm-overflow"),
)
def test_sphere_bound_rounds_outward_at_binary_range_boundaries(scale: float) -> None:
    points = (
        (0.0, 0.0, 2 * scale),
        (3 * scale, 0.0, 2 * scale),
        (0.0, 3 * scale, 2 * scale),
        (1.5 * scale, 0.0, 2 * scale),
        (1.5 * scale, 1.5 * scale, 2 * scale),
        (0.0, 1.5 * scale, 2 * scale),
    )
    bound = _native_surface().primitive_face_distance_upper_bound(
        points,
        kind="sphere",
        radius_si=scale,
    )
    assert math.isfinite(bound) and bound > 0
    numerator, denominator = bound.as_integer_ratio()
    scale_numerator, scale_denominator = scale.as_integer_ratio()
    # The farthest vertex has exact squared radius 13*scale**2.
    # Verify bound + radius >= sqrt(13)*scale without floating arithmetic.
    assert (
        numerator * scale_denominator + scale_numerator * denominator
    ) ** 2 >= 13 * scale_numerator**2 * denominator**2


def test_cylinder_side_bound_includes_axial_overflow() -> None:
    points = (
        (1.0, -0.1, 2.5),
        (1.0, 0.1, 2.5),
        (1.0, 0.0, 3.5),
        (1.0, 0.0, 2.5),
        (1.0, 0.05, 3.0),
        (1.0, -0.05, 3.0),
    )
    bound = _native_surface().primitive_face_distance_upper_bound(
        points,
        kind="cylinder",
        radius_si=1.0,
        height_si=2.0,
    )
    assert math.isfinite(bound)
    numerator, denominator = bound.as_integer_ratio()
    assert 2 * numerator >= 5 * denominator


def test_cylinder_cap_bound_includes_disk_overflow() -> None:
    points = (
        (2.0, 0.0, 1.0),
        (3.0, 0.0, 1.0),
        (3.0, 1.0, 1.0),
        (2.5, 0.0, 1.0),
        (3.0, 0.5, 1.0),
        (2.5, 0.5, 1.0),
    )
    bound = _native_surface().primitive_face_distance_upper_bound(
        points,
        kind="cylinder",
        radius_si=1.0,
        height_si=2.0,
    )
    assert math.isfinite(bound)
    numerator, denominator = bound.as_integer_ratio()
    assert (numerator + denominator) ** 2 >= 10 * denominator**2


def test_cylinder_side_rejects_reversed_winding() -> None:
    points = (
        (1.0, -0.1, -0.5),
        (1.0, 0.1, -0.5),
        (1.0, 0.0, 0.5),
        (1.0, 0.0, -0.5),
        (1.0, 0.05, 0.0),
        (1.0, -0.05, 0.0),
    )
    reversed_points = tuple(points[index] for index in (0, 2, 1, 5, 4, 3))
    with pytest.raises(ValueError):
        _native_surface().primitive_face_distance_upper_bound(
            reversed_points,
            kind="cylinder",
            radius_si=1.0,
            height_si=2.0,
        )


def test_cylinder_cap_rejects_interior_orientation_fold() -> None:
    # x=u+2*v**2, y=v+2*u**2: determinant 1-16*u*v is positive
    # at all three parameter vertices but negative at the barycenter.
    parameters = ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (0.5, 0.0), (0.5, 0.5), (0.0, 0.5))
    points = tuple((u + 2 * v * v, v + 2 * u * u, 1.0) for u, v in parameters)
    with pytest.raises(ValueError):
        _native_surface().primitive_face_distance_upper_bound(
            points,
            kind="cylinder",
            radius_si=4.0,
            height_si=2.0,
        )
