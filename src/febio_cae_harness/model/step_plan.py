"""Pure precondition planning for an explicitly specified STEP mesh."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from math import isfinite
from typing import cast

from ..evidence import EvidenceIntegrityError, IntentSnapshotAuthority
from ._immutability import FrozenJSON, freeze_json
from .step import STEPInspection, STEPInspectionError, StepUnitFact
from .types import ASK_AND_BLOCK, EvidenceProvenance, normalise_provenance

_READY = "READY"
_DIGEST_HEX = frozenset("0123456789abcdefABCDEF")
_QUESTIONS = (
    ("element_family", "Which element family should be used for meshing?"),
    ("length_unit", "Which length unit should target_size use?"),
    ("target_size", "What target element size should be used?"),
    ("quality_criteria", "Which mesh quality criteria must be enforced?"),
)
_UNIT_ALIASES = {
    "meter": "m",
    "metre": "m",
    "millimetre": "mm",
    "centimetre": "cm",
    "micrometre": "um",
    "nanometre": "nm",
    "inch": "in",
    "foot": "ft",
}
_KNOWN_UNITS = frozenset({"m", "mm", "cm", "um", "nm", "in", "ft"})
_SUPPORTED_ELEMENT_FAMILIES = frozenset({"tet4", "tet10"})
_SUPPORTED_QUALITY_CRITERIA = frozenset({"min_jacobian", "max_aspect_ratio"})
_STEP_MESH_SETTING_KEYS = frozenset(
    {"step_sha256", "element_family", "length_unit", "target_size", "quality_criteria"}
)


def _text(name: str, value: str | None) -> None:
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise ValueError(f"{name} must be a non-empty string or None")


def _size(value: float | None) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("target_size must be a real number or None")
    if not isfinite(value) or value <= 0:
        raise ValueError("target_size must be finite and positive")


def _quality(value: Mapping[str, object] | None) -> Mapping[str, FrozenJSON] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise TypeError("quality_criteria must be a mapping or None")
    if any(not isinstance(key, str) or not key.strip() for key in value):
        raise ValueError("quality_criteria keys must be non-empty strings")
    unknown = set(value) - _SUPPORTED_QUALITY_CRITERIA
    if unknown:
        raise ValueError("quality_criteria contains an unsupported criterion")
    for name, item in value.items():
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise TypeError("quality_criteria values must be real numbers")
        numeric = float(item)
        if not isfinite(numeric):
            raise ValueError("quality_criteria values must be finite")
        if name == "min_jacobian" and numeric <= 0:
            raise ValueError("quality_criteria min_jacobian must be positive")
        if name == "max_aspect_ratio" and numeric < 1:
            raise ValueError("quality_criteria max_aspect_ratio must be at least one")
    frozen = freeze_json(value)
    if not isinstance(frozen, Mapping):  # pragma: no cover - guarded by input type
        raise TypeError("quality_criteria must be a mapping")
    return frozen


def _evidence(
    value: EvidenceProvenance | Iterable[EvidenceProvenance] | None,
) -> tuple[EvidenceProvenance, ...]:
    result = normalise_provenance(value)
    if not result:
        raise ValueError("evidence must not be empty")
    return result


def _stable(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _stable(value[key]) for key in sorted(value)}
    if isinstance(value, tuple):
        return [_stable(item) for item in value]
    return value


def _unit_token(value: str | None) -> str:
    return "" if value is None else "".join(c for c in value.casefold() if c.isalnum())


def _canonical_unit(value: str | None) -> str:
    token = _unit_token(value)
    return _UNIT_ALIASES.get(token, token)


def _fact_unit(fact: StepUnitFact) -> str:
    value = f"{fact.prefix or ''}{fact.symbol}" if fact.symbol else fact.name
    return _canonical_unit(value)


def _step_length_units(step: STEPInspection) -> tuple[str, ...]:
    units: list[str] = []
    for fact in step.units:
        if not isinstance(fact, StepUnitFact):
            raise STEPInspectionError("STEP contains an invalid unit fact")
        canonical = _fact_unit(fact)
        category = _unit_token(fact.category)
        if (
            "length" in category or category in {"", "unit"} and canonical in _KNOWN_UNITS
        ) and canonical not in units:
            units.append(canonical)
    return tuple(units)


def _questions(
    request: StepMeshingRequest,
    *,
    resolved_length_unit: str | None,
) -> tuple[str, ...]:
    missing: tuple[bool, ...] = (
        request.element_family is None,
        resolved_length_unit is None,
        request.target_size is None,
        request.quality_criteria is None or not request.quality_criteria,
    )
    if not any(item.authoritative for item in request.evidence):
        missing = (True,) * len(_QUESTIONS)
    return tuple(
        question for (_, question), absent in zip(_QUESTIONS, missing, strict=True) if absent
    )


@dataclass(frozen=True, slots=True)
class StepMeshingRequest:
    """Explicit mesh settings and their ordered provenance."""

    element_family: str | None = None
    length_unit: str | None = None
    target_size: float | None = None
    quality_criteria: Mapping[str, object] | None = None
    evidence: tuple[EvidenceProvenance, ...] = ()

    def __post_init__(self) -> None:
        _text("element_family", self.element_family)
        if (
            self.element_family is not None
            and self.element_family not in _SUPPORTED_ELEMENT_FAMILIES
        ):
            raise ValueError("element_family must be exactly tet4 or tet10")
        _text("length_unit", self.length_unit)
        _size(self.target_size)
        object.__setattr__(self, "quality_criteria", _quality(self.quality_criteria))
        object.__setattr__(self, "evidence", _evidence(self.evidence))

    def to_dict(self) -> dict[str, object]:
        return {
            "element_family": self.element_family,
            "length_unit": self.length_unit,
            "target_size": self.target_size,
            "quality_criteria": _stable(self.quality_criteria),
            "evidence": [item.to_dict() for item in self.evidence],
        }


@dataclass(frozen=True, slots=True)
class StepMeshingPlan:
    """Immutable, digest-bound preconditions for a future STEP mesher."""

    step_sha256: str
    status: str
    element_family: str | None
    length_unit: str | None
    target_size: float | None
    quality_criteria: Mapping[str, object] | None
    evidence: tuple[EvidenceProvenance, ...]
    questions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in {_READY, ASK_AND_BLOCK}:
            raise ValueError("status must be READY or ASK_AND_BLOCK")
        object.__setattr__(self, "quality_criteria", _quality(self.quality_criteria))
        object.__setattr__(self, "evidence", _evidence(self.evidence))
        object.__setattr__(self, "questions", tuple(self.questions))

    def to_dict(self) -> dict[str, object]:
        return {
            "step_sha256": self.step_sha256,
            "status": self.status,
            "element_family": self.element_family,
            "length_unit": self.length_unit,
            "target_size": self.target_size,
            "quality_criteria": _stable(self.quality_criteria),
            "evidence": [item.to_dict() for item in self.evidence],
            "questions": list(self.questions),
        }


def plan_step_meshing(
    step: STEPInspection,
    request: StepMeshingRequest,
) -> StepMeshingPlan:
    """Plan only explicit mesh preconditions; never execute a CAD mesher."""

    if not isinstance(step, STEPInspection):
        raise TypeError("step must be a STEPInspection")
    if not isinstance(request, StepMeshingRequest):
        raise TypeError("request must be a StepMeshingRequest")
    if (
        not isinstance(step.sha256, str)
        or len(step.sha256) != 64
        or any(character not in _DIGEST_HEX for character in step.sha256)
    ):
        raise STEPInspectionError("STEP sha256 must be a 64-character hexadecimal digest")
    if not step.entities:
        raise STEPInspectionError("STEP contains no explicit entity assignments")
    step_units = _step_length_units(step)
    if len(step_units) > 1:
        raise STEPInspectionError("conflicting explicit STEP length units")
    if (
        request.length_unit is not None
        and step_units
        and _canonical_unit(request.length_unit) != step_units[0]
    ):
        raise STEPInspectionError("conflicting explicit units: request disagrees with STEP")
    resolved_length_unit = request.length_unit or (step_units[0] if step_units else None)
    questions = _questions(request, resolved_length_unit=resolved_length_unit)
    return StepMeshingPlan(
        step_sha256=step.sha256,
        status=_READY if not questions else ASK_AND_BLOCK,
        element_family=request.element_family,
        length_unit=resolved_length_unit,
        target_size=request.target_size,
        quality_criteria=request.quality_criteria,
        evidence=request.evidence,
        questions=questions,
    )


def plan_authoritative_step_meshing(
    step: STEPInspection,
    snapshot: IntentSnapshotAuthority,
) -> StepMeshingPlan:
    """Plan STEP meshing only from the exact current persisted intent.

    The supported intent shape is
    ``allowed_mesh_changes.mesh.step_meshing``.  A selected STEP is bound by
    its SHA-256; caller-supplied booleans or free-standing request files do not
    authorize a mesh.
    """

    if not isinstance(step, STEPInspection):
        raise TypeError("step must be a STEPInspection")
    if type(snapshot) is not IntentSnapshotAuthority:
        raise TypeError("snapshot must be an exact IntentSnapshotAuthority")
    intent = snapshot.intent
    intent_sha256 = snapshot.intent_sha256
    declarations = intent.allowed_mesh_changes
    settings: Mapping[str, object] | None = None
    if isinstance(declarations, Mapping):
        mesh = declarations.get("mesh")
        if isinstance(mesh, Mapping) and "step_meshing" in mesh:
            candidate = mesh["step_meshing"]
            if not isinstance(candidate, Mapping):
                raise EvidenceIntegrityError("STEP meshing intent must be a mapping")
            settings = candidate
    provenance = EvidenceProvenance(
        "intent_snapshot",
        f"sha256:{intent_sha256}",
        authoritative=True,
    )
    if settings is None:
        request = StepMeshingRequest(evidence=(provenance,))
    else:
        unknown = set(settings) - _STEP_MESH_SETTING_KEYS
        if unknown:
            raise EvidenceIntegrityError("STEP meshing intent contains unexpected fields")
        source_digest = settings.get("step_sha256")
        if source_digest != step.sha256:
            raise EvidenceIntegrityError("STEP meshing intent does not match the selected STEP")
        request = StepMeshingRequest(
            element_family=cast(str | None, settings.get("element_family")),
            length_unit=cast(str | None, settings.get("length_unit")),
            target_size=cast(float | None, settings.get("target_size")),
            quality_criteria=cast(
                Mapping[str, object] | None,
                settings.get("quality_criteria"),
            ),
            evidence=(provenance,),
        )
    plan = plan_step_meshing(step, request)
    if snapshot.intent_sha256 != intent_sha256:
        raise EvidenceIntegrityError("intent snapshot changed during STEP meshing planning")
    return plan


__all__ = [
    "StepMeshingPlan",
    "StepMeshingRequest",
    "plan_authoritative_step_meshing",
    "plan_step_meshing",
]
