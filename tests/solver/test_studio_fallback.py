from __future__ import annotations

import hashlib
from pathlib import Path
from typing import cast

import pytest

from febio_cae_harness.contracts import IntentContract, IntentState
from febio_cae_harness.evidence import EvidenceIntegrityError, EvidenceStore
from febio_cae_harness.model import inspect_step
from febio_cae_harness.solver import studio_fallback as studio_module
from febio_cae_harness.solver.studio_fallback import (
    accept_step_studio_output,
    issue_step_studio_handoff,
)
from febio_cae_harness.workspace import ValidatedCaseWorkspace

STEP = b"""ISO-10303-21;
HEADER;
FILE_SCHEMA(('AUTOMOTIVE_DESIGN_CC2'));
ENDSEC;
DATA;
#10 = ( NAMED_UNIT(*) SI_UNIT(.MILLI., .METRE.) LENGTH_UNIT() );
ENDSEC;
END-ISO-10303-21;
"""
MESH_ONLY_FEB = b"""<?xml version="1.0" encoding="UTF-8"?>
<febio_spec version="4.0">
  <Module type="solid"/>
  <Mesh>
    <Nodes name="Object01">
      <node id="1">0,0,0</node><node id="2">1,0,0</node>
      <node id="3">0,1,0</node><node id="4">0,0,1</node>
      <node id="5">0.5,0,0</node><node id="6">0.5,0.5,0</node>
      <node id="7">0,0.5,0</node><node id="8">0,0,0.5</node>
      <node id="9">0.5,0,0.5</node><node id="10">0,0.5,0.5</node>
    </Nodes>
    <Elements type="tet10" name="Part1"><elem id="1">1,2,3,4,5,6,7,8,9,10</elem></Elements>
  </Mesh>
</febio_spec>
"""
FEB = MESH_ONLY_FEB.replace(
    b"  <Mesh>",
    b"""  <Material><material id="1" name="synthetic" type="neo-Hookean"/></Material>
  <Mesh>""",
).replace(
    b"</febio_spec>",
    b"""  <Step><step id="1" name="synthetic-step">
    <Control><time_steps>1</time_steps><step_size>1</step_size></Control>
    <Boundary><bc name="synthetic-constraint"/></Boundary>
    <Loads><nodal_load name="synthetic-load"/></Loads>
  </step></Step>
</febio_spec>""",
)
PNG_BEFORE = b"\x89PNG\r\n\x1a\nsynthetic-before"
PNG_AFTER = b"\x89PNG\r\n\x1a\nsynthetic-after"


def _store(tmp_path: Path, *, state: IntentState = IntentState.BOUND) -> EvidenceStore:
    inspection = inspect_step(STEP)
    workspace = ValidatedCaseWorkspace(tmp_path / "tool", tmp_path / "02_CAE")
    case = workspace.create_case("step-case")
    return EvidenceStore(
        case,
        IntentContract(
            allowed_mesh_changes={
                "mesh": {
                    "step_meshing": {
                        "step_sha256": inspection.sha256,
                        "element_family": "tet10",
                        "length_unit": "mm",
                        "target_size": 1.25,
                        "quality_criteria": {
                            "min_jacobian": 0.1,
                            "max_aspect_ratio": 4.0,
                        },
                    }
                }
            },
            state=state,
        ),
    )


