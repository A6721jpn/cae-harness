from __future__ import annotations

import copy
import hashlib
import json
import pickle
import subprocess
import sys
import zipfile
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace

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


def _write_minimal_wheel(path: Path) -> None:
    metadata = """Metadata-Version: 2.1
Name: febio-cae-harness
Version: 0.1.0
"""
    wheel = """Wheel-Version: 1.0
Generator: febio-cae-harness-test
Root-Is-Purelib: true
Tag: py3-none-any
"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("febio_cae_harness/__init__.py", "")
        archive.writestr(
            "febio_cae_harness/cli.py",
            'def main():\n    print("febio-cae 0.1.0")\n',
        )
        archive.writestr("febio_cae_harness-0.1.0.dist-info/METADATA", metadata)
        archive.writestr("febio_cae_harness-0.1.0.dist-info/WHEEL", wheel)
        archive.writestr(
            "febio_cae_harness-0.1.0.dist-info/entry_points.txt",
            "[console_scripts]\nfebio-cae = febio_cae_harness.cli:main\n",
        )
        archive.writestr("febio_cae_harness-0.1.0.dist-info/RECORD", "")


def _git(repo: Path, *arguments: str) -> str:
    completed_process = subprocess.run(
        ("git", *arguments),
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed_process.stdout.strip()


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


def test_step_evidence_report_retains_exact_command_exit_and_final_summary() -> None:
    evidence: build_script._StepEvidence = (
        "pytest",
        ("python.exe", "-m", "pytest"),
        0,
        "progress\n819 passed, 17 skipped in 1.00s\n",
        "",
    )

    line = build_script._format_step_evidence(evidence)

    label, encoded = line.split(" ", 1)
    assert label == "gate=pytest"
    assert json.loads(encoded) == {
        "command": ["python.exe", "-m", "pytest"],
        "exit_code": 0,
        "summary": "819 passed, 17 skipped in 1.00s",
    }


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


def test_build_cli_stages_only_the_issued_clean_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    wheel = tmp_path / "dist" / "fresh.whl"
    receipt = SimpleNamespace(wheel=wheel, commit_sha="a" * 40)
    deployment = SimpleNamespace(latest=tmp_path / "fixed" / "latest-development")
    shortcut = tmp_path / "start-menu" / "FEBio CAE Workbench.lnk"
    calls: list[object] = []

    def run(request: BuildRequest) -> object:
        calls.append(request)
        return receipt

    def stage(value: object) -> object:
        calls.append(value)
        return deployment

    def report(value: object) -> None:
        calls.append(("evidence", value))
        print('gate=pytest {"exit_code":0}')

    def install(value: object) -> Path:
        calls.append(value)
        return shortcut

    monkeypatch.setattr(build_script, "run_clean_build", run)
    monkeypatch.setattr(build_script, "_print_build_evidence", report, raising=False)
    monkeypatch.setattr(build_script, "stage_clean_build", stage)
    monkeypatch.setattr(build_script, "_install_fixed_shortcut", install, raising=False)

    assert build_script.main(["--repo-root", str(tmp_path)]) == 0

    assert calls == [BuildRequest(tmp_path), ("evidence", receipt), receipt, deployment]
    assert capsys.readouterr().out == (
        f"built fresh.whl from {'a' * 40}\n"
        'gate=pytest {"exit_code":0}\n'
        f"staged {deployment.latest}\nshortcut {shortcut}\n"
    )


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

    with pytest.raises(TypeError, match="run_clean_build-issued"):
        build_script.stage_clean_build(forged)
    with pytest.raises(TypeError):
        build_script.stage_clean_build(forged, Path("arbitrary-source"))  # type: ignore[call-arg]


@pytest.mark.skipif(sys.platform != "win32", reason="fixed launcher contract is Windows-only")
def test_stage_clean_build_installs_wheel_at_fixed_atomic_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".gitignore").write_text("dist/\n", encoding="utf-8")
    _git(repo, "init", "--initial-branch=main")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "base")
    commit_sha = _git(repo, "rev-parse", "HEAD")

    dist = repo / "dist"
    dist.mkdir()
    wheel = dist / "febio_cae_harness-0.1.0-py3-none-any.whl"
    _write_minimal_wheel(wheel)
    wheel_sha256 = hashlib.sha256(wheel.read_bytes()).hexdigest()
    evidence = tuple(
        (step, ("test", step), 0, "", "")
        for step in ("pytest", "format", "lint", "mypy", "boundary", "package")
    ) + (
        ("smoke-venv", ("test", "smoke-venv"), 0, "", ""),
        ("smoke-install", ("test", "smoke-install"), 0, "", ""),
        ("smoke-version", ("test", "smoke-version"), 0, "febio-cae 0.1.0\n", ""),
    )
    receipt = object.__new__(CleanBuildReceipt)
    monkeypatch.setitem(
        build_script._RECEIPTS,
        id(receipt),
        (
            receipt,
            repo,
            commit_sha,
            commit_sha,
            wheel,
            wheel_sha256,
            build_script._MANDATORY_STEPS,
            evidence,
            f"0.1.0-{commit_sha[:12]}-{wheel_sha256[:16]}",
        ),
    )
    local_app_data = tmp_path / "local-app-data"
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

    deployment = build_script.stage_clean_build(receipt)

    assert deployment.latest == (local_app_data / "FEBioCaeWorkbench" / "latest-development")
    assert deployment.build_identity.commit_sha == commit_sha
    assert deployment.build_identity.artifact_sha256 == wheel_sha256
    completed_process = subprocess.run(
        (str(deployment.latest / "febio-cae.exe"), "--version"),
        cwd=deployment.latest,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed_process.returncode == 0
    assert completed_process.stdout == "febio-cae 0.1.0\n"
    assert completed_process.stderr == ""


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
