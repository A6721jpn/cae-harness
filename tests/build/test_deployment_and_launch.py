from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import BinaryIO

import pytest

import febio_cae_harness.launch.planner as planner_module
from febio_cae_harness.launch import (
    LAUNCHER_NAME,
    BuildIdentity,
    DeploymentError,
    DeploymentLayout,
    DeploymentRollbackError,
    LaunchError,
    compute_payload_sha256,
    fixed_shortcut_descriptor,
    launch_cli,
    plan_cli_launch,
    stage_latest_development,
)
from febio_cae_harness.launch.cli import main as launch_main


def make_build(source: Path, *, name: str = LAUNCHER_NAME) -> None:
    source.mkdir()
    (source / name).write_text("@echo off\necho FEBio CAE\n", encoding="utf-8")
    (source / "README.txt").write_text("development build", encoding="utf-8")


def identity(build_id: str = "build-1") -> BuildIdentity:
    return BuildIdentity(
        commit_sha="abc123",
        build_id=build_id,
        version="0.1.0",
    )


def test_layout_is_fixed_under_local_app_data_without_touching_disk(tmp_path: Path) -> None:
    layout = DeploymentLayout.from_local_app_data(tmp_path)

    assert layout.app_root == tmp_path / "FEBioCaeWorkbench"
    assert layout.latest == layout.app_root / "latest-development"
    assert layout.launcher == layout.latest / LAUNCHER_NAME
    assert not layout.app_root.exists()


def test_stage_publishes_latest_atomically_and_records_identity(tmp_path: Path) -> None:
    source = tmp_path / "source"
    make_build(source)

    receipt = stage_latest_development(source, tmp_path / "local", identity())

    assert receipt.latest == tmp_path / "local" / "FEBioCaeWorkbench" / "latest-development"
    assert (receipt.latest / LAUNCHER_NAME).exists()
    persisted = json.loads((receipt.latest / "build-identity.json").read_text())
    assert persisted == receipt.identity.to_dict()
    assert persisted["artifact_sha256"]
    assert persisted["payload_sha256"]
    assert not list(receipt.layout.staging_root.iterdir())


def test_stage_atomically_replaces_an_empty_legacy_latest_directory(tmp_path: Path) -> None:
    source = tmp_path / "source"
    local = tmp_path / "local"
    make_build(source)
    legacy = local / "FEBioCaeWorkbench" / "latest-development"
    legacy.mkdir(parents=True)

    receipt = stage_latest_development(source, local, identity())

    assert receipt.latest == legacy
    assert (legacy / LAUNCHER_NAME).is_file()
    assert receipt.rollback_path is None
    assert receipt.previous_identity is None


def test_stage_rejects_a_nonempty_unidentified_latest_directory(tmp_path: Path) -> None:
    source = tmp_path / "source"
    local = tmp_path / "local"
    make_build(source)
    legacy = local / "FEBioCaeWorkbench" / "latest-development"
    legacy.mkdir(parents=True)
    (legacy / "unknown.txt").write_text("preserve me", encoding="utf-8")

    with pytest.raises(DeploymentError, match="existing deployment identity is invalid"):
        stage_latest_development(source, local, identity())

    assert (legacy / "unknown.txt").read_text(encoding="utf-8") == "preserve me"


def test_stage_keeps_previous_build_and_can_roll_back(tmp_path: Path) -> None:
    local = tmp_path / "local"
    first_source = tmp_path / "first"
    second_source = tmp_path / "second"
    make_build(first_source)
    make_build(second_source)
    (second_source / "README.txt").write_text("new build", encoding="utf-8")

    stage_latest_development(first_source, local, identity("build-1"))
    second = stage_latest_development(second_source, local, identity("build-2"))

    assert (second.latest / "README.txt").read_text(encoding="utf-8") == "new build"
    assert second.rollback_path is not None
    assert second.rollback_path.exists()

    second.rollback()

    assert json.loads((second.latest / "build-identity.json").read_text())["build_id"] == "build-1"


