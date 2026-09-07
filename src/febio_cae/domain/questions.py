"""Immutable issued-question records bound to one draft generation."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from .canonical import canonical_bytes
from .evidence import EvidenceRef

SCHEMA_VERSION = "1"
_TARGET = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*(?:\.[A-Za-z_][A-Za-z0-9_-]*)*$")


class QuestionValidationError(ValueError):
    """Raised when an issued question is not generation-bound and explicit."""


def _identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise QuestionValidationError(
            f"{field_name} must be non-empty text without surrounding whitespace"
        )
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise QuestionValidationError(f"{field_name} contains a Unicode control character")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise QuestionValidationError(f"{field_name} must be UTF-8 representable") from error
    return value


def _evidence(value: object, field_name: str) -> tuple[EvidenceRef, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise QuestionValidationError(f"{field_name} must be a sequence")
    copied = tuple(value)
    if not copied or any(not isinstance(item, EvidenceRef) for item in copied):
        raise QuestionValidationError(f"{field_name} must be a non-empty EvidenceRef sequence")
    keyed = [(canonical_bytes(item.to_dict()), item) for item in copied]
    if len({key for key, _ in keyed}) != len(keyed):
        raise QuestionValidationError(f"{field_name} contains duplicate complete evidence")
    return tuple(item for _, item in sorted(keyed, key=lambda pair: pair[0]))


@dataclass(frozen=True, slots=True)
class IssuedQuestion:
    question_id: str
    case_id: str
    draft_id: str
    generation: int
    target_fields: Sequence[str]
    question_time_evidence: Sequence[EvidenceRef]

    def __post_init__(self) -> None:
        for field_name in ("question_id", "case_id", "draft_id"):
            object.__setattr__(self, field_name, _identifier(getattr(self, field_name), field_name))
        if (
            isinstance(self.generation, bool)
            or not isinstance(self.generation, int)
            or self.generation < 0
        ):
            raise QuestionValidationError("generation must be a nonnegative integer excluding bool")
        if isinstance(self.target_fields, (str, bytes, bytearray)) or not isinstance(
            self.target_fields, Sequence
        ):
            raise QuestionValidationError("target_fields must be a sequence")
        fields = tuple(_identifier(item, "target_fields[]") for item in self.target_fields)
        if not fields or any(_TARGET.fullmatch(item) is None for item in fields):
            raise QuestionValidationError("target_fields must contain valid field paths")
        if len(set(fields)) != len(fields):
            raise QuestionValidationError("target_fields must not contain duplicates")
        object.__setattr__(self, "target_fields", fields)
        object.__setattr__(
            self,
            "question_time_evidence",
            _evidence(self.question_time_evidence, "question_time_evidence"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "question_id": self.question_id,
            "case_id": self.case_id,
            "draft_id": self.draft_id,
            "generation": self.generation,
            "target_fields": list(self.target_fields),
            "question_time_evidence": [item.to_dict() for item in self.question_time_evidence],
        }

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


__all__ = ["SCHEMA_VERSION", "IssuedQuestion", "QuestionValidationError"]
