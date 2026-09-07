"""Immutable content identity for one explicit case revision declaration."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import cast

from .canonical import canonical_bytes
from .case_spec import CaseSpec
from .evidence import EvidenceRef

SCHEMA_VERSION = "1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class CaseRevisionValidationError(ValueError):
    """Raised when a case revision declaration is structurally invalid."""


def _require_identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise CaseRevisionValidationError(
            f"{field} must be non-empty text without surrounding whitespace"
        )
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise CaseRevisionValidationError(f"{field} contains a Unicode control character")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise CaseRevisionValidationError(f"{field} must be UTF-8 representable") from error
    return value


def _require_digest(value: object, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise CaseRevisionValidationError(
            f"{field} must be a lowercase 64-character SHA-256 hexadecimal digest"
        )
    return value


def _spec_projection(spec: CaseSpec) -> dict[str, object]:
    try:
        projection = json.loads(spec.to_bytes().decode("utf-8"))
    except (AttributeError, TypeError, UnicodeError, ValueError, OverflowError) as error:
        raise CaseRevisionValidationError(
            f"spec canonical projection is invalid: {error}"
        ) from error
    if not isinstance(projection, dict):
        raise CaseRevisionValidationError("spec canonical projection must be an object")
    return cast(dict[str, object], projection)


def _evidence_projection(evidence: Sequence[EvidenceRef]) -> list[dict[str, object]]:
    projected: list[dict[str, object]] = []
    for index, item in enumerate(evidence):
        try:
            raw = item.to_dict()
            canonical_bytes(raw)
        except (AttributeError, TypeError, UnicodeError, ValueError, OverflowError) as error:
            raise CaseRevisionValidationError(
                f"evidence[{index}] canonical projection is invalid: {error}"
            ) from error
        projected.append(cast(dict[str, object], raw))
    return projected


def _copy_evidence(value: object) -> tuple[EvidenceRef, ...]:
    if isinstance(value, (str, bytes, bytearray, Mapping)) or not isinstance(value, Sequence):
        raise CaseRevisionValidationError("evidence must be a non-empty sequence")
    copied = tuple(value)
    if not copied:
        raise CaseRevisionValidationError("evidence must be a non-empty sequence")

    keyed: list[tuple[bytes, EvidenceRef]] = []
    seen: set[bytes] = set()
    for index, item in enumerate(copied):
        if not isinstance(item, EvidenceRef):
            raise CaseRevisionValidationError(f"evidence[{index}] must be an EvidenceRef")
        try:
            canonical = canonical_bytes(item.to_dict())
        except (AttributeError, TypeError, UnicodeError, ValueError, OverflowError) as error:
            raise CaseRevisionValidationError(
                f"evidence[{index}] canonical projection is invalid: {error}"
            ) from error
        if canonical in seen:
            raise CaseRevisionValidationError(
                f"evidence contains duplicate complete declaration at index {index}"
            )
        seen.add(canonical)
        keyed.append((canonical, item))
    keyed.sort(key=lambda entry: entry[0])
    return tuple(item for _, item in keyed)


@dataclass(frozen=True, slots=True)
class CaseRevision:
    """An immutable declaration of case content and supporting evidence.

    This value identifies content only. It does not register a revision, freeze
    a draft, resolve evidence, or establish execution readiness.
    """

    case_id: str
    revision_id: str
    parent_revision_id: str | None
    parent_spec_digest: str | None
    spec: CaseSpec
    evidence: Sequence[EvidenceRef]
    spec_digest: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        case_id = _require_identifier(self.case_id, "case_id")
        revision_id = _require_identifier(self.revision_id, "revision_id")

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
            raise CaseRevisionValidationError(
                "parent_revision_id and parent_spec_digest must be supplied together"
            )
        if parent_revision_id == revision_id:
            raise CaseRevisionValidationError("parent_revision_id must not equal revision_id")
        if not isinstance(self.spec, CaseSpec):
            raise CaseRevisionValidationError("spec must be a CaseSpec")

        evidence = _copy_evidence(self.evidence)
        object.__setattr__(self, "case_id", case_id)
        object.__setattr__(self, "revision_id", revision_id)
        object.__setattr__(self, "parent_revision_id", parent_revision_id)
        object.__setattr__(self, "parent_spec_digest", parent_spec_digest)
        object.__setattr__(self, "evidence", evidence)

        # Validate the complete nested boundary while the declaration is built.
        self.content_bytes()

    def content_bytes(self) -> bytes:
        """Return canonical bytes for schema, spec content, and evidence only."""

        try:
            content = {
                "schema_version": SCHEMA_VERSION,
                "spec": _spec_projection(self.spec),
                "evidence": _evidence_projection(self.evidence),
            }
            result = canonical_bytes(content, unordered_paths=(("evidence",),))
            object.__setattr__(self, "spec_digest", hashlib.sha256(result).hexdigest())
            return result
        except CaseRevisionValidationError:
            raise
        except (TypeError, UnicodeError, ValueError, OverflowError) as error:
            raise CaseRevisionValidationError(
                f"case revision content is not canonically serializable: {error}"
            ) from error

    def to_dict(self) -> dict[str, object]:
        """Return a detached record projection including identity metadata."""

        self.content_bytes()
        return {
            "schema_version": SCHEMA_VERSION,
            "case_id": self.case_id,
            "revision_id": self.revision_id,
            "parent_revision_id": self.parent_revision_id,
            "parent_spec_digest": self.parent_spec_digest,
            "spec": _spec_projection(self.spec),
            "evidence": _evidence_projection(self.evidence),
            "spec_digest": self.spec_digest,
        }

    def to_bytes(self) -> bytes:
        """Return deterministic canonical bytes for the complete record."""

        try:
            return canonical_bytes(self.to_dict())
        except CaseRevisionValidationError:
            raise
        except (TypeError, UnicodeError, ValueError, OverflowError) as error:
            raise CaseRevisionValidationError(
                f"case revision record is not canonically serializable: {error}"
            ) from error


__all__ = ["SCHEMA_VERSION", "CaseRevision", "CaseRevisionValidationError"]