def _evidence(
    handoff: object,
    executable: Path,
    *,
    output_feb: bytes = FEB,
) -> dict[str, object]:
    request = handoff.to_dict()  # type: ignore[attr-defined]
    return {
        "schema": "studio-fallback-evidence-v1",
        "request_id": request["request_id"],
        "operation": "STEP_IMPORT_MESH_FEB",
        "official_computer_use": True,
        "runtime": {
            "product": "FEBio Studio",
            "version": "synthetic-3.1",
            "executable_path": str(executable.resolve()),
            "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        },
        "input_sha256": request["step_sha256"],
        "output_sha256": hashlib.sha256(output_feb).hexdigest(),
        "before_sha256": hashlib.sha256(PNG_BEFORE).hexdigest(),
        "after_sha256": hashlib.sha256(PNG_AFTER).hexdigest(),
        "actions": [
            {"sequence": 1, "action": "opened exact STEP input"},
            {"sequence": 2, "action": "generated requested tet10 mesh and saved FEB"},
        ],
    }


def test_issues_current_bound_step_handoff_without_claiming_studio_success(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    snapshot = store.issue_intent_snapshot()

    handoff = issue_step_studio_handoff(
        inspect_step(STEP, source_name="part.step"),
        snapshot,
        input_name="part.step",
        attempt_id="studio-step-1",
    )

    payload = handoff.to_dict()
    assert payload == {
        "schema": "studio-fallback-request-v1",
        "request_id": payload["request_id"],
        "case_id": "step-case",
        "attempt_id": "studio-step-1",
        "intent_sha256": snapshot.intent_sha256,
        "operation": "STEP_IMPORT_MESH_FEB",
        "input_name": "part.step",
        "step_sha256": hashlib.sha256(STEP).hexdigest(),
        "plan": {
            "element_family": "tet10",
            "length_unit": "mm",
            "target_size": 1.25,
            "quality_criteria": {
                "max_aspect_ratio": 4.0,
                "min_jacobian": 0.1,
            },
        },
        "expected_output": "studio-output.feb",
        "required_evidence": [
            "before.png",
            "after.png",
            "actions.json",
            "studio-runtime-identity",
        ],
        "status": "PENDING_EXTERNAL_STUDIO",
    }
    assert len(cast(str, payload["request_id"])) == 64
    assert "success" not in str(payload).casefold()


def test_accepts_only_exact_official_evidence_and_reinspected_tet10_feb(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    handoff = issue_step_studio_handoff(
        inspect_step(STEP),
        store.issue_intent_snapshot(),
        input_name="part.step",
        attempt_id="studio-step-1",
    )
    executable = tmp_path / "FEBioStudio.exe"
    executable.write_bytes(b"synthetic-studio-runtime")

    receipt = accept_step_studio_output(
        handoff,
        output_feb=FEB,
        before_png=PNG_BEFORE,
        after_png=PNG_AFTER,
        action_evidence=_evidence(handoff, executable),
    )

    assert receipt.to_dict() == {
        "schema": "studio-fallback-receipt-v1",
        "request_id": handoff.request_id,
        "case_id": "step-case",
        "attempt_id": "studio-step-1",
        "operation": "STEP_IMPORT_MESH_FEB",
        "intent_sha256": handoff.intent_sha256,
        "output_sha256": hashlib.sha256(FEB).hexdigest(),
        "before_sha256": hashlib.sha256(PNG_BEFORE).hexdigest(),
        "after_sha256": hashlib.sha256(PNG_AFTER).hexdigest(),
        "runtime_executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "headless_ready": True,
        "official_computer_use": True,
    }


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda evidence: evidence.update(official_computer_use=False), "official Computer Use"),
        (lambda evidence: evidence.update(output_sha256="0" * 64), "output"),
        (
            lambda evidence: evidence["runtime"].update(executable_sha256="0" * 64),
            "runtime",
        ),
        (lambda evidence: evidence["actions"].clear(), "actions"),
    ],
)
def test_rejects_unbound_studio_claims_without_issuing_headless_receipt(
    tmp_path: Path,
    mutation: object,
    message: str,
) -> None:
    store = _store(tmp_path)
    handoff = issue_step_studio_handoff(
        inspect_step(STEP),
        store.issue_intent_snapshot(),
        input_name="part.step",
        attempt_id="studio-step-1",
    )
    executable = tmp_path / "FEBioStudio.exe"
    executable.write_bytes(b"synthetic-studio-runtime")
    evidence = _evidence(handoff, executable)
    mutation(evidence)  # type: ignore[operator]

    with pytest.raises(EvidenceIntegrityError, match=message):
        accept_step_studio_output(
            handoff,
            output_feb=FEB,
            before_png=PNG_BEFORE,
            after_png=PNG_AFTER,
            action_evidence=evidence,
        )


