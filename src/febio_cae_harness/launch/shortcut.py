from __future__ import annotations

import json
import os
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .deployment import (
    BUILD_IDENTITY_NAME,
    LATEST_DIRECTORY_NAME,
    LAUNCHER_NAME,
    PRODUCT_DIRECTORY_NAME,
    BuildIdentity,
    DeploymentError,
    DeploymentLayout,
    _reject_reparse_alias,
    _write_json_atomic,
    deployment_lock,
    verify_payload_identity,
)

SHORTCUT_DESCRIPTOR_NAME = "FEBio CAE Workbench.shortcut.json"
START_MENU_RELATIVE_PATH = Path("Microsoft") / "Windows" / "Start Menu" / "Programs"
SHORTCUT_DISPLAY_NAME = "FEBio CAE Workbench"
_TOKEN = object()


def default_start_menu_root(environ: Mapping[str, str] | None = None) -> Path:
    """Return the per-user Start Menu Programs directory without creating it."""

    values = os.environ if environ is None else environ
    app_data = values.get("APPDATA")
    if not app_data:
        raise DeploymentError("APPDATA is not set")
    return Path(app_data) / START_MENU_RELATIVE_PATH


def _stamp(path: Path, label: str) -> tuple[int, ...]:
    safe = _reject_reparse_alias(path, label)
    try:
        metadata = os.stat(os.fspath(safe), follow_symlinks=False)
    except OSError as error:
        raise DeploymentError(f"{label} is unavailable: {safe}") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise DeploymentError(f"{label} has the wrong type: {safe}")
    links = int(getattr(metadata, "st_nlink", 1))
    if links > 1:
        raise DeploymentError(f"{label} must not be hard-linked: {safe}")
    return tuple(
        int(getattr(metadata, name))
        for name in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink")
    )


@dataclass(frozen=True, slots=True)
class _ManagerRecord:
    manager: ShortcutManager
    layout: DeploymentLayout
    paths: tuple[Path, ...]
    output_path: Path


@dataclass(frozen=True, slots=True)
class _DescriptorRecord:
    descriptor: ShortcutDescriptor
    manager: ShortcutManager
    path: Path
    target: Path
    arguments: tuple[str, ...]
    metadata: Mapping[str, str]
    identity: BuildIdentity
    launcher_stamp: tuple[int, ...]
    identity_stamp: tuple[int, ...]


_MANAGER_REGISTRY: dict[int, _ManagerRecord] = {}
_DESCRIPTOR_REGISTRY: dict[int, _DescriptorRecord] = {}
_PLANNING_DESCRIPTORS: dict[str, ShortcutDescriptor] = {}
_PLANNING_IDENTITY = BuildIdentity("planning", "planning", "planning")


def _reject_opaque(*_args: object) -> Any:
    raise TypeError("opaque shortcut capabilities cannot be copied or serialized")


class _Opaque:
    __slots__ = ()
    __copy__ = __deepcopy__ = __reduce__ = __reduce_ex__ = _reject_opaque


class ShortcutDescriptor(_Opaque):
    __slots__ = ("__weakref__",)
    display_name = SHORTCUT_DISPLAY_NAME
    description = "Launch the FEBio CAE Harness headlessly"
    fixed_target = True
    __init_subclass__ = _reject_opaque

    def __new__(cls, token: object | None = None) -> ShortcutDescriptor:
        if cls is not ShortcutDescriptor or token is not _TOKEN:
            raise TypeError("ShortcutDescriptor instances are manager-issued")
        return super().__new__(cls)

    def __getattribute__(self, name: str) -> Any:
        if name in {"path", "target", "arguments", "metadata"}:
            return getattr(_descriptor(self), name)
        if name == "working_directory":
            return _descriptor(self).target.parent
        if name == "build_identity":
            return _descriptor(self).identity
        return super().__getattribute__(name)

    def to_dict(self) -> dict[str, object]:
        return _payload(_descriptor(self))

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> ShortcutDescriptor:
        del cls
        value = _PLANNING_DESCRIPTORS.get(json.dumps(payload, sort_keys=True))
        if value is None:
            raise TypeError("ShortcutDescriptor instances are manager-issued")
        return value


