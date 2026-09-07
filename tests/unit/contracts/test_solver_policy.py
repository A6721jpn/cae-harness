from __future__ import annotations

import importlib
from dataclasses import FrozenInstanceError
from types import ModuleType
from typing import Any

import pytest

from febio_cae.domain import NumericalProfileRef, Quantity, canonical_bytes


def _optional_module(name: str) -> ModuleType | None:
    try:
        return importlib.import_module(name)
    except (ImportError, ModuleNotFoundError):
        return None


SOLVER_MODULE = _optional_module("febio_cae.domain.solver_policy")
_MISSING = object()


def _solver() -> ModuleType:
    if SOLVER_MODULE is None:
        pytest.skip("solver policy API availability is covered by the dedicated assertion")
    return SOLVER_MODULE


def _profile(
    *,
    profile_id: str = "synthetic-solver-profile",
    purpose: str = "solver",
    record_digest: str = "a" * 64,
) -> NumericalProfileRef:
    return NumericalProfileRef(
        profile_id=profile_id,
        purpose=purpose,
        record_digest=record_digest,
    )


def _control(
    solver: ModuleType,
    *,
    name: object = "control_alpha",
    value: object = Quantity(1, "1"),
) -> Any:
    return solver.SolverControl(name=name, value=value)


def _increments(
    solver: ModuleType,
    *,
    initial_step: object = Quantity(1, "s"),
    minimum_step: object = Quantity(0.1, "s"),
    maximum_step: object = Quantity(2, "s"),
    adaptive: object = False,
    max_steps: object = 10,
    max_step_retries: object = 0,
    must_points: object = _MISSING,
) -> Any:
    return solver.TimeIncrementPolicy(
        initial_step=initial_step,
        minimum_step=minimum_step,
        maximum_step=maximum_step,
        adaptive=adaptive,
        max_steps=max_steps,
        max_step_retries=max_step_retries,
        must_points=() if must_points is _MISSING else must_points,
    )


def _policy(
    solver: ModuleType,
    *,
    profile: object = _MISSING,
    controls: object = _MISSING,
    increments: object = _MISSING,
    retry_recipe_ids: object = _MISSING,
) -> Any:
    return solver.SolverPolicy(
        profile=_profile() if profile is _MISSING else profile,
        controls=([_control(solver)] if controls is _MISSING else controls),
        increments=(_increments(solver) if increments is _MISSING else increments),
        retry_recipe_ids=(() if retry_recipe_ids is _MISSING else retry_recipe_ids),
    )


def test_solver_policy_api_is_available() -> None:
    assert SOLVER_MODULE is not None, "P1-B9 solver policy module is not available"
    for name in (
        "SCHEMA_VERSION",
        "SolverControl",
        "TimeIncrementPolicy",
        "SolverPolicy",
        "SolverPolicyValidationError",
    ):
        assert getattr(SOLVER_MODULE, name, None) is not None, name
    assert SOLVER_MODULE.__all__ == [
        "SCHEMA_VERSION",
        "SolverControl",
        "SolverPolicy",
        "SolverPolicyValidationError",
        "TimeIncrementPolicy",
    ]


def test_solver_control_projects_all_value_kinds_without_collisions() -> None:
    solver = _solver()
    quantity = _control(solver, name="control_quantity", value=Quantity(1000, "ms"))
    integer = _control(solver, name="control_integer", value=1)
    boolean = _control(solver, name="control_boolean", value=True)

    assert quantity.to_dict() == {
        "schema_version": "1",
        "name": "control_quantity",
        "value": {"kind": "quantity", "value": {"value": 1.0, "unit": "s"}},
    }
    assert integer.to_dict()["value"] == {"kind": "integer", "value": 1}
    assert boolean.to_dict()["value"] == {"kind": "boolean", "value": True}
    assert quantity.to_bytes() == canonical_bytes(quantity.to_dict())
    assert len({quantity.to_bytes(), integer.to_bytes(), boolean.to_bytes()}) == 3


