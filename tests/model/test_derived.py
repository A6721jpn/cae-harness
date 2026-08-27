import json
from collections.abc import Mapping
from copy import copy
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from febio_cae_harness.autonomy import (
    IntentState,
    IntentStateAuthority,
    Proposal,
    ProposalAuthority,
    ProposalAuthorityManager,
    ProposalClass,
    transition_intent,
)
from febio_cae_harness.contracts import IntentContract, JSONValue
from febio_cae_harness.evidence import EvidenceIntegrityError, EvidenceStore
from febio_cae_harness.model import EvidenceProvenance, IntentImpact, OriginalModel
from febio_cae_harness.model.derived import DerivedFebReceipt, FebPatch, write_derived_feb
from febio_cae_harness.workspace import ValidatedCaseWorkspace

FEB = b"""<?xml version="1.0" encoding="UTF-8"?>
<febio_spec version="4.0">
  <Control><time_steps>4</time_steps></Control>
  <Module type="solid"/>
  <Material><material id="1" name="synthetic"/></Material>
</febio_spec>
"""
TIME = "/febio_spec/Control[1]/time_steps[1]"
MODULE = "/febio_spec/Module[1]"
EVIDENCE = EvidenceProvenance("synthetic-intent", "intent.json", authoritative=True)


def patch(
    target: str,
    mode: str,
    value: str | int | float | bool,
    *,
    attribute_name: str | None = None,
    provenance: tuple[EvidenceProvenance, ...] = (EVIDENCE,),
    intent_impact: IntentImpact = IntentImpact.INTENT_PRESERVING,
) -> FebPatch:
    return FebPatch(
        target=target,
        mode=mode,
        attribute_name=attribute_name,
        value=value,
        reason="synthetic explicit patch",
        provenance=provenance,
        intent_impact=intent_impact,
    )


def bound_intent(**overrides: object) -> IntentContract:
    facts = (  # noqa: SIM905
        "engineering_question units material loads constraints contact "
        "analysis_step roi evaluation_quantities"
    ).split()
    values: dict[str, object] = {
        **{field: {"source": "synthetic", "value": field} for field in facts},
        "loads": ({"source": "synthetic", "value": "100 N"},),
        "constraints": ({"source": "synthetic", "value": "fixed"},),
        "condition_sources": {
            field: {"authoritative": True, "source": "synthetic"} for field in facts
        },
        "state": IntentState.GATHERING,
    }
    values.update(overrides)
    return IntentContract(**values)  # type: ignore[arg-type]


def authorized_context(
    tmp_path: Path,
    change: Mapping[str, JSONValue],
    suffix: str = "authorized",
) -> tuple[IntentStateAuthority, Proposal, ProposalAuthority, EvidenceStore]:
    intent = bound_intent(allowed_mesh_changes={"mesh": {"patches": (change,)}})
    workspace = ValidatedCaseWorkspace(
        tmp_path / f"tool-{suffix}",
        tmp_path / f"02_CAE-{suffix}",
    )
    case = workspace.create_case(f"case-{suffix}")
    store = EvidenceStore(case, intent)
    state_authority = transition_intent(store.issue_intent_snapshot())
    manager = ProposalAuthorityManager(state_authority)
    proposal = Proposal(
        proposal_id=f"proposal-{suffix}",
        proposal_class=ProposalClass.INTENT_PRESERVING,
        rationale="synthetic explicit patch",
        evidence_ids=(f"evidence-{suffix}",),
        changes={"mesh": {"patches": (change,)}},
        authorized=True,
    )
    proposal_authority = manager.issue(proposal)
    return state_authority, proposal, proposal_authority, store


def write_authorized(
    original: OriginalModel,
    patches: tuple[FebPatch, ...],
    destination: Path,
    attempt: Path,
    context: tuple[IntentStateAuthority, Proposal, ProposalAuthority, EvidenceStore],
) -> DerivedFebReceipt:
    state_authority, proposal, proposal_authority, _ = context
    return write_derived_feb(
        original,
        patches,
        destination,
        attempt,
        state_authority=state_authority,
        proposal=proposal,
        proposal_authority=proposal_authority,
    )


