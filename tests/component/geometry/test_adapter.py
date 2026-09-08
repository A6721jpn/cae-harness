from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Any

import pytest

from febio_cae.adapters.geometry import (
    BACKEND_TET10_ORDER_ID,
    BackendError,
    BackendErrorCategory,
    GmshOCCBackend,
    GmshOCCConfig,
    InitialContactPlacement,
    StepGeometryMeshAdapter,
)
from febio_cae.domain import (
    BodyId,
    CaseRevision,
    CoordinatePredicate,
    CoordinatePredicateRule,
    FaceId,
    FaceSetRule,
    FrameId,
    GeometryInspectionRequest,
    GeometryPort,
    GeometrySelectionRequest,
    MeshArtifact,
    MeshingPort,
    NamedAttributeRule,
    PortError,
    PortErrorCategory,
    Quantity,
    SelectionRef,
    SpecifiedGap,
    UnitDirection,
    WholeBodyRule,
)


def _evidence(target_field: str, seed: str) -> Any:
    from febio_cae.domain import EvidenceRef

    return EvidenceRef(
        schema_version="1",
        source_kind="registered_document",
        reference="P2InputAdapterTest:1",
        target_field=target_field,
        content_digest=hashlib.sha256(seed.encode("utf-8")).hexdigest(),
    )


def _selection(
    name: str,
    role: str,
    geometry_digest: str,
    body_id: BodyId,
    rule: Any,
) -> SelectionRef:
    return SelectionRef(
        name=name,
        role=role,
        role_evidence=_evidence("selection.role", name),
        geometry_digest=geometry_digest,
        body_id=body_id,
        frame=rule.frame if hasattr(rule, "frame") else FrameId("World"),
        rule=rule,
    )


def _assert_port_error(error: pytest.ExceptionInfo[PortError], category: PortErrorCategory) -> None:
    assert error.value.category is category


def test_geometry_mesh_adapter_api_is_available() -> None:
    assert StepGeometryMeshAdapter is not None, "P2 input/model adapter is not available"
    assert BackendError is not None
    assert InitialContactPlacement is not None
    assert GmshOCCBackend is not None
    assert GmshOCCConfig is not None


def test_adapter_implements_frozen_geometry_and_mesh_ports(
    adapter: Any,
) -> None:
    assert isinstance(adapter, GeometryPort)
    assert isinstance(adapter, MeshingPort)


def test_inspection_preserves_units_bodies_boundary_geometry_and_defects(
    adapter: Any,
    source_content: Any,
) -> None:
    inspection = adapter.inspect(
        GeometryInspectionRequest(source_content.source_asset),
        source_content,
    )

    assert inspection.declared_unit == "mm"
    assert inspection.body_ids == ("part-body",)
    assert inspection.closed_solid_body_ids == ("part-body",)
    assert inspection.body_facts[0].face_count == 4
    assert inspection.body_facts[0].volume_si == pytest.approx(1.0e-6)

    details = adapter.inspection_details(inspection)
    assert details.source_digest == source_content.source_asset.content_digest
    assert details.geometry_digest == "a" * 64
    assert details.bodies[0].faces[0].boundary_points_si
    assert details.bodies[0].faces[0].area_si > 0.0
    assert details.defects == ()


def test_inspection_reports_structured_unit_and_topology_failures(
    adapter: Any,
    synthetic_backend: Any,
    source_content: Any,
) -> None:
    synthetic_backend.declared_units = ()
    with pytest.raises(PortError) as missing:
        adapter.inspect(GeometryInspectionRequest(source_content.source_asset), source_content)
    _assert_port_error(missing, PortErrorCategory.INVALID_INPUT)

    synthetic_backend.declared_units = ("mm", "m")
    with pytest.raises(PortError) as conflicting:
        adapter.inspect(GeometryInspectionRequest(source_content.source_asset), source_content)
    _assert_port_error(conflicting, PortErrorCategory.INVALID_INPUT)

    synthetic_backend.declared_units = ("kg",)
    with pytest.raises(PortError) as unsupported_unit:
        adapter.inspect(GeometryInspectionRequest(source_content.source_asset), source_content)
    _assert_port_error(unsupported_unit, PortErrorCategory.UNSUPPORTED_CAPABILITY)

    synthetic_backend.declared_units = ("mm",)
    synthetic_backend.unsupported_topology = ("non-manifold volume",)
    with pytest.raises(PortError) as unsupported_topology:
        adapter.inspect(GeometryInspectionRequest(source_content.source_asset), source_content)
    _assert_port_error(unsupported_topology, PortErrorCategory.UNSUPPORTED_CAPABILITY)


