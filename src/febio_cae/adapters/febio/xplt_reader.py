"""Registered immutable XPLT snapshots and the observed 0x35/no-compression subset.

The trusted caller registers bytes/mesh/state/entity bindings before reading.
Native headers never authorize an attempt. Unsupported layouts fail closed.
"""

from __future__ import annotations

import hashlib
import math
import struct
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from itertools import pairwise

from febio_cae.domain import (
    AttemptRecord,
    CapabilityStatus,
    CompatibilityProfile,
    ExecutionBundle,
    FileEntry,
    MeshArtifact,
    NumericResultData,
    OutputMapping,
    OutputObservation,
    PortError,
    PortErrorCategory,
    ReadResult,
    ReadStatus,
    ResolvedFileContent,
    ResultDataPort,
    ResultDataRef,
    ResultManifest,
    RunState,
)
from febio_cae.domain.canonical import canonical_bytes
from febio_cae.domain.codec import decode_record, encode_record

_OUTPUT_PATH = "output/results.xplt"
_CODEC_ID = "numeric-result-v1"
_MAX_FILE = 128 * 1024 * 1024
_MAX_BLOCK = 16 * 1024 * 1024
_MAX_BLOCKS = 900000
_NODE_DICTIONARY = 0x01023000
_DOMAIN_DICTIONARY = 0x01024000
_VECTOR = ("x", "y", "z")
_TENSOR = ("xx", "yy", "zz", "xy", "yz", "xz")
_LAYOUTS = {
    "displacement": (_NODE_DICTIONARY, 1, 0, "node", "VEC3F", "m", _VECTOR),
    "reaction forces": (_NODE_DICTIONARY, 1, 0, "node", "VEC3F", "N", _VECTOR),
    "stress": (_DOMAIN_DICTIONARY, 2, 1, "element", "MAT3FS", "Pa", _TENSOR),
    "rigid force": (_DOMAIN_DICTIONARY, 1, 3, "rigid_body", "VEC3F", "N", _VECTOR),
    "rigid position": (_DOMAIN_DICTIONARY, 1, 3, "rigid_body", "VEC3F", "m", _VECTOR),
}
_OBJECT_NAMES = (
    "Position",
    "Velocity",
    "Acceleration",
    "Euler angles (deg)",
    "Angular velocity",
    "Angular acceleration",
    "Force",
    "Moment",
)


class _ParseError(ValueError):
    pass


@dataclass(frozen=True)
class _Block:
    identifier: int
    payload: bytes


@dataclass(frozen=True)
class _Source:
    attempt: AttemptRecord
    bundle: ExecutionBundle
    raw: ResolvedFileContent
    mesh: MeshArtifact
    state_times: tuple[float, ...]
    part_bodies: tuple[tuple[int, str], ...]
    entity_ids: tuple[tuple[str, tuple[str, ...]], ...]


def _scope(attempt: AttemptRecord) -> tuple[str, str, str, int]:
    return attempt.case_id, attempt.run_id, attempt.attempt_id, attempt.owner_generation


def _uint(payload: bytes, label: str) -> int:
    if len(payload) != 4:
        raise _ParseError(f"{label}: expected uint32")
    return struct.unpack("<I", payload)[0]


def _uints(payload: bytes, label: str) -> tuple[int, ...]:
    if len(payload) % 4:
        raise _ParseError(f"{label}: invalid integer width")
    return tuple(item[0] for item in struct.iter_unpack("<I", payload))


def _floats(payload: bytes, width: int, label: str) -> tuple[float, ...]:
    if len(payload) != width * 4:
        raise _ParseError(f"{label}: invalid value layout/width")
    values = tuple(item[0] for item in struct.iter_unpack("<f", payload))
    if any(not math.isfinite(value) for value in values):
        raise _ParseError(f"{label}: nonfinite value")
    return values


def _float32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", value))[0]


def _text(payload: bytes, label: str, *, fixed: bool = False) -> str:
    if fixed:
        if len(payload) != 64 or b"\0" not in payload:
            raise _ParseError(f"{label}: invalid fixed-string layout")
        # Native writer padding can contain nonzero bytes after the first NUL.
        raw = payload.split(b"\0", 1)[0]
    else:
        if len(payload) < 4 or _uint(payload[:4], label) != len(payload) - 4:
            raise _ParseError(f"{label}: invalid counted-string layout")
        raw = payload[4:]
    value = raw.decode("utf-8")
    if not value or value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise _ParseError(f"{label}: invalid text")
    return value


