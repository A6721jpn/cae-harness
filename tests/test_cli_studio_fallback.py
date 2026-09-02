from __future__ import annotations

import hashlib
import io
import json
import sys
from pathlib import Path

import pytest

from febio_cae_harness import cli as cli_module
from febio_cae_harness.cli_context import CaseContextService, dump_root_capability
from febio_cae_harness.contracts import IntentContract, IntentState
from febio_cae_harness.evidence import EvidenceIntegrityError

STEP = b"""ISO-10303-21;
HEADER;
FILE_SCHEMA(('AUTOMOTIVE_DESIGN_CC2'));
ENDSEC;
DATA;
#10 = ( NAMED_UNIT(*) SI_UNIT(.MILLI., .METRE.) LENGTH_UNIT() );
ENDSEC;
END-ISO-10303-21;
"""
FEB = b"""<?xml version="1.0" encoding="UTF-8"?>
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
PNG_BEFORE = b"\x89PNG\r\n\x1a\nsynthetic-before"
PNG_AFTER = b"\x89PNG\r\n\x1a\nsynthetic-after"


def _intent() -> IntentContract:
    step_sha256 = hashlib.sha256(STEP).hexdigest()
    physical = (
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
    return IntentContract(
        engineering_question="What is the synthetic response?",
        units={"length": "mm"},
        material={"name": "synthetic-material"},
        loads=({"name": "synthetic-load"},),
        constraints=({"name": "synthetic-constraint"},),
        contact={"mode": "synthetic-contact"},
        analysis_step={"name": "synthetic-step"},
        roi=({"name": "synthetic-roi"},),
        evaluation_quantities=({"name": "synthetic-output"},),
        allowed_mesh_changes={
            "mesh": {
                "step_meshing": {
                    "step_sha256": step_sha256,
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
        condition_sources={
            name: {"authoritative": True, "current": True, "source": "synthetic-user"}
            for name in physical
        },
        state=IntentState.BOUND,
    )


def _case(tmp_path: Path) -> tuple[CaseContextService, Path, object]:
    tool_root = tmp_path / "tool"
    tool_root.mkdir()
    service = CaseContextService._for_tests(
        registry_root=tmp_path / "registry",
        tool_root=tool_root,
    )
    cae_root = tmp_path / "02_CAE"
    cae_root.mkdir()
    source = tmp_path / "part.step"
    source.write_bytes(STEP)
    registered = service.register_root(cae_root)
    service.create_case(
        root_capability=registered["capability"],
        case_id="step-case",
        sources=(source,),
        intent=_intent(),
    )
    return service, cae_root, registered["capability"]


def _stdin(monkeypatch: pytest.MonkeyPatch, capability: object) -> None:
    stream = io.TextIOWrapper(io.BytesIO(dump_root_capability(capability)), encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", stream)


def test_begin_step_studio_fallback_persists_exact_pending_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service, cae_root, capability = _case(tmp_path)
    monkeypatch.setattr(cli_module, "_case_service", lambda: service)
    _stdin(monkeypatch, capability)

    assert (
        cli_module.main(
            [
                "begin-step-studio-fallback",
                "--case-id",
                "step-case",
                "--capability-stdin",
                "--input-name",
                "part.step",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert payload["ok"] is True
    assert payload["command"] == "begin-step-studio-fallback"
    assert payload["status"] == "PENDING_EXTERNAL_STUDIO"
    assert payload["input"] == {
        "name": "part.step",
        "sha256": hashlib.sha256(STEP).hexdigest(),
    }
    attempt_id = payload["attempt"]["attempt_id"]
    request_id = payload["attempt"]["request_id"]
    assert attempt_id.startswith("studio-step-")
    assert len(request_id) == 64
    attempt = cae_root / "step-case" / "90_Temporary" / "attempts" / attempt_id
    request = json.loads((attempt / "studio-request.json").read_text(encoding="utf-8"))
    record = json.loads((attempt / "ATTEMPT.json").read_text(encoding="utf-8"))
    assert request["request_id"] == request_id
    assert request["input_name"] == "part.step"
    assert record["payload"] == {
        "status": "studio_fallback_pending",
        "studio_fallback": request,
    }
    assert payload["attempt"]["required_files"] == {
        "action_evidence": "actions.json",
        "after_screenshot": "after.png",
        "before_screenshot": "before.png",
        "output_feb": "studio-output.feb",
    }
    assert str(tmp_path) not in captured.out


def test_accept_step_studio_fallback_records_evidence_and_headless_return(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service, cae_root, capability = _case(tmp_path)
    monkeypatch.setattr(cli_module, "_case_service", lambda: service)
    _stdin(monkeypatch, capability)
    assert (
        cli_module.main(
            [
                "begin-step-studio-fallback",
                "--case-id",
                "step-case",
                "--capability-stdin",
                "--input-name",
                "part.step",
            ]
        )
        == 0
    )
    begun = json.loads(capsys.readouterr().out)
    attempt_id = begun["attempt"]["attempt_id"]
    request_id = begun["attempt"]["request_id"]
    attempt = cae_root / "step-case" / "90_Temporary" / "attempts" / attempt_id
    executable = tmp_path / "FEBioStudio.exe"
    executable.write_bytes(b"synthetic-studio-runtime")
    evidence = {
        "schema": "studio-fallback-evidence-v1",
        "request_id": request_id,
        "operation": "STEP_IMPORT_MESH_FEB",
        "official_computer_use": True,
        "runtime": {
            "product": "FEBio Studio",
            "version": "synthetic-3.1",
            "executable_path": str(executable.resolve()),
            "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        },
        "input_sha256": hashlib.sha256(STEP).hexdigest(),
        "output_sha256": hashlib.sha256(FEB).hexdigest(),
        "before_sha256": hashlib.sha256(PNG_BEFORE).hexdigest(),
        "after_sha256": hashlib.sha256(PNG_AFTER).hexdigest(),
        "actions": [
            {"sequence": 1, "action": "opened exact STEP input"},
            {"sequence": 2, "action": "generated requested tet10 mesh and saved FEB"},
        ],
    }
    (attempt / "studio-output.feb").write_bytes(FEB)
    (attempt / "before.png").write_bytes(PNG_BEFORE)
    (attempt / "after.png").write_bytes(PNG_AFTER)
    (attempt / "actions.json").write_bytes(
        (
            json.dumps(evidence, allow_nan=False, separators=(",", ":"), sort_keys=True) + "\n"
        ).encode("utf-8")
    )
    _stdin(monkeypatch, capability)

    assert (
        cli_module.main(
            [
                "accept-step-studio-fallback",
                "--case-id",
                "step-case",
                "--capability-stdin",
                "--attempt-id",
                attempt_id,
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert payload["ok"] is True
    assert payload["command"] == "accept-step-studio-fallback"
    assert payload["status"] == "HEADLESS_READY"
    assert payload["attempt"] == {
        "attempt_id": attempt_id,
        "request_id": request_id,
        "output_feb": "studio-output.feb",
        "output_sha256": hashlib.sha256(FEB).hexdigest(),
    }
    receipt = json.loads((attempt / "studio-receipt.json").read_text(encoding="utf-8"))
    assert receipt["headless_ready"] is True
    assert receipt["official_computer_use"] is True
    manifest = json.loads(
        (cae_root / "step-case" / "CASE_MANIFEST.json").read_text(encoding="utf-8")
    )
    artifact_names = {Path(item["path"]).name for item in manifest["artifacts"]}
    assert {
        "actions.json",
        "after.png",
        "before.png",
        "studio-output.feb",
        "studio-receipt.json",
        "studio-request.json",
    }.issubset(artifact_names)
    events = [
        json.loads(line)
        for line in (cae_root / "step-case" / "90_Temporary" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    completed = [item for item in events if item["event_type"] == "studio_fallback_completed"]
    assert len(completed) == 1
    assert completed[0]["payload"]["receipt"] == receipt
    assert str(tmp_path) not in captured.out

    _stdin(monkeypatch, capability)
    assert (
        cli_module.main(
            [
                "accept-step-studio-fallback",
                "--case-id",
                "step-case",
                "--capability-stdin",
                "--attempt-id",
                attempt_id,
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "HEADLESS_READY"
    repeated_events = [
        json.loads(line)
        for line in (cae_root / "step-case" / "90_Temporary" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert (
        len([item for item in repeated_events if item["event_type"] == "studio_fallback_completed"])
        == 1
    )

    opened = service._open_context(capability, "step-case")
    completion_payload = completed[0]["payload"]
    with pytest.raises(EvidenceIntegrityError, match="dedicated API"):
        opened.store.append_event("studio_fallback_completed", completion_payload)
    stale_snapshot = opened.store.issue_intent_snapshot()
    opened.store.append_event("synthetic_intervening_evidence", {"current": True})
    before_rejected_record = (cae_root / "step-case" / "90_Temporary" / "events.jsonl").read_bytes()
    with pytest.raises(EvidenceIntegrityError, match="stale"):
        opened.store.record_studio_fallback_completion(
            stale_snapshot,
            attempt_id,
            completion_payload,
        )
    assert (
        cae_root / "step-case" / "90_Temporary" / "events.jsonl"
    ).read_bytes() == before_rejected_record