def test_source_identity_is_checked_before_backend_use(
    adapter: Any,
    source_content: Any,
) -> None:
    from febio_cae.domain import SourceAssetRef

    wrong_ref = SourceAssetRef(
        "different-registered-source",
        source_content.source_asset.content_digest,
        source_content.source_asset.media_type,
    )
    with pytest.raises(PortError) as mismatch:
        adapter.inspect(
            GeometryInspectionRequest(wrong_ref),
            source_content,
        )
    _assert_port_error(mismatch, PortErrorCategory.INVALID_INPUT)


def test_selection_resolves_whole_body_named_and_coordinate_rules(
    adapter: Any,
    source_content: Any,
) -> None:
    whole = _selection(
        "whole",
        "support_surface",
        "a" * 64,
        BodyId("part-body"),
        WholeBodyRule(BodyId("part-body")),
    )
    whole_result = adapter.resolve_selection(
        GeometrySelectionRequest(source_content.source_asset, whole),
        source_content,
    )
    assert [face.face_id.value for face in whole_result.faces] == [
        "bottom-face",
        "other-face",
        "side-face",
        "top-face",
    ]

    named = _selection(
        "named",
        "support_surface",
        "a" * 64,
        BodyId("part-body"),
        NamedAttributeRule("top"),
    )
    named_result = adapter.resolve_selection(
        GeometrySelectionRequest(source_content.source_asset, named),
        source_content,
    )
    assert [face.face_id.value for face in named_result.faces] == ["top-face"]
    assert named_result.faces[0].centroid.z.to_si().value == pytest.approx(0.01)

    coordinate = _selection(
        "coordinate",
        "support_surface",
        "a" * 64,
        BodyId("part-body"),
        CoordinatePredicateRule(
            BodyId("part-body"),
            FrameId("World"),
            (
                CoordinatePredicate(
                    UnitDirection(
                        FrameId("World"),
                        0.0,
                        0.0,
                        1.0,
                    ),
                    "gte",
                    Quantity(0.009, "m"),
                ),
            ),
        ),
    )
    coordinate_result = adapter.resolve_selection(
        GeometrySelectionRequest(source_content.source_asset, coordinate),
        source_content,
    )
    assert [face.face_id.value for face in coordinate_result.faces] == ["top-face"]


def test_selection_rejects_empty_foreign_stale_and_changed_geometry(
    adapter: Any,
    synthetic_backend: Any,
    source_content: Any,
) -> None:
    empty = _selection(
        "empty",
        "support_surface",
        "a" * 64,
        BodyId("part-body"),
        NamedAttributeRule("does-not-exist"),
    )
    with pytest.raises(PortError) as empty_error:
        adapter.resolve_selection(
            GeometrySelectionRequest(source_content.source_asset, empty), source_content
        )
    _assert_port_error(empty_error, PortErrorCategory.INVALID_INPUT)

    foreign = _selection(
        "foreign",
        "support_surface",
        "a" * 64,
        BodyId("foreign-body"),
        WholeBodyRule(BodyId("foreign-body")),
    )
    with pytest.raises(PortError) as foreign_error:
        adapter.resolve_selection(
            GeometrySelectionRequest(source_content.source_asset, foreign), source_content
        )
    _assert_port_error(foreign_error, PortErrorCategory.INVALID_INPUT)

    stale = _selection(
        "stale",
        "support_surface",
        "f" * 64,
        BodyId("part-body"),
        WholeBodyRule(BodyId("part-body")),
    )
    with pytest.raises(PortError) as stale_error:
        adapter.resolve_selection(
            GeometrySelectionRequest(source_content.source_asset, stale), source_content
        )
    _assert_port_error(stale_error, PortErrorCategory.INTEGRITY)

    face_set = _selection(
        "preserved",
        "support_surface",
        "a" * 64,
        BodyId("part-body"),
        FaceSetRule(
            "a" * 64,
            BodyId("part-body"),
            FrameId("World"),
            (FaceId("top-face"),),
            _evidence("selection.faces", "preserved"),
        ),
    )
    first = adapter.resolve_selection(
        GeometrySelectionRequest(source_content.source_asset, face_set), source_content
    )
    preserved = replace(face_set, resolution=first)
    synthetic_backend.changed_measurement = True
    with pytest.raises(PortError) as changed:
        adapter.resolve_selection(
            GeometrySelectionRequest(source_content.source_asset, preserved), source_content
        )
    _assert_port_error(changed, PortErrorCategory.INTEGRITY)


