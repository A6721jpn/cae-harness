"""Immutable, authority-free snapshots of evolving case intent."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import cast

from .canonical import canonical_bytes
from .evidence import EvidenceRef
from .partial_case_spec import PartialCaseSpec

SCHEMA_VERSION = "1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class CaseDraftValidationError(ValueError):
    """Raised when an immutable case-draft snapshot is structurally invalid."""


def _require_identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise CaseDraftValidationError(
            f"{field_name} must be non-empty text without surrounding whitespace"
        )
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise CaseDraftValidationError(f"{field_name} contains a Unicode control character")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise CaseDraftValidationError(f"{field_name} must be UTF-8 representable") from error
    return value


def _require_digest(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise CaseDraftValidationError(
            f"{field_name} must be a lowercase 64-character SHA-256 hexadecimal digest"
        )
    return value


def _require_input_intent(value: object) -> str:
    if not isinstance(value, str):
        raise CaseDraftValidationError("input_intent must be UTF-8-representable text")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise CaseDraftValidationError("input_intent must be UTF-8-representable text") from error
    return value


def _values_projection(values: PartialCaseSpec) -> dict[str, object]:
    try:
        projection = json.loads(values.to_bytes().decode("utf-8"))
    except (AttributeError, TypeError, UnicodeError, ValueError, OverflowError) as error:
        raise CaseDraftValidationError(
            f"values canonical projection is invalid: {error}"
        ) from error
    if not isinstance(projection, dict):
        raise CaseDraftValidationError("values canonical projection must be an object")
    return cast(dict[str, object], projection)


def _evidence_projection(evidence: Sequence[EvidenceRef]) -> list[dict[str, object]]:
    projected: list[dict[str, object]] = []
    for index, item in enumerate(evidence):
        try:
            raw = item.to_dict()
            canonical_bytes(raw)
        except (AttributeError, TypeError, UnicodeError, ValueError, OverflowError) as error:
            raise CaseDraftValidationError(
                f"evidence[{index}] canonical projection is invalid: {error}"
            ) from error
        projected.append(cast(dict[str, object], raw))
    return projected


def _copy_evidence(value: object) -> tuple[EvidenceRef, ...]:
    if isinstance(value, (str, bytes, bytearray, Mapping)) or not isinstance(value, Sequence):
        raise CaseDraftValidationError("evidence must be a sequence")
    copied = tuple(value)
    keyed: list[tuple[bytes, EvidenceRef]] = []
    seen: set[bytes] = set()
    for index, item in enumerate(copied):
        if not isinstance(item, EvidenceRef):
            raise CaseDraftValidationError(f"evidence[{index}] must be an EvidenceRef")
        try:
            canonical = canonical_bytes(item.to_dict())
        except (AttributeError, TypeError, UnicodeError, ValueError, OverflowError) as error:
            raise CaseDraftValidationError(
                f"evidence[{index}] canonical projection is invalid: {error}"
            ) from error
        if canonical in seen:
            raise CaseDraftValidationError(
                f"evidence contains duplicate complete declaration at index {index}"
            )
        seen.add(canonical)
        keyed.append((canonical, item))
    keyed.sort(key=lambda entry: entry[0])
    return tuple(item for _, item in keyed)


def _validate_generation_canonical(generation: int) -> None:
    try:
        canonical_bytes(generation)
    except (TypeError, UnicodeError, ValueError, OverflowError) as error:
        raise CaseDraftValidationError(
            f"generation is not canonically representable: {error}"
        ) from error


@dataclass(frozen=True, slots=True)
class CaseDraft:
    """An immutable draft snapshot without persistence or execution authority."""

    case_id: str
    draft_id: str
    generation: int
    input_intent: str
    parent_revision_id: str | None
    parent_spec_digest: str | None
    values: PartialCaseSpec
    evidence: Sequence[EvidenceRef]
    unresolved_fields: tuple[str, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        case_id = _require_identifier(self.case_id, "case_id")
        draft_id = _require_identifier(self.draft_id, "draft_id")
        if isinstance(self.generation, bool) or not isinstance(self.generation, int):
            raise CaseDraftValidationError("generation must be an integer")
        if self.generation < 0:
            raise CaseDraftValidationError("generation must be nonnegative")
        input_intent = _require_input_intent(self.input_intent)

        parent_revision_id: str | None
        if self.parent_revision_id is None:
            parent_revision_id = None
        else:
            parent_revision_id = _require_identifier(self.parent_revision_id, "parent_revision_id")

        parent_spec_digest: str | None
        if self.parent_spec_digest is None:
            parent_spec_digest = None
        else:
            parent_spec_digest = _require_digest(self.parent_spec_digest, "parent_spec_digest")
        if (parent_revision_id is None) != (parent_spec_digest is None):
            raise CaseDraftValidationError(
                "parent_revision_id and parent_spec_digest must be supplied together"
            )
        if not isinstance(self.values, PartialCaseSpec):
            raise CaseDraftValidationError("values must be a PartialCaseSpec")

        evidence = _copy_evidence(self.evidence)
        _validate_generation_canonical(self.generation)
        object.__setattr__(self, "case_id", case_id)
        object.__setattr__(self, "draft_id", draft_id)
        object.__setattr__(self, "input_intent", input_intent)
        object.__setattr__(self, "parent_revision_id", parent_revision_id)
        object.__setattr__(self, "parent_spec_digest", parent_spec_digest)
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(self, "unresolved_fields", self.values.unresolved_fields)
        self.to_bytes()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "case_id": self.case_id,
            "draft_id": self.draft_id,
            "generation": self.generation,
            "input_intent": self.input_intent,
            "parent_revision_id": self.parent_revision_id,
            "parent_spec_digest": self.parent_spec_digest,
            "values": _values_projection(self.values),
            "evidence": _evidence_projection(self.evidence),
            "unresolved_fields": list(self.unresolved_fields),
        }

    def to_bytes(self) -> bytes:
        try:
            _validate_generation_canonical(self.generation)
            return canonical_bytes(self.to_dict())
        except CaseDraftValidationError:
            raise
        except (TypeError, UnicodeError, ValueError, OverflowError) as error:
            raise CaseDraftValidationError(
                f"case draft canonical projection is invalid: {error}"
            ) from error


__all__ = ["SCHEMA_VERSION", "CaseDraft", "CaseDraftValidationError"]