def _layout_paths(layout: DeploymentLayout) -> tuple[Path, ...]:
    if type(layout) is not DeploymentLayout:
        raise TypeError("layout must be an exact DeploymentLayout instance")
    local = Path(os.path.abspath(os.fspath(layout.local_app_data)))
    latest = local / PRODUCT_DIRECTORY_NAME / LATEST_DIRECTORY_NAME
    return (local, latest, latest / LAUNCHER_NAME, latest / BUILD_IDENTITY_NAME)


def _manager(value: object) -> _ManagerRecord:
    if type(value) is not ShortcutManager:
        raise TypeError("value is not an exact ShortcutManager instance")
    record = _MANAGER_REGISTRY.get(id(value))
    if record is None or record.manager is not value:
        raise TypeError("ShortcutManager is not registry-issued")
    return record


def _descriptor(value: object) -> _DescriptorRecord:
    if type(value) is not ShortcutDescriptor:
        raise TypeError("value is not an exact ShortcutDescriptor instance")
    record = _DESCRIPTOR_REGISTRY.get(id(value))
    if record is None or record.descriptor is not value:
        raise TypeError("ShortcutDescriptor was not issued by a manager")
    return record


def _read_identity(record: _ManagerRecord) -> BuildIdentity:
    try:
        payload = json.loads(record.paths[3].read_text(encoding="utf-8"))
        identity = BuildIdentity.from_mapping(payload)
        return verify_payload_identity(record.paths[1], identity)
    except (OSError, TypeError, ValueError, DeploymentError) as error:
        raise DeploymentError("published launcher identity is invalid") from error


def _live(record: _ManagerRecord) -> tuple[BuildIdentity, tuple[int, ...], tuple[int, ...]]:
    if _layout_paths(record.layout) != record.paths:
        raise DeploymentError("deployment layout changed")
    with deployment_lock(record.layout):
        launcher = _stamp(record.paths[2], "latest-development launcher")
        identity_file = _stamp(record.paths[3], "build identity")
        identity = _read_identity(record)
    return identity, launcher, identity_file


def _normalise_arguments(arguments: Sequence[str]) -> tuple[str, ...]:
    normalised = tuple(arguments)
    if any(not isinstance(argument, str) for argument in normalised):
        raise TypeError("shortcut arguments must be strings")
    if any("\x00" in argument for argument in normalised):
        raise ValueError("shortcut arguments cannot contain NUL")
    return normalised


def _issued_live(record: _DescriptorRecord) -> None:
    current = _live(_manager(record.manager))
    expected = (record.identity, record.launcher_stamp, record.identity_stamp)
    if current != expected:
        raise DeploymentError("launcher or build identity changed after issuance")


def _payload(record: _DescriptorRecord) -> dict[str, object]:
    return {
        "arguments": list(record.arguments),
        "build_identity": record.identity.to_dict(),
        "description": "Launch the FEBio CAE Harness headlessly",
        "display_name": SHORTCUT_DISPLAY_NAME,
        "fixed_target": True,
        "metadata": dict(record.metadata),
        "path": str(record.path),
        "schema_version": 2,
        "target": str(record.target),
        "working_directory": str(record.target.parent),
    }


def _output(record: _DescriptorRecord) -> None:
    _manager(record.manager)
    _reject_reparse_alias(record.path.parent, "Start Menu output root")
    if os.path.lexists(os.fspath(record.path)):
        _stamp(record.path, "shortcut descriptor")


