"""Immutable geometry, mesh, and file-entry contracts for the common boundary."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

from .canonical import canonical_bytes
from .spatial import FrameId

SCHEMA_VERSION = "1"
TET10_NODE_ORDER_ID: Final[str] = "tet10-canonical-v1"
TET10_FACE_ORDER_ID: Final[str] = "tet10-face-canonical-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DIGEST_FIELDS = frozenset({"content_digest", "inspection_digest", "mesh_recipe_digest"})


class ArtifactValidationError(ValueError):
    """Raised when a geometry, mesh, or file-entry value is structurally invalid."""


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ArtifactValidationError(
            f"{field_name} must be non-empty text without surrounding whitespace"
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ArtifactValidationError(f"{field_name} contains a control character")
    return value


def _digest(value: object, field_name: str) -> str:
    value = _text(value, field_name)
    if _SHA256.fullmatch(value) is None:
        raise ArtifactValidationError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def _finite(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ArtifactValidationError(f"{field_name} must be a finite number")
    try:
        result = float(value)
    except OverflowError as error:
        raise ArtifactValidationError(f"{field_name} must be finite") from error
    if not math.isfinite(result):
        raise ArtifactValidationError(f"{field_name} must be finite")
    return 0.0 if result == 0.0 else result


def _positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ArtifactValidationError(f"{field_name} must be a positive integer")
    return value


def _tuple_text(value: object, field_name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ArtifactValidationError(f"{field_name} must be a sequence")
    result = tuple(_text(item, f"{field_name}[]") for item in value)
    if not result:
        raise ArtifactValidationError(f"{field_name} must not be empty")
    if len(set(result)) != len(result):
        raise ArtifactValidationError(f"{field_name} must not contain duplicate values")
    return result


def _tuple_int(value: object, field_name: str, *, allow_empty: bool = False) -> tuple[int, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ArtifactValidationError(f"{field_name} must be a sequence")
    result = tuple(_positive_int(item, f"{field_name}[]") for item in value)
    if not allow_empty and not result:
        raise ArtifactValidationError(f"{field_name} must not be empty")
    if len(set(result)) != len(result):
        raise ArtifactValidationError(f"{field_name} must not contain duplicate values")
    return result


def _sha_projection(value: object) -> bytes:
    return canonical_bytes(value)


@dataclass(frozen=True, slots=True)
class SourceAssetRef:
    """Registered source identity usable before a CaseRevision exists."""

    asset_id: str
    content_digest: str
    media_type: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "asset_id", _text(self.asset_id, "asset_id"))
        object.__setattr__(self, "content_digest", _digest(self.content_digest, "content_digest"))
        object.__setattr__(self, "media_type", _text(self.media_type, "media_type"))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "asset_id": self.asset_id,
            "content_digest": self.content_digest,
            "media_type": self.media_type,
        }

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


@dataclass(frozen=True, slots=True)
class GeometryInspectionRequest:
    """Geometry-only inspection request with no physical CaseRevision dependency."""

    source_asset: SourceAssetRef
    requested_body_ids: Sequence[str] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.source_asset, SourceAssetRef):
            raise ArtifactValidationError("source_asset must be a SourceAssetRef")
        requested = tuple(_text(item, "requested_body_ids[]") for item in self.requested_body_ids)
        if len(set(requested)) != len(requested):
            raise ArtifactValidationError("requested_body_ids must not contain duplicates")
        object.__setattr__(self, "requested_body_ids", requested)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "source_asset": self.source_asset.to_dict(),
            "requested_body_ids": list(self.requested_body_ids),
        }

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


@dataclass(frozen=True, slots=True)
class GeometryInspection:
    """Geometry-only inspection result; it does not assign physical meaning."""

    source_asset: SourceAssetRef
    inspection_digest: str
    declared_unit: str
    body_ids: Sequence[str]
    closed_solid_body_ids: Sequence[str]

    def __post_init__(self) -> None:
        if not isinstance(self.source_asset, SourceAssetRef):
            raise ArtifactValidationError("source_asset must be a SourceAssetRef")
        object.__setattr__(
            self, "inspection_digest", _digest(self.inspection_digest, "inspection_digest")
        )
        object.__setattr__(self, "declared_unit", _text(self.declared_unit, "declared_unit"))
        bodies = _tuple_text(self.body_ids, "body_ids")
        closed = tuple(
            _text(item, "closed_solid_body_ids[]") for item in self.closed_solid_body_ids
        )
        if not set(closed).issubset(bodies):
            raise ArtifactValidationError("closed_solid_body_ids must be a subset of body_ids")
        object.__setattr__(self, "body_ids", bodies)
        object.__setattr__(self, "closed_solid_body_ids", closed)

    @property
    def body_count(self) -> int:
        return len(self.body_ids)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "source_asset": self.source_asset.to_dict(),
            "inspection_digest": self.inspection_digest,
            "declared_unit": self.declared_unit,
            "body_ids": list(self.body_ids),
            "closed_solid_body_ids": list(self.closed_solid_body_ids),
        }

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


@dataclass(frozen=True, slots=True)
class MeshNode:
    """One node in canonical SI coordinates relative to the artifact frame."""

    node_id: int
    coordinates_si: Sequence[float]

    def __post_init__(self) -> None:
        object.__setattr__(self, "node_id", _positive_int(self.node_id, "node_id"))
        if isinstance(self.coordinates_si, (str, bytes, bytearray)) or not isinstance(
            self.coordinates_si, Sequence
        ):
            raise ArtifactValidationError("coordinates_si must be a sequence")
        if len(self.coordinates_si) != 3:
            raise ArtifactValidationError("coordinates_si must have exactly three components")
        object.__setattr__(
            self,
            "coordinates_si",
            tuple(
                _finite(item, f"coordinates_si[{index}]")
                for index, item in enumerate(self.coordinates_si)
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "node_id": self.node_id,
            "coordinates_si": list(self.coordinates_si),
        }


@dataclass(frozen=True, slots=True)
class MeshElement:
    """One element with tool-independent canonical connectivity."""

    element_id: int
    element_type: str
    node_ids: Sequence[int]

    def __post_init__(self) -> None:
        object.__setattr__(self, "element_id", _positive_int(self.element_id, "element_id"))
        element_type = _text(self.element_type, "element_type").lower()
        if element_type != "tet10":
            raise ArtifactValidationError("element_type must be 'tet10' in schema v1")
        object.__setattr__(self, "element_type", element_type)
        node_ids = _tuple_int(self.node_ids, "node_ids")
        if len(node_ids) != 10:
            raise ArtifactValidationError("tet10 node_ids must contain exactly 10 nodes")
        object.__setattr__(self, "node_ids", node_ids)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "element_id": self.element_id,
            "element_type": self.element_type,
            "node_ids": list(self.node_ids),
        }


@dataclass(frozen=True, slots=True)
class MeshFace:
    """One oriented face and its explicit owning element/local-face references."""

    face_id: str
    body_id: str
    node_ids: Sequence[int]
    adjacent_element_ids: Sequence[int]
    local_face_ids: Sequence[int]

    def __post_init__(self) -> None:
        object.__setattr__(self, "face_id", _text(self.face_id, "face_id"))
        object.__setattr__(self, "body_id", _text(self.body_id, "body_id"))
        node_ids = _tuple_int(self.node_ids, "node_ids")
        if len(node_ids) != 6:
            raise ArtifactValidationError("tet10 face node_ids must contain exactly 6 nodes")
        object.__setattr__(self, "node_ids", node_ids)
        elements = _tuple_int(self.adjacent_element_ids, "adjacent_element_ids")
        object.__setattr__(self, "adjacent_element_ids", elements)
        if isinstance(self.local_face_ids, (str, bytes, bytearray)) or not isinstance(
            self.local_face_ids, Sequence
        ):
            raise ArtifactValidationError("local_face_ids must be a sequence")
        local = tuple(item for item in self.local_face_ids)
        if not local or len(local) != len(elements):
            raise ArtifactValidationError("local_face_ids must align with adjacent_element_ids")
        if any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in local):
            raise ArtifactValidationError("local_face_ids must contain nonnegative integers")
        object.__setattr__(self, "local_face_ids", local)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "face_id": self.face_id,
            "body_id": self.body_id,
            "node_ids": list(self.node_ids),
            "adjacent_element_ids": list(self.adjacent_element_ids),
            "local_face_ids": list(self.local_face_ids),
        }


@dataclass(frozen=True, slots=True)
class MeshSet:
    """A named typed set of canonical mesh IDs."""

    set_id: str
    kind: str
    body_id: str
    member_ids: Sequence[int]

    def __post_init__(self) -> None:
        object.__setattr__(self, "set_id", _text(self.set_id, "set_id"))
        kind = _text(self.kind, "kind").lower()
        if kind not in {"node", "element", "face", "body"}:
            raise ArtifactValidationError("mesh set kind must be node, element, face, or body")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "body_id", _text(self.body_id, "body_id"))
        object.__setattr__(self, "member_ids", _tuple_int(self.member_ids, "member_ids"))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "set_id": self.set_id,
            "kind": self.kind,
            "body_id": self.body_id,
            "member_ids": list(self.member_ids),
        }


@dataclass(frozen=True, slots=True)
class MeshQualityRecord:
    """One structural mesh measurement; it is not a physical success authority."""

    metric_id: str
    value: float
    unit: str
    threshold: float | None
    status: str
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "metric_id", _text(self.metric_id, "metric_id"))
        object.__setattr__(self, "value", _finite(self.value, "value"))
        object.__setattr__(self, "unit", _text(self.unit, "unit"))
        if self.threshold is not None:
            object.__setattr__(self, "threshold", _finite(self.threshold, "threshold"))
        status = _text(self.status, "status").upper()
        if status not in {"PASS", "FAIL", "UNVERIFIED", "NOT_APPLICABLE"}:
            raise ArtifactValidationError("mesh quality status is unsupported")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "reason", _text(self.reason, "reason"))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "metric_id": self.metric_id,
            "value": self.value,
            "unit": self.unit,
            "threshold": self.threshold,
            "status": self.status,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class MeshProvenance:
    """Source, recipe, tool, and canonical mapping identities for a mesh."""

    source_geometry_digest: str
    source_body_ids: Sequence[str]
    source_selection_digests: Sequence[str]
    mesh_recipe_digest: str
    tool_id: str
    tool_version: str
    mapping_id: str
    node_ordering_id: str
    face_ordering_id: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_geometry_digest",
            _digest(self.source_geometry_digest, "source_geometry_digest"),
        )
        object.__setattr__(
            self, "source_body_ids", _tuple_text(self.source_body_ids, "source_body_ids")
        )
        selection_digests = tuple(
            _digest(item, "source_selection_digests[]") for item in self.source_selection_digests
        )
        object.__setattr__(self, "source_selection_digests", selection_digests)
        object.__setattr__(
            self, "mesh_recipe_digest", _digest(self.mesh_recipe_digest, "mesh_recipe_digest")
        )
        object.__setattr__(self, "tool_id", _text(self.tool_id, "tool_id"))
        object.__setattr__(self, "tool_version", _text(self.tool_version, "tool_version"))
        object.__setattr__(self, "mapping_id", _text(self.mapping_id, "mapping_id"))
        object.__setattr__(
            self, "node_ordering_id", _text(self.node_ordering_id, "node_ordering_id")
        )
        object.__setattr__(
            self, "face_ordering_id", _text(self.face_ordering_id, "face_ordering_id")
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "source_geometry_digest": self.source_geometry_digest,
            "source_body_ids": list(self.source_body_ids),
            "source_selection_digests": list(self.source_selection_digests),
            "mesh_recipe_digest": self.mesh_recipe_digest,
            "tool_id": self.tool_id,
            "tool_version": self.tool_version,
            "mapping_id": self.mapping_id,
            "node_ordering_id": self.node_ordering_id,
            "face_ordering_id": self.face_ordering_id,
        }


@dataclass(frozen=True, slots=True)
class FileEntry:
    """Immutable logical file identity shared by bundle, attempt, and manifest."""

    logical_path: str
    digest: str
    size_bytes: int
    role: str

    def __post_init__(self) -> None:
        path = _text(self.logical_path, "logical_path")
        if "\\" in path or path.startswith(("/", "//")):
            raise ArtifactValidationError("logical_path must use relative POSIX separators")
        if re.match(r"^[A-Za-z]:", path) or ":" in path.split("/", 1)[0]:
            raise ArtifactValidationError("logical_path must not be drive-qualified")
        parts = path.split("/")
        if any(not part or part in {".", ".."} for part in parts):
            raise ArtifactValidationError(
                "logical_path contains an ambiguous or escaping component"
            )
        object.__setattr__(self, "logical_path", path)
        object.__setattr__(self, "digest", _digest(self.digest, "digest"))
        if (
            isinstance(self.size_bytes, bool)
            or not isinstance(self.size_bytes, int)
            or self.size_bytes < 0
        ):
            raise ArtifactValidationError("size_bytes must be a nonnegative integer")
        object.__setattr__(self, "role", _text(self.role, "role"))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "logical_path": self.logical_path,
            "digest": self.digest,
            "size_bytes": self.size_bytes,
            "role": self.role,
        }


@dataclass(frozen=True, slots=True)
class MeshArtifact:
    """Canonical immutable mesh snapshot; construction does not prove native validity."""

    artifact_id: str
    frame: FrameId
    provenance: MeshProvenance
    nodes: Sequence[MeshNode]
    elements: Sequence[MeshElement]
    faces: Sequence[MeshFace]
    sets: Sequence[MeshSet]
    quality_records: Sequence[MeshQualityRecord]
    artifact_digest: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifact_id", _text(self.artifact_id, "artifact_id"))
        if not isinstance(self.frame, FrameId):
            raise ArtifactValidationError("frame must be a FrameId")
        if not isinstance(self.provenance, MeshProvenance):
            raise ArtifactValidationError("provenance must be a MeshProvenance")
        nodes = tuple(self.nodes)
        elements = tuple(self.elements)
        faces = tuple(self.faces)
        sets = tuple(self.sets)
        quality_records = tuple(self.quality_records)
        if not nodes or not elements:
            raise ArtifactValidationError("mesh must contain nodes and elements")
        for collection, item_type, name in (
            (nodes, MeshNode, "nodes"),
            (elements, MeshElement, "elements"),
            (faces, MeshFace, "faces"),
            (sets, MeshSet, "sets"),
            (quality_records, MeshQualityRecord, "quality_records"),
        ):
            if any(not isinstance(item, item_type) for item in collection):
                raise ArtifactValidationError(f"{name} contains an invalid item")
        for collection, key, name in (
            (nodes, lambda item: item.node_id, "nodes"),
            (elements, lambda item: item.element_id, "elements"),
            (faces, lambda item: item.face_id, "faces"),
            (sets, lambda item: item.set_id, "sets"),
        ):
            identifiers = [key(item) for item in collection]
            if len(set(identifiers)) != len(identifiers):
                raise ArtifactValidationError(f"{name} contains duplicate IDs")
        node_ids = {item.node_id for item in nodes}
        element_ids = {item.element_id for item in elements}
        for element in elements:
            if any(node_id not in node_ids for node_id in element.node_ids):
                raise ArtifactValidationError("element references an unknown node")
        for face in faces:
            if any(node_id not in node_ids for node_id in face.node_ids):
                raise ArtifactValidationError("face references an unknown node")
            if any(element_id not in element_ids for element_id in face.adjacent_element_ids):
                raise ArtifactValidationError("face references an unknown element")
        for item in sets:
            if item.kind == "node" and any(member not in node_ids for member in item.member_ids):
                raise ArtifactValidationError("node set references an unknown node")
            if item.kind == "element" and any(
                member not in element_ids for member in item.member_ids
            ):
                raise ArtifactValidationError("element set references an unknown element")
            if item.kind == "face" and any(
                member not in {face_id for face_id in (face.face_id for face in faces)}
                for member in item.member_ids
            ):
                raise ArtifactValidationError(
                    "face set member IDs must be resolved by a face set adapter"
                )
        object.__setattr__(self, "nodes", nodes)
        object.__setattr__(self, "elements", elements)
        object.__setattr__(self, "faces", faces)
        object.__setattr__(self, "sets", sets)
        object.__setattr__(self, "quality_records", quality_records)
        digest = hashlib.sha256(canonical_bytes(self._projection(include_digest=False))).hexdigest()
        object.__setattr__(self, "artifact_digest", digest)

    def _projection(self, *, include_digest: bool) -> dict[str, object]:
        result: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "artifact_id": self.artifact_id,
            "frame": self.frame.value,
            "provenance": self.provenance.to_dict(),
            "nodes": [item.to_dict() for item in self.nodes],
            "elements": [item.to_dict() for item in self.elements],
            "faces": [item.to_dict() for item in self.faces],
            "sets": [item.to_dict() for item in self.sets],
            "quality_records": [item.to_dict() for item in self.quality_records],
        }
        if include_digest:
            result["artifact_digest"] = self.artifact_digest
        return result

    def to_dict(self) -> dict[str, object]:
        return self._projection(include_digest=True)

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


__all__ = [
    "SCHEMA_VERSION",
    "TET10_FACE_ORDER_ID",
    "TET10_NODE_ORDER_ID",
    "ArtifactValidationError",
    "FileEntry",
    "GeometryInspection",
    "GeometryInspectionRequest",
    "MeshArtifact",
    "MeshElement",
    "MeshFace",
    "MeshNode",
    "MeshProvenance",
    "MeshQualityRecord",
    "MeshSet",
    "SourceAssetRef",
]