def test_failed_publish_restores_previous_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = tmp_path / "local"
    first_source = tmp_path / "first"
    second_source = tmp_path / "second"
    make_build(first_source)
    make_build(second_source)
    stage_latest_development(first_source, local, identity("build-1"))

    import febio_cae_harness.launch.deployment as deployment

    real_replace = deployment.os.replace

    def fail_new_publish(source: str, target: str) -> None:
        if target.endswith("latest-development") and Path(source).name.startswith("build-2-"):
            raise OSError("simulated publish failure")
        real_replace(source, target)

    monkeypatch.setattr(deployment.os, "replace", fail_new_publish)

    with pytest.raises(DeploymentError):
        stage_latest_development(second_source, local, identity("build-2"))

    latest = local / "FEBioCaeWorkbench" / "latest-development"
    assert json.loads((latest / "build-identity.json").read_text())["build_id"] == "build-1"
    assert not list((local / "FEBioCaeWorkbench" / ".staging").iterdir())
    assert not list((local / "FEBioCaeWorkbench" / ".rollback").iterdir())


def test_shortcut_descriptor_always_targets_fixed_latest_launcher(tmp_path: Path) -> None:
    layout = DeploymentLayout.from_local_app_data(tmp_path / "local")

    descriptor = fixed_shortcut_descriptor(layout, tmp_path / "start-menu")

    assert descriptor.path == tmp_path / "start-menu" / "FEBio CAE Workbench.shortcut.json"
    assert descriptor.target == layout.launcher
    assert descriptor.target == layout.latest / LAUNCHER_NAME
    assert descriptor.to_dict()["fixed_target"] is True


def test_shortcut_descriptor_round_trips_without_creating_real_start_menu_files(
    tmp_path: Path,
) -> None:
    layout = DeploymentLayout.from_local_app_data(tmp_path / "local")
    descriptor = fixed_shortcut_descriptor(layout, tmp_path / "start-menu", ["--version"])

    encoded = descriptor.to_dict()
    restored = type(descriptor).from_mapping(encoded)

    assert restored == descriptor
    assert not (tmp_path / "start-menu").exists()


def test_launch_plan_is_headless_and_does_not_select_versioned_folder(tmp_path: Path) -> None:
    layout = DeploymentLayout.from_local_app_data(tmp_path / "local")

    plan = plan_cli_launch(layout, ["--version"])

    assert plan.argv == (str(layout.launcher), "--version")
    assert plan.cwd == layout.latest
    assert "latest-development" in str(plan.argv[0])
    assert "version" not in str(plan.argv[0]).lower()


