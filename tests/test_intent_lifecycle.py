from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import multiprocessing
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest

from febio_cae_harness.contracts import IntentContract, IntentState
from febio_cae_harness.evidence import EvidenceIntegrityError, EvidenceStore
from febio_cae_harness.workspace import CaseWorkspace, ValidatedCaseWorkspace

_QUESTION_HASH_FIELDS = (
    "schema",
    "case_id",
    "intent_sha256",
    "ordinal",
    "condition",
    "source",
    "detail",
)


def _lifecycle_module() -> Any:
    module_name = "febio_cae_harness.intent_lifecycle"
    assert importlib.util.find_spec(module_name) is not None, (
        "intent lifecycle module has not been implemented"
    )
    module = importlib.import_module(module_name)
    package = importlib.import_module("febio_cae_harness")
    for name in ("IntentLifecycle", "IntentLifecycleResult", "IntentQuestion"):
        assert hasattr(module, name), f"missing lifecycle API: {name}"
        assert getattr(package, name, None) is getattr(module, name), (
            f"package does not export lifecycle API: {name}"
        )
    return module


def _make_store(
    root: Path,
    intent: IntentContract,
    *,
    case_id: str = "case-a",
) -> tuple[ValidatedCaseWorkspace, CaseWorkspace, EvidenceStore]:
    tool_root = root / "tool"
    tool_root.mkdir(parents=True)
    workspace = ValidatedCaseWorkspace(tool_root, root / "02_CAE")
    case = workspace.create_case(case_id)
    return workspace, case, EvidenceStore(case, intent)


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


def _events(store: EvidenceStore) -> list[dict[str, object]]:
    return [json.loads(line) for line in store.events_path.read_text(encoding="utf-8").splitlines()]


def _fresh_process_question(
    tool_root: str,
    cae_root: str,
    case_id: str,
    results: Any,
) -> None:
    try:
        workspace = ValidatedCaseWorkspace(Path(tool_root), Path(cae_root))
        store = EvidenceStore.open(workspace.open_case(case_id))
        module = _lifecycle_module()
        lifecycle = module.IntentLifecycle(store)
        result = lifecycle.reconcile()
        question = result.question
        results.put(("ok", None if question is None else question.to_dict()))
    except BaseException as error:  # pragma: no cover - assertion reports details
        results.put(("error", type(error).__name__, str(error)))


def test_reconcile_persists_gathering_for_incomplete_intent_without_question(
    tmp_path: Path,
) -> None:
    module = _lifecycle_module()
    _, case, store = _make_store(
        tmp_path,
        IntentContract(state=IntentState.BOUND),
    )

    result = module.IntentLifecycle(store).reconcile()

    assert result.state is IntentState.GATHERING
    assert result.authority.current is IntentState.GATHERING
    assert result.snapshot.intent.state is IntentState.GATHERING
    assert result.question is None
    events = _events(store)
    assert [event["event_type"] for event in events] == ["intent_revised"]
    assert EvidenceStore.open(case).intent.state is IntentState.GATHERING


def test_reconcile_persists_bound_only_for_complete_current_authority(tmp_path: Path) -> None:
    module = _lifecycle_module()
    _, case, store = _make_store(tmp_path, _complete_intent())

    result = module.IntentLifecycle(store).reconcile()

    assert result.state is IntentState.BOUND
    assert result.snapshot.intent.state is IntentState.BOUND
    assert result.authority.current is IntentState.BOUND
    assert result.question is None
    assert EvidenceStore.open(case).intent.state is IntentState.BOUND