def _check_written(record: _DescriptorRecord, expected: Mapping[str, object]) -> None:
    _output(record)
    try:
        actual = json.loads(record.path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DeploymentError("shortcut descriptor cannot be read") from error
    if actual != dict(expected):
        raise DeploymentError("shortcut descriptor contents do not match issuance")


def _issue(
    manager: ShortcutManager,
    path: Path,
    target: Path,
    arguments: tuple[str, ...],
    metadata: Mapping[str, str],
    identity: BuildIdentity = _PLANNING_IDENTITY,
    stamps: tuple[tuple[int, ...], tuple[int, ...]] = ((), ()),
) -> ShortcutDescriptor:
    descriptor = ShortcutDescriptor(_TOKEN)
    _DESCRIPTOR_REGISTRY[id(descriptor)] = _DescriptorRecord(
        descriptor, manager, path, target, arguments, metadata, identity, *stamps
    )
    return descriptor


class ShortcutManager(_Opaque):
    __slots__ = ("__weakref__",)
    __init_subclass__ = _reject_opaque

    def __init__(
        self,
        layout: DeploymentLayout,
        start_menu_root: str | Path | None = None,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        paths = _layout_paths(layout)
        fixed = default_start_menu_root(environ)
        supplied = (
            None if start_menu_root is None else Path(os.path.abspath(os.fspath(start_menu_root)))
        )
        if supplied is not None and supplied != fixed:
            raise DeploymentError("shortcut output root must be the fixed Start Menu Programs path")
        root = _reject_reparse_alias(fixed, "Start Menu output root")
        _MANAGER_REGISTRY[id(self)] = _ManagerRecord(
            self,
            DeploymentLayout.from_local_app_data(paths[0]),
            paths,
            root / SHORTCUT_DESCRIPTOR_NAME,
        )

    def issue_descriptor(
        self, arguments: Sequence[str] = (), *, metadata: Mapping[str, str] | None = None
    ) -> ShortcutDescriptor:
        manager = _manager(self)
        args = _normalise_arguments(arguments)
        values = MappingProxyType({} if metadata is None else dict(metadata))
        identity, launcher, identity_file = _live(manager)
        return _issue(
            self,
            manager.output_path,
            manager.paths[2],
            args,
            values,
            identity,
            (launcher, identity_file),
        )

    def write(self, descriptor: ShortcutDescriptor) -> Path:
        record = _require(descriptor, self)
        _issued_live(record)
        expected = _payload(record)
        _output(record)
        _write_json_atomic(record.path, expected)
        _check_written(record, expected)
        return record.path

    def verify(self, descriptor: ShortcutDescriptor) -> Path:
        record = _require(descriptor, self)
        _issued_live(record)
        _check_written(record, _payload(record))
        return record.path


def _require(value: object, manager: ShortcutManager) -> _DescriptorRecord:
    record = _descriptor(value)
    if _manager(manager).manager is not record.manager:
        raise TypeError("shortcut descriptor does not belong to this manager")
    return record


def _planning_descriptor(
    layout: DeploymentLayout, root: Path, arguments: Sequence[str], metadata: Mapping[str, str]
) -> ShortcutDescriptor:
    paths = _layout_paths(layout)
    manager = object.__new__(ShortcutManager)
    output = root / SHORTCUT_DESCRIPTOR_NAME
    descriptor = _issue(manager, output, paths[2], tuple(arguments), metadata)
    _PLANNING_DESCRIPTORS[json.dumps(descriptor.to_dict(), sort_keys=True)] = descriptor
    return descriptor


ShortcutDescriptorManager = ShortcutManager


def fixed_shortcut_descriptor(
    layout: DeploymentLayout,
    start_menu_root: str | Path | None = None,
    arguments: Sequence[str] = (),
    *,
    metadata: Mapping[str, str] | None = None,
) -> ShortcutDescriptor:
    if start_menu_root is not None and not any(
        os.path.lexists(os.fspath(path)) for path in (layout.launcher, layout.identity_path)
    ):
        return _planning_descriptor(
            layout,
            Path(os.path.abspath(os.fspath(start_menu_root))),
            arguments,
            {} if metadata is None else metadata,
        )
    manager = ShortcutManager(layout, start_menu_root=start_menu_root)
    return manager.issue_descriptor(arguments, metadata=metadata)


def write_shortcut_descriptor(
    descriptor: ShortcutDescriptor, *, manager: ShortcutManager | None = None
) -> Path:
    if manager is None:
        manager = _descriptor(descriptor).manager
    return manager.write(descriptor)


shortcut_descriptor = fixed_shortcut_descriptor


__all__ = [
    "SHORTCUT_DESCRIPTOR_NAME",
    "SHORTCUT_DISPLAY_NAME",
    "START_MENU_RELATIVE_PATH",
    "ShortcutDescriptor",
    "ShortcutDescriptorManager",
    "ShortcutManager",
    "default_start_menu_root",
    "fixed_shortcut_descriptor",
    "shortcut_descriptor",
    "write_shortcut_descriptor",
]
