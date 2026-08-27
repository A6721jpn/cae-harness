from __future__ import annotations

import ctypes
import json
import os
import stat
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
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


class _Guid(ctypes.Structure):
    _fields_ = (
        ("data1", ctypes.c_uint32),
        ("data2", ctypes.c_uint16),
        ("data3", ctypes.c_uint16),
        ("data4", ctypes.c_ubyte * 8),
    )


_FOLDERID_PROGRAMS = _Guid(
    0xA77F5D77,
    0x2E2B,
    0x44C3,
    (ctypes.c_ubyte * 8)(0xA6, 0xA2, 0xAB, 0xA6, 0x01, 0x05, 0x4A, 0x51),
)

_FILE_ATTRIBUTE_DIRECTORY, _FILE_ATTRIBUTE_REPARSE_POINT = 0x10, 0x400
_DELETE, _FILE_READ_ATTRIBUTES, _SYNCHRONIZE = 0x00010000, 0x80, 0x00100000
_FILE_FLAG_BACKUP_SEMANTICS, _FILE_FLAG_OPEN_REPARSE_POINT = 0x02000000, 0x00200000
_FILE_SHARE_READ, _FILE_SHARE_WRITE, _OPEN_EXISTING = 0x1, 0x2, 3


class _ByHandleFileInformation(ctypes.Structure):
    _fields_ = (("values", ctypes.c_uint32 * 13),)


def _handle_directory_identity(handle: int, label: str) -> tuple[int, int, int]:
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_info = kernel32.GetFileInformationByHandle
        get_info.argtypes = (ctypes.c_void_p, ctypes.POINTER(_ByHandleFileInformation))
        get_info.restype = ctypes.c_int
        info = _ByHandleFileInformation()
        if not get_info(ctypes.c_void_p(handle), ctypes.byref(info)):
            raise DeploymentError(f"cannot inspect {label} handle")
    except (AttributeError, OSError, TypeError, ValueError) as error:
        raise DeploymentError(f"cannot inspect {label} handle") from error
    if not info.values[0] & _FILE_ATTRIBUTE_DIRECTORY:
        raise DeploymentError(f"{label} handle is not a directory")
    if info.values[0] & _FILE_ATTRIBUTE_REPARSE_POINT:
        raise DeploymentError(f"{label} handle is a reparse point")
    return int(info.values[7]), int(info.values[11]), int(info.values[12])


def _close_directory_handle(handle: int, label: str) -> None:
    try:
        close = ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle
        if not close(ctypes.c_void_p(handle)):
            raise DeploymentError(f"cannot close {label} handle")
    except (AttributeError, OSError, TypeError, ValueError) as error:
        raise DeploymentError(f"cannot close {label} handle") from error