class LocalResultDataStore(ResultDataPort):
    """Trusted source registration and strict-codec in-memory persistence.

    Registration is a controller/storage operation, not a proposed caller path.
    ResolvedFileContent is an immutable verified snapshot; this store makes no
    claim that a mutable filesystem alias is still current.
    """

    def __init__(self) -> None:
        self._sources: dict[tuple[str, str, str, int], _Source] = {}
        self._data: dict[str, bytes] = {}
        self._manifest_outputs: dict[tuple[str, str], ResultDataRef] = {}

    def register_source(
        self,
        attempt: AttemptRecord,
        bundle: ExecutionBundle,
        raw: ResolvedFileContent,
        *,
        mesh: MeshArtifact,
        state_times: Sequence[float],
        part_bodies: Mapping[int, str],
        entity_ids: Mapping[str, Sequence[str]],
    ) -> None:
        if attempt.state not in {RunState.VALIDATING, RunState.SUCCEEDED}:
            raise PortError(
                PortErrorCategory.CONFLICT, "source registration requires drained validation"
            )
        process = attempt.process
        if (
            process is None
            or (attempt.case_id, attempt.revision_id, attempt.bundle_digest)
            != (bundle.case_id, bundle.revision_id, bundle.bundle_digest)
            or mesh.artifact_digest != bundle.mesh_digest
            or tuple(process.argv) != tuple(bundle.argv)
            or process.executable != bundle.argv[0]
            or process.executable_digest != bundle.tool.executable_digest
            or process.thread_count != bundle.thread_count
        ):
            raise PortError(
                PortErrorCategory.INTEGRITY, "registered source execution/mesh binding mismatch"
            )
        if (
            raw.entry.logical_path != _OUTPUT_PATH
            or raw.entry.role != "result"
            or len(raw.content) > _MAX_FILE
        ):
            raise PortError(
                PortErrorCategory.INTEGRITY, "unsupported registered XPLT file identity or size"
            )
        ResolvedFileContent(raw.entry, raw.content)
        times = tuple(float(value) for value in state_times)
        if not times or any(not math.isfinite(value) or value < 0 for value in times):
            raise PortError(
                PortErrorCategory.INVALID_INPUT, "registered state policy must be finite"
            )
        if any(b <= a for a, b in pairwise(times)):
            raise PortError(
                PortErrorCategory.INVALID_INPUT, "registered state policy must increase"
            )
        bodies = tuple(sorted(part_bodies.items()))
        if (
            not bodies
            or any(type(key) is not int or key <= 0 for key, _ in bodies)
            or {body for _, body in bodies} != {element.body_id for element in mesh.elements}
        ):
            raise PortError(
                PortErrorCategory.INVALID_INPUT, "registered part/body mapping is incomplete"
            )
        entities = tuple(sorted((name, tuple(ids)) for name, ids in entity_ids.items()))
        if not entities or any(
            not ids
            or len(set(ids)) != len(ids)
            or any(not isinstance(item, str) or not item for item in ids)
            for _, ids in entities
        ):
            raise PortError(
                PortErrorCategory.INVALID_INPUT, "registered output entities are invalid"
            )
        source = _Source(attempt, bundle, raw, mesh, times, bodies, entities)
        previous = self._sources.get(_scope(attempt))
        if previous is not None and previous != source:
            raise PortError(PortErrorCategory.CONFLICT, "registered source is immutable")
        self._sources[_scope(attempt)] = source

    def source_for(self, attempt: AttemptRecord, bundle: ExecutionBundle) -> _Source:
        source = self._sources.get(_scope(attempt))
        if (
            source is None
            or bundle != source.bundle
            or replace(attempt, state=source.attempt.state) != source.attempt
        ):
            raise PortError(
                PortErrorCategory.INTEGRITY, "no exact registered source for this execution"
            )
        return source

    def resolve_file(
        self, entry: FileEntry, bundle: ExecutionBundle, attempt: AttemptRecord
    ) -> ResolvedFileContent:
        source = self.source_for(attempt, bundle)
        if entry != source.raw.entry:
            raise PortError(PortErrorCategory.INTEGRITY, "file differs from registered source")
        return ResolvedFileContent(entry, source.raw.content)

    def register(self, manifest_id: str, output_id: str, data: NumericResultData) -> None:
        data.verify_content_digest()
        encoded = encode_record(data)
        restored = decode_record(encoded, NumericResultData)
        if restored != data or output_id != data.mapping.canonical_id:
            raise PortError(PortErrorCategory.INTEGRITY, "numeric output registration mismatch")
        previous = self._data.get(data.reference.data_id)
        previous_ref = self._manifest_outputs.get((manifest_id, output_id))
        if (previous is not None and previous != encoded) or (
            previous_ref is not None and previous_ref != data.reference
        ):
            raise PortError(PortErrorCategory.CONFLICT, "registered numeric result is immutable")
        self._data[data.reference.data_id] = encoded
        self._manifest_outputs[(manifest_id, output_id)] = data.reference

    def resolve(self, reference: ResultDataRef) -> NumericResultData:
        payload = self._data.get(reference.data_id)
        if payload is None:
            raise PortError(
                PortErrorCategory.INTEGRITY, "numeric result reference is not registered"
            )
        data = decode_record(payload, NumericResultData)
        if data.reference != reference:
            raise PortError(PortErrorCategory.INTEGRITY, "numeric result reference does not match")
        data.verify_content_digest()
        return data

    def resolve_manifest_output(self, manifest_id: str, output_id: str) -> NumericResultData:
        reference = self._manifest_outputs.get((manifest_id, output_id))
        if reference is None:
            raise PortError(
                PortErrorCategory.INTEGRITY, f"numeric output is unavailable: {output_id}"
            )
        return self.resolve(reference)


