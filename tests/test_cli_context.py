from __future__ import annotations

import importlib
import importlib.util
import json
import multiprocessing
from pathlib import Path
from typing import Any

import pytest

from febio_cae_harness import cli as cli_module
from febio_cae_harness.contracts import IntentContract, IntentState


def _context_module() -> Any:
    module_name = "febio_cae_harness.cli_context"
    assert importlib.util.find_spec(module_name) is not None, (
        "registered case CLI context has not been implemented"
    )
    module = importlib.import_module(module_name)
    assert hasattr(module, "CaseContextError")
    assert hasattr(module, "CaseContextService")
    return module


def _service(root: Path) -> Any:
    module = _context_module()
    tool_root = root / "tool"
    tool_root.mkdir(parents=True, exist_ok=True)
    return module.CaseContextService._for_tests(
        registry_root=root / "registry",
        tool_root=tool_root,
    )


def _complete_intent(**overrides: object) -> IntentContract:
    values: dict[str, object] = {
        "engineering_question": "What is the synthetic response?",
        "units": {"length": "synthetic-unit"},
        "material": {"name": "synthetic-material"},
        "loads": ({"name": "synthetic-load"},),
        "constraints": ({"name": "synthetic-constraint"},),
        "contact": {"mode": "synthetic-contact"},
        "analysis_step": {"name": "synthetic-step"},
        "roi": ({"name": "synthetic-roi"},),
        "evaluation_quantities": ({"name": "synthetic-output"},),
        "condition_sources": {
            name: {"authoritative": True, "current": True, "source": "synthetic-user"}
            for name in (
                "engineering_question",
                "units",
                "material",
                "loads",
                "constraints",
                "contact",
                "analysis_step",
                "roi",
                "evaluation_quantities",
            )
        },
        "state": IntentState.GATHERING,
    }
    values.update(overrides)
    return IntentContract(**values)  # type: ignore[arg-type]


def _registered_case(root: Path, intent: IntentContract) -> tuple[Any, Path, dict[str, object]]:
    service = _service(root)
    cae_root = root / "02_CAE"
    cae_root.mkdir(parents=True)
    source = root / "synthetic.feb"
    source.write_text("<febio_spec version='4.0' />", encoding="utf-8")
    registered = service.register_root(cae_root)
    created = service.create_case(
        root_id=registered["root_id"],
        case_id="case-a",
        sources=(source,),
        intent=intent,
    )
    return service, cae_root, created


def _fresh_process_open(
    registry_root: str,
    tool_root: str,
    case_id: str,
    results: Any,
) -> None:
    try:
        module = _context_module()
        service = module.CaseContextService._for_tests(
            registry_root=Path(registry_root),
            tool_root=Path(tool_root),
        )
        results.put(("ok", service.open_case(case_id)))
    except BaseException as error:  # pragma: no cover - parent reports exact failure
        results.put(("error", type(error).__name__, str(error)))


def test_case_subcommands_expose_no_free_path_reopen_options(tmp_path: Path) -> None:
    parser = cli_module.build_parser()
    for arguments in (
        ("case", "open", "--case-id", "case-a", "--cae-root", str(tmp_path)),
        ("case", "revise", "--case-id", "case-a", "--tool-root", str(tmp_path)),
        ("case", "answer", "--case-id", "case-a", "--attempt-root", str(tmp_path)),
    ):
        with pytest.raises(SystemExit) as raised:
            parser.parse_args(arguments)
        assert raised.value.code == 2


def test_registered_case_context_survives_fresh_process_and_reconciles(
    tmp_path: Path,
) -> None:
    service, _, created = _registered_case(tmp_path, _complete_intent())

    assert created["case_id"] == "case-a"
    assert created["lifecycle"]["state"] == "BOUND"
    assert created["intent"]["document"]["state"] == "BOUND"
    assert created["pending_questions"] == []
    assert created["inputs"] == [
        {
            "case_path": "01_Input/synthetic.feb",
            "name": "synthetic.feb",
            "sha256": created["inputs"][0]["sha256"],
        }
    ]

    reopened = service.open_case("case-a")
    assert reopened == created

    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    child = context.Process(
        target=_fresh_process_open,
        args=(
            str(tmp_path / "registry"),
            str(tmp_path / "tool"),
            "case-a",
            results,
        ),
    )
    child.start()
    child.join(timeout=15)
    assert not child.is_alive()
    assert child.exitcode == 0
    assert results.get(timeout=5) == ("ok", created)
    results.close()
    results.join_thread()


