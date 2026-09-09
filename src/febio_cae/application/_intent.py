"""Short leased application publication around a separately bounded proposal call."""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import fields, replace
from typing import Any

from febio_cae.domain.canonical import canonical_bytes
from febio_cae.domain.case_draft import CaseDraft
from febio_cae.domain.case_patch import CasePatch, CasePatchEdit
from febio_cae.domain.evidence import EvidenceRef
from febio_cae.domain.material import IsotropicLinearElastic
from febio_cae.domain.partial_case_spec import PartialCaseSpec
from febio_cae.domain.ports import PortError, PortErrorCategory
from febio_cae.domain.units import Quantity
from febio_cae.storage.llm_operations import LLMOperations
from febio_cae.storage.registry import (
    CaseStorage,
    StorageConflictError,
    StorageIntegrityError,
    nested_evidence,
)

from ._intent_grounding import (
    Fact,
    evidence,
    ground,
    known_projection,
    local_facts,
    material_from_facts,
)
from .intent_contracts import (
    INSTRUCTIONS,
    MATERIAL_FIELDS,
    PHYSICAL_COMPONENTS,
    PROPOSAL_SCHEMA,
    digest,
    effective_settings,
    settings_from_dict,
)
from .service import (
    ConcurrentUpdateError,
    RegisteredCaseService,
    ServiceConflictError,
    ServiceResult,
)


def _context(
    service: RegisteredCaseService,
    storage: CaseStorage,
    case_id: str,
    generation: int,
    question_id: str | None,
    base: str | None,
    expected: str | None = None,
) -> CaseDraft:
    draft = storage.current_draft(case_id)
    if draft.generation != generation or (
        expected is not None and digest(draft.to_dict()) != expected
    ):
        raise ConcurrentUpdateError("intent context no longer matches the current draft")
    service._resolve_declarations(storage, (), (*draft.evidence, *nested_evidence(draft.values)))
    if question_id is not None:
        question = storage.get_question(question_id)
        if (question.case_id, question.generation, question.draft_id) != (
            case_id,
            generation,
            draft.draft_id,
        ):
            raise ConcurrentUpdateError("answer question is stale")
        service._resolve_declarations(storage, (), question.question_time_evidence)
    if base is not None:
        parent = storage.get_revision(case_id, base)
        if (
            storage.current_frozen_revision(case_id) != base
            or storage.revision_generation(parent.revision_id) != draft.generation
        ):
            raise ConcurrentUpdateError(
                "edit requires the exact current frozen parent and unchanged draft"
            )
        service._resolve_declarations(
            storage, (), (*parent.evidence, *nested_evidence(parent.spec))
        )
    return draft


def _sources(
    storage: CaseStorage, question_id: str | None, text: str
) -> tuple[list[dict[str, str]], set[str]]:
    retained: dict[str, str] = {}
    if question_id is not None:
        for item in storage.get_question(question_id).question_time_evidence:
            if item.source_kind == "user_instruction" and item.target_field == "intent":
                retained[item.reference] = storage.resolve_source(
                    storage.source_asset(item.reference)
                ).content.decode("utf-8")
    if (
        len(retained) > 15
        or sum(len(value.encode("utf-8")) for value in retained.values())
        + len(text.encode("utf-8"))
        > 64 * 1024
    ):
        raise ValueError("retained explicit facts exceed the bounded input; use explicit case spec")
    source_id = "intent-" + hashlib.sha256(text.encode("utf-8")).hexdigest()
    sources = [{"id": key, "text": value} for key, value in retained.items()]
    if source_id not in retained:
        sources.append({"id": source_id, "text": text})
    return sources, set(retained)