def test_solver_control_preserves_signed_zero_and_large_integer_identity() -> None:
    solver = _solver()
    negative = _control(solver, name="control_negative", value=-7)
    zero = _control(solver, name="control_zero", value=0)
    large = 10**1000
    huge = _control(solver, name="control_large", value=large)

    assert negative.to_dict()["value"] == {"kind": "integer", "value": -7}
    assert zero.to_dict()["value"] == {"kind": "integer", "value": 0}
    assert huge.to_dict()["value"] == {"kind": "integer", "value": large}
    assert huge.to_bytes() == _control(solver, name="control_large", value=large).to_bytes()


@pytest.mark.parametrize("bad_value", [1.0, "1", None, [], object()])
def test_solver_control_rejects_non_quantity_non_integer_non_boolean_values(
    bad_value: object,
) -> None:
    solver = _solver()
    with pytest.raises(solver.SolverPolicyValidationError, match="value"):
        _control(solver, value=bad_value)


@pytest.mark.parametrize(
    "bad_name",
    ["", " control", "control ", "1control", "control-name", "control.name", "制御", "\ud800"],
)
def test_solver_control_requires_ascii_identifier_names(bad_name: str) -> None:
    solver = _solver()
    with pytest.raises(solver.SolverPolicyValidationError, match="name.*identifier"):
        _control(solver, name=bad_name)


def test_solver_control_requires_all_fields_and_rejects_unknown_fields() -> None:
    solver = _solver()
    with pytest.raises(TypeError):
        solver.SolverControl(value=1)
    with pytest.raises(TypeError):
        solver.SolverControl(name="control_alpha")
    with pytest.raises(TypeError):
        solver.SolverControl(name="control_alpha", value=1, unexpected=True)


def test_solver_control_is_immutable_and_projection_isolated() -> None:
    solver = _solver()
    value = _control(solver)
    payload = value.to_dict()
    payload["name"] = "mutated"
    payload["value"]["value"] = {"kind": "integer", "value": 99}

    with pytest.raises(FrozenInstanceError):
        value.name = "mutated"
    assert value.name == "control_alpha"
    assert value.to_dict()["value"] == {
        "kind": "quantity",
        "value": {"value": 1.0, "unit": "1"},
    }


@pytest.mark.parametrize(
    "bad_quantity",
    [Quantity(10**400, "s"), Quantity(5e-324, "ms")],
    ids=["overflow", "underflow"],
)
def test_solver_control_rejects_unrepresentable_quantity_at_construction(
    bad_quantity: Quantity,
) -> None:
    solver = _solver()
    with pytest.raises(
        solver.SolverPolicyValidationError,
        match="solver control projection is not canonically serializable",
    ):
        _control(solver, value=bad_quantity)


def test_solver_control_rejects_integer_beyond_shared_json_serialization_range() -> None:
    solver = _solver()
    with pytest.raises(
        solver.SolverPolicyValidationError,
        match="solver control projection is not canonically serializable",
    ):
        _control(solver, value=10**5000)


def test_time_increment_projects_explicit_bounds_and_empty_points() -> None:
    solver = _solver()
    value = _increments(
        solver,
        initial_step=Quantity(1000, "ms"),
        minimum_step=Quantity(100, "ms"),
        maximum_step=Quantity(2, "s"),
        adaptive=False,
        max_steps=10,
        max_step_retries=0,
        must_points=[],
    )

    assert value.to_dict() == {
        "schema_version": "1",
        "initial_step": {"value": 1.0, "unit": "s"},
        "minimum_step": {"value": 0.1, "unit": "s"},
        "maximum_step": {"value": 2.0, "unit": "s"},
        "adaptive": False,
        "max_steps": 10,
        "max_step_retries": 0,
        "must_points": [],
    }
    assert value.must_points == ()
    assert value.to_bytes() == canonical_bytes(value.to_dict())


def test_time_increment_retains_adaptive_and_explicit_zero_retry_values() -> None:
    solver = _solver()
    adaptive = _increments(solver, adaptive=True, max_step_retries=0)
    fixed = _increments(solver, adaptive=False, max_step_retries=0)

    assert adaptive.adaptive is True
    assert adaptive.max_step_retries == 0
    assert adaptive.to_bytes() != fixed.to_bytes()


