from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from febio_cae_harness.launch import (
    LAUNCHER_NAME,
    BuildIdentity,
    DeploymentError,
    DeploymentLayout,
    DeploymentRollbackError,
    LaunchError,
    compute_payload_sha256,
    fixed_shortcut_descriptor,
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
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=5)
