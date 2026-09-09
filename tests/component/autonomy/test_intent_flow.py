"""Public CLI behavior with registered synthetic sources and private HTTP."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from test_persistence_authority import _created, _evidence, _quality_registration, complete_spec
from test_openai_boundary import response, settings

from febio_cae.adapters.llm import openai_responses as adapter
from febio_cae.cli import case as case_cli
from febio_cae.cli.main import main
from febio_cae.domain.partial_case_spec import PartialCaseSpec


def setup_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any, Path]:
    service, created, _ = _created(tmp_path)
    spec = complete_spec()
    values = PartialCaseSpec(**{name: getattr(spec, name) for name in
        ("geometry", "support", "rigid_tool", "motion", "contact", "mesh_policy", "solver_policy", "outputs", "quality_policy")})
    service.register_mesh_quality(created.case_id, _quality_registration())
    service.set_spec(created.case_id, values=values, expected_generation=0, evidence=(_evidence("case_revision.spec"),))
    monkeypatch.setattr(case_cli, "RegisteredCaseService", lambda **_: service)
    monkeypatch.setattr(adapter, "_resolve_key", lambda _: "private-injected")
    path = tmp_path / "llm.json"
    path.write_text(json.dumps(settings()), encoding="utf-8")
    return service, created, path


def proposal_for(body: dict[str, Any]) -> list[dict[str, str]]:
    content = json.loads(body["input"])
    found = []
    for source in content["sources"]:
        for clause in source["text"].splitlines():
            if " = " not in clause:
                continue
            field, value = clause.split(" = ", 1)
            unit = ""
            if field in {"material.youngs_modulus", "material.poisson_ratio"}:
                value, unit = value.rsplit(" ", 1)
            found.append({"field": field, "value": value, "unit": unit, "source": source["id"],
                          "clause": clause, "entity": "part", "scope": "case"})
    return found


def invoke(capsys: pytest.CaptureFixture[str], created: Any, path: Path, action: str, generation: int,
           operation: str, text: str, *extra: str) -> tuple[int, dict[str, Any]]:
    code = main(["case", action, created.case_id, "--llm-settings", str(path), "--expected-generation", str(generation),
                 "--operation-id", operation, "--text", text, "--json", *extra])
    return code, json.loads(capsys.readouterr().out)


def test_public_intent_answer_freeze_and_explicit_E_edit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    service, created, path = setup_case(tmp_path, monkeypatch)
    calls: list[str] = []
    def http(endpoint: str, body: dict[str, Any], *_: Any) -> dict[str, Any]:
        calls.append(endpoint)
        return {"input_tokens": 100} if endpoint.endswith("input_tokens") else response(proposal_for(body))
    monkeypatch.setattr(adapter, "_http", http)
    text = "material.model = isotropic_linear_elastic\nmaterial.youngs_modulus = 1 MPa"
    code, first = invoke(capsys, created, path, "intent", 1, "initial", text)
    assert code == 3 and first["status"] == "NEEDS_INPUT"
    assert first["draft"]["values"]["material"] is None
    assert "material.youngs_modulus" in first["known_facts"]
    assert "material.youngs_modulus" not in first["missing_fields"]
    question = first["question"]["question_id"]
    count = len(calls)
    assert invoke(capsys, created, path, "intent", 1, "initial", text) == (code, first)
    assert len(calls) == count
    code, second = invoke(capsys, created, path, "answer", 2, "partial", "material.poisson_ratio = 0.3 1", "--question", question)
    assert code == 3 and second["question"]["question_id"] != question
    assert "material.poisson_ratio" in second["known_facts"]
    code, final = invoke(capsys, created, path, "answer", 3, "answer",
        "material.strain_applicability = applicable\nmaterial.rate_applicability = applicable",
        "--question", second["question"]["question_id"])
    assert code == 0 and final["draft"]["values"]["material"] is not None
    # Budget is explicit numerical configuration, never inferred physics.
    from febio_cae.domain.codec import decode_record
    from febio_cae.domain.budget import Budget
    draft = service.set_spec(created.case_id, values=PartialCaseSpec(budget=decode_record(json.dumps(settings()["budget"]), Budget)), expected_generation=4)
    frozen = service.freeze_case(created.case_id)
    assert frozen.status == "FROZEN"
    code, edited = invoke(capsys, created, path, "edit", draft.generation, "edit", "material.youngs_modulus = 2 MPa", "--base", frozen.revision_id)
    assert code == 0 and edited["draft"]["parent_revision_id"] == frozen.revision_id
    current = service.current_draft(created.case_id)
    assert current.values.material.youngs_modulus.to_si().value == 2e6
    assert replace(current.values.material, youngs_modulus=frozen.revision.spec.material.youngs_modulus,
        youngs_modulus_evidence=frozen.revision.spec.material.youngs_modulus_evidence) == frozen.revision.spec.material
    assert service._storage(created.case_id).current_frozen_revision(created.case_id) == frozen.revision_id


def test_negation_conflict_and_wrong_entity_never_ground(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    service, created, path = setup_case(tmp_path, monkeypatch)
    def http(endpoint: str, body: dict[str, Any], *_: Any) -> dict[str, Any]:
        if endpoint.endswith("input_tokens"):
            return {"input_tokens": 100}
        values = proposal_for(body)
        values[0]["entity"] = "other-body"
        return response(values)
    monkeypatch.setattr(adapter, "_http", http)
    text = "material.model = isotropic_linear_elastic\nmaterial.youngs_modulus = not 1 MPa\nmaterial.poisson_ratio = 0.3 1\nmaterial.poisson_ratio = 0.4 1"
    code, result = invoke(capsys, created, path, "intent", 1, "negative", text)
    assert code == 3 and result["known_facts"] == {}
    assert service.current_draft(created.case_id).values.material is None


def test_stale_before_spend_and_during_response_retains_accounting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                capsys: pytest.CaptureFixture[str]) -> None:
    service, created, path = setup_case(tmp_path, monkeypatch)
    calls: list[str] = []
    def http(endpoint: str, body: dict[str, Any], *_: Any) -> dict[str, Any]:
        calls.append(endpoint)
        if endpoint.endswith("input_tokens"):
            return {"input_tokens": 100}
        service.set_spec(created.case_id, values=PartialCaseSpec(), expected_generation=1)
        return response(proposal_for(body))
    monkeypatch.setattr(adapter, "_http", http)
    text = "material.youngs_modulus = 1 MPa"
    assert invoke(capsys, created, path, "intent", 0, "stale", text)[0] == 8
    assert calls == []
    code, result = invoke(capsys, created, path, "intent", 1, "race", text)
    assert code == 8 and result["operation"]["usage"]["total_tokens"] == 120
    assert service.current_draft(created.case_id).generation == 2
    assert invoke(capsys, created, path, "intent", 1, "race", text) == (code, result)
    assert len(calls) == 2
    assert invoke(capsys, created, path, "intent", 1, "race", text + " changed")[0] == 8


def test_numerical_diagnostics_and_uncertain_reentry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                  capsys: pytest.CaptureFixture[str]) -> None:
    _, created, path = setup_case(tmp_path, monkeypatch)
    bad = settings(); bad["output_tokens"] = 15
    path.write_text(json.dumps(bad), encoding="utf-8")
    assert invoke(capsys, created, path, "intent", 1, "bad", "material.model = isotropic_linear_elastic")[0] == 2
    path.write_text(json.dumps(settings()), encoding="utf-8")
    calls: list[str] = []
    def http(endpoint: str, *_: Any) -> dict[str, Any]:
        calls.append(endpoint)
        raise TimeoutError("injected uncertainty")
    monkeypatch.setattr(adapter, "_http", http)
    result = invoke(capsys, created, path, "intent", 1, "timeout", "material.model = isotropic_linear_elastic")
    assert result[0] == 2 and result[1]["operation"]["reserved_tokens"] == 2200
    assert invoke(capsys, created, path, "intent", 1, "timeout", "material.model = isotropic_linear_elastic") == result
    assert len(calls) == 1
