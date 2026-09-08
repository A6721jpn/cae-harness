from __future__ import annotations

import json
import math
from dataclasses import replace
from typing import Any

import pytest

from febio_cae.adapters.geometry import BackendFace
from febio_cae.domain import (
    CoordinatePredicate,
    CoordinatePredicateRule,
    FaceId,
    FaceSetRule,
    FrameId,
    GeometryInspectionRequest,
    MeshArtifact,
    PortError,
    PortErrorCategory,
    ProperRotation,
    Quantity,
    SpecifiedGap,
    Translation3,
    UnitDirection,
)
from febio_cae.domain.codec import decode_record, encode_record

from .conftest import _evidence


@pytest.fixture
def connected(
    adapter: Any,
    synthetic_backend: Any,
    synthetic_case_revision: Any,
    source_content: Any,
    monkeypatch: Any,
) -> tuple[Any, Any, Any]:
    original = synthetic_backend.inspect
    points = ((0.0, 0.0, 0.0), (0.01, 0.0, 0.0), (0.0, 0.01, 0.0), (0.0, 0.0, 0.01))
    triangles = ((0, 2, 1), (0, 1, 3), (1, 2, 3), (0, 3, 2))
    names = ("bottom-face", "side-face", "top-face", "other-face")

    def inspect(*args: Any) -> Any:
        report = original(*args)
        faces = []
        for name, ids in zip(names, triangles, strict=True):
            vertices = tuple(points[i] for i in ids)
            area = 0.00005 * (math.sqrt(3) if name == "top-face" else 1)
            faces.append(
                BackendFace(
                    name,
                    "part-body",
                    FrameId("World"),
                    area,
                    tuple(sum(p[j] for p in vertices) / 3 for j in range(3)),
                    boundary_points_si=vertices,
                    attributes=("planar-triangle-v1",),
                )
            )
        return replace(report, bodies=(replace(report.bodies[0], faces=tuple(faces)),))

    monkeypatch.setattr(synthetic_backend, "inspect", inspect)
    inspection = adapter.inspect(
        GeometryInspectionRequest(source_content.source_asset), source_content
    )
    revision = replace(
        synthetic_case_revision,
        spec=replace(
            synthetic_case_revision.spec,
            geometry=replace(
                synthetic_case_revision.spec.geometry,
                inspection_digest=inspection.inspection_digest,
            ),
        ),
    )
    return adapter, synthetic_backend, revision


def test_each_source_face_requires_explicit_coverage(connected: Any, monkeypatch: Any) -> None:
    adapter, backend, revision = connected
    original = backend.mesh

    def missing(*args: Any) -> Any:
        mesh = original(*args)
        return replace(
            mesh, faces=(replace(mesh.faces[0], source_face_id="unrelated"), *mesh.faces[1:])
        )

    monkeypatch.setattr(backend, "mesh", missing)
    with pytest.raises(PortError, match="covered") as error:
        adapter.mesh(revision)
    assert error.value.category == PortErrorCategory.INTEGRITY


def test_rotated_translated_selection_and_mesh_share_frame(connected: Any) -> None:
    adapter, _, revision = connected
    spec = revision.spec
    placement = replace(
        spec.geometry.placement,
        rotation=ProperRotation(((0.0, 0.0, 1.0), (0.0, 1.0, 0.0), (-1.0, 0.0, 0.0))),
        translation=Translation3(
            FrameId("World"), Quantity(1, "m"), Quantity(2, "m"), Quantity(3, "m")
        ),
    )
    selection = replace(
        spec.contact.part_surface,
        rule=CoordinatePredicateRule(
            spec.geometry.body_id,
            FrameId("World"),
            (
                CoordinatePredicate(
                    UnitDirection(FrameId("World"), 1.0, 0.0, 0.0), "gte", Quantity(0.9, "m")
                ),
            ),
        ),
    )
    spec = replace(
        spec,
        geometry=replace(spec.geometry, placement=placement),
        contact=replace(spec.contact, part_surface=selection),
    )
    artifact = adapter.mesh(replace(revision, spec=spec))
    assert decode_record(encode_record(artifact), MeshArtifact) == artifact
    points = {
        n.coordinates_si
        for n in artifact.nodes
        if n.node_id
        in {i for e in artifact.elements if e.body_id == "part-body" for i in e.node_ids[:4]}
    }
    assert points == {(1.0, 2.0, 3.0), (1.0, 2.0, 2.99), (1.0, 2.01, 3.0), (1.01, 2.0, 3.0)}
    assert all(len(s.member_ids) == 4 for s in artifact.sets if s.body_id == "part-body")


