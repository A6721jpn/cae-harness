"""Explicit tool, capability, and output-mapping contracts."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from .canonical import canonical_bytes
from .evidence import EvidenceRef
from .spatial import FrameId

SCHEMA_VERSION = "1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class CompatibilityValidationError(ValueError):
    """Raised when a compatibility declaration is structurally invalid."""


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise CompatibilityValidationError(
            f"{field_name} must be non-empty text without surrounding whitespace"
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise CompatibilityValidationError(f"{field_name} contains a control character")
    return value


def _digest(value: object, field_name: str) -> str:
    value = _text(value, field_name)
    if _SHA256.fullmatch(value) is None:
        raise CompatibilityValidationError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def _evidence(value: object, field_name: str) -> tuple[EvidenceRef, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise CompatibilityValidationError(f"{field_name} must be a sequence")
    result = tuple(value)
    if any(not isinstance(item, EvidenceRef) for item in result):
        raise CompatibilityValidationError(f"{field_name} contains an invalid EvidenceRef")
    return result


class CapabilityStatus(str, Enum):
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    UNVERIFIED = "UNVERIFIED"


class _Sign(int, Enum):
    POSITIVE = 1
    NEGATIVE = -1


@dataclass(frozen=True, slots=True)
class ToolIdentity:
    tool_id: str
    version: str
    executable_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool_id", _text(self.tool_id, "tool_id"))
        object.__setattr__(self, "version", _text(self.version, "version"))
        object.__setattr__(
            self, "executable_digest", _digest(self.executable_digest, "executable_digest")
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "tool_id": self.tool_id,
            "version": self.version,
            "executable_digest": self.executable_digest,
        }


@dataclass(frozen=True, slots=True)
class CapabilityRef:
    capability_id: str
    status: CapabilityStatus
    version: str
    compression: str
    ordering_id: str
    sign_mapping_id: str
    evidence: Sequence[EvidenceRef]

    def __post_init__(self) -> None:
        object.__setattr__(self, "capability_id", _text(self.capability_id, "capability_id"))
        if not isinstance(self.status, CapabilityStatus):
            raise CompatibilityValidationError("status must be a CapabilityStatus")
        object.__setattr__(self, "version", _text(self.version, "version"))
        object.__setattr__(self, "compression", _text(self.compression, "compression"))
        object.__setattr__(self, "ordering_id", _text(self.ordering_id, "ordering_id"))
        object.__setattr__(self, "sign_mapping_id", _text(self.sign_mapping_id, "sign_mapping_id"))
        object.__setattr__(self, "evidence", _evidence(self.evidence, "evidence"))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "capability_id": self.capability_id,
            "status": self.status.value,
            "version": self.version,
            "compression": self.compression,
            "ordering_id": self.ordering_id,
            "sign_mapping_id": self.sign_mapping_id,
            "evidence": [item.to_dict() for item in self.evidence],
        }


@dataclass(frozen=True, slots=True)
class OutputMapping:
    """Explicit canonical-output to native-output mapping."""

    canonical_id: str
    native_name: str
    location: str
    value_type: str
    unit: str
    frame: FrameId
    raw_sign: int
    canonical_sign: int
    measure_id: str

    def __post_init__(self) -> None:
        for field_name in (
            "canonical_id",
            "native_name",
            "location",
            "value_type",
            "unit",
            "measure_id",
        ):
            object.__setattr__(self, field_name, _text(getattr(self, field_name), field_name))
        if not isinstance(self.frame, FrameId):
            raise CompatibilityValidationError("frame must be a FrameId")
        for field_name in ("raw_sign", "canonical_sign"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or value not in (-1, 1):
                raise CompatibilityValidationError(f"{field_name} must be -1 or 1")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "canonical_id": self.canonical_id,
            "native_name": self.native_name,
            "location": self.location,
            "value_type": self.value_type,
            "unit": self.unit,
            "frame": self.frame.value,
            "raw_sign": self.raw_sign,
            "canonical_sign": self.canonical_sign,
            "measure_id": self.measure_id,
        }


@dataclass(frozen=True, slots=True)
class CompatibilityProfile:
    profile_id: str
    solver: ToolIdentity
    reader: ToolIdentity
    capabilities: Sequence[CapabilityRef]
    output_mappings: Sequence[OutputMapping]
    evidence: Sequence[EvidenceRef]

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile_id", _text(self.profile_id, "profile_id"))
        if not isinstance(self.solver, ToolIdentity) or not isinstance(self.reader, ToolIdentity):
            raise CompatibilityValidationError("solver and reader must be ToolIdentity values")
        capabilities = tuple(self.capabilities)
        mappings = tuple(self.output_mappings)
        if any(not isinstance(item, CapabilityRef) for item in capabilities):
            raise CompatibilityValidationError("capabilities contains an invalid item")
        if any(not isinstance(item, OutputMapping) for item in mappings):
            raise CompatibilityValidationError("output_mappings contains an invalid item")
        if len({item.capability_id for item in capabilities}) != len(capabilities):
            raise CompatibilityValidationError("capabilities contains duplicate IDs")
        if len({item.canonical_id for item in mappings}) != len(mappings):
            raise CompatibilityValidationError("output_mappings contains duplicate canonical IDs")
        object.__setattr__(self, "capabilities", capabilities)
        object.__setattr__(self, "output_mappings", mappings)
        object.__setattr__(self, "evidence", _evidence(self.evidence, "evidence"))

    def mapping_for(self, canonical_id: str) -> OutputMapping:
        for mapping in self.output_mappings:
            if mapping.canonical_id == canonical_id:
                return mapping
        raise CompatibilityValidationError(f"no output mapping for {canonical_id!r}")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "profile_id": self.profile_id,
            "solver": self.solver.to_dict(),
            "reader": self.reader.to_dict(),
            "capabilities": [item.to_dict() for item in self.capabilities],
            "output_mappings": [item.to_dict() for item in self.output_mappings],
            "evidence": [item.to_dict() for item in self.evidence],
        }

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


__all__ = [
    "SCHEMA_VERSION",
    "CapabilityRef",
    "CapabilityStatus",
    "CompatibilityProfile",
    "CompatibilityValidationError",
    "OutputMapping",
    "ToolIdentity",
]