def test_rejects_blocked_or_stale_intent_and_wrong_mesh_family(tmp_path: Path) -> None:
    blocked = _store(tmp_path / "blocked", state=IntentState.ASK_AND_BLOCK)
    with pytest.raises(EvidenceIntegrityError, match="BOUND"):
        issue_step_studio_handoff(
            inspect_step(STEP),
            blocked.issue_intent_snapshot(),
            input_name="part.step",
            attempt_id="studio-step-1",
        )

    store = _store(tmp_path / "stale")
    handoff = issue_step_studio_handoff(
        inspect_step(STEP),
        store.issue_intent_snapshot(),
        input_name="part.step",
        attempt_id="studio-step-1",
    )
    executable = tmp_path / "stale" / "FEBioStudio.exe"
    executable.write_bytes(b"synthetic-studio-runtime")
    evidence = _evidence(handoff, executable)
    store.revise_intent(IntentContract())
    with pytest.raises(EvidenceIntegrityError, match="intent|snapshot"):
        accept_step_studio_output(
            handoff,
            output_feb=FEB,
            before_png=PNG_BEFORE,
            after_png=PNG_AFTER,
            action_evidence=evidence,
        )

    fresh = _store(tmp_path / "wrong-family")
    fresh_handoff = issue_step_studio_handoff(
        inspect_step(STEP),
        fresh.issue_intent_snapshot(),
        input_name="part.step",
        attempt_id="studio-step-1",
    )
    fresh_executable = tmp_path / "wrong-family" / "FEBioStudio.exe"
    fresh_executable.write_bytes(b"synthetic-studio-runtime")
    wrong_feb = FEB.replace(b'type="tet10"', b'type="tet4"')
    wrong_evidence = _evidence(fresh_handoff, fresh_executable)
    wrong_evidence["output_sha256"] = hashlib.sha256(wrong_feb).hexdigest()
    with pytest.raises(EvidenceIntegrityError, match="element family"):
        accept_step_studio_output(
            fresh_handoff,
            output_feb=wrong_feb,
            before_png=PNG_BEFORE,
            after_png=PNG_AFTER,
            action_evidence=wrong_evidence,
        )


def test_rejects_mesh_only_feb_without_required_model_sections(tmp_path: Path) -> None:
    store = _store(tmp_path)
    handoff = issue_step_studio_handoff(
        inspect_step(STEP),
        store.issue_intent_snapshot(),
        input_name="part.step",
        attempt_id="studio-step-1",
    )
    executable = tmp_path / "FEBioStudio.exe"
    executable.write_bytes(b"synthetic-studio-runtime")

    with pytest.raises(EvidenceIntegrityError, match="model sections"):
        accept_step_studio_output(
            handoff,
            output_feb=MESH_ONLY_FEB,
            before_png=PNG_BEFORE,
            after_png=PNG_AFTER,
            action_evidence=_evidence(
                handoff,
                executable,
                output_feb=MESH_ONLY_FEB,
            ),
        )


def test_restores_only_an_exact_persisted_handoff_projection(tmp_path: Path) -> None:
    store = _store(tmp_path)
    snapshot = store.issue_intent_snapshot()
    step = inspect_step(STEP, source_name="part.step")
    issued = issue_step_studio_handoff(
        step,
        snapshot,
        input_name="part.step",
        attempt_id="studio-step-1",
    )
    persisted = issued.to_dict()

    restored = studio_module.restore_step_studio_handoff(persisted, step, snapshot)

    assert restored is not issued
    assert restored.to_dict() == persisted

    for field, value in (
        ("request_id", "0" * 64),
        ("input_name", "other.step"),
        ("expected_output", "other.feb"),
    ):
        tampered = dict(persisted)
        tampered[field] = value
        with pytest.raises(EvidenceIntegrityError, match="persisted|request"):
            studio_module.restore_step_studio_handoff(tampered, step, snapshot)