def _adoptions(
    service: RegisteredCaseService,
    storage: CaseStorage,
    case_id: str,
    draft: CaseDraft,
    facts: dict[str, Fact],
) -> dict[str, Any]:
    adopted: dict[str, Any] = {}
    parents = []
    for name, fact in facts.items():
        if name not in PHYSICAL_COMPONENTS or not fact.value.startswith("adopt "):
            continue
        revision_id, component = fact.value[6:].rsplit(".", 1)
        if component != name:
            raise ValueError("component adoption target differs")
        parent = storage.get_revision(case_id, revision_id)
        service._resolve_declarations(
            storage, (), (*parent.evidence, *nested_evidence(parent.spec))
        )
        if draft.values.geometry is not None and parent.spec.geometry != draft.values.geometry:
            raise ServiceConflictError("registered component belongs to another geometry context")
        adopted[name] = getattr(parent.spec, name)
        parents.append(parent)
    # Existing domain cross-reference checks assess adoption against all explicit
    # current components. Missing components are not copied into the draft.
    for parent in parents:
        present = {
            field.name: getattr(draft.values, field.name)
            for field in fields(draft.values)
            if field.init and getattr(draft.values, field.name) is not None
        }
        replace(parent.spec, **{**present, **adopted})
    return adopted


def _operation(entry: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "operation_id",
        "state",
        "reserved_calls",
        "attempted_requests",
        "reserved_tokens",
        "local_count_allowance",
        "counted_input_tokens",
        "usage",
        "actual_model",
        "bootstrap_allocation",
    )
    return {key: entry[key] for key in keys if key in entry}


def _reentry(entry: dict[str, Any]) -> dict[str, Any]:
    if "result" in entry:
        return entry["result"]
    return {
        "schema_version": "1",
        "status": "UNCERTAIN",
        "case_id": entry["case_id"],
        "revision_id": None,
        "run_id": None,
        "diagnostics": [
            {
                "schema_version": "1",
                "code": "invalid_input",
                "message": "operation already claimed; send/application outcome is uncertain and will not be repeated",
                "field": "operation_id",
                "retryable": False,
            }
        ],
        "next_actions": ["inspect current registered state"],
        "operation": _operation(entry),
    }


def _failure(case_id: str, error: Exception) -> dict[str, Any]:
    if isinstance(error, (ConcurrentUpdateError, ServiceConflictError, StorageConflictError)):
        status, code, message = "CONFLICT", "conflict", str(error)
    elif isinstance(error, StorageIntegrityError):
        status, code, message = (
            "INVALID_INPUT",
            "integrity",
            "registered intent state failed integrity checks",
        )
    elif isinstance(error, PortError):
        code = error.category.value
        status = (
            "UNSUPPORTED_ENVIRONMENT"
            if code in {"environment", "unsupported_capability"}
            else "INVALID_INPUT"
        )
        if code == "conflict":
            status = "CONFLICT"
        message = str(error)
    elif isinstance(error, OSError):
        status, code = "UNSUPPORTED_ENVIRONMENT", "environment"
        message = "bounded LLM transport outcome is uncertain"
    else:
        status, code, message = "INVALID_INPUT", "invalid_input", str(error)
    return {
        "schema_version": "1",
        "status": status,
        "case_id": case_id,
        "revision_id": None,
        "run_id": None,
        "diagnostics": [
            {
                "schema_version": "1",
                "code": code,
                "message": message,
                "field": "intent",
                "retryable": False,
            }
        ],
        "next_actions": [],
    }


