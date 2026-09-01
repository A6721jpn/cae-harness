from __future__ import annotations

import importlib
import importlib.util
import io
import json
import multiprocessing
import os
import sys
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


def _windows_short_path(path: Path) -> Path:
    if os.name != "nt":
        pytest.skip("Windows alternate-path regression")
    import ctypes

    buffer = ctypes.create_unicode_buffer(32_768)
    result = ctypes.windll.kernel32.GetShortPathNameW(os.fspath(path), buffer, len(buffer))
    if result == 0 or result >= len(buffer) or Path(buffer.value) == path:
        pytest.skip("8.3 short names are unavailable on this volume")
    return Path(buffer.value)


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


def _registered_case(root: Path, intent: IntentContract) -> tuple[Any, Path, dict[str, Any], Any]:
    service = _service(root)
    cae_root = root / "02_CAE"
    cae_root.mkdir(parents=True)
    source = root / "synthetic.feb"
    source.write_text("<febio_spec version='4.0' />", encoding="utf-8")
    registered = service.register_root(cae_root)
    created = service.create_case(
        root_capability=registered["capability"],
        case_id="case-a",
        sources=(source,),
        intent=intent,
    )
    return service, cae_root, created, registered["capability"]


def _set_capability_stdin(
    monkeypatch: pytest.MonkeyPatch,
    capability: Any,
) -> None:
    module = _context_module()
    stream = io.TextIOWrapper(io.BytesIO(module.dump_root_capability(capability)), encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", stream)


def _fresh_process_open(
    registry_root: str,
    tool_root: str,
    case_id: str,
    capability_document: bytes,
    results: Any,
) -> None:
    try:
        module = _context_module()
        service = module.CaseContextService._for_tests(
            registry_root=Path(registry_root),
            tool_root=Path(tool_root),
        )
        capability = module.load_root_capability(capability_document)
        results.put(("ok", service.open_case(root_capability=capability, case_id=case_id)))
    except BaseException as error:  # pragma: no cover - parent reports exact failure
        results.put(("error", type(error).__name__, str(error)))


def test_case_subcommands_expose_no_free_path_reopen_options(tmp_path: Path) -> None:
    parser = cli_module.build_parser()
    with pytest.raises(SystemExit) as missing_capability_output:
        parser.parse_args(("root", "register", "--cae-root", str(tmp_path)))
    assert missing_capability_output.value.code == 2
    parsed = parser.parse_args(("case", "open", "--case-id", "case-a", "--capability-stdin"))
    assert parsed.command == "case"
    assert parsed.case_command == "open"
    assert parsed.case_id == "case-a"
    with pytest.raises(SystemExit) as missing_capability:
        parser.parse_args(("case", "open", "--case-id", "case-a"))
    assert missing_capability.value.code == 2
    for arguments in (
        (
            "case",
            "open",
            "--case-id",
            "case-a",
            "--capability-stdin",
            "--cae-root",
            str(tmp_path),
        ),
        (
            "case",
            "revise",
            "--case-id",
            "case-a",
            "--capability-stdin",
            "--tool-root",
            str(tmp_path),
        ),
        (
            "case",
            "answer",
            "--case-id",
            "case-a",
            "--capability-stdin",
            "--attempt-root",
            str(tmp_path),
        ),
    ):
        with pytest.raises(SystemExit) as raised:
            parser.parse_args(arguments)
        assert raised.value.code == 2


def test_registered_case_context_survives_fresh_process_and_reconciles(
    tmp_path: Path,
) -> None:
    service, _, created, capability = _registered_case(tmp_path, _complete_intent())

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

    reopened = service.open_case(root_capability=capability, case_id="case-a")
    assert reopened == created

    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    child = context.Process(
        target=_fresh_process_open,
        args=(
            str(tmp_path / "registry"),
            str(tmp_path / "tool"),
            "case-a",
            _context_module().dump_root_capability(capability),
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
    service, cae_root, created, capability = _registered_case(tmp_path, _complete_intent())
    events = cae_root / "case-a" / "90_Temporary" / "events.jsonl"
    before = events.read_bytes()
    revised_intent = _complete_intent(
        engineering_question="What is the revised synthetic response?",
        state=IntentState.BOUND,
    )

    with pytest.raises(module.CaseContextError) as raised:
        service.revise_case(
            root_capability=capability,
            case_id="case-a",
            expected_intent_sha256="0" * 64,
            intent=revised_intent,
        )
    assert raised.value.code == "STALE_INTENT_OR_QUESTION"
    assert events.read_bytes() == before

    revised = service.revise_case(
        root_capability=capability,
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
    service, cae_root, created, capability = _registered_case(
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
                root_capability=capability,
                case_id="case-a",
                expected_intent_sha256=digest,
                question_id=question_id,
                value={"mode": "explicit-synthetic-contact"},
                source="synthetic-user",
            )
        assert raised.value.code == "STALE_INTENT_OR_QUESTION"
        assert events.read_bytes() == before

    answered = service.answer_case(
        root_capability=capability,
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
            root_capability=capability,
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
    case_registration = service.register_root(cae_root)
    _set_capability_stdin(monkeypatch, case_registration["capability"])
    assert (
        cli_module.main(["case", "open", "--case-id", "missing-case", "--capability-stdin"]) == 22
    )
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


def test_root_register_emits_capability_only_when_explicit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _context_module()
    service = _service(tmp_path)
    monkeypatch.setattr(cli_module, "_case_service", lambda: service)
    cae_root = tmp_path / "02_CAE"
    cae_root.mkdir()

    assert (
        cli_module.main(["root", "register", "--cae-root", str(cae_root), "--emit-capability"]) == 0
    )
    captured = capsys.readouterr()
    assert captured.err == ""
    capability = module.load_root_capability(captured.out.encode("utf-8"))
    assert captured.out.encode("utf-8") == module.dump_root_capability(capability)


def test_registered_root_identity_replacement_is_rejected(tmp_path: Path) -> None:
    module = _context_module()
    service = _service(tmp_path)
    cae_root = tmp_path / "02_CAE"
    cae_root.mkdir()
    registered = service.register_root(cae_root)
    displaced = tmp_path / "displaced-02_CAE"
    cae_root.rename(displaced)
    cae_root.mkdir()
    source = tmp_path / "synthetic.feb"
    source.write_text("<febio_spec />", encoding="utf-8")

    with pytest.raises(module.CaseContextError) as raised:
        service.create_case(
            root_capability=registered["capability"],
            case_id="case-a",
            sources=(source,),
            intent=_complete_intent(),
        )
    assert raised.value.code == "BOUNDARY_OR_IDENTITY_VIOLATION"
    assert tuple(cae_root.iterdir()) == ()

    with pytest.raises(module.CaseContextError) as repeated:
        service.register_root(cae_root)
    assert repeated.value.code == "REGISTRATION_CONFLICT"


def test_case_create_preserves_source_and_writes_no_tool_or_root_control_file(
    tmp_path: Path,
) -> None:
    source_bytes = b"<febio_spec version='4.0' />"
    source = tmp_path / "source.feb"
    source.write_bytes(source_bytes)
    service = _service(tmp_path)
    cae_root = tmp_path / "02_CAE"
    cae_root.mkdir()
    registered = service.register_root(cae_root)

    service.create_case(
        root_capability=registered["capability"],
        case_id="case-a",
        sources=(source,),
        intent=_complete_intent(),
    )

    assert source.read_bytes() == source_bytes
    assert [path for path in (tmp_path / "tool").rglob("*") if path.is_file()] == []
    assert sorted(path.name for path in cae_root.iterdir()) == ["case-a"]
    assert not (cae_root / "registry.json").exists()


def test_json_inputs_reject_unknown_fields_and_nonfinite_values(tmp_path: Path) -> None:
    module = _context_module()
    intent_path = tmp_path / "intent.json"
    invalid_intent = _complete_intent().to_dict()
    invalid_intent["caller_extension"] = True
    intent_path.write_text(json.dumps(invalid_intent), encoding="utf-8")
    with pytest.raises(module.CaseContextError) as intent_error:
        module.load_intent_document(intent_path)
    assert intent_error.value.code == "INVALID_INPUT"

    answer_path = tmp_path / "answer.json"
    answer_path.write_text('{"source":"synthetic-user","value":NaN}', encoding="utf-8")
    with pytest.raises(module.CaseContextError) as answer_error:
        module.load_answer_document(answer_path)
    assert answer_error.value.code == "INVALID_INPUT"


def test_cli_case_projection_omits_intent_values_and_absolute_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service, _, _, capability = _registered_case(tmp_path, _complete_intent())
    monkeypatch.setattr(cli_module, "_case_service", lambda: service)
    _set_capability_stdin(monkeypatch, capability)

    assert cli_module.main(["case", "open", "--case-id", "case-a", "--capability-stdin"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    payload = json.loads(captured.out)
    encoded = captured.out
    assert payload["case"]["intent"] == {"sha256": payload["case"]["intent"]["sha256"]}
    assert "synthetic-material" not in encoded
    assert str(tmp_path) not in encoded
    assert "attempt" not in payload["case"]


def test_production_registry_ignores_environment_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _context_module()
    known_folder = tmp_path / "known-folder"
    known_folder.mkdir()
    forged_environment = tmp_path / "forged-local-app-data"
    monkeypatch.setenv("LOCALAPPDATA", str(forged_environment))
    monkeypatch.setattr(module, "_local_app_data_known_folder", lambda: known_folder)
    service = module.CaseContextService()
    cae_root = tmp_path / "02_CAE"
    cae_root.mkdir()

    service.register_root(cae_root)

    assert (known_folder / "FEBioCaeWorkbench" / "case-registry-v1" / "registry.json").is_file()
    assert not forged_environment.exists()


def test_root_registry_stores_only_capability_hashes(tmp_path: Path) -> None:
    module = _context_module()
    service = _service(tmp_path)
    cae_root = tmp_path / "02_CAE"
    cae_root.mkdir()

    registered = service.register_root(cae_root)
    capability_document = json.loads(module.dump_root_capability(registered["capability"]))
    registry_bytes = (tmp_path / "registry" / "registry.json").read_bytes()
    registry = json.loads(registry_bytes)

    assert set(registry["roots"][0]) == {
        "root_id",
        "projection_sha256",
        "binding_sha256",
    }
    assert os.fspath(cae_root).encode("utf-8") not in registry_bytes
    assert capability_document["secret"].encode("ascii") not in registry_bytes
    source = tmp_path / "synthetic.feb"
    source.write_text("<febio_spec />", encoding="utf-8")
    service.create_case(
        root_capability=registered["capability"],
        case_id="case-a",
        sources=(source,),
        intent=_complete_intent(),
    )
    for evidence_path in (cae_root / "case-a").rglob("*"):
        if evidence_path.is_file():
            evidence = evidence_path.read_bytes()
            assert capability_document["secret"].encode("ascii") not in evidence
            assert os.fspath(cae_root).encode("utf-8") not in evidence


def test_case_operations_require_exact_root_capability(tmp_path: Path) -> None:
    module = _context_module()
    service = _service(tmp_path)
    cae_root = tmp_path / "02_CAE"
    cae_root.mkdir()
    service.register_root(cae_root)
    source = tmp_path / "synthetic.feb"
    source.write_text("<febio_spec />", encoding="utf-8")

    with pytest.raises(module.CaseContextError) as raised:
        service.create_case(
            root_capability=object(),
            case_id="case-a",
            sources=(source,),
            intent=_complete_intent(),
        )
    assert raised.value.code == "REGISTRY_AUTHORITY_REQUIRED"
    assert tuple(cae_root.iterdir()) == ()


def test_registered_root_rejects_tool_tree_windows_short_path_alias(
    tmp_path: Path,
) -> None:
    module = _context_module()
    tool_root = tmp_path / "tool-root-with-a-long-name"
    cae_root = tool_root / "nested-location-with-a-long-name" / "02_CAE"
    cae_root.mkdir(parents=True)
    service = module.CaseContextService._for_tests(
        registry_root=tmp_path / "registry",
        tool_root=tool_root,
    )
    alias = _windows_short_path(cae_root)

    with pytest.raises(module.CaseContextError) as raised:
        service.register_root(alias)
    assert raised.value.code == "BOUNDARY_OR_IDENTITY_VIOLATION"
    assert not (tmp_path / "registry" / "registry.json").exists()


def test_forged_registry_root_record_cannot_redirect_case_creation(tmp_path: Path) -> None:
    module = _context_module()
    service = _service(tmp_path)
    cae_root = tmp_path / "02_CAE"
    cae_root.mkdir()
    registered = service.register_root(cae_root)
    registry_path = tmp_path / "registry" / "registry.json"
    document = json.loads(registry_path.read_bytes())
    document["roots"][0]["projection_sha256"] = "f" * 64
    body = {name: document[name] for name in ("schema_version", "roots", "cases")}
    document["sha256"] = module._digest(body)
    registry_path.write_bytes(module._canonical_bytes(document) + b"\n")
    source = tmp_path / "synthetic.feb"
    source.write_text("<febio_spec />", encoding="utf-8")

    with pytest.raises(module.CaseContextError) as raised:
        service.create_case(
            root_capability=registered["capability"],
            case_id="case-a",
            sources=(source,),
            intent=_complete_intent(),
        )
    assert raised.value.code in {
        "BOUNDARY_OR_IDENTITY_VIOLATION",
        "EVIDENCE_INTEGRITY_FAILURE",
    }
    assert tuple(cae_root.iterdir()) == ()


def test_coherent_registry_rewrite_without_capability_cannot_redirect_case_creation(
    tmp_path: Path,
) -> None:
    module = _context_module()
    service = _service(tmp_path)
    cae_root = tmp_path / "02_CAE"
    cae_root.mkdir()
    registered = service.register_root(cae_root)
    foreign = tmp_path / "foreign-location" / "02_CAE"
    foreign.mkdir(parents=True)
    registry_path = tmp_path / "registry" / "registry.json"
    document = json.loads(registry_path.read_bytes())
    document["roots"][0] = {
        "root_id": "root-" + "f" * 64,
        "projection_sha256": "e" * 64,
        "binding_sha256": "d" * 64,
    }
    body = {name: document[name] for name in ("schema_version", "roots", "cases")}
    document["sha256"] = module._digest(body)
    registry_path.write_bytes(module._canonical_bytes(document) + b"\n")
    source = tmp_path / "synthetic.feb"
    source.write_text("<febio_spec />", encoding="utf-8")

    with pytest.raises(module.CaseContextError) as raised:
        service.create_case(
            root_capability=registered["capability"],
            case_id="case-a",
            sources=(source,),
            intent=_complete_intent(),
        )
    assert raised.value.code in {
        "REGISTRY_AUTHORITY_REQUIRED",
        "EVIDENCE_INTEGRITY_FAILURE",
    }
    assert tuple(foreign.iterdir()) == ()
    assert tuple(cae_root.iterdir()) == ()


def test_failed_case_creation_releases_only_untouched_preparing_reservation(
    tmp_path: Path,
) -> None:
    module = _context_module()
    service = _service(tmp_path)
    cae_root = tmp_path / "02_CAE"
    cae_root.mkdir()
    registered = service.register_root(cae_root)
    missing = tmp_path / "private-missing-source.feb"

    with pytest.raises(module.CaseContextError):
        service.create_case(
            root_capability=registered["capability"],
            case_id="case-a",
            sources=(missing,),
            intent=_complete_intent(),
        )
    registry = json.loads((tmp_path / "registry" / "registry.json").read_bytes())
    assert registry["cases"] == []

    source = tmp_path / "synthetic.feb"
    source.write_text("<febio_spec />", encoding="utf-8")
    created = service.create_case(
        root_capability=registered["capability"],
        case_id="case-a",
        sources=(source,),
        intent=_complete_intent(),
    )
    assert created["case_id"] == "case-a"


def test_json_documents_require_canonical_bytes_and_unique_keys(tmp_path: Path) -> None:
    module = _context_module()
    intent_path = tmp_path / "intent.json"
    intent_path.write_text(json.dumps(_complete_intent().to_dict(), indent=2), encoding="utf-8")
    with pytest.raises(module.CaseContextError) as intent_error:
        module.load_intent_document(intent_path)
    assert intent_error.value.code == "INVALID_INPUT"

    answer_path = tmp_path / "answer.json"
    answer_path.write_text(
        '{"source":"first","source":"second","value":{"mode":"synthetic"}}\n',
        encoding="utf-8",
    )
    with pytest.raises(module.CaseContextError) as answer_error:
        module.load_answer_document(answer_path)
    assert answer_error.value.code == "INVALID_INPUT"


def test_case_cli_error_omits_absolute_input_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _context_module()
    service = _service(tmp_path)
    monkeypatch.setattr(cli_module, "_case_service", lambda: service)
    cae_root = tmp_path / "02_CAE"
    cae_root.mkdir()
    registered = service.register_root(cae_root)
    intent_path = tmp_path / "intent.json"
    intent_path.write_bytes(module._canonical_bytes(_complete_intent().to_dict()) + b"\n")
    missing = tmp_path / "private-missing-source.feb"
    _set_capability_stdin(monkeypatch, registered["capability"])

    assert (
        cli_module.main(
            [
                "case",
                "create",
                "--case-id",
                "case-a",
                "--intent-file",
                str(intent_path),
                "--input",
                str(missing),
                "--capability-stdin",
            ]
        )
        == 20
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert str(tmp_path) not in captured.err
    assert missing.name not in captured.err


def test_same_registered_root_alias_is_rejected_without_duplicate_identity(
    tmp_path: Path,
) -> None:
    module = _context_module()
    tool_root = tmp_path / "tool"
    tool_root.mkdir()
    cae_root = tmp_path / "external-location-with-a-long-name" / "02_CAE"
    cae_root.mkdir(parents=True)
    service = module.CaseContextService._for_tests(
        registry_root=tmp_path / "registry",
        tool_root=tool_root,
    )

    service.register_root(cae_root)
    with pytest.raises(module.CaseContextError) as raised:
        service.register_root(_windows_short_path(cae_root))

    assert raised.value.code == "REGISTRATION_CONFLICT"
    registry = json.loads((tmp_path / "registry" / "registry.json").read_bytes())
    assert len(registry["roots"]) == 1


def test_interrupted_preparing_without_case_tree_recovers_on_retry(tmp_path: Path) -> None:
    module = _context_module()
    service = _service(tmp_path)
    cae_root = tmp_path / "02_CAE"
    cae_root.mkdir()
    registered = service.register_root(cae_root)
    registry_path = tmp_path / "registry" / "registry.json"
    document = json.loads(registry_path.read_bytes())
    document["cases"] = [
        {
            "case_id": "case-a",
            "root_id": registered["root_id"],
            "state": "PREPARING",
            "stamp": [],
            "reservation_id": "a" * 64,
        }
    ]
    body = {name: document[name] for name in ("schema_version", "roots", "cases")}
    document["sha256"] = module._digest(body)
    registry_path.write_bytes(module._canonical_bytes(document) + b"\n")
    source = tmp_path / "synthetic.feb"
    source.write_text("<febio_spec />", encoding="utf-8")

    created = service.create_case(
        root_capability=registered["capability"],
        case_id="case-a",
        sources=(source,),
        intent=_complete_intent(),
    )

    assert created["case_id"] == "case-a"
    registry = json.loads(registry_path.read_bytes())
    assert registry["cases"][0]["state"] == "ACTIVE"


def test_interrupted_preparing_with_existing_tree_is_durably_quarantined(
    tmp_path: Path,
) -> None:
    module = _context_module()
    service = _service(tmp_path)
    cae_root = tmp_path / "02_CAE"
    cae_root.mkdir()
    registered = service.register_root(cae_root)
    registry_path = tmp_path / "registry" / "registry.json"
    document = json.loads(registry_path.read_bytes())
    document["cases"] = [
        {
            "case_id": "case-a",
            "root_id": registered["root_id"],
            "state": "PREPARING",
            "stamp": [],
            "reservation_id": "a" * 64,
        }
    ]
    body = {name: document[name] for name in ("schema_version", "roots", "cases")}
    document["sha256"] = module._digest(body)
    registry_path.write_bytes(module._canonical_bytes(document) + b"\n")
    interrupted_tree = cae_root / "case-a"
    interrupted_tree.mkdir()
    marker = interrupted_tree / "foreign-marker.txt"
    marker.write_text("preserve", encoding="utf-8")
    source = tmp_path / "synthetic.feb"
    source.write_text("<febio_spec />", encoding="utf-8")

    with pytest.raises(module.CaseContextError) as raised:
        service.create_case(
            root_capability=registered["capability"],
            case_id="case-a",
            sources=(source,),
            intent=_complete_intent(),
        )
    assert raised.value.code == "REGISTRATION_CONFLICT"
    assert marker.read_text(encoding="utf-8") == "preserve"
    registry = json.loads(registry_path.read_bytes())
    assert registry["cases"][0]["state"] == "QUARANTINED"
