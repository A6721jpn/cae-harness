"""Run the clean-build gates from a source checkout.

The authority-producing path is deliberately closed: it always uses this
process's Python 3.12 interpreter and the real ``subprocess.run`` callable.
Test doubles are accepted only by the private command-plan helper, which never
creates a receipt or stages a directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
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
from febio_cae_harness.launch import (  # noqa: E402
    LAUNCHER_NAME,
    SHORTCUT_LINK_NAME,
    BuildIdentity,
    DeploymentError,
    DeploymentLayout,
    DeploymentReceipt,
    ShortcutManager,
    stage_latest_development,
)

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


def _format_step_evidence(evidence: _StepEvidence) -> str:
    name, command, exit_code, stdout, stderr = evidence
    output = stdout if stdout.strip() else stderr
    lines = tuple(line.strip() for line in output.splitlines() if line.strip())
    payload = {
        "command": list(command),
        "exit_code": exit_code,
        "summary": lines[-1] if lines else "",
    }
    return f"gate={name} {json.dumps(payload, sort_keys=True, separators=(',', ':'))}"


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
    output = _git_output(
        repo_root, ("git", "rev-parse", "HEAD"), "determine build commit", _REAL_RUN
    )
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
        if (
            type(returncode) is not int
            or not isinstance(stdout, str)
            or not isinstance(stderr, str)
        ):
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


def _retarget_windows_console_launcher(
    launcher: Path,
    old_python: Path,
    new_python: Path,
) -> None:
    """Atomically retarget one distlib launcher to an equal-length venv path."""

    source = Path(os.path.abspath(os.fspath(old_python)))
    target = Path(os.path.abspath(os.fspath(new_python)))
    old_shebang = b"#!" + os.fspath(source).encode("utf-8") + b"\n"
    new_shebang = b"#!" + os.fspath(target).encode("utf-8") + b"\n"
    if len(old_shebang) != len(new_shebang):
        raise BuildFailure("console launcher retarget requires equal-length interpreter paths")
    try:
        if launcher.is_symlink() or not launcher.is_file():
            raise BuildFailure(f"console launcher is not a regular file: {launcher}")
        payload = launcher.read_bytes()
    except BuildFailure:
        raise
    except OSError as error:
        raise BuildFailure(f"cannot read console launcher: {launcher}") from error
    if payload.count(old_shebang) != 1:
        raise BuildFailure("console launcher must contain exactly one embedded interpreter")

    replacement = payload.replace(old_shebang, new_shebang, 1)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{launcher.name}.",
            suffix=".tmp",
            dir=launcher.parent,
            delete=False,
        ) as stream:
            temporary_name = stream.name
            stream.write(replacement)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_name, launcher.stat().st_mode)
        os.replace(temporary_name, launcher)
    except OSError as error:
        raise BuildFailure(f"cannot retarget console launcher: {launcher}") from error
    finally:
        if temporary_name is not None:
            with suppress(OSError):
                Path(temporary_name).unlink(missing_ok=True)


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


def _print_build_evidence(receipt: CleanBuildReceipt) -> None:
    record = _require_receipt(receipt)
    for evidence in cast(tuple[_StepEvidence, ...], record[7]):
        print(_format_step_evidence(evidence))


def _validated_stage_record(receipt: CleanBuildReceipt) -> tuple[object, ...]:
    record = _require_receipt(receipt)
    _require_current_python()
    repo_root = cast(Path, record[1])
    start_commit = cast(str, record[2])
    end_commit = cast(str, record[3])
    wheel = cast(Path, record[4])
    wheel_sha256 = cast(str, record[5])
    mandatory_steps = cast(tuple[str, ...], record[6])
    evidence = cast(tuple[_StepEvidence, ...], record[7])

    require_clean_repository(repo_root)
    if start_commit != end_commit or current_commit(repo_root) != start_commit:
        raise BuildFailure("clean-build receipt no longer matches repository HEAD")
    current_wheel = _wheel_path(repo_root)
    try:
        if current_wheel.resolve(strict=True) != wheel.resolve(strict=True):
            raise BuildFailure("clean-build receipt no longer identifies the fresh wheel")
    except OSError as error:
        raise BuildFailure("cannot resolve clean-build receipt wheel") from error
    if _wheel_digest(current_wheel) != wheel_sha256:
        raise BuildFailure("clean-build receipt wheel digest changed")
    if mandatory_steps != _MANDATORY_STEPS:
        raise BuildFailure("clean-build receipt is missing mandatory gates")
    expected_evidence = (
        "pytest",
        "format",
        "lint",
        "mypy",
        "boundary",
        "package",
        "smoke-venv",
        "smoke-install",
        "smoke-version",
    )
    if tuple(item[0] for item in evidence) != expected_evidence or any(
        item[2] != 0 for item in evidence
    ):
        raise BuildFailure("clean-build receipt gate evidence is incomplete")
    if evidence[-1][3] != f"febio-cae {__version__}\n" or evidence[-1][4]:
        raise BuildFailure("clean-build receipt installed smoke evidence is invalid")
    return record


_RUNTIME_SOURCE_PREFIX = ".febio-runtime-"
_REPARSE_POINT = 0x400


def _runtime_source_name_length(layout: DeploymentLayout) -> int:
    return len(os.fspath(layout.latest.relative_to(layout.local_app_data)).encode("utf-8"))


def _new_runtime_source(layout: DeploymentLayout) -> Path:
    name_length = _runtime_source_name_length(layout)
    if name_length <= len(_RUNTIME_SOURCE_PREFIX):
        raise BuildFailure("fixed deployment path is too short for private runtime staging")
    try:
        layout.local_app_data.mkdir(parents=True, exist_ok=True)
        local_app_data = layout.local_app_data.resolve(strict=True)
    except OSError as error:
        raise BuildFailure("cannot create private runtime staging parent") from error
    for _ in range(32):
        suffix_length = name_length - len(_RUNTIME_SOURCE_PREFIX)
        suffix = uuid.uuid4().hex[:suffix_length]
        candidate = local_app_data / f"{_RUNTIME_SOURCE_PREFIX}{suffix}"
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        except OSError as error:
            raise BuildFailure("cannot create private runtime staging directory") from error
        if len(os.fspath(candidate).encode("utf-8")) != len(
            os.fspath(layout.latest).encode("utf-8")
        ):
            _remove_runtime_source(candidate, layout)
            raise BuildFailure("private runtime and fixed deployment paths are not equal-length")
        return candidate
    raise BuildFailure("cannot allocate a unique private runtime staging directory")


def _remove_runtime_source(source: Path, layout: DeploymentLayout) -> None:
    try:
        local_app_data = layout.local_app_data.resolve(strict=True)
        source_absolute = Path(os.path.abspath(os.fspath(source)))
        metadata = source_absolute.lstat()
        is_reparse = bool(getattr(metadata, "st_file_attributes", 0) & _REPARSE_POINT)
        if (
            source_absolute.parent != local_app_data
            or not source_absolute.name.startswith(_RUNTIME_SOURCE_PREFIX)
            or len(source_absolute.name.encode("utf-8")) != _runtime_source_name_length(layout)
            or source_absolute.is_symlink()
            or is_reparse
            or not source_absolute.is_dir()
        ):
            raise BuildFailure("refusing to remove unowned private runtime staging path")
        shutil.rmtree(source_absolute)
    except BuildFailure:
        raise
    except FileNotFoundError:
        return
    except OSError as error:
        raise BuildFailure("cannot remove private runtime staging directory") from error


def _materialise_runtime_source(
    receipt: CleanBuildReceipt,
    source: Path,
    layout: DeploymentLayout,
) -> None:
    record = _require_receipt(receipt)
    repo_root = cast(Path, record[1])
    wheel = cast(Path, record[4])
    request = BuildRequest(repo_root)
    _run_step(
        "stage-venv",
        (_CURRENT_PYTHON, "-m", "venv", "--without-pip", os.fspath(source)),
        request=request,
        runner=_REAL_RUN,
        use_source_path=False,
    )
    venv_python = source / "Scripts" / "python.exe"
    installed_launcher = source / "Scripts" / LAUNCHER_NAME
    _run_step(
        "stage-install",
        (
            _CURRENT_PYTHON,
            "-m",
            "pip",
            "--python",
            os.fspath(source),
            "install",
            "--no-deps",
            os.fspath(wheel),
        ),
        request=request,
        runner=_REAL_RUN,
        use_source_path=False,
    )
    smoke = _run_step(
        "stage-source-version",
        (os.fspath(installed_launcher), "--version"),
        request=request,
        runner=_REAL_RUN,
        use_source_path=False,
    )
    if smoke[3] != f"febio-cae {__version__}\n" or smoke[4]:
        raise BuildFailure("staged wheel launcher returned unexpected version output")
    target_python = layout.latest / "Scripts" / "python.exe"
    _retarget_windows_console_launcher(installed_launcher, venv_python, target_python)
    try:
        shutil.copy2(installed_launcher, source / LAUNCHER_NAME)
        artifact_directory = source / "artifact"
        artifact_directory.mkdir()
        shutil.copy2(wheel, artifact_directory / wheel.name)
    except OSError as error:
        raise BuildFailure("cannot complete wheel-derived runtime payload") from error


def stage_clean_build(receipt: CleanBuildReceipt) -> DeploymentReceipt:
    """Atomically stage one exact clean-build wheel at the fixed Windows location."""

    _validated_stage_record(receipt)
    if os.name != "nt":
        raise BuildFailure("fixed clean-build staging requires Windows")
    layout = DeploymentLayout.from_environment()
    source = _new_runtime_source(layout)
    try:
        _materialise_runtime_source(receipt, source, layout)
        deployment = stage_latest_development(
            source,
            identity=receipt.build_identity,
            layout=layout,
        )
    except DeploymentError as error:
        failure = BuildFailure("cannot atomically stage clean build")
        try:
            _remove_runtime_source(source, layout)
        except BaseException as cleanup_error:
            failure.add_note(f"private runtime cleanup failed: {cleanup_error}")
        raise failure from error
    except BaseException as primary_error:
        try:
            _remove_runtime_source(source, layout)
        except BaseException as cleanup_error:
            primary_error.add_note(f"private runtime cleanup failed: {cleanup_error}")
        raise
    _remove_runtime_source(source, layout)
    return deployment


def _install_fixed_shortcut(deployment: DeploymentReceipt) -> Path:
    if type(deployment) is not DeploymentReceipt:
        raise TypeError("deployment must be an exact DeploymentReceipt")
    manager = ShortcutManager(deployment.layout)
    descriptor = manager.issue_descriptor()
    descriptor_path = manager.write(descriptor)
    manager.verify(descriptor)
    link_path = descriptor_path.parent / SHORTCUT_LINK_NAME
    if not link_path.is_file() or link_path.is_symlink():
        raise BuildFailure("verified Start Menu shortcut is missing")
    return link_path


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
    _print_build_evidence(result)
    deployment = stage_clean_build(result)
    shortcut = _install_fixed_shortcut(deployment)
    print(f"staged {deployment.latest}")
    print(f"shortcut {shortcut}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
