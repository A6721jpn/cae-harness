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
from febio_cae_harness.workspace import (
    AttemptWorkspace,
    ValidatedCaseWorkspace,
    WorkspaceBoundaryError,
)

FEB = b"""<?xml version="1.0" encoding="UTF-8"?>
<febio_spec version="4.0">
  <Control><time_steps>4</time_steps></Control>
  <Module type="solid"/>
  <Material><material id="1" name="synthetic"/></Material>
  <Mesh><Elements type="tet4"/></Mesh>
</febio_spec>
"""
TIME = "/febio_spec/Control[1]/time_steps[1]"
MODULE = "/febio_spec/Module[1]"
MESH_ELEMENTS = "/febio_spec/Mesh[1]/Elements[1]"
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
) -> tuple[IntentStateAuthority, Proposal, ProposalAuthority, EvidenceStore, AttemptWorkspace]:
    intent = bound_intent(allowed_mesh_changes={"mesh": {"patches": (change,)}})
    workspace = ValidatedCaseWorkspace(
        tmp_path / f"tool-{suffix}",
        tmp_path / f"02_CAE-{suffix}",
    )
    case = workspace.create_case(f"case-{suffix}")
    store = EvidenceStore(case, intent)
    attempt_id = f"attempt-{suffix}"
    store.record_attempt(attempt_id)
    attempt = AttemptWorkspace._from_manager(
        case,
        attempt_id,
        case.temporary_root / "attempts" / attempt_id,
    )
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
    return state_authority, proposal, proposal_authority, store, attempt


def write_authorized(
    original: OriginalModel,
    patches: tuple[FebPatch, ...],
    destination: Path,
    attempt: AttemptWorkspace,
    context: tuple[
        IntentStateAuthority,
        Proposal,
        ProposalAuthority,
        EvidenceStore,
        AttemptWorkspace,
    ],
) -> DerivedFebReceipt:
    state_authority, proposal, proposal_authority, _, issued_attempt = context
    assert attempt is issued_attempt
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
            attempt,  # type: ignore[arg-type]
        )
    assert not destination.exists()


def test_authorized_write_requires_exact_proposal_patch_binding(tmp_path: Path) -> None:
    source = tmp_path / "original.feb"
    source.write_bytes(FEB)
    original = OriginalModel.from_path(source)
    change: dict[str, JSONValue] = {
        "target": MESH_ELEMENTS,
        "mode": "ATTRIBUTE",
        "attribute_name": "type",
        "value": "tet10",
    }
    context = authorized_context(tmp_path, change)
    attempt = context[4]
    destination = attempt.root / "derived.feb"
    receipt = write_authorized(
        original,
        (patch(MESH_ELEMENTS, "ATTRIBUTE", "tet10", attribute_name="type"),),
        destination,
        attempt,
        context,
    )

    assert receipt.applied_patch_targets == (MESH_ELEMENTS,)
    assert b'type="tet10"' in destination.read_bytes()


@pytest.mark.parametrize("target", [TIME, MODULE])
def test_mesh_proposal_cannot_patch_non_mesh_sections(tmp_path: Path, target: str) -> None:
    source = tmp_path / f"original-{target.rsplit('/', 1)[-1]}.feb"
    source.write_bytes(FEB)
    change: dict[str, JSONValue] = {"target": target, "mode": "TEXT", "value": 8}
    context = authorized_context(tmp_path, change, f"non-mesh-{target.rsplit('/', 1)[-1]}")
    attempt = context[4]
    destination = attempt.root / "derived.feb"

    with pytest.raises(EvidenceIntegrityError, match="Mesh"):
        write_authorized(
            OriginalModel.from_path(source),
            (patch(target, "TEXT", 8),),
            destination,
            attempt,
            context,
        )
    assert not destination.exists()


def test_mesh_proposal_rejects_intent_changing_patch_marker(tmp_path: Path) -> None:
    source = tmp_path / "original-impact.feb"
    source.write_bytes(FEB)
    change: dict[str, JSONValue] = {
        "target": MODULE,
        "mode": "ATTRIBUTE",
        "attribute_name": "type",
        "value": "hex8",
    }
    context = authorized_context(tmp_path, change, "intent-impact")
    attempt = context[4]

    with pytest.raises(EvidenceIntegrityError, match="intent-preserving"):
        write_authorized(
            OriginalModel.from_path(source),
            (
                patch(
                    MODULE,
                    "ATTRIBUTE",
                    "hex8",
                    attribute_name="type",
                    intent_impact=IntentImpact.INTENT_CHANGING,
                ),
            ),
            attempt.root / "derived.feb",
            attempt,
            context,
        )


