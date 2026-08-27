"""Run the clean-build gates from a source checkout.

The authority-producing path is deliberately closed: it always uses this
process's Python 3.12 interpreter and the real ``subprocess.run`` callable.
Test doubles are accepted only by the private command-plan helper, which never
creates a receipt or stages a directory.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, NoReturn, cast

# The script is run directly from a source checkout, before the package has
# necessarily been installed into the invoking interpreter.
SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from febio_cae_harness import __version__  # noqa: E402
from febio_cae_harness.launch import BuildIdentity  # noqa: E402

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]
_REAL_RUN = subprocess.run
_CURRENT_PYTHON = os.fspath(Path(sys.executable).resolve())


class BuildFailure(RuntimeError):
    """Raised when a clean-build step fails or the repository is not clean."""


@dataclass(frozen=True, slots=True)
class BuildRequest:
    """Inputs for a reproducible clean-build run."""

    repo_root: Path
    run_installed_smoke: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "repo_root", Path(os.path.abspath(os.fspath(self.repo_root))))
        if not self.repo_root.is_dir():
            raise ValueError(f"repo_root must be a directory: {self.repo_root}")
        if self.run_installed_smoke is not True:
            raise BuildFailure("installed smoke is mandatory")


_MANDATORY_STEPS = ("pytest", "format", "lint", "mypy", "boundary", "package", "installed-smoke")
_StepEvidence = tuple[str, tuple[str, ...], int, str, str]
_RECEIPTS: dict[int, tuple[object, ...]] = {}


class CleanBuildReceipt:
    """Opaque evidence issued only after every clean-build gate succeeds."""

    __slots__ = ()

    def __new__(cls, *args: object, **kwargs: object) -> NoReturn:
        del args, kwargs
        raise TypeError("CleanBuildReceipt instances are run_clean_build-issued")

    def __init_subclass__(cls, **kwargs: object) -> NoReturn:
        del kwargs
        raise TypeError("CleanBuildReceipt cannot be subclassed")

    def __setattr__(self, name: str, value: object) -> NoReturn:
        del name, value
        raise AttributeError("clean-build receipts are immutable")

    def _forbidden(self, *args: object) -> NoReturn:
        del args
        raise TypeError("clean-build receipts cannot be copied or pickled")

    __copy__ = __deepcopy__ = __reduce__ = __reduce_ex__ = _forbidden

    def __repr__(self) -> str:
        return "CleanBuildReceipt(<opaque>)"

    def _record(self) -> tuple[object, ...]:
        return _require_receipt(self)

    @property
    def start_commit_sha(self) -> str:
        return cast(str, self._record()[2])

    @property
    def end_commit_sha(self) -> str:
        return cast(str, self._record()[3])

    @property
    def commit_sha(self) -> str:
        return self.start_commit_sha

    @property
    def wheel(self) -> Path:
        return cast(Path, self._record()[4])

    @property
    def steps(self) -> tuple[str, ...]:
        return cast(tuple[str, ...], self._record()[6])

    @property
    def gate_exit_codes(self) -> Mapping[str, int]:
        return MappingProxyType({step: 0 for step in self.steps})

    def __getattr__(self, name: str) -> Any:
        record = self._record()
        values: dict[str, object] = {
            "wheel_path": self.wheel,
            "wheel_sha256": record[5],
            "artifact_sha256": record[5],
            "mandatory_gates": self.steps,
            "exit_codes": self.gate_exit_codes,
            "build_id": record[8],
            "version": __version__,
            "start_commit": record[2],
            "end_commit": record[3],
            "starting_commit": record[2],
            "ending_commit": record[3],
        }
        if name in {"identity", "build_identity"}:
            values[name] = BuildIdentity(
                commit_sha=cast(str, record[2]),
                build_id=cast(str, record[8]),
                version=__version__,
                artifact_sha256=cast(str, record[5]),
            )
        elif name in {"gate_evidence", "evidence", "gates"}:
            evidence: dict[str, Mapping[str, object]] = {}
            for step, command, exit_code, stdout, stderr in cast(
                tuple[_StepEvidence, ...], record[7]
            ):
                evidence[step] = MappingProxyType(
                    {"command": command, "exit_code": exit_code, "stdout": stdout, "stderr": stderr}
                )
            if "smoke-version" in evidence:
                evidence["installed-smoke"] = evidence["smoke-version"]
            values[name] = MappingProxyType(evidence)
        elif name == "commands":
            values[name] = MappingProxyType(
                {item[0]: item[1] for item in cast(tuple[_StepEvidence, ...], record[7])}
            )
        try:
            return values[name]
        except KeyError as error:
            raise AttributeError(name) from error


BuildResult = CleanBuildReceipt


def _require_current_python() -> str:
    if sys.version_info[:2] != (3, 12):
        raise BuildFailure("clean build requires the current Python 3.12 interpreter")
    try:
        executable = Path(sys.executable).resolve(strict=True)
    except OSError as error:
        raise BuildFailure("cannot resolve the current Python executable") from error
    if not executable.is_file() or os.fspath(executable) != _CURRENT_PYTHON:
        raise BuildFailure("clean build requires the exact current Python executable")
    return _CURRENT_PYTHON


def _command(request: BuildRequest, *arguments: str) -> tuple[str, ...]:
    del request
    return (_CURRENT_PYTHON, *arguments)


def _git_output(
    repo_root: Path, command: tuple[str, ...], label: str, runner: CommandRunner
) -> str:
    try:
        completed = runner(
            command,
            cwd=os.fspath(repo_root),
            check=False,
            capture_output=True,
            text=True,
        )
        if type(completed.returncode) is not int or completed.returncode != 0:
            detail = completed.stderr.strip() if isinstance(completed.stderr, str) else ""
            raise BuildFailure(f"cannot {label}: {detail}")
        if not isinstance(completed.stdout, str):
            raise TypeError(f"git {label} did not capture text output")
        return completed.stdout
    except BuildFailure:
        raise
    except Exception as error:
        raise BuildFailure(f"cannot {label}") from error


def repository_status(repo_root: Path) -> str:
    """Return porcelain status, including untracked files."""

    return _git_output(
        repo_root,
        ("git", "status", "--porcelain", "--untracked-files=all"),
        "inspect repository status",
        _REAL_RUN,
    )


def require_clean_repository(repo_root: Path) -> None:
    if repository_status(repo_root):
        raise BuildFailure("clean build requires a clean repository commit")


def current_commit(repo_root: Path) -> str:
    output = _git_output(repo_root, ("git", "rev-parse", "HEAD"), "determine build commit", _REAL_RUN)
    if not output.strip():
        raise BuildFailure("cannot determine build commit")
    return output.strip()


def _run_step(
    name: str,
    command: Sequence[str],
    *,
    request: BuildRequest,
    runner: CommandRunner,
    use_source_path: bool = True,
) -> _StepEvidence:
    environment = dict(os.environ)
    if use_source_path:
        environment["PYTHONPATH"] = os.fspath(request.repo_root / "src")
    else:
        environment.pop("PYTHONPATH", None)
    try:
        completed = runner(
            tuple(command),
            cwd=os.fspath(request.repo_root),
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        returncode, stdout, stderr = completed.returncode, completed.stdout, completed.stderr
        if type(returncode) is not int or not isinstance(stdout, str) or not isinstance(stderr, str):
            raise TypeError("command did not capture text output and an exit code")
    except BuildFailure:
        raise
    except Exception as error:
        raise BuildFailure(f"clean-build step failed: {name} (exception)") from error
    if returncode != 0:
        raise BuildFailure(f"clean-build step failed: {name} (exit {returncode})")
    return (name, tuple(command), returncode, stdout, stderr)


def _command_plan(request: BuildRequest) -> tuple[tuple[str, tuple[str, ...]], ...]:
    return (
        ("pytest", _command(request, "-m", "pytest")),
        ("format", _command(request, "-m", "ruff", "format", "--check", ".")),
        ("lint", _command(request, "-m", "ruff", "check", ".")),
        ("mypy", _command(request, "-m", "mypy", "src", "tests")),
        ("boundary", _command(request, "scripts/scan_cae_data.py", "--root", ".")),
        ("package", _command(request, "-m", "build")),
    )


def _execute_command_plan(
    request: BuildRequest, *, runner: CommandRunner = _REAL_RUN
) -> tuple[_StepEvidence, ...]:
    """Run a command plan for tests; this helper cannot issue or stage receipts."""

    evidence: list[_StepEvidence] = []
    for name, command in _command_plan(request):
        if name == "package" and _wheel_paths(request.repo_root):
            raise BuildFailure(f"stale wheel(s) exist in {request.repo_root / 'dist'}")
        evidence.append(_run_step(name, command, request=request, runner=runner))
    return tuple(evidence)


def _wheel_paths(repo_root: Path) -> tuple[Path, ...]:
    dist = repo_root / "dist"
    if not dist.exists():
        return ()
    try:
        if not dist.is_dir() or dist.is_symlink() or dist.resolve().parent != repo_root.resolve():
            raise BuildFailure(f"wheel directory must be a repo-local directory: {dist}")
    except OSError as error:
        raise BuildFailure(f"cannot inspect wheel directory: {dist}") from error
    return tuple(sorted(dist.glob("*.whl")))


def _wheel_path(repo_root: Path) -> Path:
    wheels = _wheel_paths(repo_root)
    if len(wheels) != 1:
        raise BuildFailure(f"expected exactly one freshly produced wheel in {repo_root / 'dist'}")
    if wheels[0].is_symlink() or not wheels[0].is_file():
        raise BuildFailure(f"fresh wheel is not a regular file: {wheels[0]}")
    return wheels[0]


def _wheel_digest(wheel: Path) -> str:
    digest = hashlib.sha256()
    try:
        with wheel.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise BuildFailure(f"cannot read wheel: {wheel}") from error
    return digest.hexdigest()


def _smoke_executable(venv_root: Path) -> Path:
    directory = "Scripts" if os.name == "nt" else "bin"
    suffix = ".exe" if os.name == "nt" else ""
    return venv_root / directory / f"febio-cae{suffix}"


def _run_installed_smoke(
    wheel: Path, repo_root: Path, *, runner: CommandRunner = _REAL_RUN
) -> tuple[_StepEvidence, ...]:
    """Install the wheel into a temporary clean venv and check its CLI version."""

    request = BuildRequest(repo_root)
    with tempfile.TemporaryDirectory(prefix=".febio-build-smoke-", dir=repo_root) as name:
        venv_root = Path(name) / "venv"
        venv_evidence = _run_step(
            "smoke-venv",
            _command(request, "-m", "venv", os.fspath(venv_root)),
            request=request,
            runner=runner,
            use_source_path=False,
        )
        venv_python = venv_root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        install_evidence = _run_step(
            "smoke-install",
            (os.fspath(venv_python), "-m", "pip", "install", "--no-deps", os.fspath(wheel)),
            request=request,
            runner=runner,
            use_source_path=False,
        )
        version_evidence = _run_step(
            "smoke-version",
            (os.fspath(_smoke_executable(venv_root)), "--version"),
            request=request,
            runner=runner,
            use_source_path=False,
        )
        if version_evidence[3] != "febio-cae 0.1.0\n" or version_evidence[4]:
            raise BuildFailure("clean-build step failed: smoke-version (unexpected output)")
        return (venv_evidence, install_evidence, version_evidence)


def run_installed_smoke(wheel: Path, repo_root: Path) -> tuple[_StepEvidence, ...]:
    """Run the real installed smoke; test runners are intentionally unsupported."""

    return _run_installed_smoke(wheel, repo_root)


def _require_receipt(value: object) -> tuple[object, ...]:
    if type(value) is not CleanBuildReceipt:
        raise TypeError("value is not an exact CleanBuildReceipt")
    record = _RECEIPTS.get(id(value))
    if record is None or record[0] is not value:
        raise TypeError("CleanBuildReceipt is not run_clean_build-issued")
    return record


def stage_clean_build(*args: object, **kwargs: object) -> NoReturn:
    """Staging is unavailable until a trusted wheel-derived runtime exists."""

    del args, kwargs
    raise BuildFailure("clean-build staging is unavailable")


def run_clean_build(request: BuildRequest) -> CleanBuildReceipt:
    """Run every mandatory gate and return receipt evidence for one fresh wheel."""

    _require_current_python()
    require_clean_repository(request.repo_root)
    start_commit = current_commit(request.repo_root)
    if _wheel_paths(request.repo_root):
        raise BuildFailure(f"stale wheel(s) exist in {request.repo_root / 'dist'}")

    evidence = list(_execute_command_plan(request))
    wheel = _wheel_path(request.repo_root)
    evidence.extend(_run_installed_smoke(wheel, request.repo_root))
    wheel_sha256 = _wheel_digest(wheel)
    require_clean_repository(request.repo_root)
    end_commit = current_commit(request.repo_root)
    if end_commit != start_commit:
        raise BuildFailure("repository HEAD changed during clean build")

    build_id = f"{__version__}-{start_commit[:12]}-{wheel_sha256[:16]}"
    receipt = object.__new__(CleanBuildReceipt)
    _RECEIPTS[id(receipt)] = (
        receipt,
        request.repo_root,
        start_commit,
        end_commit,
        wheel,
        wheel_sha256,
        _MANDATORY_STEPS,
        tuple(evidence),
        build_id,
    )
    return receipt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="febio-cae-build")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    result = run_clean_build(BuildRequest(repo_root=arguments.repo_root))
    print(f"built {result.wheel.name} from {result.commit_sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
