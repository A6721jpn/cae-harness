"""Typed evidence and physical-condition records.

These records intentionally do not assign physical meaning to geometry,
element names, or file conventions.  Caller-supplied provenance is a
diagnostic projection; a condition is resolved only through a live,
snapshot-bound completeness authority.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, NoReturn, SupportsIndex, cast

from ..evidence import EvidenceIntegrityError, IntentSnapshotAuthority
from ._immutability import freeze_value, thaw_json

ASK_AND_BLOCK = "ASK_AND_BLOCK"


class PhysicalConditionName(StrEnum):
    """Canonical names for physical conditions that may be required."""

    ENGINEERING_QUESTION = "engineering_question"
    UNITS = "units"
    MATERIAL = "material"
    LOADS = "loads"
    CONSTRAINTS = "constraints"
    CONTACT = "contact"
    ANALYSIS_STEP = "analysis_step"
    ROI = "roi"
    LOAD_PATH = "load_path"
    EVALUATION_QUANTITIES = "evaluation_quantities"
    MESH = "mesh"
    ELEMENT = "element"


ConditionName = PhysicalConditionName


class IntentImpact(StrEnum):
    """Impact classification for a proposed model change."""

    INTENT_PRESERVING = "INTENT_PRESERVING"
    INTENT_SENSITIVE = "INTENT_SENSITIVE"
    INTENT_CHANGING = "INTENT_CHANGING"


@dataclass(frozen=True, slots=True)
class _CompletenessAuthorityBinding:
    authority: CompletenessAuthority
    snapshot: IntentSnapshotAuthority
    case_id: str
    case_sha256: str
    intent_sha256: str
    required: tuple[str, ...]
    values: Mapping[str, object]
    sources: Mapping[str, object]


_COMPLETENESS_AUTHORITY_STATES: dict[int, _CompletenessAuthorityBinding] = {}


def _condition_source_mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, (list, tuple)):
        result: dict[str, object] = {}
        for item in value:
            if not isinstance(item, Mapping):
                continue
            condition = next(
                (
                    item[key]
                    for key in ("condition", "name", "field")
                    if isinstance(item.get(key), str) and item[key].strip()
                ),
                None,
            )
            if isinstance(condition, str):
                result[condition] = item
        return result
    return {}


def _diagnostic_json(value: object) -> object:
    """Project typed diagnostic values without granting them authority."""

    if isinstance(value, Mapping):
        return {key: _diagnostic_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_diagnostic_json(item) for item in value]
    projector = getattr(value, "to_dict", None)
    if callable(projector):
        projected = projector()
        if projected is not value:
            return _diagnostic_json(projected)
    return thaw_json(value)


class CompletenessAuthority:
    """Opaque authority bound to one live EvidenceStore intent snapshot.

    The public model records remain useful diagnostic projections, but their
    caller-controlled provenance flags cannot authorize a physical condition.
    This capability is issued only by :func:`issue_completeness_authority`
    after an exact ``IntentSnapshotAuthority`` has been revalidated.
    """

    __slots__ = ()

    def __new__(cls, *args: object, **kwargs: object) -> CompletenessAuthority:
        del args, kwargs
        raise TypeError("completeness authorities are issued by EvidenceStore snapshots")

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise TypeError("completeness authorities are issued by EvidenceStore snapshots")

    def __init_subclass__(cls, **kwargs: object) -> None:
        del kwargs
        raise TypeError("completeness authorities cannot be subclassed")

    @classmethod
    def _issue(
        cls,
        snapshot: IntentSnapshotAuthority,
        required: tuple[str, ...],
    ) -> CompletenessAuthority:
        if cls is not CompletenessAuthority:
            raise TypeError("completeness authorities cannot be subclassed")
        if type(snapshot) is not IntentSnapshotAuthority:
            raise TypeError("completeness authority requires an IntentSnapshotAuthority")
        if not all(type(name) is str and bool(name.strip()) for name in required):
            raise TypeError("completeness authority names must be non-empty strings")
        if len(set(required)) != len(required):
            raise ValueError("completeness authority names must be unique")

        try:
            intent = snapshot.intent
            case_id = snapshot.case_id
            case_sha256 = snapshot.case_sha256
            intent_sha256 = snapshot.intent_sha256
            payload = intent.to_dict()
        except EvidenceIntegrityError:
            raise
        except Exception as error:
            raise EvidenceIntegrityError(
                "cannot issue authority from an invalid snapshot"
            ) from error
        if not isinstance(payload, Mapping):
            raise EvidenceIntegrityError("intent snapshot projection is invalid")
        source_mapping = _condition_source_mapping(payload.get("condition_sources"))
        values = freeze_value({name: payload.get(name) for name in required})
        sources = freeze_value({name: source_mapping.get(name) for name in required})
        if not isinstance(values, Mapping) or not isinstance(sources, Mapping):
            raise EvidenceIntegrityError("intent snapshot authority values are invalid")

        authority = object.__new__(cls)
        _COMPLETENESS_AUTHORITY_STATES[id(authority)] = _CompletenessAuthorityBinding(
            authority=authority,
            snapshot=snapshot,
            case_id=case_id,
            case_sha256=case_sha256,
            intent_sha256=intent_sha256,
            required=required,
            values=values,
            sources=sources,
        )
        return authority

    def __repr__(self) -> str:
        return "CompletenessAuthority(<opaque>)"

    __str__ = __repr__

    def __setattr__(self, name: str, value: object) -> None:
        del name, value
        raise AttributeError("completeness authorities are immutable")

    def __delattr__(self, name: str) -> None:
        del name
        raise AttributeError("completeness authorities are immutable")

    def __copy__(self) -> CompletenessAuthority:
        raise TypeError("completeness authorities cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> CompletenessAuthority:
        del memo
        raise TypeError("completeness authorities cannot be copied")

    def __reduce__(self) -> NoReturn:
        raise TypeError("completeness authorities cannot be pickled")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        del protocol
        raise TypeError("completeness authorities cannot be pickled")

    def __getstate__(self) -> NoReturn:
        raise TypeError("completeness authorities cannot be serialized")

    def _validated_binding(self) -> _CompletenessAuthorityBinding:
        binding = _COMPLETENESS_AUTHORITY_STATES.get(id(self))
        if (
            type(self) is not CompletenessAuthority
            or binding is None
            or binding.authority is not self
        ):
            raise EvidenceIntegrityError("completeness authority is invalid")
        snapshot = binding.snapshot
        if type(snapshot) is not IntentSnapshotAuthority:
            raise EvidenceIntegrityError("completeness snapshot authority is invalid")
        try:
            current_case_id = snapshot.case_id
            current_case_sha256 = snapshot.case_sha256
            current_intent_sha256 = snapshot.intent_sha256
            payload = snapshot.intent.to_dict()
        except EvidenceIntegrityError:
            raise
        except Exception as error:
            raise EvidenceIntegrityError("completeness snapshot evidence is invalid") from error
        if not isinstance(payload, Mapping):
            raise EvidenceIntegrityError("completeness snapshot projection is invalid")
        source_mapping = _condition_source_mapping(payload.get("condition_sources"))
        current_values = freeze_value({name: payload.get(name) for name in binding.required})
        current_sources = freeze_value(
            {name: source_mapping.get(name) for name in binding.required}
        )
        if (
            current_case_id != binding.case_id
            or current_case_sha256 != binding.case_sha256
            or current_intent_sha256 != binding.intent_sha256
            or current_values != binding.values
            or current_sources != binding.sources
        ):
            raise EvidenceIntegrityError("completeness authority snapshot is stale")
        return binding

    def _validated_for(self, snapshot: IntentSnapshotAuthority) -> _CompletenessAuthorityBinding:
        binding = self._validated_binding()
        if type(snapshot) is not IntentSnapshotAuthority or snapshot is not binding.snapshot:
            raise EvidenceIntegrityError("completeness authority is bound to another snapshot")
        return binding

    @property
    def snapshot(self) -> IntentSnapshotAuthority:
        return self._validated_binding().snapshot

    @property
    def case_id(self) -> str:
        return self._validated_binding().case_id

    @property
    def case_sha256(self) -> str:
        return self._validated_binding().case_sha256

    @property
    def case_digest(self) -> str:
        return self.case_sha256

    @property
    def intent_sha256(self) -> str:
        return self._validated_binding().intent_sha256

    @property
    def intent_digest(self) -> str:
        return self.intent_sha256

    @property
    def required(self) -> tuple[str, ...]:
        return self._validated_binding().required

    @property
    def required_conditions(self) -> tuple[str, ...]:
        return self.required

    @property
    def required_names(self) -> tuple[str, ...]:
        return self.required

    @property
    def values(self) -> dict[str, object]:
        return cast(dict[str, object], thaw_json(self._validated_binding().values))

    @property
    def condition_values(self) -> dict[str, object]:
        return self.values

    @property
    def sources(self) -> dict[str, object]:
        return cast(dict[str, object], thaw_json(self._validated_binding().sources))

    @property
    def condition_sources(self) -> dict[str, object]:
        return self.sources


@dataclass(frozen=True, slots=True)
class EvidenceProvenance:
    """Where an explicit fact came from.

    ``authoritative`` is retained as a diagnostic source claim.  It is never
    sufficient for a model decision and is never inferred from a filename,
    geometry, or a naming convention.
    """

    source: str
    location: str = ""
    excerpt: str | None = None
    authoritative: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("evidence source must be a non-empty string")
        if not isinstance(self.location, str):
            raise TypeError("evidence location must be a string")
        if self.excerpt is not None and not isinstance(self.excerpt, str):
            raise TypeError("evidence excerpt must be a string or None")

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "location": self.location,
            "excerpt": self.excerpt,
            "authoritative": self.authoritative,
        }


def normalise_provenance(
    value: EvidenceProvenance | Iterable[EvidenceProvenance] | None,
) -> tuple[EvidenceProvenance, ...]:
    """Normalize one provenance record or a sequence to an immutable tuple."""

    if value is None:
        return ()
    if isinstance(value, EvidenceProvenance):
        return (value,)
    result = tuple(value)
    if not all(isinstance(item, EvidenceProvenance) for item in result):
        raise TypeError("provenance must contain EvidenceProvenance records")
    return result


@dataclass(frozen=True, slots=True)
class ConditionEvidence:
    """An explicit condition value and the provenance supporting it."""

    condition: str
    value: object | None
    provenance: tuple[EvidenceProvenance, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.condition, PhysicalConditionName):
            object.__setattr__(self, "condition", self.condition.value)
        if not isinstance(self.condition, str) or not self.condition.strip():
            raise ValueError("condition must be a non-empty string")
        provenance = normalise_provenance(self.provenance)
        object.__setattr__(self, "provenance", provenance)
        object.__setattr__(self, "value", freeze_value(self.value))

    @property
    def authoritative(self) -> bool:
        """Whether the diagnostic projection marks support authoritative."""

        return any(item.authoritative for item in self.provenance)

    def to_dict(self) -> dict[str, object]:
        return {
            "condition": self.condition,
            "value": _diagnostic_json(self.value),
            "provenance": [item.to_dict() for item in self.provenance],
            "authoritative": self.authoritative,
        }


@dataclass(frozen=True, slots=True)
class UnresolvedEvidenceField:
    """A required named field that cannot be resolved from authority."""

    field_name: str
    reason: str
    value: object | None = None
    evidence: tuple[EvidenceProvenance, ...] = ()
    required: bool = True

    def __post_init__(self) -> None:
        if isinstance(self.field_name, PhysicalConditionName):
            object.__setattr__(self, "field_name", self.field_name.value)
        if not isinstance(self.field_name, str) or not self.field_name.strip():
            raise ValueError("field_name must be a non-empty string")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("reason must be a non-empty string")
        evidence = normalise_provenance(self.evidence)
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(self, "value", freeze_value(self.value))

    @property
    def condition(self) -> str:
        """Alias used by completeness and preflight consumers."""

        return self.field_name

    def to_dict(self) -> dict[str, object]:
        return {
            "field_name": self.field_name,
            "reason": self.reason,
            "value": _diagnostic_json(self.value),
            "evidence": [item.to_dict() for item in self.evidence],
            "required": self.required,
        }


@dataclass(frozen=True, slots=True)
class MissingConditionFact:
    """Authoritative fact that a required condition is missing.

    The action is deliberately fixed to ``ASK_AND_BLOCK``.  An authoritative
    instance is emitted only while evaluating a live completeness authority;
    direct records remain diagnostic and are never used to authorize a model.
    """

    condition: str
    reason: str
    evidence: tuple[EvidenceProvenance, ...] = ()
    action: str = ASK_AND_BLOCK
    authoritative: bool = True

    def __post_init__(self) -> None:
        if isinstance(self.condition, PhysicalConditionName):
            object.__setattr__(self, "condition", self.condition.value)
        if not isinstance(self.condition, str) or not self.condition.strip():
            raise ValueError("condition must be a non-empty string")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("reason must be a non-empty string")
        if self.action != ASK_AND_BLOCK:
            raise ValueError("missing conditions must use ASK_AND_BLOCK")
        evidence = normalise_provenance(self.evidence)
        object.__setattr__(self, "evidence", evidence)

    @property
    def field_name(self) -> str:
        """Alias matching :class:`UnresolvedEvidenceField`."""

        return self.condition

    def to_dict(self) -> dict[str, object]:
        return {
            "condition": self.condition,
            "reason": self.reason,
            "evidence": [item.to_dict() for item in self.evidence],
            "action": self.action,
            "authoritative": self.authoritative,
        }


def normalise_evidence(value: Any, condition: str) -> ConditionEvidence:
    """Coerce common evidence-shaped inputs without assigning authority."""

    if isinstance(value, ConditionEvidence):
        if value.condition == condition:
            return value
        return ConditionEvidence(condition, value.value, value.provenance)
    if isinstance(value, EvidenceProvenance):
        return ConditionEvidence(condition, None, (value,))
    return ConditionEvidence(condition, value, ())


ConditionFact = ConditionEvidence
PhysicalCondition = ConditionEvidence
UnresolvedCondition = UnresolvedEvidenceField
MissingCondition = MissingConditionFact
PhysicalConditionAuthority = CompletenessAuthority
ConditionAuthority = CompletenessAuthority


__all__ = [
    "ASK_AND_BLOCK",
    "ConditionEvidence",
    "ConditionFact",
    "ConditionName",
    "CompletenessAuthority",
    "ConditionAuthority",
    "EvidenceProvenance",
    "IntentImpact",
    "MissingConditionFact",
    "MissingCondition",
    "PhysicalCondition",
    "PhysicalConditionAuthority",
    "PhysicalConditionName",
    "UnresolvedCondition",
    "UnresolvedEvidenceField",
    "normalise_evidence",
    "normalise_provenance",
]