@dataclass(frozen=True)
class _Variable:
    group: int
    index: int
    mapping: OutputMapping
    storage_format: int
    components: tuple[str, ...]


@dataclass(frozen=True)
class _Domain:
    part_id: int
    body_id: str
    element_ids: tuple[int, ...]


@dataclass(frozen=True)
class _Mesh:
    node_ids: tuple[int, ...]
    domains: tuple[_Domain, ...]
    objects: tuple[int, ...]


class _NativeParser:
    def __init__(self, source: _Source, profile: CompatibilityProfile) -> None:
        self.source = source
        self.profile = profile
        self.block_count = 0

    def blocks(self, payload: bytes) -> tuple[_Block, ...]:
        cursor = 0
        result = []
        while cursor < len(payload):
            if len(payload) - cursor < 8:
                raise _ParseError("truncated XPLT block header")
            tag, size = struct.unpack_from("<II", payload, cursor)
            cursor += 8
            if size > _MAX_BLOCK or cursor + size > len(payload):
                raise _ParseError("XPLT block exceeds parent bounds or size limit")
            self.block_count += 1
            if self.block_count > _MAX_BLOCKS:
                raise _ParseError("XPLT block count exceeds supported limit")
            result.append(_Block(tag, payload[cursor : cursor + size]))
            cursor += size
        return tuple(result)

    def fields(
        self, payload: bytes, required: set[int], optional: set[int] | None = None, *, label: str
    ) -> dict[int, bytes]:
        result: dict[int, bytes] = {}
        allowed = required | (optional or set())
        for item in self.blocks(payload):
            if item.identifier not in allowed or item.identifier in result:
                raise _ParseError(f"{label}: unknown or duplicate tag 0x{item.identifier:08x}")
            result[item.identifier] = item.payload
        if not required <= result.keys():
            raise _ParseError(f"{label}: missing required fields")
        return result

    def parse(
        self,
    ) -> tuple[tuple[_Variable, ...], tuple[float, ...], tuple[dict[str, tuple[float, ...]], ...]]:
        content = self.source.raw.content
        if len(content) < 4 or struct.unpack_from("<I", content)[0] != 0x00464542:
            raise _ParseError("invalid XPLT magic")
        top = self.blocks(content[4:])
        if len(top) < 3 or [item.identifier for item in top[:2]] != [0x01000000, 0x01040000]:
            raise _ParseError("missing or unordered XPLT root/mesh/state")
        if any(item.identifier != 0x02000000 for item in top[2:]):
            raise _ParseError("unknown XPLT state tag")
        root = self.fields(top[0].payload, {0x01010000, 0x01020000}, label="root")
        header = self.fields(
            root[0x01010000], {0x01010001, 0x01010004, 0x01010006}, {0x01010007}, label="header"
        )
        if _uint(header[0x01010001], "version") != 0x35:
            raise _ParseError("unsupported XPLT version")
        if _uint(header[0x01010004], "compression") != 0:
            raise _ParseError("unsupported XPLT compression")
        if _text(header[0x01010006], "software") != "FEBio 4.12.0":
            raise _ParseError("XPLT software differs from registered version")
        if 0x01010007 in header and _text(header[0x01010007], "units") != "SI":
            raise _ParseError("unsupported XPLT unit system")
        variables = self.dictionary(root[0x01020000])
        mesh = self.mesh(top[1].payload)
        states = tuple(self.state(item.payload, variables, mesh) for item in top[2:])
        times = tuple(time for time, _ in states)
        if any(b <= a for a, b in pairwise(times)):
            raise _ParseError("XPLT state times are not strictly increasing")
        required_times = tuple(_float32(time) for time in self.source.state_times)
        if len(set(required_times)) != len(required_times) or not set(required_times) <= set(times):
            raise _ParseError("XPLT does not cover the registered required state times")
        return variables, times, tuple(values for _, values in states)

    def dictionary(self, payload: bytes) -> tuple[_Variable, ...]:
        groups = self.fields(
            payload, set(), {_NODE_DICTIONARY, _DOMAIN_DICTIONARY}, label="dictionary layout"
        )
        mappings = {mapping.native_name: mapping for mapping in self.profile.output_mappings}
        if len(mappings) != len(self.profile.output_mappings):
            raise _ParseError("ambiguous native output mapping")
        variables: list[_Variable] = []
        for group, data in groups.items():
            for index, item in enumerate(self.blocks(data), 1):
                if item.identifier != 0x01020001:
                    raise _ParseError("unknown dictionary item layout")
                fields = self.fields(
                    item.payload,
                    {0x01020002, 0x01020003, 0x01020004, 0x01020005},
                    {0x01020007},
                    label="dictionary item",
                )
                name = _text(fields[0x01020004], "dictionary name", fixed=True)
                layout = _LAYOUTS.get(name)
                mapping = mappings.get(name)
                actual = (
                    group,
                    _uint(fields[0x01020002], "type"),
                    _uint(fields[0x01020003], "format"),
                )
                if layout is None or mapping is None or actual != layout[:3]:
                    raise _ParseError(f"unsupported dictionary layout/location: {name}")
                if _uint(fields[0x01020005], "array size") != 0:
                    raise _ParseError("unsupported array dictionary layout")
                if 0x01020007 in fields and len(fields[0x01020007]) != 64:
                    raise _ParseError("invalid observed dictionary ancillary width")
                if (mapping.location, mapping.value_type, mapping.unit) != layout[
                    3:6
                ] or mapping.frame != self.source.mesh.frame:
                    raise _ParseError(f"registered mapping differs from native layout: {name}")
                variables.append(_Variable(group, index, mapping, actual[2], layout[6]))
        names = [item.mapping.native_name for item in variables]
        if len(set(names)) != len(names) or set(names) != set(mappings):
            raise _ParseError("dictionary missing or duplicating registered outputs")
        if {item.mapping.canonical_id for item in variables} != dict(self.source.entity_ids).keys():
            raise _ParseError("registered output entity policy differs from dictionary")
        return tuple(variables)

    def mesh(self, payload: bytes) -> _Mesh:
        fields = self.fields(
            payload,
            {0x01041000, 0x01042000, 0x01045000},
            {0x01043000, 0x01044000, 0x01046000, 0x01047000, 0x01050000},
            label="mesh",
        )
        nodes = self.fields(fields[0x01041000], {0x01041100, 0x01041200}, label="nodes")
        header = self.fields(nodes[0x01041100], {0x01041101, 0x01041102}, label="node header")
        count = _uint(header[0x01041101], "node count")
        if (
            count == 0
            or _uint(header[0x01041102], "node dimension") != 3
            or len(nodes[0x01041200]) != count * 16
        ):
            raise _ParseError("invalid node coordinate layout")
        coordinates: dict[int, tuple[float, ...]] = {}
        for node_id, x, y, z in struct.iter_unpack("<Ifff", nodes[0x01041200]):
            if (
                node_id <= 0
                or node_id in coordinates
                or not all(math.isfinite(value) for value in (x, y, z))
            ):
                raise _ParseError("invalid/duplicate node identity or nonfinite coordinate")
            coordinates[node_id] = (x, y, z)
        expected_coordinates = {
            node.node_id: tuple(_float32(value) for value in node.coordinates_si)
            for node in self.source.mesh.nodes
        }
        if coordinates != expected_coordinates:
            raise _ParseError("XPLT node identities/coordinates differ from registered mesh")
        node_ids = tuple(coordinates)
        parts: dict[int, str] = {}
        for item in self.blocks(fields[0x01045000]):
            if item.identifier != 0x01045100:
                raise _ParseError("unknown part layout")
            part = self.fields(item.payload, {0x01045101, 0x01045102}, label="part")
            identifier = _uint(part[0x01045101], "part ID")
            if identifier <= 0 or identifier in parts:
                raise _ParseError("invalid/duplicate part ID")
            parts[identifier] = _text(part[0x01045102], "part name", fixed=True)
        part_bodies = dict(self.source.part_bodies)
        if parts.keys() != part_bodies.keys():
            raise _ParseError("mesh parts differ from registered part/body mapping")
        expected_elements = {element.element_id: element for element in self.source.mesh.elements}
        observed_elements: set[int] = set()
        domains: list[_Domain] = []
        for item in self.blocks(fields[0x01042000]):
            if item.identifier != 0x01042100:
                raise _ParseError("unsupported domain layout")
            domain = self.fields(item.payload, {0x01042101, 0x01042200}, label="domain")
            header = self.fields(
                domain[0x01042101],
                {0x01042102, 0x01042103, 0x01032104, 0x01032105},
                label="domain header",
            )
            if _uint(header[0x01042102], "element type") != 7:
                raise _ParseError("unsupported element layout: only observed Tet10")
            part_id = _uint(header[0x01042103], "domain part ID")
            if part_id not in part_bodies:
                raise _ParseError("domain references unknown part")
            _text(header[0x01032105], "domain name")
            element_ids = []
            for element in self.blocks(domain[0x01042200]):
                if element.identifier != 0x01042201 or len(element.payload) != 44:
                    raise _ParseError("invalid Tet10 element connectivity layout")
                element_id, *indices = _uints(element.payload, "connectivity")
                if any(index >= len(node_ids) for index in indices):
                    raise _ParseError("element connectivity index out of bounds")
                expected = expected_elements.get(element_id)
                if (
                    expected is None
                    or element_id in observed_elements
                    or expected.body_id != part_bodies[part_id]
                    or tuple(node_ids[index] for index in indices) != tuple(expected.node_ids)
                ):
                    raise _ParseError(
                        "element identity/body/connectivity differs from registered mesh"
                    )
                observed_elements.add(element_id)
                element_ids.append(element_id)
            if not element_ids or len(element_ids) != _uint(header[0x01032104], "element count"):
                raise _ParseError("domain element count mismatch")
            domains.append(_Domain(part_id, part_bodies[part_id], tuple(element_ids)))
        if observed_elements != expected_elements.keys():
            raise _ParseError("XPLT domain entities do not cover registered mesh")
        if 0x01044000 in fields:
            self.region_sets(fields[0x01044000], 0x01044000, set(range(len(node_ids))))
        if 0x01046000 in fields:
            self.region_sets(fields[0x01046000], 0x01046000, observed_elements)
        for tag in (0x01043000, 0x01047000):
            if tag in fields:
                self.surfaces(fields[tag], tag, len(node_ids))
        objects = self.objects(fields[0x01050000]) if 0x01050000 in fields else ()
        return _Mesh(node_ids, tuple(domains), objects)

    def region_sets(self, payload: bytes, base: int, allowed_members: set[int]) -> None:
        seen: set[int] = set()
        for item in self.blocks(payload):
            if item.identifier != base + 0x100:
                raise _ParseError("unknown mesh region set")
            fields = self.fields(item.payload, {base + 0x101, base + 0x200}, label="region set")
            header = self.fields(
                fields[base + 0x101],
                {base + 0x102, base + 0x103, base + 0x104},
                label="region header",
            )
            identifier = _uint(header[base + 0x102], "region ID")
            members = _uints(fields[base + 0x200], "region members")
            if (
                identifier <= 0
                or identifier in seen
                or len(members) != _uint(header[base + 0x104], "region count")
                or len(set(members)) != len(members)
                or not set(members) <= allowed_members
            ):
                raise _ParseError("invalid region membership/layout")
            seen.add(identifier)
            _text(header[base + 0x103], "region name")

    def surfaces(self, payload: bytes, base: int, node_count: int) -> None:
        seen: set[int] = set()
        for item in self.blocks(payload):
            if item.identifier != base + 0x100:
                raise _ParseError("unknown surface/facet-set layout")
            fields = self.fields(item.payload, {base + 0x101, base + 0x200}, label="surface")
            header = self.fields(
                fields[base + 0x101],
                {base + 0x102, base + 0x103, base + 0x104, base + 0x105},
                label="surface header",
            )
            identifier = _uint(header[base + 0x102], "surface ID")
            name_offset, count_offset = (0x104, 0x103) if base == 0x01043000 else (0x103, 0x104)
            _text(header[base + name_offset], "surface name")
            maximum = _uint(header[base + 0x105], "surface maximum width")
            if identifier <= 0 or identifier in seen or maximum not in {4, 6, 10}:
                raise _ParseError("unsupported surface identity/layout")
            seen.add(identifier)
            faces = self.blocks(fields[base + 0x200])
            if len(faces) != _uint(header[base + count_offset], "surface face count"):
                raise _ParseError("surface face count mismatch")
            face_ids: set[int] = set()
            for face in faces:
                if face.identifier != base + 0x201 or len(face.payload) != (maximum + 2) * 4:
                    raise _ParseError("unsupported surface connectivity layout")
                face_id, count, *indices = _uints(face.payload, "surface connectivity")
                if (
                    face_id <= 0
                    or face_id in face_ids
                    or count not in {4, 6}
                    or count > maximum
                    or any(index >= node_count for index in indices[:count])
                    or len(set(indices[:count])) != count
                ):
                    raise _ParseError("invalid surface connectivity")
                face_ids.add(face_id)

    def objects(self, payload: bytes) -> tuple[int, ...]:
        identifiers = []
        for item in self.blocks(payload):
            if item.identifier != 0x01051000:
                raise _ParseError("unsupported mesh object layout")
            children = self.blocks(item.payload)
            definitions = [block for block in children if block.identifier == 0x01050006]
            metadata = b"".join(
                struct.pack("<II", block.identifier, len(block.payload)) + block.payload
                for block in children
                if block.identifier != 0x01050006
            )
            fields = self.fields(
                metadata,
                {0x01050001, 0x01050002, 0x01050003, 0x01050004, 0x01050005},
                label="object metadata",
            )
            identifier = _uint(fields[0x01050001], "object ID")
            if (
                identifier <= 0
                or identifier in identifiers
                or _uint(fields[0x01050003], "object type") != 1
            ):
                raise _ParseError("unsupported object identity/type")
            identifiers.append(identifier)
            _text(fields[0x01050002], "object name")
            _floats(fields[0x01050004], 3, "object vector")
            _floats(fields[0x01050005], 4, "object quaternion")
            names = []
            for definition in definitions:
                fields = self.fields(
                    definition.payload,
                    {0x01020002, 0x01020003, 0x01020004, 0x01020005},
                    {0x01020007},
                    label="object dictionary",
                )
                if tuple(
                    _uint(fields[tag], "object dictionary layout")
                    for tag in (0x01020002, 0x01020003, 0x01020005)
                ) != (1, 1, 0):
                    raise _ParseError("unsupported object dictionary layout")
                if 0x01020007 in fields and len(fields[0x01020007]) != 64:
                    raise _ParseError("invalid object dictionary ancillary width")
                names.append(_text(fields[0x01020004], "object variable", fixed=True))
            if tuple(names) != _OBJECT_NAMES:
                raise _ParseError("unsupported object dictionary names/order")
        return tuple(identifiers)

    def state(
        self, payload: bytes, variables: tuple[_Variable, ...], mesh: _Mesh
    ) -> tuple[float, dict[str, tuple[float, ...]]]:
        fields = self.fields(
            payload, {0x02010000, 0x02020000, 0x02030000}, {0x02040000}, label="state"
        )
        header = self.fields(fields[0x02010000], {0x02010002, 0x02010003}, label="state header")
        time = _floats(header[0x02010002], 1, "state time")[0]
        if time < 0 or _uint(header[0x02010003], "state ancillary") != 0:
            raise _ParseError("unsupported state header layout")
        ancillary = self.fields(fields[0x02030000], {0x02030001}, label="state ancillary")
        values = _uints(ancillary[0x02030001], "state ancillary values")
        if len(values) != sum(len(domain.element_ids) for domain in mesh.domains) or set(
            values
        ) != {1}:
            raise _ParseError("unsupported observed state ancillary layout")
        if bool(mesh.objects) != (0x02040000 in fields):
            raise _ParseError("state object metadata/data mismatch")
        if mesh.objects:
            self.state_objects(fields[0x02040000], mesh.objects)
        category_tags = {0x02020300: _NODE_DICTIONARY, 0x02020400: _DOMAIN_DICTIONARY}
        categories = self.fields(fields[0x02020000], set(), set(category_tags), label="state data")
        result: dict[str, tuple[float, ...]] = {}
        expected_entities = dict(self.source.entity_ids)
        for tag, data in categories.items():
            known = {
                variable.index: variable
                for variable in variables
                if variable.group == category_tags[tag]
            }
            seen: set[int] = set()
            for item in self.blocks(data):
                if item.identifier != 0x02020001:
                    raise _ParseError("unknown state variable tag")
                fields = self.fields(item.payload, {0x02020002, 0x02020003}, label="state variable")
                index = _uint(fields[0x02020002], "variable ID")
                if index not in known or index in seen:
                    raise _ParseError("unknown/duplicate state variable ID")
                seen.add(index)
                variable = known[index]
                entities: dict[str, tuple[float, ...]] = {}
                regions: set[int] = set()
                for region in self.blocks(fields[0x02020003]):
                    if region.identifier in regions:
                        raise _ParseError("duplicate data region")
                    regions.add(region.identifier)
                    if variable.group == _NODE_DICTIONARY:
                        if region.identifier != 0:
                            raise _ParseError("unsupported node data region")
                        ids = tuple(str(value) for value in mesh.node_ids)
                    else:
                        if not 1 <= region.identifier <= len(mesh.domains):
                            raise _ParseError("unknown domain data region")
                        domain = mesh.domains[region.identifier - 1]
                        ids = (
                            (domain.body_id,)
                            if variable.storage_format == 3
                            else tuple(str(value) for value in domain.element_ids)
                        )
                    width = len(variable.components)
                    values_raw = _floats(region.payload, len(ids) * width, "state variable")
                    for offset, entity in enumerate(ids):
                        if entity in entities:
                            raise _ParseError("duplicate result entity across regions")
                        entities[entity] = values_raw[offset * width : (offset + 1) * width]
                output_id = variable.mapping.canonical_id
                expected = expected_entities[output_id]
                if set(entities) != set(expected):
                    raise _ParseError(
                        f"data region entities do not match registration: {output_id}"
                    )
                result[output_id] = tuple(
                    value for entity in expected for value in entities[entity]
                )
            if seen != known.keys():
                raise _ParseError("state is missing registered dictionary variables")
        if result.keys() != expected_entities.keys():
            raise _ParseError("state is missing registered output categories")
        return time, result

    def state_objects(self, payload: bytes, expected: tuple[int, ...]) -> None:
        seen: set[int] = set()
        for item in self.blocks(payload):
            if item.identifier != 0x01051000:
                raise _ParseError("unknown state object tag")
            fields = self.fields(
                item.payload,
                {0x01050001, 0x01050004, 0x01050005, 0x01051001, 0x01050006},
                label="state object",
            )
            identifier = _uint(fields[0x01050001], "state object ID")
            if identifier not in expected or identifier in seen:
                raise _ParseError("unknown/duplicate state object ID")
            seen.add(identifier)
            _floats(fields[0x01050004], 3, "state object vector")
            _floats(fields[0x01050005], 4, "state object quaternion")
            _floats(fields[0x01051001], 3, "state object ancillary")
            values = self.fields(
                fields[0x01050006], set(range(len(_OBJECT_NAMES))), label="state object values"
            )
            for data in values.values():
                _floats(data, 3, "state object data")
        if seen != set(expected):
            raise _ParseError("state object data is incomplete")


