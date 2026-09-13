from __future__ import annotations

import importlib
import math
import sys
from typing import Any

import pytest

Point = tuple[float, float, float]


def _native_surface() -> Any:
    return importlib.import_module("febio_cae.adapters.meshing.native_surface")


def test_sphere_minimum_finds_interior_penetration_between_outside_nodes() -> None:
    # Every nodal point is outside the unit sphere, but the quadratic patch
    # contains the origin at its barycenter.
    points: tuple[Point, ...] = (
        (1.5, 0.0, 0.0),
        (0.0, 1.5, 0.0),
        (0.0, 0.0, 1.5),
        (1.0, 1.0, 1.0),
        (-1.0, -1.0, 1.0),
        (0.375, 0.375, -1.625),
    )

    lower, upper = _native_surface().primitive_face_minimum_signed_distance_interval(
        "sphere",
        points,
        radius_si=1.0,
        tolerance_si=1.0e-8,
    )

    assert lower <= -1.0 <= upper
    assert upper < -0.9


@pytest.mark.parametrize("kind", ("sphere", "cylinder"))
def test_signed_radial_sqrt_subtracts_radius_before_float_conversion(kind: str) -> None:
    maximum = sys.float_info.max
    points: tuple[Point, ...] = ((maximum, maximum, 0.0),) * 6
    expected = (math.sqrt(2.0) - 1.0) * maximum

    if kind == "sphere":
        lower, upper = _native_surface().primitive_face_minimum_signed_distance_interval(
            kind,
            points,
            radius_si=maximum,
            tolerance_si=1.0,
        )
    else:
        lower, upper = _native_surface().primitive_face_minimum_signed_distance_interval(
            kind,
            points,
            radius_si=maximum,
            height_si=maximum,
            tolerance_si=1.0,
        )

    assert math.isfinite(lower) and math.isfinite(upper)
    assert lower <= upper
    assert math.isclose(lower, expected, rel_tol=1.0e-15)
    assert math.isclose(upper, expected, rel_tol=1.0e-15)


def test_signed_leaf_accepts_a_reversed_patch_orientation() -> None:
    points: tuple[Point, ...] = (
        (1.5, 0.0, 0.0),
        (0.0, 1.5, 0.0),
        (0.0, 0.0, 1.5),
        (1.0, 1.0, 1.0),
        (-1.0, -1.0, 1.0),
        (0.375, 0.375, -1.625),
    )
    reversed_points = tuple(points[index] for index in (0, 2, 1, 5, 4, 3))

    lower, upper = _native_surface().primitive_face_minimum_signed_distance_interval(
        "sphere",
        reversed_points,
        radius_si=1.0,
        tolerance_si=1.0e-8,
    )

    assert lower <= -1.0 <= upper


def test_cylinder_side_cap_transition_includes_the_corner() -> None:
    points: tuple[Point, ...] = (
        (1.0, 0.0, 1.0),
        (1.5, 0.0, 1.0),
        (1.0, 0.0, 1.5),
        (1.25, 0.0, 1.0),
        (1.25, 0.0, 1.25),
        (1.0, 0.0, 1.25),
    )

    lower, upper = _native_surface().primitive_face_minimum_signed_distance_interval(
        "cylinder",
        points,
        radius_si=1.0,
        height_si=2.0,
        tolerance_si=1.0e-8,
    )

    assert lower <= 0.0 <= upper
    assert upper <= 1.0e-8


def test_box_corner_distance_uses_all_three_outside_features() -> None:
    points: tuple[Point, ...] = (
        (1.5, 2.5, 3.5),
        (2.0, 2.5, 3.5),
        (1.5, 3.0, 3.5),
        (1.75, 2.5, 3.5),
        (1.75, 2.75, 3.5),
        (1.5, 2.75, 3.5),
    )
    expected = math.sqrt(0.75)

    lower, upper = _native_surface().primitive_face_minimum_signed_distance_interval(
        "box",
        points,
        dimensions_si=(2.0, 4.0, 6.0),
        tolerance_si=1.0e-8,
    )

    assert lower <= expected <= upper
    assert upper - lower <= 1.0e-8