def test_launch_module_plan_command_is_headless_and_json_serializable(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        launch_main(
            [
                "plan",
                "--local-app-data",
                str(tmp_path / "local"),
                "--json",
                "--",
                "--version",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["argv"][-1] == "--version"
    assert payload["latest_development"].endswith("FEBioCaeWorkbench\\latest-development")


def test_old_receipt_cannot_rollback_over_a_newer_deployment(tmp_path: Path) -> None:
    local = tmp_path / "local"
    first_source = tmp_path / "first"
    second_source = tmp_path / "second"
    third_source = tmp_path / "third"
    make_build(first_source)
    make_build(second_source)
    make_build(third_source)

    stage_latest_development(first_source, local, identity("build-1"))
    second = stage_latest_development(second_source, local, identity("build-2"))
    stage_latest_development(third_source, local, identity("build-3"))

    with pytest.raises(DeploymentRollbackError, match="identity does not match"):
        second.rollback()

    assert (
        json.loads(
            (
                (local / "FEBioCaeWorkbench" / "latest-development") / "build-identity.json"
            ).read_text()
        )["build_id"]
        == "build-3"
    )


def test_launch_rejects_tampered_published_payload(tmp_path: Path) -> None:
    source = tmp_path / "source"
    local = tmp_path / "local"
    make_build(source)

    receipt = stage_latest_development(source, local, identity())
    (receipt.latest / "README.txt").write_text("tampered", encoding="utf-8")

    with pytest.raises(LaunchError, match="digest|payload|identity"):
        plan_cli_launch(receipt.layout, require_published=True)


@pytest.mark.skipif(os.name != "nt", reason="Windows executable sharing authority")
def test_launch_holds_exact_launcher_against_replacement_during_spawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    make_build(source)
    receipt = stage_latest_development(source, tmp_path / "local", identity())
    replacement = tmp_path / "replacement.exe"
    replacement.write_bytes(b"replacement executable")
    replacement_blocked = False
    parent_replacement_blocked = False
    app_root_replacement_blocked = False
    local_root_replacement_blocked = False
    write_blocked = False

    def spawn_while_replacing(
        command: tuple[str, ...],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal app_root_replacement_blocked, local_root_replacement_blocked
        nonlocal parent_replacement_blocked, replacement_blocked, write_blocked
        assert command[0] == str(receipt.layout.launcher)
        try:
            os.replace(replacement, receipt.layout.launcher)
        except OSError:
            replacement_blocked = True
        try:
            receipt.layout.launcher.write_bytes(b"modified in place")
        except OSError:
            write_blocked = True
        try:
            os.replace(receipt.layout.latest, tmp_path / "displaced-latest")
        except OSError:
            parent_replacement_blocked = True
        try:
            os.replace(receipt.layout.app_root, tmp_path / "displaced-app")
        except OSError:
            app_root_replacement_blocked = True
        try:
            os.replace(receipt.layout.local_app_data, tmp_path / "displaced-local")
        except OSError:
            local_root_replacement_blocked = True
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", spawn_while_replacing)

    completed = launch_cli(layout=receipt.layout)

    assert completed.returncode == 0
    assert replacement_blocked
    assert write_blocked
    assert parent_replacement_blocked
    assert app_root_replacement_blocked
    assert local_root_replacement_blocked
    assert replacement.exists()
    os.replace(replacement, receipt.layout.launcher)
    assert not replacement.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows CreateProcess proof")
def test_launch_executes_the_real_image_while_its_exact_handle_is_held(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    shutil.copy2(os.environ["COMSPEC"], source / LAUNCHER_NAME)
    (source / "README.txt").write_text("synthetic native launcher", encoding="utf-8")
    receipt = stage_latest_development(source, tmp_path / "local", identity())

    completed = launch_cli(("/d", "/c", "exit", "0"), layout=receipt.layout)

    assert completed.returncode == 0


@pytest.mark.skipif(os.name != "nt", reason="Windows CreateProcess race proof")
def test_real_create_process_keeps_launcher_and_ancestors_bound_until_child_exit(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    shutil.copy2(os.environ["COMSPEC"], source / LAUNCHER_NAME)
    (source / "README.txt").write_text("synthetic native launcher", encoding="utf-8")
    receipt = stage_latest_development(source, tmp_path / "local", identity())
    marker = receipt.layout.latest / "child-started.txt"
    command = "echo started>child-started.txt & ping -n 4 127.0.0.1 >nul"
    completed: list[subprocess.CompletedProcess[str]] = []
    failures: list[BaseException] = []

    def run_child() -> None:
        try:
            completed.append(launch_cli(("/d", "/c", command), layout=receipt.layout))
        except BaseException as error:
            failures.append(error)

    launcher_thread = threading.Thread(target=run_child)
    launcher_thread.start()
    deadline = time.monotonic() + 10
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert marker.exists() and launcher_thread.is_alive()

    replacement = tmp_path / "replacement.exe"
    replacement.write_bytes(b"replacement executable")
    attempts = (
        (replacement, receipt.layout.launcher),
        (receipt.layout.latest, tmp_path / "displaced-latest"),
        (receipt.layout.app_root, tmp_path / "displaced-app"),
        (receipt.layout.local_app_data, tmp_path / "displaced-local"),
    )
    for original, displaced in attempts:
        with pytest.raises(OSError):
            os.replace(original, displaced)

    launcher_thread.join(timeout=10)
    assert not launcher_thread.is_alive()
    assert failures == []
    assert len(completed) == 1 and completed[0].returncode == 0


@pytest.mark.skipif(os.name != "nt", reason="Windows executable sharing authority")
def test_launch_rejects_a_preexisting_writer_before_spawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    make_build(source)
    receipt = stage_latest_development(source, tmp_path / "local", identity())
    spawned = False

    def unexpected_spawn(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal spawned
        spawned = True
        return subprocess.CompletedProcess((), 0)

    monkeypatch.setattr(subprocess, "run", unexpected_spawn)
    with (
        receipt.layout.launcher.open("r+b"),
        pytest.raises(LaunchError, match="hold exact launcher"),
    ):
        launch_cli(layout=receipt.layout)

    assert not spawned


@pytest.mark.skipif(os.name != "nt", reason="Windows executable sharing authority")
def test_launch_close_failure_becomes_indeterminate_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    make_build(source)
    receipt = stage_latest_development(source, tmp_path / "local", identity())
    replacement = tmp_path / "replacement.exe"
    replacement.write_bytes(b"replacement executable")
    real_close = planner_module._windows_close_launcher
    close_calls = 0

    def fail_close_once(value: int) -> bool:
        nonlocal close_calls
        close_calls += 1
        return False if close_calls == 1 else real_close(value)

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0),
    )
    monkeypatch.setattr(planner_module, "_windows_close_launcher", fail_close_once)

    indeterminate = None
    try:
        with pytest.raises(LaunchError, match="close exact launcher handle"):
            launch_cli(layout=receipt.layout)
        assert planner_module._PENDING_LAUNCHER_CLAIMS == []
        assert len(planner_module._INDETERMINATE_LAUNCHER_CLAIMS) == 1
        indeterminate = planner_module._INDETERMINATE_LAUNCHER_CLAIMS[0]
        assert indeterminate.value is None and indeterminate.indeterminate
        assert indeterminate.last_native_value is not None
        with pytest.raises(OSError):
            os.replace(replacement, receipt.layout.launcher)

        planner_module._drain_launcher_claims()

        assert close_calls == 1
        assert planner_module._PENDING_LAUNCHER_CLAIMS == []
    finally:
        if indeterminate is not None:
            assert indeterminate.last_native_value is not None
            assert real_close(indeterminate.last_native_value)
            planner_module._INDETERMINATE_LAUNCHER_CLAIMS.remove(indeterminate)
    os.replace(replacement, receipt.layout.launcher)


@pytest.mark.skipif(os.name != "nt", reason="Windows executable sharing authority")
def test_launch_cleanup_failure_does_not_mask_the_spawn_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    make_build(source)
    receipt = stage_latest_development(source, tmp_path / "local", identity())
    real_close = planner_module._windows_close_launcher

    def fail_spawn(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise OSError("primary spawn failure")

    monkeypatch.setattr(subprocess, "run", fail_spawn)
    monkeypatch.setattr(planner_module, "_windows_close_launcher", lambda _value: False)

    with pytest.raises(LaunchError, match="cannot execute launcher") as caught:
        launch_cli(layout=receipt.layout)

    assert any("cannot close exact launcher handle" in note for note in caught.value.__notes__)
    assert planner_module._PENDING_LAUNCHER_CLAIMS == []
    assert len(planner_module._INDETERMINATE_LAUNCHER_CLAIMS) == 1
    indeterminate = planner_module._INDETERMINATE_LAUNCHER_CLAIMS[0]
    assert indeterminate.value is None and indeterminate.indeterminate
    assert indeterminate.last_native_value is not None
    assert real_close(indeterminate.last_native_value)
    planner_module._INDETERMINATE_LAUNCHER_CLAIMS.remove(indeterminate)


@pytest.mark.skipif(os.name != "nt", reason="Windows exact handle cleanup")
def test_transient_close_inspection_failure_retains_the_protected_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    make_build(source)
    receipt = stage_latest_development(source, tmp_path / "local", identity())
    real_info = planner_module._launcher_handle_info
    inspection_calls = 0

    def fail_fourth_inspection(value: int) -> tuple[int, int, int, int]:
        nonlocal inspection_calls
        inspection_calls += 1
        if inspection_calls == 4:
            raise planner_module._LauncherHandleInspectionError(5)
        return real_info(value)

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0),
    )
    monkeypatch.setattr(planner_module, "_launcher_handle_info", fail_fourth_inspection)

    with pytest.raises(LaunchError, match="inspect exact launcher handle"):
        launch_cli(layout=receipt.layout)
    assert inspection_calls == 4
    assert len(planner_module._PENDING_LAUNCHER_CLAIMS) == 1
    pending = planner_module._PENDING_LAUNCHER_CLAIMS[0]
    assert pending.value is not None and pending.protected

    monkeypatch.setattr(planner_module, "_launcher_handle_info", real_info)
    planner_module._drain_launcher_claims()
    assert planner_module._PENDING_LAUNCHER_CLAIMS == []


@pytest.mark.skipif(os.name != "nt", reason="Windows exact handle cleanup")
def test_cleanup_inspection_base_exception_retains_the_protected_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    make_build(source)
    receipt = stage_latest_development(source, tmp_path / "local", identity())
    real_info = planner_module._launcher_handle_info
    inspection_calls = 0

    def interrupt_fourth_inspection(value: int) -> tuple[int, int, int, int]:
        nonlocal inspection_calls
        inspection_calls += 1
        if inspection_calls == 4:
            raise KeyboardInterrupt
        return real_info(value)

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0),
    )
    monkeypatch.setattr(planner_module, "_launcher_handle_info", interrupt_fourth_inspection)

    with pytest.raises(KeyboardInterrupt):
        launch_cli(layout=receipt.layout)
    assert inspection_calls == 4
    assert len(planner_module._PENDING_LAUNCHER_CLAIMS) == 1
    pending = planner_module._PENDING_LAUNCHER_CLAIMS[0]
    assert pending.value is not None and pending.protected

    monkeypatch.setattr(planner_module, "_launcher_handle_info", real_info)
    planner_module._drain_launcher_claims()
    assert planner_module._PENDING_LAUNCHER_CLAIMS == []


@pytest.mark.skipif(os.name != "nt", reason="Windows exact handle cleanup")
def test_cleanup_base_exception_after_unprotect_never_closes_a_foreign_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    source = tmp_path / "source"
    make_build(source)
    receipt = stage_latest_development(source, tmp_path / "local", identity())
    replacement = tmp_path / "replacement.exe"
    replacement.write_bytes(b"replacement executable")
    launcher_bytes = receipt.layout.launcher.read_bytes()
    real_protect = planner_module._windows_set_launcher_protection
    real_close = planner_module._windows_close_launcher
    interrupted = False
    foreign: BinaryIO | None = None
    other_values: list[int] = []
    kernel32 = planner_module._windows_kernel32()
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE

    def interrupt_after_unprotect(value: int, protected: bool) -> None:
        nonlocal foreign, interrupted
        real_protect(value, protected)
        if not protected and not interrupted:
            interrupted = True
            assert real_close(value)
            for _ in range(256):
                raw = create_file(
                    os.fspath(receipt.layout.launcher),
                    planner_module._GENERIC_READ,
                    planner_module._FILE_SHARE_READ,
                    None,
                    planner_module._OPEN_EXISTING,
                    planner_module._FILE_ATTRIBUTE_NORMAL,
                    None,
                )
                candidate_value = ctypes.cast(raw, ctypes.c_void_p).value
                assert candidate_value is not None
                if candidate_value == value:
                    descriptor = msvcrt.open_osfhandle(
                        candidate_value,
                        os.O_RDONLY | os.O_BINARY,
                    )
                    foreign = os.fdopen(descriptor, "rb")
                    break
                other_values.append(candidate_value)
            assert foreign is not None, "Windows did not reuse the interrupted handle value"
            raise KeyboardInterrupt

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0),
    )
    monkeypatch.setattr(
        planner_module,
        "_windows_set_launcher_protection",
        interrupt_after_unprotect,
    )

    with pytest.raises(KeyboardInterrupt):
        launch_cli(layout=receipt.layout)
    assert planner_module._PENDING_LAUNCHER_CLAIMS == []
    assert len(planner_module._INDETERMINATE_LAUNCHER_CLAIMS) == 1
    indeterminate = planner_module._INDETERMINATE_LAUNCHER_CLAIMS[0]
    assert indeterminate.value is None and indeterminate.indeterminate
    monkeypatch.setattr(planner_module, "_windows_set_launcher_protection", real_protect)
    for other_value in other_values:
        assert real_close(other_value)
    assert foreign is not None
    try:
        planner_module._drain_launcher_claims()
        assert foreign.read() == launcher_bytes
    finally:
        foreign.close()
        planner_module._INDETERMINATE_LAUNCHER_CLAIMS.remove(indeterminate)
    os.replace(replacement, receipt.layout.launcher)


