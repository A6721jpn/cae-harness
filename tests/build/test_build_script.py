from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from scripts.build.build import (
    BuildFailure,
    BuildRequest,
    require_clean_repository,
    run_clean_build,
)


def completed(
    command: Sequence[str],
    *,
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(tuple(command), returncode, stdout, stderr)


def test_require_clean_repository_rejects_dirty_status() -> None:
    def runner(command: Sequence[str], **_: object) -> subprocess.CompletedProcess[str]:
        return completed(command, stdout=" M src/febio_cae_harness/cli.py\n")

    with pytest.raises(BuildFailure, match="clean repository"):
        require_clean_repository(Path.cwd(), runner=runner)


def test_clean_build_runs_gates_before_returning_wheel(tmp_path: Path) -> None:
    commands: list[tuple[str, ...]] = []

    def runner(command: Sequence[str], **_: object) -> subprocess.CompletedProcess[str]:
        normalised = tuple(command)
        commands.append(normalised)
        if normalised[:2] == ("git", "rev-parse"):
            return completed(normalised, stdout="abc123\n")
        if normalised[:2] == ("git", "status"):
            return completed(normalised)
        if "-m" in normalised and "build" in normalised:
            dist = tmp_path / "dist"
            dist.mkdir(exist_ok=True)
            (dist / "febio_cae_harness-0.1.0-py3-none-any.whl").write_bytes(b"wheel")
        return completed(normalised)

    result = run_clean_build(
        BuildRequest(tmp_path, python_executable="python", run_installed_smoke=False),
        runner=runner,
    )

    assert result.commit_sha == "abc123"
    assert result.wheel.name == "febio_cae_harness-0.1.0-py3-none-any.whl"
    assert result.steps == ("pytest", "format", "lint", "mypy", "boundary", "package")
    assert commands[2][2:] == ("pytest",)
    assert commands[3][2:] == ("ruff", "format", "--check", ".")
    assert commands[4][2:] == ("ruff", "check", ".")
    assert commands[5][2:] == ("mypy", "src", "tests")
    assert commands[6][1:] == ("scripts/scan_cae_data.py", "--root", ".")
    assert commands[7][2:] == ("build",)
