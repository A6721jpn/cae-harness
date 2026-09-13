from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import pytest

from febio_cae.adapters.geometry import (
    BACKEND_TET10_TO_CANONICAL_POSITIONS,
    BackendError,
    BackendMesh,
    GmshOCCBackend,
    GmshOCCConfig,
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

GEOMETRY_DIGEST = "f" * 64


@dataclass(frozen=True)
class _MeshLayout:
    node_coordinates: dict[int, tuple[float, float, float]]
    volume_elements: tuple[tuple[int, ...], ...]
    surface_elements: tuple[tuple[int, ...], ...]
    surface_element_type: int = 9


def _backend_node_order(canonical_nodes: tuple[int, ...]) -> tuple[int, ...]:
    return tuple(canonical_nodes[position] for position in BACKEND_TET10_TO_CANONICAL_POSITIONS)


def _surface_facets(canonical_nodes: tuple[int, ...]) -> tuple[tuple[int, ...], ...]:
    return tuple(
        tuple(canonical_nodes[position] for position in positions)
        for positions in TET10_FACE_NODE_POSITIONS
    )


def _single_tet_layout() -> _MeshLayout:
    canonical_nodes = tuple(range(1, 11))
    return _MeshLayout(
        node_coordinates={
            1: (0.0, 0.0, 0.0),
            2: (1.0, 0.0, 0.0),
            3: (0.0, 1.0, 0.0),
            4: (0.0, 0.0, 1.0),
            5: (0.5, 0.0, 0.0),
            6: (0.5, 0.5, 0.0),
            7: (0.0, 0.5, 0.0),
            8: (0.0, 0.0, 0.5),
            9: (0.5, 0.0, 0.5),
            10: (0.0, 0.5, 0.5),
        },
        volume_elements=(_backend_node_order(canonical_nodes),),
        surface_elements=_surface_facets(canonical_nodes),
    )


def _two_tet_layout() -> _MeshLayout:
    first = (1, 2, 3, 4, 6, 7, 8, 9, 10, 11)
    second = (1, 3, 2, 5, 8, 7, 6, 12, 13, 14)
    facets: list[tuple[int, ...]] = []
    corner_keys: set[frozenset[int]] = set()
    for canonical_nodes in (first, second):
        for facet in _surface_facets(canonical_nodes):
            corner_key = frozenset(facet[:3])
            if corner_key not in corner_keys:
                corner_keys.add(corner_key)
                facets.append(facet)
    return _MeshLayout(
        node_coordinates={
            1: (0.0, 0.0, 0.0),
            2: (1.0, 0.0, 0.0),
            3: (0.0, 1.0, 0.0),
            4: (0.0, 0.0, 1.0),
            5: (0.0, 0.0, -1.0),
            6: (0.5, 0.0, 0.0),
            7: (0.5, 0.5, 0.0),
            8: (0.0, 0.5, 0.0),
            9: (0.0, 0.0, 0.5),
            10: (0.5, 0.0, 0.5),
            11: (0.0, 0.5, 0.5),
            12: (0.0, 0.0, -0.5),
            13: (0.0, 0.5, -0.5),
            14: (0.5, 0.0, -0.5),
        },
        volume_elements=(_backend_node_order(first), _backend_node_order(second)),
        surface_elements=tuple(facets),
    )


def _layout_for(variant: str) -> _MeshLayout:
    valid = _single_tet_layout()
    if variant == "linear-triangles":
        return replace(
            valid,
            surface_element_type=2,
            surface_elements=tuple(facet[:3] for facet in valid.surface_elements),
        )
    if variant == "swapped-midsides":
        first = list(valid.surface_elements[0])
        first[3], first[4] = first[4], first[3]
        return replace(valid, surface_elements=(tuple(first), *valid.surface_elements[1:]))
    if variant == "duplicate-facet":
        return replace(
            valid,
            surface_elements=(*valid.surface_elements, valid.surface_elements[0]),
        )
    if variant == "missing-exterior-facet":
        return replace(valid, surface_elements=valid.surface_elements[:-1])
    if variant == "two-adjacent-tets":
        return _two_tet_layout()
    if variant == "valid":
        return valid
    raise AssertionError(f"unknown synthetic layout: {variant}")


class _SyntheticOption:
    def __init__(self) -> None:
        self.events: list[tuple[Any, ...]] = []

    def setNumber(self, name: str, value: float) -> None:
        self.events.append(("setNumber", name, value))

    def setString(self, name: str, value: str) -> None:
        self.events.append(("setString", name, value))


class _SyntheticOCC:
    def __init__(self, owner: _SyntheticGmsh) -> None:
        self.owner = owner

    def addSphere(self, x: float, y: float, z: float, radius: float) -> int:
        self.owner.events.append(("addSphere", x, y, z, radius))
        return 1

    def synchronize(self) -> None:
        self.owner.events.append(("synchronize",))

    def getMass(self, dimension: int, tag: int) -> float:
        if dimension == 3 and tag == 1:
            return 1.0 / 6.0
        if dimension == 2 and tag == 1:
            return 0.5
        raise RuntimeError("unexpected mass query")

    def getCenterOfMass(self, dimension: int, tag: int) -> tuple[float, float, float]:
        if dimension == 2 and tag == 1:
            return (1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0)
        raise RuntimeError("unexpected centroid query")


class _SyntheticModelMesh:
    def __init__(self, owner: _SyntheticGmsh) -> None:
        self.owner = owner

    def generate(self, dimension: int) -> None:
        self.owner.events.append(("generate", dimension))

    def setOrder(self, order: int) -> None:
        self.owner.events.append(("setOrder", order))

    def getNodes(
        self, dimension: int, tag: int, include_boundary: bool, parametric: bool
    ) -> tuple[list[int], list[float], list[float]]:
        del dimension, tag, include_boundary, parametric
        node_ids = sorted(self.owner.layout.node_coordinates)
        coordinates = [
            coordinate
            for node_id in node_ids
            for coordinate in self.owner.layout.node_coordinates[node_id]
        ]
        return node_ids, coordinates, []

    def getElements(
        self, dimension: int, tag: int
    ) -> tuple[list[int], list[list[int]], list[list[int]]]:
        if dimension == 3 and tag == 1:
            return (
                [11],
                [list(range(1, len(self.owner.layout.volume_elements) + 1))],
                [[node for element in self.owner.layout.volume_elements for node in element]],
            )
        if dimension == 2 and tag == 1:
            return (
                [self.owner.layout.surface_element_type],
                [list(range(1, len(self.owner.layout.surface_elements) + 1))],
                [[node for facet in self.owner.layout.surface_elements for node in facet]],
            )
        raise RuntimeError("unexpected element query")

    def getElementProperties(self, type_id: int) -> tuple[Any, ...]:
        if type_id == 11:
            return ("Tetrahedron 10", 3, 2, 10, 0.0, 4)
        if type_id == 9:
            return ("Triangle 6", 2, 2, 6, 0.0, 3)
        if type_id == 2:
            return ("Triangle 3", 2, 1, 3, 0.0, 3)
        raise RuntimeError("unexpected element type query")


class _SyntheticModel:
    def __init__(self, owner: _SyntheticGmsh) -> None:
        self.owner = owner
        self.occ = _SyntheticOCC(owner)
        self.mesh = _SyntheticModelMesh(owner)

    def add(self, name: str) -> None:
        self.owner.events.append(("add", name))

    def getEntities(self, dimension: int) -> list[tuple[int, int]]:
        return [(3, 1)] if dimension == 3 else []

    def getBoundary(
        self, entities: list[tuple[int, int]], combined: bool, oriented: bool
    ) -> list[tuple[int, int]]:
        del entities, combined, oriented
        return [(2, 1)]

    def getAdjacencies(self, dimension: int, tag: int) -> tuple[list[int], list[int]]:
        if dimension == 2 and tag == 1:
            return [1], []
        raise RuntimeError("unexpected adjacency query")


class _SyntheticGmsh:
    __version__ = "synthetic-1"

    def __init__(self, layout: _MeshLayout) -> None:
        self.layout = layout
        self.events: list[tuple[Any, ...]] = []
        self.option = _SyntheticOption()
        self.model = _SyntheticModel(self)

    def isInitialized(self) -> bool:
        return False

    def initialize(self) -> None:
        self.events.append(("initialize",))

    def clear(self) -> None:
        self.events.append(("clear",))

    def finalize(self) -> None:
        self.events.append(("finalize",))


def _primitive() -> RigidPrimitive:
    local = FrameId("ToolLocal")
    world = FrameId("World")
    evidence = EvidenceRef(
        schema_version="1",
        source_kind="registered_test_condition",
        reference="GmshBoundaryComponentTest:1",
        target_field="rigid_tool.model",
        content_digest="1" * 64,
    )
    return RigidPrimitive(
        kind="sphere",
        body_id=BodyId("synthetic-tool"),
        local_frame=local,
        placement=RigidTransform(
            source_frame=local,
            target_frame=world,
            translation=Translation3(world, Quantity(0, "m"), Quantity(0, "m"), Quantity(0, "m")),
            rotation=ProperRotation(((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))),
        ),
        dimensions={"radius": Quantity(1, "m")},
        dimension_evidence={
            "radius": EvidenceRef(
                schema_version="1",
                source_kind="registered_test_condition",
                reference="GmshBoundaryComponentTest:1",
                target_field="rigid_tool.radius",
                content_digest="2" * 64,
            )
        },
        model_evidence=evidence,
        placement_evidence=EvidenceRef(
            schema_version="1",
            source_kind="registered_test_condition",
            reference="GmshBoundaryComponentTest:1",
            target_field="rigid_tool.placement",
            content_digest="3" * 64,
        ),
    )


def _backend(monkeypatch: pytest.MonkeyPatch, layout: _MeshLayout) -> GmshOCCBackend:
    fake = _SyntheticGmsh(layout)
    monkeypatch.setattr(
        "febio_cae.adapters.geometry.gmsh_occ.importlib.import_module",
        lambda name: fake,
    )
    return GmshOCCBackend(
        GmshOCCConfig(
            module_name="synthetic-gmsh",
            expected_version="synthetic-1",
            geometry_kernel="OpenCASCADE",
            frame_id="World",
        )
    )


def _mesh(backend: GmshOCCBackend) -> BackendMesh:
    return backend.mesh_rigid_primitive(
        _primitive(),
        geometry_digest=GEOMETRY_DIGEST,
        global_size_si=0.5,
    )


def test_public_backend_accepts_complete_quadratic_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mesh = _mesh(_backend(monkeypatch, _layout_for("valid")))

    assert isinstance(mesh, BackendMesh)
    assert len(mesh.elements) == 1
    assert len(mesh.faces) == 4
    assert all(len(face.adjacent_element_ids) == 1 for face in mesh.faces)
    assert all(face.source_face_id is not None for face in mesh.faces)
    assert all(len(face.boundary_points_si) == 3 for face in mesh.faces)


@pytest.mark.parametrize(
    "variant",
    (
        "linear-triangles",
        "swapped-midsides",
        "duplicate-facet",
        "missing-exterior-facet",
        "two-adjacent-tets",
    ),
    ids=lambda value: value,
)
def test_public_backend_rejects_incomplete_or_nonmanifold_quadratic_boundary(
    monkeypatch: pytest.MonkeyPatch, variant: str
) -> None:
    backend = _backend(monkeypatch, _layout_for(variant))

    with pytest.raises(BackendError):
        _mesh(backend)
