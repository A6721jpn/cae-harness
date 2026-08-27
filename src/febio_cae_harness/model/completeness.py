"""Completeness checks that stop when physical evidence is insufficient."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, cast

from ..evidence import EvidenceIntegrityError, IntentSnapshotAuthority
from .types import (
    ASK_AND_BLOCK,
    CompletenessAuthority,
    ConditionEvidence,
    EvidenceProvenance,
    MissingConditionFact,
    PhysicalConditionAuthority,
    PhysicalConditionName,
    UnresolvedEvidenceField,
    normalise_evidence,
)

DEFAULT_REQUIRED_CONDITIONS: tuple[str, ...] = (
    PhysicalConditionName.ENGINEERING_QUESTION.value,
    PhysicalConditionName.UNITS.value,
    PhysicalConditionName.MATERIAL.value,
    PhysicalConditionName.LOADS.value,
    PhysicalConditionName.CONSTRAINTS.value,
    PhysicalConditionName.CONTACT.value,
    PhysicalConditionName.ANALYSIS_STEP.value,
    PhysicalConditionName.ROI.value,
    PhysicalConditionName.EVALUATION_QUANTITIES.value,
)


def _condition_name(value: str | PhysicalConditionName) -> str:
    if isinstance(value, PhysicalConditionName):
        return value.value
    if not isinstance(value, str) or not value.strip():
        raise ValueError("condition names must be non-empty strings")
    return value


def _provenance(value: Any) -> tuple[EvidenceProvenance, ...]:
    if value is None:
        return ()
    if isinstance(value, EvidenceProvenance):
        return (value,)
    if isinstance(value, str):
        return (EvidenceProvenance(source=value),) if value.strip() else ()
    if isinstance(value, Mapping):
        value = (value,)
    if isinstance(value, (list, tuple)):
        result: list[EvidenceProvenance] = []
        for item in value:
            if isinstance(item, EvidenceProvenance):
                result.append(item)
            elif isinstance(item, Mapping):
                source = item.get("source")
                if not isinstance(source, str) or not source.strip():
                    continue
                location = item.get("location", "")
                excerpt = item.get("excerpt")
                result.append(
                    EvidenceProvenance(
                        source=source,
                        location=location if isinstance(location, str) else "",
                        excerpt=excerpt if isinstance(excerpt, str) else None,
                        authoritative=bool(
                            item.get("authoritative", item.get("is_authoritative", False))
                        ),
                    )
                )
            else:
                raise TypeError("provenance must contain EvidenceProvenance records")
        return tuple(result)
    raise TypeError("provenance must be a record or sequence of records")


def _coerce_condition_evidence(name: str, value: Any) -> ConditionEvidence:
    if isinstance(value, ConditionEvidence):
        return normalise_evidence(value, name)
    if isinstance(value, Mapping) and ("value" in value or "provenance" in value):
        raw_provenance = value.get("provenance")
        if raw_provenance is None and "source" in value:
            raw_provenance = {
                "source": value.get("source"),
                "location": value.get("location", ""),
                "excerpt": value.get("excerpt"),
                "authoritative": value.get("authoritative", value.get("is_authoritative", False)),
            }
        return ConditionEvidence(
            name,
            value.get("value"),
            _provenance(raw_provenance),
        )
    return normalise_evidence(value, name)


@dataclass(frozen=True, slots=True)
class CompletenessResult:
    """Evidence-backed completeness result.

    ``complete`` means all requested names have explicit values and live,
    snapshot-bound authority.  Caller evidence remains diagnostic.  It says
    nothing about solver or FEBio success.
    """

    required: tuple[str, ...]
    resolved: tuple[str, ...]
    missing: tuple[MissingConditionFact, ...]
    unresolved: tuple[UnresolvedEvidenceField, ...]
    evidence: tuple[ConditionEvidence, ...] = ()
    state: str = ASK_AND_BLOCK

    def __post_init__(self) -> None:
        required = tuple(self.required)
        resolved = tuple(self.resolved)
        missing = tuple(self.missing)
        unresolved = tuple(self.unresolved)
        evidence = tuple(self.evidence)
        if not all(isinstance(item, str) and item for item in required + resolved):
            raise TypeError("required and resolved condition names must be strings")
        if not all(isinstance(item, MissingConditionFact) for item in missing):
            raise TypeError("missing must contain MissingConditionFact records")
        if not all(isinstance(item, UnresolvedEvidenceField) for item in unresolved):
            raise TypeError("unresolved must contain UnresolvedEvidenceField records")
        if not all(isinstance(item, ConditionEvidence) for item in evidence):
            raise TypeError("evidence must contain ConditionEvidence records")
        if self.state not in {"GATHERING", "BOUND", ASK_AND_BLOCK}:
            raise ValueError("state must be GATHERING, BOUND, or ASK_AND_BLOCK")
        object.__setattr__(self, "required", required)
        object.__setattr__(self, "resolved", resolved)
        object.__setattr__(self, "missing", missing)
        object.__setattr__(self, "unresolved", unresolved)
        object.__setattr__(self, "evidence", evidence)

    @property
    def complete(self) -> bool:
        return self.state == "BOUND" and not self.missing and not self.unresolved

    @property
    def is_complete(self) -> bool:
        return self.complete

    @property
    def ask_and_block(self) -> bool:
        return self.state == ASK_AND_BLOCK

    @property
    def missing_conditions(self) -> tuple[MissingConditionFact, ...]:
        return self.missing

    @property
    def missing_condition_facts(self) -> tuple[MissingConditionFact, ...]:
        return self.missing

    @property
    def unresolved_fields(self) -> tuple[UnresolvedEvidenceField, ...]:
        return self.unresolved

    @property
    def evidence_by_condition(self) -> Mapping[str, ConditionEvidence]:
        return MappingProxyType({item.condition: item for item in self.evidence})

    def to_dict(self) -> dict[str, object]:
        return {
            "required": list(self.required),
            "resolved": list(self.resolved),
            "missing": [item.to_dict() for item in self.missing],
            "unresolved": [item.to_dict() for item in self.unresolved],
            "evidence": [item.to_dict() for item in self.evidence],
            "complete": self.complete,
            "state": self.state,
        }


def _required_names(
    required_conditions: Iterable[str | PhysicalConditionName] | None,
) -> tuple[str, ...]:
    required = (
        DEFAULT_REQUIRED_CONDITIONS
        if required_conditions is None
        else tuple(_condition_name(item) for item in required_conditions)
    )
    if len(set(required)) != len(required):
        raise ValueError("required condition names must be unique")
    return required


def _value_is_present(value: object) -> bool:
    """Return whether an exact snapshot value is non-empty and explicit."""

    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        if not value:
            return False
        if "value" in value:
            return _value_is_present(value["value"])
        return True
    if isinstance(value, (list, tuple)):
        return bool(value) and any(_value_is_present(item) for item in value)
    return True


def _source_is_current(value: object) -> bool:
    """Reject source records explicitly marked stale by the live intent."""

    if isinstance(value, Mapping):
        if value.get("stale") is True:
            return False
        if value.get("current") is False or value.get("fresh") is False:
            return False
        status = value.get("status")
        if isinstance(status, str) and status.casefold() in {
            "stale",
            "expired",
            "superseded",
        }:
            return False
        return all(
            _source_is_current(value[key])
            for key in ("source", "provenance", "evidence")
            if key in value
        )
    if isinstance(value, (list, tuple)):
        return all(_source_is_current(item) for item in value)
    return True


def _authority_provenance(value: object) -> tuple[EvidenceProvenance, ...]:
    """Project a bound source into diagnostic provenance records.

    The ``authoritative`` bit on these records is set by this function because
    the source has already crossed the exact snapshot authority boundary.  A
    caller-controlled record never reaches this helper on its own.
    """

    if isinstance(value, str):
        if value.strip():
            return (EvidenceProvenance(source=value, authoritative=True),)
        return ()
    if isinstance(value, Mapping):
        source = value.get("source")
        if isinstance(source, str) and source.strip():
            location = value.get("location", "")
            excerpt = value.get("excerpt")
            return (
                EvidenceProvenance(
                    source=source,
                    location=location if isinstance(location, str) else "",
                    excerpt=excerpt if isinstance(excerpt, str) else None,
                    authoritative=True,
                ),
            )
        nested: list[EvidenceProvenance] = []
        for key in ("provenance", "evidence"):
            if key in value:
                nested.extend(_authority_provenance(value[key]))
        return tuple(nested)
    if isinstance(value, (list, tuple)):
        result: list[EvidenceProvenance] = []
        for item in value:
            result.extend(_authority_provenance(item))
        return tuple(result)
    return ()


def _diagnostic_completeness(
    required: tuple[str, ...],
    evidence: Mapping[str, Any] | None,
) -> CompletenessResult:
    """Build a non-authoritative projection from caller-supplied evidence."""

    supplied = evidence or {}
    records: list[ConditionEvidence] = []
    unresolved: list[UnresolvedEvidenceField] = []
    for name in required:
        record = _coerce_condition_evidence(name, supplied[name]) if name in supplied else None
        if record is not None:
            records.append(record)
            value = record.value
            support = record.provenance
            reason = "The supplied condition evidence is diagnostic only"
        else:
            value = None
            support = ()
            reason = "No snapshot-bound authority was supplied for this required condition"
        unresolved.append(
            UnresolvedEvidenceField(
                field_name=name,
                reason=reason,
                value=value,
                evidence=support,
                required=False,
            )
        )
    return CompletenessResult(
        required=required,
        resolved=(),
        missing=(),
        unresolved=tuple(unresolved),
        evidence=tuple(records),
        state="GATHERING",
    )


def _authority_completeness(authority: CompletenessAuthority) -> CompletenessResult:
    """Assess values and sources captured by one live authority."""

    required = authority.required
    values = authority.values
    sources = authority.sources
    records: list[ConditionEvidence] = []
    resolved: list[str] = []
    missing: list[MissingConditionFact] = []
    unresolved: list[UnresolvedEvidenceField] = []
    for name in required:
        value = values[name]
        source = sources[name]
        support = _authority_provenance(source)
        record = ConditionEvidence(name, value, support)
        records.append(record)
        if (
            _value_is_present(value)
            and source is not None
            and support
            and _source_is_current(source)
        ):
            resolved.append(name)
            continue

        if not _value_is_present(value):
            reason = "The live intent snapshot does not provide a non-empty condition value"
        elif source is None or not support:
            reason = "The live intent snapshot does not provide a condition source"
        else:
            reason = "The live intent snapshot condition source is stale"
        unresolved.append(
            UnresolvedEvidenceField(
                field_name=name,
                reason=reason,
                value=value if _value_is_present(value) else None,
                evidence=support,
            )
        )
        fact_basis = EvidenceProvenance(
            source="completeness-authority",
            location=f"{authority.case_id}:{name}",
            excerpt="required condition unresolved in live intent snapshot",
            authoritative=True,
        )
        missing.append(
            MissingConditionFact(
                condition=name,
                reason=reason,
                evidence=(fact_basis,),
            )
        )

    state = "BOUND" if not missing else ASK_AND_BLOCK
    return CompletenessResult(
        required=required,
        resolved=tuple(resolved),
        missing=tuple(missing),
        unresolved=tuple(unresolved),
        evidence=tuple(records),
        state=state,
    )


def _exact_value_equal(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return len(left) == len(right) and all(
            key in right and _exact_value_equal(value, right[key]) for key, value in left.items()
        )
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return len(left) == len(right) and all(
            _exact_value_equal(item_left, item_right)
            for item_left, item_right in zip(left, right, strict=True)
        )
    return left == right


def issue_completeness_authority(
    snapshot: IntentSnapshotAuthority,
    required_conditions: Iterable[str | PhysicalConditionName] | None = None,
    values: Mapping[str, Any] | None = None,
    sources: Mapping[str, Any] | None = None,
    *,
    required_names: Iterable[str | PhysicalConditionName] | None = None,
) -> CompletenessAuthority:
    """Issue a completeness capability from one exact live intent snapshot."""

    if type(snapshot) is not IntentSnapshotAuthority:
        raise TypeError("snapshot must be an IntentSnapshotAuthority")
    if required_conditions is not None and required_names is not None:
        raise TypeError("required condition names were supplied twice")
    names = _required_names(
        required_conditions if required_conditions is not None else required_names
    )
    authority = CompletenessAuthority._issue(snapshot, names)
    if values is not None and not _exact_value_equal(authority.values, values):
        raise EvidenceIntegrityError("authority values do not match the live intent snapshot")
    if sources is not None and not _exact_value_equal(authority.sources, sources):
        raise EvidenceIntegrityError("authority sources do not match the live intent snapshot")
    return authority


issue_physical_condition_authority = issue_completeness_authority
issue_condition_authority = issue_completeness_authority


def assess_completeness(
    required_conditions: (
        Iterable[str | PhysicalConditionName]
        | Mapping[str, Any]
        | CompletenessAuthority
        | IntentSnapshotAuthority
        | None
    ) = None,
    evidence: Mapping[str, Any] | None = None,
    *,
    authority: CompletenessAuthority | None = None,
    snapshot: IntentSnapshotAuthority | None = None,
) -> CompletenessResult:
    """Assess physical conditions through a snapshot authority.

    Caller mappings, ``IntentContract`` projections, provenance flags, and
    source labels are retained as diagnostics only.  A ``BOUND`` result or an
    authoritative ``ASK_AND_BLOCK`` fact requires an exact authority issued
    from a live ``IntentSnapshotAuthority``.
    """

    if type(required_conditions) is CompletenessAuthority:
        if authority is not None:
            raise TypeError("completeness authority was supplied twice")
        authority = required_conditions
        required_conditions = None
    elif type(required_conditions) is IntentSnapshotAuthority:
        if authority is not None:
            raise TypeError("completeness authority was supplied twice")
        if evidence is not None:
            raise TypeError("authoritative completeness does not accept caller evidence")
        live_snapshot = required_conditions
        authority = issue_completeness_authority(live_snapshot)
        required_conditions = None

    if snapshot is not None and authority is None:
        if evidence is not None:
            raise TypeError("authoritative completeness does not accept caller evidence")
        if isinstance(required_conditions, Mapping):
            raise TypeError("authoritative completeness does not accept caller mappings")
        else:
            required = _required_names(
                cast(Iterable[str | PhysicalConditionName] | None, required_conditions)
            )
        authority = issue_completeness_authority(snapshot, required)
        required_conditions = None

    if authority is not None:
        if type(authority) is not CompletenessAuthority:
            raise TypeError("authority must be a CompletenessAuthority")
        if evidence is not None:
            raise TypeError("authoritative completeness does not accept caller evidence")
        if required_conditions is not None:
            required = _required_names(
                cast(Iterable[str | PhysicalConditionName], required_conditions)
            )
            if required != authority.required:
                raise ValueError("required condition names do not match the authority")
        if snapshot is not None:
            authority._validated_for(snapshot)
        return _authority_completeness(authority)

    if required_conditions is not None and hasattr(required_conditions, "to_dict"):
        if evidence is not None:
            raise TypeError("raw intent projections do not accept caller evidence")
        return assess_intent_completeness(required_conditions)

    if isinstance(required_conditions, Mapping):
        if evidence is not None:
            raise TypeError("evidence must be omitted when required_conditions is a mapping")
        evidence = required_conditions
        required = _required_names(required_conditions.keys())
    else:
        required = _required_names(
            cast(Iterable[str | PhysicalConditionName] | None, required_conditions)
        )
    return _diagnostic_completeness(required, evidence)


def assess_intent_completeness(
    intent: Mapping[str, Any] | Any,
    required_conditions: Iterable[str | PhysicalConditionName] | None = None,
) -> CompletenessResult:
    """Assess an intent projection while preserving its explicit sources.

    ``IntentContract`` exposes a ``to_dict`` projection.  This adapter keeps
    the model package independent of that common contract while allowing a
    caller to pass either the contract or an equivalent mapping.  A raw
    contract or mapping remains diagnostic; an exact live snapshot is the only
    form that can produce an authoritative result.
    """

    names = _required_names(required_conditions)
    if type(intent) is IntentSnapshotAuthority:
        return _authority_completeness(issue_completeness_authority(intent, names))

    payload: Mapping[str, Any]
    if isinstance(intent, Mapping):
        payload = intent
    elif hasattr(intent, "to_dict"):
        projected = intent.to_dict()
        if not isinstance(projected, Mapping):
            raise TypeError("intent.to_dict() must return a mapping")
        payload = projected
    else:
        raise TypeError("intent must be a mapping or expose to_dict()")
    raw_sources = payload.get("condition_sources", {})
    sources = raw_sources if isinstance(raw_sources, Mapping) else {}
    records: dict[str, ConditionEvidence] = {}
    for name in names:
        if name not in payload:
            continue
        records[name] = ConditionEvidence(
            condition=name,
            value=payload[name],
            provenance=_provenance(sources.get(name)),
        )
    return assess_completeness(names, records)


check_completeness = assess_completeness
evaluate_completeness = assess_completeness
completeness_result = assess_completeness
assess_authoritative_completeness = assess_completeness
Completeness = CompletenessResult
check_intent_completeness = assess_intent_completeness
completeness_from_intent = assess_intent_completeness


__all__ = [
    "Completeness",
    "CompletenessAuthority",
    "CompletenessResult",
    "DEFAULT_REQUIRED_CONDITIONS",
    "PhysicalConditionAuthority",
    "assess_authoritative_completeness",
    "assess_completeness",
    "assess_intent_completeness",
    "check_completeness",
    "check_intent_completeness",
    "completeness_from_intent",
    "evaluate_completeness",
    "completeness_result",
    "issue_completeness_authority",
    "issue_condition_authority",
    "issue_physical_condition_authority",
]
