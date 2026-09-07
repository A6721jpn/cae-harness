"""Result-manifest, reading, and quality-assessment records."""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from .artifacts import FileEntry
from .canonical import canonical_bytes
from .compatibility import ToolIdentity
from .spatial import FrameId

SCHEMA_VERSION = "1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ResultsValidationError(ValueError):
    """Raised when result or quality records are malformed."""


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ResultsValidationError(
            f"{field_name} must be non-empty text without surrounding whitespace"
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ResultsValidationError(f"{field_name} contains a control character")
    return value


def _digest(value: object, field_name: str) -> str:
    value = _text(value, field_name)
    if _SHA256.fullmatch(value) is None:
        raise ResultsValidationError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def _finite(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ResultsValidationError(f"{field_name} must be finite numeric data")
    result = float(value)
    if not math.isfinite(result):
        raise ResultsValidationError(f"{field_name} must be finite numeric data")
    return result


def _sequence(value: object, field_name: str) -> tuple[object, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ResultsValidationError(f"{field_name} must be a sequence")
    return tuple(value)


class ReadStatus(str, Enum):
    VALIDATED = "VALIDATED"
    UNVERIFIED = "UNVERIFIED"
    FAILED = "FAILED"


class AssessmentStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNVERIFIED = "UNVERIFIED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True, slots=True)
class OutputObservation:
    output_id: str
    location: str
    value_type: str
    unit: str
    frame: FrameId
    measure_id: str
    state_count: int

    def __post_init__(self) -> None:
        for field_name in ("output_id", "location", "value_type", "unit", "measure_id"):
            object.__setattr__(self, field_name, _text(getattr(self, field_name), field_name))
        if not isinstance(self.frame, FrameId):
            raise ResultsValidationError("frame must be a FrameId")
        if (
            isinstance(self.state_count, bool)
            or not isinstance(self.state_count, int)
            or self.state_count < 0
        ):
            raise ResultsValidationError("state_count must be a nonnegative integer")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "output_id": self.output_id,
            "location": self.location,
            "value_type": self.value_type,
            "unit": self.unit,
            "frame": self.frame.value,
            "measure_id": self.measure_id,
            "state_count": self.state_count,
        }


@dataclass(frozen=True, slots=True)
class ReadResult:
    """Validated reader output; it is a manifest candidate, not publication authority."""

    status: ReadStatus
    reader: ToolIdentity
    observations: Sequence[OutputObservation]
    diagnostics: Sequence[str]

    def __post_init__(self) -> None:
        if not isinstance(self.status, ReadStatus):
            raise ResultsValidationError("status must be a ReadStatus")
        if not isinstance(self.reader, ToolIdentity):
            raise ResultsValidationError("reader must be a ToolIdentity")
        observations = tuple(self.observations)
        if any(not isinstance(item, OutputObservation) for item in observations):
            raise ResultsValidationError("observations contains an invalid value")
        if len({item.output_id for item in observations}) != len(observations):
            raise ResultsValidationError("observations contains duplicate output IDs")
        diagnostics = tuple(_text(item, "diagnostics[]") for item in self.diagnostics)
        if self.status is ReadStatus.FAILED and not diagnostics:
            raise ResultsValidationError("failed read results require diagnostics")
        object.__setattr__(self, "observations", observations)
        object.__setattr__(self, "diagnostics", diagnostics)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": self.status.value,
            "reader": self.reader.to_dict(),
            "observations": [item.to_dict() for item in self.observations],
            "diagnostics": list(self.diagnostics),
        }


@dataclass(frozen=True, slots=True)
class ResultManifest:
    """Candidate manifest bound to an attempt and exact input bundle."""

    manifest_id: str
    attempt_id: str
    bundle_digest: str
    files: Sequence[FileEntry]
    read_result: ReadResult

    def __post_init__(self) -> None:
        for field_name in ("manifest_id", "attempt_id"):
            object.__setattr__(self, field_name, _text(getattr(self, field_name), field_name))
        object.__setattr__(self, "bundle_digest", _digest(self.bundle_digest, "bundle_digest"))
        files = tuple(self.files)
        if not files or any(not isinstance(item, FileEntry) for item in files):
            raise ResultsValidationError("files must be a non-empty sequence of FileEntry values")
        if len({item.logical_path for item in files}) != len(files):
            raise ResultsValidationError("files contains duplicate logical paths")
        if not isinstance(self.read_result, ReadResult):
            raise ResultsValidationError("read_result must be a ReadResult")
        object.__setattr__(self, "files", tuple(sorted(files, key=lambda item: item.logical_path)))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "manifest_id": self.manifest_id,
            "attempt_id": self.attempt_id,
            "bundle_digest": self.bundle_digest,
            "files": [item.to_dict() for item in self.files],
            "read_result": self.read_result.to_dict(),
        }

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


