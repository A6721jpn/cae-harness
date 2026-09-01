"""Durable intent-state reconciliation and authoritative question handling."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace

from .autonomy.policy import (
    IntentStateAuthority,
    PhysicalConditionEvidence,
    transition_intent,
    unresolved_authoritative_conditions,
)
from .contracts import IntentContract, IntentState, JSONInput
from .evidence import EvidenceIntegrityError, EvidenceStore, IntentSnapshotAuthority

__all__ = ["IntentLifecycle", "IntentLifecycleResult", "IntentQuestion"]

_QUESTION_SCHEMA = "intent-question-v1"
_QUESTION_FACTORY = object()
_NON_ANSWER_FIELDS = frozenset({"condition_sources", "unresolved", "state"})
_RECORD_NAME_FIELDS = ("condition", "name", "field")


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise TypeError("intent question values must be JSON-compatible") from error


def _question_digest(
    *,
    case_id: str,
    intent_sha256: str,
    ordinal: int,
    condition: str,
    source: str | None,
    detail: str | None,
) -> str:
    projection = {
        "schema": _QUESTION_SCHEMA,
        "case_id": case_id,
        "intent_sha256": intent_sha256,
        "ordinal": ordinal,
        "condition": condition,
        "source": source,
        "detail": detail,
    }
    return hashlib.sha256(_canonical_bytes(projection)).hexdigest()


@dataclass(frozen=True, slots=True, init=False)
class IntentQuestion:
    """Immutable projection of one current policy-authorized blocker."""

    schema: str
    question_id: str
    case_id: str
    intent_sha256: str
    ordinal: int
    condition: str
    source: str | None
    detail: str | None
    prompt: str

    def __new__(
        cls,
        *args: object,
        _factory: object | None = None,
        **kwargs: object,
    ) -> IntentQuestion:
        del args, kwargs
        if cls is not IntentQuestion:
            raise TypeError("intent questions cannot be subclassed")
        if _factory is not _QUESTION_FACTORY:
            raise TypeError("intent questions are issued by IntentLifecycle")
        return object.__new__(cls)

    def __init__(
        self,
        *args: object,
        _factory: object | None = None,
        **kwargs: object,
    ) -> None:
        del args, kwargs
        if _factory is not _QUESTION_FACTORY:
            raise TypeError("intent questions are issued by IntentLifecycle")

    def __init_subclass__(cls, **kwargs: object) -> None:
        del kwargs
        raise TypeError("intent questions cannot be subclassed")

    @classmethod
    def _issue(
        cls,
        *,
        case_id: str,
        intent_sha256: str,
        ordinal: int,
        blocker: PhysicalConditionEvidence,
    ) -> IntentQuestion:
        if cls is not IntentQuestion:
            raise TypeError("intent questions cannot be subclassed")
        if type(blocker) is not PhysicalConditionEvidence or not blocker.authoritative:
            raise EvidenceIntegrityError("intent question requires an authoritative blocker")
        question = cls(_factory=_QUESTION_FACTORY)
        condition = blocker.condition
        question_id = _question_digest(
            case_id=case_id,
            intent_sha256=intent_sha256,
            ordinal=ordinal,
            condition=condition,
            source=blocker.source,
            detail=blocker.detail,
        )
        object.__setattr__(question, "schema", _QUESTION_SCHEMA)
        object.__setattr__(question, "question_id", question_id)
        object.__setattr__(question, "case_id", case_id)
        object.__setattr__(question, "intent_sha256", intent_sha256)
        object.__setattr__(question, "ordinal", ordinal)
        object.__setattr__(question, "condition", condition)
        object.__setattr__(question, "source", blocker.source)
        object.__setattr__(question, "detail", blocker.detail)
        object.__setattr__(
            question,
            "prompt",
            f"Provide an authoritative value for the required analysis condition: {condition}",
        )
        return question

    def to_dict(self) -> dict[str, object]:
        """Return the deterministic JSON projection of this question."""

        return {
            "schema": self.schema,
            "question_id": self.question_id,
            "case_id": self.case_id,
            "intent_sha256": self.intent_sha256,
            "ordinal": self.ordinal,
            "condition": self.condition,
            "source": self.source,
            "detail": self.detail,
            "prompt": self.prompt,
        }


@dataclass(frozen=True, slots=True)
class IntentLifecycleResult:
    """One reconciled, durable intent lifecycle view."""

    snapshot: IntentSnapshotAuthority
    authority: IntentStateAuthority
    state: IntentState
    question: IntentQuestion | None

    def __post_init__(self) -> None:
        if type(self.snapshot) is not IntentSnapshotAuthority:
            raise TypeError("lifecycle result requires an IntentSnapshotAuthority")
        if type(self.authority) is not IntentStateAuthority:
            raise TypeError("lifecycle result requires an IntentStateAuthority")
        self.authority._validated_for(self.snapshot)
        if self.authority.current is not self.state or self.snapshot.intent.state is not self.state:
            raise EvidenceIntegrityError("lifecycle result state is not durably reconciled")
        if (self.state is IntentState.ASK_AND_BLOCK) != (self.question is not None):
            raise EvidenceIntegrityError("lifecycle result question does not match its state")
        if self.question is not None and (
            self.question.case_id != self.snapshot.case_id
            or self.question.intent_sha256 != self.snapshot.intent_sha256
        ):
            raise EvidenceIntegrityError("lifecycle result question is bound to another snapshot")


def _record_condition(value: object) -> str | None:
    if not isinstance(value, Mapping):
        return None
    for name in _RECORD_NAME_FIELDS:
        candidate = value.get(name)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _matches_condition(value: object, condition: str) -> bool:
    if isinstance(value, str):
        return value.strip() == condition
    candidate = _record_condition(value)
    return candidate is not None and candidate == condition


def _replace_condition_source(
    value: object,
    *,
    condition: str,
    source: str,
    detail: str | None,
) -> object:
    canonical: dict[str, object] = {
        "authoritative": True,
        "condition": condition,
        "current": True,
        "source": source,
    }
    if detail is not None:
        canonical["detail"] = detail

    if isinstance(value, Mapping):
        record_fields = set((*_RECORD_NAME_FIELDS, "authoritative", "resolved", "status"))
        if set(value) & record_fields:
            return canonical if _matches_condition(value, condition) else [dict(value), canonical]
        matching_keys = [
            key
            for key, item in value.items()
            if key == condition
            or (isinstance(item, Mapping) and _matches_condition(item, condition))
        ]
        if not matching_keys:
            mapping_result = dict(value)
            mapping_result[condition] = canonical
            return mapping_result
        first = matching_keys[0]
        replaced_mapping: dict[str, object] = {}
        for key, item in value.items():
            if key == first:
                replaced_mapping[key] = canonical
            elif key not in matching_keys:
                replaced_mapping[key] = item
        return replaced_mapping

    records = (
        list(value) if isinstance(value, (list, tuple)) else ([] if value is None else [value])
    )
    retained: list[object] = []
    insertion: int | None = None
    for record in records:
        if _matches_condition(record, condition):
            if insertion is None:
                insertion = len(retained)
            continue
        retained.append(record)
    if insertion is None:
        retained.append(canonical)
    else:
        retained.insert(insertion, canonical)
    return retained


def _remove_matching_unresolved(value: object, condition: str) -> object:
    if isinstance(value, Mapping):
        if set(value) & set((*_RECORD_NAME_FIELDS, "authoritative", "resolved", "status")):
            return [] if _matches_condition(value, condition) else dict(value)
        return {
            key: item for key, item in value.items() if key != condition
        }
    if isinstance(value, (list, tuple)):
        return [item for item in value if not _matches_condition(item, condition)]
    if isinstance(value, str) and _matches_condition(value, condition):
        return []
    return value


def _value_is_explicit(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        if not value:
            return False
        return _value_is_explicit(value["value"]) if "value" in value else True
    if isinstance(value, (list, tuple)):
        return bool(value) and any(_value_is_explicit(item) for item in value)
    return True


class IntentLifecycle:
    """Durable lifecycle owner bound to exactly one evidence store."""

    __slots__ = ("_store", "_issued_question")

    def __init__(self, store: EvidenceStore) -> None:
        if type(store) is not EvidenceStore:
            raise TypeError("intent lifecycle requires an EvidenceStore")
        self._store = store
        self._issued_question: IntentQuestion | None = None

    def _question(
        self,
        snapshot: IntentSnapshotAuthority,
        authority: IntentStateAuthority,
    ) -> IntentQuestion | None:
        authority._validated_for(snapshot)
        if authority.current is not IntentState.ASK_AND_BLOCK:
            return None
        blockers = unresolved_authoritative_conditions(snapshot)
        if not blockers or blockers != authority.blocking_conditions:
            raise EvidenceIntegrityError("ASK_AND_BLOCK lacks a current authoritative blocker")
        issued = IntentQuestion._issue(
            case_id=snapshot.case_id,
            intent_sha256=snapshot.intent_sha256,
            ordinal=0,
            blocker=blockers[0],
        )
        self._issued_question = issued
        return issued

    def _result(
        self,
        snapshot: IntentSnapshotAuthority,
        authority: IntentStateAuthority,
    ) -> IntentLifecycleResult:
        return IntentLifecycleResult(
            snapshot=snapshot,
            authority=authority,
            state=authority.current,
            question=self._question(snapshot, authority),
        )

    def reconcile(
        self,
        snapshot: IntentSnapshotAuthority | None = None,
    ) -> IntentLifecycleResult:
        """Persist the state derived from one exact current intent snapshot."""

        if snapshot is None:
            current = self._store.issue_intent_snapshot()
        else:
            if type(snapshot) is not IntentSnapshotAuthority:
                raise TypeError("snapshot must be an IntentSnapshotAuthority")
            self._store._validate_intent_snapshot(snapshot)
            current = snapshot
        authority = transition_intent(current)
        persisted_intent = current.intent
        if authority.current is not persisted_intent.state:
            self._store.revise_intent(
                replace(persisted_intent, state=authority.current),
                expected_snapshot=current,
            )
            current = self._store.issue_intent_snapshot()
            authority = transition_intent(current)
            if authority.current is not current.intent.state:
                raise EvidenceIntegrityError("intent state reconciliation did not converge")
        return self._result(current, authority)

    def answer(
        self,
        question_id: IntentQuestion,
        value: JSONInput,
        source: str,
        *,
        detail: str | None = None,
    ) -> IntentLifecycleResult:
        """Record an explicit authoritative answer to the exact current question."""

        snapshot = self._store.issue_intent_snapshot()
        authority = transition_intent(snapshot)
        if authority.current is not snapshot.intent.state:
            raise EvidenceIntegrityError("intent must be reconciled before answering")
        issued_question = self._issued_question
        question = self._question(snapshot, authority)
        self._issued_question = issued_question
        if question is None:
            raise EvidenceIntegrityError("intent has no current authoritative question")
        if type(question_id) is not IntentQuestion or question_id is not issued_question:
            raise EvidenceIntegrityError("question is not the exact issued current question")

        current_payload = snapshot.intent.to_dict()
        field_by_name = {
            name: name for name in current_payload if name not in _NON_ANSWER_FIELDS
        }
        condition_field = field_by_name.get(question.condition)
        if condition_field is None:
            raise EvidenceIntegrityError(
                "current question condition is not an intent contract field"
            )
        if not _value_is_explicit(value):
            raise ValueError("answer value must be non-empty and explicit")
        if not isinstance(source, str) or not source.strip():
            raise ValueError("answer source must be a non-empty authoritative label")
        source_label = source.strip()
        if detail is not None and not isinstance(detail, str):
            raise TypeError("answer detail must be a string or None")
        answer_detail = None if detail is None or not detail.strip() else detail.strip()

        current_payload[condition_field] = value
        current_payload["condition_sources"] = _replace_condition_source(
            current_payload["condition_sources"],
            condition=condition_field,
            source=source_label,
            detail=answer_detail,
        )
        current_payload["unresolved"] = _remove_matching_unresolved(
            current_payload["unresolved"],
            question.condition,
        )
        current_payload["state"] = IntentState.GATHERING.value
        try:
            answered_intent = IntentContract.from_mapping(current_payload)
        except (TypeError, ValueError) as error:
            raise ValueError(
                "answer value must be JSON-compatible with the intent field"
            ) from error
        self._store.revise_intent(answered_intent, expected_snapshot=snapshot)
        return self.reconcile()