def test_time_increment_requires_all_fields_and_rejects_unknown_fields() -> None:
    solver = _solver()
    values = {
        "initial_step": Quantity(1, "s"),
        "minimum_step": Quantity(0.1, "s"),
        "maximum_step": Quantity(2, "s"),
        "adaptive": False,
        "max_steps": 10,
        "max_step_retries": 0,
        "must_points": (),
    }
    for missing in tuple(values):
        incomplete = values.copy()
        incomplete.pop(missing)
        with pytest.raises(TypeError):
            solver.TimeIncrementPolicy(**incomplete)
    with pytest.raises(TypeError):
        solver.TimeIncrementPolicy(**values, unexpected=True)


@pytest.mark.parametrize(
    "field",
    ["initial_step", "minimum_step", "maximum_step"],
)
@pytest.mark.parametrize(
    "bad_value",
    [Quantity(0, "s"), Quantity(-1, "s"), Quantity(1, "mm"), "1 s", None],
    ids=["zero", "negative", "length", "string", "none"],
)
def test_time_increment_requires_strictly_positive_time_steps(
    field: str,
    bad_value: object,
) -> None:
    solver = _solver()
    with pytest.raises(solver.SolverPolicyValidationError, match=field):
        _increments(solver, **{field: bad_value})


@pytest.mark.parametrize("bad_value", [0, 1, "false", None])
def test_time_increment_requires_strict_boolean_adaptive(bad_value: object) -> None:
    solver = _solver()
    with pytest.raises(solver.SolverPolicyValidationError, match="adaptive"):
        _increments(solver, adaptive=bad_value)


@pytest.mark.parametrize("field", ["max_steps", "max_step_retries"])
@pytest.mark.parametrize("bad_value", [True, False, 1.0, "1", None])
def test_time_increment_counts_are_strict_integers(field: str, bad_value: object) -> None:
    solver = _solver()
    with pytest.raises(solver.SolverPolicyValidationError, match=field):
        _increments(solver, **{field: bad_value})


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("max_steps", 0),
        ("max_steps", -1),
        ("max_step_retries", -1),
    ],
)
def test_time_increment_counts_have_explicit_nonnegative_bounds(
    field: str,
    bad_value: int,
) -> None:
    solver = _solver()
    with pytest.raises(solver.SolverPolicyValidationError, match=field):
        _increments(solver, **{field: bad_value})


@pytest.mark.parametrize(
    ("minimum_step", "initial_step", "maximum_step"),
    [
        (Quantity(2, "s"), Quantity(1, "s"), Quantity(3, "s")),
        (Quantity(1, "s"), Quantity(3, "s"), Quantity(2, "s")),
    ],
    ids=["minimum-after-initial", "initial-after-maximum"],
)
def test_time_increment_enforces_si_ordering(
    minimum_step: Quantity,
    initial_step: Quantity,
    maximum_step: Quantity,
) -> None:
    solver = _solver()
    with pytest.raises(solver.SolverPolicyValidationError, match="minimum_step|maximum_step"):
        _increments(
            solver,
            minimum_step=minimum_step,
            initial_step=initial_step,
            maximum_step=maximum_step,
        )


@pytest.mark.parametrize(
    "must_points",
    [
        [Quantity(-1, "s")],
        [Quantity(1, "mm")],
        [Quantity(1, "s"), Quantity(1, "s")],
        [Quantity(2, "s"), Quantity(1, "s")],
        [Quantity(1, "s"), Quantity(1000, "ms")],
        [Quantity(1, "s"), "2 s"],
        [Quantity(1, "s"), None],
    ],
    ids=[
        "negative",
        "wrong-dimension",
        "duplicate",
        "decreasing",
        "equivalent-duplicate",
        "string",
        "none",
    ],
)
def test_time_increment_rejects_invalid_must_points(must_points: list[object]) -> None:
    solver = _solver()
    with pytest.raises(solver.SolverPolicyValidationError, match="must_points"):
        _increments(solver, must_points=must_points)


@pytest.mark.parametrize(
    "bad_quantity",
    [Quantity(10**400, "s"), Quantity(5e-324, "ms")],
    ids=["overflow", "underflow"],
)
def test_time_increment_rejects_unrepresentable_must_points_at_construction(
    bad_quantity: Quantity,
) -> None:
    solver = _solver()
    with pytest.raises(
        solver.SolverPolicyValidationError,
        match="must_points.*SI-representable",
    ):
        _increments(solver, must_points=[bad_quantity])


