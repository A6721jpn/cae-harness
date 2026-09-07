"""Backend-neutral records for the connected geometry/mesh adapter.

The records in this module are deliberately adapter-private.  They describe
what a CAD/meshing backend observed or produced; the product-facing adapter
converts them into the frozen records in :mod:`febio_cae.domain`.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from febio_cae.domain import FrameId

BACKEND_TET10_ORDER_ID = "tet10-backend-v1"
# Position in a backend Tet10 connectivity tuple for each canonical position.
# Gmsh's type-11 tuple differs from the frozen FEBio candidate only in the
# final two edge nodes; the adapter applies this explicit permutation.
BACKEND_TET10_TO_CANONICAL_POSITIONS = (0, 1, 2, 3, 4, 5, 6, 7, 9, 8)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class BackendErrorCategory(str, Enum):
    """Failure categories emitted by an optional backend."""

    INVALID_INPUT = "invalid_input"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    ENVIRONMENT = "environment"
    INTEGRITY = "integrity"
    QUALITY = "quality"


class BackendError(RuntimeError):
    """Structured backend failure that the product adapter maps to ``PortError``."""

    def __init__(self, category: BackendErrorCategory, message: str) -> None:
        if not isinstance(category, BackendErrorCategory):
            raise TypeError("category must be a BackendErrorCategory")
        if not isinstance(message, str) or not message or message != message.strip():
            raise ValueError("message must be non-empty text")
        super().__init__(message)
        self.category = category


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field} must be non-empty text without surrounding whitespace")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{field} contains a control character")
    return value


def _digest(value: object, field: str) -> str:
    value = _text(value, field)
    if _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _finite(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be a finite number")
    try:
        result = float(value)
    except OverflowError as error:
        raise ValueError(f"{field} must be finite") from error
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return 0.0 if result == 0.0 else result


def _coordinates(value: object, field: str) -> tuple[float, float, float]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise TypeError(f"{field} must have exactly three finite components")
    if len(value) != 3:
        raise ValueError(f"{field} must have exactly three finite components")
    return tuple(_finite(item, f"{field}[{index}]") for index, item in enumerate(value))  # type: ignore[return-value]


def _point_sequence(
    value: object, field: str, *, allow_empty: bool = True
) -> tuple[tuple[float, float, float], ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise TypeError(f"{field} must be a sequence of points")
    result = tuple(_coordinates(item, f"{field}[{index}]") for index, item in enumerate(value))
    if not allow_empty and not result:
        raise ValueError(f"{field} must not be empty")
    return result


def _text_sequence(value: object, field: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise TypeError(f"{field} must be a sequence")
    result = tuple(_text(item, f"{field}[]") for item in value)
    if not allow_empty and not result:
        raise ValueError(f"{field} must not be empty")
    if len(set(result)) != len(result):
        raise ValueError(f"{field} must not contain duplicates")
    return result


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


@dataclass(frozen=True, slots=True)
class BackendFace:
    """One backend-observed boundary face and its geometric measurements."""

    face_id: str
    body_id: str
    frame: FrameId
    area_si: float
    centroid_si: Sequence[float]
    boundary_points_si: Sequence[Sequence[float]] = ()
    attributes: Sequence[str] = ()
    defects: Sequence[str] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "face_id", _text(self.face_id, "face_id"))
        object.__setattr__(self, "body_id", _text(self.body_id, "body_id"))
        if not isinstance(self.frame, FrameId):
            raise TypeError("frame must be a FrameId")
        area = _finite(self.area_si, "area_si")
        if area <= 0.0:
            raise ValueError("area_si must be positive")
        object.__setattr__(self, "area_si", area)
        object.__setattr__(self, "centroid_si", _coordinates(self.centroid_si, "centroid_si"))
        object.__setattr__(
            self,
            "boundary_points_si",
            _point_sequence(self.boundary_points_si, "boundary_points_si"),
        )
        object.__setattr__(self, "attributes", _text_sequence(self.attributes, "attributes"))
        object.__setattr__(self, "defects", _text_sequence(self.defects, "defects"))

    def to_dict(self) -> dict[str, object]:
        return {
            "face_id": self.face_id,
            "body_id": self.body_id,
            "frame": self.frame.value,
            "area_si": self.area_si,
            "centroid_si": list(self.centroid_si),
            "boundary_points_si": [list(point) for point in self.boundary_points_si],
            "attributes": sorted(self.attributes),
            "defects": sorted(self.defects),
        }


@dataclass(frozen=True, slots=True)
class BackendBody:
    """One backend body with its observed boundary facts."""

    body_id: str
    closed_solid: bool
    volume_si: float
    faces: Sequence[BackendFace]
    defects: Sequence[str] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "body_id", _text(self.body_id, "body_id"))
        if not isinstance(self.closed_solid, bool):
            raise TypeError("closed_solid must be a bool")
        volume = _finite(self.volume_si, "volume_si")
        if volume <= 0.0:
            raise ValueError("volume_si must be positive")
        object.__setattr__(self, "volume_si", volume)
        if isinstance(self.faces, (str, bytes, bytearray)) or not isinstance(self.faces, Sequence):
            raise TypeError("faces must be a sequence")
        faces = tuple(self.faces)
        if not faces or any(not isinstance(face, BackendFace) for face in faces):
            raise ValueError("faces must contain BackendFace values")
        if any(face.body_id != self.body_id for face in faces):
            raise ValueError("faces must belong to the containing body")
        if len({face.face_id for face in faces}) != len(faces):
            raise ValueError("faces must not contain duplicate IDs")
        object.__setattr__(self, "faces", faces)
        object.__setattr__(self, "defects", _text_sequence(self.defects, "defects"))

    def to_dict(self) -> dict[str, object]:
        return {
            "body_id": self.body_id,
            "closed_solid": self.closed_solid,
            "volume_si": self.volume_si,
            "faces": [face.to_dict() for face in sorted(self.faces, key=lambda item: item.face_id)],
            "defects": sorted(self.defects),
        }


@dataclass(frozen=True, slots=True)
class BackendInspection:
    """Raw geometry inspection retained by the adapter for selection resolution."""

    source_digest: str
    geometry_digest: str
    declared_units: Sequence[str]
    frame: FrameId
    bodies: Sequence[BackendBody]
    unsupported_topology: Sequence[str] = ()
    defects: Sequence[str] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_digest", _digest(self.source_digest, "source_digest"))
        object.__setattr__(
            self, "geometry_digest", _digest(self.geometry_digest, "geometry_digest")
        )
        object.__setattr__(
            self, "declared_units", _text_sequence(self.declared_units, "declared_units")
        )
        if not isinstance(self.frame, FrameId):
            raise TypeError("frame must be a FrameId")
        if isinstance(self.bodies, (str, bytes, bytearray)) or not isinstance(
            self.bodies, Sequence
        ):
            raise TypeError("bodies must be a sequence")
        bodies = tuple(self.bodies)
        if not bodies or any(not isinstance(body, BackendBody) for body in bodies):
            raise ValueError("bodies must contain BackendBody values")
        if len({body.body_id for body in bodies}) != len(bodies):
            raise ValueError("bodies must not contain duplicate IDs")
        all_faces = [face for body in bodies for face in body.faces]
        if any(face.frame != self.frame for face in all_faces):
            raise ValueError("all face frames must match the inspection frame")
        if len({face.face_id for face in all_faces}) != len(all_faces):
            raise ValueError("face IDs must be unique across the inspection")
        object.__setattr__(self, "bodies", bodies)
        object.__setattr__(
            self,
            "unsupported_topology",
            _text_sequence(self.unsupported_topology, "unsupported_topology"),
        )
        object.__setattr__(self, "defects", _text_sequence(self.defects, "defects"))

    def to_dict(self) -> dict[str, object]:
        return {
            "source_digest": self.source_digest,
            "geometry_digest": self.geometry_digest,
            "declared_units": sorted(self.declared_units),
            "frame": self.frame.value,
            "bodies": [
                body.to_dict() for body in sorted(self.bodies, key=lambda item: item.body_id)
            ],
            "unsupported_topology": sorted(self.unsupported_topology),
            "defects": sorted(self.defects),
        }


@dataclass(frozen=True, slots=True)
class BackendNode:
    """One backend mesh node in SI coordinates in an explicit frame."""

    node_id: int
    coordinates_si: Sequence[float]

    def __post_init__(self) -> None:
        object.__setattr__(self, "node_id", _positive_int(self.node_id, "node_id"))
        object.__setattr__(
            self, "coordinates_si", _coordinates(self.coordinates_si, "coordinates_si")
        )

    def to_dict(self) -> dict[str, object]:
        return {"node_id": self.node_id, "coordinates_si": list(self.coordinates_si)}


@dataclass(frozen=True, slots=True)
class BackendElement:
    """One backend Tet10 element with an explicit node-ordering identity."""

    element_id: int
    element_type: str
    node_ids: Sequence[int]
    body_id: str
    ordering_id: str = BACKEND_TET10_ORDER_ID

    def __post_init__(self) -> None:
        object.__setattr__(self, "element_id", _positive_int(self.element_id, "element_id"))
        element_type = _text(self.element_type, "element_type").lower()
        if element_type != "tet10":
            raise ValueError("element_type must be 'tet10'")
        object.__setattr__(self, "element_type", element_type)
        if isinstance(self.node_ids, (str, bytes, bytearray)) or not isinstance(
            self.node_ids, Sequence
        ):
            raise TypeError("node_ids must be a sequence")
        node_ids = tuple(_positive_int(item, "node_ids[]") for item in self.node_ids)
        if len(node_ids) != 10 or len(set(node_ids)) != 10:
            raise ValueError("Tet10 node_ids must contain ten distinct nodes")
        object.__setattr__(self, "node_ids", node_ids)
        object.__setattr__(self, "body_id", _text(self.body_id, "body_id"))
        object.__setattr__(self, "ordering_id", _text(self.ordering_id, "ordering_id"))

    def to_dict(self) -> dict[str, object]:
        return {
            "element_id": self.element_id,
            "element_type": self.element_type,
            "node_ids": list(self.node_ids),
            "body_id": self.body_id,
            "ordering_id": self.ordering_id,
        }


@dataclass(frozen=True, slots=True)
class BackendMeshFace:
    """One backend face with explicit adjacent element/local-face references.

    ``local_face_ids`` use the four-face numbering in the frozen domain
    contract.  ``source_face_id`` links a generated surface facet back to its
    CAD boundary face when a native surface is tessellated into many facets.
    """

    face_id: str
    adjacent_element_ids: Sequence[int]
    local_face_ids: Sequence[int]
    area_si: float | None = None
    centroid_si: Sequence[float] | None = None
    boundary_points_si: Sequence[Sequence[float]] = ()
    source_face_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "face_id", _text(self.face_id, "face_id"))
        if isinstance(self.adjacent_element_ids, (str, bytes, bytearray)) or not isinstance(
            self.adjacent_element_ids, Sequence
        ):
            raise TypeError("adjacent_element_ids must be a sequence")
        adjacent = tuple(
            _positive_int(item, "adjacent_element_ids[]") for item in self.adjacent_element_ids
        )
        if not adjacent or len(adjacent) > 2 or len(set(adjacent)) != len(adjacent):
            raise ValueError("a backend face must have one or two distinct adjacent elements")
        object.__setattr__(self, "adjacent_element_ids", adjacent)
        if isinstance(self.local_face_ids, (str, bytes, bytearray)) or not isinstance(
            self.local_face_ids, Sequence
        ):
            raise TypeError("local_face_ids must be a sequence")
        local = tuple(item for item in self.local_face_ids)
        if len(local) != len(adjacent) or any(
            isinstance(item, bool) or not isinstance(item, int) or not 0 <= item < 4
            for item in local
        ):
            raise ValueError("local_face_ids must align with adjacent elements and be 0..3")
        object.__setattr__(self, "local_face_ids", local)
        if self.area_si is not None:
            area = _finite(self.area_si, "area_si")
            if area <= 0.0:
                raise ValueError("area_si must be positive")
            object.__setattr__(self, "area_si", area)
        if self.centroid_si is not None:
            object.__setattr__(self, "centroid_si", _coordinates(self.centroid_si, "centroid_si"))
        object.__setattr__(
            self,
            "boundary_points_si",
            _point_sequence(self.boundary_points_si, "boundary_points_si"),
        )
        if self.source_face_id is not None:
            object.__setattr__(self, "source_face_id", _text(self.source_face_id, "source_face_id"))

    def to_dict(self) -> dict[str, object]:
        return {
            "face_id": self.face_id,
            "adjacent_element_ids": list(self.adjacent_element_ids),
            "local_face_ids": list(self.local_face_ids),
            "area_si": self.area_si,
            "centroid_si": None if self.centroid_si is None else list(self.centroid_si),
            "boundary_points_si": [list(point) for point in self.boundary_points_si],
            "source_face_id": self.source_face_id,
        }


@dataclass(frozen=True, slots=True)
class BackendMesh:
    """One backend Tet10 mesh in SI coordinates."""

    source_digest: str
    geometry_digest: str
    frame: FrameId
    body_id: str
    nodes: Sequence[BackendNode]
    elements: Sequence[BackendElement]
    faces: Sequence[BackendMeshFace]
    ordering_id: str = BACKEND_TET10_ORDER_ID

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_digest", _digest(self.source_digest, "source_digest"))
        object.__setattr__(
            self, "geometry_digest", _digest(self.geometry_digest, "geometry_digest")
        )
        if not isinstance(self.frame, FrameId):
            raise TypeError("frame must be a FrameId")
        object.__setattr__(self, "body_id", _text(self.body_id, "body_id"))
        if isinstance(self.nodes, (str, bytes, bytearray)) or not isinstance(self.nodes, Sequence):
            raise TypeError("nodes must be a sequence")
        nodes = tuple(self.nodes)
        if not nodes or any(not isinstance(node, BackendNode) for node in nodes):
            raise ValueError("nodes must contain BackendNode values")
        if len({node.node_id for node in nodes}) != len(nodes):
            raise ValueError("nodes must not contain duplicate IDs")
        object.__setattr__(self, "nodes", nodes)
        if isinstance(self.elements, (str, bytes, bytearray)) or not isinstance(
            self.elements, Sequence
        ):
            raise TypeError("elements must be a sequence")
        elements = tuple(self.elements)
        if not elements or any(not isinstance(element, BackendElement) for element in elements):
            raise ValueError("elements must contain BackendElement values")
        if len({element.element_id for element in elements}) != len(elements):
            raise ValueError("elements must not contain duplicate IDs")
        if any(element.body_id != self.body_id for element in elements):
            raise ValueError("elements must belong to the mesh body")
        node_ids = {node.node_id for node in nodes}
        if any(node_id not in node_ids for element in elements for node_id in element.node_ids):
            raise ValueError("elements reference unknown nodes")
        if any(element.ordering_id != self.ordering_id for element in elements):
            raise ValueError("element ordering IDs must match mesh ordering_id")
        object.__setattr__(self, "elements", elements)
        if isinstance(self.faces, (str, bytes, bytearray)) or not isinstance(self.faces, Sequence):
            raise TypeError("faces must be a sequence")
        faces = tuple(self.faces)
        if not faces or any(not isinstance(face, BackendMeshFace) for face in faces):
            raise ValueError("faces must contain BackendMeshFace values")
        if len({face.face_id for face in faces}) != len(faces):
            raise ValueError("faces must not contain duplicate IDs")
        element_ids = {element.element_id for element in elements}
        if any(
            element_id not in element_ids
            for face in faces
            for element_id in face.adjacent_element_ids
        ):
            raise ValueError("faces reference unknown elements")
        object.__setattr__(self, "faces", faces)
        object.__setattr__(self, "ordering_id", _text(self.ordering_id, "ordering_id"))

    def to_dict(self) -> dict[str, object]:
        return {
            "source_digest": self.source_digest,
            "geometry_digest": self.geometry_digest,
            "frame": self.frame.value,
            "body_id": self.body_id,
            "nodes": [node.to_dict() for node in sorted(self.nodes, key=lambda item: item.node_id)],
            "elements": [
                element.to_dict()
                for element in sorted(self.elements, key=lambda item: item.element_id)
            ],
            "faces": [face.to_dict() for face in sorted(self.faces, key=lambda item: item.face_id)],
            "ordering_id": self.ordering_id,
        }


@runtime_checkable
class GeometryMeshBackend(Protocol):
    """Minimal backend contract consumed by ``StepGeometryMeshAdapter``."""

    backend_id: str
    backend_version: str

    def inspect(self, content: bytes, requested_body_ids: Sequence[str]) -> BackendInspection:
        """Inspect exact source bytes and return backend geometry facts."""

    def mesh(self, content: bytes, body_id: str, global_size_si: float) -> BackendMesh:
        """Generate a Tet10 mesh for one body, with SI coordinates."""


__all__ = [
    "BACKEND_TET10_ORDER_ID",
    "BACKEND_TET10_TO_CANONICAL_POSITIONS",
    "BackendBody",
    "BackendElement",
    "BackendError",
    "BackendErrorCategory",
    "BackendFace",
    "BackendInspection",
    "BackendMesh",
    "BackendMeshFace",
    "BackendNode",
    "GeometryMeshBackend",
]