def test_registered_synthetic_source_to_artifact_consumer_is_connected(
    adapter: Any,
    mesh_port: Any,
    synthetic_case_revision: CaseRevision,
) -> None:
    artifact = mesh_port.mesh(synthetic_case_revision)

    assert isinstance(artifact, MeshArtifact)
    assert artifact.provenance.source_geometry_digest == "a" * 64
    assert artifact.provenance.source_body_ids == ("part-body", "tool-body")
    assert artifact.provenance.mapping_id == "backend-tet10-to-domain-tet10-v1"
    assert artifact.provenance.node_ordering_id == "tet10-canonical-v1"
    assert artifact.provenance.face_ordering_id == "tet10-face-canonical-v1"
    assert len(artifact.artifact_digest) == 64
    assert artifact.artifact_digest in artifact.to_bytes().decode("utf-8")


def test_mesh_has_canonical_faces_and_disjoint_part_tool_ownership(
    mesh_port: Any,
    synthetic_case_revision: CaseRevision,
) -> None:
    from febio_cae.domain.artifacts import TET10_FACE_NODE_POSITIONS

    artifact = mesh_port.mesh(synthetic_case_revision)
    elements = {element.element_id: element for element in artifact.elements}
    for face in artifact.faces:
        element = elements[face.adjacent_element_ids[0]]
        expected = tuple(
            element.node_ids[position]
            for position in TET10_FACE_NODE_POSITIONS[face.local_face_ids[0]]
        )
        assert tuple(face.node_ids) == expected

    node_ids_by_body: dict[str, set[int]] = {}
    element_ids_by_body: dict[str, set[int]] = {}
    for element in artifact.elements:
        node_ids_by_body.setdefault(element.body_id, set()).update(element.node_ids)
        element_ids_by_body.setdefault(element.body_id, set()).add(element.element_id)
    assert node_ids_by_body["part-body"].isdisjoint(node_ids_by_body["tool-body"])
    assert element_ids_by_body["part-body"].isdisjoint(element_ids_by_body["tool-body"])


@pytest.mark.parametrize(
    ("kind", "dimensions", "bounds"),
    [
        (
            "sphere",
            {"radius": Quantity(2, "mm")},
            ((-0.002, 0.002), (-0.002, 0.002), (0.018, 0.022)),
        ),
        (
            "cylinder",
            {"radius": Quantity(2, "mm"), "height": Quantity(6, "mm")},
            ((-0.002, 0.002), (-0.002, 0.002), (0.017, 0.023)),
        ),
        (
            "box",
            {
                "length": Quantity(4, "mm"),
                "width": Quantity(6, "mm"),
                "height": Quantity(8, "mm"),
            },
            ((-0.002, 0.002), (-0.003, 0.003), (0.016, 0.024)),
        ),
    ],
)
def test_rigid_generators_honor_typed_dimensions_and_pose(
    mesh_port: Any,
    synthetic_case_revision: CaseRevision,
    kind: str,
    dimensions: dict[str, Quantity],
    bounds: tuple[tuple[float, float], ...],
) -> None:
    old_primitive = synthetic_case_revision.spec.rigid_tool.primitive
    primitive = replace(
        old_primitive,
        kind=kind,
        dimensions=dimensions,
        dimension_evidence={
            name: _evidence(f"rigid_tool.{name}", f"{kind}-{name}") for name in dimensions
        },
    )
    tool = replace(synthetic_case_revision.spec.rigid_tool, primitive=primitive)
    revision = replace(
        synthetic_case_revision,
        spec=replace(synthetic_case_revision.spec, rigid_tool=tool),
    )
    if kind in {"sphere", "cylinder"}:
        with pytest.raises(PortError, match="curved.*quality") as error:
            mesh_port.mesh(revision)
        _assert_port_error(error, PortErrorCategory.UNSUPPORTED_CAPABILITY)
        return
    artifact = mesh_port.mesh(revision)
    tool_element_ids = {
        element.element_id for element in artifact.elements if element.body_id == "tool-body"
    }
    tool_nodes = {
        node_id: node
        for element in artifact.elements
        if element.element_id in tool_element_ids
        for node_id in element.node_ids
        for node in artifact.nodes
        if node.node_id == node_id
    }
    for axis, (lower, upper) in enumerate(bounds):
        values = [node.coordinates_si[axis] for node in tool_nodes.values()]
        assert min(values) == pytest.approx(lower)
        assert max(values) == pytest.approx(upper)