def test_subdivision_budget_preserves_negative_interior_before_discovery() -> None:
    # P(u, v) = (u - 1/8, v - 1/8, 0) has its origin at an interior point
    # that is not among the initial finite witnesses.
    points: tuple[Point, ...] = (
        (7.0 / 8.0, -1.0 / 8.0, 0.0),
        (-1.0 / 8.0, 7.0 / 8.0, 0.0),
        (-1.0 / 8.0, -1.0 / 8.0, 0.0),
        (3.0 / 8.0, 3.0 / 8.0, 0.0),
        (-1.0 / 8.0, 3.0 / 8.0, 0.0),
        (3.0 / 8.0, -1.0 / 8.0, 0.0),
    )
    radius = 1.0 / 64.0

    coarse_lower, coarse_upper = _native_surface().primitive_face_minimum_signed_distance_interval(
        "sphere",
        points,
        radius_si=radius,
        tolerance_si=1.0e-3,
        max_subdivisions=1,
    )
    refined_lower, refined_upper = (
        _native_surface().primitive_face_minimum_signed_distance_interval(
            "sphere",
            points,
            radius_si=radius,
            tolerance_si=1.0e-3,
            max_subdivisions=256,
        )
    )

    assert coarse_lower <= -radius <= coarse_upper
    assert coarse_lower < 0.0 < coarse_upper
    assert refined_lower <= -radius <= refined_upper
    assert refined_upper < 0.0
    assert refined_upper - refined_lower <= 1.0e-3


def test_cylinder_interior_has_a_negative_signed_minimum() -> None:
    points: tuple[Point, ...] = ((0.0, 0.0, 0.0),) * 6

    lower, upper = _native_surface().primitive_face_minimum_signed_distance_interval(
        "cylinder",
        points,
        radius_si=1.0,
        height_si=2.0,
        tolerance_si=1.0e-8,
    )

    assert lower <= -1.0 <= upper
    assert upper < 0.0


def test_box_interior_has_a_negative_signed_minimum() -> None:
    points: tuple[Point, ...] = ((0.0, 0.0, 0.0),) * 6

    lower, upper = _native_surface().primitive_face_minimum_signed_distance_interval(
        "box",
        points,
        dimensions_si=(2.0, 4.0, 6.0),
        tolerance_si=1.0e-8,
    )

    assert lower <= -1.0 <= upper
    assert upper < 0.0


def test_cylinder_outside_side_and_cap_combines_both_features() -> None:
    points: tuple[Point, ...] = ((2.0, 0.0, 2.0),) * 6
    expected = math.sqrt(2.0)

    lower, upper = _native_surface().primitive_face_minimum_signed_distance_interval(
        "cylinder",
        points,
        radius_si=1.0,
        height_si=2.0,
        tolerance_si=1.0e-8,
    )

    assert lower <= expected <= upper
    assert upper - lower <= 1.0e-8


def test_exact_binary_boundaries_and_invalid_dimensions_are_checked() -> None:
    scale = math.ldexp(1.0, -1074)
    boundary_points: tuple[Point, ...] = ((scale, 0.0, 0.0),) * 6
    lower, upper = _native_surface().primitive_face_minimum_signed_distance_interval(
        "sphere",
        boundary_points,
        radius_si=scale,
        tolerance_si=scale,
    )
    assert math.isfinite(lower) and math.isfinite(upper)
    assert lower <= 0.0 <= upper

    origin_points: tuple[Point, ...] = ((0.0, 0.0, 0.0),) * 6
    with pytest.raises((TypeError, ValueError)):
        _native_surface().primitive_face_minimum_signed_distance_interval(
            "sphere",
            origin_points,
            radius_si=math.inf,
            tolerance_si=1.0,
        )
    with pytest.raises((TypeError, ValueError)):
        _native_surface().primitive_face_minimum_signed_distance_interval(
            "box",
            origin_points,
            dimensions_si=(1.0, 2.0),
            tolerance_si=1.0,
        )
    with pytest.raises((TypeError, ValueError)):
        _native_surface().primitive_face_minimum_signed_distance_interval(
            "cylinder",
            origin_points,
            radius_si=1.0,
            height_si=2.0,
            dimensions_si=(1.0, 1.0, 1.0),
            tolerance_si=1.0,
        )


def test_invalid_tolerance_and_subdivision_budget_are_rejected() -> None:
    points: tuple[Point, ...] = ((0.0, 0.0, 0.0),) * 6

    with pytest.raises((TypeError, ValueError)):
        _native_surface().primitive_face_minimum_signed_distance_interval(
            "sphere",
            points,
            radius_si=1.0,
            tolerance_si=0.0,
        )
    with pytest.raises((TypeError, ValueError)):
        _native_surface().primitive_face_minimum_signed_distance_interval(
            "sphere",
            points,
            radius_si=1.0,
            tolerance_si=1.0,
            max_subdivisions=0,
        )
