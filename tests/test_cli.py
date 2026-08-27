from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run(
        [sys.executable, "-m", "febio_cae_harness", *arguments],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )


def test_module_version_command() -> None:
    completed = _run_cli("--version")

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "febio-cae 0.1.0\n"
    assert completed.stderr == ""


def test_inspect_feb_emits_structural_json_and_reference_evidence(tmp_path: Path) -> None:
    source = tmp_path / "sample.feb"
    source.write_text(
        '<febio version="4.0"><Material id="1" name="steel" />'
        '<Load material="1" /><Nodeset name="support">1 2</Nodeset></febio>',
        encoding="utf-8",
    )

    completed = _run_cli("inspect-feb", str(source))

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    payload = json.loads(completed.stdout)
    assert payload["root_tag"] == "febio"
    assert payload["tag_counts"] == {"febio": 1, "Material": 1, "Load": 1, "Nodeset": 1}
    assert payload["reference_closure"]["references"] == [
        {
            "attribute": "material",
            "path": "/febio/Load[1]",
            "resolved": True,
            "resolved_definition": {
                "attribute": "id",
                "identifier": "1",
                "kind": "material",
                "path": "/febio/Material[1]",
                "tag": "Material",
            },
            "tag": "Load",
            "target_kind": "material",
            "value": "1",
        },
        {
            "attribute": "#text",
            "path": "/febio/Nodeset[1]",
            "resolved": False,
            "resolved_definition": None,
            "tag": "Nodeset",
            "target_kind": "node",
            "value": "1",
        },
        {
            "attribute": "#text",
            "path": "/febio/Nodeset[1]",
            "resolved": False,
            "resolved_definition": None,
            "tag": "Nodeset",
            "target_kind": "node",
            "value": "2",
        },
    ]
    assert completed.stdout == json.dumps(payload, sort_keys=True) + "\n"


def test_inspect_step_emits_structural_json_and_explicit_unit_evidence(tmp_path: Path) -> None:
    source = tmp_path / "sample.step"
    source.write_text(
        "ISO-10303-21;\nHEADER;\n"
        "FILE_SCHEMA(('AUTOMOTIVE_DESIGN_CC2'));\nENDSEC;\nDATA;\n"
        "#1 = SI_UNIT(.MILLI., .METRE.);\nENDSEC;\nEND-ISO-10303-21;\n",
        encoding="utf-8",
    )

    completed = _run_cli("inspect-step", str(source))

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    payload = json.loads(completed.stdout)
    assert payload["entity_count"] == 1
    assert payload["entity_counts"] == {"SI_UNIT": 1}
    assert payload["schema_identifiers"] == ["AUTOMOTIVE_DESIGN_CC2"]
    assert payload["units"][0]["name"] == "milli metre"
    assert payload["units"][0]["provenance"] == {
        "source": "STEP",
        "location": "#1:SI_UNIT",
        "excerpt": "SI_UNIT(.MILLI., .METRE.)",
        "authoritative": True,
    }
    assert completed.stdout == json.dumps(payload, sort_keys=True) + "\n"


def test_inspect_missing_input_is_nonzero_with_concise_stderr(tmp_path: Path) -> None:
    completed = _run_cli("inspect-feb", str(tmp_path / "missing.feb"))

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert completed.stderr.startswith("febio-cae: ")
    assert "missing.feb" in completed.stderr
    assert "Traceback" not in completed.stderr


def test_inspect_invalid_feb_is_nonzero_with_concise_stderr(tmp_path: Path) -> None:
    source = tmp_path / "invalid.feb"
    source.write_text("not XML", encoding="utf-8")

    completed = _run_cli("inspect-feb", str(source))

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert completed.stderr.startswith("febio-cae: ")
    assert "invalid FEB XML" in completed.stderr
    assert "Traceback" not in completed.stderr


def test_inspect_invalid_step_is_nonzero_with_concise_stderr(tmp_path: Path) -> None:
    source = tmp_path / "invalid.step"
    source.write_bytes(b"\xff")

    completed = _run_cli("inspect-step", str(source))

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert completed.stderr.startswith("febio-cae: ")
    assert "not UTF-8/ASCII" in completed.stderr
    assert "Traceback" not in completed.stderr


def test_preflight_feb_emits_deterministic_ready_json(tmp_path: Path) -> None:
    source = tmp_path / "ready.feb"
    source.write_text(
        '<febio_spec version="4.0"><Material id="1" /><Load material="1" /></febio_spec>',
        encoding="utf-8",
    )

    completed = _run_cli("preflight-feb", str(source))

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    payload = json.loads(completed.stdout)
    assert payload == {
        "completeness": None,
        "diagnostics": [],
        "ready": True,
        "status": "READY",
    }
    assert completed.stdout == json.dumps(payload, sort_keys=True) + "\n"


def test_preflight_feb_uses_distinct_blocking_exit_codes(tmp_path: Path) -> None:
    cases = (
        (
            "invalid-root.feb",
            '<febio><Material id="1" /></febio>',
            2,
            "INVALID_FEB_ROOT",
        ),
        (
            "unresolved-reference.feb",
            '<febio_spec><Load material="9" /></febio_spec>',
            3,
            "MISSING_REFERENCE",
        ),
        (
            "duplicate-identifier.feb",
            '<febio_spec><Material id="1" /><Material id="1" /></febio_spec>',
            4,
            "DUPLICATE_IDENTIFIER",
        ),
    )

    exit_codes: list[int] = []
    for filename, contents, expected_code, expected_diagnostic in cases:
        source = tmp_path / filename
        source.write_text(contents, encoding="utf-8")

        completed = _run_cli("preflight-feb", str(source))

        exit_codes.append(completed.returncode)
        assert completed.returncode == expected_code
        assert completed.stderr == ""
        payload = json.loads(completed.stdout)
        assert payload["ready"] is False
        assert payload["status"] == "BLOCKED"
        assert [item["code"] for item in payload["diagnostics"]] == [expected_diagnostic]
    assert len(set(exit_codes)) == len(exit_codes)


def test_preflight_feb_parse_error_is_concise_stderr(tmp_path: Path) -> None:
    source = tmp_path / "invalid.feb"
    source.write_text("not XML", encoding="utf-8")

    completed = _run_cli("preflight-feb", str(source))

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert completed.stderr.startswith("febio-cae: ")
    assert "invalid FEB XML" in completed.stderr
    assert "Traceback" not in completed.stderr


def test_preflight_feb_missing_input_is_concise_stderr(tmp_path: Path) -> None:
    completed = _run_cli("preflight-feb", str(tmp_path / "missing.feb"))

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert completed.stderr.startswith("febio-cae: ")
    assert "missing.feb" in completed.stderr
    assert "Traceback" not in completed.stderr
