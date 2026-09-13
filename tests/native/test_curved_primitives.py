"""Opt-in native Gmsh/OpenCASCADE contracts for curved rigid primitives.

This module is outside the default pytest ``testpaths``.  The explicit native
gate must set ``FEBIO_CAE_NATIVE_GMSH_CONFIG`` to an absolute JSON file whose
values are the measured runtime configuration, for example::

    {
      "module_name": "gmsh",
      "expected_version": "<measured Gmsh version>",
      "geometry_kernel": "OpenCASCADE",
      "frame_id": "World",
      "expected_occt_version": "<measured OCC version>",
      "require_step_ap214": false,
      "cpu_workers": 1
    }

The backend API is checked inside each test before that configuration is read,
so a missing primitive API is a product assertion failure rather than a native
import or collection failure.  No fixture or mesher double is used here.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any, cast

import pytest

from febio_cae.adapters.geometry.backend import (
    BACKEND_TET10_ORDER_ID,
    BACKEND_TET10_TO_CANONICAL_POSITIONS,
    BackendInspection,
    BackendMesh,
)
from febio_cae.domain import (
    TET10_FACE_NODE_POSITIONS,
    BodyId,
    EvidenceRef,
    FrameId,
    ProperRotation,
    Quantity,
    RigidPrimitive,
    RigidTransform,
    Translation3,
)

NATIVE_CONFIG_ENV = "FEBIO_CAE_NATIVE_GMSH_CONFIG"
_CONFIG_FIELDS = frozenset(
    {
        "module_name",
        "expected_version",
        "geometry_kernel",
        "frame_id",
        "expected_occt_version",
        "require_step_ap214",
        "cpu_workers",
    }
)
_SURFACE_TOLERANCE = 2.0e-8


@dataclass(frozen=True, slots=True)
class _PrimitiveCase:
    kind: str
    body_id: str
    local_frame: FrameId
    radius: Quantity
    height: Quantity | None
    geometry_digest: str

    @property
    def global_size_si(self) -> float:
        return float(self.radius.to_si().value) / 2.0


CASES = (
    _PrimitiveCase(
        kind="sphere",
        body_id="native-sphere-body",
        local_frame=FrameId("ToolLocal"),
        radius=Quantity(10, "mm"),
        height=None,
        geometry_digest="a" * 64,
    ),
    _PrimitiveCase(
        kind="cylinder",
        body_id="native-cylinder-body",
        local_frame=FrameId("ToolLocal"),
        radius=Quantity(6, "mm"),
        height=Quantity(18, "mm"),
        geometry_digest="b" * 64,
    ),
)


def _evidence(target_field: str, seed: str) -> EvidenceRef:
    return EvidenceRef(
        schema_version="1",
        source_kind="registered_test_condition",
        reference="NativeCurvedPrimitiveTest:1",
        target_field=target_field,
        content_digest=hashlib.sha256(seed.encode("utf-8")).hexdigest(),
    )


def _placement(local_frame: FrameId) -> RigidTransform:
    world = FrameId("World")
    # A nonzero translation and a quarter-turn about x make accidental backend
    # placement application observable, especially for the cylinder's z-axis.
    return RigidTransform(
        source_frame=local_frame,
        target_frame=world,
        translation=Translation3(
            world,
            Quantity(31, "mm"),
            Quantity(-47, "mm"),
            Quantity(83, "mm"),
        ),
        rotation=ProperRotation(
            (
                (1.0, 0.0, 0.0),
                (0.0, 0.0, -1.0),
                (0.0, 1.0, 0.0),
            )
        ),
    )


def _primitive(case: _PrimitiveCase, *, radius: Quantity | None = None) -> RigidPrimitive:
    radius = case.radius if radius is None else radius
    dimensions: dict[str, Quantity] = {"radius": radius}
    if case.height is not None:
        dimensions["height"] = case.height
    return RigidPrimitive(
        kind=case.kind,
        body_id=BodyId(case.body_id),
        local_frame=case.local_frame,
        placement=_placement(case.local_frame),
        dimensions=dimensions,
        dimension_evidence={
            name: _evidence(f"rigid_tool.{name}", f"{case.kind}-{name}-{radius.value}")
            for name in dimensions
        },
        model_evidence=_evidence("rigid_tool.model", f"{case.kind}-model"),
        placement_evidence=_evidence("rigid_tool.placement", f"{case.kind}-placement"),
    )


def _native_config(config_type: Any) -> Any:
    configured = os.environ.get(NATIVE_CONFIG_ENV, "").strip()
    if not configured:
        pytest.fail(
            f"NATIVE_PREREQUISITE_MISSING: {NATIVE_CONFIG_ENV} is unset; "
            "supply measured Gmsh/OCC configuration",
            pytrace=False,
        )
    path = Path(configured).expanduser().absolute()
    if not path.is_file():
        pytest.fail(
            f"NATIVE_PREREQUISITE_MISSING: {NATIVE_CONFIG_ENV} does not name a file: {path}",
            pytrace=False,
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        pytest.fail(f"NATIVE_PREREQUISITE_INVALID: cannot read Gmsh config: {error}", pytrace=False)
    if not isinstance(raw, dict) or set(raw) != _CONFIG_FIELDS:
        pytest.fail(
            f"NATIVE_PREREQUISITE_INVALID: config must contain exactly {sorted(_CONFIG_FIELDS)!r}",
            pytrace=False,
        )
    if raw.get("geometry_kernel") != "OpenCASCADE":
        pytest.fail(
            "NATIVE_PREREQUISITE_INVALID: geometry_kernel must explicitly be OpenCASCADE",
            pytrace=False,
        )
    if type(raw.get("cpu_workers")) is not int or raw["cpu_workers"] != 1:
        pytest.fail(
            "NATIVE_PREREQUISITE_INVALID: cpu_workers must explicitly be deterministic CPU1",
            pytrace=False,
        )
    if not isinstance(raw.get("expected_version"), str) or not raw["expected_version"]:
        pytest.fail(
            "NATIVE_PREREQUISITE_INVALID: expected_version must be measured and explicit",
            pytrace=False,
        )
    if not isinstance(raw.get("expected_occt_version"), str) or not raw["expected_occt_version"]:
        pytest.fail(
            "NATIVE_PREREQUISITE_INVALID: expected_occt_version must be measured and explicit",
            pytrace=False,
        )
    try:
        return config_type(**raw)
    except (TypeError, ValueError) as error:
        pytest.fail(
            f"NATIVE_PREREQUISITE_INVALID: Gmsh config is not accepted: {error}", pytrace=False
        )


def _backend() -> Any:
    """Resolve the product API before opening the configured native module."""

    module = importlib.import_module("febio_cae.adapters.geometry.gmsh_occ")
    backend_type = getattr(module, "GmshOCCBackend", None)
    assert backend_type is not None, "GmshOCCBackend is not available"
    assert callable(getattr(backend_type, "inspect_rigid_primitive", None)), (
        "MISSING_API: GmshOCCBackend.inspect_rigid_primitive"
    )
    assert callable(getattr(backend_type, "mesh_rigid_primitive", None)), (
        "MISSING_API: GmshOCCBackend.mesh_rigid_primitive"
    )
    config_type = getattr(module, "GmshOCCConfig", None)
    assert config_type is not None, "GmshOCCConfig is not available"
    return backend_type(_native_config(config_type))


def _assert_digest(value: object, field: str) -> None:
    assert isinstance(value, str), field
    assert len(value) == 64 and value == value.lower(), field
    assert all(character in "0123456789abcdef" for character in value), field


def _assert_inspection_contract(
    case: _PrimitiveCase, primitive: RigidPrimitive, inspection: BackendInspection
) -> None:
    assert isinstance(inspection, BackendInspection)
    _assert_digest(inspection.source_digest, "inspection.source_digest")
    assert inspection.geometry_digest == case.geometry_digest
    assert tuple(inspection.declared_units) == ("m",)
    assert inspection.frame == primitive.local_frame
    assert len(inspection.bodies) == 1

    body = inspection.bodies[0]
    body_id = primitive.body_id.value
    assert body.body_id == body_id
    assert body.closed_solid is True
    assert body.volume_si == pytest.approx(_analytic_volume(case), rel=2.0e-7)
    assert sum(face.area_si for face in body.faces) == pytest.approx(
        _analytic_surface_area(case), rel=2.0e-7
    )
    assert all(face.body_id == body_id for face in body.faces)
    assert all(face.frame == primitive.local_frame for face in body.faces)
    assert len({face.face_id for face in body.faces}) == len(body.faces)


def _radius_si(case: _PrimitiveCase) -> float:
    return float(case.radius.to_si().value)


def _height_si(case: _PrimitiveCase) -> float:
    assert case.height is not None
    return float(case.height.to_si().value)


def _analytic_volume(case: _PrimitiveCase) -> float:
    radius = _radius_si(case)
    return (
        4.0 * math.pi * radius**3 / 3.0
        if case.kind == "sphere"
        else math.pi * radius**2 * _height_si(case)
    )


def _analytic_surface_area(case: _PrimitiveCase) -> float:
    radius = _radius_si(case)
    return (
        4.0 * math.pi * radius**2
        if case.kind == "sphere"
        else 2.0 * math.pi * radius * (radius + _height_si(case))
    )


def _assert_local_coordinates(
    case: _PrimitiveCase, primitive: RigidPrimitive, mesh: BackendMesh
) -> None:
    assert mesh.frame == primitive.local_frame
    radius = _radius_si(case)
    tolerance = max(_SURFACE_TOLERANCE, radius * 1.0e-5)
    coordinates = [tuple(node.coordinates_si) for node in mesh.nodes]
    if case.kind == "sphere":
        distances = [math.sqrt(sum(value * value for value in point)) for point in coordinates]
        assert all(distance <= radius + tolerance for distance in distances)
        assert max(distances) >= radius - tolerance
        return

    half_height = _height_si(case) / 2.0
    radial = [math.hypot(point[0], point[1]) for point in coordinates]
    assert all(value <= radius + tolerance for value in radial)
    assert all(
        -half_height - tolerance <= point[2] <= half_height + tolerance for point in coordinates
    )
    assert max(radial) >= radius - tolerance
    assert min(point[2] for point in coordinates) <= -half_height + tolerance
    assert max(point[2] for point in coordinates) >= half_height - tolerance


def _face_coordinates(face: Any, mesh: BackendMesh) -> tuple[tuple[float, float, float], ...]:
    assert len(face.adjacent_element_ids) == 1
    assert len(face.local_face_ids) == 1
    elements = {element.element_id: element for element in mesh.elements}
    nodes = {node.node_id: tuple(node.coordinates_si) for node in mesh.nodes}
    element = elements[face.adjacent_element_ids[0]]
    canonical_ids = tuple(
        element.node_ids[position] for position in BACKEND_TET10_TO_CANONICAL_POSITIONS
    )
    positions = TET10_FACE_NODE_POSITIONS[face.local_face_ids[0]]
    return tuple(
        cast(tuple[float, float, float], nodes[canonical_ids[position]]) for position in positions
    )


def _assert_surface_membership(
    case: _PrimitiveCase, points: tuple[tuple[float, float, float], ...]
) -> None:
    assert len(points) == 6, "boundary checks must include all Tet10 face nodes"
    radius = _radius_si(case)
    tolerance = max(_SURFACE_TOLERANCE, radius * 1.0e-5)
    if case.kind == "sphere":
        assert all(
            abs(math.sqrt(sum(value * value for value in point)) - radius) <= tolerance
            for point in points
        )
        return

    half_height = _height_si(case) / 2.0
    top = all(abs(point[2] - half_height) <= tolerance for point in points)
    bottom = all(abs(point[2] + half_height) <= tolerance for point in points)
    if top or bottom:
        assert all(math.hypot(point[0], point[1]) <= radius + tolerance for point in points)
    else:
        assert all(abs(math.hypot(point[0], point[1]) - radius) <= tolerance for point in points)
        assert all(
            -half_height - tolerance <= point[2] <= half_height + tolerance for point in points
        )


def _max_chord_deviation(
    faces: list[tuple[tuple[float, float, float], ...]],
) -> float:
    deviation = 0.0
    for points in faces:
        corners = points[:3]
        for index, (first, second) in enumerate(((0, 1), (1, 2), (2, 0)), start=3):
            chord_midpoint = tuple(
                (corners[first][axis] + corners[second][axis]) / 2.0 for axis in range(3)
            )
            deviation = max(deviation, math.dist(points[index], chord_midpoint))
    return deviation


def _corner_edge_records(mesh: BackendMesh) -> tuple[tuple[float, float], ...]:
    nodes = {node.node_id: tuple(node.coordinates_si) for node in mesh.nodes}
    records: list[tuple[float, float]] = []
    seen: set[tuple[int, int]] = set()
    for element in mesh.elements:
        corners = element.node_ids[:4]
        for first, second in (
            (0, 1),
            (0, 2),
            (0, 3),
            (1, 2),
            (1, 3),
            (2, 3),
        ):
            first_id, second_id = corners[first], corners[second]
            edge = (first_id, second_id) if first_id < second_id else (second_id, first_id)
            if edge in seen:
                continue
            seen.add(edge)
            first_point = nodes[edge[0]]
            second_point = nodes[edge[1]]
            midpoint = tuple((first_point[axis] + second_point[axis]) / 2.0 for axis in range(3))
            records.append(
                (math.dist(midpoint, (0.0, 0.0, 0.0)), math.dist(first_point, second_point))
            )
    return tuple(records)


@pytest.mark.native
@pytest.mark.parametrize("case", CASES, ids=lambda value: value.kind)
def test_native_curved_primitive_inspection_and_tet10_mesh(case: _PrimitiveCase) -> None:
    """Exercise both concrete APIs against distinct analytic OCC solids."""

    # This helper deliberately checks the missing API before reading native
    # configuration or allowing Gmsh's Python module to be imported by backend.
    backend = _backend()
    primitive = _primitive(case)
    inspection = backend.inspect_rigid_primitive(
        primitive,
        geometry_digest=case.geometry_digest,
    )
    _assert_inspection_contract(case, primitive, inspection)

    mesh = backend.mesh_rigid_primitive(
        primitive,
        geometry_digest=case.geometry_digest,
        global_size_si=case.global_size_si,
    )
    assert isinstance(mesh, BackendMesh)
    _assert_digest(mesh.source_digest, "mesh.source_digest")
    assert mesh.geometry_digest == case.geometry_digest
    assert mesh.body_id == primitive.body_id.value
    assert mesh.ordering_id == BACKEND_TET10_ORDER_ID
    assert all(element.body_id == primitive.body_id.value for element in mesh.elements)
    assert all(element.element_type == "tet10" for element in mesh.elements)
    assert all(element.ordering_id == BACKEND_TET10_ORDER_ID for element in mesh.elements)
    assert all(
        len(element.node_ids) == 10 and len(set(element.node_ids)) == 10
        for element in mesh.elements
    )
    assert len({face.face_id for face in mesh.faces}) == len(mesh.faces)

    inspection_face_ids = {face.face_id for body in inspection.bodies for face in body.faces}
    boundary_faces = [face for face in mesh.faces if face.source_face_id is not None]
    assert {face.source_face_id for face in boundary_faces} == inspection_face_ids

    boundary_coordinates = [_face_coordinates(face, mesh) for face in boundary_faces]
    for points in boundary_coordinates:
        _assert_surface_membership(case, points)
    assert _max_chord_deviation(boundary_coordinates) > 1.0e-8
    _assert_local_coordinates(case, primitive, mesh)

    # The recipe identity must vary with both the typed primitive and the
    # native size control while the registered geometry identity is retained.
    changed_primitive = _primitive(case, radius=Quantity(case.radius.value * 1.1, "mm"))
    changed_primitive_mesh = backend.mesh_rigid_primitive(
        changed_primitive,
        geometry_digest=case.geometry_digest,
        global_size_si=case.global_size_si,
    )
    changed_size_mesh = backend.mesh_rigid_primitive(
        primitive,
        geometry_digest=case.geometry_digest,
        global_size_si=case.global_size_si * 0.8,
    )
    assert changed_primitive_mesh.geometry_digest == case.geometry_digest
    assert changed_size_mesh.geometry_digest == case.geometry_digest
    assert changed_primitive_mesh.source_digest != mesh.source_digest
    assert changed_size_mesh.source_digest != mesh.source_digest


@pytest.mark.native
def test_native_source_local_ball_refines_only_bounded_sphere_region() -> None:
    """Predeclared native gate: exactly two mesh calls and no inspection call.

    Compare interior and far-shell edge medians with the coarse baseline.
    The 4 mm ball plus the 5 mm transition ends at 9 mm; the measured far
    shell starts at 9.5 mm. These distribution checks distinguish a local
    field from uniform refinement without treating size as a hard edge bound.
    """

    case = next(item for item in CASES if item.kind == "sphere")
    backend = _backend()
    primitive = _primitive(case)
    from febio_cae.adapters.geometry import BackendLocalRefinement

    local_size_si = 0.001
    local_refinement = BackendLocalRefinement(
        body_id=primitive.body_id.value,
        frame=primitive.local_frame,
        center_si=(0.0, 0.0, 0.0),
        radius_si=0.004,
        size_si=local_size_si,
    )
    coarse = backend.mesh_rigid_primitive(
        primitive,
        geometry_digest=case.geometry_digest,
        global_size_si=case.global_size_si,
    )
    refined = backend.mesh_rigid_primitive(
        primitive,
        geometry_digest=case.geometry_digest,
        global_size_si=case.global_size_si,
        local_refinements=(local_refinement,),
    )

    coarse_records = _corner_edge_records(coarse)
    refined_records = _corner_edge_records(refined)
    coarse_inside = [length for distance, length in coarse_records if distance <= 0.003]
    refined_inside = [length for distance, length in refined_records if distance <= 0.003]
    coarse_far = [length for distance, length in coarse_records if distance >= 0.0095]
    refined_far = [length for distance, length in refined_records if distance >= 0.0095]

    # At least three samples prevent a single oversized edge from qualifying.
    assert min(map(len, (coarse_inside, refined_inside, coarse_far, refined_far))) >= 3
    inside_median = median(refined_inside)
    far_median = median(refined_far)
    assert inside_median < median(coarse_inside) * 0.6
    assert far_median >= median(coarse_far) * 0.65
    assert far_median >= inside_median * 2.0
