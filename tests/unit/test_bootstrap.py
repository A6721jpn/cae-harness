from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPOSITORY_ROOT / "src"


def _cli_environment() -> dict[str, str]:
    environment = os.environ.copy()
    pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        f"{SOURCE_ROOT}{os.pathsep}{pythonpath}" if pythonpath else str(SOURCE_ROOT)
    )
    return environment


def _run_cli(*arguments: str, environment: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "febio_cae", *arguments],
        cwd=REPOSITORY_ROOT,
        env=environment or _cli_environment(),
        capture_output=True,
        text=True,
        check=False,
    )


def test_version_prints_product_version() -> None:
    completed = _run_cli("--version")

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.startswith("febio-cae ")
    assert completed.stdout.strip().split(" ", maxsplit=1)[1]


def test_doctor_reports_missing_capabilities_as_structured_exit_four() -> None:
    environment = _cli_environment()
    environment.update(
        {
            "PATH": "",
            "FEBIO_CAE_FEBIO_PATH": "",
            "FEBIO_CAE_STUDIO_PATH": "",
            "FEBIO_CAE_GMSH_PATH": "",
        }
    )

    completed = _run_cli("doctor", "--json", environment=environment)

    assert completed.returncode == 4, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["schema_version"] == "1"
    assert payload["status"] == "UNAVAILABLE"
    assert {"febio", "febio_studio", "gmsh"} <= set(payload["capabilities"])
    assert any(item["code"] == "CAPABILITY_MISSING" for item in payload["diagnostics"])
    assert payload["next_actions"]