def test_time_increment_preserves_point_sequence_and_copies_input() -> None:
    solver = _solver()
    points = [Quantity(1, "s"), Quantity(2000, "ms")]
    value = _increments(solver, must_points=points)
    points.clear()

    assert value.must_points == (Quantity(1, "s"), Quantity(2000, "ms"))
    assert value.to_dict()["must_points"] == [
        {"value": 1.0, "unit": "s"},
        {"value": 2.0, "unit": "s"},
    ]


def test_time_increment_equivalent_si_values_have_identical_canonical_bytes() -> None:
    solver = _solver()
    displayed = _increments(
        solver,
        initial_step=Quantity(1000, "ms"),
        minimum_step=Quantity(100, "ms"),
        maximum_step=Quantity(2, "s"),
        must_points=[Quantity(1000, "ms"), Quantity(2000, "ms")],
    )
    si = _increments(
        solver,
        initial_step=Quantity(1, "s"),
        minimum_step=Quantity(0.1, "s"),
        maximum_step=Quantity(2, "s"),
        must_points=[Quantity(1, "s"), Quantity(2, "s")],
    )

    assert displayed.to_bytes() == si.to_bytes()


def test_time_increment_accepts_large_but_serializable_count_identity() -> None:
    solver = _solver()
    large = 10**1000
    value = _increments(solver, max_steps=large, max_step_retries=large)

    assert value.max_steps == large
    assert value.max_step_retries == large
    assert (
        value.to_bytes()
        == _increments(
            solver,
            max_steps=large,
            max_step_retries=large,
        ).to_bytes()
    )


def test_time_increment_rejects_integer_beyond_shared_json_serialization_range() -> None:
    solver = _solver()
    with pytest.raises(
        solver.SolverPolicyValidationError,
        match="time increment policy projection is not canonically serializable",
    ):
        _increments(solver, max_steps=10**5000)


@pytest.mark.parametrize("field", ["max_steps", "max_step_retries"])
def test_time_increment_rejects_unrepresentable_count_with_field_context(field: str) -> None:
    solver = _solver()
    valid = _increments(solver)
    values = {
        "initial_step": valid.initial_step,
        "minimum_step": valid.minimum_step,
        "maximum_step": valid.maximum_step,
        "adaptive": valid.adaptive,
        "max_steps": valid.max_steps,
        "max_step_retries": valid.max_step_retries,
        "must_points": valid.must_points,
    }
    values[field] = 10**5000

    with pytest.raises(solver.SolverPolicyValidationError, match=field):
        solver.TimeIncrementPolicy(**values)


def test_time_increment_is_immutable_and_projection_isolated() -> None:
    solver = _solver()
    value = _increments(solver, must_points=[Quantity(1, "s")])
    payload = value.to_dict()
    payload["adaptive"] = True
    payload["must_points"].append({"value": 9.0, "unit": "s"})

    with pytest.raises(FrozenInstanceError):
        value.adaptive = True
    assert value.adaptive is False
    assert value.must_points == (Quantity(1, "s"),)


def test_solver_policy_projects_sorted_controls_and_ordered_retry_recipes() -> None:
    solver = _solver()
    controls = [
        _control(solver, name="control_beta", value=2),
        _control(solver, name="control_alpha", value=True),
    ]
    value = _policy(
        solver,
        controls=controls,
        retry_recipe_ids=["recipe_b", "recipe_b", "recipe_a"],
    )

    assert [control.name for control in value.controls] == ["control_alpha", "control_beta"]
    assert value.retry_recipe_ids == ("recipe_b", "recipe_b", "recipe_a")
    assert value.to_dict()["retry_recipe_ids"] == ["recipe_b", "recipe_b", "recipe_a"]
    assert value.to_bytes() == canonical_bytes(value.to_dict())


def test_solver_policy_requires_solver_profile_and_typed_increments() -> None:
    solver = _solver()
    with pytest.raises(solver.SolverPolicyValidationError, match="profile"):
        _policy(solver, profile=_profile(purpose="mesh_quality"))
    with pytest.raises(solver.SolverPolicyValidationError, match="profile"):
        _policy(solver, profile=object())
    with pytest.raises(solver.SolverPolicyValidationError, match="increments"):
        _policy(solver, increments=object())


