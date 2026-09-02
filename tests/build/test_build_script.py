from __future__ import annotations

import copy
import pickle
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

import scripts.build.build as build_script
from scripts.build.build import BuildFailure, BuildRequest, run_clean_build

CleanBuildReceipt = build_script.CleanBuildReceipt


def completed(
    command: Sequence[str], *, stdout: str = "", stderr: str = "", returncode: int = 0
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(tuple(command), returncode, stdout, stderr)


def fake_runner(
    root: Path,
    calls: list[tuple[tuple[str, ...], dict[str, object]]],
    *,
    version: str = "febio-cae 0.1.0\n",
) -> build_script.CommandRunner:
    def run(command: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        normalised = tuple(command)
        calls.append((normalised, kwargs))
        if normalised[-1:] == ("--version",):
            return completed(normalised, stdout=version)
        if normalised == (str(Path(sys.executable).resolve()), "-m", "build"):
            dist = root / "dist"
            dist.mkdir(exist_ok=True)
            (dist / "febio_cae_harness-0.1.0-py3-none-any.whl").write_bytes(b"wheel")
        return completed(normalised)

    return run


def test_command_plan_runs_gates_with_bound_environment_but_no_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    runner = fake_runner(tmp_path, calls)
    monkeypatch.setenv("PYTHONPATH", "inherited-value")
    request = BuildRequest(tmp_path)
    evidence = list(build_script._execute_command_plan(request, runner=runner))
    evidence.extend(
        build_script._run_installed_smoke(
            tmp_path / "dist" / "febio_cae_harness-0.1.0-py3-none-any.whl", tmp_path, runner=runner
        )
    )

    assert [item[0] for item in evidence] == [
        "pytest",
        "format",
        "lint",
        "mypy",
        "boundary",
        "package",
        "smoke-venv",
        "smoke-install",
        "smoke-version",
    ]
    expected_python = str(Path(sys.executable).resolve())
    assert all(command[0] == expected_python for command, _ in calls[:6])
    assert all(kwargs["env"]["PYTHONPATH"] == str(tmp_path / "src") for _, kwargs in calls[:6])  # type: ignore[index]
    assert all("PYTHONPATH" not in kwargs["env"] for _, kwargs in calls[6:])  # type: ignore[operator]
    assert build_script._RECEIPTS == {}


def test_installed_smoke_requires_exact_version_output(tmp_path: Path) -> None:
    wheel = tmp_path / "wheel.whl"
    wheel.write_bytes(b"wheel")
    with pytest.raises(BuildFailure, match="smoke-version"):
        build_script._run_installed_smoke(
            wheel, tmp_path, runner=fake_runner(tmp_path, [], version="forged\n")
        )


def test_stale_wheel_is_rejected_by_non_authoritative_plan(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "stale.whl").write_bytes(b"old")
    with pytest.raises(BuildFailure, match="stale"):
        build_script._execute_command_plan(BuildRequest(tmp_path), runner=fake_runner(tmp_path, []))


def test_authority_rejects_runner_python_stage_and_build_id_overrides(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        run_clean_build(BuildRequest(tmp_path), runner=fake_runner(tmp_path, []))  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        BuildRequest(tmp_path, python_executable="python-shim")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        run_clean_build(
            BuildRequest(tmp_path),
            stage_source=tmp_path / "arbitrary-source",  # type: ignore[call-arg]
            local_app_data=tmp_path / "arbitrary-app",  # type: ignore[call-arg]
            build_id="forged-build-id",  # type: ignore[call-arg]
        )
    with pytest.raises(BuildFailure, match="mandatory"):
        BuildRequest(tmp_path, run_installed_smoke=False)


def test_cli_has_no_untrusted_authority_arguments() -> None:
    parser = build_script.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--python", "python-shim"])
    with pytest.raises(SystemExit):
        parser.parse_args(["--stage-source", "arbitrary-source"])
    with pytest.raises(SystemExit):
        parser.parse_args(["--build-id", "forged-build-id"])


def test_receipt_is_exact_opaque_authority_and_staging_is_fail_closed() -> None:
    assert build_script.BuildResult is CleanBuildReceipt
    with pytest.raises(TypeError):
        CleanBuildReceipt()  # type: ignore[call-arg]
    forged = object.__new__(CleanBuildReceipt)
    with pytest.raises(TypeError):
        _ = forged.wheel
    for operation in (
        lambda: copy.copy(forged),
        lambda: copy.deepcopy(forged),
        lambda: pickle.dumps(forged),
    ):
        with pytest.raises(TypeError):
            operation()
    with pytest.raises(TypeError):

        class ReceiptChild(CleanBuildReceipt):
            pass

    with pytest.raises(BuildFailure, match="staging is unavailable"):
        build_script.stage_clean_build(forged, Path("arbitrary-source"), Path("arbitrary-app"))


def test_windows_console_launcher_retarget_is_exact_and_single_use(tmp_path: Path) -> None:
    launcher = tmp_path / "febio-cae.exe"
    old_python = tmp_path / "aaaaaaaa" / "Scripts" / "python.exe"
    new_python = tmp_path / "bbbbbbbb" / "Scripts" / "python.exe"
    old_shebang = b"#!" + str(old_python).encode("utf-8") + b"\n"
    new_shebang = b"#!" + str(new_python).encode("utf-8") + b"\n"
    launcher.write_bytes(b"MZ-native-launcher\0" + old_shebang + b"zip-payload")

    build_script._retarget_windows_console_launcher(launcher, old_python, new_python)

    assert launcher.read_bytes() == b"MZ-native-launcher\0" + new_shebang + b"zip-payload"

    duplicated = b"MZ" + old_shebang + old_shebang
    launcher.write_bytes(duplicated)
    with pytest.raises(BuildFailure, match="exactly one embedded interpreter"):
        build_script._retarget_windows_console_launcher(launcher, old_python, new_python)
    assert launcher.read_bytes() == duplicated


def test_windows_console_launcher_retarget_requires_equal_length_paths(tmp_path: Path) -> None:
    launcher = tmp_path / "febio-cae.exe"
    launcher.write_bytes(b"MZ")

    with pytest.raises(BuildFailure, match="equal-length"):
        build_script._retarget_windows_console_launcher(
            launcher,
            tmp_path / "short" / "python.exe",
            tmp_path / "much-longer" / "python.exe",
        )
    assert launcher.read_bytes() == b"MZ"
