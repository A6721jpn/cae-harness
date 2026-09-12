"""AI-02 live-provider acceptance for the public natural-language case flow.

This test uses the repository's synthetic registration fixture.  It does not
replace the Responses adapter or its transport; with approved settings it
exercises the real configured provider through the public CLI intent,
answer, and edit boundaries.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import uuid
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any, NoReturn

import pytest

from febio_cae import __version__
from febio_cae.adapters.llm.openai_responses import _resolve_key
from febio_cae.application.intent_contracts import parse_budget, settings_from_dict, strict_json
from febio_cae.application.service import RegisteredCaseService
from febio_cae.cli import case as case_cli
from febio_cae.cli.main import main
from febio_cae.domain.partial_case_spec import PartialCaseSpec
from febio_cae.domain.ports import PortError
from febio_cae.domain.units import Quantity

SETTINGS_ENV = "FEBIO_CAE_NATIVE_LLM_SETTINGS"
MAX_EXPECTED_CALLS = 6
CASE_SPEC_FIELDS = (
    "geometry",
    "support",
    "rigid_tool",
    "motion",
    "contact",
    "mesh_policy",
    "solver_policy",
    "outputs",
    "quality_policy",
)


def _receipt() -> dict[str, Any]:
    return {
        "schema_version": "1",
        "status": "NOT_RUN",
        "package_version": __version__,
        "provider": None,
        "model": None,
        "key_env": None,
        "case_id": None,
        "question_id": None,
        "environment_preflight": {"settings_env": SETTINGS_ENV, "status": "NOT_RUN"},
        "operation_ids": [],
        "actions": [],
        "call_count": 0,
        "max_expected_calls": MAX_EXPECTED_CALLS,
        "final_revision_id": None,
        "final_draft_generation": None,
        "scope": {
            "registered_case": "repository synthetic fixture",
            "geometry": "synthetic only",
            "llm": "live configured provider through the product adapter",
            "native_solver": False,
            "febio_studio": False,
            "real_model": False,
        },
    }


def _write_receipt(path: Path, receipt: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _environment_not_ready(receipt: dict[str, Any], reason: str) -> NoReturn:
    receipt["status"] = "ENVIRONMENT_NOT_READY"
    receipt["environment_preflight"] = {
        "settings_env": SETTINGS_ENV,
        "status": "ENVIRONMENT_NOT_READY",
        "reason": reason,
    }
    pytest.fail(f"ENVIRONMENT_NOT_READY: {reason}", pytrace=False)


def _load_approved_settings(receipt: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    configured_path = os.environ.get(SETTINGS_ENV)
    if not configured_path:
        _environment_not_ready(receipt, f"{SETTINGS_ENV} is unset")

    settings_path = Path(configured_path).expanduser().absolute()
    if not settings_path.is_file():
        _environment_not_ready(receipt, f"{SETTINGS_ENV} does not name an existing file")

    try:
        settings = settings_from_dict(strict_json(settings_path.read_bytes()))
    except (OSError, OverflowError, PortError, TypeError, ValueError):
        _environment_not_ready(
            receipt,
            "the configured JSON is not accepted by the public settings parser",
        )

    key_env = settings["key_env"]
    receipt.update(
        {
            "provider": settings["provider"],
            "model": settings["model"],
            "key_env": key_env,
        }
    )
    try:
        _resolve_key(key_env)
    except PortError:
        _environment_not_ready(receipt, "the configured provider key is unavailable or unusable")

    receipt.update(
        {
            "environment_preflight": {
                "settings_env": SETTINGS_ENV,
                "status": "READY",
                "parser": "febio_cae.application.intent_contracts.settings_from_dict",
            },
        }
    )
    return settings_path, settings


def _load_repository_fixture() -> ModuleType:
    """Load the existing fixture by path so explicit tests/native collection works."""

    fixture_path = (
        Path(__file__).resolve().parents[1]
        / "component"
        / "application"
        / "test_persistence_authority.py"
    )
    name = "_ai02_persistence_fixture"
    spec = importlib.util.spec_from_file_location(name, fixture_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("repository synthetic fixture cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _setup_synthetic_case(tmp_path: Path) -> tuple[RegisteredCaseService, Any, Any]:
    fixture = _load_repository_fixture()
    service, created, _ = fixture._created(tmp_path)
    spec = fixture.complete_spec()
    service.register_mesh_quality(created.case_id, fixture._quality_registration())
    service.set_spec(
        created.case_id,
        values=PartialCaseSpec(
            **{name: getattr(spec, name) for name in CASE_SPEC_FIELDS},
        ),
        expected_generation=0,
        evidence=(fixture._evidence("case_revision.spec"),),
    )
    return service, created, spec


def _revision_snapshot(service: RegisteredCaseService, case_id: str) -> dict[str, bytes]:
    revision_root = service._storage(case_id).root / "cases" / case_id / "revisions"
    return {
        path.relative_to(revision_root).as_posix(): path.read_bytes()
        for path in sorted(revision_root.glob("*/revision.json"))
        if path.is_file()
    }


def _assert_answered_material(material: Any, original_material: Any) -> None:
    assert type(material) is type(original_material)
    assert material.youngs_modulus.to_si() == Quantity(1_000_000, "Pa")
    assert material.poisson_ratio == Quantity(0.3, "1")
    assert material.applicability.strain_statement == "applicable"
    assert material.applicability.rate_statement == "applicable"

    # Evidence and lineage are intentionally excluded from this semantic
    # comparison; the typed values and applicability must match the supplied
    # synthetic material semantics.
    observed = replace(
        material,
        model_evidence=original_material.model_evidence,
        youngs_modulus_evidence=original_material.youngs_modulus_evidence,
        poisson_ratio_evidence=original_material.poisson_ratio_evidence,
        applicability=replace(
            material.applicability,
            strain_evidence=original_material.applicability.strain_evidence,
            rate_evidence=original_material.applicability.rate_evidence,
        ),
    )
    expected = replace(
        original_material,
        applicability=replace(
            original_material.applicability,
            strain_statement="applicable",
            rate_statement="applicable",
        ),
    )
    assert observed.to_bytes() == expected.to_bytes()


def _invoke_llm(
    capsys: pytest.CaptureFixture[str],
    created: Any,
    settings_path: Path,
    action: str,
    generation: int,
    operation_id: str,
    text: str,
    *extra: str,
) -> tuple[int, dict[str, Any]]:
    code = main(
        [
            "case",
            action,
            created.case_id,
            "--llm-settings",
            str(settings_path),
            "--expected-generation",
            str(generation),
            "--operation-id",
            operation_id,
            "--text",
            text,
            "--json",
            *extra,
        ]
    )
    return code, json.loads(capsys.readouterr().out)


def _invoke_case(capsys: pytest.CaptureFixture[str], *arguments: str) -> tuple[int, dict[str, Any]]:
    code = main(["case", *arguments, "--json"])
    return code, json.loads(capsys.readouterr().out)


def _record_operation(
    receipt: dict[str, Any], action: str, operation_id: str, result: dict[str, Any]
) -> None:
    operation = result.get("operation")
    assert isinstance(operation, dict)
    assert operation.get("operation_id") == operation_id
    receipt["operation_ids"].append(operation_id)
    receipt["actions"].append(
        {
            "action": action,
            "operation_id": operation_id,
            "status": result.get("status"),
            "generation": result.get("draft", {}).get("generation"),
            "revision_id": result.get("revision_id"),
            "attempted_requests": operation["attempted_requests"],
            "reserved_calls": operation["reserved_calls"],
        }
    )
    receipt["call_count"] = sum(
        item.get("attempted_requests", 0)
        for item in receipt["actions"]
        if item.get("action") in {"intent", "answer", "edit"}
    )
    assert operation.get("reserved_calls") == 2
    assert operation.get("attempted_requests") == 2


@pytest.mark.llm
def test_live_ai02_japanese_intent_answer_freeze_and_e_edit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Run AI-02 only when the caller supplies the approved live configuration."""

    receipt_path = tmp_path / "ai-02-live-llm-receipt.json"
    receipt = _receipt()
    try:
        settings_path, settings = _load_approved_settings(receipt)
        service, created, original_spec = _setup_synthetic_case(tmp_path)
        receipt["case_id"] = created.case_id
        # Keep the registered synthetic geometry/profiles while preserving the
        # public case CLI and the real, unmodified LLM adapter path.
        monkeypatch.setattr(case_cli, "RegisteredCaseService", lambda **_: service)

        initial_draft = service.current_draft(created.case_id)
        initial_generation = initial_draft.generation
        intent_revision_snapshot = _revision_snapshot(service, created.case_id)
        intent_operation = f"ai02-intent-{uuid.uuid4().hex}"
        intent_text = "材料モデル = 等方線形弾性\nヤング率 = 1 MPa"
        intent_code, intent = _invoke_llm(
            capsys,
            created,
            settings_path,
            "intent",
            initial_generation,
            intent_operation,
            intent_text,
        )
        _record_operation(receipt, "intent", intent_operation, intent)
        assert intent_code == 3
        assert intent["status"] == "NEEDS_INPUT"
        assert intent["draft"]["values"]["material"] is None
        assert set(intent["known_facts"]) == {
            "material.model",
            "material.youngs_modulus",
        }
        known_modulus = intent["known_facts"]["material.youngs_modulus"]
        assert Quantity(float(known_modulus["value"]), known_modulus["unit"]).to_si() == Quantity(
            1_000_000, "Pa"
        )
        assert intent["missing_fields"] == [
            "material.poisson_ratio",
            "material.strain_applicability",
            "material.rate_applicability",
        ]
        assert intent["question"]["target_fields"] == ["material"]
        assert intent["question"]["generation"] == intent["draft"]["generation"]
        intent_draft = service.current_draft(created.case_id)
        assert intent["draft"] == intent_draft.to_dict()
        assert intent_draft.generation == initial_draft.generation + 1
        assert _revision_snapshot(service, created.case_id) == intent_revision_snapshot
        receipt["question_id"] = intent["question"]["question_id"]

        replay_before = service.current_draft(created.case_id)
        replay_revision_snapshot = _revision_snapshot(service, created.case_id)
        replay_frozen_revision = service._storage(created.case_id).current_frozen_revision(
            created.case_id
        )
        replay_code, replay = _invoke_llm(
            capsys,
            created,
            settings_path,
            "intent",
            initial_generation,
            intent_operation,
            intent_text,
        )
        assert replay_code == intent_code
        assert replay == intent
        replay_after = service.current_draft(created.case_id)
        assert replay_after.to_bytes() == replay_before.to_bytes()
        assert replay_after.generation == replay_before.generation
        assert _revision_snapshot(service, created.case_id) == replay_revision_snapshot
        assert service._storage(created.case_id).current_frozen_revision(created.case_id) == (
            replay_frozen_revision
        )
        receipt["actions"].append(
            {
                "action": "intent_reentry",
                "operation_id": intent_operation,
                "status": replay["status"],
            }
        )

        answer_operation = f"ai02-answer-{uuid.uuid4().hex}"
        answer_text = "ポアソン比 = 0.3 1\nひずみ適用性 = 適用可\n速度適用性 = 適用可"
        answer_before = service.current_draft(created.case_id)
        answer_revision_snapshot = _revision_snapshot(service, created.case_id)
        answer_generation = answer_before.generation
        answer_code, answer = _invoke_llm(
            capsys,
            created,
            settings_path,
            "answer",
            answer_generation,
            answer_operation,
            answer_text,
            "--question",
            intent["question"]["question_id"],
        )
        _record_operation(receipt, "answer", answer_operation, answer)
        assert answer_code == 0
        assert answer["status"] == "UPDATED"
        assert answer["draft"]["values"]["material"] is not None
        assert answer["draft"]["values"]["material"]["kind"] == "isotropic_linear_elastic"
        assert set(answer["known_facts"]) == {
            "material.model",
            "material.youngs_modulus",
            "material.poisson_ratio",
            "material.strain_applicability",
            "material.rate_applicability",
        }
        answered_draft = service.current_draft(created.case_id)
        assert answer["draft"] == answered_draft.to_dict()
        assert answered_draft.generation == answer_before.generation + 1
        assert _revision_snapshot(service, created.case_id) == answer_revision_snapshot
        assert answered_draft.values.material is not None
        _assert_answered_material(answered_draft.values.material, original_spec.material)
        for field in CASE_SPEC_FIELDS:
            assert (
                getattr(answered_draft.values, field).to_bytes()
                == getattr(original_spec, field).to_bytes()
            )

        # Numerical policy is explicit test setup, not an inferred physical fact.
        budget_before = service.current_draft(created.case_id)
        budget_generation = budget_before.generation
        prepared_draft = service.set_spec(
            created.case_id,
            values=PartialCaseSpec(budget=parse_budget(settings["budget"])),
            expected_generation=budget_generation,
        )
        assert prepared_draft.generation == budget_before.generation + 1
        assert prepared_draft.to_bytes() == service.current_draft(created.case_id).to_bytes()
        validate_code, validated = _invoke_case(capsys, "validate", created.case_id)
        assert validate_code == 0
        assert validated["status"] == "VALIDATED"
        assert prepared_draft.generation == service.current_draft(created.case_id).generation

        freeze_before = service.current_draft(created.case_id)
        freeze_revision_snapshot = _revision_snapshot(service, created.case_id)
        freeze_code, frozen = _invoke_case(capsys, "freeze", created.case_id)
        assert freeze_code == 0
        assert frozen["status"] == "FROZEN"
        frozen_revision_id = frozen["revision_id"]
        assert isinstance(frozen_revision_id, str) and frozen_revision_id
        parent = service.get_revision(created.case_id, frozen_revision_id)
        assert service.current_draft(created.case_id).to_bytes() == freeze_before.to_bytes()
        assert service.current_draft(created.case_id).generation == freeze_before.generation
        assert _revision_snapshot(service, created.case_id) != freeze_revision_snapshot
        assert parent.spec.budget == prepared_draft.values.budget
        assert parent.spec.material is not None
        _assert_answered_material(parent.spec.material, original_spec.material)
        frozen_values = parent.spec.to_dict()
        for field in CASE_SPEC_FIELDS:
            assert frozen_values[field] == validated["draft"]["values"][field]
        receipt["actions"].append(
            {
                "action": "validate",
                "status": validated["status"],
                "generation": prepared_draft.generation,
            }
        )
        receipt["actions"].append(
            {
                "action": "freeze",
                "status": frozen["status"],
                "generation": prepared_draft.generation,
                "revision_id": frozen_revision_id,
            }
        )
        receipt["final_revision_id"] = frozen_revision_id

        edit_operation = f"ai02-edit-{uuid.uuid4().hex}"
        edit_before = service.current_draft(created.case_id)
        edit_revision_snapshot = _revision_snapshot(service, created.case_id)
        edit_frozen_revision = service._storage(created.case_id).current_frozen_revision(
            created.case_id
        )
        edit_generation = edit_before.generation
        edit_code, edited = _invoke_llm(
            capsys,
            created,
            settings_path,
            "edit",
            edit_generation,
            edit_operation,
            "ヤング率 = 2 MPa",
            "--base",
            frozen_revision_id,
        )
        _record_operation(receipt, "edit", edit_operation, edited)
        assert edit_code == 0
        assert edited["status"] == "UPDATED"
        assert edited["draft"]["parent_revision_id"] == frozen_revision_id
        edited_draft = service.current_draft(created.case_id)
        assert edited["draft"] == edited_draft.to_dict()
        assert edited_draft.generation == edit_before.generation + 1
        assert edited_draft.parent_revision_id == frozen_revision_id
        assert edited_draft.parent_spec_digest == parent.spec_digest
        assert _revision_snapshot(service, created.case_id) == edit_revision_snapshot
        assert service._storage(created.case_id).current_frozen_revision(created.case_id) == (
            edit_frozen_revision
        )

        current = edited_draft
        assert current.values.material is not None
        assert current.values.material.youngs_modulus.to_si().value == 2e6
        assert current.parent_revision_id == frozen_revision_id
        assert service._storage(created.case_id).current_frozen_revision(created.case_id) == (
            frozen_revision_id
        )
        assert (
            replace(
                current.values.material,
                youngs_modulus=parent.spec.material.youngs_modulus,
                youngs_modulus_evidence=parent.spec.material.youngs_modulus_evidence,
            )
            == parent.spec.material
        )
        for field in (
            "geometry",
            "support",
            "rigid_tool",
            "motion",
            "contact",
            "mesh_policy",
            "solver_policy",
            "outputs",
            "quality_policy",
            "budget",
        ):
            assert getattr(current.values, field) == getattr(parent.spec, field)
        receipt["final_draft_generation"] = current.generation
        assert receipt["call_count"] == MAX_EXPECTED_CALLS
        receipt["status"] = "PASS"
    except Exception:
        if receipt["status"] == "NOT_RUN":
            receipt["status"] = "FAILED"
        raise
    finally:
        _write_receipt(receipt_path, receipt)