def test_solver_policy_requires_all_fields_and_rejects_unknown_fields() -> None:
    solver = _solver()
    values = {
        "profile": _profile(),
        "controls": [_control(solver)],
        "increments": _increments(solver),
        "retry_recipe_ids": (),
    }
    for missing in tuple(values):
        incomplete = values.copy()
        incomplete.pop(missing)
        with pytest.raises(TypeError):
            solver.SolverPolicy(**incomplete)
    with pytest.raises(TypeError):
        solver.SolverPolicy(**values, unexpected=True)


@pytest.mark.parametrize("bad_controls", [[], (), "control_alpha", None, [object()]])
def test_solver_policy_requires_nonempty_sequence_of_controls(bad_controls: object) -> None:
    solver = _solver()
    with pytest.raises(solver.SolverPolicyValidationError, match="controls"):
        _policy(solver, controls=bad_controls)


def test_solver_policy_rejects_duplicate_control_names() -> None:
    solver = _solver()
    controls = [
        _control(solver, name="control_alpha", value=1),
        _control(solver, name="control_alpha", value=2),
    ]
    with pytest.raises(solver.SolverPolicyValidationError, match="duplicate.*control"):
        _policy(solver, controls=controls)


def test_solver_policy_control_permutations_have_identical_canonical_bytes() -> None:
    solver = _solver()
    alpha = _control(solver, name="control_alpha", value=1)
    beta = _control(solver, name="control_beta", value=Quantity(1000, "ms"))

    first = _policy(solver, controls=[alpha, beta])
    second = _policy(solver, controls=[beta, alpha])

    assert first.controls == (alpha, beta)
    assert second.controls == (beta, alpha) or second.controls == (alpha, beta)
    assert first.to_bytes() == second.to_bytes()


def test_solver_policy_accepts_empty_and_repeated_ordered_retry_recipe_ids() -> None:
    solver = _solver()
    empty = _policy(solver, retry_recipe_ids=[])
    repeated = _policy(solver, retry_recipe_ids=["recipe_a", "recipe_a"])

    assert empty.retry_recipe_ids == ()
    assert repeated.retry_recipe_ids == ("recipe_a", "recipe_a")
    assert repeated.to_dict()["retry_recipe_ids"] == ["recipe_a", "recipe_a"]


@pytest.mark.parametrize(
    "bad_ids",
    ["recipe_a", None, ["recipe-a"], ["1recipe"], ["recipe.a"], ["recipe", 1]],
)
def test_solver_policy_requires_identifier_retry_recipe_ids(bad_ids: object) -> None:
    solver = _solver()
    with pytest.raises(solver.SolverPolicyValidationError, match="retry_recipe_ids"):
        _policy(solver, retry_recipe_ids=bad_ids)


def test_solver_policy_copies_inputs_and_is_immutable() -> None:
    solver = _solver()
    controls = [_control(solver, name="control_alpha")]
    retry_recipe_ids = ["recipe_a"]
    value = _policy(solver, controls=controls, retry_recipe_ids=retry_recipe_ids)
    controls.clear()
    retry_recipe_ids.append("recipe_b")
    payload = value.to_dict()
    payload["controls"].clear()
    payload["retry_recipe_ids"].append("recipe_c")

    with pytest.raises(FrozenInstanceError):
        value.profile = _profile()
    assert len(value.controls) == 1
    assert value.retry_recipe_ids == ("recipe_a",)
    assert value.to_dict()["controls"]


def test_solver_policy_identity_changes_for_control_profile_increment_and_retry_data() -> None:
    solver = _solver()
    baseline = _policy(solver)
    changed_control = _policy(
        solver,
        controls=[_control(solver, value=2)],
    )
    changed_profile = _policy(solver, profile=_profile(record_digest="b" * 64))
    changed_increment = _policy(
        solver,
        increments=_increments(solver, max_steps=11),
    )
    changed_retry = _policy(solver, retry_recipe_ids=["recipe_a"])

    assert changed_control.to_bytes() != baseline.to_bytes()
    assert changed_profile.to_bytes() != baseline.to_bytes()
    assert changed_increment.to_bytes() != baseline.to_bytes()
    assert changed_retry.to_bytes() != baseline.to_bytes()
