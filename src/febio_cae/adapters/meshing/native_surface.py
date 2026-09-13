"""Whole-face Bernstein certificates for local native primitive surfaces.

The public function in this module is deliberately a small numerical leaf.  It
certifies the represented quadratic face and returns a one-sided upper bound on
its distance to a finite analytic primitive boundary.  It does not classify
physical regions, apply placement, or establish a two-sided Hausdorff bound.

All input binary numbers are first represented as exact integers over one common
scale.  The fixed-degree Bernstein operations below retain a common exact
integer denominator; subdivision uses the four dyadic child triangles.  Float
conversion is used only for square roots and is checked against the exact
rational value before a result is returned.
"""

from __future__ import annotations

import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass

ALGORITHM = "native-quadratic-primitive-bernstein-v1"

type _Index = tuple[int, int, int]
type _Point = tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class _Rational:
    numerator: int
    denominator: int


def _rational(numerator: int, denominator: int = 1) -> _Rational:
    if denominator <= 0:
        raise ValueError("internal rational denominator must be positive")
    if numerator == 0:
        return _Rational(0, 1)
    common = math.gcd(abs(numerator), denominator)
    return _Rational(numerator // common, denominator // common)


def _q_add(left: _Rational, right: _Rational) -> _Rational:
    return _rational(
        left.numerator * right.denominator + right.numerator * left.denominator,
        left.denominator * right.denominator,
    )


def _q_sub(left: _Rational, right: _Rational) -> _Rational:
    return _rational(
        left.numerator * right.denominator - right.numerator * left.denominator,
        left.denominator * right.denominator,
    )


def _q_mul(left: _Rational, right: _Rational) -> _Rational:
    return _rational(
        left.numerator * right.numerator,
        left.denominator * right.denominator,
    )


def _q_square(value: _Rational) -> _Rational:
    return _q_mul(value, value)


def _q_neg(value: _Rational) -> _Rational:
    return _Rational(-value.numerator, value.denominator)


def _q_less(left: _Rational, right: _Rational) -> bool:
    return left.numerator * right.denominator < right.numerator * left.denominator


def _q_equal(left: _Rational, right: _Rational) -> bool:
    return left.numerator * right.denominator == right.numerator * left.denominator


def _q_min(left: _Rational, right: _Rational) -> _Rational:
    return left if _q_less(left, right) else right


def _q_max(left: _Rational, right: _Rational) -> _Rational:
    return right if _q_less(left, right) else left


def _q_zero() -> _Rational:
    return _Rational(0, 1)


def _q_from_int(value: int) -> _Rational:
    return _Rational(value, 1)


def _indices(degree: int) -> tuple[_Index, ...]:
    return tuple(
        (first, second, degree - first - second)
        for first in range(degree, -1, -1)
        for second in range(degree - first, -1, -1)
    )


_INDICES = {degree: _indices(degree) for degree in range(5)}
_INDEX_POSITIONS = {
    degree: {index: position for position, index in enumerate(indices)}
    for degree, indices in _INDICES.items()
}


def _multinomial(index: _Index) -> int:
    degree = sum(index)
    return math.factorial(degree) // math.prod(math.factorial(value) for value in index)


_MULTINOMIALS = {
    degree: tuple(_multinomial(index) for index in indices) for degree, indices in _INDICES.items()
}
_MULTINOMIAL_BY_INDEX = {
    degree: dict(zip(_INDICES[degree], _MULTINOMIALS[degree], strict=True)) for degree in range(5)
}


@dataclass(frozen=True, slots=True)
class _Bernstein:
    degree: int
    numerators: tuple[int, ...]
    denominator: int


def _make_polynomial(degree: int, numerators: Sequence[int], denominator: int) -> _Bernstein:
    if denominator <= 0:
        raise ValueError("internal Bernstein denominator must be positive")
    expected = len(_INDICES[degree])
    if len(numerators) != expected:
        raise ValueError("internal Bernstein coefficient count mismatch")
    common = denominator
    for numerator in numerators:
        common = math.gcd(common, abs(numerator))
    if common > 1:
        denominator //= common
        numerators = tuple(numerator // common for numerator in numerators)
    if not any(numerators):
        denominator = 1
    return _Bernstein(degree, tuple(numerators), denominator)


def _polynomial_add(left: _Bernstein, right: _Bernstein) -> _Bernstein:
    if left.degree != right.degree:
        raise ValueError("internal Bernstein degree mismatch")
    common = math.lcm(left.denominator, right.denominator)
    left_factor = common // left.denominator
    right_factor = common // right.denominator
    numerators = tuple(
        left_value * left_factor + right_value * right_factor
        for left_value, right_value in zip(left.numerators, right.numerators, strict=True)
    )
    return _make_polynomial(left.degree, numerators, common)


def _polynomial_negate(value: _Bernstein) -> _Bernstein:
    return _make_polynomial(
        value.degree,
        tuple(-item for item in value.numerators),
        value.denominator,
    )


def _polynomial_sub(left: _Bernstein, right: _Bernstein) -> _Bernstein:
    return _polynomial_add(left, _polynomial_negate(right))


def _polynomial_product(left: _Bernstein, right: _Bernstein) -> _Bernstein:
    degree = left.degree + right.degree
    total_indices = _INDICES[degree]
    total_multinomials = _MULTINOMIAL_BY_INDEX[degree]
    common_multinomial = 1
    for value in total_multinomials.values():
        common_multinomial = math.lcm(common_multinomial, value)

    left_positions = _INDEX_POSITIONS[left.degree]
    right_positions = _INDEX_POSITIONS[right.degree]
    left_multinomials = _MULTINOMIAL_BY_INDEX[left.degree]
    right_multinomials = _MULTINOMIAL_BY_INDEX[right.degree]
    numerators: list[int] = []
    for total_index in total_indices:
        coefficient = 0
        total_multinomial = total_multinomials[total_index]
        for left_index in _INDICES[left.degree]:
            right_index: _Index = (
                total_index[0] - left_index[0],
                total_index[1] - left_index[1],
                total_index[2] - left_index[2],
            )
            if min(right_index) < 0 or sum(right_index) != right.degree:
                continue
            coefficient += (
                left.numerators[left_positions[left_index]]
                * right.numerators[right_positions[right_index]]
                * left_multinomials[left_index]
                * right_multinomials[right_index]
            )
        numerators.append(coefficient * (common_multinomial // total_multinomial))
    denominator = left.denominator * right.denominator * common_multinomial
    return _make_polynomial(degree, numerators, denominator)


def _polynomial_minmax(value: _Bernstein) -> tuple[_Rational, _Rational]:
    minimum = value.numerators[0]
    maximum = minimum
    for numerator in value.numerators[1:]:
        minimum = min(minimum, numerator)
        maximum = max(maximum, numerator)
    return _rational(minimum, value.denominator), _rational(maximum, value.denominator)


_COMPOSITIONS = {degree: _indices(degree) for degree in range(5)}
_CHILD_VERTICES: tuple[tuple[_Index, _Index, _Index], ...] = (
    ((2, 0, 0), (1, 1, 0), (1, 0, 1)),
    ((1, 1, 0), (0, 2, 0), (0, 1, 1)),
    ((1, 0, 1), (0, 1, 1), (0, 0, 2)),
    ((1, 1, 0), (0, 1, 1), (1, 0, 1)),
)


def _compose_child(value: _Bernstein, child_vertices: Sequence[_Index]) -> _Bernstein:
    """Compose a Bernstein polynomial with one dyadic child triangle."""
    degree = value.degree
    power_coefficients: dict[_Index, int] = {}
    value_positions = _INDEX_POSITIONS[degree]
    for parent_index in _INDICES[degree]:
        terms: dict[_Index, int] = {(0, 0, 0): 1}
        for parent_axis, exponent in enumerate(parent_index):
            row_terms: dict[_Index, int] = {}
            for exponent_split in _COMPOSITIONS[exponent]:
                coefficient = _multinomial(exponent_split)
                for child_axis in range(3):
                    coefficient *= (
                        child_vertices[child_axis][parent_axis] ** exponent_split[child_axis]
                    )
                row_terms[exponent_split] = coefficient
            expanded: dict[_Index, int] = {}
            for existing_index, existing_coefficient in terms.items():
                for row_index, row_coefficient in row_terms.items():
                    combined: _Index = (
                        existing_index[0] + row_index[0],
                        existing_index[1] + row_index[1],
                        existing_index[2] + row_index[2],
                    )
                    expanded[combined] = expanded.get(combined, 0) + (
                        existing_coefficient * row_coefficient
                    )
            terms = expanded
        multiplier = _MULTINOMIAL_BY_INDEX[degree][parent_index]
        parent_numerator = value.numerators[value_positions[parent_index]]
        for index, coefficient in terms.items():
            power_coefficients[index] = power_coefficients.get(index, 0) + (
                parent_numerator * multiplier * coefficient
            )

    denominator = value.denominator * (2**degree) * math.factorial(degree)
    numerators = tuple(
        power_coefficients.get(index, 0)
        * math.prod(math.factorial(component) for component in index)
        for index in _INDICES[degree]
    )
    return _make_polynomial(degree, numerators, denominator)


def _subdivide(value: _Bernstein) -> tuple[_Bernstein, ...]:
    return tuple(_compose_child(value, vertices) for vertices in _CHILD_VERTICES)


def _enclosure(value: _Bernstein) -> tuple[_Rational, _Rational]:
    # One exact split tightens the enclosure while retaining complete coverage.
    # Orientation uses its separate adaptive proof, not this distance bound.
    leaves = iter(_subdivide(value))
    minimum, maximum = _polynomial_minmax(next(leaves))
    for leaf in leaves:
        leaf_minimum, leaf_maximum = _polynomial_minmax(leaf)
        minimum = _q_min(minimum, leaf_minimum)
        maximum = _q_max(maximum, leaf_maximum)
    return minimum, maximum


_ORIENTATION_MAX_DEPTH = 8
_ORIENTATION_MAX_NODES = 4096


def _certify_positive(value: _Bernstein) -> bool:
    pending: list[tuple[_Bernstein, int]] = [(value, 0)]
    visited = 0
    while pending:
        if visited >= _ORIENTATION_MAX_NODES:
            return False
        current, depth = pending.pop()
        visited += 1
        if all(numerator > 0 for numerator in current.numerators):
            continue
        if all(numerator <= 0 for numerator in current.numerators):
            return False
        if depth >= _ORIENTATION_MAX_DEPTH:
            return False
        pending.extend((child, depth + 1) for child in _subdivide(current))
    return True


def _derivative(value: _Bernstein, direction: int) -> _Bernstein:
    if value.degree == 0:
        return _make_polynomial(0, (0,), value.denominator)
    degree = value.degree
    positions = _INDEX_POSITIONS[degree]
    numerators: list[int] = []
    for index in _INDICES[degree - 1]:
        plus: _Index = (
            index[0] + int(direction == 0),
            index[1] + int(direction == 1),
            index[2] + int(direction == 2),
        )
        minus: _Index = (index[0] + 1, index[1], index[2])
        numerators.append(
            degree * (value.numerators[positions[plus]] - value.numerators[positions[minus]])
        )
    return _make_polynomial(degree - 1, numerators, value.denominator)


def _cross(
    left: tuple[_Bernstein, _Bernstein, _Bernstein],
    right: tuple[_Bernstein, _Bernstein, _Bernstein],
) -> tuple[_Bernstein, _Bernstein, _Bernstein]:
    return (
        _polynomial_sub(
            _polynomial_product(left[1], right[2]),
            _polynomial_product(left[2], right[1]),
        ),
        _polynomial_sub(
            _polynomial_product(left[2], right[0]),
            _polynomial_product(left[0], right[2]),
        ),
        _polynomial_sub(
            _polynomial_product(left[0], right[1]),
            _polynomial_product(left[1], right[0]),
        ),
    )


def _dot(
    left: tuple[_Bernstein, _Bernstein, _Bernstein],
    right: tuple[_Bernstein, _Bernstein, _Bernstein],
) -> _Bernstein:
    result = _polynomial_product(left[0], right[0])
    result = _polynomial_add(result, _polynomial_product(left[1], right[1]))
    return _polynomial_add(result, _polynomial_product(left[2], right[2]))


def _binary_ratio(value: float) -> tuple[int, int]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("native surface inputs must be finite binary numbers")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("native surface inputs must be finite binary numbers")
    try:
        return value.as_integer_ratio()
    except (AttributeError, OverflowError, ValueError) as error:
        raise ValueError("native surface inputs must be finite binary numbers") from error


def _read_input(
    coordinates_si: Sequence[Sequence[float]],
    kind: str,
    radius_si: float,
    height_si: float | None,
) -> tuple[tuple[_Point, ...], int, int, int, int]:
    if kind not in {"sphere", "cylinder"}:
        raise ValueError("native surface kind must be sphere or cylinder")
    try:
        if len(coordinates_si) != 6:
            raise ValueError("native surface requires six Tri6 points")
    except TypeError as error:
        raise ValueError("native surface requires six Tri6 points") from error

    coordinate_ratios: list[tuple[tuple[int, int], ...]] = []
    for point in coordinates_si:
        try:
            if len(point) != 3:
                raise ValueError("native surface requires six three-component points")
        except TypeError as error:
            raise ValueError("native surface requires six three-component points") from error
        coordinate_ratios.append(tuple(_binary_ratio(value) for value in point))

    radius_ratio = _binary_ratio(radius_si)
    if radius_ratio[0] <= 0:
        raise ValueError("native surface radius must be positive")
    if kind == "cylinder":
        if height_si is None:
            raise ValueError("cylinder height is required")
        height_ratio = _binary_ratio(height_si)
        if height_ratio[0] <= 0:
            raise ValueError("native surface height must be positive")
    else:
        if height_si is not None:
            raise ValueError("sphere does not accept a height")
        height_ratio = (0, 1)

    denominators = [denominator for point in coordinate_ratios for _, denominator in point]
    denominators.extend((radius_ratio[1], height_ratio[1]))
    scale = max(denominators)
    exact_coordinates: tuple[_Point, ...] = tuple(
        (
            point[0][0] * (scale // point[0][1]),
            point[1][0] * (scale // point[1][1]),
            point[2][0] * (scale // point[2][1]),
        )
        for point in coordinate_ratios
    )
    exact_radius = radius_ratio[0] * (scale // radius_ratio[1])
    exact_height = height_ratio[0] * (scale // height_ratio[1])
    return exact_coordinates, scale, exact_radius, exact_height, 2 * scale


def _position_polynomials(
    points: tuple[_Point, ...], denominator: int
) -> tuple[_Bernstein, _Bernstein, _Bernstein]:
    control_indices = (
        (2, 0, 0),
        (0, 2, 0),
        (0, 0, 2),
        (1, 1, 0),
        (0, 1, 1),
        (1, 0, 1),
    )
    controls = [
        (
            2 * points[0][axis],
            2 * points[1][axis],
            2 * points[2][axis],
            4 * points[3][axis] - points[0][axis] - points[1][axis],
            4 * points[4][axis] - points[1][axis] - points[2][axis],
            4 * points[5][axis] - points[2][axis] - points[0][axis],
        )
        for axis in range(3)
    ]
    result: list[_Bernstein] = []
    for axis_controls in controls:
        by_index = dict(zip(control_indices, axis_controls, strict=True))
        result.append(
            _make_polynomial(
                2,
                tuple(by_index[index] for index in _INDICES[2]),
                denominator,
            )
        )
    return result[0], result[1], result[2]


def _squared_norm(
    position: tuple[_Bernstein, _Bernstein, _Bernstein], include_z: bool
) -> _Bernstein:
    result = _polynomial_product(position[0], position[0])
    result = _polynomial_add(result, _polynomial_product(position[1], position[1]))
    if include_z:
        result = _polynomial_add(result, _polynomial_product(position[2], position[2]))
    return result


def _nonnegative(value: _Rational) -> _Rational:
    return _q_max(value, _q_zero())


def _sqrt_approximation(numerator: int, denominator: int) -> float:
    numerator_bits = numerator.bit_length()
    denominator_bits = denominator.bit_length()
    numerator_take = min(numerator_bits, 53)
    denominator_take = min(denominator_bits, 53)
    numerator_top = numerator >> (numerator_bits - numerator_take)
    denominator_top = denominator >> (denominator_bits - denominator_take)
    numerator_mantissa = math.ldexp(float(numerator_top), -numerator_take)
    denominator_mantissa = math.ldexp(float(denominator_top), -denominator_take)
    ratio_mantissa = numerator_mantissa / denominator_mantissa
    exponent = numerator_bits - denominator_bits
    if exponent % 2:
        ratio_mantissa *= 2.0
        exponent -= 1
    try:
        return math.ldexp(math.sqrt(ratio_mantissa), exponent // 2)
    except OverflowError:
        return math.inf


def _square_at_least(candidate: float, numerator: int, denominator: int) -> bool:
    candidate_numerator, candidate_denominator = candidate.as_integer_ratio()
    return (
        candidate_numerator * candidate_numerator * denominator
        >= numerator * candidate_denominator * candidate_denominator
    )


def _square_at_most(candidate: float, numerator: int, denominator: int) -> bool:
    candidate_numerator, candidate_denominator = candidate.as_integer_ratio()
    return (
        candidate_numerator * candidate_numerator * denominator
        <= numerator * candidate_denominator * candidate_denominator
    )


def _sqrt_ratio(numerator: int, denominator: int, *, upward: bool) -> float:
    if numerator < 0 or denominator <= 0:
        raise ValueError("internal square-root ratio must be nonnegative")
    if numerator == 0:
        return 0.0
    candidate = _sqrt_approximation(numerator, denominator)
    if not math.isfinite(candidate):
        candidate = sys.float_info.max
    if candidate == 0.0 and upward:
        candidate = math.nextafter(0.0, math.inf)

    # The scaled 53-bit approximation is within a handful of ulps.  The exact
    # integer comparison makes the finite adjustment a proof, not a tolerance.
    for _ in range(16):
        if upward:
            if _square_at_least(candidate, numerator, denominator):
                return candidate
            next_candidate = math.nextafter(candidate, math.inf)
            if not math.isfinite(next_candidate):
                break
        else:
            if _square_at_most(candidate, numerator, denominator):
                return candidate
            next_candidate = math.nextafter(candidate, 0.0)
        candidate = next_candidate
    raise ValueError("native surface distance is not representable")


def _sqrt_down(value: _Rational) -> float:
    return _sqrt_ratio(value.numerator, value.denominator, upward=False)


def _sqrt_up(value: _Rational) -> float:
    return _sqrt_ratio(value.numerator, value.denominator, upward=True)


def _float_as_rational(value: float) -> _Rational:
    numerator, denominator = value.as_integer_ratio()
    return _rational(numerator, denominator)


def _radial_error_squared_upper(
    minimum_squared: _Rational,
    maximum_squared: _Rational,
    radius: _Rational,
) -> _Rational:
    minimum_squared = _nonnegative(minimum_squared)
    maximum_squared = _nonnegative(maximum_squared)
    radius_squared = _q_square(radius)

    def endpoint_bound(squared_radius: _Rational) -> _Rational:
        lower_root = _float_as_rational(_sqrt_down(squared_radius))
        result = _q_sub(
            _q_add(squared_radius, radius_squared),
            _q_mul(_q_mul(_q_from_int(2), radius), lower_root),
        )
        return _nonnegative(result)

    return _q_max(endpoint_bound(minimum_squared), endpoint_bound(maximum_squared))


def _radial_overshoot_squared_upper(maximum_squared: _Rational, radius: _Rational) -> _Rational:
    maximum_squared = _nonnegative(maximum_squared)
    if _q_less(maximum_squared, _q_square(radius)) or _q_equal(maximum_squared, _q_square(radius)):
        return _q_zero()
    lower_root = _float_as_rational(_sqrt_down(maximum_squared))
    return _nonnegative(
        _q_sub(
            _q_add(maximum_squared, _q_square(radius)),
            _q_mul(_q_mul(_q_from_int(2), radius), lower_root),
        )
    )


def _interval_distance_squared_upper(
    minimum: _Rational, maximum: _Rational, target: _Rational
) -> _Rational:
    return _q_max(_q_square(_q_sub(minimum, target)), _q_square(_q_sub(maximum, target)))


def _sphere_bound(position: tuple[_Bernstein, _Bernstein, _Bernstein], radius: _Rational) -> float:
    first_derivative = (
        _derivative(position[0], 1),
        _derivative(position[1], 1),
        _derivative(position[2], 1),
    )
    second_derivative = (
        _derivative(position[0], 2),
        _derivative(position[1], 2),
        _derivative(position[2], 2),
    )
    normal = _cross(first_derivative, second_derivative)
    orientation = _dot(position, normal)
    if not _certify_positive(orientation):
        raise ValueError("sphere face outward orientation cannot be certified")
    minimum, maximum = _enclosure(_squared_norm(position, include_z=True))
    if _q_less(maximum, _q_zero()):
        raise ValueError("sphere face squared radius cannot be certified")
    bound_squared = _radial_error_squared_upper(minimum, maximum, radius)
    result = _sqrt_up(bound_squared)
    if not math.isfinite(result):
        raise ValueError("native surface distance is not representable")
    return result


def _cylinder_bound(
    position: tuple[_Bernstein, _Bernstein, _Bernstein],
    radius: _Rational,
    height: _Rational,
) -> float:
    first_derivative = (
        _derivative(position[0], 1),
        _derivative(position[1], 1),
        _derivative(position[2], 1),
    )
    second_derivative = (
        _derivative(position[0], 2),
        _derivative(position[1], 2),
        _derivative(position[2], 2),
    )
    normal = _cross(first_derivative, second_derivative)
    side_orientation = _certify_positive(_q_dot(position, normal))
    top_orientation = _certify_positive(normal[2])
    bottom_orientation = _certify_positive(_polynomial_negate(normal[2]))

    radial_squared = _enclosure(_squared_norm(position, include_z=False))
    z_minimum, z_maximum = _enclosure(position[2])
    half_height = _q_mul(height, _q_rational_half())
    radius_squared = _radial_error_squared_upper(radial_squared[0], radial_squared[1], radius)
    outside_top = _nonnegative(_q_sub(z_maximum, half_height))
    outside_bottom = _nonnegative(_q_sub(_q_neg(half_height), z_minimum))
    axial_outside = _q_max(outside_top, outside_bottom)
    side_squared = _q_add(radius_squared, _q_square(axial_outside))

    top_plane_squared = _interval_distance_squared_upper(z_minimum, z_maximum, half_height)
    bottom_plane_squared = _interval_distance_squared_upper(
        z_minimum, z_maximum, _q_neg(half_height)
    )
    radial_overshoot = _radial_overshoot_squared_upper(radial_squared[1], radius)
    top_squared = _q_add(top_plane_squared, radial_overshoot)
    bottom_squared = _q_add(bottom_plane_squared, radial_overshoot)

    candidates = (
        (side_squared, side_orientation),
        (top_squared, top_orientation),
        (bottom_squared, bottom_orientation),
    )
    minimum_squared = candidates[0][0]
    for candidate, _ in candidates[1:]:
        minimum_squared = _q_min(minimum_squared, candidate)
    if not any(
        certified and _q_equal(candidate, minimum_squared) for candidate, certified in candidates
    ):
        raise ValueError("cylinder face outward orientation cannot be certified")
    result = _sqrt_up(minimum_squared)
    if not math.isfinite(result):
        raise ValueError("native surface distance is not representable")
    return result


def _q_rational_half() -> _Rational:
    return _Rational(1, 2)


def _q_dot(
    position: tuple[_Bernstein, _Bernstein, _Bernstein],
    normal: tuple[_Bernstein, _Bernstein, _Bernstein],
) -> _Bernstein:
    return _polynomial_add(
        _polynomial_product(position[0], normal[0]),
        _polynomial_product(position[1], normal[1]),
    )


def primitive_face_distance_upper_bound(
    coordinates_si: Sequence[Sequence[float]],
    *,
    kind: str,
    radius_si: float,
    height_si: float | None = None,
) -> float:
    """Return a conservative complete-face distance upper bound in SI units.

    ``coordinates_si`` contains corners 0/1/2 followed by midsides 01/12/20.
    The canonical order is part of the orientation certificate; this function
    never reorders the face or takes an absolute normal component.
    """
    points, scale, exact_radius, exact_height, position_denominator = _read_input(
        coordinates_si, kind, radius_si, height_si
    )
    position = _position_polynomials(points, position_denominator)
    radius = _rational(exact_radius, scale)
    if kind == "sphere":
        return _sphere_bound(position, radius)
    return _cylinder_bound(position, radius, _rational(exact_height, scale))


__all__ = ["ALGORITHM", "primitive_face_distance_upper_bound"]