@dataclass(frozen=True, slots=True)
class MeasuredValue:
    metric_id: str
    value: float
    unit: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "metric_id", _text(self.metric_id, "metric_id"))
        object.__setattr__(self, "value", _finite(self.value, "value"))
        object.__setattr__(self, "unit", _text(self.unit, "unit"))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "metric_id": self.metric_id,
            "value": self.value,
            "unit": self.unit,
        }


@dataclass(frozen=True, slots=True)
class CriterionAssessment:
    criterion_id: str
    dimension: str
    status: AssessmentStatus
    measured: Sequence[MeasuredValue]
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "criterion_id", _text(self.criterion_id, "criterion_id"))
        dimension = _text(self.dimension, "dimension")
        if dimension not in {"execution", "numeric", "applicability"}:
            raise ResultsValidationError("dimension must be execution, numeric, or applicability")
        object.__setattr__(self, "dimension", dimension)
        if not isinstance(self.status, AssessmentStatus):
            raise ResultsValidationError("status must be an AssessmentStatus")
        measured = tuple(self.measured)
        if any(not isinstance(item, MeasuredValue) for item in measured):
            raise ResultsValidationError("measured contains an invalid value")
        object.__setattr__(self, "measured", measured)
        object.__setattr__(self, "reason", _text(self.reason, "reason"))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "criterion_id": self.criterion_id,
            "dimension": self.dimension,
            "status": self.status.value,
            "measured": [item.to_dict() for item in self.measured],
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class QualityAssessment:
    """Immutable quality result with execution/numeric/applicability separated."""

    assessment_id: str
    manifest_id: str
    policy_digest: str
    criteria: Sequence[CriterionAssessment]
    overall_status: AssessmentStatus

    def __post_init__(self) -> None:
        object.__setattr__(self, "assessment_id", _text(self.assessment_id, "assessment_id"))
        object.__setattr__(self, "manifest_id", _text(self.manifest_id, "manifest_id"))
        object.__setattr__(self, "policy_digest", _digest(self.policy_digest, "policy_digest"))
        criteria = tuple(self.criteria)
        if not criteria or any(not isinstance(item, CriterionAssessment) for item in criteria):
            raise ResultsValidationError("criteria must be a non-empty sequence of assessments")
        if len({item.criterion_id for item in criteria}) != len(criteria):
            raise ResultsValidationError("criteria contains duplicate IDs")
        if not isinstance(self.overall_status, AssessmentStatus):
            raise ResultsValidationError("overall_status must be an AssessmentStatus")
        object.__setattr__(self, "criteria", criteria)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "assessment_id": self.assessment_id,
            "manifest_id": self.manifest_id,
            "policy_digest": self.policy_digest,
            "criteria": [item.to_dict() for item in self.criteria],
            "overall_status": self.overall_status.value,
        }

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


__all__ = [
    "SCHEMA_VERSION",
    "AssessmentStatus",
    "CriterionAssessment",
    "MeasuredValue",
    "OutputObservation",
    "QualityAssessment",
    "ReadResult",
    "ReadStatus",
    "ResultManifest",
    "ResultsValidationError",
]
