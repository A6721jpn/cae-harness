from __future__ import annotations

import json
from pathlib import Path

from febio_cae.cli.main import main


def test_case_cli_create_inspect_and_spec_use_registered_state(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("FEBIO_CAE_STATE_DIR", str(tmp_path / "state"))
    cad_path = tmp_path / "source.step"
    cad_path.write_bytes(b"cli-step")
    case_root = tmp_path / "case-root"

    assert main(
        [
            "case",
            "create",
            "--case-root",
            str(case_root),
            "--cad",
            str(cad_path),
            "--json",
        ]
    ) == 0
    created = json.loads(capsys.readouterr().out)
    case_id = created["case_id"]

    assert main(["case", "inspect", case_id, "--json"]) == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["case_id"] == case_id
    assert inspected["status"] in {"REGISTERED", "UNSUPPORTED_ENVIRONMENT"}

    spec_file = tmp_path / "spec.json"
    spec_file.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "input_intent": "explicit",
                "values": {
                    "schema_version": "1",
                    "geometry": None,
                    "material": None,
                    "support": None,
                    "rigid_tool": None,
                    "motion": None,
                    "contact": None,
                    "mesh_policy": None,
                    "solver_policy": None,
                    "outputs": None,
                    "quality_policy": None,
                    "budget": None,
                },
                "source_declarations": [],
                "evidence": [],
            }
        ),
        encoding="utf-8",
    )
    assert (
        main(
            [
                "case",
                "spec",
                case_id,
                "--file",
                str(spec_file),
                "--expected-generation",
                "0",
                "--json",
            ]
        )
        == 0
    )
    specified = json.loads(capsys.readouterr().out)
    assert specified["case_id"] == case_id
    assert specified["status"] == "UPDATED"
    assert specified["draft"]["generation"] == 1


def test_case_cli_validate_is_honest_about_unavailable_native_capabilities(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("FEBIO_CAE_STATE_DIR", str(tmp_path / "state"))
    cad_path = tmp_path / "source.step"
    cad_path.write_bytes(b"cli-step")
    assert (
        main(
            [
                "case",
                "create",
                "--case-root",
                str(tmp_path / "case"),
                "--cad",
                str(cad_path),
                "--json",
            ]
        )
        == 0
    )
    case_id = json.loads(capsys.readouterr().out)["case_id"]

    assert main(["case", "validate", case_id, "--json"]) == 4
    validated = json.loads(capsys.readouterr().out)
    assert validated["status"] == "UNSUPPORTED_ENVIRONMENT"
    assert validated["revision_id"] is None