@contextmanager
def _directory_handle(path: Path, label: str) -> Iterator[tuple[int, tuple[int, int, int]]]:
    if os.name != "nt":
        raise DeploymentError("Windows directory handles are required for shortcut authority")
    handle: int | None = None
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.restype = ctypes.c_void_p
        raw_handle = create_file(
            ctypes.c_wchar_p(os.fspath(path)),
            _DELETE | _FILE_READ_ATTRIBUTES | _SYNCHRONIZE,
            _FILE_SHARE_READ | _FILE_SHARE_WRITE,
            None,
            _OPEN_EXISTING,
            _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        handle_value = ctypes.cast(raw_handle, ctypes.c_void_p).value
        if handle_value is None or handle_value == ctypes.c_void_p(-1).value:
            raise DeploymentError(f"cannot open {label} handle")
        handle = int(handle_value)
    except (AttributeError, OSError, TypeError, ValueError) as error:
        raise DeploymentError(f"cannot open {label} handle") from error
    try:
        yield handle, _handle_directory_identity(handle, label)
    finally:
        _close_directory_handle(handle, label)


def _known_folder_programs() -> Path:
    if os.name != "nt":
        raise DeploymentError("Start Menu Programs is available only on Windows")
    allocated = ctypes.c_wchar_p()
    try:
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        ole32 = ctypes.WinDLL("ole32", use_last_error=True)
        get_path = shell32.SHGetKnownFolderPath
        get_path.restype = ctypes.c_long
        result = get_path(ctypes.byref(_FOLDERID_PROGRAMS), 0, None, ctypes.byref(allocated))
        if result != 0 or not allocated.value:
            raise DeploymentError("Windows Known Folder Programs resolution failed")
        return Path(allocated.value)
    except (AttributeError, OSError, TypeError, ValueError) as error:
        raise DeploymentError("Windows Known Folder Programs resolution failed") from error
    finally:
        if allocated.value:
            ole32.CoTaskMemFree(ctypes.cast(allocated, ctypes.c_void_p))


def _directory_identity(value: str | Path, label: str) -> tuple[Path, tuple[int, int]]:
    try:
        path = Path(os.fspath(value))
    except (TypeError, ValueError) as error:
        raise DeploymentError(f"{label} is invalid") from error
    if not path.is_absolute():
        raise DeploymentError(f"{label} must be an absolute path")
    path = _reject_reparse_alias(path, label)
    if path in (Path(path.anchor), Path.cwd()):
        raise DeploymentError(f"{label} is unsafe: {path}")
    try:
        metadata = os.stat(os.fspath(path), follow_symlinks=False)
    except OSError as error:
        raise DeploymentError(f"{label} is unavailable: {path}") from error
    if not stat.S_ISDIR(metadata.st_mode):
        raise DeploymentError(f"{label} must be a directory: {path}")
    return path, (int(metadata.st_dev), int(metadata.st_ino))


def _authoritative_start_menu_root() -> tuple[Path, tuple[int, int]]:
    if os.name != "nt":
        raise DeploymentError("Start Menu Programs is available only on Windows")
    return _directory_identity(_known_folder_programs(), "Start Menu output root")


def default_start_menu_root(environ: Mapping[str, str] | None = None) -> Path:
    """Return the current user's authoritative Start Menu Programs directory."""

    if environ is not None:
        raise DeploymentError("environment overrides cannot select the Start Menu root")
    return _authoritative_start_menu_root()[0]


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
    start_menu_root: Path
    root_identity: tuple[int, int]
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


def _assert_live_start_menu_root(record: _ManagerRecord) -> None:
    current, identity = _authoritative_start_menu_root()
    if current != record.start_menu_root or identity != record.root_identity:
        raise DeploymentError("Start Menu Programs root changed after manager creation")


def _assert_directory_authority(
    entries: Sequence[tuple[Path, tuple[int, int], int, tuple[int, int, int], str]],
) -> None:
    for path, path_identity, handle, handle_identity, label in entries:
        if _directory_identity(path, label)[1] != path_identity:
            raise DeploymentError(f"{label} identity changed while writing")
        if _handle_directory_identity(handle, label) != handle_identity:
            raise DeploymentError(f"{label} handle identity changed while writing")


@contextmanager
def _output_authority(record: _ManagerRecord) -> Iterator[None]:
    if os.name != "nt":
        raise DeploymentError("Windows directory handles are required for shortcut authority")
    _assert_live_start_menu_root(record)
    root = _reject_reparse_alias(record.start_menu_root, "Start Menu output root")
    _, root_path_identity = _directory_identity(root, "Start Menu output root")
    if root_path_identity != record.root_identity:
        raise DeploymentError("Start Menu Programs root changed after manager creation")
    with _directory_handle(root, "Start Menu output root") as root_open:
        root_entry = (root, root_path_identity, *root_open, "Start Menu output root")
        _assert_directory_authority((root_entry,))
        product = root / PRODUCT_DIRECTORY_NAME
        _reject_reparse_alias(product, "Start Menu product directory")
        if not os.path.lexists(os.fspath(product)):
            try:
                product.mkdir()
            except FileExistsError:
                pass
            except OSError as error:
                raise DeploymentError("cannot create Start Menu product directory") from error
        _, product_path_identity = _directory_identity(product, "Start Menu product directory")
        with _directory_handle(product, "Start Menu product directory") as product_open:
            entries = (
                root_entry,
                (product, product_path_identity, *product_open, "Start Menu product directory"),
            )
            _assert_directory_authority(entries)
            try:
                yield
            finally:
                _assert_live_start_menu_root(record)
                _assert_directory_authority(entries)


def _live(record: _ManagerRecord) -> tuple[BuildIdentity, tuple[int, ...], tuple[int, ...]]:
    if _layout_paths(record.layout) != record.paths:
        raise DeploymentError("deployment layout changed")
    _assert_live_start_menu_root(record)
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
    manager = _manager(record.manager)
    _assert_live_start_menu_root(manager)
    expected = manager.start_menu_root / PRODUCT_DIRECTORY_NAME / SHORTCUT_DESCRIPTOR_NAME
    if record.path != expected:
        raise DeploymentError("shortcut descriptor path is not the approved product path")
    _reject_reparse_alias(record.path.parent, "Start Menu product directory")
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
        root, root_identity = _directory_identity(fixed, "Start Menu output root")
        _MANAGER_REGISTRY[id(self)] = _ManagerRecord(
            self,
            DeploymentLayout.from_local_app_data(paths[0]),
            paths,
            root,
            root_identity,
            root / PRODUCT_DIRECTORY_NAME / SHORTCUT_DESCRIPTOR_NAME,
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
        with _output_authority(_manager(self)):
            _output(record)
            _write_json_atomic(record.path, expected)
            _check_written(record, expected)
        return record.path

    def verify(self, descriptor: ShortcutDescriptor) -> Path:
        record = _require(descriptor, self)
        _issued_live(record)
        with _output_authority(_manager(self)):
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
