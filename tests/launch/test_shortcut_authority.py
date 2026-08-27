from __future__ import annotations

import copy
import json
import os
import pickle
import subprocess
import tempfile
from contextlib import suppress
from pathlib import Path

import pytest

import febio_cae_harness.launch.shortcut as shortcut_module
from febio_cae_harness.launch.deployment import (
    PRODUCT_DIRECTORY_NAME,
    BuildIdentity,
    DeploymentError,
    DeploymentLayout,
    stage_latest_development,
)
from febio_cae_harness.launch.shortcut import (
    SHORTCUT_DESCRIPTOR_NAME,
    ShortcutDescriptor,
    ShortcutDescriptorManager,
    default_start_menu_root,
    fixed_shortcut_descriptor,
    write_shortcut_descriptor,
)


def _published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[DeploymentLayout, ShortcutDescriptorManager]:
    programs = tmp_path / "programs"
    programs.mkdir(parents=True)
    monkeypatch.setattr(shortcut_module, "_known_folder_programs", lambda: programs)
    source = tmp_path / "source"
    source.mkdir(parents=True)
    (source / "febio-cae.exe").write_bytes(b"synthetic launcher")
    stage_latest_development(
        source,
        local_app_data=tmp_path / "local",
        identity=BuildIdentity(commit_sha="commit", build_id="build", version="0.1"),
    )
    layout = DeploymentLayout.from_local_app_data(tmp_path / "local")
    return layout, ShortcutDescriptorManager(layout)


def test_public_constructor_and_forgery_paths_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout, manager = _published(tmp_path, monkeypatch)
    with pytest.raises(TypeError):
        ShortcutDescriptor(path=tmp_path / "x", target=layout.launcher)  # type: ignore[call-arg]
    forged = object.__new__(ShortcutDescriptor)
    forged_manager = object.__new__(ShortcutDescriptorManager)
    with pytest.raises(TypeError):
        manager.write(forged)
    with pytest.raises(TypeError):
        forged_manager.issue_descriptor()
    descriptor = manager.issue_descriptor()
    for operation in (copy.copy, copy.deepcopy, pickle.dumps):
        with pytest.raises(TypeError):
            operation(descriptor)
    with pytest.raises(TypeError):

        class DescriptorChild(ShortcutDescriptor):
            pass

    with pytest.raises(AttributeError):
        setattr(descriptor, "path", tmp_path / "forged.json")  # noqa: B010


def test_manager_issues_only_the_fixed_live_descriptor_and_writes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout, manager = _published(tmp_path, monkeypatch)
    descriptor = manager.issue_descriptor(("--headless",), metadata={"origin": "test"})
    assert type(descriptor) is ShortcutDescriptor
    assert (
        descriptor.path
        == default_start_menu_root() / PRODUCT_DIRECTORY_NAME / SHORTCUT_DESCRIPTOR_NAME
    )
    assert descriptor.target == layout.launcher
    assert descriptor.working_directory == layout.latest
    assert descriptor.fixed_target and descriptor.arguments == ("--headless",)
    assert not descriptor.path.exists()
    path = manager.write(descriptor)
    assert path == descriptor.path
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["target"] == str(layout.launcher)
    assert payload["build_identity"]["build_id"] == "build"
    assert manager.verify(descriptor) == path
    assert write_shortcut_descriptor(descriptor) == path
    assert manager.verify(descriptor) == path


def test_caller_environment_cannot_choose_start_menu_programs_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout, _manager = _published(tmp_path, monkeypatch)
    attacker_root = tmp_path / "attacker"
    attacker_root.mkdir()

    with pytest.raises(DeploymentError):
        ShortcutDescriptorManager(layout, environ={"APPDATA": str(attacker_root)})