def test_patch_markers_cannot_authorize_a_derived_write(tmp_path: Path) -> None:
    source = tmp_path / "original.feb"
    attempt = tmp_path / "attempt"
    destination = attempt / "derived.feb"
    source.write_bytes(FEB)
    attempt.mkdir()

    with pytest.raises((TypeError, ValueError, EvidenceIntegrityError)):
        write_derived_feb(  # type: ignore[call-arg]
            OriginalModel.from_path(source),
            (patch(TIME, "TEXT", 8, provenance=(EVIDENCE,)),),
            destination,
            attempt,
        )
    assert not destination.exists()


def test_authorized_write_requires_exact_proposal_patch_binding(tmp_path: Path) -> None:
    source = tmp_path / "original.feb"
    attempt = tmp_path / "attempt"
    destination = attempt / "derived.feb"
    source.write_bytes(FEB)
    attempt.mkdir()
    original = OriginalModel.from_path(source)
    change: dict[str, JSONValue] = {"target": TIME, "mode": "TEXT", "value": 8}
    context = authorized_context(tmp_path, change)
    receipt = write_authorized(
        original,
        (patch(TIME, "TEXT", 8),),
        destination,
        attempt,
        context,
    )

    assert receipt.applied_patch_targets == (TIME,)
    assert b"<time_steps>8</time_steps>" in destination.read_bytes()


@pytest.mark.parametrize(
    "record",
    [
        patch(MODULE, "TEXT", 8),
        patch(TIME, "ATTRIBUTE", 8, attribute_name="type"),
        patch(TIME, "TEXT", 9),
        patch(TIME, "TEXT", 8, attribute_name="type"),
    ],
)
def test_authorized_write_rejects_any_patch_field_mismatch(
    tmp_path: Path, record: FebPatch
) -> None:
    source = tmp_path / "original.feb"
    attempt = tmp_path / "attempt"
    destination = attempt / "derived.feb"
    source.write_bytes(FEB)
    attempt.mkdir()
    change: dict[str, JSONValue] = {"target": TIME, "mode": "TEXT", "value": 8}
    context = authorized_context(tmp_path, change, "mismatch")

    with pytest.raises((ValueError, EvidenceIntegrityError)):
        write_authorized(
            OriginalModel.from_path(source),
            (record,),
            destination,
            attempt,
            context,
        )
    assert not destination.exists()


def test_forged_foreign_stale_and_mismatched_authorities_never_write(tmp_path: Path) -> None:
    source = tmp_path / "original.feb"
    source.write_bytes(FEB)
    change: dict[str, JSONValue] = {"target": TIME, "mode": "TEXT", "value": 8}

    local_context = authorized_context(tmp_path, change, "authority")
    state_authority, proposal, proposal_authority, store = local_context
    foreign_authority = authorized_context(tmp_path, change, "foreign")[2]

    cases: tuple[tuple[str, IntentStateAuthority, Proposal, object], ...] = (
        ("forged", state_authority, proposal, object.__new__(ProposalAuthority)),
        ("foreign", state_authority, proposal, foreign_authority),
    )
    for name, state, candidate, authority in cases:
        attempt = tmp_path / f"attempt-{name}"
        attempt.mkdir()
        destination = attempt / "derived.feb"
        with pytest.raises((TypeError, ValueError, EvidenceIntegrityError)):
            write_authorized(
                OriginalModel.from_path(source),
                (patch(TIME, "TEXT", 8),),
                destination,
                attempt,
                (state, candidate, cast(ProposalAuthority, authority), store),
            )
        assert not destination.exists()

    mismatched = replace(proposal, proposal_id="different-proposal")
    attempt = tmp_path / "attempt-mismatched"
    attempt.mkdir()
    destination = attempt / "derived.feb"
    with pytest.raises((TypeError, ValueError, EvidenceIntegrityError)):
        write_authorized(
            OriginalModel.from_path(source),
            (patch(TIME, "TEXT", 8),),
            destination,
            attempt,
            (state_authority, mismatched, proposal_authority, store),
        )
    assert not destination.exists()

    stale_context = authorized_context(tmp_path, change, "stale")
    stale_store = stale_context[3]
    payload = json.loads(stale_store.intent_path.read_text(encoding="utf-8"))
    payload["engineering_question"] = "tampered"
    stale_store.intent_path.write_text(json.dumps(payload), encoding="utf-8")
    attempt = tmp_path / "attempt-stale"
    attempt.mkdir()
    destination = attempt / "derived.feb"
    with pytest.raises((TypeError, ValueError, EvidenceIntegrityError)):
        write_authorized(
            OriginalModel.from_path(source),
            (patch(TIME, "TEXT", 8),),
            destination,
            attempt,
            stale_context,
        )
    assert not destination.exists()
    with pytest.raises(TypeError):
        copy(proposal_authority)