def _face_rule(selection: Any, face_id: str) -> FaceSetRule:
    return FaceSetRule(
        selection.geometry_digest,
        selection.body_id,
        selection.frame,
        (FaceId(face_id),),
        _evidence("selection.rule", "face"),
    )


def _gap_case(adapter: Any, revision: Any, gap: float = 0.0) -> Any:
    spec = revision.spec
    report = adapter.inspect_rigid_tool(
        spec.rigid_tool.primitive, spec.rigid_tool.contact_surface.geometry_digest
    )
    upper = next(
        f
        for f in report.bodies[0].faces
        if all(abs(p[2] - 0.022) < 1e-12 for p in f.boundary_points_si)
    )
    part = replace(
        spec.contact.part_surface, rule=_face_rule(spec.contact.part_surface, "bottom-face")
    )
    tool = replace(
        spec.contact.tool_surface,
        rule=_face_rule(spec.contact.tool_surface, upper.face_id),
    )
    arrangement = SpecifiedGap(
        Quantity(gap, "m"),
        UnitDirection(FrameId("World"), 0.0, 0.0, -1.0),
        _evidence("contact.arrangement", "a"),
        _evidence("contact.gap", "g"),
        _evidence("contact.direction", "d"),
    )
    return replace(
        revision,
        spec=replace(
            spec,
            contact=replace(
                spec.contact, part_surface=part, tool_surface=tool, arrangement=arrangement
            ),
            rigid_tool=replace(spec.rigid_tool, contact_surface=tool),
        ),
    )


def test_gap_changes_applied_geometry_and_recipe(connected: Any) -> None:
    adapter, _, revision = connected
    first = adapter.mesh(_gap_case(adapter, revision, 0.0))
    second = adapter.mesh(_gap_case(adapter, revision, 0.002))
    for artifact, expected in ((first, 0.0), (second, -0.002)):
        ids = {i for e in artifact.elements if e.body_id == "tool-body" for i in e.node_ids}
        assert max(
            n.coordinates_si[2] for n in artifact.nodes if n.node_id in ids
        ) == pytest.approx(expected)
    assert first.provenance.mesh_recipe_digest != second.provenance.mesh_recipe_digest
    record = next(r for r in second.quality_records if r.metric_id == "initial-contact-placement")
    assert json.loads(record.reason)["requested_gap_si"] == 0.002


@pytest.mark.parametrize("bad", ["sloped", "overlap", "no_footprint"])
def test_gap_rejects_unsupported_geometry(connected: Any, bad: str) -> None:
    adapter, _, revision = connected
    revision = _gap_case(adapter, revision, -0.001 if bad == "overlap" else 0.0)
    spec = revision.spec
    if bad == "sloped":
        part = replace(
            spec.contact.part_surface,
            rule=_face_rule(spec.contact.part_surface, "top-face"),
        )
        spec = replace(spec, contact=replace(spec.contact, part_surface=part))
    if bad == "no_footprint":
        primitive = spec.rigid_tool.primitive
        placement = replace(
            primitive.placement,
            translation=replace(primitive.placement.translation, x=Quantity(1, "m")),
        )
        spec = replace(
            spec,
            rigid_tool=replace(spec.rigid_tool, primitive=replace(primitive, placement=placement)),
        )
    with pytest.raises(PortError):
        adapter.mesh(replace(revision, spec=spec))


def test_unit_conflict_stops_before_backend_meshing(connected: Any) -> None:
    adapter, backend, revision = connected
    revision = replace(
        revision,
        spec=replace(revision.spec, geometry=replace(revision.spec.geometry, step_unit="m")),
    )
    with pytest.raises(PortError, match="unit") as error:
        adapter.mesh(revision)
    assert error.value.category == PortErrorCategory.INTEGRITY
    assert backend.mesh_requests == []