def test_foreign_manager_and_arbitrary_output_root_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout, manager = _published(tmp_path, monkeypatch)
    with pytest.raises(DeploymentError):
        ShortcutDescriptorManager(layout, start_menu_root=tmp_path / "arbitrary")
    with pytest.raises(DeploymentError):
        fixed_shortcut_descriptor(layout, start_menu_root=tmp_path / "arbitrary")
    descriptor = manager.issue_descriptor()
    foreign = ShortcutDescriptorManager(layout)
    with pytest.raises(TypeError):
        foreign.write(descriptor)
    with pytest.raises(TypeError):
        foreign.verify(descriptor)


def test_replaced_launcher_and_identity_invalidate_issued_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout, manager = _published(tmp_path, monkeypatch)
    launcher_descriptor = manager.issue_descriptor()
    replacement = tmp_path / "replacement.exe"
    replacement.write_bytes(layout.launcher.read_bytes())
    os.replace(replacement, layout.launcher)
    with pytest.raises(DeploymentError):
        manager.write(launcher_descriptor)

    layout, manager = _published(tmp_path / "identity", monkeypatch)
    identity_descriptor = manager.issue_descriptor()
    replacement = tmp_path / "identity-replacement.json"
    replacement.write_text(layout.identity_path.read_text(encoding="utf-8"), encoding="utf-8")
    os.replace(replacement, layout.identity_path)
    with pytest.raises(DeploymentError):
        manager.write(identity_descriptor)


def test_hardlinked_launcher_and_descriptor_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout, manager = _published(tmp_path, monkeypatch)
    descriptor = manager.issue_descriptor()
    outside = tmp_path / "launcher-hardlink.exe"
    os.link(layout.launcher, outside)
    with pytest.raises(DeploymentError):
        manager.write(descriptor)

    layout, manager = _published(tmp_path / "output", monkeypatch)
    descriptor = manager.issue_descriptor()
    manager.write(descriptor)
    outside = tmp_path / "descriptor-hardlink.json"
    os.link(descriptor.path, outside)
    with pytest.raises(DeploymentError):
        manager.verify(descriptor)


def test_changed_persisted_descriptor_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _layout, manager = _published(tmp_path, monkeypatch)
    descriptor = manager.issue_descriptor()
    manager.write(descriptor)
    payload = json.loads(descriptor.path.read_text(encoding="utf-8"))
    payload["target"] = str(tmp_path / "arbitrary.exe")
    descriptor.path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DeploymentError):
        manager.verify(descriptor)


def test_write_holds_product_directory_authority_before_temp_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout, manager = _published(tmp_path, monkeypatch)
    descriptor = manager.issue_descriptor()
    programs = default_start_menu_root()
    product = programs / PRODUCT_DIRECTORY_NAME
    product.mkdir()
    displaced = tmp_path / "displaced-product"
    outside = tmp_path / "outside"
    outside.mkdir()
    writer_called = False
    swap_succeeded = False

    def swap_then_write(path: Path, payload: dict[str, object]) -> None:
        nonlocal swap_succeeded, writer_called
        writer_called = True
        try:
            os.replace(product, displaced)
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(product), str(outside)],
                check=True,
                capture_output=True,
                text=True,
            )
            swap_succeeded = True
            descriptor_fd, temporary_name = tempfile.mkstemp(
                prefix=f".{path.name}.", dir=os.fspath(path.parent)
            )
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor_fd, "w", encoding="utf-8", newline="\n") as stream:
                    json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        except OSError:
            manager_writer(descriptor.path, payload)
        finally:
            if swap_succeeded:
                subprocess.run(["cmd", "/c", "rmdir", str(product)], check=True)
                os.replace(displaced, product)

    manager_writer = shortcut_module._write_json_atomic  # type: ignore[attr-defined]
    monkeypatch.setattr(shortcut_module, "_write_json_atomic", swap_then_write)

    with suppress(DeploymentError):
        manager.write(descriptor)
    assert writer_called
    assert not swap_succeeded
    assert not any(outside.iterdir())
