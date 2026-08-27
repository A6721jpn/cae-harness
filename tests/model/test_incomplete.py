from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from febio_cae_harness.contracts import IntentContract
from febio_cae_harness.evidence import EvidenceStore
from febio_cae_harness.model import (
    ASK_AND_BLOCK,
    CompletenessResult,
    EvidenceProvenance,
    MissingConditionFact,
    MissingConditionQuestion,
    UnresolvedEvidenceField,
    assess_completeness,
    inspect_feb_xml,
    inspect_incomplete_feb,
    issue_completeness_authority,
)
from febio_cae_harness.workspace import ValidatedCaseWorkspace

AUTH_A = EvidenceProvenance("intent", "intent.loads", authoritative=True)
AUTH_B = EvidenceProvenance("case", "case/requirements", authoritative=True)
NON_AUTH = EvidenceProvenance("geometry", "mesh", authoritative=False)

VALID_FEB = b"""
<febio_spec version='4.0'>
  <Material><material id='1' name='synthetic'/></Material>
  <Mesh><Nodes><node id='1'>0,0,0</node></Nodes>
    <Elements mat='1'><elem id='1'>1</elem></Elements>
  </Mesh>
</febio_spec>
"""


def _result(
    required: tuple[str, ...],
    *,
    missing: tuple[MissingConditionFact, ...] = (),
    unresolved: tuple[UnresolvedEvidenceField, ...] = (),
    state: str = ASK_AND_BLOCK,
) -> CompletenessResult:
    return CompletenessResult(
        required=required,
        resolved=(),
        missing=missing,
        unresolved=unresolved,
        state=state,
    )


def _bound_loads(tmp_path: Path) -> CompletenessResult:
    workspace = ValidatedCaseWorkspace(tmp_path / "tool", tmp_path / "02_CAE")
    case = workspace.create_case("case")
    store = EvidenceStore(
        case,
        IntentContract(
            loads="traction",
            condition_sources={"loads": {"source": "intent", "location": "intent.json"}},
        ),
    )
    authority = issue_completeness_authority(store.issue_intent_snapshot(), ("loads",))
    return assess_completeness(authority)


def test_question_is_immutable_authoritative_and_json_shaped() -> None:
    question = MissingConditionQuestion(
        condition="loads",
        reason="the load case is not specified",
        evidence=(AUTH_B, AUTH_A),
    )

    assert question.action == ASK_AND_BLOCK
    assert question.evidence == (AUTH_B, AUTH_A)
    assert question.to_dict() == {
        "condition": "loads",
        "reason": "the load case is not specified",
        "evidence": [AUTH_B.to_dict(), AUTH_A.to_dict()],
        "action": ASK_AND_BLOCK,
    }
    with pytest.raises(FrozenInstanceError):
        question.reason = "guessed"  # type: ignore[misc]
    with pytest.raises(ValueError):
        MissingConditionQuestion("loads", "reason", (NON_AUTH,))
    with pytest.raises(ValueError):
        MissingConditionQuestion("loads", "reason", (), action="EDIT")


def test_structural_diagnostics_are_separate_and_never_questions(tmp_path: Path) -> None:
    feb = inspect_feb_xml(
        b"""
        <not_febio>
          <Material><material id='1'/><material id='1'/></Material>
          <Boundary><fix node_set='missing-set'/></Boundary>
        </not_febio>
        """
    )
    completeness = _bound_loads(tmp_path)

    inventory = inspect_incomplete_feb(feb, completeness)

    assert [item.code for item in inventory.structural_diagnostics] == [
        "INVALID_FEB_ROOT",
        "MISSING_REFERENCE",
        "DUPLICATE_IDENTIFIER",
    ]
    assert inventory.questions == ()
    assert inventory.ready is False


def test_questions_follow_required_order_deduplicate_and_filter_authority() -> None:
    completeness = _result(
        ("loads", "units", "material"),
        missing=(
            MissingConditionFact("material", "material is absent", (AUTH_B,)),
            MissingConditionFact("loads", "loads are absent", (AUTH_A,)),
            MissingConditionFact("geometry", "geometry is absent", (AUTH_A,)),
            MissingConditionFact("units", "units are absent", (NON_AUTH,), authoritative=False),
        ),
        unresolved=(
            UnresolvedEvidenceField("loads", "second load reason", evidence=(AUTH_B,)),
            UnresolvedEvidenceField("units", "no trusted units", evidence=(NON_AUTH,)),
            UnresolvedEvidenceField("contact", "contact is absent", evidence=(AUTH_A,)),
        ),
    )

    inventory = inspect_incomplete_feb(inspect_feb_xml(VALID_FEB), completeness)

    assert [item.condition for item in inventory.questions] == ["loads", "units", "material"]
    assert inventory.questions[0].reason == "loads are absent"
    assert inventory.questions[0].evidence == (AUTH_A, AUTH_B)
    assert inventory.questions[1].reason == "units are absent"
    assert inventory.questions[1].evidence == ()
    assert inventory.questions[2].evidence == (AUTH_B,)
    assert inventory.ready is False


def test_ready_requires_bound_completeness_without_required_unresolved_fields(
    tmp_path: Path,
) -> None:
    complete = _bound_loads(tmp_path)
    ready = inspect_incomplete_feb(inspect_feb_xml(VALID_FEB), complete)
    assert ready.questions == ()
    assert ready.structural_diagnostics == ()
    assert ready.ready is True
    json.dumps(ready.to_dict())

    incomplete = _result(
        ("loads",),
        unresolved=(UnresolvedEvidenceField("loads", "still unresolved"),),
        state="BOUND",
    )
    blocked = inspect_incomplete_feb(inspect_feb_xml(VALID_FEB), incomplete)
    assert blocked.ready is False
    assert blocked.questions == (MissingConditionQuestion("loads", "still unresolved"),)
