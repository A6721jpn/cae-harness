"""Synthetic CLI routing only; never starts native software."""

from __future__ import annotations

import json
from typing import Any

import pytest

from febio_cae.application.service import RegisteredCaseService
from febio_cae.cli.main import main
from febio_cae.domain.ports import PortError, PortErrorCategory


@pytest.mark.parametrize("fail", [False, True])
def test_demo_cli_routes_registered_ids_and_reports_cleanup(
    tmp_path: Any, monkeypatch: Any, capsys: Any, fail: bool
) -> None:
    calls: list[Any] = []

    def run(self: Any, case_id: str, revision_id: str, *, executable: str, preflight: bool) -> Any:
        calls.append((case_id, revision_id, executable, preflight))
        if fail:
            raise PortError(PortErrorCategory.EXECUTION, "synthetic failure")
        return {"status": "NEEDS_PREVIEW", "run_status": "SUCCEEDED"}

    def cleanup(self: Any) -> int:
        calls.append("cleanup")
        return 0

    monkeypatch.setattr(RegisteredCaseService, "run_demo", run, raising=False)
    monkeypatch.setattr(RegisteredCaseService, "_retry_pending_cleanup", cleanup)
    code = main(
        [
            "case",
            "--state-dir",
            str(tmp_path / "state"),
            "run-demo",
            "case-known",
            "--revision-id",
            "revision-known",
            "--solver",
            "solver.exe",
            "--json",
        ]
    )
    assert calls[0] == ("case-known", "revision-known", "solver.exe", False)
    payload = json.loads(capsys.readouterr().out)
    if fail:
        assert code != 0
        assert calls[1:] == ["cleanup"]
        assert payload["pending_cleanup"] == 0
    else:
        assert code == 0
        assert payload["status"] == "NEEDS_PREVIEW"


@pytest.mark.parametrize(
    ("status", "quality", "expected"),
    [("NEEDS_REVIEW", "FAIL", 6), ("NEEDS_PREVIEW", "PASS", 0), ("PREFLIGHT_PASSED", None, 0)],
)
def test_demo_completed_quality_exit_contract(
    tmp_path: Any,
    monkeypatch: Any,
    capsys: Any,
    status: str,
    quality: str | None,
    expected: int,
) -> None:
    payload = {
        "status": status,
        "run_status": "SUCCEEDED",
        "quality": {"overall_status": quality},
    }

    def run(self: Any, case_id: str, revision_id: str, *, executable: str, preflight: bool) -> Any:
        return payload

    monkeypatch.setattr(RegisteredCaseService, "run_demo", run)
    code = main(
        [
            "case",
            "--state-dir",
            str(tmp_path / "state"),
            "run-demo",
            "case-known",
            "--revision-id",
            "revision-known",
            "--solver",
            "solver.exe",
            "--json",
            *(["--preflight"] if status == "PREFLIGHT_PASSED" else []),
        ]
    )
    assert json.loads(capsys.readouterr().out) == payload
    assert code == expected