@pytest.mark.skipif(os.name != "nt", reason="Windows exact handle cleanup")
def test_launcher_acquisition_base_exception_releases_the_exact_handle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    make_build(source)
    receipt = stage_latest_development(source, tmp_path / "local", identity())
    replacement = tmp_path / "replacement.exe"
    replacement.write_bytes(b"replacement executable")

    def interrupt_final_path(_value: int) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr(planner_module, "_launcher_final_path", interrupt_final_path)

    with pytest.raises(KeyboardInterrupt):
        launch_cli(layout=receipt.layout)

    assert planner_module._PENDING_LAUNCHER_CLAIMS == []
    os.replace(replacement, receipt.layout.launcher)


@pytest.mark.skipif(os.name != "nt", reason="Windows native handle reuse")
def test_ambiguous_close_never_retries_a_same_file_foreign_handle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    source = tmp_path / "source"
    make_build(source)
    receipt = stage_latest_development(source, tmp_path / "local", identity())
    real_close = planner_module._windows_close_launcher
    close_calls = 0
    closed_value: int | None = None
    launcher_bytes = receipt.layout.launcher.read_bytes()
    foreign: BinaryIO | None = None
    other_values: list[int] = []
    kernel32 = planner_module._windows_kernel32()
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE

    def close_but_report_failure(value: int) -> bool:
        nonlocal close_calls, closed_value, foreign
        close_calls += 1
        closed_value = value
        assert real_close(value)
        for _ in range(256):
            raw = create_file(
                os.fspath(receipt.layout.launcher),
                planner_module._GENERIC_READ,
                planner_module._FILE_SHARE_READ,
                None,
                planner_module._OPEN_EXISTING,
                planner_module._FILE_ATTRIBUTE_NORMAL,
                None,
            )
            candidate_value = ctypes.cast(raw, ctypes.c_void_p).value
            assert candidate_value is not None
            if candidate_value == value:
                descriptor = msvcrt.open_osfhandle(
                    candidate_value,
                    os.O_RDONLY | os.O_BINARY,
                )
                foreign = os.fdopen(descriptor, "rb")
                break
            other_values.append(candidate_value)
        assert foreign is not None, "Windows did not reuse the ambiguous native handle value"
        return False

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0),
    )
    monkeypatch.setattr(
        planner_module,
        "_windows_close_launcher",
        close_but_report_failure,
    )

    with pytest.raises(LaunchError, match="close exact launcher handle"):
        launch_cli(layout=receipt.layout)
    assert planner_module._PENDING_LAUNCHER_CLAIMS == []
    assert len(planner_module._INDETERMINATE_LAUNCHER_CLAIMS) == 1
    indeterminate = planner_module._INDETERMINATE_LAUNCHER_CLAIMS[0]
    stale_value = indeterminate.last_native_value
    assert stale_value is not None
    assert indeterminate.value is None and indeterminate.indeterminate
    monkeypatch.setattr(planner_module, "_windows_close_launcher", real_close)
    for other_value in other_values:
        assert real_close(other_value)
    assert foreign is not None
    assert planner_module._launcher_handle_info(stale_value)[:2] == indeterminate.identity
    try:
        planner_module._drain_launcher_claims()
        assert planner_module._PENDING_LAUNCHER_CLAIMS == []
        assert foreign.read() == launcher_bytes
    finally:
        foreign.close()
        planner_module._INDETERMINATE_LAUNCHER_CLAIMS.remove(indeterminate)
    assert close_calls == 1


