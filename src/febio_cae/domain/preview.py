"""Preview request and confirmation-receipt contracts."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from .canonical import canonical_bytes
from .compatibility import ToolIdentity
from .evidence import EvidenceRef
from .lifecycle import PreviewStatus

SCHEMA_VERSION = "1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PreviewValidationError(ValueError):
    """Raised when a preview request or receipt is malformed."""


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise PreviewValidationError(
            f"{field_name} must be non-empty text without surrounding whitespace"
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise PreviewValidationError(f"{field_name} contains a control character")
    return value


def _digest(value: object, field_name: str) -> str:
    value = _text(value, field_name)
    if _SHA256.fullmatch(value) is None:
        raise PreviewValidationError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


@dataclass(frozen=True, slots=True)
class PreviewRequest:
    preview_id: str
    manifest_id: str
    state_ids: Sequence[int]
    variables: Sequence[str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "preview_id", _text(self.preview_id, "preview_id"))
        object.__setattr__(self, "manifest_id", _text(self.manifest_id, "manifest_id"))
        if isinstance(self.state_ids, (str, bytes, bytearray)) or not isinstance(
            self.state_ids, Sequence
        ):
            raise PreviewValidationError("state_ids must be a sequence")
        states = tuple(self.state_ids)
        if any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in states):
            raise PreviewValidationError("state_ids must contain nonnegative integers")
        if not states:
            raise PreviewValidationError("state_ids must not be empty")
        if len(set(states)) != len(states):
            raise PreviewValidationError("state_ids must not contain duplicates")
        if isinstance(self.variables, (str, bytes, bytearray)) or not isinstance(
            self.variables, Sequence
        ):
            raise PreviewValidationError("variables must be a sequence")
        variables = tuple(_text(item, "variables[]") for item in self.variables)
        if not variables:
            raise PreviewValidationError("variables must not be empty")
        if len(set(variables)) != len(variables):
            raise PreviewValidationError("variables must not contain duplicates")
        object.__setattr__(self, "state_ids", states)
        object.__setattr__(self, "variables", variables)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "preview_id": self.preview_id,
            "manifest_id": self.manifest_id,
            "state_ids": list(self.state_ids),
            "variables": list(self.variables),
        }

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


@dataclass(frozen=True, slots=True)
class PreviewReceipt:
    """Receipt whose confirmation requires independent evidence and observed data."""

    receipt_id: str
    manifest_id: str
    xplt_digest: str
    studio: ToolIdentity
    status: PreviewStatus
    requested_state_ids: Sequence[int]
    requested_variables: Sequence[str]
    observed_state_ids: Sequence[int]
    observed_variables: Sequence[str]
    confirmation_evidence: Sequence[EvidenceRef]

    def __post_init__(self) -> None:
        for field_name in ("receipt_id", "manifest_id"):
            object.__setattr__(self, field_name, _text(getattr(self, field_name), field_name))
        object.__setattr__(self, "xplt_digest", _digest(self.xplt_digest, "xplt_digest"))
        if not isinstance(self.studio, ToolIdentity):
            raise PreviewValidationError("studio must be a ToolIdentity")
        if not isinstance(self.status, PreviewStatus):
            raise PreviewValidationError("status must be a PreviewStatus")
        for field_name in ("requested_state_ids", "observed_state_ids"):
            value = getattr(self, field_name)
            if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
                raise PreviewValidationError(f"{field_name} must be a sequence")
            states = tuple(value)
            if any(
                isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in states
            ):
                raise PreviewValidationError(f"{field_name} must contain nonnegative integers")
            if len(set(states)) != len(states):
                raise PreviewValidationError(f"{field_name} must not contain duplicates")
            object.__setattr__(self, field_name, states)
        for field_name in ("requested_variables", "observed_variables"):
            value = getattr(self, field_name)
            if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
                raise PreviewValidationError(f"{field_name} must be a sequence")
            object.__setattr__(
                self, field_name, tuple(_text(item, f"{field_name}[]") for item in value)
            )
        evidence = tuple(self.confirmation_evidence)
        if any(not isinstance(item, EvidenceRef) for item in evidence):
            raise PreviewValidationError("confirmation_evidence contains an invalid EvidenceRef")
        if self.status is PreviewStatus.CONFIRMED and (
            not evidence or not self.observed_state_ids or not self.observed_variables
        ):
            raise PreviewValidationError(
                "CONFIRMED preview receipts require observed state/variables and evidence"
            )
        object.__setattr__(self, "confirmation_evidence", evidence)

    def confirmed(
        self,
        *,
        evidence: Sequence[EvidenceRef],
        observed_state_ids: Sequence[int] | None = None,
        observed_variables: Sequence[str] | None = None,
    ) -> PreviewReceipt:
        if observed_state_ids is None:
            if not self.observed_state_ids:
                raise PreviewValidationError(
                    "confirmed preview receipts require explicit observed state IDs and confirmation evidence"
                )
            states = self.observed_state_ids
        else:
            states = observed_state_ids
        if observed_variables is None:
            if not self.observed_variables:
                raise PreviewValidationError(
                    "confirmed preview receipts require explicit observed variables and confirmation evidence"
                )
            variables = self.observed_variables
        else:
            variables = observed_variables
        return PreviewReceipt(
            receipt_id=self.receipt_id,
            manifest_id=self.manifest_id,
            xplt_digest=self.xplt_digest,
            studio=self.studio,
            status=PreviewStatus.CONFIRMED,
            requested_state_ids=self.requested_state_ids,
            requested_variables=self.requested_variables,
            observed_state_ids=states,
            observed_variables=variables,
            confirmation_evidence=evidence,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "receipt_id": self.receipt_id,
            "manifest_id": self.manifest_id,
            "xplt_digest": self.xplt_digest,
            "studio": self.studio.to_dict(),
            "status": self.status.value,
            "requested_state_ids": list(self.requested_state_ids),
            "requested_variables": list(self.requested_variables),
            "observed_state_ids": list(self.observed_state_ids),
            "observed_variables": list(self.observed_variables),
            "confirmation_evidence": [item.to_dict() for item in self.confirmation_evidence],
        }

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


__all__ = [
    "SCHEMA_VERSION",
    "PreviewReceipt",
    "PreviewRequest",
    "PreviewValidationError",
]
