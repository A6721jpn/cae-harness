"""Two successive refinements must converge over the declared evaluation states."""

from typing import Any

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


def _admission_criteria(*metric_ids: str) -> tuple[Any, ...]:
    from dataclasses import replace

    from febio_cae.domain import QualityThreshold, Quantity

    from .fixtures import evidence, make_revision

    base = make_revision().spec.quality_policy.criteria[0]
    criteria = []
    for index, metric_id in enumerate(metric_ids):
        criterion_id = base.criterion_id if index == 0 else f"{base.criterion_id}_{index}"
        criteria.append(
            replace(
                base,
                criterion_id=criterion_id,
                metric_id=metric_id,
                thresholds=tuple(
                    QualityThreshold(name, quantity)
                    for name, quantity in (
                        ("coarse_size", Quantity(3, "mm")),
                        ("refined_size", Quantity(2, "mm")),
                        ("fine_size", Quantity(1, "mm")),
                        ("relative_max", Quantity(0.1, "1")),
                        ("absolute_floor", Quantity(0.01, "N")),
                    )
                ),
                evidence=evidence(f"quality_policy.criteria.{criterion_id}", criterion_id),
            )
        )
    return tuple(criteria)


def _admission_policy(
    size_mm: float,
    *,
    local: bool,
    region: Any = "present",
    center_x_mm: float = 0.0,
    max_refinements: int = 2,
) -> Any:
    from dataclasses import replace

    from .fixtures import make_revision

    from febio_cae.domain import (
        LocalRefinement,
        MeshPolicy,
        Point3,
        Quantity,
        SourceLocalRefinementBall,
    )

    spec = make_revision().spec
    if not local:
        return MeshPolicy(
            "tet10",
            Quantity(size_mm, "mm"),
            (),
            spec.mesh_policy.quality_profile,
            max_refinements,
        )
    selection = replace(spec.contact.part_surface, role="mesh_refinement")
    source_frame = spec.geometry.placement.source_frame
    ball = (
        None
        if region is None
        else SourceLocalRefinementBall(
            Point3(
                source_frame,
                Quantity(center_x_mm, "mm"),
                Quantity(0, "mm"),
                Quantity(0, "mm"),
            ),
            Quantity(2, "mm"),
        )
    )
    refinement = LocalRefinement(
        "part-ball",
        selection,
        Quantity(size_mm, "mm"),
        ball,
    )
    return MeshPolicy(
        "tet10",
        Quantity(5, "mm"),
        (refinement,),
        spec.mesh_policy.quality_profile,
        max_refinements,
    )


def test_next_global_refinement_is_adjacent_and_preserves_local_policy() -> None:
    from febio_cae.application._mesh_refinement import validate_next_refinement

    validate_next_refinement(
        _admission_policy(3, local=False),
        _admission_policy(2, local=False),
        _admission_criteria("mesh_dependence"),
    )

    with pytest.raises(ValueError, match="next"):
        validate_next_refinement(
            _admission_policy(3, local=False),
            _admission_policy(1, local=False),
            _admission_criteria("mesh_dependence"),
        )


@pytest.mark.parametrize(
    ("parent", "requested", "criteria"),
    [
        (
            _admission_policy(3, local=True),
            _admission_policy(1, local=True),
            _admission_criteria("source_local_mesh_dependence"),
        ),
        (
            _admission_policy(3, local=True),
            _admission_policy(2, local=True, region=None),
            _admission_criteria("source_local_mesh_dependence"),
        ),
        (
            _admission_policy(3, local=True),
            _admission_policy(2, local=True, center_x_mm=0.5),
            _admission_criteria("source_local_mesh_dependence"),
        ),
        (
            _admission_policy(3, local=False),
            _admission_policy(2, local=False),
            _admission_criteria("mesh_dependence", "source_local_mesh_dependence"),
        ),
    ],
)
def test_invalid_local_refinement_scope_is_rejected(
    parent: Any, requested: Any, criteria: tuple[Any, ...]
) -> None:
    from febio_cae.application._mesh_refinement import validate_next_refinement

    with pytest.raises(ValueError):
        validate_next_refinement(parent, requested, criteria)


def test_local_admission_rejects_undeclared_target_and_policy_mutation() -> None:
    from dataclasses import replace

    from febio_cae.application._mesh_refinement import validate_next_refinement

    parent = _admission_policy(3, local=True)
    requested = _admission_policy(2, local=True)
    validate_next_refinement(
        parent,
        requested,
        _admission_criteria("source_local_mesh_dependence"),
    )

    with pytest.raises(ValueError):
        validate_next_refinement(
            parent,
            _admission_policy(2.5, local=True),
            _admission_criteria("source_local_mesh_dependence"),
        )
    with pytest.raises(ValueError):
        validate_next_refinement(
            parent,
            replace(requested, max_refinements=1),
            _admission_criteria("source_local_mesh_dependence"),
        )