def test_stage_rejects_mismatched_claimed_payload_sha256_before_publish(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    local = tmp_path / "local"
    make_build(source)
    claimed = identity()
    claimed = BuildIdentity(
        commit_sha=claimed.commit_sha,
        build_id=claimed.build_id,
        version=claimed.version,
        payload_sha256="0" * 64,
    )

    with pytest.raises(DeploymentError, match="claimed payload digest"):
        stage_latest_development(source, local, claimed)

    assert not (local / "FEBioCaeWorkbench" / "latest-development").exists()


def test_payload_sha256_is_deterministic_and_changes_for_arbitrary_bytes(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    make_build(first)
    make_build(second)

    assert compute_payload_sha256(first) == compute_payload_sha256(second)
    (second / "README.txt").write_bytes(b"arbitrary bytes")
    assert compute_payload_sha256(first) != compute_payload_sha256(second)


def test_deployment_lock_is_cross_process_and_releases_on_owner_exit(tmp_path: Path) -> None:
    layout = DeploymentLayout.from_local_app_data(tmp_path / "local")
    script = """
import sys
import time
from pathlib import Path
from febio_cae_harness.launch import DeploymentLayout, deployment_lock

layout = DeploymentLayout.from_local_app_data(Path(sys.argv[1]))
with deployment_lock(layout):
    print("held", flush=True)
    time.sleep(2)
"""
    environment = os.environ.copy()
    source_root = str(Path(__file__).resolve().parents[2] / "src")
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        source_root
        if not existing_pythonpath
        else f"{source_root}{os.pathsep}{existing_pythonpath}"
    )
    owner = subprocess.Popen(
        [sys.executable, "-c", script, str(layout.local_app_data)],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert owner.stdout is not None
        assert owner.stdout.readline().strip() == "held"
        contender = subprocess.Popen(
            [sys.executable, "-c", script, str(layout.local_app_data)],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            time.sleep(0.2)
            assert contender.poll() is None
            owner.terminate()
            assert owner.wait(timeout=5) is not None
            assert contender.wait(timeout=5) == 0
        finally:
            if contender.poll() is None:
                contender.kill()
                contender.wait(timeout=5)
            if contender.stdout is not None:
                contender.stdout.close()
            if contender.stderr is not None:
                contender.stderr.close()
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=5)
        if owner.stdout is not None:
            owner.stdout.close()
        if owner.stderr is not None:
            owner.stderr.close()
