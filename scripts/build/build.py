"""Run the clean-build gate and optionally stage a build for development.

The script is intentionally an external orchestration layer: package tests and
checks run before any deployment side effect.  Staging is opt-in and requires
an explicit source directory and LOCALAPPDATA override, which keeps tests and
dry runs away from a real user installation.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

# The script is run directly from a source checkout, before the package has
# necessarily been installed into the invoking interpreter.
SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from febio_cae_harness import __version__  # noqa: E402
from febio_cae_harness.launch import (  # noqa: E402
    BuildIdentity,
    DeploymentReceipt,
    stage_latest_development,
)

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


class BuildFailure(RuntimeError):
    """Raised when a clean-build step fails or the repository is not clean."""


@dataclass(frozen=True, slots=True)
class BuildRequest:
    """Inputs for a reproducible clean-build run."""

    repo_root: Path
    python_executable: str = sys.executable
    run_installed_smoke: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "repo_root",
            Path(os.path.abspath(os.fspath(self.repo_root))),
        )
        if not self.repo_root.is_dir():
            raise ValueError(f"repo_root must be a directory: {self.repo_root}")
        if not self.python_executable.strip():
            raise ValueError("python_executable must be non-empty")


@dataclass(frozen=True, slots=True)
class BuildResult:
    """Evidence about a completed build gate."""

    commit_sha: str
    wheel: Path
    steps: tuple[str, ...]
    deployment: DeploymentReceipt | None = None


def _command(
    request: BuildRequest,
    *arguments: str,
) -> tuple[str, ...]:
    return (request.python_executable, *arguments)


def repository_status(
    repo_root: Path,
    *,
    runner: CommandRunner = subprocess.run,
) -> str:
    """Return porcelain status, including untracked files."""

    completed = runner(
        ("git", "status", "--porcelain", "--untracked-files=all"),
        cwd=os.fspath(repo_root),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise BuildFailure(f"cannot inspect repository status: {completed.stderr.strip()}")
    return completed.stdout


def require_clean_repository(
    repo_root: Path,
    *,
    runner: CommandRunner = subprocess.run,
) -> None:
    status = repository_status(repo_root, runner=runner)
    if status:
        raise BuildFailure("clean build requires a clean repository commit")


def current_commit(
    repo_root: Path,
    *,
    runner: CommandRunner = subprocess.run,
) -> str:
    completed = runner(
        ("git", "rev-parse", "HEAD"),
        cwd=os.fspath(repo_root),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        raise BuildFailure("cannot determine build commit")
    return completed.stdout.strip()


def _run_step(
    name: str,
    command: Sequence[str],
    *,
    request: BuildRequest,
    runner: CommandRunner,
    use_source_path: bool = True,
) -> None:
    environment: dict[str, str] | None = None
    if use_source_path:
        source_path = os.fspath(request.repo_root / "src")
        existing_path = os.environ.get("PYTHONPATH")
        environment = {
            **os.environ,
            "PYTHONPATH": (
                source_path if not existing_path else f"{source_path}{os.pathsep}{existing_path}"
            ),
        }
    completed = runner(
        tuple(command),
        cwd=os.fspath(request.repo_root),
        check=False,
        text=True,
        **({} if environment is None else {"env": environment}),
    )
    if completed.returncode != 0:
        raise BuildFailure(f"clean-build step failed: {name} (exit {completed.returncode})")


def _wheel_digest(wheel: Path) -> str:
    digest = hashlib.sha256()
    with wheel.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _wheel_path(repo_root: Path) -> Path:
    wheels = tuple(sorted((repo_root / "dist").glob("*.whl")))
    if len(wheels) != 1:
        raise BuildFailure(f"expected exactly one wheel in {repo_root / 'dist'}")
    return wheels[0]


def _smoke_executable(venv_root: Path) -> Path:
    directory = "Scripts" if os.name == "nt" else "bin"
    suffix = ".exe" if os.name == "nt" else ""
    return venv_root / directory / f"febio-cae{suffix}"


def run_installed_smoke(
    wheel: Path,
    repo_root: Path,
    *,
    python_executable: str = sys.executable,
    runner: CommandRunner = subprocess.run,
) -> None:
    """Install the wheel into a temporary clean venv and check its CLI version."""

    request = BuildRequest(repo_root, python_executable=python_executable)
    with tempfile.TemporaryDirectory(prefix=".febio-build-smoke-", dir=repo_root) as name:
        venv_root = Path(name) / "venv"
        _run_step(
            "smoke-venv",
            _command(request, "-m", "venv", os.fspath(venv_root)),
            request=request,
            runner=runner,
            use_source_path=False,
        )
        venv_python = (
            venv_root / "Scripts" / "python.exe"
            if os.name == "nt"
            else venv_root / "bin" / "python"
        )
        _run_step(
            "smoke-install",
            (os.fspath(venv_python), "-m", "pip", "install", "--no-deps", os.fspath(wheel)),
            request=request,
            runner=runner,
            use_source_path=False,
        )
        _run_step(
            "smoke-version",
            (os.fspath(_smoke_executable(venv_root)), "--version"),
            request=request,
            runner=runner,
            use_source_path=False,
        )


def run_clean_build(
    request: BuildRequest,
    *,
    stage_source: Path | None = None,
    local_app_data: Path | None = None,
    build_id: str | None = None,
    runner: CommandRunner = subprocess.run,
) -> BuildResult:
    """Run all local gates, package one wheel, and optionally publish it."""

    if stage_source is not None and local_app_data is None:
        raise BuildFailure("staging requires an explicit local_app_data path")
    require_clean_repository(request.repo_root, runner=runner)
    commit_sha = current_commit(request.repo_root, runner=runner)
    steps: list[str] = []
    commands = (
        ("pytest", _command(request, "-m", "pytest")),
        ("format", _command(request, "-m", "ruff", "format", "--check", ".")),
        ("lint", _command(request, "-m", "ruff", "check", ".")),
        ("mypy", _command(request, "-m", "mypy", "src", "tests")),
        (
            "boundary",
            _command(request, "scripts/scan_cae_data.py", "--root", "."),
        ),
        ("package", _command(request, "-m", "build")),
    )
    for name, command in commands:
        _run_step(name, command, request=request, runner=runner)
        steps.append(name)

    wheel = _wheel_path(request.repo_root)
    if request.run_installed_smoke:
        run_installed_smoke(
            wheel,
            request.repo_root,
            python_executable=request.python_executable,
            runner=runner,
        )
        steps.append("installed-smoke")

    deployment: DeploymentReceipt | None = None
    if stage_source is not None:
        identity = BuildIdentity(
            commit_sha=commit_sha,
            build_id=build_id or f"{__version__}-{commit_sha[:12]}",
            version=__version__,
            artifact_sha256=_wheel_digest(wheel),
        )
        deployment = stage_latest_development(
            stage_source,
            local_app_data,
            identity,
        )
        steps.append("stage")
    return BuildResult(commit_sha, wheel, tuple(steps), deployment)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="febio-cae-build")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--python", dest="python_executable", default=sys.executable)
    parser.add_argument("--stage-source", type=Path)
    parser.add_argument("--local-app-data", type=Path)
    parser.add_argument("--build-id")
    parser.add_argument("--skip-installed-smoke", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    request = BuildRequest(
        repo_root=arguments.repo_root,
        python_executable=arguments.python_executable,
        run_installed_smoke=not arguments.skip_installed_smoke,
    )
    result = run_clean_build(
        request,
        stage_source=arguments.stage_source,
        local_app_data=arguments.local_app_data,
        build_id=arguments.build_id,
    )
    print(f"built {result.wheel.name} from {result.commit_sha}")
    if result.deployment is not None:
        print(f"staged {result.deployment.latest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