def execute(
    service: RegisteredCaseService,
    case_id: str,
    *,
    action: str,
    text: str,
    expected_generation: int,
    operation_id: str,
    settings: dict[str, Any],
    question_id: str | None = None,
    base: str | None = None,
) -> dict[str, Any]:
    if (
        action not in {"intent", "answer", "edit"}
        or (action == "answer") != (question_id is not None)
        or (action == "edit") != (base is not None)
    ):
        raise ValueError("intent/answer/edit context arguments do not match")
    if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > 64 * 1024:
        raise ValueError("text must be nonempty and at most 64 KiB")
    if (
        type(expected_generation) is not int
        or expected_generation < 0
        or not isinstance(operation_id, str)
        or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", operation_id) is None
    ):
        raise ValueError("explicit generation and bounded operation ID are required")
    started = time.monotonic()
    settings = settings_from_dict(settings)
    identity = digest(
        {
            "case_id": case_id,
            "action": action,
            "text": text,
            "generation": expected_generation,
            "question": question_id,
            "base": base,
            "settings": settings,
        }
    )
    storage = service._storage(case_id)
    journal = LLMOperations(storage)
    existing = journal.get(operation_id)
    if existing is not None:
        if existing["identity"] != identity:
            raise ConcurrentUpdateError("operation ID already binds another payload")
        return _reentry(existing)
    with storage.transaction():
        # Close the lookup/claim race before inspecting a possibly advanced draft.
        existing = journal.get(operation_id)
        if existing is not None:
            if existing["identity"] != identity:
                raise ConcurrentUpdateError("operation ID already binds another payload")
            return _reentry(existing)
        draft = _context(service, storage, case_id, expected_generation, question_id, base)
        context_digest = digest(draft.to_dict())
        effective = effective_settings(settings, draft.values.budget)
        deadline = started + effective["budget"]["max_elapsed"]["value"]
        sources, retained_ids = _sources(storage, question_id, text)
        candidates, _ = local_facts(sources)
        _adoptions(service, storage, case_id, draft, candidates)
        if action == "edit":
            if not isinstance(draft.values.material, IsotropicLinearElastic):
                raise PortError(
                    PortErrorCategory.UNSUPPORTED_CAPABILITY,
                    "natural-language edit supports isotropic E only",
                )
            if set(candidates) != {"material.youngs_modulus"} or len(text.splitlines()) != 1:
                raise ValueError("only one affirmative isotropic E replacement is supported")
        geometry = draft.values.geometry
        if geometry is None:
            raise PortError(
                PortErrorCategory.ENVIRONMENT,
                "register explicit geometry context with case spec before intent",
            )
        if draft.values.solver_policy is None or draft.values.mesh_policy is None:
            raise PortError(
                PortErrorCategory.ENVIRONMENT,
                "register numerical profiles with case spec before intent",
            )
        # Profile identities and source evidence are checked before any HTTP spending.
        for component in (
            draft.values.solver_policy,
            draft.values.outputs,
            draft.values.quality_policy,
        ):
            if component is not None:
                profile = service.compatibility.get_profile(component.profile.profile_id)
                if digest(profile.to_dict()) != component.profile.record_digest:
                    raise ServiceConflictError("numerical profile digest is stale")
        storage.resolve_mesh_quality(draft.values.mesh_policy.quality_profile)
        entity = geometry.body_id.value
        common = {
            "model": settings["model"],
            "instructions": INSTRUCTIONS,
            "input": canonical_bytes(
                {"case_id": case_id, "entity": entity, "scope": "case", "sources": sources}
            ).decode("utf-8"),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "intent_proposal",
                    "strict": True,
                    "schema": PROPOSAL_SCHEMA,
                }
            },
        }
        claimed, entry = journal.start(
            operation_id,
            identity,
            {
                "case_id": case_id,
                "context_digest": context_digest,
                "parent_spec_digest": None
                if base is None
                else storage.get_revision(case_id, base).spec_digest,
                "instruction_digest": digest(INSTRUCTIONS),
                "settings_digest": digest(settings),
                "effective_settings": effective,
                "request_digest": digest(common),
                "generation_request_digest": digest(
                    {
                        **common,
                        "store": False,
                        "background": False,
                        "stream": False,
                        "tools": [],
                        "max_output_tokens": effective["output_tokens"],
                    }
                ),
                "schema_digest": digest(PROPOSAL_SCHEMA),
                "reserved_calls": 2,
                "reserved_tokens": 2 * effective["input_tokens"] + effective["output_tokens"],
                "local_count_allowance": effective["input_tokens"],
                "bootstrap_allocation": draft.values.budget is None,
            },
        )
        if not claimed:
            return _reentry(entry)

    def precheck() -> None:
        if time.monotonic() >= deadline:
            raise TimeoutError("intent operation deadline exceeded")
        with storage.transaction():
            current = _context(
                service, storage, case_id, expected_generation, question_id, base, context_digest
            )
            _adoptions(service, storage, case_id, current, candidates)

    def event(changes: dict[str, Any]) -> None:
        journal.update(operation_id, **changes)

    try:
        from febio_cae.adapters.llm.openai_responses import exchange

        precheck()
        proposal = exchange(
            common, effective, before_generation=precheck, on_event=event, deadline=deadline
        )
        facts = ground(proposal, sources, entity=entity, retained_ids=retained_ids)
        precheck()
        with storage.transaction():
            current = _context(
                service, storage, case_id, expected_generation, question_id, base, context_digest
            )
            adopted = _adoptions(service, storage, case_id, current, facts)
            material = material_from_facts(facts, sources)
            if material is not None:
                if current.values.material is not None and current.values.material != material:
                    raise ValueError(
                        "initial intent cannot replace existing material; use explicit edit"
                    )
                adopted["material"] = material
            if action == "edit":
                fact = facts.get("material.youngs_modulus")
                if fact is None or not isinstance(current.values.material, IsotropicLinearElastic):
                    raise ValueError("explicit E replacement was not independently grounded")
            # Prepare every typed value and question target check before source publication.
            values = PartialCaseSpec(**adopted)
            if question_id is not None:
                from febio_cae.storage.registry import check_answer_values

                check_answer_values(
                    current.values,
                    service._merge_values(current.values, values),
                    tuple(storage.get_question(question_id).target_fields),
                )
            journal.update(operation_id, state="APPLYING")
            refs: list[EvidenceRef] = []
            for source in sources:
                asset = storage.ingest_source(
                    asset_id=source["id"],
                    source_kind="user_instruction",
                    media_type="text/plain",
                    content=source["text"].encode("utf-8"),
                )
                refs.append(
                    EvidenceRef(
                        "1", "user_instruction", asset.asset_id, "intent", asset.content_digest
                    )
                )
            refs.extend(evidence(fact, sources) for fact in facts.values())
            if action == "edit":
                fact = facts["material.youngs_modulus"]
                parent = storage.get_revision(case_id, base or "")
                assert isinstance(current.values.material, IsotropicLinearElastic)
                edited_material = replace(
                    current.values.material,
                    youngs_modulus=Quantity(float(fact.value), fact.unit),
                    youngs_modulus_evidence=evidence(fact, sources),
                )
                patch = CasePatch(
                    parent.revision_id,
                    parent.spec_digest,
                    (CasePatchEdit("material", edited_material, True),),
                    # This reference retains the entire immutable edit source while
                    # preserving PREPARED's fixed non-E evidence authority.
                    (evidence(fact, sources),),
                )
                # All other fields are retained by construction; the existing patch authority publishes.
                updated = service.apply_patch(
                    case_id, patch, expected_generation=expected_generation
                )
            elif question_id is not None:
                all_refs = {
                    canonical_bytes(ref.to_dict()): ref
                    for ref in (*storage.get_question(question_id).question_time_evidence, *refs)
                }
                updated = service.answer_question(
                    case_id,
                    question_id,
                    values=values,
                    expected_generation=expected_generation,
                    evidence=tuple(all_refs.values()),
                )
            else:
                updated = service.set_spec(
                    case_id,
                    values=values,
                    expected_generation=expected_generation,
                    evidence=tuple(refs),
                    input_intent=text,
                )
            missing = [
                name for name in PHYSICAL_COMPONENTS if getattr(updated.values, name) is None
            ]
            result = ServiceResult(
                "NEEDS_INPUT" if missing else "UPDATED", case_id, draft=updated
            ).to_dict()
            result["known_facts"] = known_projection(facts)
            result["missing_fields"] = [
                field
                for name in missing
                for field in (MATERIAL_FIELDS if name == "material" else (name,))
                if field not in facts
            ]
            if missing:
                question = service.issue_question(
                    case_id, target_fields=missing, question_time_evidence=updated.evidence
                )
                result["question"] = question.to_dict()
                result["next_actions"] = [
                    "answer only the listed missing physical fields using whole affirmative labelled clauses or registered component adoption"
                ]
            else:
                result["next_actions"] = [
                    "supply any missing explicit numerical configuration, then case validate and case freeze"
                ]
            entry = journal.update(operation_id, state="APPLIED")
            result["operation"] = _operation(entry)
            journal.update(operation_id, result=result)
            return result
    except (
        OSError,
        TypeError,
        ValueError,
        PortError,
        ServiceConflictError,
        StorageConflictError,
        StorageIntegrityError,
    ) as error:
        latest = journal.get(operation_id)
        # A source/draft/question publication may have partially succeeded: never claim apply.
        state = (
            "UNCERTAIN"
            if latest is not None and latest["state"] in {"APPLYING", "APPLIED"}
            else "REJECTED"
        )
        result = _failure(case_id, error)
        entry = journal.update(operation_id, state=state)
        result["operation"] = _operation(entry)
        journal.update(operation_id, result=result)
        return result
