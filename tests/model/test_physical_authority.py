from __future__ import annotations

import copy
import json
import pickle
from dataclasses import replace
from pathlib import Path

import pytest

from febio_cae_harness import model
from febio_cae_harness.contracts import IntentContract
from febio_cae_harness.evidence import EvidenceIntegrityError, EvidenceStore
from febio_cae_harness.workspace import ValidatedCaseWorkspace

ASK_AND_BLOCK = model.ASK_AND_BLOCK
ConditionEvidence = model.ConditionEvidence
EvidenceProvenance = model.EvidenceProvenance
assess_completeness = model.assess_completeness
assess_intent_completeness = model.assess_intent_completeness
inspect_feb_xml = model.inspect_feb_xml
inspect_incomplete_feb = model.inspect_incomplete_feb


def _store(tmp_path: Path, *, suffix: str = "local") -> EvidenceStore:
    workspace = ValidatedCaseWorkspace(
        tmp_path / f"tool-{suffix}",
        tmp_path / f"02_CAE-{suffix}",
    )
    case = workspace.create_case(f"case-{suffix}")
    intent = IntentContract(
        material="neo-Hookean",
        units={"length": "mm"},
        condition_sources={
            "material": {"source": "intent", "location": "intent.json"},
            "units": {"source": "intent", "location": "intent.json"},
        },
    )
    return EvidenceStore(case, intent)


def test_raw_flags_and_direct_records_are_diagnostic_only() -> None:
    provenance = EvidenceProvenance(
        source="user",
        location="chat",
        authoritative=True,
    )
    result = assess_completeness(
        ("material",),
        {
            "material": ConditionEvidence(
                "material",
                "neo-Hookean",
                (provenance,),
            ),
        },
    )

    assert result.resolved == ()
    assert result.missing == ()
    assert result.state == "GATHERING"
    assert result.ask_and_block is False
    assert result.unresolved[0].field_name == "material"
    assert result.unresolved[0].value == "neo-Hookean"
    assert result.unresolved[0].required is False
    assert result.to_dict()["state"] == "GATHERING"


def test_raw_mapping_and_intent_contract_remain_diagnostic() -> None:
    mapping_result = assess_completeness(
        {
            "material": {
                "value": "neo-Hookean",
                "is_authoritative": True,
                "source": "user",
            }
        }
    )
    contract_result = assess_intent_completeness(
        IntentContract(
            material="neo-Hookean",
            condition_sources={"material": "user"},
        ),
        ("material",),
    )

    for result in (mapping_result, contract_result):
        assert result.complete is False
        assert result.resolved == ()
        assert result.missing == ()
        assert result.state == "GATHERING"
        json.dumps(result.to_dict())

    assert mapping_result.evidence[0].provenance[0].source == "user"
    assert mapping_result.evidence[0].provenance[0].authoritative is True


def test_diagnostic_completeness_cannot_become_a_physical_question() -> None:
    result = assess_completeness(
        ("material",),
        {"material": ConditionEvidence("material", "neo-Hookean")},
    )

    inventory = inspect_incomplete_feb(inspect_feb_xml(b"<febio_spec/>"), result)

    assert inventory.questions == ()
    assert inventory.ready is False


def test_direct_completeness_result_cannot_authorize_questions_or_ready() -> None:
    direct = model.CompletenessResult(
        required=("loads",),
        resolved=(),
        missing=(
            model.MissingConditionFact(
                "loads",
                "caller supplied an authoritative-looking fact",
                (EvidenceProvenance("caller", "chat", authoritative=True),),
            ),
        ),
        unresolved=(),
        state=ASK_AND_BLOCK,
    )

    inventory = inspect_incomplete_feb(inspect_feb_xml(b"<febio_spec/>"), direct)

    assert inventory.questions == ()
    assert inventory.ready is False


