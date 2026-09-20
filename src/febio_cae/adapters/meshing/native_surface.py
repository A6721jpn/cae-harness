"""Whole-face Bernstein certificates for local native primitive surfaces.

The unsigned public function in this module certifies a represented quadratic
face and returns a one-sided upper bound on its maximum distance to a finite
analytic primitive boundary.  The signed public function instead encloses the
minimum signed distance and has a finite subdivision budget; an interval that
has not reached the requested tolerance remains conservative but may be
ambiguous.  Neither API classifies physical regions, applies placement, or
establishes a two-sided Hausdorff bound.

All input binary numbers are first represented as exact integers over one common
scale.  The fixed-degree Bernstein operations below retain a common exact
integer denominator; subdivision uses the four dyadic child triangles.  The
unsigned path converts square-root bounds to floats only after exact checks;
the signed path retains rational square-root enclosures through signed-feature
subtractions and checks float conversion only at the final interval endpoints.
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


_SIGNED_MAX_DEPTH = 24
_SIGNED_MAX_WORK = 4096
_SIGNED_SQRT_SCALE = 1 << 64


@dataclass(frozen=True, slots=True)
class _SignedInput:
    points: tuple[_Point, ...]
    position_denominator: int
    kind: str
    radius: _Rational | None
    half_height: _Rational | None
    half_dimensions: tuple[_Rational, ...] | None
    tolerance: _Rational
    work_limit: int


@dataclass(slots=True)
class _SignedCell:
    position: tuple[_Bernstein, _Bernstein, _Bernstein]
    lower: _Rational
    depth: int


def _signed_binary_ratio(value: object, label: str) -> tuple[int, int]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be a finite binary number")
    try:
        ratio = _binary_ratio(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be a finite binary number") from error
    denominator = ratio[1]
    if denominator & (denominator - 1):
        raise ValueError(f"{label} must be a finite binary number")
    return ratio


def _signed_positive_ratio(value: object, label: str) -> tuple[int, int]:
    ratio = _signed_binary_ratio(value, label)
    if ratio[0] <= 0:
        raise ValueError(f"{label} must be positive")
    return ratio


def _signed_read_input(
    kind: str,
    coordinates_si: Sequence[Sequence[float]],
    *,
    radius_si: float | None,
    height_si: float | None,
    dimensions_si: Sequence[float] | None,
    tolerance_si: float,
    max_subdivisions: int,
) -> _SignedInput:
    if not isinstance(kind, str) or kind not in {"sphere", "cylinder", "box"}:
        raise ValueError("signed primitive kind must be sphere, cylinder, or box")
    if isinstance(max_subdivisions, bool) or not isinstance(max_subdivisions, int):
        raise TypeError("max_subdivisions must be a positive integer")
    if max_subdivisions <= 0:
        raise ValueError("max_subdivisions must be a positive integer")

    try:
        if len(coordinates_si) != 6:
            raise ValueError("signed primitive requires six Tri6 points")
    except TypeError as error:
        raise ValueError("signed primitive requires six Tri6 points") from error

    coordinate_ratios: list[tuple[tuple[int, int], ...]] = []
    for point in coordinates_si:
        try:
            if len(point) != 3:
                raise ValueError("signed primitive requires three-component points")
        except TypeError as error:
            raise ValueError("signed primitive requires three-component points") from error
        coordinate_ratios.append(
            tuple(_signed_binary_ratio(value, "point coordinate") for value in point)
        )

    radius_ratio: tuple[int, int] | None = None
    height_ratio: tuple[int, int] | None = None
    dimension_ratios: tuple[tuple[int, int], ...] | None = None
    if kind == "sphere":
        if radius_si is None:
            raise ValueError("sphere radius is required")
        if height_si is not None or dimensions_si is not None:
            raise ValueError("sphere accepts only radius_si")
        radius_ratio = _signed_positive_ratio(radius_si, "sphere radius")
    elif kind == "cylinder":
        if radius_si is None or height_si is None:
            raise ValueError("cylinder radius and height are required")
        if dimensions_si is not None:
            raise ValueError("cylinder accepts radius_si and height_si only")
        radius_ratio = _signed_positive_ratio(radius_si, "cylinder radius")
        height_ratio = _signed_positive_ratio(height_si, "cylinder height")
    else:
        if radius_si is not None or height_si is not None:
            raise ValueError("box accepts dimensions_si only")
        if dimensions_si is None:
            raise ValueError("box dimensions are required")
        try:
            if len(dimensions_si) != 3:
                raise ValueError("box requires three dimensions")
        except TypeError as error:
            raise ValueError("box requires three dimensions") from error
        dimension_ratios = tuple(
            _signed_positive_ratio(value, "box dimension") for value in dimensions_si
        )

    tolerance_ratio = _signed_positive_ratio(tolerance_si, "tolerance_si")
    denominators = [denominator for point in coordinate_ratios for _, denominator in point]
    if radius_ratio is not None:
        denominators.append(radius_ratio[1])
    if height_ratio is not None:
        denominators.append(height_ratio[1])
    if dimension_ratios is not None:
        denominators.extend(denominator for _, denominator in dimension_ratios)
    scale = 1
    for denominator in denominators:
        scale = math.lcm(scale, denominator)

    exact_points: tuple[_Point, ...] = tuple(
        (
            point[0][0] * (scale // point[0][1]),
            point[1][0] * (scale // point[1][1]),
            point[2][0] * (scale // point[2][1]),
        )
        for point in coordinate_ratios
    )

    def exact_ratio(ratio: tuple[int, int]) -> _Rational:
        return _rational(ratio[0] * (scale // ratio[1]), scale)

    radius = exact_ratio(radius_ratio) if radius_ratio is not None else None
    half_height = (
        _q_mul(exact_ratio(height_ratio), _q_rational_half()) if height_ratio is not None else None
    )
    half_dimensions = (
        tuple(_q_mul(exact_ratio(ratio), _q_rational_half()) for ratio in dimension_ratios)
        if dimension_ratios is not None
        else None
    )
    return _SignedInput(
        points=exact_points,
        position_denominator=2 * scale,
        kind=kind,
        radius=radius,
        half_height=half_height,
        half_dimensions=half_dimensions,
        tolerance=_rational(*tolerance_ratio),
        work_limit=min(max_subdivisions, _SIGNED_MAX_WORK),
    )


def _signed_ratio_approximation(numerator: int, denominator: int) -> float:
    numerator_bits = numerator.bit_length()
    denominator_bits = denominator.bit_length()
    numerator_take = min(numerator_bits, 53)
    denominator_take = min(denominator_bits, 53)
    numerator_top = numerator >> (numerator_bits - numerator_take)
    denominator_top = denominator >> (denominator_bits - denominator_take)
    numerator_mantissa = math.ldexp(float(numerator_top), -numerator_take)
    denominator_mantissa = math.ldexp(float(denominator_top), -denominator_take)
    try:
        return math.ldexp(
            numerator_mantissa / denominator_mantissa,
            numerator_bits - denominator_bits,
        )
    except OverflowError:
        return math.inf


def _signed_positive_float_ratio(numerator: int, denominator: int, *, upward: bool) -> float:
    if numerator <= 0 or denominator <= 0:
        raise ValueError("internal positive float ratio must be positive")
    candidate = _signed_ratio_approximation(numerator, denominator)
    if not math.isfinite(candidate):
        candidate = sys.float_info.max
    if candidate == 0.0 and upward:
        candidate = math.nextafter(0.0, math.inf)
    for _ in range(64):
        candidate_numerator, candidate_denominator = candidate.as_integer_ratio()
        at_least = candidate_numerator * denominator >= numerator * candidate_denominator
        at_most = candidate_numerator * denominator <= numerator * candidate_denominator
        if upward and at_least:
            return candidate
        if not upward and at_most:
            return candidate
        next_candidate = math.nextafter(candidate, math.inf if upward else 0.0)
        if not math.isfinite(next_candidate) and upward:
            raise ValueError("signed primitive result is not representable")
        candidate = next_candidate
    raise ValueError("signed primitive result is not representable")


def _signed_float_down(value: _Rational) -> float:
    if value.numerator == 0:
        return 0.0
    if value.numerator > 0:
        return _signed_positive_float_ratio(value.numerator, value.denominator, upward=False)
    positive = _signed_positive_float_ratio(-value.numerator, value.denominator, upward=True)
    return -positive


def _signed_float_up(value: _Rational) -> float:
    if value.numerator == 0:
        return 0.0
    if value.numerator > 0:
        return _signed_positive_float_ratio(value.numerator, value.denominator, upward=True)
    positive = _signed_positive_float_ratio(-value.numerator, value.denominator, upward=False)
    return -positive


def _signed_sqrt_bound(value: _Rational, *, upward: bool) -> _Rational:
    if value.numerator < 0:
        raise ValueError("internal signed square-root ratio must be nonnegative")
    if value.numerator == 0:
        return _q_zero()

    # Keep the root exact as a rational enclosure until any primitive radius or
    # half-dimension has been subtracted.  Converting sqrt(value) to a float
    # first can overflow even when the final signed distance is representable.
    scale = _SIGNED_SQRT_SCALE
    scaled = value.numerator * value.denominator * scale * scale
    root = math.isqrt(scaled)
    if upward and root * root < scaled:
        root += 1
    return _rational(root, value.denominator * scale)


def _signed_abs(value: _Rational) -> _Rational:
    return value if value.numerator >= 0 else _q_neg(value)


def _signed_abs_interval(minimum: _Rational, maximum: _Rational) -> tuple[_Rational, _Rational]:
    if _q_less(maximum, _q_zero()):
        return _q_neg(maximum), _q_neg(minimum)
    if _q_less(_q_zero(), minimum):
        return minimum, maximum
    return _q_zero(), _q_max(_signed_abs(minimum), _signed_abs(maximum))


def _signed_feature_sdf_bound(values: tuple[_Rational, ...], *, upward: bool) -> _Rational:
    positives = tuple(_nonnegative(value) for value in values)
    squared = _q_zero()
    for value in positives:
        squared = _q_add(squared, _q_square(value))
    outside = _signed_sqrt_bound(squared, upward=upward)
    largest = values[0]
    for value in values[1:]:
        largest = _q_max(largest, value)
    inside = _q_min(largest, _q_zero())
    return _q_add(outside, inside)


def _signed_polynomial_value(value: _Bernstein, barycentric: tuple[_Rational, ...]) -> _Rational:
    result = _q_zero()
    for index, numerator in zip(_INDICES[value.degree], value.numerators, strict=True):
        term = _rational(numerator, value.denominator)
        for coordinate, exponent in zip(barycentric, index, strict=True):
            for _ in range(exponent):
                term = _q_mul(term, coordinate)
        term = _q_mul(term, _q_from_int(_MULTINOMIAL_BY_INDEX[value.degree][index]))
        result = _q_add(result, term)
    return result


_SIGNED_WITNESSES: tuple[tuple[_Rational, _Rational, _Rational], ...] = (
    (_Rational(1, 1), _Rational(0, 1), _Rational(0, 1)),
    (_Rational(0, 1), _Rational(1, 1), _Rational(0, 1)),
    (_Rational(0, 1), _Rational(0, 1), _Rational(1, 1)),
    (_Rational(1, 2), _Rational(1, 2), _Rational(0, 1)),
    (_Rational(0, 1), _Rational(1, 2), _Rational(1, 2)),
    (_Rational(1, 2), _Rational(0, 1), _Rational(1, 2)),
    (_Rational(1, 3), _Rational(1, 3), _Rational(1, 3)),
)


def _signed_cell_lower(
    position: tuple[_Bernstein, _Bernstein, _Bernstein], spec: _SignedInput
) -> _Rational:
    if spec.kind == "sphere":
        if spec.radius is None:
            raise ValueError("internal sphere radius is missing")
        minimum, _ = _enclosure(_squared_norm(position, include_z=True))
        return _q_sub(
            _signed_sqrt_bound(_nonnegative(minimum), upward=False),
            spec.radius,
        )
    if spec.kind == "cylinder":
        if spec.radius is None or spec.half_height is None:
            raise ValueError("internal cylinder dimensions are missing")
        radial_minimum, _ = _enclosure(_squared_norm(position, include_z=False))
        z_minimum, z_maximum = _enclosure(position[2])
        absolute_z_minimum, _ = _signed_abs_interval(z_minimum, z_maximum)
        radial_lower = _signed_sqrt_bound(_nonnegative(radial_minimum), upward=False)
        radial_difference = _q_sub(radial_lower, spec.radius)
        axial_difference = _q_sub(absolute_z_minimum, spec.half_height)
        return _signed_feature_sdf_bound((radial_difference, axial_difference), upward=False)
    if spec.half_dimensions is None:
        raise ValueError("internal box dimensions are missing")
    differences: list[_Rational] = []
    for coordinate, half_dimension in zip(position, spec.half_dimensions, strict=True):
        minimum, maximum = _enclosure(coordinate)
        absolute_minimum, _ = _signed_abs_interval(minimum, maximum)
        differences.append(_q_sub(absolute_minimum, half_dimension))
    return _signed_feature_sdf_bound(tuple(differences), upward=False)


def _signed_point_upper(
    position: tuple[_Bernstein, _Bernstein, _Bernstein],
    barycentric: tuple[_Rational, _Rational, _Rational],
    spec: _SignedInput,
) -> _Rational:
    point = tuple(_signed_polynomial_value(axis, barycentric) for axis in position)
    if spec.kind == "sphere":
        if spec.radius is None:
            raise ValueError("internal sphere radius is missing")
        squared = _q_zero()
        for coordinate in point:
            squared = _q_add(squared, _q_square(coordinate))
        return _q_sub(
            _signed_sqrt_bound(squared, upward=True),
            spec.radius,
        )
    if spec.kind == "cylinder":
        if spec.radius is None or spec.half_height is None:
            raise ValueError("internal cylinder dimensions are missing")
        radial_squared = _q_add(_q_square(point[0]), _q_square(point[1]))
        radial_upper = _signed_sqrt_bound(radial_squared, upward=True)
        radial_difference = _q_sub(radial_upper, spec.radius)
        axial_difference = _q_sub(_signed_abs(point[2]), spec.half_height)
        return _signed_feature_sdf_bound((radial_difference, axial_difference), upward=True)
    if spec.half_dimensions is None:
        raise ValueError("internal box dimensions are missing")
    differences = tuple(
        _q_sub(_signed_abs(coordinate), half_dimension)
        for coordinate, half_dimension in zip(point, spec.half_dimensions, strict=True)
    )
    return _signed_feature_sdf_bound(differences, upward=True)


def _signed_child_positions(
    position: tuple[_Bernstein, _Bernstein, _Bernstein],
) -> tuple[tuple[_Bernstein, _Bernstein, _Bernstein], ...]:
    children = tuple(_subdivide(axis) for axis in position)
    return tuple(
        (children[0][index], children[1][index], children[2][index])
        for index in range(len(children[0]))
    )


def _signed_rational_min(values: Sequence[_Rational]) -> _Rational:
    if not values:
        raise ValueError("internal rational minimum requires a value")
    result = values[0]
    for value in values[1:]:
        result = _q_min(result, value)
    return result


def primitive_face_minimum_signed_distance_interval(
    kind: str,
    points: Sequence[Sequence[float]],
    *,
    radius_si: float | None = None,
    height_si: float | None = None,
    dimensions_si: Sequence[float] | None = None,
    tolerance_si: float,
    max_subdivisions: int = 4096,
) -> tuple[float, float]:
    """Enclose the minimum signed distance of a complete canonical Tri6 patch.

    The lower endpoint is obtained from exact Bernstein enclosures and the
    monotone signed distance of the closed primitive.  The upper endpoint is
    the upward-rounded signed distance of an actual point on the patch.  The
    subdivision budget is finite; if the requested precision is not reached,
    the returned interval remains conservative and may be ambiguous.
    """
    spec = _signed_read_input(
        kind,
        points,
        radius_si=radius_si,
        height_si=height_si,
        dimensions_si=dimensions_si,
        tolerance_si=tolerance_si,
        max_subdivisions=max_subdivisions,
    )
    position = _position_polynomials(spec.points, spec.position_denominator)
    leaves = [_SignedCell(position, _signed_cell_lower(position, spec), 0)]
    upper: _Rational | None = None
    for barycentric in _SIGNED_WITNESSES:
        candidate = _signed_point_upper(position, barycentric, spec)
        upper = candidate if upper is None else _q_min(upper, candidate)
    if upper is None:
        raise ValueError("signed primitive has no finite point witness")

    work = 0
    while True:
        lower = _signed_rational_min(tuple(cell.lower for cell in leaves))
        width = _q_sub(upper, lower)
        if _q_less(width, spec.tolerance) or _q_equal(width, spec.tolerance):
            break
        if work >= spec.work_limit:
            break
        splittable = [index for index, cell in enumerate(leaves) if cell.depth < _SIGNED_MAX_DEPTH]
        if not splittable:
            break
        selected = splittable[0]
        for index in splittable[1:]:
            if _q_less(leaves[index].lower, leaves[selected].lower):
                selected = index
        cell = leaves.pop(selected)
        for child_position in _signed_child_positions(cell.position):
            child = _SignedCell(
                child_position,
                _signed_cell_lower(child_position, spec),
                cell.depth + 1,
            )
            leaves.append(child)
            for barycentric in _SIGNED_WITNESSES:
                candidate = _signed_point_upper(child_position, barycentric, spec)
                upper = _q_min(upper, candidate)
        work += 1

    lower = _signed_rational_min(tuple(cell.lower for cell in leaves))
    if spec.kind == "sphere":
        if spec.radius is None:
            raise ValueError("internal sphere radius is missing")
        safe_lower = _q_neg(spec.radius)
    elif spec.kind == "cylinder":
        if spec.radius is None or spec.half_height is None:
            raise ValueError("internal cylinder dimensions are missing")
        safe_lower = _q_neg(_q_min(spec.radius, spec.half_height))
    else:
        if spec.half_dimensions is None:
            raise ValueError("internal box dimensions are missing")
        safe_lower = _q_neg(_signed_rational_min(spec.half_dimensions))
    if _q_less(upper, lower):
        lower = safe_lower
    if _q_less(upper, lower):
        raise ValueError("signed primitive distance bounds are not representable")
    lower_float = _signed_float_down(lower)
    upper_float = _signed_float_up(upper)
    if not math.isfinite(lower_float) or not math.isfinite(upper_float):
        raise ValueError("signed primitive distance bounds are not representable")
    return lower_float, upper_float


__all__ = [
    "ALGORITHM",
    "primitive_face_distance_upper_bound",
    "primitive_face_minimum_signed_distance_interval",
]
