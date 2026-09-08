from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

from febio_cae.cli.main import main


def test_normal_comparison_cli_uses_registered_results(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    # Dynamic import avoids giving the shared test module two mypy module names.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "application"))
    fixture = importlib.import_module("test_comparison").comparison_fixture
    _, created, _, spec = fixture(tmp_path)
    monkeypatch.setenv("FEBIO_CAE_STATE_DIR", str(tmp_path / "state"))
    path = tmp_path / "comparison.json"
    path.write_bytes(spec.to_bytes())
    try:
        code = main(
            [
                "compare",
                "run-baseline",
                "run-candidate",
                "--case-id",
                created.case_id,
                "--spec",
                str(path),
                "--json",
            ]
        )
    except SystemExit as error:
        assert isinstance(error.code, int)
        code = error.code
    assert code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "COMPARED" and result["case_id"] == created.case_id
    assert Path(result["record_path"]).is_file()