def test_authoritative_unresolved_question_is_durable_deterministic_and_fresh_process_stable(
    tmp_path: Path,
) -> None:
    module = _lifecycle_module()
    unresolved = (
        {
            "authoritative": True,
            "condition": "contact",
            "current": True,
            "detail": "An explicit contact value is required.",
            "source": "synthetic-user",
        },
    )
    workspace, case, store = _make_store(
        tmp_path,
        _complete_intent(contact=None, unresolved=unresolved),
    )
    lifecycle = module.IntentLifecycle(store)

    result = lifecycle.reconcile()

    assert result.state is IntentState.ASK_AND_BLOCK
    assert result.snapshot.intent.state is IntentState.ASK_AND_BLOCK
    question = result.question
    assert question is not None
    projection = question.to_dict()
    hash_projection = {name: projection[name] for name in _QUESTION_HASH_FIELDS}
    expected_id = hashlib.sha256(
        json.dumps(
            hash_projection,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    assert projection["schema"] == "intent-question-v1"
    assert projection["condition"] == "contact"
    assert projection["ordinal"] == 0
    assert projection["question_id"] == expected_id == question.question_id
    assert "authoritative" in question.prompt.casefold()
    assert "contact" in question.prompt
    with pytest.raises(TypeError):
        module.IntentQuestion()
    with pytest.raises(FrozenInstanceError):
        question.prompt = "changed"

    reopened = module.IntentLifecycle(EvidenceStore.open(case)).reconcile()
    assert reopened.question is not None
    assert reopened.question.to_dict() == projection

    context = multiprocessing.get_context("spawn")
    child_results = context.Queue()
    child = context.Process(
        target=_fresh_process_question,
        args=(
            str(workspace.tool_root),
            str(workspace.cae_root),
            case.case_id,
            child_results,
        ),
    )
    child.start()
    child.join(timeout=10)
    assert not child.is_alive()
    assert child.exitcode == 0
    fresh = child_results.get(timeout=5)
    child_results.close()
    child_results.join_thread()
    assert fresh == ("ok", projection)


def test_answer_is_event_backed_reconciled_and_advances_to_the_next_blocker(
    tmp_path: Path,
) -> None:
    module = _lifecycle_module()
    unresolved = (
        {
            "authoritative": True,
            "condition": "contact",
            "current": True,
            "source": "synthetic-user",
        },
        {
            "authoritative": True,
            "condition": "material",
            "current": True,
            "source": "synthetic-user",
        },
    )
    _, _, store = _make_store(
        tmp_path,
        _complete_intent(contact=None, material=None, unresolved=unresolved),
    )
    lifecycle = module.IntentLifecycle(store)
    first = lifecycle.reconcile()
    assert first.question is not None
    assert first.question.condition == "contact"

    after_contact = lifecycle.answer(
        first.question,
        {"mode": "explicit-synthetic-contact"},
        "synthetic-user",
        detail="Explicit synthetic answer.",
    )

    assert after_contact.state is IntentState.ASK_AND_BLOCK
    assert after_contact.question is not None
    assert after_contact.question.condition == "material"
    assert after_contact.question.question_id != first.question.question_id
    events_after_contact = _events(store)
    answer_projection = events_after_contact[-2]["payload"]
    assert isinstance(answer_projection, dict)
    answer_intent = answer_projection["new_intent"]
    assert isinstance(answer_intent, dict)
    assert answer_intent["state"] == "GATHERING"
    assert answer_intent["contact"] == {"mode": "explicit-synthetic-contact"}
    assert [record["condition"] for record in answer_intent["unresolved"]] == ["material"]

    after_material = lifecycle.answer(
        after_contact.question,
        {"name": "explicit-synthetic-material"},
        "synthetic-user",
    )

    assert after_material.state is IntentState.BOUND
    assert after_material.question is None
    assert after_material.snapshot.intent.state is IntentState.BOUND
    final_intent = after_material.snapshot.intent.to_dict()
    assert final_intent["unresolved"] == []
    sources = final_intent["condition_sources"]
    assert isinstance(sources, dict)
    assert sources["engineering_question"] == {
        "authoritative": True,
        "current": True,
        "source": "synthetic-user",
    }
    contact_source = sources["contact"]
    assert contact_source == {
        "authoritative": True,
        "condition": "contact",
        "current": True,
        "detail": "Explicit synthetic answer.",
        "source": "synthetic-user",
    }
    assert all(event["event_type"] == "intent_revised" for event in _events(store))


def test_answer_rejects_stale_foreign_fabricated_unknown_and_invalid_inputs_without_append(
    tmp_path: Path,
) -> None:
    module = _lifecycle_module()
    lifecycle_type = module.IntentLifecycle
    blocker = (
        {
            "authoritative": True,
            "condition": "contact",
            "current": True,
            "source": "synthetic-user",
        },
    )
    _, _, store = _make_store(
        tmp_path / "current",
        _complete_intent(contact=None, unresolved=blocker),
    )
    lifecycle = lifecycle_type(store)
    current = lifecycle.reconcile()
    assert current.question is not None

    _, _, foreign_store = _make_store(
        tmp_path / "foreign",
        _complete_intent(contact=None, unresolved=blocker),
        case_id="case-b",
    )
    foreign = lifecycle_type(foreign_store).reconcile()
    assert foreign.question is not None

    before = store.events_path.read_bytes()
    for question_id in (foreign.question.question_id, "0" * 64):
        with pytest.raises(EvidenceIntegrityError):
            lifecycle.answer(question_id, "explicit", "synthetic-user")
        assert store.events_path.read_bytes() == before
    for value, source in ((None, "synthetic-user"), (object(), "synthetic-user"), ("x", "")):
        with pytest.raises((TypeError, ValueError, EvidenceIntegrityError)):
            lifecycle.answer(current.question, value, source)
        assert store.events_path.read_bytes() == before

    advanced = lifecycle.answer(
        current.question,
        "explicit-synthetic-contact",
        "synthetic-user",
    )
    assert advanced.state is IntentState.BOUND
    after_advance = store.events_path.read_bytes()
    with pytest.raises(EvidenceIntegrityError):
        lifecycle.answer(current.question, "stale", "synthetic-user")
    assert store.events_path.read_bytes() == after_advance

    unknown = (
        {
            "authoritative": True,
            "condition": "not_a_contract_field",
            "current": True,
            "source": "synthetic-user",
        },
    )
    source_projection = _complete_intent().to_dict()["condition_sources"]
    assert isinstance(source_projection, dict)
    unknown_sources = dict(source_projection)
    unknown_sources["not_a_contract_field"] = {
        "authoritative": True,
        "current": True,
        "source": "synthetic-user",
    }
    _, _, unknown_store = _make_store(
        tmp_path / "unknown",
        _complete_intent(condition_sources=unknown_sources, unresolved=unknown),
    )
    unknown_lifecycle = lifecycle_type(unknown_store)
    unknown_result = unknown_lifecycle.reconcile()
    assert unknown_result.question is not None
    unknown_before = unknown_store.events_path.read_bytes()
    with pytest.raises(EvidenceIntegrityError):
        unknown_lifecycle.answer(
            unknown_result.question,
            "explicit",
            "synthetic-user",
        )
    assert unknown_store.events_path.read_bytes() == unknown_before


def _single_blocker(tmp_path: Path, *, case_id: str = "case-a", **intent_overrides: object) -> tuple[Any, EvidenceStore, Any]:
    module = _lifecycle_module()
    blocker = ({"authoritative": True, "condition": "contact", "current": True, "source": "synthetic-user"},)
    values = {"contact": None, "unresolved": blocker}
    values.update(intent_overrides)
    _, _, store = _make_store(tmp_path, _complete_intent(**values), case_id=case_id)
    lifecycle = module.IntentLifecycle(store)
    result = lifecycle.reconcile()
    assert result.question is not None
    return lifecycle, store, result.question


def test_answer_rejects_bare_computed_current_question_id_without_append(tmp_path: Path) -> None:
    lifecycle, store, question = _single_blocker(tmp_path)
    before = store.events_path.read_bytes()
    with pytest.raises(EvidenceIntegrityError):
        lifecycle.answer(question.question_id, "x", "synthetic-user")
    assert store.events_path.read_bytes() == before


def test_answer_rejects_fabricated_question_object_without_append(tmp_path: Path) -> None:
    lifecycle, store, question = _single_blocker(tmp_path)
    fabricated = object.__new__(type(question))
    before = store.events_path.read_bytes()
    with pytest.raises(EvidenceIntegrityError):
        lifecycle.answer(fabricated, "x", "synthetic-user")
    assert store.events_path.read_bytes() == before


def test_answer_rejects_question_from_same_labels_different_case_root_without_append(tmp_path: Path) -> None:
    lifecycle, store, _ = _single_blocker(tmp_path / "a", case_id="Case-A")
    _, _, other_question = _single_blocker(tmp_path / "b", case_id="case-a")
    before = store.events_path.read_bytes()
    with pytest.raises(EvidenceIntegrityError):
        lifecycle.answer(other_question, "x", "synthetic-user")
    assert store.events_path.read_bytes() == before


def test_answer_rejects_stale_issued_question_after_revision_without_append(tmp_path: Path) -> None:
    lifecycle, store, question = _single_blocker(tmp_path)
    lifecycle.reconcile()
    before = store.events_path.read_bytes()
    with pytest.raises(EvidenceIntegrityError):
        lifecycle.answer(question, "x", "synthetic-user")
    assert store.events_path.read_bytes() == before


def test_unicode_casefold_alias_cannot_select_canonical_intent_field_without_append(tmp_path: Path) -> None:
    lifecycle, store, question = _single_blocker(tmp_path, unresolved=({"authoritative": True, "condition": "CONTACT", "current": True, "source": "synthetic-user"},))
    before = store.events_path.read_bytes()
    with pytest.raises(EvidenceIntegrityError):
        lifecycle.answer(question, "x", "synthetic-user")
    assert store.events_path.read_bytes() == before


def test_answer_removes_nested_mapping_blocker_and_advances(tmp_path: Path) -> None:
    lifecycle, store, question = _single_blocker(
        tmp_path,
        contact=None,
        unresolved=({"condition": "contact", "name": "mode", "field": "mode", "nested": {"name": "mode"}},),
    )
    result = lifecycle.answer(question, {"mode": "new"}, "synthetic-user")
    assert result.state is IntentState.BOUND
    assert result.snapshot.intent.to_dict()["unresolved"] == []