@pytest.mark.parametrize(
    "record",
    [
        patch(MODULE, "TEXT", 8),
        patch(MESH_ELEMENTS, "TEXT", "tet10"),
        patch(MESH_ELEMENTS, "ATTRIBUTE", "hex8", attribute_name="type"),
        patch(MESH_ELEMENTS, "ATTRIBUTE", "tet10", attribute_name="name"),
    ],
)
def test_authorized_write_rejects_any_patch_field_mismatch(
    tmp_path: Path, record: FebPatch
) -> None:
    source = tmp_path / "original.feb"
    source.write_bytes(FEB)
    change: dict[str, JSONValue] = {
        "target": MESH_ELEMENTS,
        "mode": "ATTRIBUTE",
        "attribute_name": "type",
        "value": "tet10",
    }
    context = authorized_context(tmp_path, change, "mismatch")
    attempt = context[4]
    destination = attempt.root / "derived.feb"

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
    change: dict[str, JSONValue] = {
        "target": MESH_ELEMENTS,
        "mode": "ATTRIBUTE",
        "attribute_name": "type",
        "value": "tet10",
    }

    local_context = authorized_context(tmp_path, change, "authority")
    state_authority, proposal, proposal_authority, store, local_attempt = local_context
    foreign_authority = authorized_context(tmp_path, change, "foreign")[2]

    cases: tuple[tuple[str, IntentStateAuthority, Proposal, object], ...] = (
        ("forged", state_authority, proposal, object.__new__(ProposalAuthority)),
        ("foreign", state_authority, proposal, foreign_authority),
    )
    for name, state, candidate, authority in cases:
        destination = local_attempt.root / f"derived-{name}.feb"
        with pytest.raises((TypeError, ValueError, EvidenceIntegrityError)):
            write_authorized(
                OriginalModel.from_path(source),
                (patch(MESH_ELEMENTS, "ATTRIBUTE", "tet10", attribute_name="type"),),
                destination,
                local_attempt,
                (state, candidate, cast(ProposalAuthority, authority), store, local_attempt),
            )
        assert not destination.exists()

    mismatched = replace(proposal, proposal_id="different-proposal")
    destination = local_attempt.root / "derived-mismatched.feb"
    with pytest.raises((TypeError, ValueError, EvidenceIntegrityError)):
        write_authorized(
            OriginalModel.from_path(source),
            (patch(MESH_ELEMENTS, "ATTRIBUTE", "tet10", attribute_name="type"),),
            destination,
            local_attempt,
            (state_authority, mismatched, proposal_authority, store, local_attempt),
        )
    assert not destination.exists()

    stale_context = authorized_context(tmp_path, change, "stale")
    stale_store = stale_context[3]
    payload = json.loads(stale_store.intent_path.read_text(encoding="utf-8"))
    payload["engineering_question"] = "tampered"
    stale_store.intent_path.write_text(json.dumps(payload), encoding="utf-8")
    stale_attempt = stale_context[4]
    destination = stale_attempt.root / "derived-stale.feb"
    with pytest.raises((TypeError, ValueError, EvidenceIntegrityError)):
        write_authorized(
            OriginalModel.from_path(source),
            (patch(MESH_ELEMENTS, "ATTRIBUTE", "tet10", attribute_name="type"),),
            destination,
            stale_attempt,
            stale_context,
        )
    assert not destination.exists()
    with pytest.raises(TypeError):
        copy(proposal_authority)


