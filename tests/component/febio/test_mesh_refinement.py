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


def test_geometry_signature_ignores_order_but_detects_changed_node_positions() -> None:
    from dataclasses import replace

    from febio_cae.application._mesh_refinement import _geometry

    from .fixtures import make_mesh, make_revision

    mesh = make_mesh(make_revision().spec)
    original = _geometry(mesh)
    reordered = replace(
        mesh, nodes=tuple(reversed(mesh.nodes)), elements=tuple(reversed(mesh.elements))
    )
    assert _geometry(reordered) == original

    node = mesh.nodes[0]
    moved = replace(
        mesh,
        nodes=(
            replace(node, coordinates_si=(node.coordinates_si[0], 0.0001, node.coordinates_si[2])),
            *mesh.nodes[1:],
        ),
    )
    assert _geometry(moved)[0] != original[0]