def test_mesh_rejects_unsupported_backend_mapping_and_bad_jacobian(
    mesh_port: Any,
    synthetic_backend: Any,
    synthetic_case_revision: CaseRevision,
) -> None:
    synthetic_backend.mesh_ordering_id = "unknown-tet10-order"
    with pytest.raises(PortError) as mapping:
        mesh_port.mesh(synthetic_case_revision)
    _assert_port_error(mapping, PortErrorCategory.UNSUPPORTED_CAPABILITY)

    synthetic_backend.mesh_ordering_id = BACKEND_TET10_ORDER_ID
    synthetic_backend.reverse_orientation = True
    with pytest.raises(PortError) as orientation:
        mesh_port.mesh(synthetic_case_revision)
    _assert_port_error(orientation, PortErrorCategory.QUALITY)


def test_mesh_recipe_identity_changes_with_mesh_controls(
    mesh_port: Any,
    synthetic_case_revision: CaseRevision,
) -> None:
    first = mesh_port.mesh(synthetic_case_revision)
    changed_policy = replace(
        synthetic_case_revision.spec.mesh_policy,
        global_size=Quantity(1, "mm"),
    )
    changed_revision = replace(
        synthetic_case_revision,
        spec=replace(synthetic_case_revision.spec, mesh_policy=changed_policy),
    )
    second = mesh_port.mesh(changed_revision)
    assert first.provenance.mesh_recipe_digest != second.provenance.mesh_recipe_digest


def test_initial_contact_placement_records_explicit_as_placed_result(
    adapter: Any,
    synthetic_case_revision: CaseRevision,
) -> None:
    result = adapter.calculate_initial_contact_placement(synthetic_case_revision)
    assert isinstance(result, InitialContactPlacement)
    assert result.calculated is False
    assert result.reason == "as_placed"
    assert result.placement == synthetic_case_revision.spec.rigid_tool.primitive.placement


def test_initial_contact_placement_requires_unique_explicit_targets(
    adapter: Any,
    synthetic_case_revision: CaseRevision,
) -> None:
    arrangement = SpecifiedGap(
        gap=Quantity(0, "mm"),
        direction=UnitDirection(
            FrameId("World"),
            0.0,
            0.0,
            1.0,
        ),
        arrangement_evidence=_evidence("contact.arrangement", "gap-arrangement"),
        gap_evidence=_evidence("contact.gap", "gap-value"),
        direction_evidence=_evidence("contact.direction", "gap-direction"),
    )
    contact = replace(synthetic_case_revision.spec.contact, arrangement=arrangement)
    revision = replace(
        synthetic_case_revision,
        spec=replace(synthetic_case_revision.spec, contact=contact),
    )
    with pytest.raises(PortError) as ambiguous:
        adapter.calculate_initial_contact_placement(revision)
    _assert_port_error(ambiguous, PortErrorCategory.INVALID_INPUT)


def test_gmsh_backend_has_explicit_configuration_and_structured_missing_environment(
    source_content: Any,
) -> None:
    config = GmshOCCConfig(
        module_name="module-that-is-not-installed",
        expected_version="4.15.2",
        geometry_kernel="OpenCASCADE",
        frame_id="World",
    )
    backend = GmshOCCBackend(config)
    assert backend.backend_id == "gmsh-occ"
    assert backend.backend_version == "4.15.2"
    with pytest.raises(BackendError) as unavailable:
        backend.inspect(source_content.content, ())
    assert unavailable.value.category is BackendErrorCategory.ENVIRONMENT
