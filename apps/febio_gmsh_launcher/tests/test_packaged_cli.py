import os
import subprocess
from pathlib import Path

import pytest

from febio_gmsh_launcher.errors import ExitCode


def test_packaged_cli_returns_config_error_for_missing_feb() -> None:
    value = os.environ.get("FEBIO_GMSH_PACKAGED_EXE")
    if not value:
        pytest.skip("FEBIO_GMSH_PACKAGED_EXE is not set")
    executable = Path(value)

    completed = subprocess.run(
        [str(executable), "-i", "missing.feb", "--non-interactive"],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == ExitCode.CONFIG_ERROR
    assert "does not exist" in completed.stderr
