"""Public preparation boundary, using only isolated synthetic dependencies."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from febio_cae.cli.main import main


def test_public_prepare_reports_invalid_request_without_native_start(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    request = tmp_path / "request.json"
    request.write_text("{}", encoding="utf-8")
    assert (
        main(
            [
                "case",
                "--state-dir",
                str(tmp_path / "state"),
                "prepare-planar",
                "missing",
                "--file",
                str(request),
                "--expected-generation",
                "0",
                "--json",
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().out)["status"] == "INVALID_INPUT"


def test_preparation_uses_finite_owned_process_deadline(tmp_path: Path) -> None:
    module = importlib.import_module("febio_cae.adapters.geometry.preparation")
    # A Python-only child that never enters native code must still be stopped.
    import sys
    import time

    started = time.monotonic()
    with pytest.raises(TimeoutError):
        module._run_owned(
            (sys.executable, "-I", "-c", "import time; time.sleep(60)"),
            tmp_path,
            timeout_seconds=0.15,
            memory_bytes=128 * 1024 * 1024,
        )
    assert time.monotonic() - started < 5
