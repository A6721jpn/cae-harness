"""Immutable, syntax-validated references to explicit evidence sources."""

from __future__ import annotations

import re
from dataclasses import dataclass

SCHEMA_VERSION = "1"
SUPPORTED_SOURCE_KINDS = frozenset(
    {
        "user_instruction",
        "registered_document",
        "registered_material",
        "registered_test_condition",
    }
)
_TARGET_FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*(?:\.[A-Za-z_][A-Za-z0-9_-]*)*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _require_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field} must be a non-empty string without surrounding whitespace")
    if any(ord(character) < 32 for character in value):
        raise ValueError(f"{field} contains a control character")
    return value


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    schema_version: str
    source_kind: str
    reference: str
    target_field: str
    content_digest: str

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported evidence schema version: {self.schema_version!r}")
        source_kind = _require_text(self.source_kind, "source_kind")
        if source_kind not in SUPPORTED_SOURCE_KINDS:
            raise ValueError(f"unsupported evidence source kind: {source_kind!r}")
        _require_text(self.reference, "reference")
        target_field = _require_text(self.target_field, "target_field")
        if _TARGET_FIELD.fullmatch(target_field) is None:
            raise ValueError(f"invalid target field: {target_field!r}")
        content_digest = _require_text(self.content_digest, "content_digest")
        if _SHA256.fullmatch(content_digest) is None:
            raise ValueError("content_digest must be a lowercase SHA-256 hexadecimal digest")

    def to_dict(self) -> dict[str, str]:
        return {
            "schema_version": self.schema_version,
            "source_kind": self.source_kind,
            "reference": self.reference,
            "target_field": self.target_field,
            "content_digest": self.content_digest,
        }


__all__ = ["EvidenceRef", "SCHEMA_VERSION", "SUPPORTED_SOURCE_KINDS"]
