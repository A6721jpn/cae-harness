"""Immutable original-input records and derived-model planning."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from ._immutability import freeze_value, thaw_json
from .types import EvidenceProvenance, IntentImpact, normalise_provenance


@dataclass(frozen=True, slots=True)
class OriginalModel:
    """A digest-bound, read-only snapshot of an original model input."""

    source_path: Path | None = None
    sha256: str = ""
    size_bytes: int = 0
    format: str = "FEB"
    source_name: str | None = None
    _snapshot: bytes = field(default=b"", repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.source_path is not None:
            object.__setattr__(self, "source_path", Path(self.source_path))
        if self.size_bytes < 0:
            raise ValueError("size_bytes must be non-negative")
        if not isinstance(self.format, str) or not self.format.strip():
            raise ValueError("format must be a non-empty string")
        if not isinstance(self._snapshot, bytes):
            raise TypeError("snapshot must be bytes")

    @classmethod
    def from_path(
        cls,
        path: str | Path,
        *,
        format: str = "FEB",
        source_name: str | None = None,
    ) -> OriginalModel:
        source_path = Path(path)
        payload = source_path.read_bytes()
        return cls(
            source_path=source_path,
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
            format=format,
            source_name=source_name or source_path.name,
            _snapshot=payload,
        )

    @classmethod
    def from_bytes(
        cls,
        payload: bytes,
        *,
        source_name: str | None = None,
        format: str = "FEB",
    ) -> OriginalModel:
        if not isinstance(payload, bytes):
            raise TypeError("payload must be bytes")
        return cls(
            source_path=None,
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
            format=format,
            source_name=source_name,
            _snapshot=bytes(payload),
        )

    @classmethod
    def from_source(
        cls,
        source: str | bytes | Path,
        *,
        source_name: str | None = None,
        format: str = "FEB",
    ) -> OriginalModel:
        if isinstance(source, bytes):
            return cls.from_bytes(source, source_name=source_name, format=format)
        if isinstance(source, str) and source.lstrip("\ufeff \t\r\n").startswith("<"):
            return cls.from_bytes(source.encode("utf-8"), source_name=source_name, format=format)
        return cls.from_path(source, format=format, source_name=source_name)

    @property
    def path(self) -> Path | None:
        return self.source_path

    @property
    def digest(self) -> str:
        return self.sha256

    @property
    def snapshot(self) -> bytes:
        """Return the captured bytes, detached from the source file."""

        return bytes(self._snapshot)

    def read_bytes(self) -> bytes:
        """Read the source for inspection; this method never writes it."""

        if self.source_path is None:
            return self.snapshot
        return self.source_path.read_bytes()

    def verify(self) -> bool:
        """Return whether the current source still matches the captured digest."""

        try:
            current = self.read_bytes()
        except OSError:
            return False
        return (
            len(current) == self.size_bytes and hashlib.sha256(current).hexdigest() == self.sha256
        )

    @property
    def unchanged(self) -> bool:
        return self.verify()

    def to_dict(self) -> dict[str, object]:
        return {
            "source_path": None if self.source_path is None else str(self.source_path),
            "source_name": self.source_name,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "format": self.format,
        }


@dataclass(frozen=True, slots=True)
class ModelChange:
    """One explicit, evidence-backed change in a derived-model plan."""

    target: str
    value: object
    reason: str
    evidence: tuple[EvidenceProvenance, ...] = ()
    impact: IntentImpact | str = IntentImpact.INTENT_PRESERVING

    def __post_init__(self) -> None:
        if not isinstance(self.target, str) or not self.target.strip():
            raise ValueError("change target must be a non-empty string")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("change reason must be a non-empty string")
        try:
            impact = IntentImpact(self.impact)
        except (TypeError, ValueError) as error:
            raise ValueError("impact must be a supported IntentImpact value") from error
        evidence = normalise_provenance(self.evidence)
        object.__setattr__(self, "impact", impact)
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(self, "value", freeze_value(self.value))

    @property
    def requires_ask_and_block(self) -> bool:
        return IntentImpact(self.impact) is IntentImpact.INTENT_CHANGING

    @property
    def intent_impact(self) -> IntentImpact:
        return IntentImpact(self.impact)

    def to_dict(self) -> dict[str, object]:
        return {
            "target": self.target,
            "value": thaw_json(self.value),
            "reason": self.reason,
            "evidence": [item.to_dict() for item in self.evidence],
            "impact": IntentImpact(self.impact).value,
        }


@dataclass(frozen=True, slots=True)
class DerivedModelPlan:
    """A non-mutating plan that derives a model from an immutable original."""

    original: OriginalModel
    destination: Path | None = None
    changes: tuple[ModelChange, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.original, OriginalModel):
            raise TypeError("original must be an OriginalModel")
        if self.destination is not None:
            object.__setattr__(self, "destination", Path(self.destination))
        changes = tuple(self.changes)
        if not all(isinstance(item, ModelChange) for item in changes):
            raise TypeError("changes must contain ModelChange records")
        object.__setattr__(self, "changes", changes)

    @property
    def source(self) -> OriginalModel:
        return self.original

    @property
    def original_sha256(self) -> str:
        return self.original.sha256

    @property
    def is_intent_preserving(self) -> bool:
        return all(
            IntentImpact(item.impact) is IntentImpact.INTENT_PRESERVING for item in self.changes
        )

    @property
    def requires_ask_and_block(self) -> bool:
        return any(item.requires_ask_and_block for item in self.changes)

    @property
    def intent_sensitive_changes(self) -> tuple[ModelChange, ...]:
        return tuple(
            item
            for item in self.changes
            if IntentImpact(item.impact) is IntentImpact.INTENT_SENSITIVE
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "original": self.original.to_dict(),
            "destination": None if self.destination is None else self.destination.as_posix(),
            "changes": [item.to_dict() for item in self.changes],
            "is_intent_preserving": self.is_intent_preserving,
            "requires_ask_and_block": self.requires_ask_and_block,
        }


def plan_derived_model(
    original: OriginalModel | str | bytes | Path,
    changes: Iterable[ModelChange] | Mapping[str, object] = (),
    *,
    destination: str | Path | None = None,
) -> DerivedModelPlan:
    """Create a derived plan while leaving the original source untouched."""

    original_record = (
        original if isinstance(original, OriginalModel) else OriginalModel.from_source(original)
    )
    if isinstance(changes, Mapping):
        change_records = tuple(
            ModelChange(
                target=str(target),
                value=value,
                reason="explicit plan mapping",
            )
            for target, value in changes.items()
        )
    else:
        change_records = tuple(changes)
    return DerivedModelPlan(
        original=original_record,
        destination=None if destination is None else Path(destination),
        changes=change_records,
    )


derive_model_plan = plan_derived_model
create_derived_model_plan = plan_derived_model
OriginalInput = OriginalModel
ChangeProposal = ModelChange
DerivedPlan = DerivedModelPlan
DerivedModel = DerivedModelPlan


__all__ = [
    "ChangeProposal",
    "DerivedModel",
    "DerivedModelPlan",
    "DerivedPlan",
    "ModelChange",
    "OriginalInput",
    "OriginalModel",
    "derive_model_plan",
    "create_derived_model_plan",
    "plan_derived_model",
]