def test_local_admission_rejects_material_transfer_threshold() -> None:
    from dataclasses import replace

    from febio_cae.application._mesh_refinement import validate_next_refinement
    from febio_cae.domain import QualityThreshold, Quantity

    criterion = _admission_criteria("source_local_mesh_dependence")[0]
    criterion = replace(
        criterion,
        thresholds=(
            *criterion.thresholds,
            QualityThreshold("material_scaling_relative_max", Quantity(0.1, "1")),
        ),
    )
    with pytest.raises(ValueError, match="material scaling"):
        validate_next_refinement(
            _admission_policy(3, local=True),
            _admission_policy(2, local=True),
            (criterion,),
        )


def _local_revision(size_mm: float) -> Any:
    from dataclasses import replace

    from .fixtures import make_revision

    revision = make_revision()
    return replace(
        revision,
        spec=replace(revision.spec, mesh_policy=_admission_policy(size_mm, local=True)),
    )


def _local_mesh(revision: Any, local_elements: int, local_size_m: float) -> Any:
    from febio_cae.domain import MeshArtifact, MeshElement, MeshNode, MeshProvenance

    body = revision.spec.geometry.body_id.value
    nodes: list[MeshNode] = []
    elements: list[MeshElement] = []

    def add_tet(element_id: int, origin: tuple[float, float, float], size: float) -> None:
        base = len(nodes) + 1
        corners = (
            (origin[0], origin[1], origin[2]),
            (origin[0] + size, origin[1], origin[2]),
            (origin[0], origin[1] + size, origin[2]),
            (origin[0], origin[1], origin[2] + size),
        )
        edge_positions = ((0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3))
        mids = tuple(
            tuple((corners[left][axis] + corners[right][axis]) / 2 for axis in range(3))
            for left, right in edge_positions
        )
        nodes.extend(
            MeshNode(base + index, point) for index, point in enumerate((*corners, *mids))
        )
        elements.append(MeshElement(element_id, "tet10", tuple(range(base, base + 10)), body))

    for index in range(local_elements):
        add_tet(index + 1, (0.0, 0.0, 0.0), local_size_m)
    # This deliberately unchanged far-field element is outside the declared ball.
    add_tet(local_elements + 1, (0.01, 0.0, 0.0), 0.004)
    return MeshArtifact(
        f"local-{local_elements}-{local_size_m}",
        revision.spec.geometry.placement.target_frame,
        MeshProvenance(
            revision.spec.geometry.geometry_digest,
            (body,),
            (),
            "9" * 64,
            "synthetic-local-fixture",
            "1",
            "tet10-canonical-v1",
            "tet10-canonical-v1",
            "tet10-face-canonical-v1",
        ),
        tuple(nodes),
        tuple(elements),
        (),
        (),
        (),
    )


def test_local_measurement_counts_in_ball_edges_and_ignores_unchanged_far_field() -> None:
    from febio_cae.application._mesh_refinement import _geometry, _local_mesh_measurements

    revisions = tuple(_local_revision(size) for size in (3, 2, 1))
    meshes = tuple(
        _local_mesh(revision, count, size)
        for revision, count, size in zip(
            revisions, (1, 2, 3), (0.001, 0.0005, 0.00025), strict=True
        )
    )
    measurements = tuple(
        _local_mesh_measurements(revision, mesh)
        for revision, mesh in zip(revisions, meshes, strict=True)
    )
    observations = tuple(
        measurement.balls["part-ball"] for measurement in measurements
    )
    assert tuple(item.corner_edge_count for item in observations) == (6, 12, 18)
    assert (
        observations[0].maximum_edge_m
        > observations[1].maximum_edge_m
        > observations[2].maximum_edge_m
    )
    assert tuple(measurement.body_element_counts["part-body"] for measurement in measurements) == (
        2,
        3,
        4,
    )
    assert _geometry(meshes[0])[2] == _geometry(meshes[1])[2] == _geometry(meshes[2])[2]


def test_local_measurement_missing_in_ball_edges_is_unverified_input() -> None:
    from dataclasses import replace

    from febio_cae.application._mesh_refinement import _local_mesh_measurements

    revision = _local_revision(1)
    mesh = _local_mesh(revision, 1, 0.001)
    far_nodes = tuple(
        replace(node, coordinates_si=(node.coordinates_si[0] + 0.01, *node.coordinates_si[1:]))
        for node in mesh.nodes
    )
    far_mesh = replace(mesh, nodes=far_nodes)
    with pytest.raises(ValueError, match="fewer than three"):
        _local_mesh_measurements(revision, far_mesh)


def test_local_mesh_index_deduplicates_edges_before_ball_membership() -> None:
    from dataclasses import replace

    from febio_cae.application._mesh_refinement import _local_mesh_index

    revision = _local_revision(1)
    mesh = _local_mesh(revision, 1, 0.001)
    duplicate = replace(mesh.elements[-1], element_id=mesh.elements[-1].element_id + 1)
    shared_mesh = replace(mesh, elements=(*mesh.elements, duplicate))

    index = _local_mesh_index(shared_mesh, {"part-body"})

    assert index.body_element_counts == {"part-body": 3}
    assert len(index.edges_by_body["part-body"]) == 12