class XpltReaderAdapter:
    """Return manifest candidates from exactly registered supported source bytes."""

    def __init__(
        self, *, profile: CompatibilityProfile, data_store: LocalResultDataStore | None = None
    ) -> None:
        self.profile = profile
        self.data_store = data_store or LocalResultDataStore()

    def read(self, attempt: AttemptRecord, bundle: ExecutionBundle) -> ResultManifest:
        if attempt.state not in {RunState.VALIDATING, RunState.SUCCEEDED}:
            raise PortError(PortErrorCategory.CONFLICT, "XPLT cannot be read before validation")
        source = self.data_store.source_for(attempt, bundle)
        self._check_profile(bundle)
        resolved = self.data_store.resolve_file(source.raw.entry, bundle, attempt)
        if resolved != source.raw:
            raise PortError(
                PortErrorCategory.INTEGRITY, "resolved file differs from registered source"
            )
        try:
            variables, times, states = _NativeParser(source, self.profile).parse()
            manifest_id = hashlib.sha256(
                canonical_bytes(
                    {
                        "attempt": replace(attempt, state=RunState.VALIDATING).to_dict(),
                        "bundle": bundle.bundle_digest,
                        "file": resolved.entry.to_dict(),
                        "entities": {key: list(ids) for key, ids in source.entity_ids},
                        "states": list(source.state_times),
                        "parts": {str(key): body for key, body in source.part_bodies},
                    }
                )
            ).hexdigest()
            entities = dict(source.entity_ids)
            outputs: list[NumericResultData] = []
            for variable in variables:
                mapping = variable.mapping
                reference = ResultDataRef(
                    f"{manifest_id}:{mapping.canonical_id}",
                    "0" * 64,
                    _CODEC_ID,
                    _OUTPUT_PATH,
                    bundle.bundle_digest,
                    attempt.attempt_id,
                )
                sign = mapping.raw_sign * mapping.canonical_sign
                data = NumericResultData(
                    reference,
                    mapping,
                    "state_time",
                    "s",
                    times,
                    entities[mapping.canonical_id],
                    variable.components,
                    tuple(
                        tuple(value * sign for value in state[mapping.canonical_id])
                        for state in states
                    ),
                )
                data = replace(
                    data, reference=replace(reference, content_digest=data.expected_content_digest)
                )
                data.verify_content_digest()
                if decode_record(encode_record(data), NumericResultData) != data:
                    raise _ParseError("numeric codec round-trip failed")
                outputs.append(data)
            observations = tuple(
                OutputObservation(
                    data.mapping.canonical_id,
                    data.mapping.location,
                    data.mapping.value_type,
                    data.mapping.unit,
                    data.mapping.frame,
                    data.mapping.measure_id,
                    len(times),
                    data.reference,
                )
                for data in outputs
            )
            manifest = ResultManifest(
                manifest_id,
                attempt.attempt_id,
                bundle.bundle_digest,
                (resolved.entry,),
                ReadResult(ReadStatus.VALIDATED, self.profile.reader, observations, ()),
            )
            # Do not register partial output before all parser/policy/codec gates pass.
            for data in outputs:
                self.data_store.register(manifest_id, data.mapping.canonical_id, data)
            return manifest
        except (ValueError, TypeError, struct.error, OverflowError) as error:
            raise PortError(
                PortErrorCategory.INTEGRITY, f"unsupported or invalid XPLT: {error}"
            ) from error

    def _check_profile(self, bundle: ExecutionBundle) -> None:
        capability = next(
            (
                item
                for item in self.profile.capabilities
                if item.capability_id == "febio.output.xplt"
            ),
            None,
        )
        settings = {item.name: item.value for item in bundle.settings}
        if (
            bundle.tool != self.profile.solver
            or self.profile.solver.version != "4.12.0"
            or bundle.profile_id != self.profile.profile_id
            or settings.get("compatibility_profile_digest")
            != hashlib.sha256(self.profile.to_bytes()).hexdigest()
            or capability is None
            or capability.status is not CapabilityStatus.SUPPORTED
            or capability.version != "4.12.0/0x35"
            or capability.compression != "none"
            or not capability.evidence
        ):
            raise PortError(
                PortErrorCategory.UNSUPPORTED_CAPABILITY,
                "registered XPLT compatibility profile/version/compression mismatch",
            )


__all__ = ["LocalResultDataStore", "XpltReaderAdapter"]
