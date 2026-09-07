from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from febio_cae.application.specs import SourceDeclaration
from febio_cae.domain.evidence import EvidenceRef
from febio_cae.domain.partial_case_spec import PartialCaseSpec


def _service_types():
    from febio_cae.application.service import (
        ConcurrentUpdateError,
        RegisteredCaseService,
        ServiceConflictError,
    )

    return RegisteredCaseService, ConcurrentUpdateError, ServiceConflictError


def _service(tmp_path: Path) -> tuple[Any, str]:
    RegisteredCaseService, _, _ = _service_types()
    cad_path = tmp_path / "source.step"
    cad_path.write_bytes(b"step-content")
    service = RegisteredCaseService(state_dir=tmp_path / "state")
    created = service.create_case(case_root=tmp_path / "case", cad_path=cad_path)
    return service, created.case_id


def test_set_spec_is_generation_cas_and_keeps_unknown_fields_unresolved(tmp_path: Path) -> None:
    _, ConcurrentUpdateError, _ = _service_types()
    service, case_id = _service(tmp_path)
    current = service.current_draft(case_id)

    updated = service.set_spec(
        case_id,
        values=PartialCaseSpec(),
        expected_generation=current.generation,
        input_intent="explicit synthetic declaration",
    )

    assert updated.generation == 1
    assert updated.unresolved_fields == current.unresolved_fields
    with pytest.raises(ConcurrentUpdateError):
        service.set_spec(
            case_id,
            values=PartialCaseSpec(),
            expected_generation=current.generation,
        )


def test_two_service_instances_have_one_winning_generation_cas(tmp_path: Path) -> None:
    RegisteredCaseService, ConcurrentUpdateError, _ = _service_types()
    service, case_id = _service(tmp_path)
    peer = RegisteredCaseService(state_dir=tmp_path / "state")

    def write(instance: Any) -> str:
        try:
            instance.set_spec(
                case_id,
                values=PartialCaseSpec(),
                expected_generation=0,
                input_intent="concurrent",
            )
        except ConcurrentUpdateError:
            return "conflict"
        return "winner"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(write, (service, peer)))

    assert sorted(outcomes) == ["conflict", "winner"]
    assert service.current_draft(case_id).generation == 1


def test_source_declaration_is_ingested_as_bytes_with_independent_digest(tmp_path: Path) -> None:
    service, case_id = _service(tmp_path)
    content = b"user supplied condition"
    draft = service.set_spec(
        case_id,
        values=PartialCaseSpec(),
        expected_generation=0,
        source_declarations=(
            SourceDeclaration(
                "user_instruction",
                "instruction-1",
                "material",
                content=content,
                content_digest=hashlib.sha256(content).hexdigest(),
            ),
        ),
    )

    assert draft.evidence[0].reference == "instruction-1"
    assert draft.evidence[0].content_digest == hashlib.sha256(content).hexdigest()
    assert service.resolve_source(case_id, "instruction-1").content == content


def test_question_can_be_consumed_once_and_is_generation_bound(tmp_path: Path) -> None:
    _, _, ServiceConflictError = _service_types()
    service, case_id = _service(tmp_path)
    draft = service.current_draft(case_id)
    evidence = EvidenceRef(
        "1",
        "registered_document",
        "cad",
        "material",
        hashlib.sha256(b"step-content").hexdigest(),
    )
    question = service.issue_question(
        case_id,
        target_fields=("material",),
        question_time_evidence=(evidence,),
    )

    answered = service.answer_question(
        case_id,
        question.question_id,
        values=PartialCaseSpec(),
        expected_generation=draft.generation,
        evidence=(evidence,),
    )
    assert answered.generation == draft.generation + 1

    with pytest.raises(ServiceConflictError):
        service.answer_question(
            case_id,
            question.question_id,
            values=PartialCaseSpec(),
            expected_generation=answered.generation,
            evidence=(evidence,),
        )


def test_validate_reports_missing_geometry_and_profile_without_ready_claim(tmp_path: Path) -> None:
    service, case_id = _service(tmp_path)
    result = service.validate_case(case_id)

    assert result.status == "UNSUPPORTED_ENVIRONMENT"
    codes = {diagnostic.code.value for diagnostic in result.diagnostics}
    assert "unsupported_capability" in codes
    assert result.revision_id is None


def test_freeze_never_uses_caller_ready_or_approved_flags(tmp_path: Path) -> None:
    _, _, ServiceConflictError = _service_types()
    service, case_id = _service(tmp_path)
    result = service.set_spec(
        case_id,
        values=PartialCaseSpec(),
        expected_generation=0,
    )
    assert result.generation == 1

    with pytest.raises(ServiceConflictError):
        service.freeze_case(case_id, caller_payload={"approved": True, "ready": True})
