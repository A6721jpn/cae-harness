"""Immutable result-reporting contracts.

Reporting consumes validation objects produced by other phases.  It never
starts a solver, opens an XPLT file, or writes a report.  In particular, a
``ResultReport`` is only a typed projection of supplied evidence; it is not an
additional source of physical meaning.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from os import fspath
from pathlib import Path
from types import MappingProxyType
from typing import Self

from febio_cae_harness.solver import (
    FbsValidation,
    LogValidation,
    SolverRunResult,
)

__all__ = [
    "AttemptIdentity",
    "EvidenceKind",
    "EvidenceProvenance",
    "EvidenceReference",
    "FreshOutputValidation",
    "ReportEvidence",
    "ReportProvenance",
    "ResultReport",
    "SuccessGateEvaluation",
]


class EvidenceProvenance(StrEnum):
    """Origin/authority state carried by report evidence.

    ``SYNTHETIC`` and ``UNVERIFIED`` are intentionally first-class values so
    a report can be useful for fixtures and diagnostics without being
    mistaken for a real FEBio/FBS result.  Only ``OFFICIAL`` can satisfy the
    report's final success gate.
    """

    OFFICIAL = "official"
    SYNTHETIC = "synthetic"
    UNVERIFIED = "unverified"


ReportProvenance = EvidenceProvenance


class EvidenceKind(StrEnum):
    """Required categories of intent/result evidence."""

    MESH = "mesh"
    JACOBIAN = "jacobian"
    ROI = "roi"
    EVALUATION = "evaluation"


_PROVENANCE_ALIASES = {
    "official": EvidenceProvenance.OFFICIAL,
    "official-fbs": EvidenceProvenance.OFFICIAL,
    "fbs": EvidenceProvenance.OFFICIAL,
    "synthetic": EvidenceProvenance.SYNTHETIC,
    "synthetic-adapter": EvidenceProvenance.SYNTHETIC,
    "fixture": EvidenceProvenance.SYNTHETIC,
    "unverified": EvidenceProvenance.UNVERIFIED,
    "unknown": EvidenceProvenance.UNVERIFIED,
}
_KIND_ALIASES = {
    "mesh": EvidenceKind.MESH,
    "mesh-quality": EvidenceKind.MESH,
    "mesh_quality": EvidenceKind.MESH,
    "jacobian": EvidenceKind.JACOBIAN,
    "jacobian-quality": EvidenceKind.JACOBIAN,
    "jacobian_quality": EvidenceKind.JACOBIAN,
    "all-integral-point-jacobian": EvidenceKind.JACOBIAN,
    "all_integration_point_jacobian": EvidenceKind.JACOBIAN,
    "all-integration-point-jacobian": EvidenceKind.JACOBIAN,
    "roi": EvidenceKind.ROI,
    "region-of-interest": EvidenceKind.ROI,
    "region_of_interest": EvidenceKind.ROI,
    "evaluation": EvidenceKind.EVALUATION,
    "evaluation-quantities": EvidenceKind.EVALUATION,
    "evaluation_quantities": EvidenceKind.EVALUATION,
}


def _provenance(value: EvidenceProvenance | str) -> EvidenceProvenance:
    if isinstance(value, EvidenceProvenance):
        return value
    if not isinstance(value, str):
        raise TypeError("provenance must be an EvidenceProvenance or string")
    normalised = value.strip().casefold()
    try:
        return _PROVENANCE_ALIASES[normalised]
    except KeyError as error:
        raise ValueError("provenance must be official, synthetic, or unverified") from error


def _kind(value: EvidenceKind | str) -> EvidenceKind:
    if isinstance(value, EvidenceKind):
        return value
    if not isinstance(value, str):
        raise TypeError("evidence kind must be an EvidenceKind or string")
    normalised = value.strip().casefold()
    try:
        return _KIND_ALIASES[normalised]
    except KeyError as error:
        raise ValueError(
            "evidence kind is not one of mesh, jacobian, roi, or evaluation"
        ) from error


def _required_text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _path(value: str | Path, name: str) -> Path:
    if not isinstance(value, (str, Path)):
        raise TypeError(f"{name} must be a path or string")
    path = Path(value)
    if not str(path).strip():
        raise ValueError(f"{name} must be a non-empty path")
    return path


def _bool(value: bool, name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be a bool")
    return value


@dataclass(frozen=True, slots=True)
class AttemptIdentity:
    """The case, bound intent, and attempt that own a report."""

    case_id: str
    intent_id: str
    attempt_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", _required_text(self.case_id, "case_id"))
        object.__setattr__(self, "intent_id", _required_text(self.intent_id, "intent_id"))
        object.__setattr__(self, "attempt_id", _required_text(self.attempt_id, "attempt_id"))

    @property
    def intent_digest(self) -> str:
        """Alias for callers that identify an intent by its digest."""

        return self.intent_id

    @property
    def intent_sha256(self) -> str:
        """Alias for evidence stores that use a SHA-256 intent identity."""

        return self.intent_id

    @property
    def case(self) -> str:
        return self.case_id

    @property
    def intent(self) -> str:
        return self.intent_id

    @property
    def attempt(self) -> str:
        return self.attempt_id

    def to_dict(self) -> dict[str, str]:
        return {
            "case_id": self.case_id,
            "intent_id": self.intent_id,
            "attempt_id": self.attempt_id,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> Self:
        if not isinstance(value, Mapping):
            raise TypeError("attempt identity must be a mapping")
        values = dict(value)
        aliases = {
            "case": "case_id",
            "intent": "intent_id",
            "intent_digest": "intent_id",
            "attempt": "attempt_id",
        }
        for alias, canonical in aliases.items():
            if alias in values and canonical not in values:
                values[canonical] = values[alias]
            values.pop(alias, None)
        expected = {"case_id", "intent_id", "attempt_id"}
        unknown = set(values) - expected
        if unknown:
            names = ", ".join(sorted(str(item) for item in unknown))
            raise TypeError(f"unknown attempt identity field(s): {names}")
        return cls(**values)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    """A supplied, identity-bound reference to one result-evidence record.

    ``verified`` and ``satisfies_intent`` are assertions made by the caller;
    this class deliberately does not inspect the referenced path.  A
    synthetic or unverified reference remains visible in the final report and
    cannot pass the official success gate.
    """

    kind: EvidenceKind | str
    reference: str | Path
    case_id: str
    intent_id: str
    attempt_id: str
    verified: bool = False
    satisfies_intent: bool = False
    provenance: EvidenceProvenance | str = EvidenceProvenance.UNVERIFIED
    sha256: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _kind(self.kind))
        object.__setattr__(self, "reference", _path(self.reference, "reference"))
        object.__setattr__(self, "case_id", _required_text(self.case_id, "case_id"))
        object.__setattr__(self, "intent_id", _required_text(self.intent_id, "intent_id"))
        object.__setattr__(self, "attempt_id", _required_text(self.attempt_id, "attempt_id"))
        object.__setattr__(self, "verified", _bool(self.verified, "verified"))
        object.__setattr__(
            self, "satisfies_intent", _bool(self.satisfies_intent, "satisfies_intent")
        )
        object.__setattr__(self, "provenance", _provenance(self.provenance))
        if self.sha256 is not None:
            object.__setattr__(self, "sha256", _required_text(self.sha256, "sha256"))

    @property
    def identity(self) -> AttemptIdentity:
        return AttemptIdentity(self.case_id, self.intent_id, self.attempt_id)

    @property
    def canonical_kind(self) -> EvidenceKind:
        return _kind(self.kind)

    @property
    def normalized_provenance(self) -> EvidenceProvenance:
        return _provenance(self.provenance)

    @property
    def authoritative(self) -> bool:
        return (
            self.verified
            and self.satisfies_intent
            and self.normalized_provenance is EvidenceProvenance.OFFICIAL
        )

    @property
    def is_authoritative(self) -> bool:
        return self.authoritative

    @property
    def bound(self) -> bool:
        return bool(self.case_id and self.intent_id and self.attempt_id)

    def matches(self, identity: AttemptIdentity) -> bool:
        return (
            self.case_id == identity.case_id
            and self.intent_id == identity.intent_id
            and self.attempt_id == identity.attempt_id
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> Self:
        if not isinstance(value, Mapping):
            raise TypeError("evidence reference must be a mapping")
        values = dict(value)
        aliases = {
            "path": "reference",
            "locator": "reference",
            "intent_digest": "intent_id",
            "evidence_kind": "kind",
            "intent_satisfied": "satisfies_intent",
            "authoritative": "verified",
        }
        for alias, canonical in aliases.items():
            if alias in values and canonical not in values:
                values[canonical] = values[alias]
            values.pop(alias, None)
        expected = {
            "kind",
            "reference",
            "case_id",
            "intent_id",
            "attempt_id",
            "verified",
            "satisfies_intent",
            "provenance",
            "sha256",
        }
        unknown = set(values) - expected
        if unknown:
            names = ", ".join(sorted(str(item) for item in unknown))
            raise TypeError(f"unknown evidence reference field(s): {names}")
        return cls(**values)  # type: ignore[arg-type]

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "kind": self.canonical_kind.value,
            "reference": fspath(self.reference),
            "case_id": self.case_id,
            "intent_id": self.intent_id,
            "attempt_id": self.attempt_id,
            "verified": self.verified,
            "satisfies_intent": self.satisfies_intent,
            "provenance": self.normalized_provenance.value,
        }
        if self.sha256 is not None:
            result["sha256"] = self.sha256
        return result


# Descriptive aliases make the same small contract usable by each downstream
# reporting consumer without creating duplicate mutable types.
MeshEvidence = EvidenceReference
JacobianEvidence = EvidenceReference
RoiEvidence = EvidenceReference
EvaluationEvidence = EvidenceReference
EvidenceRef = EvidenceReference
ReportEvidenceReference = EvidenceReference


@dataclass(frozen=True, slots=True)
class FreshOutputValidation:
    """Supplied freshness and LOG/XPLT path validation for one attempt.

    No path existence check occurs here.  ``log_present`` and ``xplt_present``
    are explicit evidence supplied by the solver/output phase, while
    ``log_validation`` is the parsed LOG evidence.  Defaults are intentionally
    conservative for presence/freshness and do not manufacture success.
    """

    attempt_id: str
    log_path: str | Path
    xplt_path: str | Path
    log_fresh: bool = False
    xplt_fresh: bool = False
    log_validation: LogValidation | None = None
    log_present: bool = True
    xplt_present: bool = True
    provenance: EvidenceProvenance | str = EvidenceProvenance.OFFICIAL

    def __post_init__(self) -> None:
        object.__setattr__(self, "attempt_id", _required_text(self.attempt_id, "attempt_id"))
        object.__setattr__(self, "log_path", _path(self.log_path, "log_path"))
        object.__setattr__(self, "xplt_path", _path(self.xplt_path, "xplt_path"))
        object.__setattr__(self, "log_fresh", _bool(self.log_fresh, "log_fresh"))
        object.__setattr__(self, "xplt_fresh", _bool(self.xplt_fresh, "xplt_fresh"))
        object.__setattr__(self, "log_present", _bool(self.log_present, "log_present"))
        object.__setattr__(self, "xplt_present", _bool(self.xplt_present, "xplt_present"))
        object.__setattr__(self, "provenance", _provenance(self.provenance))

    @property
    def log_is_fresh(self) -> bool:
        return self.log_fresh

    @property
    def xplt_is_fresh(self) -> bool:
        return self.xplt_fresh

    @property
    def normalized_provenance(self) -> EvidenceProvenance:
        return _provenance(self.provenance)

    @property
    def fresh(self) -> bool:
        return self.log_fresh and self.xplt_fresh and self.log_present and self.xplt_present

    @property
    def valid(self) -> bool:
        return self.fresh and self.log_path != self.xplt_path

    @property
    def outputs_fresh(self) -> bool:
        return self.fresh

    @property
    def log_created(self) -> bool:
        return self.log_fresh

    @property
    def xplt_created(self) -> bool:
        return self.xplt_fresh

    @property
    def both_fresh(self) -> bool:
        return self.fresh

    def matches(self, identity: AttemptIdentity, solver_result: SolverRunResult) -> bool:
        return (
            self.attempt_id == identity.attempt_id
            and self.log_path == solver_result.log_path
            and self.xplt_path == solver_result.xplt_path
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "attempt_id": self.attempt_id,
            "log_path": fspath(self.log_path),
            "xplt_path": fspath(self.xplt_path),
            "log_fresh": self.log_fresh,
            "xplt_fresh": self.xplt_fresh,
            "log_present": self.log_present,
            "xplt_present": self.xplt_present,
            "provenance": self.normalized_provenance.value,
        }


OutputFreshness = FreshOutputValidation
OutputFreshValidation = FreshOutputValidation
FreshnessValidation = FreshOutputValidation
ResultOutputValidation = FreshOutputValidation


@dataclass(frozen=True, slots=True)
class ReportEvidence:
    """The four evidence categories required by the success authority."""

    mesh: EvidenceReference | None = None
    jacobian: EvidenceReference | None = None
    roi: EvidenceReference | None = None
    evaluation: EvidenceReference | None = None
    additional: tuple[EvidenceReference, ...] = ()
    identity: AttemptIdentity | None = None

    def __post_init__(self) -> None:
        for name in ("mesh", "jacobian", "roi", "evaluation"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, EvidenceReference):
                raise TypeError(f"{name} evidence must be an EvidenceReference or None")
        normalised = tuple(self.additional)
        if any(not isinstance(value, EvidenceReference) for value in normalised):
            raise TypeError("additional evidence must contain EvidenceReference values")
        object.__setattr__(self, "additional", normalised)
        if self.identity is not None and not isinstance(self.identity, AttemptIdentity):
            raise TypeError("evidence identity must be an AttemptIdentity or None")

    @property
    def references(self) -> tuple[EvidenceReference, ...]:
        required = tuple(
            value
            for value in (self.mesh, self.jacobian, self.roi, self.evaluation)
            if value is not None
        )
        return required + self.additional

    @property
    def all_references(self) -> tuple[EvidenceReference, ...]:
        return self.references

    def for_kind(self, kind: EvidenceKind | str) -> EvidenceReference | None:
        category = _kind(kind)
        return {
            EvidenceKind.MESH: self.mesh,
            EvidenceKind.JACOBIAN: self.jacobian,
            EvidenceKind.ROI: self.roi,
            EvidenceKind.EVALUATION: self.evaluation,
        }[category]

    def matches(self, identity: AttemptIdentity) -> bool:
        return (self.identity is None or self.identity == identity) and all(
            reference.matches(identity) for reference in self.references
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> Self:
        if not isinstance(value, Mapping):
            raise TypeError("report evidence must be a mapping")
        aliases = {
            "mesh_quality": "mesh",
            "mesh_evidence": "mesh",
            "jacobian_quality": "jacobian",
            "jacobian_evidence": "jacobian",
            "roi_evidence": "roi",
            "evaluation_quantities": "evaluation",
            "evaluation_evidence": "evaluation",
            "references": "additional",
        }
        values = dict(value)
        for alias, canonical in aliases.items():
            if alias in values and canonical not in values:
                values[canonical] = values[alias]
            values.pop(alias, None)
        for name in ("mesh", "jacobian", "roi", "evaluation"):
            raw = values.get(name)
            if isinstance(raw, Mapping):
                values[name] = EvidenceReference.from_mapping(raw)
        additional = values.get("additional", ())
        if isinstance(additional, (list, tuple)):
            values["additional"] = tuple(
                EvidenceReference.from_mapping(raw) if isinstance(raw, Mapping) else raw
                for raw in additional
            )
        raw_identity = values.get("identity")
        if isinstance(raw_identity, Mapping):
            values["identity"] = AttemptIdentity.from_mapping(raw_identity)
        expected = {"mesh", "jacobian", "roi", "evaluation", "additional", "identity"}
        unknown = set(values) - expected
        if unknown:
            names = ", ".join(sorted(str(item) for item in unknown))
            raise TypeError(f"unknown report evidence field(s): {names}")
        return cls(**values)  # type: ignore[arg-type]

    def to_dict(self) -> dict[str, object]:
        return {
            "mesh": self.mesh.to_dict() if self.mesh is not None else None,
            "jacobian": self.jacobian.to_dict() if self.jacobian is not None else None,
            "roi": self.roi.to_dict() if self.roi is not None else None,
            "evaluation": self.evaluation.to_dict() if self.evaluation is not None else None,
            "additional": [value.to_dict() for value in self.additional],
            "identity": self.identity.to_dict() if self.identity is not None else None,
        }


EvidenceReferences = ReportEvidence
ResultEvidence = ReportEvidence


def _json_value(value: object) -> object:
    """Convert nested validation values without reading external state."""

    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, Path):
        return fspath(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, StrEnum):
        return value.value
    return str(value)


@dataclass(frozen=True, slots=True)
class SuccessGateEvaluation:
    """Immutable result of evaluating every authority condition."""

    passed: bool
    checks: Mapping[str, bool]
    failures: tuple[str, ...]
    provenance: EvidenceProvenance

    def __post_init__(self) -> None:
        checks = {str(name): _bool(value, f"check {name}") for name, value in self.checks.items()}
        object.__setattr__(self, "checks", MappingProxyType(checks))
        object.__setattr__(self, "passed", _bool(self.passed, "passed"))
        object.__setattr__(self, "failures", tuple(str(value) for value in self.failures))
        object.__setattr__(self, "provenance", _provenance(self.provenance))
        if self.passed != (not self.failures and all(checks.values())):
            raise ValueError("passed must equal the conjunction of checks and absence of failures")

    @property
    def success(self) -> bool:
        return self.passed

    def __bool__(self) -> bool:
        return self.passed

    @property
    def valid(self) -> bool:
        return self.passed

    @property
    def failed_checks(self) -> tuple[str, ...]:
        return tuple(name for name, passed in self.checks.items() if not passed)

    @property
    def issues(self) -> tuple[str, ...]:
        return self.failures

    @property
    def reasons(self) -> tuple[str, ...]:
        return self.failures

    @property
    def all_passed(self) -> bool:
        return self.passed

    @property
    def is_synthetic(self) -> bool:
        return self.provenance is EvidenceProvenance.SYNTHETIC

    @property
    def is_unverified(self) -> bool:
        return self.provenance is EvidenceProvenance.UNVERIFIED

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "success": self.success,
            "checks": dict(self.checks),
            "failed_checks": list(self.failed_checks),
            "failures": list(self.failures),
            "provenance": self.provenance.value,
        }


GateEvaluation = SuccessGateEvaluation
SuccessGateResult = SuccessGateEvaluation


@dataclass(frozen=True, slots=True)
class ResultReport:
    """Pure assembled report for one attempt and its supplied evidence."""

    identity: AttemptIdentity
    solver_result: SolverRunResult
    fresh_outputs: FreshOutputValidation | None
    fbs_validation: FbsValidation | None
    evidence: ReportEvidence
    gates: SuccessGateEvaluation
    provenance: EvidenceProvenance

    @property
    def attempt_identity(self) -> AttemptIdentity:
        return self.identity

    @property
    def solver(self) -> SolverRunResult:
        return self.solver_result

    @property
    def output_validation(self) -> FreshOutputValidation | None:
        return self.fresh_outputs

    @property
    def fbs(self) -> FbsValidation | None:
        return self.fbs_validation

    @property
    def gate_evaluation(self) -> SuccessGateEvaluation:
        return self.gates

    @property
    def success_gate(self) -> SuccessGateEvaluation:
        return self.gates

    @property
    def success(self) -> bool:
        return self.gates.success

    @property
    def passed(self) -> bool:
        return self.success

    @property
    def is_synthetic(self) -> bool:
        return self.provenance is EvidenceProvenance.SYNTHETIC

    @property
    def is_unverified(self) -> bool:
        return self.provenance is EvidenceProvenance.UNVERIFIED

    @property
    def unverified(self) -> bool:
        return self.is_unverified or self.is_synthetic

    @property
    def verified(self) -> bool:
        return self.success and self.provenance is EvidenceProvenance.OFFICIAL

    @property
    def synthetic(self) -> bool:
        return self.is_synthetic

    def to_dict(self) -> dict[str, object]:
        fbs: dict[str, object] | None
        if self.fbs_validation is None:
            fbs = None
        else:
            fbs = {
                "xplt_path": fspath(self.fbs_validation.xplt_path),
                "requested_fields": list(self.fbs_validation.requested_fields),
                "available_fields": list(self.fbs_validation.available_fields),
                "values": _json_value(self.fbs_validation.values),
                "missing_fields": list(self.fbs_validation.missing_fields),
                "non_finite_fields": list(self.fbs_validation.non_finite_fields),
                "valid": self.fbs_validation.valid,
                "official": self.fbs_validation.official,
                "provenance": self.fbs_validation.provenance,
                "issues": list(self.fbs_validation.issues),
            }
        return {
            "identity": self.identity.to_dict(),
            "provenance": self.provenance.value,
            "success": self.success,
            "solver": {
                "state": self.solver_result.state.value,
                "classification": self.solver_result.classification.value,
                "return_code": self.solver_result.return_code,
                "log_path": fspath(self.solver_result.log_path),
                "xplt_path": fspath(self.solver_result.xplt_path),
                "error": self.solver_result.error,
            },
            "fresh_outputs": (
                self.fresh_outputs.to_dict() if self.fresh_outputs is not None else None
            ),
            "fbs_validation": fbs,
            "evidence": self.evidence.to_dict(),
            "gates": self.gates.to_dict(),
        }


Report = ResultReport
ReportAssembly = ResultReport
AssembledReport = ResultReport
