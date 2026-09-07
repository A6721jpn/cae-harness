"""Bounded binary FEBio XPLT reader for the observed 0x35/no-compression subset."""

from __future__ import annotations

import hashlib
import math
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

from febio_cae.domain import (
    AttemptRecord,
    CompatibilityProfile,
    ExecutionBundle,
    FileEntry,
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

_MAGIC: Final[int] = 0x00464542
_VERSION: Final[int] = 0x35
_COMPRESSION_NONE: Final[int] = 0
_OUTPUT_PATH: Final[str] = "output/results.xplt"
_CODEC_ID: Final[str] = "febio-xplt-0x35-v1"


@dataclass(frozen=True)
class _Block:
    identifier: int
    payload: bytes


class _ParseError(ValueError):
    pass


class _Cursor:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.offset = 0

    def block(self) -> _Block:
        if len(self.payload) - self.offset < 8:
            raise _ParseError("truncated XPLT block header")
        identifier, size = struct.unpack_from("<II", self.payload, self.offset)
        self.offset += 8
        end = self.offset + size
        if end > len(self.payload):
            raise _ParseError("XPLT block payload exceeds file bounds")
        payload = self.payload[self.offset : end]
        self.offset = end
        return _Block(identifier, payload)

    def all_blocks(self) -> tuple[_Block, ...]:
        blocks: list[_Block] = []
        while self.offset < len(self.payload):
            blocks.append(self.block())
        return tuple(blocks)


def _child_blocks(payload: bytes) -> tuple[_Block, ...]:
    return _Cursor(payload).all_blocks()


def _uint(payload: bytes, field: str) -> int:
    if len(payload) != 4:
        raise _ParseError(f"{field} is not a uint32 payload")
    return struct.unpack("<I", payload)[0]


def _text(payload: bytes, field: str) -> str:
    if len(payload) < 4:
        raise _ParseError(f"{field} text length is truncated")
    size = struct.unpack_from("<I", payload)[0]
    raw = payload[4:]
    if size != len(raw):
        raise _ParseError(f"{field} text length does not match payload")
    try:
        value = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise _ParseError(f"{field} is not UTF-8") from error
    if not value or value != value.strip():
        raise _ParseError(f"{field} is empty or padded")
    return value


class LocalResultDataStore(ResultDataPort):
    """In-memory decoded-data store used until application publication is wired."""

    def __init__(self) -> None:
        self._data: dict[str, NumericResultData] = {}
        self._manifest_outputs: dict[tuple[str, str], NumericResultData] = {}

    def register(self, manifest_id: str, output_id: str, data: NumericResultData) -> None:
        self._data[data.reference.data_id] = data
        self._manifest_outputs[(manifest_id, output_id)] = data

    def resolve_file(
        self, entry: FileEntry, bundle: ExecutionBundle, attempt: AttemptRecord
    ) -> ResolvedFileContent:
        if entry.logical_path != _OUTPUT_PATH:
            raise PortError(PortErrorCategory.INTEGRITY, "result file is not the fixed XPLT output")
        if attempt.bundle_digest != bundle.bundle_digest or attempt.process is None:
            raise PortError(
                PortErrorCategory.INTEGRITY, "attempt is not bound to the requested bundle"
            )
        path = Path(attempt.process.cwd) / Path(*entry.logical_path.split("/"))
        if not path.is_file() or path.is_symlink():
            raise PortError(PortErrorCategory.INTEGRITY, "fixed XPLT output is unavailable")
        content = path.read_bytes()
        resolved = ResolvedFileContent(
            FileEntry(
                entry.logical_path, hashlib.sha256(content).hexdigest(), len(content), entry.role
            ),
            content,
        )
        if resolved.entry.digest != entry.digest or resolved.entry.size_bytes != entry.size_bytes:
            raise PortError(PortErrorCategory.INTEGRITY, "fixed XPLT output digest or size changed")
        return resolved

    def resolve(self, reference: ResultDataRef) -> NumericResultData:
        data = self._data.get(reference.data_id)
        if data is None or data.reference != reference:
            raise PortError(
                PortErrorCategory.INTEGRITY, "numeric result reference is not registered"
            )
        data.verify_content_digest()
        return data

    def resolve_manifest_output(self, manifest_id: str, output_id: str) -> NumericResultData:
        data = self._manifest_outputs.get((manifest_id, output_id))
        if data is None:
            raise PortError(
                PortErrorCategory.INTEGRITY, f"numeric output is unavailable: {output_id}"
            )
        data.verify_content_digest()
        return data


class XpltReaderAdapter:
    """Read only the exact attempt output and only explicitly supported XPLT fields."""

    def __init__(
        self, *, profile: CompatibilityProfile, data_store: LocalResultDataStore | None = None
    ) -> None:
        self.profile = profile
        self.data_store = data_store or LocalResultDataStore()

    def read(self, attempt: AttemptRecord, bundle: ExecutionBundle) -> ResultManifest:
        if attempt.bundle_digest != bundle.bundle_digest:
            raise PortError(
                PortErrorCategory.INTEGRITY,
                "attempt bundle identity does not match requested bundle",
            )
        if attempt.state not in {RunState.VALIDATING, RunState.SUCCEEDED}:
            raise PortError(
                PortErrorCategory.CONFLICT,
                "XPLT cannot be read before the owned process reaches validation",
            )
        if attempt.process is None:
            raise PortError(PortErrorCategory.INTEGRITY, "attempt has no fixed output directory")
        path = Path(attempt.process.cwd) / Path(*_OUTPUT_PATH.split("/"))
        if not path.is_file() or path.is_symlink():
            raise PortError(PortErrorCategory.INTEGRITY, "fixed XPLT output is unavailable")
        before = path.stat()
        content = path.read_bytes()
        after = path.stat()
        if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
            raise PortError(PortErrorCategory.INTEGRITY, "XPLT changed while it was being read")
        digest = hashlib.sha256(content).hexdigest()
        entry = FileEntry(_OUTPUT_PATH, digest, len(content), "result")
        try:
            header, variables, node_ids, states = self._parse(content)
            self._check_header(header, attempt, bundle)
            manifest_id = hashlib.sha256(
                f"{attempt.attempt_id}|{bundle.bundle_digest}|{digest}".encode()
            ).hexdigest()
            observations: list[OutputObservation] = []
            for native_name, variable in variables.items():
                mapping = self._mapping(
                    native_name, variable["location"], variable["value_type"], variable["unit"]
                )
                raw_rows = [
                    cast(dict[str, tuple[float, ...]], state["values"])[native_name]
                    for state in states
                ]
                sign = mapping.raw_sign * mapping.canonical_sign
                values = tuple(tuple(float(value) * sign for value in row) for row in raw_rows)
                reference = ResultDataRef(
                    data_id=f"{manifest_id}:{mapping.canonical_id}",
                    content_digest="0" * 64,
                    codec_id=_CODEC_ID,
                    logical_path=_OUTPUT_PATH,
                    bundle_digest=bundle.bundle_digest,
                    attempt_id=attempt.attempt_id,
                )
                provisional = NumericResultData(
                    reference=reference,
                    mapping=mapping,
                    axis_id="state_time",
                    axis_unit="s",
                    axis_values=tuple(float(cast(float, state["time"])) for state in states),
                    entity_ids=tuple(str(node_id) for node_id in node_ids),
                    component_ids=("x", "y", "z"),
                    values=values,
                )
                reference = ResultDataRef(
                    data_id=reference.data_id,
                    content_digest=provisional.expected_content_digest,
                    codec_id=reference.codec_id,
                    logical_path=reference.logical_path,
                    bundle_digest=reference.bundle_digest,
                    attempt_id=reference.attempt_id,
                )
                data = NumericResultData(
                    reference=reference,
                    mapping=mapping,
                    axis_id=provisional.axis_id,
                    axis_unit=provisional.axis_unit,
                    axis_values=provisional.axis_values,
                    entity_ids=provisional.entity_ids,
                    component_ids=provisional.component_ids,
                    values=provisional.values,
                )
                data.verify_content_digest()
                self.data_store.register(manifest_id, mapping.canonical_id, data)
                observations.append(
                    OutputObservation(
                        output_id=mapping.canonical_id,
                        location=mapping.location,
                        value_type=mapping.value_type,
                        unit=mapping.unit,
                        frame=mapping.frame,
                        measure_id=mapping.measure_id,
                        state_count=len(states),
                        data_ref=reference,
                    )
                )
            read_result = ReadResult(ReadStatus.VALIDATED, self.profile.reader, observations, ())
            return ResultManifest(
                manifest_id, attempt.attempt_id, bundle.bundle_digest, (entry,), read_result
            )
        except PortError:
            raise
        except (ValueError, TypeError, struct.error, OverflowError) as error:
            raise PortError(
                PortErrorCategory.INTEGRITY, f"unsupported or invalid XPLT: {error}"
            ) from error

    def _mapping(
        self, native_name: str, location: str, value_type: str, unit: str
    ) -> OutputMapping:
        for mapping in self.profile.output_mappings:
            if mapping.native_name == native_name:
                if (mapping.location, mapping.value_type, mapping.unit) != (
                    location,
                    value_type,
                    unit,
                ):
                    raise _ParseError(f"XPLT dictionary does not match mapping for {native_name}")
                return mapping
        raise _ParseError(f"XPLT contains an unrequested variable: {native_name}")

    @staticmethod
    def _check_header(
        header: dict[str, object], attempt: AttemptRecord, bundle: ExecutionBundle
    ) -> None:
        if header.get("version") != _VERSION:
            raise _ParseError("unsupported XPLT version")
        if header.get("compression") != _COMPRESSION_NONE:
            raise _ParseError("compressed XPLT output is not supported")
        if header.get("attempt_id") != attempt.attempt_id:
            raise _ParseError("XPLT attempt identity does not match requested attempt")
        if header.get("bundle_digest") != bundle.bundle_digest:
            raise _ParseError("XPLT bundle identity does not match requested bundle")
        expected_version = bundle.tool.version
        tool_text = header.get("tool")
        if not isinstance(tool_text, str) or expected_version not in tool_text:
            raise _ParseError("XPLT solver identity does not match the registered profile")
        if header.get("mesh_digest") != bundle.mesh_digest:
            raise _ParseError("XPLT mesh identity does not match the requested bundle")

    @staticmethod
    def _parse(
        content: bytes,
    ) -> tuple[
        dict[str, object], dict[str, dict[str, str]], tuple[int, ...], tuple[dict[str, object], ...]
    ]:
        if len(content) < 4 or struct.unpack_from("<I", content)[0] != _MAGIC:
            raise _ParseError("invalid FEBio XPLT magic")
        top = _child_blocks(content[4:])
        if not top or top[0].identifier != 0x01000000:
            raise _ParseError("XPLT root block is missing or out of order")
        root_children = _child_blocks(top[0].payload)
        header: dict[str, object] = {}
        variables: dict[str, dict[str, str]] = {}
        for block in root_children:
            if block.identifier == 0x01010000:
                header = XpltReaderAdapter._parse_header(block.payload)
            elif block.identifier == 0x01020000:
                variables = XpltReaderAdapter._parse_dictionary(block.payload)
            else:
                raise _ParseError(f"unsupported XPLT root child 0x{block.identifier:08x}")
        if not header or not variables:
            raise _ParseError("XPLT header or dictionary is missing")
        if len(top) < 3 or top[1].identifier != 0x01040000:
            raise _ParseError("XPLT mesh block is missing or out of order")
        node_ids = XpltReaderAdapter._parse_mesh(top[1].payload)
        states = tuple(
            XpltReaderAdapter._parse_state(block.payload, variables) for block in top[2:]
        )
        if len(states) < 2:
            raise _ParseError("XPLT must contain at least two states")
        return header, variables, node_ids, states

    @staticmethod
    def _parse_header(payload: bytes) -> dict[str, object]:
        result: dict[str, object] = {}
        allowed = {
            0x01010001: "version",
            0x01010002: "compression",
            0x01010004: "tool",
            0x01010005: "units",
            0x01010006: "attempt_id",
            0x01010007: "bundle_digest",
            0x01010008: "mesh_digest",
        }
        for block in _child_blocks(payload):
            key = allowed.get(block.identifier)
            if key is None:
                raise _ParseError(f"unsupported XPLT header field 0x{block.identifier:08x}")
            result[key] = (
                _uint(block.payload, key)
                if key in {"version", "compression"}
                else _text(block.payload, key)
            )
        return result

    @staticmethod
    def _parse_dictionary(payload: bytes) -> dict[str, dict[str, str]]:
        variables: dict[str, dict[str, str]] = {}
        allowed = {
            0x01020002: "variable_id",
            0x01020003: "storage",
            0x01020004: "native_name",
            0x01020005: "location",
            0x01020006: "value_type",
            0x01020007: "unit",
        }
        for variable_block in _child_blocks(payload):
            if variable_block.identifier != 0x01020001:
                raise _ParseError("unsupported XPLT dictionary block")
            item: dict[str, str] = {}
            for block in _child_blocks(variable_block.payload):
                key = allowed.get(block.identifier)
                if key is None:
                    raise _ParseError(f"unsupported XPLT dictionary field 0x{block.identifier:08x}")
                item[key] = (
                    str(_uint(block.payload, key))
                    if key in {"variable_id", "storage"}
                    else _text(block.payload, key)
                )
            required = {"native_name", "location", "value_type", "unit"}
            if set(item) < required or item["value_type"] != "VEC3F" or item.get("storage") != "0":
                raise _ParseError("unsupported XPLT dictionary variable representation")
            if item["native_name"] in variables:
                raise _ParseError("duplicate XPLT dictionary variable")
            variables[item["native_name"]] = item
        return variables

    @staticmethod
    def _parse_mesh(payload: bytes) -> tuple[int, ...]:
        children = _child_blocks(payload)
        if not children or children[0].identifier != 0x01041000:
            raise _ParseError("unsupported or missing XPLT node mesh block")
        node_payload = children[0].payload
        if len(node_payload) < 8:
            raise _ParseError("truncated XPLT node mesh block")
        count, dimension = struct.unpack_from("<II", node_payload)
        if dimension != 3 or len(node_payload) != 8 + count * 16 or count == 0:
            raise _ParseError("invalid XPLT node mesh dimensions")
        node_ids: list[int] = []
        offset = 8
        for _ in range(count):
            node_id, x, y, z = struct.unpack_from("<Ifff", node_payload, offset)
            offset += 16
            if not all(math.isfinite(value) for value in (x, y, z)):
                raise _ParseError("nonfinite XPLT mesh coordinate")
            if node_id in node_ids:
                raise _ParseError("duplicate XPLT node ID")
            node_ids.append(node_id)
        return tuple(node_ids)

    @staticmethod
    def _parse_state(payload: bytes, variables: dict[str, dict[str, str]]) -> dict[str, object]:
        children = _child_blocks(payload)
        time_blocks = [item for item in children if item.identifier == 0x02010000]
        data_blocks = [item for item in children if item.identifier == 0x02030000]
        if len(time_blocks) != 1 or len(data_blocks) != 1:
            raise _ParseError("XPLT state is missing time or data block")
        time_payload = time_blocks[0].payload
        if len(time_payload) != 12:
            raise _ParseError("invalid XPLT state time payload")
        time_value, state_id = struct.unpack("<dI", time_payload)
        if not math.isfinite(time_value):
            raise _ParseError("nonfinite XPLT state time")
        values: dict[str, tuple[float, ...]] = {}
        data_children = _child_blocks(data_blocks[0].payload)
        for index, (native_name, _variable) in enumerate(variables.items(), start=1):
            expected_id = 0x02030000 + index
            matching = [item for item in data_children if item.identifier == expected_id]
            if len(matching) != 1:
                raise _ParseError(f"XPLT state is missing {native_name}")
            raw = matching[0].payload
            if len(raw) < 8:
                raise _ParseError("truncated XPLT state variable payload")
            compression, byte_count = struct.unpack_from("<II", raw)
            value_bytes = raw[8:]
            if (
                compression != _COMPRESSION_NONE
                or byte_count != len(value_bytes)
                or byte_count % 4 != 0
            ):
                raise _ParseError("unsupported XPLT state storage")
            floats = struct.unpack("<" + "f" * (byte_count // 4), value_bytes)
            if any(not math.isfinite(value) for value in floats):
                raise _ParseError("nonfinite XPLT result value")
            if len(floats) == 0 or len(floats) % 3 != 0:
                raise _ParseError("XPLT VEC3F width is invalid")
            values[native_name] = tuple(float(value) for value in floats)
        if len(values) != len(variables):
            raise _ParseError("XPLT state contains unknown variables")
        return {"state_id": state_id, "time": time_value, "values": values}


__all__ = ["LocalResultDataStore", "XpltReaderAdapter"]