def test_case_revision_requires_current_digest_before_write(tmp_path: Path) -> None:
    module = _context_module()
    service, cae_root, created = _registered_case(tmp_path, _complete_intent())
    events = cae_root / "case-a" / "90_Temporary" / "events.jsonl"
    before = events.read_bytes()
    revised_intent = _complete_intent(
        engineering_question="What is the revised synthetic response?",
        state=IntentState.BOUND,
    )

    with pytest.raises(module.CaseContextError) as raised:
        service.revise_case(
            case_id="case-a",
            expected_intent_sha256="0" * 64,
            intent=revised_intent,
        )
    assert raised.value.code == "STALE_INTENT_OR_QUESTION"
    assert events.read_bytes() == before

    revised = service.revise_case(
        case_id="case-a",
        expected_intent_sha256=created["intent"]["sha256"],
        intent=revised_intent,
    )
    assert revised["intent"]["document"]["engineering_question"] == (
        "What is the revised synthetic response?"
    )
    assert revised["intent"]["sha256"] != created["intent"]["sha256"]
    assert revised["lifecycle"]["state"] == "BOUND"


def test_case_answer_reissues_exact_question_and_rejects_stale_inputs(tmp_path: Path) -> None:
    module = _context_module()
    blocker = (
        {
            "authoritative": True,
            "condition": "contact",
            "current": True,
            "source": "synthetic-user",
        },
    )
    service, cae_root, created = _registered_case(
        tmp_path,
        _complete_intent(contact=None, unresolved=blocker),
    )
    assert created["lifecycle"]["state"] == "ASK_AND_BLOCK"
    question = created["pending_questions"][0]
    events = cae_root / "case-a" / "90_Temporary" / "events.jsonl"
    before = events.read_bytes()

    for digest, question_id in (
        ("0" * 64, question["question_id"]),
        (created["intent"]["sha256"], "0" * 64),
    ):
        with pytest.raises(module.CaseContextError) as raised:
            service.answer_case(
                case_id="case-a",
                expected_intent_sha256=digest,
                question_id=question_id,
                value={"mode": "explicit-synthetic-contact"},
                source="synthetic-user",
            )
        assert raised.value.code == "STALE_INTENT_OR_QUESTION"
        assert events.read_bytes() == before

    answered = service.answer_case(
        case_id="case-a",
        expected_intent_sha256=created["intent"]["sha256"],
        question_id=question["question_id"],
        value={"mode": "explicit-synthetic-contact"},
        source="synthetic-user",
    )
    assert answered["lifecycle"]["state"] == "BOUND"
    assert answered["pending_questions"] == []
    assert answered["intent"]["document"]["contact"] == {"mode": "explicit-synthetic-contact"}
    after = events.read_bytes()

    with pytest.raises(module.CaseContextError) as raised:
        service.answer_case(
            case_id="case-a",
            expected_intent_sha256=created["intent"]["sha256"],
            question_id=question["question_id"],
            value={"mode": "stale"},
            source="synthetic-user",
        )
    assert raised.value.code == "STALE_INTENT_OR_QUESTION"
    assert events.read_bytes() == after


def test_case_cli_emits_canonical_json_and_stable_error_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service = _service(tmp_path)
    monkeypatch.setattr(cli_module, "_case_service", lambda: service)
    cae_root = tmp_path / "02_CAE"
    cae_root.mkdir()

    assert cli_module.main(["root", "register", "--cae-root", str(cae_root)]) == 0
    registered_output = capsys.readouterr()
    assert registered_output.err == ""
    registered = json.loads(registered_output.out)
    assert registered == {
        "command": "root.register",
        "ok": True,
        "root": {"root_id": registered["root"]["root_id"]},
        "schema_version": "febio-cae-cli/v1",
    }
    assert registered_output.out == json.dumps(registered, sort_keys=True) + "\n"

    assert cli_module.main(["case", "open", "--case-id", "missing-case"]) == 22
    missing_output = capsys.readouterr()
    assert missing_output.out == ""
    failure = json.loads(missing_output.err)
    assert failure == {
        "command": "case.open",
        "error": {
            "code": "CASE_NOT_REGISTERED",
            "message": failure["error"]["message"],
            "retryable": False,
        },
        "ok": False,
        "schema_version": "febio-cae-cli/v1",
    }
    assert "Traceback" not in missing_output.err
    assert missing_output.err == json.dumps(failure, sort_keys=True) + "\n"
