"""Pure, immutable records for a synthetic negative-Jacobian diagnostic."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from types import MappingProxyType

__all__ = [
    "NegativeJacobianObservation",
    "NegativeJacobianDiagnostic",
    "diagnose_negative_jacobian",
]
_RELATION_FIELDS = tuple(
    "roi_relation_evidence contact_relation_evidence constraint_relation_evidence".split()  # noqa: SIM905
)
_REPAIR_CHECKLIST = tuple(
    "geometry_delta mesh_quality load_path reactions displacement contact "  # noqa: SIM905
    "evaluation_quantities".split()  # noqa: SIM905
)


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _finite_float(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (OverflowError, ValueError) as error:
        raise ValueError(f"{name} must be a finite number") from error
    if not isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _finite_int(value: object, name: str) -> int:
    result = _finite_float(value, name)
    if not result.is_integer():
        raise TypeError(f"{name} must be an integer")
    return int(result)


def _freeze_json(value: object) -> object:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("evidence values must be finite")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key.strip():
                raise TypeError("evidence mapping keys must be non-empty strings")
            frozen[key.strip()] = _freeze_json(item)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    raise TypeError("evidence values must be JSON-compatible")


def _metrics(value: object) -> Mapping[str, float]:
    if value is None:
        return MappingProxyType({})
    if not isinstance(value, Mapping):
        raise TypeError("surrounding_mesh_metrics must be a mapping")
    result: dict[str, float] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            raise TypeError("mesh metric names must be non-empty strings")
        result[key.strip()] = _finite_float(item, f"mesh metric {key!r}")
    return MappingProxyType(result)


def _labels(value: object, name: str) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise TypeError(f"{name} must be a sequence of strings")
    labels = tuple(value)
    if not all(isinstance(item, str) and item.strip() for item in labels):
        raise ValueError(f"{name} must contain non-empty strings")
    return tuple(item.strip() for item in labels)


def _has_evidence(value: object | None) -> bool:
    return value is not None and (not isinstance(value, (str, Mapping)) or bool(value))


@dataclass(frozen=True, slots=True)
class NegativeJacobianObservation:
    attempt_id: str
    log_evidence_digest: str
    initial_mesh_valid: bool
    failure_step: int
    failure_time: float
    element_id: int
    integration_point_jacobian: float
    surrounding_mesh_metrics: Mapping[str, float]
    roi_relation_evidence: object | None
    contact_relation_evidence: object | None
    constraint_relation_evidence: object | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "attempt_id", _required_text(self.attempt_id, "attempt_id"))
        object.__setattr__(
            self,
            "log_evidence_digest",
            _required_text(self.log_evidence_digest, "LOG evidence digest"),
        )
        if not isinstance(self.initial_mesh_valid, bool):
            raise TypeError("initial_mesh_valid must be a bool")
        step = _finite_int(self.failure_step, "failure_step")
        if step < 0:
            raise ValueError("failure_step must not be negative")
        failure_time = _finite_float(self.failure_time, "failure_time")
        if failure_time < 0:
            raise ValueError("failure_time must not be negative")
        element_id = _finite_int(self.element_id, "element_id")
        if element_id <= 0:
            raise ValueError("element_id must be positive")
        jacobian = _finite_float(self.integration_point_jacobian, "integration_point_jacobian")
        if jacobian >= 0:
            raise ValueError("integration_point_jacobian must be negative")
        object.__setattr__(self, "failure_step", step)
        object.__setattr__(self, "failure_time", failure_time)
        object.__setattr__(self, "element_id", element_id)
        object.__setattr__(self, "integration_point_jacobian", jacobian)
        object.__setattr__(
            self, "surrounding_mesh_metrics", _metrics(self.surrounding_mesh_metrics)
        )
        for name in _RELATION_FIELDS:
            object.__setattr__(self, name, _freeze_json(getattr(self, name)))


@dataclass(frozen=True, slots=True)
class NegativeJacobianDiagnostic:
    observation: NegativeJacobianObservation
    observed_fields: Mapping[str, object]
    missing_technical_evidence: tuple[str, ...]
    missing_physical_authority_evidence: tuple[str, ...]
    repair_comparison_checklist: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.observation, NegativeJacobianObservation):
            raise TypeError("observation must be a NegativeJacobianObservation")
        if not isinstance(self.observed_fields, Mapping):
            raise TypeError("observed_fields must be a mapping")
        observed: dict[str, object] = {}
        for key, value in self.observed_fields.items():
            if (
                not isinstance(key, str)
                or key not in NegativeJacobianObservation.__dataclass_fields__
            ):
                raise ValueError("observed_fields contains an unknown field")
            observed[key] = _freeze_json(value)
        object.__setattr__(self, "observed_fields", MappingProxyType(observed))
        object.__setattr__(
            self,
            "missing_technical_evidence",
            _labels(self.missing_technical_evidence, "missing_technical_evidence"),
        )
        object.__setattr__(
            self,
            "missing_physical_authority_evidence",
            _labels(
                self.missing_physical_authority_evidence,
                "missing_physical_authority_evidence",
            ),
        )
        checklist = _labels(self.repair_comparison_checklist, "repair_comparison_checklist")
        if checklist != _REPAIR_CHECKLIST:
            raise ValueError("repair_comparison_checklist must include every required comparison")
        object.__setattr__(self, "repair_comparison_checklist", checklist)


def diagnose_negative_jacobian(
    observation: NegativeJacobianObservation,
) -> NegativeJacobianDiagnostic:
    if not isinstance(observation, NegativeJacobianObservation):
        raise TypeError("observation must be a NegativeJacobianObservation")
    observed: dict[str, object] = {
        "attempt_id": observation.attempt_id,
        "log_evidence_digest": observation.log_evidence_digest,
        "initial_mesh_valid": observation.initial_mesh_valid,
        "failure_step": observation.failure_step,
        "failure_time": observation.failure_time,
        "element_id": observation.element_id,
        "integration_point_jacobian": observation.integration_point_jacobian,
    }
    technical_missing: tuple[str, ...] = ()
    if observation.surrounding_mesh_metrics:
        observed["surrounding_mesh_metrics"] = observation.surrounding_mesh_metrics
    else:
        technical_missing = ("surrounding_mesh_metrics",)
    authority_missing: list[str] = []
    for name in _RELATION_FIELDS:
        value = getattr(observation, name)
        if _has_evidence(value):
            observed[name] = value
        # Raw caller mappings are observed diagnostics, not canonical relation
        # authority.  This phase has no closed relation validator, so every
        # physical relation remains conservatively unresolved.
        authority_missing.append(name)
    return NegativeJacobianDiagnostic(
        observation=observation,
        observed_fields=observed,
        missing_technical_evidence=technical_missing,
        missing_physical_authority_evidence=tuple(authority_missing),
        repair_comparison_checklist=_REPAIR_CHECKLIST,
    )
