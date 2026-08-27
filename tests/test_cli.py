from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from febio_cae_harness import cli as cli_module
from febio_cae_harness.solver.runtime import FebioRuntimeDiagnostic, RuntimeProbeError

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


def test_probe_febio_emits_identity_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    executable = Path("C:/synthetic/febio.exe")
    diagnostic = FebioRuntimeDiagnostic(executable, "a" * 64, 123, "4.2.0")
    monkeypatch.setattr(cli_module, "probe_febio", lambda path: diagnostic)

    assert cli_module.main(["probe-febio", str(executable)]) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == diagnostic.to_dict()
    assert "official" not in captured.out
    assert "signed" not in captured.out
    assert "success" not in captured.out


def test_probe_febio_failure_is_concise_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail(path: Path) -> FebioRuntimeDiagnostic:
        raise RuntimeProbeError("synthetic probe failure")

    monkeypatch.setattr(cli_module, "probe_febio", fail)

    assert cli_module.main(["probe-febio", "missing-febio.exe"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "febio-cae: synthetic probe failure\n"


def test_run_febio_missing_executable_is_genuine_exit_one(tmp_path: Path) -> None:
    attempt_root = tmp_path / "attempt"
    attempt_root.mkdir()
    input_path = attempt_root / "model.feb"
    input_path.write_text("synthetic completed FEB", encoding="utf-8")

    completed = _run_cli(
        "run-febio",
        "--executable",
        str(tmp_path / "missing-febio.exe"),
        "--input",
        str(input_path),
        "--attempt-root",
        str(attempt_root),
        "--case-id",
        "case-a",
        "--intent-id",
        "intent-a",
        "--attempt-id",
        "attempt-a",
    )

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert completed.stderr.startswith("febio-cae: ")
    assert "does not exist" in completed.stderr
    assert "Traceback" not in completed.stderr


def test_run_febio_reports_fbs_unverified_as_exit_five(tmp_path: Path) -> None:
    attempt_root = tmp_path / "attempt"
    attempt_root.mkdir()
    input_path = attempt_root / "model.feb"
    input_path.write_text("synthetic completed FEB", encoding="utf-8")
    probe_script = tmp_path / "probe_runtime.py"
    probe_script.write_text(
        "import sys\n"
        "print('version 4.12.0', flush=True)\n"
        "assert sys.stdin.readline().strip() == 'quit'\n",
        encoding="utf-8",
    )
    log = "time step 1\ntime = 1.0\nnormal termination\n"
    solver_code = (
        "import os; from pathlib import Path; "
        "Path(os.environ['FEBIO_CAE_HARNESS_LOG']).write_text(" + repr(log) + "); "
        "Path(os.environ['FEBIO_CAE_HARNESS_XPLT']).write_bytes(b'synthetic xplt')"
    )

    completed = _run_cli(
        "run-febio",
        "--executable",
        sys.executable,
        "--input",
        str(input_path),
        "--attempt-root",
        str(attempt_root),
        "--case-id",
        "case-a",
        "--intent-id",
        "intent-a",
        "--attempt-id",
        "attempt-a",
        "--expected-steps",
        "1",
        "--expected-final-time",
        "1.0",
        "--timeout-seconds",
        "5",
        "--probe-argument=" + str(probe_script),
        "--argument=-c",
        "--argument=" + solver_code,
    )

    assert completed.returncode == 5
    assert completed.stderr == ""
    payload = json.loads(completed.stdout)
    assert payload["classification"] == "FBS_UNVERIFIED"
    assert payload["official_fbs"] is False
    assert payload["success"] is False
    assert payload["state"] == "NORMAL_EXIT"
    assert payload["return_code"] == 0
    assert payload["runtime"]["version"] == "4.12.0"