def test_completeness_consumers_require_the_exact_expected_snapshot(tmp_path: Path) -> None:
    store_a = _store(tmp_path, suffix="case-a")
    snapshot_a = store_a.issue_intent_snapshot()
    authority_a = model.issue_completeness_authority(snapshot_a, ("units",))
    result_a = assess_completeness(authority_a)

    store_b = _store(tmp_path, suffix="case-b")
    snapshot_b = store_b.issue_intent_snapshot()
    feb = inspect_feb_xml(b"<febio_spec/>")
    no_units = model.inspect_step(
        b"ISO-10303-21;HEADER;ENDSEC;DATA;"
        b"#1 = CARTESIAN_POINT('',(0.,0.,0.));ENDSEC;"
        b"END-ISO-10303-21;"
    )

    omitted_inventory = inspect_incomplete_feb(feb, result_a)
    foreign_inventory = inspect_incomplete_feb(feb, result_a, snapshot=snapshot_b)
    exact_inventory = inspect_incomplete_feb(feb, result_a, snapshot=snapshot_a)

    assert omitted_inventory.questions == ()
    assert omitted_inventory.ready is False
    assert foreign_inventory.questions == ()
    assert foreign_inventory.ready is False
    assert exact_inventory.questions == ()
    assert exact_inventory.ready is True

    omitted_preflight = model.run_preflight(step=no_units, completeness=result_a)
    foreign_preflight = model.run_preflight(
        step=no_units,
        completeness=result_a,
        snapshot=snapshot_b,
    )
    exact_preflight = model.run_preflight(
        step=no_units,
        completeness=result_a,
        snapshot=snapshot_a,
    )

    for invalid in (omitted_preflight, foreign_preflight):
        codes = {diagnostic.code for diagnostic in invalid.diagnostics}
        assert invalid.ready is False
        assert "INVALID_COMPLETENESS_AUTHORITY" in codes
        assert "UNRESOLVED_UNITS" in codes
        assert invalid.to_dict()["completeness"] is None
    assert exact_preflight.ready is True
    assert not any(item.code == "UNRESOLVED_UNITS" for item in exact_preflight.diagnostics)


def _stale_after_fourth_authority_validation(
    monkeypatch: pytest.MonkeyPatch,
    store: EvidenceStore,
) -> None:
    original = model.CompletenessAuthority._validated_binding
    calls = 0

    def hooked(authority: model.CompletenessAuthority) -> object:
        nonlocal calls
        binding = original(authority)
        calls += 1
        if calls == 4:
            payload = store.intent_path.read_text(encoding="utf-8")
            store.intent_path.write_text(
                payload.replace("neo-Hookean", "tampered"),
                encoding="utf-8",
            )
        return binding

    monkeypatch.setattr(model.CompletenessAuthority, "_validated_binding", hooked)


def test_incomplete_inventory_receipt_revalidates_at_consumption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    snapshot = store.issue_intent_snapshot()
    result = assess_completeness(model.issue_completeness_authority(snapshot, ("material",)))
    _stale_after_fourth_authority_validation(monkeypatch, store)

    inventory = inspect_incomplete_feb(
        inspect_feb_xml(b"<febio_spec/>"),
        result,
        snapshot=snapshot,
    )

    assert inventory.ready is False
    assert inventory.questions == ()
    assert inventory.to_dict()["ready"] is False
    assert inventory.to_dict()["questions"] == []


def test_preflight_receipt_revalidates_at_consumption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    snapshot = store.issue_intent_snapshot()
    result = assess_completeness(model.issue_completeness_authority(snapshot, ("material",)))
    _stale_after_fourth_authority_validation(monkeypatch, store)

    preflight = model.run_preflight(completeness=result, snapshot=snapshot)

    assert preflight.ready is False
    assert preflight.to_dict()["ready"] is False
    assert preflight.to_dict()["status"] == "BLOCKED"


def test_direct_consumer_construction_has_no_action_receipt() -> None:
    question = model.MissingConditionQuestion(
        condition="loads",
        reason="caller supplied a question",
        evidence=(EvidenceProvenance("caller", "chat", authoritative=True),),
    )
    inventory = model.IncompleteFebInventory((), (question,), True)
    preflight = model.PreflightResult()

    assert inventory.questions == ()
    assert inventory.ready is False
    assert inventory.to_dict()["questions"] == []
    assert inventory.to_dict()["ready"] is False
    assert preflight.ready is False
    assert preflight.to_dict()["ready"] is False


def test_result_binding_rejects_copies(tmp_path: Path) -> None:
    store = _store(tmp_path)
    snapshot = store.issue_intent_snapshot()
    authority = model.issue_completeness_authority(snapshot, ("material",))
    result = assess_completeness(authority)
    feb = inspect_feb_xml(b"<febio_spec/>")

    assert inspect_incomplete_feb(feb, result, snapshot=snapshot).ready is True

    copied = copy.copy(result)
    copied_inventory = inspect_incomplete_feb(feb, copied, snapshot=snapshot)
    assert copied_inventory.questions == ()
    assert copied_inventory.ready is False


def test_result_binding_rejects_mutations(tmp_path: Path) -> None:
    store = _store(tmp_path)
    snapshot = store.issue_intent_snapshot()
    authority = model.issue_completeness_authority(snapshot, ("material",))
    result = assess_completeness(authority)
    feb = inspect_feb_xml(b"<febio_spec/>")
    assert inspect_incomplete_feb(feb, result, snapshot=snapshot).ready is True

    object.__setattr__(
        result,
        "missing",
        (model.MissingConditionFact("material", "late forged fact"),),
    )
    mutated_inventory = inspect_incomplete_feb(feb, result, snapshot=snapshot)
    assert mutated_inventory.questions == ()
    assert mutated_inventory.ready is False


