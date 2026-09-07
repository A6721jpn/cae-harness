"""Strict application request parsing around the frozen common codec."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from febio_cae.domain.codec import CodecError, decode_record
from febio_cae.domain.evidence import SUPPORTED_SOURCE_KINDS, EvidenceRef
from febio_cae.domain.partial_case_spec import PartialCaseSpec


class SpecInputError(ValueError):
    """Raised for an explicit specification request outside its schema."""


@dataclass(frozen=True, slots=True)
class SourceDeclaration:
    source_kind: str
    reference: str
    target_field: str
    content: bytes | None = None
    content_digest: str | None = None
    media_type: str = "text/plain"


@dataclass(frozen=True, slots=True)
class SpecUpdateRequest:
    values: PartialCaseSpec
    evidence: tuple[EvidenceRef, ...]
    source_declarations: tuple[SourceDeclaration, ...]
    input_intent: str = ""


def _mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise SpecInputError(f"{field} must be an object")
    return {str(key): item for key, item in value.items()}


def _strict(
    payload: object, required: set[str], allowed: set[str], field: str
) -> dict[str, object]:
    item = _mapping(payload, field)
    actual = set(item)
    if not required.issubset(actual) or not actual.issubset(allowed):
        raise SpecInputError(
            f"{field} has unknown or missing keys "
            f"(missing={sorted(required - actual)!r}, unknown={sorted(actual - allowed)!r})"
        )
    return item


def _evidence(value: object, field: str) -> EvidenceRef:
    item = _strict(
        value,
        {"schema_version", "source_kind", "reference", "target_field", "content_digest"},
        {"schema_version", "source_kind", "reference", "target_field", "content_digest"},
        field,
    )
    try:
        return EvidenceRef(
            schema_version=cast(str, item["schema_version"]),
            source_kind=cast(str, item["source_kind"]),
            reference=cast(str, item["reference"]),
            target_field=cast(str, item["target_field"]),
            content_digest=cast(str, item["content_digest"]),
        )
    except (TypeError, ValueError) as error:
        raise SpecInputError(f"{field} is not a valid EvidenceRef: {error}") from error


def _source(value: object, field: str) -> SourceDeclaration:
    item = _strict(
        value,
        {"source_kind", "reference", "target_field"},
        {
            "source_kind",
            "reference",
            "target_field",
            "content",
            "content_digest",
            "media_type",
        },
        field,
    )
    kind = item["source_kind"]
    reference = item["reference"]
    target = item["target_field"]
    if not isinstance(kind, str) or kind not in SUPPORTED_SOURCE_KINDS:
        raise SpecInputError(f"{field}.source_kind is unsupported")
    if not isinstance(reference, str) or not reference.strip():
        raise SpecInputError(f"{field}.reference must be non-empty text")
    if not isinstance(target, str) or not target.strip():
        raise SpecInputError(f"{field}.target_field must be non-empty text")
    raw_content = item.get("content")
    if raw_content is not None and not isinstance(raw_content, str):
        raise SpecInputError(f"{field}.content must be UTF-8 text or null")
    digest = item.get("content_digest")
    if digest is not None and (not isinstance(digest, str) or not digest.strip()):
        raise SpecInputError(f"{field}.content_digest must be text or null")
    media_type = item.get("media_type", "text/plain")
    if not isinstance(media_type, str) or not media_type.strip():
        raise SpecInputError(f"{field}.media_type must be non-empty text")
    return SourceDeclaration(
        source_kind=kind,
        reference=reference,
        target_field=target,
        content=None if raw_content is None else raw_content.encode("utf-8"),
        content_digest=digest,
        media_type=media_type,
    )


def parse_spec_request(payload: object) -> SpecUpdateRequest:
    """Parse the wrapper while delegating ``values`` to the common JSON codec."""

    item = _strict(
        payload,
        {"schema_version", "values", "evidence", "source_declarations"},
        {"schema_version", "values", "evidence", "source_declarations", "input_intent"},
        "spec",
    )
    if item["schema_version"] != "1":
        raise SpecInputError("spec.schema_version must be '1'")
    input_intent = item.get("input_intent", "")
    if not isinstance(input_intent, str):
        raise SpecInputError("spec.input_intent must be text")
    try:
        values = decode_record(
            json.dumps(item["values"], ensure_ascii=False, separators=(",", ":")),
            PartialCaseSpec,
        )
    except CodecError as error:
        raise SpecInputError(f"spec.values is not a PartialCaseSpec: {error}") from error
    raw_evidence = item["evidence"]
    if isinstance(raw_evidence, (str, bytes, bytearray)) or not isinstance(raw_evidence, list):
        raise SpecInputError("spec.evidence must be a JSON array")
    evidence = tuple(
        _evidence(value, f"spec.evidence[{index}]") for index, value in enumerate(raw_evidence)
    )
    raw_sources = item["source_declarations"]
    if isinstance(raw_sources, (str, bytes, bytearray)) or not isinstance(raw_sources, list):
        raise SpecInputError("spec.source_declarations must be a JSON array")
    sources = tuple(
        _source(value, f"spec.source_declarations[{index}]")
        for index, value in enumerate(raw_sources)
    )
    return SpecUpdateRequest(values, evidence, sources, input_intent)


__all__ = ["SourceDeclaration", "SpecInputError", "SpecUpdateRequest", "parse_spec_request"]