def test_derived_write_rejects_foreign_issued_and_raw_attempts_before_output(
    tmp_path: Path,
) -> None:
    source = tmp_path / "original.feb"
    source.write_bytes(FEB)
    change: dict[str, JSONValue] = {
        "target": MESH_ELEMENTS,
        "mode": "ATTRIBUTE",
        "attribute_name": "type",
        "value": "tet10",
    }
    state_authority, proposal, proposal_authority, store, _ = authorized_context(
        tmp_path, change, "attempt-binding"
    )
    local_case = store.case_workspace

    foreign_workspace = ValidatedCaseWorkspace(
        tmp_path / "tool-foreign-attempt",
        tmp_path / "02_CAE-foreign-attempt",
    )
    foreign_case = foreign_workspace.create_case(local_case.case_id)
    foreign_attempt = foreign_case.allocate_attempt("foreign")

    forged_attempt = object.__new__(AttemptWorkspace)
    object.__setattr__(forged_attempt, "case_id", local_case.case_id)
    object.__setattr__(forged_attempt, "attempt_id", "foreign")
    object.__setattr__(forged_attempt, "root", foreign_attempt.root)

    supplied_attempts: tuple[object, ...] = (
        foreign_attempt,
        foreign_attempt.root,
        forged_attempt,
    )
    for index, supplied_attempt in enumerate(supplied_attempts):
        destination = foreign_attempt.root / f"rejected-{index}.feb"
        with pytest.raises((TypeError, EvidenceIntegrityError, WorkspaceBoundaryError)):
            write_derived_feb(
                OriginalModel.from_path(source),
                (patch(MESH_ELEMENTS, "ATTRIBUTE", "tet10", attribute_name="type"),),
                destination,
                supplied_attempt,  # type: ignore[arg-type]
                state_authority=state_authority,
                proposal=proposal,
                proposal_authority=proposal_authority,
            )
        assert not destination.exists()


def test_writes_detached_mesh_derived_and_receipt(tmp_path: Path) -> None:
    source = tmp_path / "original.feb"
    source.write_bytes(FEB)
    original = OriginalModel.from_path(source)
    change: dict[str, JSONValue] = {
        "target": MESH_ELEMENTS,
        "mode": "ATTRIBUTE",
        "attribute_name": "type",
        "value": "tet10",
    }
    context = authorized_context(tmp_path, change, "detached")
    attempt = context[4]
    destination = attempt.root / "derived.feb"

    receipt = write_authorized(
        original,
        (patch(MESH_ELEMENTS, "ATTRIBUTE", "tet10", attribute_name="type"),),
        destination,
        attempt,
        context,
    )

    assert receipt.original_sha256 == original.sha256
    assert receipt.destination == destination
    assert receipt.applied_patch_targets == (MESH_ELEMENTS,)
    assert receipt.derived_sha256 != receipt.original_sha256
    assert b'type="tet10"' in destination.read_bytes()
    assert source.read_bytes() == FEB
    assert original.verify()


def test_writes_attribute_and_preserves_diagnostic_metadata(tmp_path: Path) -> None:
    source = tmp_path / "original.feb"
    source.write_bytes(FEB)
    patch_record = patch(
        MESH_ELEMENTS,
        "ATTRIBUTE",
        "tet10",
        attribute_name="type",
        provenance=(),
    )
    change = {
        "target": MESH_ELEMENTS,
        "mode": "ATTRIBUTE",
        "attribute_name": "type",
        "value": "tet10",
    }
    context = authorized_context(tmp_path, change, "attribute")
    attempt = context[4]

    write_authorized(
        OriginalModel.from_path(source),
        (patch_record,),
        attempt.root / "x.feb",
        attempt,
        context,
    )

    assert b'type="tet10"' in (attempt.root / "x.feb").read_bytes()


def test_rejects_unsafe_destination_and_dtd(tmp_path: Path) -> None:
    original = OriginalModel.from_bytes(FEB)
    change: dict[str, JSONValue] = {
        "target": MESH_ELEMENTS,
        "mode": "ATTRIBUTE",
        "attribute_name": "type",
        "value": "tet10",
    }
    context = authorized_context(tmp_path, change, "unsafe")
    attempt = context[4]
    destination = attempt.root / "derived.feb"
    destination.write_bytes(b"existing")

    with pytest.raises(FileExistsError):
        write_authorized(
            original,
            (patch(MESH_ELEMENTS, "ATTRIBUTE", "tet10", attribute_name="type"),),
            destination,
            attempt,
            context,
        )
    with pytest.raises(ValueError):
        write_authorized(
            original,
            (patch(MESH_ELEMENTS, "ATTRIBUTE", "tet10", attribute_name="type"),),
            tmp_path / "out.feb",
            attempt,
            context,
        )

    dtd = b'<!DOCTYPE febio_spec [<!ENTITY x "bad">]><febio_spec>&x;</febio_spec>'
    with pytest.raises(ValueError):
        write_authorized(
            original.from_bytes(dtd),
            (patch(MESH_ELEMENTS, "ATTRIBUTE", "tet10", attribute_name="type"),),
            attempt.root / "d.feb",
            attempt,
            context,
        )
    assert not (attempt.root / "d.feb").exists()