def test_result_binding_rejects_stale_authority(tmp_path: Path) -> None:
    store = _store(tmp_path)
    fresh_snapshot = store.issue_intent_snapshot()
    fresh_authority = model.issue_completeness_authority(fresh_snapshot, ("material",))
    fresh_result = assess_completeness(fresh_authority)
    feb = inspect_feb_xml(b"<febio_spec/>")
    assert inspect_incomplete_feb(feb, fresh_result, snapshot=fresh_snapshot).ready is True

    payload = store.intent_path.read_text(encoding="utf-8")
    store.intent_path.write_text(payload.replace("neo-Hookean", "tampered"), encoding="utf-8")

    stale_inventory = inspect_incomplete_feb(feb, fresh_result, snapshot=fresh_snapshot)
    assert stale_inventory.questions == ()
    assert stale_inventory.ready is False


def test_forged_result_fails_closed_before_action() -> None:
    forged = object.__new__(model.CompletenessResult)

    inventory = inspect_incomplete_feb(inspect_feb_xml(b"<febio_spec/>"), forged)

    assert inventory.questions == ()
    assert inventory.ready is False


def test_only_a_live_snapshot_can_issue_a_completeness_authority(tmp_path: Path) -> None:
    store = _store(tmp_path)
    snapshot = store.issue_intent_snapshot()

    authority = model.issue_completeness_authority(snapshot, ("material", "units"))
    result = assess_completeness(authority)

    assert type(authority) is model.CompletenessAuthority
    assert authority.snapshot is snapshot
    assert authority.case_id == store.case_workspace.case_id
    assert authority.intent_sha256 == snapshot.intent_sha256
    assert authority.required == ("material", "units")
    assert authority.values == {
        "material": "neo-Hookean",
        "units": {"length": "mm"},
    }
    assert authority.sources == {
        "material": {"source": "intent", "location": "intent.json"},
        "units": {"source": "intent", "location": "intent.json"},
    }
    with pytest.raises(EvidenceIntegrityError):
        model.issue_completeness_authority(
            snapshot,
            required_names=("material", "units"),
            values={"material": "forged", "units": {"length": "mm"}},
            sources=authority.sources,
        )
    assert result.complete is True
    assert result.state == "BOUND"
    assert result.resolved == ("material", "units")
    assert result.missing == ()
    with pytest.raises(TypeError):
        assess_completeness(snapshot=snapshot, required_conditions={"material": "forged"})
    with pytest.raises(TypeError):
        assess_completeness(snapshot, {"material": "forged"})
    assert (
        assess_completeness(snapshot=snapshot, required_conditions=("material", "units")) == result
    )


def test_missing_snapshot_value_can_block_only_through_issued_authority(tmp_path: Path) -> None:
    store = _store(tmp_path)
    snapshot = store.issue_intent_snapshot()
    authority = model.issue_completeness_authority(snapshot, ("material", "loads"))

    result = assess_completeness(authority)

    assert result.complete is False
    assert result.state == ASK_AND_BLOCK
    assert [item.condition for item in result.missing] == ["loads"]
    assert result.missing[0].authoritative is True


def test_forged_copied_mutated_stale_and_foreign_authorities_fail_closed(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    snapshot = store.issue_intent_snapshot()
    authority = model.issue_completeness_authority(snapshot, ("material",))

    with pytest.raises(TypeError):
        model.CompletenessAuthority()
    forged = object.__new__(model.CompletenessAuthority)
    with pytest.raises(EvidenceIntegrityError):
        assess_completeness(forged)
    with pytest.raises(TypeError):
        copy.copy(authority)
    with pytest.raises(TypeError):
        copy.deepcopy(authority)
    with pytest.raises(TypeError):
        pickle.dumps(authority)
    with pytest.raises(TypeError):
        replace(authority, case_id="forged")  # type: ignore[type-var]

    with pytest.raises(AttributeError):
        authority.case_id = "forged"  # type: ignore[misc]
    with pytest.raises(EvidenceIntegrityError):
        assess_completeness(object.__new__(model.CompletenessAuthority))

    foreign_store = _store(tmp_path, suffix="foreign")
    foreign_snapshot = foreign_store.issue_intent_snapshot()
    foreign_authority = model.issue_completeness_authority(foreign_snapshot, ("material",))
    with pytest.raises(EvidenceIntegrityError):
        authority._validated_for(foreign_snapshot)
    with pytest.raises(EvidenceIntegrityError):
        assess_completeness(foreign_authority, snapshot=snapshot)

    stale_store = _store(tmp_path, suffix="stale")
    stale_snapshot = stale_store.issue_intent_snapshot()
    stale_authority = model.issue_completeness_authority(stale_snapshot, ("material",))
    payload = stale_store.intent_path.read_text(encoding="utf-8")
    stale_store.intent_path.write_text(
        payload.replace("neo-Hookean", "tampered"),
        encoding="utf-8",
    )
    with pytest.raises(EvidenceIntegrityError):
        assess_completeness(stale_authority)
