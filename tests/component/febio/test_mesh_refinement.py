"""Two successive refinements must converge over the declared evaluation states."""

import pytest


def test_refinement_checks_both_successive_changes_and_not_only_final_force() -> None:
    from febio_cae.application._mesh_refinement import _refinement_error

    curves = ((0.0, 10.0, 20.0), (0.0, 15.0, 20.0), (0.0, 15.1, 20.0))
    assert _refinement_error(curves, 0.1) == pytest.approx(1 / 3)


def test_refinement_does_not_treat_unloaded_or_missing_states_as_convergence() -> None:
    from febio_cae.application._mesh_refinement import _refinement_error

    with pytest.raises(ValueError):
        _refinement_error(((0.0, 0.0),) * 3, 0.1)
    with pytest.raises(ValueError):
        _refinement_error(((0.0, 1.0), (1.0,), (0.0, 1.0)), 0.1)
