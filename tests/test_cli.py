from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_module_version_command() -> None:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")

    completed = subprocess.run(
        [sys.executable, "-m", "febio_cae_harness", "--version"],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "febio-cae 0.1.0\n"
    assert completed.stderr == ""