def test_writes_detached_text_derived_and_receipt(tmp_path: Path) -> None:
    source = tmp_path / "original.feb"
    attempt = tmp_path / "attempt"
    destination = attempt / "derived.feb"
    source.write_bytes(FEB)
    attempt.mkdir()
    original = OriginalModel.from_path(source)
    change: dict[str, JSONValue] = {"target": TIME, "mode": "TEXT", "value": 8}
    context = authorized_context(tmp_path, change, "detached")

    receipt = write_authorized(
        original,
        (patch(TIME, "TEXT", 8),),
        destination,
        attempt,
        context,
    )

    assert receipt.original_sha256 == original.sha256
    assert receipt.destination == destination
    assert receipt.applied_patch_targets == (TIME,)
    assert receipt.derived_sha256 != receipt.original_sha256
    assert b"<time_steps>8</time_steps>" in destination.read_bytes()
    assert source.read_bytes() == FEB
    assert original.verify()


def test_writes_attribute_and_preserves_diagnostic_metadata(tmp_path: Path) -> None:
    source = tmp_path / "original.feb"
    attempt = tmp_path / "attempt"
    source.write_bytes(FEB)
    attempt.mkdir()
    patch_record = patch(
        MODULE,
        "ATTRIBUTE",
        "hex8",
        attribute_name="type",
        provenance=(),
        intent_impact=IntentImpact.INTENT_CHANGING,
    )
    change = {
        "target": MODULE,
        "mode": "ATTRIBUTE",
        "attribute_name": "type",
        "value": "hex8",
    }
    context = authorized_context(tmp_path, change, "attribute")

    write_authorized(
        OriginalModel.from_path(source),
        (patch_record,),
        attempt / "x.feb",
        attempt,
        context,
    )

    assert b'type="hex8"' in (attempt / "x.feb").read_bytes()


def test_rejects_unsafe_destination_and_dtd(tmp_path: Path) -> None:
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    destination = attempt / "derived.feb"
    destination.write_bytes(b"existing")
    original = OriginalModel.from_bytes(FEB)
    change: dict[str, JSONValue] = {"target": TIME, "mode": "TEXT", "value": 2}
    context = authorized_context(tmp_path, change, "unsafe")

    with pytest.raises(FileExistsError):
        write_authorized(
            original,
            (patch(TIME, "TEXT", 2),),
            destination,
            attempt,
            context,
        )
    with pytest.raises(ValueError):
        write_authorized(
            original,
            (patch(TIME, "TEXT", 2),),
            tmp_path / "out.feb",
            attempt,
            context,
        )

    dtd = b'<!DOCTYPE febio_spec [<!ENTITY x "bad">]><febio_spec>&x;</febio_spec>'
    with pytest.raises(ValueError):
        write_authorized(
            original.from_bytes(dtd),
            (patch(TIME, "TEXT", 2),),
            attempt / "d.feb",
            attempt,
            context,
        )
    assert not (attempt / "d.feb").exists()
