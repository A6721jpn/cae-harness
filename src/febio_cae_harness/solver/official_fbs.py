"""Pinned out-of-process adapter for the official FEBio Studio FBS module."""

from __future__ import annotations

import contextlib
import ctypes
import hashlib
import json
import math
import os
import secrets
import stat
import struct
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import IO, Any, NoReturn

from . import fbs as _fbs
from .process_authority import ProcessAuthority, ProcessAuthorityError

__all__ = [
    "OfficialFbsResultReceipt",
    "OfficialFbsRuntime",
    "OfficialFbsRuntimeError",
    "open_official_fbs_manager",
    "probe_official_fbs_runtime",
    "require_official_fbs_result",
    "validate_official_fbs_runtime",
]

_PROTOCOL = 1
_PROFILE = "official-fbs-3.1-cp313"
_HELPER_TIMEOUT_SECONDS = 60.0
_MAX_RESPONSE_BYTES = 1024 * 1024
_MAX_STDERR_BYTES = 64 * 1024
_RUNTIME_TOKEN = object()
_ADAPTER_TOKEN = object()
_RESULT_TOKEN = object()
_WINDOWS_DELETE = 0x00010000
_WINDOWS_GENERIC_WRITE = 0x40000000
_WINDOWS_FILE_CREATE = 2
_WINDOWS_FILE_DISPOSITION_INFO = 4
_WINDOWS_FILE_SHARE_DELETE = 0x00000004
_APPROVED_SYSTEM_IMPORTS = {
    "advapi32.dll",
    "bcrypt.dll",
    "crypt32.dll",
    "dnsapi.dll",
    "gdi32.dll",
    "iphlpapi.dll",
    "kernel32.dll",
    "msvcp140.dll",
    "ole32.dll",
    "oleaut32.dll",
    "psapi.dll",
    "secur32.dll",
    "shell32.dll",
    "user32.dll",
    "userenv.dll",
    "vcomp140.dll",
    "version.dll",
    "winmm.dll",
    "ws2_32.dll",
}

# Exact FEBio Studio 3.1 external-FBS and CPython 3.13.15 embed artifacts.
_APPROVED_SHA256: Mapping[str, str] = {
    "python_executable": "85b71d8c6ec1905935f74be0c9869aae198d00e98f39df699ec66f9c5a84cecd",
    "python_dll": "e820bf024efd2b56bb2b82791e6b6ddc7303f070f8e72cba7637482a8a906238",
    "python_stdlib": "1916abd946d2044ec8c04c3319f96c8415d5b6fce01e125622827f2b7756cbab",
    "python_path_config": "35ddf94682ff9aa713a8d63557242ad00f3f28fdd39337f02c3bda4c0f791577",
    "fbs_module": "85d27387427804805fc004bf972ea96e475c7bd357f3b77ff7ef75d07ceace1c",
    "zlib": "b22e4b08c7e7c6c46a70f74cb811da16a28af8185e9a4f928c84dbcced7a2ffe",
}

_APPROVED_PYTHON_TREE: Mapping[str, str] = {
    "_asyncio.pyd": "6941e2aeed3aad1a4658774ecb60309170066a040c2118a5efd16458ac3bed00",
    "_bz2.pyd": "261545bd24f15a783fc0c82568b766a4ab569dde14507a81701cccf5293e6e54",
    "_ctypes.pyd": "9d6dccb4cd84a44a107f9b06b91c0a44e85766d9bf4debb6abbde8fda7494c2c",
    "_decimal.pyd": "1e6a5707163d3c103e0eb4695eefedbea4b855fb227c61cc265b168970d6c6f0",
    "_elementtree.pyd": "26c5c2f3f2b8dac1499f07a50d7afd14315c20a6a541abbe4e306bcd9df404f1",
    "_hashlib.pyd": "79a26289aef0d92545a082507c7021f10e219621d137cb726c4d0fbbfd4b46ae",
    "_lzma.pyd": "ee224aacf5cf9c9974a1b842aac8d89d5d9b7eeebbf4d76619ae0ce4da8ecd96",
    "_multiprocessing.pyd": "db19972cf033b598f5826f8219bc2608412b84061d294f5972b43c2ab95f7af5",
    "_overlapped.pyd": "9db08b5449e18088754ce57e315657d00b46575e0151cd379efef8847488a606",
    "_queue.pyd": "4404c2569443ffb54e7cfffdca8ddf58c9a609175305970728ec12bc1c147b43",
    "_socket.pyd": "ccbd0f83df3784da08147b8e8765e28c978de28b5c0eddcfba5567b59d43fef9",
    "_sqlite3.pyd": "4c5fa9341a76ac525fd36e2cb23260934ad9b9f6825725ff139b26c8dc72436d",
    "_ssl.pyd": "8d2f51fa1dc929073a2ad8d1c7c0c911e8f695870dcf4dda2846f4b75a7f77fb",
    "_uuid.pyd": "b93832742a2348d6659fd62e8a8aff061594693fc537da74c7d2d1cdf83a8475",
    "_wmi.pyd": "db45362354fd4965329cb14bd974e60b26525045d09fb4fdfb1674a93b79d60c",
    "_zoneinfo.pyd": "6650db91accc05e625d3c5d8a40e15127a040ea1135e40e875fd78d8a7665a84",
    "libcrypto-3.dll": "09499e186cf0e434ffa17ad153aa9a66d6048c54a2c03b823e2a06c49d9affe4",
    "libffi-8.dll": "eff52743773eb550fcc6ce3efc37c85724502233b6b002a35496d828bd7b280a",
    "libssl-3.dll": "28c395290279fa4f4f54a57b85cd66d853895f925fefb8f6fdebc0d8b1cdd07c",
    "LICENSE.txt": "62bec384df47b0328307db41455ff6ea2559e5546b394ac69148561b21703120",
    "pyexpat.pyd": "b9c9c5a9f52af559a7eb4e8ddfd60558185bfc7794f32840bed1747d1ec9026a",
    "python.cat": "49eb507453b924ff70c19c8b8940b033ac5865f11cdd637447024725ed105bef",
    "python.exe": "85b71d8c6ec1905935f74be0c9869aae198d00e98f39df699ec66f9c5a84cecd",
    "python3.dll": "76e7fd69a01081726308317d6e502721c2ba6792b48880dc1ef649c755788db7",
    "python313._pth": "35ddf94682ff9aa713a8d63557242ad00f3f28fdd39337f02c3bda4c0f791577",
    "python313.dll": "e820bf024efd2b56bb2b82791e6b6ddc7303f070f8e72cba7637482a8a906238",
    "python313.zip": "1916abd946d2044ec8c04c3319f96c8415d5b6fce01e125622827f2b7756cbab",
    "pythonw.exe": "146c01e5448a084ff15d63113a790b9aa75250baf862d984c51dd695ac4775bc",
    "select.pyd": "024f955349fe46cf1bb2c22c6b57fcf38cf5755d46166fbf63e7e5b92433b111",
    "sqlite3.dll": "c6812eaf0f8605df273b9bf7e359b83fd65038ecc66455c3f7a933768cb92f3f",
    "unicodedata.pyd": "111e0fdfeef9887c04f6ebf1972ce1ccbec55b226012e4a288c2d4a0b17b216b",
    "vcruntime140.dll": "d1f4225df2cd877dbf130d5668a021dce3f94118455ff5ec952061c30afc9ce7",
    "vcruntime140_1.dll": "a7146c08f89fe5b04541ab507cdb59ff7b44534d4ba3c668a426c6450a03434e",
    "winsound.pyd": "b078b7f5c53efd3c4201b63fa34a17c6747142e37298293f52c3a9ad643e69e2",
}

_ROLE_FILENAMES = {
    "python_executable": "python.exe",
    "python_dll": "python313.dll",
    "python_stdlib": "python313.zip",
    "python_path_config": "python313._pth",
}

_FIELD_PROFILE: Mapping[str, tuple[str, str, int]] = {
    "displacement": ("displacement", "POINT_DATA", 3),
    "stress": ("stress", "CELL_DATA", 9),
    "Lagrange strain": ("Lagrange_strain", "CELL_DATA", 9),
    "pressure": ("pressure", "CELL_DATA", 1),
}

_FIELD_COMPONENT_PROFILE: Mapping[str, tuple[str, str]] = {
    "displacement": ("DATA_VECTOR", "displacement"),
    "stress": ("DATA_TENSOR2", "stress"),
    "Lagrange strain": ("DATA_TENSOR2", "Lagrange strain"),
    "pressure": ("DATA_SCALAR", "pressure"),
}


class OfficialFbsRuntimeError(RuntimeError):
    """The pinned official FBS runtime could not be established or used."""


class OfficialFbsRuntime:
    """Opaque receipt for a probed official FBS runtime profile."""

    _python_executable: Path
    _fbs_module: Path
    _zlib: Path
    _fingerprints: Mapping[str, str]
    _runtime_identity: str
    _module_attestation_sha256: str
    _issuance: object

    __slots__ = (
        "_fbs_module",
        "_fingerprints",
        "_issuance",
        "_module_attestation_sha256",
        "_python_executable",
        "_runtime_identity",
        "_zlib",
    )

    def __new__(
        cls,
        token: object | None = None,
        *_args: object,
        **_kwargs: object,
    ) -> OfficialFbsRuntime:
        if cls is not OfficialFbsRuntime or token is not _RUNTIME_TOKEN:
            raise TypeError("OfficialFbsRuntime instances are probe-issued")
        return super().__new__(cls)

    def __init__(
        self,
        token: object,
        python_executable: Path,
        fbs_module: Path,
        zlib: Path,
        fingerprints: Mapping[str, str],
        runtime_identity: str,
        module_attestation_sha256: str,
    ) -> None:
        if token is not _RUNTIME_TOKEN:
            raise TypeError("OfficialFbsRuntime instances are probe-issued")
        object.__setattr__(self, "_python_executable", python_executable)
        object.__setattr__(self, "_fbs_module", fbs_module)
        object.__setattr__(self, "_zlib", zlib)
        object.__setattr__(self, "_fingerprints", MappingProxyType(dict(fingerprints)))
        object.__setattr__(self, "_runtime_identity", runtime_identity)
        object.__setattr__(self, "_module_attestation_sha256", module_attestation_sha256)
        object.__setattr__(self, "_issuance", _RUNTIME_TOKEN)

    def __init_subclass__(cls, **kwargs: object) -> None:
        del kwargs
        raise TypeError("OfficialFbsRuntime cannot be subclassed")

    def __copy__(self) -> NoReturn:
        raise TypeError("OfficialFbsRuntime cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> NoReturn:
        del memo
        raise TypeError("OfficialFbsRuntime cannot be copied")

    def __reduce__(self) -> NoReturn:
        raise TypeError("OfficialFbsRuntime cannot be serialized")

    @property
    def python_executable(self) -> Path:
        return self._python_executable

    @property
    def fbs_module(self) -> Path:
        return self._fbs_module

    @property
    def zlib(self) -> Path:
        return self._zlib

    @property
    def fbs_module_sha256(self) -> str:
        return self._fingerprints["fbs_module"]

    @property
    def profile(self) -> str:
        return _PROFILE

    @property
    def runtime_identity(self) -> str:
        return self._runtime_identity

    @property
    def module_attestation_sha256(self) -> str:
        return self._module_attestation_sha256


@dataclass(frozen=True, slots=True)
class _OfficialFbsResultRecord:
    receipt: OfficialFbsResultReceipt
    validation: _fbs.FbsValidation
    adapter: _OfficialFbsAdapter
    runtime: OfficialFbsRuntime
    xplt_path: Path
    xplt_sha256: str
    model_manifest: Mapping[str, object]
    model_manifest_sha256: str
    requested_fields: tuple[str, ...]
    values: Mapping[str, object]
    transport: str


class OfficialFbsResultReceipt:
    """Opaque, live receipt for one exact official-FBS result read."""

    __slots__ = ("_record",)

    def __new__(
        cls,
        token: object | None = None,
        *_args: object,
        **_kwargs: object,
    ) -> OfficialFbsResultReceipt:
        if cls is not OfficialFbsResultReceipt or token is not _RESULT_TOKEN:
            raise TypeError("OfficialFbsResultReceipt instances are manager-issued")
        return super().__new__(cls)

    def __init__(self, token: object) -> None:
        if token is not _RESULT_TOKEN:
            raise TypeError("OfficialFbsResultReceipt instances are manager-issued")

    def __init_subclass__(cls, **kwargs: object) -> None:
        del kwargs
        raise TypeError("OfficialFbsResultReceipt cannot be subclassed")

    def __copy__(self) -> NoReturn:
        raise TypeError("OfficialFbsResultReceipt cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> NoReturn:
        del memo
        raise TypeError("OfficialFbsResultReceipt cannot be copied")

    def __reduce__(self) -> NoReturn:
        raise TypeError("OfficialFbsResultReceipt cannot be serialized")

    @property
    def validation(self) -> _fbs.FbsValidation:
        return _official_result_record(self).validation

    @property
    def xplt_path(self) -> Path:
        return _official_result_record(self).xplt_path

    @property
    def xplt_sha256(self) -> str:
        return _official_result_record(self).xplt_sha256

    @property
    def model_manifest(self) -> Mapping[str, object]:
        return _official_result_record(self).model_manifest

    @property
    def model_manifest_sha256(self) -> str:
        return _official_result_record(self).model_manifest_sha256

    @property
    def node_count(self) -> int:
        return _manifest_count(_official_result_record(self), "node_count")

    @property
    def element_count(self) -> int:
        return _manifest_count(_official_result_record(self), "element_count")

    @property
    def state_count(self) -> int:
        return _manifest_count(_official_result_record(self), "state_count")

    @property
    def state_times(self) -> tuple[float, ...]:
        record = _official_result_record(self)
        values = record.model_manifest["state_times"]
        if not isinstance(values, tuple):  # pragma: no cover - issuance guard
            raise TypeError("official FBS result receipt binding is invalid")
        return tuple(float(value) for value in values)

    @property
    def requested_fields(self) -> tuple[str, ...]:
        return _official_result_record(self).requested_fields

    @property
    def values(self) -> Mapping[str, object]:
        return _official_result_record(self).values

    @property
    def transport(self) -> str:
        return _official_result_record(self).transport

    @property
    def runtime_identity(self) -> str:
        return _official_result_record(self).runtime.runtime_identity

    @property
    def runtime_profile(self) -> str:
        return _official_result_record(self).runtime.profile

    @property
    def module_attestation_sha256(self) -> str:
        return _official_result_record(self).runtime.module_attestation_sha256


class _HeldEntry:
    __slots__ = ("cleanup_keeper", "digest", "name", "owner", "path")

    def __init__(
        self,
        name: str,
        path: Path,
        owner: _fbs._OwnedHandle,
        digest: str,
        *,
        cleanup_keeper: _fbs._OwnedHandle | None = None,
    ) -> None:
        self.name = name
        self.path = path
        self.owner = owner
        self.digest = digest
        self.cleanup_keeper = cleanup_keeper


class _HeldRuntime:
    __slots__ = ("entries", "roles")

    def __init__(
        self,
        entries: Mapping[str, _HeldEntry],
        roles: Mapping[str, _HeldEntry],
    ) -> None:
        self.entries = MappingProxyType(dict(entries))
        self.roles = MappingProxyType(dict(roles))

    def close(self) -> None:
        failures: list[BaseException] = []
        for entry in reversed(tuple(self.entries.values())):
            try:
                _fbs._close_owned_handle(entry.owner)
            except BaseException as error:
                _fbs._retain_cleanup_owner(entry.owner)
                failures.append(error)
        if failures:
            raise OfficialFbsRuntimeError("unable to close pinned runtime handles") from failures[0]


@dataclass(frozen=True, slots=True)
class _RequestBinding:
    nonce: str
    request_sha256: str
    xplt_sha256: str
    model_sha256: str | None = None
    model_manifest: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class _HelperProcessResult:
    return_code: int
    response: bytes
    module_attestation_sha256: str


@dataclass(frozen=True, slots=True)
class _ProbeHelperResult:
    response: Mapping[str, object]
    module_attestation_sha256: str


class _BoundedPipeReader:
    """Continuously drain one child pipe while retaining only a fixed prefix."""

    __slots__ = (
        "_captured",
        "_condition",
        "_done",
        "_error",
        "_label",
        "_limit",
        "_overflow",
        "_stream",
        "_thread",
    )

    def __init__(self, stream: IO[Any], limit: int, label: str) -> None:
        if limit <= 0:
            raise ValueError("pipe capture limit must be positive")
        self._stream = stream
        self._limit = limit
        self._label = label
        self._captured = bytearray()
        self._condition = threading.Condition()
        self._done = False
        self._overflow = False
        self._error: BaseException | None = None
        self._thread: threading.Thread | None = None

    @property
    def captured(self) -> bytes:
        with self._condition:
            return bytes(self._captured)

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("pipe reader was already started")
        self._thread = threading.Thread(target=self._drain, daemon=True)
        self._thread.start()

    def _drain(self) -> None:
        try:
            while True:
                chunk = os.read(self._stream.fileno(), 64 * 1024)
                if not chunk:
                    break
                with self._condition:
                    remaining = self._limit - len(self._captured)
                    if remaining > 0:
                        self._captured.extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        self._overflow = True
                    self._condition.notify_all()
        except BaseException as error:
            with self._condition:
                self._error = error
                self._condition.notify_all()
        finally:
            with self._condition:
                self._done = True
                self._condition.notify_all()

    def _check(self) -> None:
        if self._overflow:
            raise OfficialFbsRuntimeError(f"official FBS helper {self._label} is oversized")
        if self._error is not None:
            raise OfficialFbsRuntimeError(
                f"official FBS helper {self._label} pipe failed"
            ) from self._error

    def wait_line(self, index: int, deadline: float) -> bytes:
        if index < 0:
            raise ValueError("line index must be non-negative")
        with self._condition:
            while True:
                self._check()
                lines = bytes(self._captured).split(b"\n")
                if len(lines) > index + 1:
                    return lines[index].removesuffix(b"\r")
                if self._done:
                    raise OfficialFbsRuntimeError(
                        f"official FBS helper {self._label} ended before line {index + 1}"
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise OfficialFbsRuntimeError(
                        f"official FBS helper {self._label} read timed out"
                    )
                self._condition.wait(remaining)

    def finish(self, deadline: float) -> bytes:
        with self._condition:
            while not self._done:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise OfficialFbsRuntimeError(
                        f"official FBS helper {self._label} read timed out"
                    )
                self._condition.wait(remaining)
            self._check()
            return bytes(self._captured)

    def join(self, timeout_seconds: float) -> bool:
        thread = self._thread
        if thread is None:
            return True
        thread.join(max(0.0, timeout_seconds))
        return not thread.is_alive()


def _absolute(path: str | Path, role: str) -> Path:
    try:
        return Path(os.path.abspath(os.fspath(path)))
    except TypeError as error:
        raise OfficialFbsRuntimeError(f"{role} must be a filesystem path") from error


def _open_file(path: Path, role: str) -> _HeldEntry:
    owner: _fbs._OwnedHandle | None = None
    try:
        if os.name == "nt":
            owner = _fbs._windows_open_absolute(
                path,
                _fbs._WINDOWS_GENERIC_READ
                | _fbs._WINDOWS_FILE_READ_ATTRIBUTES
                | _fbs._WINDOWS_SYNCHRONIZE,
                _fbs._WINDOWS_FILE_SHARE_READ,
                _fbs._WINDOWS_FILE_NON_DIRECTORY_FILE
                | _fbs._WINDOWS_FILE_SYNCHRONOUS_IO_NONALERT
                | _fbs._WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT,
            )
            _fbs._windows_verify_owner(owner, directory=False, expected_path=path)
        else:
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            value = os.open(path, flags)
            owner = _fbs._OwnedHandle(value, 0, 0, path, False, identity_known=False)
            metadata = os.fstat(value)
            owner.device = int(metadata.st_dev)
            owner.inode = int(metadata.st_ino)
            owner.identity_known = True
            if not stat.S_ISREG(metadata.st_mode) or int(metadata.st_nlink) != 1:
                raise OfficialFbsRuntimeError(f"{role} must be one regular, non-linked file")
        digest = _fbs._digest_fd(owner)
        return _HeldEntry(role, path, owner, digest)
    except (OSError, ValueError) as error:
        if owner is not None:
            _fbs._best_effort_close(owner)
        raise OfficialFbsRuntimeError(f"unable to hold {role}") from error
    except BaseException:
        if owner is not None:
            _fbs._best_effort_close(owner)
        raise


def _verify_digest(entry: _HeldEntry, expected: str) -> None:
    if entry.digest.lower() != expected.lower():
        raise OfficialFbsRuntimeError(f"{entry.name} SHA-256 is not approved")


@contextlib.contextmanager
def _hold_runtime(
    python_executable: Path,
    fbs_module: Path,
    zlib: Path,
) -> Iterator[_HeldRuntime]:
    python_root = python_executable.parent
    entries: dict[str, _HeldEntry] = {}
    roles: dict[str, _HeldEntry] = {}
    held: _HeldRuntime | None = None
    try:
        for filename, expected in _APPROVED_PYTHON_TREE.items():
            entry = _open_file(python_root / filename, f"python_tree:{filename}")
            _verify_digest(entry, expected)
            entries[f"python:{filename.casefold()}"] = entry
        for role, filename in _ROLE_FILENAMES.items():
            if role == "python_executable" and python_executable.name.casefold() != "python.exe":
                raise OfficialFbsRuntimeError("python_executable must be named python.exe")
            role_entry = entries.get(f"python:{filename.casefold()}")
            if role_entry is None or role_entry.path != python_root / filename:
                raise OfficialFbsRuntimeError(f"{role} is absent from the approved Python tree")
            _verify_digest(role_entry, _APPROVED_SHA256[role])
            roles[role] = role_entry
        for role, path in {"fbs_module": fbs_module, "zlib": zlib}.items():
            external_entry = _open_file(path, role)
            _verify_digest(external_entry, _APPROVED_SHA256[role])
            entries[role] = external_entry
            roles[role] = external_entry
        held = _HeldRuntime(entries, roles)
        yield held
    finally:
        if held is not None:
            held.close()
        else:
            for entry in reversed(tuple(entries.values())):
                _fbs._best_effort_close(entry.owner)


def _read_entry(entry: _HeldEntry) -> bytes:
    if os.name == "nt":
        _fbs._windows_verify_owner(entry.owner, directory=False, expected_path=entry.path)
        try:
            payload = entry.path.read_bytes()
        except OSError as error:
            raise OfficialFbsRuntimeError(f"unable to copy held {entry.name}") from error
        _fbs._windows_verify_owner(entry.owner, directory=False, expected_path=entry.path)
    else:
        value = _fbs._owned_value(entry.owner)
        chunks: list[bytes] = []
        try:
            os.lseek(value, 0, os.SEEK_SET)
            while chunk := os.read(value, 1024 * 1024):
                chunks.append(chunk)
            os.lseek(value, 0, os.SEEK_SET)
        except OSError as error:
            raise OfficialFbsRuntimeError(f"unable to copy held {entry.name}") from error
        payload = b"".join(chunks)
    if hashlib.sha256(payload).hexdigest() != entry.digest:
        raise OfficialFbsRuntimeError(f"held {entry.name} changed while staging")
    return payload


def _pe_imports(payload: bytes) -> tuple[str, ...]:
    try:
        if payload[:2] != b"MZ" or len(payload) < 0x40:
            raise ValueError("missing DOS header")
        pe = struct.unpack_from("<I", payload, 0x3C)[0]
        if pe + 24 > len(payload) or payload[pe : pe + 4] != b"PE\0\0":
            raise ValueError("missing PE signature")
        coff = pe + 4
        section_count = struct.unpack_from("<H", payload, coff + 2)[0]
        optional_size = struct.unpack_from("<H", payload, coff + 16)[0]
        optional = coff + 20
        magic = struct.unpack_from("<H", payload, optional)[0]
        directory_offset = 112 if magic == 0x20B else 96 if magic == 0x10B else -1
        if directory_offset < 0 or optional_size < directory_offset + 16:
            raise ValueError("invalid optional header")
        import_rva, import_size = struct.unpack_from(
            "<II", payload, optional + directory_offset + 8
        )
        if import_rva == 0 or import_size == 0:
            return ()
        sections: list[tuple[int, int, int]] = []
        section_table = optional + optional_size
        if section_count <= 0 or section_count > 96:
            raise ValueError("invalid section count")
        for index in range(section_count):
            offset = section_table + index * 40
            if offset + 40 > len(payload):
                raise ValueError("truncated section table")
            virtual_size, virtual_address, raw_size, raw_pointer = struct.unpack_from(
                "<IIII", payload, offset + 8
            )
            sections.append((virtual_address, max(virtual_size, raw_size), raw_pointer))

        def file_offset(rva: int) -> int:
            for virtual_address, size, raw_pointer in sections:
                if virtual_address <= rva < virtual_address + size:
                    result = raw_pointer + rva - virtual_address
                    if result >= len(payload):
                        break
                    return result
            raise ValueError("unmapped PE RVA")

        imports: list[str] = []
        descriptor = file_offset(import_rva)
        limit = descriptor + min(import_size, 20 * 4096)
        while descriptor + 20 <= len(payload) and descriptor < limit:
            values = struct.unpack_from("<IIIII", payload, descriptor)
            if values == (0, 0, 0, 0, 0):
                return tuple(imports)
            name_offset = file_offset(values[3])
            end = payload.find(b"\0", name_offset, min(len(payload), name_offset + 512))
            if end < 0:
                raise ValueError("unterminated PE import")
            name = payload[name_offset:end].decode("ascii", errors="strict")
            if (
                not name
                or Path(name).name != name
                or name.casefold() in {item.casefold() for item in imports}
            ):
                raise ValueError("invalid PE import name")
            imports.append(name)
            descriptor += 20
        raise ValueError("unterminated PE import table")
    except (IndexError, UnicodeDecodeError, struct.error, ValueError) as error:
        raise OfficialFbsRuntimeError("release manifest contains an invalid PE image") from error


def _system_import_allowed(name: str) -> bool:
    folded = name.casefold()
    return (
        folded in _APPROVED_SYSTEM_IMPORTS
        or folded.startswith("api-ms-win-")
        or folded.startswith("ext-ms-win-")
    )


def _validate_release_import_closure(files: Mapping[str, bytes]) -> tuple[str, ...]:
    by_name: dict[str, tuple[str, bytes]] = {}
    for name, payload in files.items():
        if not isinstance(name, str) or Path(name).name != name or not isinstance(payload, bytes):
            raise OfficialFbsRuntimeError("release manifest is invalid")
        folded = name.casefold()
        if folded in by_name:
            raise OfficialFbsRuntimeError("release manifest has duplicate filenames")
        by_name[folded] = (name, payload)
    roots = ("python.exe", "python313.dll", "fbs.cp313-win_amd64.pyd", "zlib1.dll")
    missing_roots = [name for name in roots if name.casefold() not in by_name]
    if missing_roots:
        raise OfficialFbsRuntimeError("release manifest is missing " + ", ".join(missing_roots))
    pending = list(roots)
    visited: list[str] = []
    while pending:
        requested = pending.pop(0)
        folded = requested.casefold()
        if folded in {name.casefold() for name in visited}:
            continue
        actual_name, payload = by_name[folded]
        visited.append(actual_name)
        for imported in _pe_imports(payload):
            imported_folded = imported.casefold()
            if imported_folded in by_name:
                pending.append(by_name[imported_folded][0])
            elif not _system_import_allowed(imported):
                raise OfficialFbsRuntimeError(
                    f"release import closure is missing {imported} required by {actual_name}"
                )
    return tuple(visited)


def _is_within(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath((os.fspath(path), os.fspath(root))) == os.fspath(root)
    except ValueError:
        return False


def _validate_loaded_module_paths(
    loaded_paths: Sequence[Path],
    stage: Path,
    entries: Mapping[str, object],
    system_root: Path,
) -> tuple[Path, ...]:
    stage_real = Path(os.path.normcase(os.path.realpath(stage)))
    system_real = Path(os.path.normcase(os.path.realpath(system_root)))
    expected: dict[str, Path] = {}
    for name, entry in entries.items():
        entry_path = getattr(entry, "path", None)
        if not isinstance(entry_path, Path):
            raise OfficialFbsRuntimeError("loaded module manifest entry is invalid")
        expected[name.casefold()] = Path(os.path.normcase(os.path.realpath(entry_path)))
    observed: dict[str, Path] = {}
    for raw_path in loaded_paths:
        path = Path(os.path.normcase(os.path.realpath(raw_path)))
        name = path.name.casefold()
        if name in observed and observed[name] != path:
            raise OfficialFbsRuntimeError("loaded module basename is ambiguous")
        observed[name] = path
        if _is_within(path, stage_real):
            if name not in expected or expected[name] != path:
                raise OfficialFbsRuntimeError("loaded module path is not release-manifest bound")
        elif not _is_within(path, system_real):
            raise OfficialFbsRuntimeError("loaded module path is outside stage or SystemRoot")
    required = {
        "python.exe",
        "python313.dll",
        "fbs.cp313-win_amd64.pyd",
        "zlib1.dll",
    }
    if not required.issubset(observed):
        raise OfficialFbsRuntimeError("required loaded modules are unavailable")
    return tuple(observed.values())


def _enumerate_loaded_module_paths(process: subprocess.Popen[bytes]) -> tuple[Path, ...]:
    if os.name != "nt":
        raise OfficialFbsRuntimeError("loaded module attestation is available only on Windows")
    process_handle = getattr(process, "_handle", None)
    if process_handle is None:
        raise OfficialFbsRuntimeError("helper process handle is unavailable")
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    enum_modules = psapi.EnumProcessModulesEx
    enum_modules.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ctypes.c_void_p),
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.DWORD,
    ]
    enum_modules.restype = wintypes.BOOL
    get_name = psapi.GetModuleFileNameExW
    get_name.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.LPWSTR,
        wintypes.DWORD,
    ]
    get_name.restype = wintypes.DWORD
    capacity = 128
    while True:
        modules = (ctypes.c_void_p * capacity)()
        needed = wintypes.DWORD()
        if not enum_modules(
            wintypes.HANDLE(int(process_handle)),
            modules,
            wintypes.DWORD(ctypes.sizeof(modules)),
            ctypes.byref(needed),
            wintypes.DWORD(0x03),
        ):
            raise OfficialFbsRuntimeError("unable to enumerate helper loaded modules")
        count = needed.value // ctypes.sizeof(ctypes.c_void_p)
        if count <= capacity:
            break
        if count > 4096:
            raise OfficialFbsRuntimeError("helper loaded module count is invalid")
        capacity = count + 32
    paths: list[Path] = []
    for module in modules[:count]:
        buffer = ctypes.create_unicode_buffer(32768)
        length = get_name(
            wintypes.HANDLE(int(process_handle)),
            module,
            buffer,
            wintypes.DWORD(len(buffer)),
        )
        if length == 0 or length >= len(buffer):
            raise OfficialFbsRuntimeError("unable to identify helper loaded module")
        paths.append(Path(buffer.value))
    return tuple(paths)


def _loaded_module_attestation_sha256(
    loaded_paths: Sequence[Path],
    stage: Path,
    entries: Mapping[str, _HeldEntry],
    system_root: Path,
) -> str:
    stage_real = Path(os.path.normcase(os.path.realpath(stage)))
    system_real = Path(os.path.normcase(os.path.realpath(system_root)))
    stage_entries = {name.casefold(): entry for name, entry in entries.items()}
    manifest: list[dict[str, object]] = []
    for raw_path in loaded_paths:
        path = Path(os.path.normcase(os.path.realpath(raw_path)))
        if _is_within(path, stage_real):
            entry = stage_entries[path.name.casefold()]
            _fbs._windows_verify_owner(entry.owner, directory=False, expected_path=entry.path)
            manifest.append(
                {
                    "digest": entry.digest,
                    "location": "stage",
                    "name": path.name.casefold(),
                }
            )
            continue
        if not _is_within(path, system_real):  # pragma: no cover - validated by caller
            raise OfficialFbsRuntimeError("loaded module path is outside trusted roots")
        owner: _fbs._OwnedHandle | None = None
        try:
            owner = _fbs._windows_open_absolute(
                path,
                _fbs._WINDOWS_GENERIC_READ
                | _fbs._WINDOWS_FILE_READ_ATTRIBUTES
                | _fbs._WINDOWS_SYNCHRONIZE,
                _fbs._WINDOWS_FILE_SHARE_READ
                | _fbs._WINDOWS_FILE_SHARE_WRITE
                | _WINDOWS_FILE_SHARE_DELETE,
                _fbs._WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT,
            )
            attributes, device, inode, _ = _fbs._windows_file_info(_fbs._owned_value(owner))
            final_path = _fbs._windows_final_path(_fbs._owned_value(owner))
            if (
                device != owner.device
                or inode != owner.inode
                or bool(attributes & _fbs._WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT)
                or bool(attributes & _fbs._WINDOWS_FILE_ATTRIBUTE_DIRECTORY)
                or _fbs._windows_normalise_final(final_path) != _fbs._windows_expected_path(path)
            ):
                raise OfficialFbsRuntimeError("loaded system module identity changed")
            manifest.append(
                {
                    "device": owner.device,
                    "digest": _fbs._digest_fd(owner),
                    "file_id": owner.inode,
                    "location": "system",
                    "path": os.fspath(path),
                }
            )
            _fbs._close_owned_handle(owner)
        except BaseException as error:
            if owner is not None and not owner.closed:
                _fbs._retain_cleanup_owner(owner)
            raise OfficialFbsRuntimeError(
                f"unable to attest loaded system module {path.name}"
            ) from error
    manifest.sort(key=lambda item: (str(item["location"]), str(item.get("name", item.get("path")))))
    return hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _windows_write_owner(owner: _fbs._OwnedHandle, payload: bytes) -> None:
    value = _fbs._owned_value(owner)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    write_file = kernel32.WriteFile
    write_file.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    write_file.restype = wintypes.BOOL
    set_end = kernel32.SetEndOfFile
    set_end.argtypes = [wintypes.HANDLE]
    set_end.restype = wintypes.BOOL
    flush = kernel32.FlushFileBuffers
    flush.argtypes = [wintypes.HANDLE]
    flush.restype = wintypes.BOOL
    _fbs._windows_seek(value)
    offset = 0
    while offset < len(payload):
        chunk = payload[offset : offset + 1024 * 1024]
        written = wintypes.DWORD()
        buffer = ctypes.create_string_buffer(chunk)
        if not write_file(
            wintypes.HANDLE(value),
            buffer,
            wintypes.DWORD(len(chunk)),
            ctypes.byref(written),
            None,
        ) or written.value != len(chunk):
            raise OfficialFbsRuntimeError("unable to write exact private helper input")
        offset += written.value
    if not set_end(wintypes.HANDLE(value)) or not flush(wintypes.HANDLE(value)):
        raise OfficialFbsRuntimeError("unable to finalize exact private helper input")
    _fbs._windows_seek(value)


def _create_private_stage(
    root: Path,
) -> tuple[Path, _fbs._RootBinding, _fbs._RootBinding, _fbs._OwnedHandle]:
    if os.name != "nt":
        raise OfficialFbsRuntimeError("official FBS runtime is available only on Windows")
    parent = _fbs._hold_root(root)
    name = f".official-fbs-{secrets.token_hex(16)}"
    path = root / name
    owner: _fbs._OwnedHandle | None = None
    keeper: _fbs._OwnedHandle | None = None
    creator_has_delete = False
    try:
        parent_owner = parent.fd
        if parent_owner is None:
            raise OfficialFbsRuntimeError("private helper parent authority is unavailable")
        owner = _fbs._windows_open_relative(
            parent_owner,
            name,
            _fbs._WINDOWS_FILE_LIST_DIRECTORY
            | _fbs._WINDOWS_FILE_READ_ATTRIBUTES
            | _fbs._WINDOWS_READ_CONTROL
            | _fbs._WINDOWS_SYNCHRONIZE
            | _WINDOWS_DELETE,
            _fbs._WINDOWS_FILE_SHARE_READ
            | _fbs._WINDOWS_FILE_SHARE_WRITE
            | _WINDOWS_FILE_SHARE_DELETE,
            _fbs._WINDOWS_FILE_DIRECTORY_FILE
            | _fbs._WINDOWS_FILE_SYNCHRONOUS_IO_NONALERT
            | _fbs._WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT,
            path,
            create_disposition=_WINDOWS_FILE_CREATE,
        )
        creator_has_delete = True
        _fbs._windows_verify_owner(owner, directory=True, expected_path=path)
        keeper = _fbs._windows_open_relative(
            parent_owner,
            name,
            _fbs._WINDOWS_FILE_READ_ATTRIBUTES | _fbs._WINDOWS_SYNCHRONIZE,
            _fbs._WINDOWS_FILE_SHARE_READ
            | _fbs._WINDOWS_FILE_SHARE_WRITE
            | _WINDOWS_FILE_SHARE_DELETE,
            _fbs._WINDOWS_FILE_DIRECTORY_FILE
            | _fbs._WINDOWS_FILE_SYNCHRONOUS_IO_NONALERT
            | _fbs._WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT,
            path,
        )
        _fbs._windows_verify_owner(keeper, directory=True, expected_path=path)
        if (keeper.device, keeper.inode) != (owner.device, owner.inode):
            raise OfficialFbsRuntimeError("private helper cleanup identity changed")
        _fbs._close_owned_handle(owner)
        owner = None
        creator_has_delete = False
        owner = _fbs._windows_open_relative(
            parent_owner,
            name,
            _fbs._WINDOWS_FILE_LIST_DIRECTORY
            | _fbs._WINDOWS_FILE_READ_ATTRIBUTES
            | _fbs._WINDOWS_READ_CONTROL
            | _fbs._WINDOWS_SYNCHRONIZE,
            _fbs._WINDOWS_FILE_SHARE_READ | _fbs._WINDOWS_FILE_SHARE_WRITE,
            _fbs._WINDOWS_FILE_DIRECTORY_FILE
            | _fbs._WINDOWS_FILE_SYNCHRONOUS_IO_NONALERT
            | _fbs._WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT,
            path,
        )
        _fbs._windows_verify_owner(owner, directory=True, expected_path=path)
        if (keeper.device, keeper.inode) != (owner.device, owner.inode):
            raise OfficialFbsRuntimeError("private helper guard identity changed")
        return path, parent, _fbs._RootBinding(path, owner, owner.device, owner.inode), keeper
    except BaseException:
        if owner is not None and not owner.closed:
            if creator_has_delete:
                try:
                    _mark_delete(owner, "private helper directory")
                    _fbs._close_owned_handle(owner)
                    if keeper is not None:
                        _fbs._close_owned_handle(keeper)
                except BaseException:
                    if not owner.closed:
                        _fbs._retain_cleanup_owner(owner)
                    if keeper is not None and not keeper.closed:
                        _fbs._retain_cleanup_owner(keeper)
            else:
                _fbs._retain_cleanup_owner(owner)
                if keeper is not None and not keeper.closed:
                    _fbs._retain_cleanup_owner(keeper)
        elif keeper is not None and not keeper.closed:
            _fbs._retain_cleanup_owner(keeper)
        _fbs._finalize_root_binding(parent)
        raise


def _write_and_hold(
    stage: _fbs._RootBinding,
    name: str,
    payload: bytes,
    role: str,
) -> _HeldEntry:
    if os.name != "nt" or stage.fd is None:
        raise OfficialFbsRuntimeError("exact private helper staging is unavailable")
    path = stage.path / name
    owner: _fbs._OwnedHandle | None = None
    cleanup_keeper: _fbs._OwnedHandle | None = None
    creator_has_delete = False
    try:
        owner = _fbs._windows_open_relative(
            stage.fd,
            name,
            _fbs._WINDOWS_GENERIC_READ
            | _WINDOWS_GENERIC_WRITE
            | _fbs._WINDOWS_SYNCHRONIZE
            | _WINDOWS_DELETE,
            _fbs._WINDOWS_FILE_SHARE_READ
            | _fbs._WINDOWS_FILE_SHARE_WRITE
            | _WINDOWS_FILE_SHARE_DELETE,
            _fbs._WINDOWS_FILE_NON_DIRECTORY_FILE
            | _fbs._WINDOWS_FILE_SYNCHRONOUS_IO_NONALERT
            | _fbs._WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT,
            path,
            create_disposition=_WINDOWS_FILE_CREATE,
        )
        creator_has_delete = True
        _fbs._windows_verify_owner(owner, directory=False, expected_path=path)
        _windows_write_owner(owner, payload)
        digest = _fbs._digest_fd(owner)
        if digest != hashlib.sha256(payload).hexdigest():
            raise OfficialFbsRuntimeError(f"staged {role} digest differs")
        cleanup_keeper = _fbs._windows_open_relative(
            stage.fd,
            name,
            _fbs._WINDOWS_FILE_READ_ATTRIBUTES | _fbs._WINDOWS_SYNCHRONIZE,
            _fbs._WINDOWS_FILE_SHARE_READ
            | _fbs._WINDOWS_FILE_SHARE_WRITE
            | _WINDOWS_FILE_SHARE_DELETE,
            _fbs._WINDOWS_FILE_NON_DIRECTORY_FILE
            | _fbs._WINDOWS_FILE_SYNCHRONOUS_IO_NONALERT
            | _fbs._WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT,
            path,
        )
        _fbs._windows_verify_owner(cleanup_keeper, directory=False, expected_path=path)
        if (cleanup_keeper.device, cleanup_keeper.inode) != (owner.device, owner.inode):
            raise OfficialFbsRuntimeError(f"exact {role} cleanup identity changed")
        _fbs._close_owned_handle(owner)
        owner = None
        creator_has_delete = False
        owner = _fbs._windows_open_relative(
            stage.fd,
            name,
            _fbs._WINDOWS_GENERIC_READ | _fbs._WINDOWS_SYNCHRONIZE,
            _fbs._WINDOWS_FILE_SHARE_READ,
            _fbs._WINDOWS_FILE_NON_DIRECTORY_FILE
            | _fbs._WINDOWS_FILE_SYNCHRONOUS_IO_NONALERT
            | _fbs._WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT,
            path,
        )
        _fbs._windows_verify_owner(owner, directory=False, expected_path=path)
        if (cleanup_keeper.device, cleanup_keeper.inode) != (owner.device, owner.inode):
            raise OfficialFbsRuntimeError(f"exact {role} guard identity changed")
        return _HeldEntry(
            role,
            path,
            owner,
            digest,
            cleanup_keeper=cleanup_keeper,
        )
    except BaseException:
        if owner is not None and not owner.closed:
            if creator_has_delete:
                try:
                    _mark_delete(owner, role)
                    _fbs._close_owned_handle(owner)
                    if cleanup_keeper is not None:
                        _fbs._close_owned_handle(cleanup_keeper)
                except BaseException:
                    if not owner.closed:
                        _fbs._retain_cleanup_owner(owner)
                    if cleanup_keeper is not None and not cleanup_keeper.closed:
                        _fbs._retain_cleanup_owner(cleanup_keeper)
            elif cleanup_keeper is not None:
                partial = _HeldEntry(
                    role,
                    path,
                    owner,
                    "",
                    cleanup_keeper=cleanup_keeper,
                )
                try:
                    _delete_stage_entry(stage, partial)
                except BaseException:
                    if not owner.closed:
                        _fbs._retain_cleanup_owner(owner)
                    if not cleanup_keeper.closed:
                        _fbs._retain_cleanup_owner(cleanup_keeper)
        elif cleanup_keeper is not None and not cleanup_keeper.closed:
            _fbs._retain_cleanup_owner(cleanup_keeper)
        raise


def _mark_delete(owner: _fbs._OwnedHandle, label: str) -> None:
    value = _fbs._owned_value(owner)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    set_information = kernel32.SetFileInformationByHandle
    set_information.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    set_information.restype = wintypes.BOOL
    disposition = ctypes.c_ubyte(1)
    if not set_information(
        wintypes.HANDLE(value),
        _WINDOWS_FILE_DISPOSITION_INFO,
        ctypes.byref(disposition),
        ctypes.sizeof(disposition),
    ):
        raise OfficialFbsRuntimeError(f"unable to delete exact {label}")


def _delete_stage_entry(stage: _fbs._RootBinding, entry: _HeldEntry) -> None:
    if stage.fd is None or entry.owner.closed:
        raise OfficialFbsRuntimeError("private helper stage authority is unavailable")
    _fbs._windows_verify_owner(entry.owner, directory=False, expected_path=entry.path)
    if entry.cleanup_keeper is None:
        _mark_delete(entry.owner, entry.name)
        return
    keeper = entry.cleanup_keeper
    if keeper is None or keeper.closed:
        raise OfficialFbsRuntimeError(f"exact {entry.name} cleanup keeper is unavailable")
    _fbs._windows_verify_owner(keeper, directory=False, expected_path=entry.path)
    if (keeper.device, keeper.inode) != (entry.owner.device, entry.owner.inode):
        raise OfficialFbsRuntimeError(f"exact {entry.name} cleanup identity changed")
    delete_owner: _fbs._OwnedHandle | None = None
    try:
        _fbs._close_owned_handle(entry.owner)
        delete_owner = _fbs._windows_open_relative(
            stage.fd,
            entry.path.name,
            _fbs._WINDOWS_FILE_READ_ATTRIBUTES | _fbs._WINDOWS_SYNCHRONIZE | _WINDOWS_DELETE,
            _fbs._WINDOWS_FILE_SHARE_READ
            | _fbs._WINDOWS_FILE_SHARE_WRITE
            | _WINDOWS_FILE_SHARE_DELETE,
            _fbs._WINDOWS_FILE_NON_DIRECTORY_FILE
            | _fbs._WINDOWS_FILE_SYNCHRONOUS_IO_NONALERT
            | _fbs._WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT,
            entry.path,
        )
        _fbs._windows_verify_owner(delete_owner, directory=False, expected_path=entry.path)
        if (delete_owner.device, delete_owner.inode) != (keeper.device, keeper.inode):
            raise OfficialFbsRuntimeError(f"exact {entry.name} identity changed")
        _mark_delete(delete_owner, entry.name)
        _fbs._close_owned_handle(delete_owner)
        _fbs._close_owned_handle(keeper)
    except BaseException:
        if delete_owner is not None and not delete_owner.closed:
            _fbs._retain_cleanup_owner(delete_owner)
        if not entry.owner.closed:
            _fbs._retain_cleanup_owner(entry.owner)
        if not keeper.closed:
            _fbs._retain_cleanup_owner(keeper)
        raise


def _delete_stage_root(
    parent: _fbs._RootBinding,
    stage: _fbs._RootBinding,
    keeper: _fbs._OwnedHandle,
) -> None:
    if parent.fd is None or stage.fd is None or keeper.closed:
        raise OfficialFbsRuntimeError("private helper root authority is unavailable")
    _fbs._windows_verify_owner(stage.fd, directory=True, expected_path=stage.path)
    _fbs._windows_verify_owner(keeper, directory=True, expected_path=stage.path)
    if (keeper.device, keeper.inode) != (stage.fd.device, stage.fd.inode):
        raise OfficialFbsRuntimeError("private helper cleanup identity changed")
    _fbs._release_root_binding(stage)
    delete_owner: _fbs._OwnedHandle | None = None
    try:
        delete_owner = _fbs._windows_open_relative(
            parent.fd,
            stage.path.name,
            _fbs._WINDOWS_FILE_READ_ATTRIBUTES | _fbs._WINDOWS_SYNCHRONIZE | _WINDOWS_DELETE,
            _fbs._WINDOWS_FILE_SHARE_READ
            | _fbs._WINDOWS_FILE_SHARE_WRITE
            | _WINDOWS_FILE_SHARE_DELETE,
            _fbs._WINDOWS_FILE_DIRECTORY_FILE
            | _fbs._WINDOWS_FILE_SYNCHRONOUS_IO_NONALERT
            | _fbs._WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT,
            stage.path,
        )
        _fbs._windows_verify_owner(delete_owner, directory=True, expected_path=stage.path)
        if (delete_owner.device, delete_owner.inode) != (keeper.device, keeper.inode):
            raise OfficialFbsRuntimeError("private helper directory identity changed")
        _mark_delete(delete_owner, "private helper directory")
        _fbs._close_owned_handle(delete_owner)
        _fbs._close_owned_handle(keeper)
    except BaseException:
        if delete_owner is not None and not delete_owner.closed:
            _fbs._retain_cleanup_owner(delete_owner)
        if not keeper.closed:
            _fbs._retain_cleanup_owner(keeper)
        raise


def _json_object(payload: bytes) -> Mapping[str, object]:
    if len(payload) > _MAX_RESPONSE_BYTES:
        raise OfficialFbsRuntimeError("official FBS helper response is oversized")

    def reject_constant(value: str) -> NoReturn:
        raise ValueError(f"non-finite JSON constant: {value}")

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        result = json.loads(
            payload.decode("utf-8", errors="strict"),
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise OfficialFbsRuntimeError("official FBS helper returned invalid JSON") from error
    if not isinstance(result, dict) or any(not isinstance(key, str) for key in result):
        raise OfficialFbsRuntimeError("official FBS helper response must be an object")
    pending: list[tuple[object, int]] = [(result, 1)]
    while pending:
        value, depth = pending.pop()
        if depth > 32:
            raise OfficialFbsRuntimeError("official FBS helper response is too deeply nested")
        if isinstance(value, dict):
            pending.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            pending.extend((item, depth + 1) for item in value)
    return result


def _exact_keys(value: Mapping[str, object], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise OfficialFbsRuntimeError(f"{label} has an invalid schema")


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OfficialFbsRuntimeError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise OfficialFbsRuntimeError(f"{label} must be finite")
    return result


def _model_manifest_sha256(value: Mapping[str, object]) -> str:
    try:
        payload = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, ValueError) as error:
        raise OfficialFbsRuntimeError("official FBS model manifest is invalid") from error
    return hashlib.sha256(payload).hexdigest()


def _positive_count(value: object, label: str, *, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or value > maximum:
        raise OfficialFbsRuntimeError(f"{label} is invalid")
    return value


def _validate_model_manifest(
    value: object,
    requested_fields: Sequence[str],
) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise OfficialFbsRuntimeError("official FBS model manifest is invalid")
    _exact_keys(
        value,
        {
            "available_fields",
            "element_count",
            "node_count",
            "requested_fields",
            "state_count",
            "state_times",
        },
        "model manifest",
    )
    state_count = _positive_count(value["state_count"], "model state count", maximum=10000)
    node_count = _positive_count(value["node_count"], "model node count", maximum=100000000)
    element_count = _positive_count(
        value["element_count"], "model element count", maximum=100000000
    )
    state_times = value["state_times"]
    if not isinstance(state_times, list) or len(state_times) != state_count:
        raise OfficialFbsRuntimeError("model state times are invalid")
    for state_time in state_times:
        _number(state_time, "model state time")
    available = value["available_fields"]
    if not isinstance(available, list) or len(available) > 10000:
        raise OfficialFbsRuntimeError("model available fields are invalid")
    names: list[str] = []
    indexes: dict[str, int] = {}
    for expected_index, item in enumerate(available):
        if not isinstance(item, dict):
            raise OfficialFbsRuntimeError("model field identity is invalid")
        _exact_keys(item, {"index", "name"}, "model field identity")
        index, name = item["index"], item["name"]
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or index != expected_index
            or not isinstance(name, str)
            or not name
            or name in indexes
        ):
            raise OfficialFbsRuntimeError("model field identity is invalid")
        names.append(name)
        indexes[name] = index
    requested = value["requested_fields"]
    if not isinstance(requested, dict) or any(not isinstance(key, str) for key in requested):
        raise OfficialFbsRuntimeError("model requested field manifest is invalid")
    expected_present = {field for field in requested_fields if field in indexes}
    if set(requested) != expected_present:
        raise OfficialFbsRuntimeError("model requested field manifest is incomplete")
    for field, item in requested.items():
        if not isinstance(item, dict):
            raise OfficialFbsRuntimeError(f"{field} model field manifest is invalid")
        _exact_keys(
            item,
            {
                "association",
                "component_index",
                "component_name",
                "components",
                "field_index",
                "tensor_type",
                "vtk_name",
            },
            f"{field} model field manifest",
        )
        vtk_name, association, components = _FIELD_PROFILE[field]
        tensor_type, component_name = _FIELD_COMPONENT_PROFILE[field]
        expected = {
            "association": association,
            "component_index": 0,
            "component_name": component_name,
            "components": components,
            "field_index": indexes[field],
            "tensor_type": tensor_type,
            "vtk_name": vtk_name,
        }
        if any(
            type(item[name]) is not type(expected_value) or item[name] != expected_value
            for name, expected_value in expected.items()
        ):
            raise OfficialFbsRuntimeError(f"{field} model field identity is invalid")
    # Keep both model cardinalities live in the validation path; association-specific
    # checks below choose one of them for each field.
    if node_count <= 0 or element_count <= 0:  # pragma: no cover - guarded above
        raise OfficialFbsRuntimeError("model cardinality is invalid")
    return value


def _validate_inspect_response(
    response: Mapping[str, object],
    requested_fields: Sequence[str],
    binding: _RequestBinding,
) -> Mapping[str, object]:
    _exact_keys(
        response,
        {
            "model",
            "nonce",
            "profile",
            "protocol",
            "request_sha256",
            "xplt_sha256",
        },
        "inspect response",
    )
    if response["protocol"] != _PROTOCOL:
        raise OfficialFbsRuntimeError("official FBS helper protocol mismatch")
    if (
        response["profile"] != _PROFILE
        or response["nonce"] != binding.nonce
        or response["request_sha256"] != binding.request_sha256
        or response["xplt_sha256"] != binding.xplt_sha256
    ):
        raise OfficialFbsRuntimeError("official FBS helper response binding mismatch")
    return _validate_model_manifest(response["model"], requested_fields)


def _validate_probe_response(response: Mapping[str, object]) -> None:
    _exact_keys(response, {"protocol", "python", "module", "api"}, "probe response")
    if response["protocol"] != _PROTOCOL:
        raise OfficialFbsRuntimeError("official FBS helper protocol mismatch")
    if response["python"] != "3.13" or response["module"] != "fbs":
        raise OfficialFbsRuntimeError("official FBS helper identity mismatch")
    if response["api"] != ["ReadPlotFile", "vtkExport"]:
        raise OfficialFbsRuntimeError("official FBS post API is unavailable")


def _validate_field_response(
    response: Mapping[str, object],
    requested_fields: Sequence[str],
    binding: _RequestBinding,
) -> Mapping[str, object]:
    _exact_keys(
        response,
        {
            "available_fields",
            "field_data",
            "model_sha256",
            "nonce",
            "profile",
            "protocol",
            "request_sha256",
            "xplt_sha256",
        },
        "field response",
    )
    if response["protocol"] != _PROTOCOL:
        raise OfficialFbsRuntimeError("official FBS helper protocol mismatch")
    if (
        response["profile"] != _PROFILE
        or response["nonce"] != binding.nonce
        or response["request_sha256"] != binding.request_sha256
        or response["xplt_sha256"] != binding.xplt_sha256
        or binding.model_manifest is None
        or binding.model_sha256 is None
        or response["model_sha256"] != binding.model_sha256
    ):
        raise OfficialFbsRuntimeError("official FBS helper response binding mismatch")
    model = _validate_model_manifest(binding.model_manifest, requested_fields)
    if _model_manifest_sha256(model) != binding.model_sha256:
        raise OfficialFbsRuntimeError("official FBS model binding changed")
    available, field_data = response["available_fields"], response["field_data"]
    raw_available = model["available_fields"]
    if not isinstance(raw_available, list):  # pragma: no cover - validated above
        raise OfficialFbsRuntimeError("model available fields were revoked")
    expected_available = [item["name"] for item in raw_available]
    if not isinstance(available, list) or available != expected_available:
        raise OfficialFbsRuntimeError("available_fields is invalid")
    state_count = model["state_count"]
    state_times = model["state_times"]
    node_count = model["node_count"]
    element_count = model["element_count"]
    model_fields = model["requested_fields"]
    if (
        not isinstance(state_count, int)
        or not isinstance(state_times, list)
        or not isinstance(node_count, int)
        or not isinstance(element_count, int)
        or not isinstance(model_fields, dict)
    ):  # pragma: no cover - validated above
        raise OfficialFbsRuntimeError("official FBS model manifest was revoked")
    if not isinstance(field_data, dict) or any(not isinstance(key, str) for key in field_data):
        raise OfficialFbsRuntimeError("raw field data is invalid")
    requested = set(requested_fields)
    if not set(field_data).issubset(requested):
        raise OfficialFbsRuntimeError("helper returned unrequested fields")
    summaries: dict[str, object] = {}
    non_finite: list[str] = []
    for field in requested_fields:
        if field not in available:
            if field in field_data:
                raise OfficialFbsRuntimeError(f"{field} availability is contradictory")
            continue
        raw_field = field_data.get(field)
        if not isinstance(raw_field, dict):
            raise OfficialFbsRuntimeError(f"{field} raw data is missing")
        _exact_keys(
            raw_field,
            {"vtk_name", "association", "components", "field_index", "states"},
            f"{field} raw data",
        )
        vtk_name, association, components = _FIELD_PROFILE[field]
        model_field = model_fields.get(field)
        if not isinstance(model_field, dict):
            raise OfficialFbsRuntimeError(f"{field} model field identity is missing")
        if (
            raw_field["vtk_name"] != vtk_name
            or raw_field["association"] != association
            or raw_field["components"] != components
            or raw_field["field_index"] != model_field["field_index"]
        ):
            raise OfficialFbsRuntimeError(f"{field} semantic mapping is invalid")
        states = raw_field["states"]
        if not isinstance(states, list) or len(states) != state_count:
            raise OfficialFbsRuntimeError(f"{field} states are invalid")
        aggregate: list[float] = []
        expected_entity_count = node_count if association == "POINT_DATA" else element_count
        field_non_finite = False
        for expected_index, raw_state in enumerate(states):
            if not isinstance(raw_state, dict):
                raise OfficialFbsRuntimeError(f"{field} state is invalid")
            _exact_keys(
                raw_state,
                {"index", "time", "entity_count", "values"},
                f"{field} state",
            )
            index = raw_state["index"]
            state_time = raw_state["time"]
            count = raw_state["entity_count"]
            raw_values = raw_state["values"]
            if (
                isinstance(index, bool)
                or not isinstance(index, int)
                or index != expected_index
                or isinstance(count, bool)
                or not isinstance(count, int)
                or count != expected_entity_count
                or _number(state_time, f"{field} state time")
                != _number(state_times[expected_index], "model state time")
                or not isinstance(raw_values, list)
                or len(raw_values) != count * components
            ):
                raise OfficialFbsRuntimeError(f"{field} state cardinality is invalid")
            for raw_value in raw_values:
                if raw_value is None:
                    field_non_finite = True
                    continue
                aggregate.append(_number(raw_value, f"{field} component"))
        if field_non_finite:
            non_finite.append(field)
        elif aggregate:
            summaries[field] = {
                "components": components,
                "count": len(aggregate),
                "entity_count": expected_entity_count,
                "field_index": model_field["field_index"],
                "minimum": min(aggregate),
                "maximum": max(aggregate),
                "state_count": state_count,
            }
        else:
            raise OfficialFbsRuntimeError(f"{field} has no component values")
    return {
        "protocol": _PROTOCOL,
        "available_fields": list(available),
        "model_manifest": model,
        "model_sha256": binding.model_sha256,
        "values": summaries,
        "non_finite_fields": non_finite,
        "xplt_sha256": binding.xplt_sha256,
    }


class _WindowsBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("per_process_user_time_limit", ctypes.c_longlong),
        ("per_job_user_time_limit", ctypes.c_longlong),
        ("limit_flags", wintypes.DWORD),
        ("minimum_working_set_size", ctypes.c_size_t),
        ("maximum_working_set_size", ctypes.c_size_t),
        ("active_process_limit", wintypes.DWORD),
        ("affinity", ctypes.c_size_t),
        ("priority_class", wintypes.DWORD),
        ("scheduling_class", wintypes.DWORD),
    ]


class _WindowsIoCounters(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_ulonglong)
        for name in (
            "read_operation_count",
            "write_operation_count",
            "other_operation_count",
            "read_transfer_count",
            "write_transfer_count",
            "other_transfer_count",
        )
    ]


class _WindowsExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("basic_limit_information", _WindowsBasicLimitInformation),
        ("io_info", _WindowsIoCounters),
        ("process_memory_limit", ctypes.c_size_t),
        ("job_memory_limit", ctypes.c_size_t),
        ("peak_process_memory_used", ctypes.c_size_t),
        ("peak_job_memory_used", ctypes.c_size_t),
    ]


def _enable_kill_on_close(authority: ProcessAuthority) -> None:
    if os.name != "nt":
        return
    handle = getattr(authority, "_handle", None)
    if not handle:
        raise OfficialFbsRuntimeError("helper process Job handle is unavailable")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    set_information = kernel32.SetInformationJobObject
    set_information.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    set_information.restype = wintypes.BOOL
    limits = _WindowsExtendedLimitInformation()
    limits.basic_limit_information.limit_flags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not set_information(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        raise OfficialFbsRuntimeError("unable to configure helper process Job containment")


def _launch_helper(
    command: Sequence[str],
    *,
    stage: Path,
    release_entries: Sequence[_HeldEntry],
    environment: Mapping[str, str],
    timeout_seconds: float,
    context_digest: str,
) -> _HelperProcessResult:
    authority: ProcessAuthority | None = None
    process: subprocess.Popen[bytes] | None = None
    stdout_reader: _BoundedPipeReader | None = None
    stderr_reader: _BoundedPipeReader | None = None
    bound = False
    try:
        if os.name != "nt":
            raise OfficialFbsRuntimeError("official FBS runtime is available only on Windows")
        code_entries = {
            entry.path.name: entry
            for entry in release_entries
            if entry.path.suffix.casefold() in {".exe", ".dll", ".pyd"}
        }
        _validate_release_import_closure(
            {name: _read_entry(entry) for name, entry in code_entries.items()}
        )
        system_root_value = next(
            (value for key, value in environment.items() if key.casefold() == "systemroot"),
            None,
        )
        if not system_root_value:
            raise OfficialFbsRuntimeError("SystemRoot is unavailable for module attestation")
        authority = ProcessAuthority.create(stage, context_digest)
        _enable_kill_on_close(authority)
        creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | 0x00000004
        process = subprocess.Popen(
            command,
            cwd=stage,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(environment),
            close_fds=True,
            creationflags=creation_flags,
        )
        authority.bind(process.pid)
        bound = True
        authority.resume(process.pid)
        stdout_pipe = process.stdout
        stderr_pipe = process.stderr
        stdin_pipe = process.stdin
        if stdout_pipe is None or stderr_pipe is None or stdin_pipe is None:
            raise OfficialFbsRuntimeError("helper attestation pipes are unavailable")
        deadline = time.monotonic() + timeout_seconds
        stdout_reader = _BoundedPipeReader(stdout_pipe, _MAX_RESPONSE_BYTES, "response")
        stderr_reader = _BoundedPipeReader(stderr_pipe, _MAX_STDERR_BYTES, "stderr")
        stdout_reader.start()
        stderr_reader.start()
        ready = stderr_reader.wait_line(0, deadline)
        if ready != b"READY":
            detail = ready.decode("utf-8", errors="replace").strip()
            raise OfficialFbsRuntimeError(
                "official FBS helper attestation handshake failed: " + detail[:160]
            )
        loaded_paths = _enumerate_loaded_module_paths(process)
        _validate_loaded_module_paths(
            loaded_paths,
            stage,
            code_entries,
            Path(system_root_value),
        )
        for entry in code_entries.values():
            _fbs._windows_verify_owner(entry.owner, directory=False, expected_path=entry.path)
            if _fbs._digest_fd(entry.owner) != entry.digest:
                raise OfficialFbsRuntimeError("loaded release module changed during attestation")
        stdin_pipe.write(b"1")
        stdin_pipe.flush()
        completed = stderr_reader.wait_line(1, deadline)
        if completed != b"COMPLETE":
            detail = completed.decode("utf-8", errors="replace").strip()
            raise OfficialFbsRuntimeError(
                "official FBS helper completion attestation failed: " + detail[:160]
            )
        final_loaded_paths = _enumerate_loaded_module_paths(process)
        validated_loaded_paths = _validate_loaded_module_paths(
            final_loaded_paths,
            stage,
            code_entries,
            Path(system_root_value),
        )
        module_attestation_sha256 = _loaded_module_attestation_sha256(
            validated_loaded_paths,
            stage,
            code_entries,
            Path(system_root_value),
        )
        stdin_pipe.write(b"2")
        stdin_pipe.flush()
        stdin_pipe.close()
        process.stdin = None
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout_seconds)
            process.wait(timeout=remaining)
            response = stdout_reader.finish(deadline)
            stderr_payload = stderr_reader.finish(deadline)
            if stderr_payload.splitlines() != [b"READY", b"COMPLETE"]:
                raise OfficialFbsRuntimeError("official FBS helper emitted unexpected stderr")
            return _HelperProcessResult(
                process.returncode,
                response,
                module_attestation_sha256,
            )
        except subprocess.TimeoutExpired as error:
            if authority is not None and bound:
                authority.terminate(process.pid, force=True)
            else:
                process.kill()
            process.wait(timeout=5.0)
            raise OfficialFbsRuntimeError("official FBS helper timed out") from error
    except ProcessAuthorityError as error:
        raise OfficialFbsRuntimeError("official FBS helper process authority failed") from error
    finally:
        failures: list[BaseException] = []
        if process is not None and process.poll() is None:
            try:
                if authority is not None and bound:
                    authority.terminate(process.pid, force=True)
                else:
                    process.kill()
                process.wait(timeout=5.0)
            except BaseException as error:
                failures.append(error)
        for reader in (stdout_reader, stderr_reader):
            if reader is not None and not reader.join(5.0):
                failures.append(OfficialFbsRuntimeError("helper pipe drain did not terminate"))
        if process is not None:
            for pipe in (process.stdout, process.stderr, process.stdin):
                if pipe is not None:
                    try:
                        pipe.close()
                    except BaseException as error:
                        failures.append(error)
        if authority is not None:
            if bound:
                try:
                    authority.drain()
                except BaseException as error:
                    failures.append(error)
            try:
                authority.close()
            except BaseException as error:
                failures.append(error)
        if failures:
            raise OfficialFbsRuntimeError(
                "official FBS helper process cleanup failed"
            ) from failures[0]


def _run_helper(
    held: _HeldRuntime,
    request: Mapping[str, object],
    root: Path,
    timeout_seconds: float,
    *,
    xplt_entry: _HeldEntry | None = None,
    expected_module_attestation_sha256: str | None = None,
    module_attestation_sink: list[str] | None = None,
) -> Mapping[str, object]:
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise OfficialFbsRuntimeError("helper timeout must be positive and finite")
    parent_binding: _fbs._RootBinding | None = None
    stage_binding: _fbs._RootBinding | None = None
    stage_keeper: _fbs._OwnedHandle | None = None
    stage_entries: list[_HeldEntry] = []
    try:
        stage, parent_binding, stage_binding, stage_keeper = _create_private_stage(root)
        for key, entry in held.entries.items():
            if key.startswith("python:"):
                name = entry.path.name
            elif key == "fbs_module":
                name = "fbs.cp313-win_amd64.pyd"
            elif key == "zlib":
                name = "zlib1.dll"
            else:
                continue
            staged = _write_and_hold(
                stage_binding,
                name,
                _read_entry(entry),
                f"staged:{name}",
            )
            if staged.digest != entry.digest:
                raise OfficialFbsRuntimeError(f"staged {entry.name} digest differs")
            stage_entries.append(staged)
        if xplt_entry is not None:
            staged_xplt = _write_and_hold(
                stage_binding,
                "input.xplt",
                _read_entry(xplt_entry),
                "staged:input.xplt",
            )
            if staged_xplt.digest != xplt_entry.digest:
                raise OfficialFbsRuntimeError("staged XPLT digest differs")
            stage_entries.append(staged_xplt)
        request_path = stage / "request.json"
        helper_path = stage / "official_fbs_helper.py"
        request_entry = _write_and_hold(
            stage_binding,
            request_path.name,
            json.dumps(
                request,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("ascii"),
            "staged:request.json",
        )
        stage_entries.append(request_entry)
        helper_entry = _write_and_hold(
            stage_binding,
            helper_path.name,
            _HELPER_SOURCE.encode("utf-8"),
            "staged:official_fbs_helper.py",
        )
        stage_entries.append(helper_entry)
        executable = stage / "python.exe"
        environment = {
            key: value
            for key, value in os.environ.items()
            if key.casefold() in {"systemroot", "windir"}
        }
        command = [
            os.fspath(executable),
            "-I",
            "-S",
            "-E",
            os.fspath(helper_path),
            os.fspath(request_path),
        ]
        context_digest = hashlib.sha256(
            json.dumps(request, sort_keys=True, separators=(",", ":")).encode("utf-8")
            + _HELPER_SOURCE.encode("utf-8")
        ).hexdigest()
        process_result = _launch_helper(
            command,
            stage=stage,
            release_entries=tuple(stage_entries),
            environment=environment,
            timeout_seconds=timeout_seconds,
            context_digest=context_digest,
        )
        if (
            expected_module_attestation_sha256 is not None
            and process_result.module_attestation_sha256 != expected_module_attestation_sha256
        ):
            raise OfficialFbsRuntimeError("loaded module identity changed after probe")
        if module_attestation_sink is not None:
            if module_attestation_sink:
                raise OfficialFbsRuntimeError("loaded module attestation sink was reused")
            module_attestation_sink.append(process_result.module_attestation_sha256)
        if process_result.return_code != 0:
            raise OfficialFbsRuntimeError(
                f"official FBS helper failed with exit code {process_result.return_code}"
            )
        response_payload = process_result.response
        if len(response_payload) > _MAX_RESPONSE_BYTES:
            raise OfficialFbsRuntimeError("official FBS helper response object is invalid")
        response = _json_object(response_payload)
        allowed_names = {entry.path.name for entry in stage_entries}
        for item in stage.iterdir():
            if not item.is_file() or item.name not in allowed_names:
                raise OfficialFbsRuntimeError("official FBS helper left unexpected output")
        return response
    except OSError as error:
        raise OfficialFbsRuntimeError("unable to execute official FBS helper") from error
    finally:
        cleanup_failures: list[BaseException] = []
        for entry in reversed(stage_entries):
            try:
                if _fbs._digest_fd(entry.owner) != entry.digest:
                    raise OfficialFbsRuntimeError(f"held {entry.name} changed during execution")
                if stage_binding is None:
                    raise OfficialFbsRuntimeError("private helper stage authority is unavailable")
                _delete_stage_entry(stage_binding, entry)
                if not entry.owner.closed:
                    _fbs._close_owned_handle(entry.owner)
            except BaseException as error:
                if not entry.owner.closed:
                    _fbs._retain_cleanup_owner(entry.owner)
                if entry.cleanup_keeper is not None and not entry.cleanup_keeper.closed:
                    _fbs._retain_cleanup_owner(entry.cleanup_keeper)
                cleanup_failures.append(error)
        if (
            stage_binding is not None
            and parent_binding is not None
            and stage_keeper is not None
            and not cleanup_failures
        ):
            try:
                _delete_stage_root(parent_binding, stage_binding, stage_keeper)
            except BaseException as error:
                cleanup_failures.append(error)
        elif stage_binding is not None:
            _fbs._finalize_root_binding(stage_binding)
            if stage_keeper is not None and not stage_keeper.closed:
                _fbs._retain_cleanup_owner(stage_keeper)
        if parent_binding is not None:
            try:
                _fbs._release_root_binding(parent_binding)
            except BaseException as error:
                cleanup_failures.append(error)
        if cleanup_failures:
            raise OfficialFbsRuntimeError("private helper cleanup failed") from cleanup_failures[0]


def _probe_helper(held: _HeldRuntime, timeout_seconds: float) -> _ProbeHelperResult:
    attestation: list[str] = []
    response = _run_helper(
        held,
        {"protocol": _PROTOCOL, "mode": "probe"},
        Path(tempfile.gettempdir()),
        timeout_seconds,
        module_attestation_sink=attestation,
    )
    if len(attestation) != 1:
        raise OfficialFbsRuntimeError("loaded module attestation is unavailable")
    return _ProbeHelperResult(response, attestation[0])


def _paths(
    python_executable: str | Path,
    fbs_module: str | Path,
    zlib: str | Path,
) -> tuple[Path, Path, Path]:
    return (
        _absolute(python_executable, "python_executable"),
        _absolute(fbs_module, "fbs_module"),
        _absolute(zlib, "zlib"),
    )


def probe_official_fbs_runtime(
    python_executable: str | Path,
    fbs_module: str | Path,
    zlib: str | Path,
    *,
    timeout_seconds: float = _HELPER_TIMEOUT_SECONDS,
) -> OfficialFbsRuntime:
    """Verify the exact release profile and issue an opaque runtime receipt."""

    executable, module, zlib_path = _paths(python_executable, fbs_module, zlib)
    with _hold_runtime(executable, module, zlib_path) as held:
        probe_result = _probe_helper(held, timeout_seconds)
        _validate_probe_response(probe_result.response)
        fingerprints = {name: entry.digest for name, entry in held.entries.items()}
    identity_payload = json.dumps(
        {
            "fingerprints": fingerprints,
            "helper_sha256": hashlib.sha256(_HELPER_SOURCE.encode("utf-8")).hexdigest(),
            "module_attestation_sha256": probe_result.module_attestation_sha256,
            "profile": _PROFILE,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    identity = f"{_PROFILE}:{hashlib.sha256(identity_payload).hexdigest()}"
    return OfficialFbsRuntime(
        _RUNTIME_TOKEN,
        executable,
        module,
        zlib_path,
        fingerprints,
        identity,
        probe_result.module_attestation_sha256,
    )


def validate_official_fbs_runtime(runtime: OfficialFbsRuntime) -> OfficialFbsRuntime:
    """Revalidate a probe-issued receipt against all exact source artifacts."""

    if type(runtime) is not OfficialFbsRuntime or runtime._issuance is not _RUNTIME_TOKEN:
        raise TypeError("official FBS runtime must be probe-issued")
    with _hold_runtime(runtime.python_executable, runtime.fbs_module, runtime.zlib) as held:
        current = {name: entry.digest for name, entry in held.entries.items()}
    if current != dict(runtime._fingerprints):
        raise OfficialFbsRuntimeError("official FBS runtime changed after probe")
    return runtime


def _invoke_helper(
    checked: OfficialFbsRuntime,
    path: Path,
    fields: Sequence[str],
    root: Path,
) -> Mapping[str, object]:
    if any(field not in _FIELD_PROFILE for field in fields):
        unsupported = sorted(field for field in fields if field not in _FIELD_PROFILE)
        raise OfficialFbsRuntimeError("unsupported official FBS fields: " + ", ".join(unsupported))
    validate_official_fbs_runtime(checked)
    xplt_entry = _open_file(path, "XPLT")
    try:
        inspect_nonce = secrets.token_hex(32)
        inspect_body: dict[str, object] = {
            "protocol": _PROTOCOL,
            "profile": _PROFILE,
            "nonce": inspect_nonce,
            "mode": "inspect_fields",
            "xplt": "input.xplt",
            "xplt_sha256": xplt_entry.digest,
            "fields": list(fields),
        }
        inspect_sha256 = hashlib.sha256(
            json.dumps(
                inspect_body,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("ascii")
        ).hexdigest()
        inspect_body["request_sha256"] = inspect_sha256
        inspect_binding = _RequestBinding(inspect_nonce, inspect_sha256, xplt_entry.digest)
        with _hold_runtime(checked.python_executable, checked.fbs_module, checked.zlib) as held:
            inspect_response = _run_helper(
                held,
                inspect_body,
                root,
                _HELPER_TIMEOUT_SECONDS,
                xplt_entry=xplt_entry,
                expected_module_attestation_sha256=checked.module_attestation_sha256,
            )
            model_manifest = _validate_inspect_response(
                inspect_response,
                fields,
                inspect_binding,
            )
            model_sha256 = _model_manifest_sha256(model_manifest)
            nonce = secrets.token_hex(32)
            request_body: dict[str, object] = {
                "protocol": _PROTOCOL,
                "profile": _PROFILE,
                "nonce": nonce,
                "mode": "read_fields",
                "xplt": "input.xplt",
                "xplt_sha256": xplt_entry.digest,
                "fields": list(fields),
                "model_manifest": model_manifest,
                "model_sha256": model_sha256,
            }
            request_sha256 = hashlib.sha256(
                json.dumps(
                    request_body,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                ).encode("ascii")
            ).hexdigest()
            request_body["request_sha256"] = request_sha256
            binding = _RequestBinding(
                nonce,
                request_sha256,
                xplt_entry.digest,
                model_sha256,
                model_manifest,
            )
            response = _run_helper(
                held,
                request_body,
                root,
                _HELPER_TIMEOUT_SECONDS,
                xplt_entry=xplt_entry,
                expected_module_attestation_sha256=checked.module_attestation_sha256,
            )
            if _fbs._digest_fd(xplt_entry.owner) != xplt_entry.digest:
                raise OfficialFbsRuntimeError("held XPLT changed during official FBS read")
            result = _validate_field_response(response, fields, binding)
    finally:
        try:
            _fbs._close_owned_handle(xplt_entry.owner)
        except BaseException:
            _fbs._retain_cleanup_owner(xplt_entry.owner)
            raise
    return result


class _OfficialFieldMapping(Mapping[str, object]):
    """Field-only view retaining the exact private official helper result."""

    __slots__ = ("_adapter", "_requested_fields", "_response", "_values")

    def __init__(
        self,
        token: object,
        adapter: _OfficialFbsAdapter,
        requested_fields: tuple[str, ...],
        response: Mapping[str, object],
    ) -> None:
        if token is not _RESULT_TOKEN:
            raise TypeError("official FBS field mappings are adapter-issued")
        try:
            detached = json.loads(
                json.dumps(
                    response,
                    allow_nan=False,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise OfficialFbsRuntimeError("official FBS result is not canonical JSON") from error
        if not isinstance(detached, dict):  # pragma: no cover - schema guard
            raise OfficialFbsRuntimeError("official FBS result is not an object")
        _validate_normalized_result(detached, requested_fields)
        raw_values = detached["values"]
        raw_non_finite = detached["non_finite_fields"]
        if not isinstance(raw_values, dict) or not isinstance(raw_non_finite, list):
            raise OfficialFbsRuntimeError("official FBS result field data is invalid")
        values: dict[str, object] = dict(raw_values)
        for field in raw_non_finite:
            if not isinstance(field, str):  # pragma: no cover - schema guard
                raise OfficialFbsRuntimeError("official FBS result field name is invalid")
            values[field] = math.nan
        self._adapter = adapter
        self._requested_fields = requested_fields
        self._response = MappingProxyType(detached)
        self._values = MappingProxyType(values)

    def __getitem__(self, key: str) -> object:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)


class _OfficialFbsAdapter:
    __slots__ = ("_attempt_root", "_issuance", "_runtime")

    def __new__(cls, token: object | None = None, *_args: object) -> _OfficialFbsAdapter:
        if cls is not _OfficialFbsAdapter or token is not _ADAPTER_TOKEN:
            raise TypeError("official FBS adapters are factory-issued")
        return super().__new__(cls)

    def __init__(self, token: object, runtime: OfficialFbsRuntime, attempt_root: Path) -> None:
        if token is not _ADAPTER_TOKEN:
            raise TypeError("official FBS adapters are factory-issued")
        self._runtime = runtime
        self._attempt_root = attempt_root
        self._issuance = _ADAPTER_TOKEN

    def __init_subclass__(cls, **kwargs: object) -> None:
        del kwargs
        raise TypeError("official FBS adapters cannot be subclassed")

    def read_fields(self, xplt_path: Path, fields: Sequence[str]) -> Mapping[str, object]:
        response = _invoke_helper(self._runtime, xplt_path, fields, self._attempt_root)
        requested_fields = tuple(fields)
        return _OfficialFieldMapping(
            _RESULT_TOKEN,
            self,
            requested_fields,
            response,
        )


def _valid_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _validate_normalized_result(
    response: Mapping[str, object],
    requested_fields: Sequence[str],
) -> Mapping[str, object]:
    _exact_keys(
        response,
        {
            "available_fields",
            "model_manifest",
            "model_sha256",
            "non_finite_fields",
            "protocol",
            "values",
            "xplt_sha256",
        },
        "normalized official FBS result",
    )
    if response["protocol"] != _PROTOCOL:
        raise OfficialFbsRuntimeError("official FBS result protocol mismatch")
    if not _valid_sha256(response["xplt_sha256"]) or not _valid_sha256(response["model_sha256"]):
        raise OfficialFbsRuntimeError("official FBS result digest is invalid")
    model = _validate_model_manifest(response["model_manifest"], requested_fields)
    if _model_manifest_sha256(model) != response["model_sha256"]:
        raise OfficialFbsRuntimeError("official FBS result model binding is invalid")
    available = response["available_fields"]
    raw_model_available = model["available_fields"]
    if not isinstance(available, list) or not isinstance(raw_model_available, list):
        raise OfficialFbsRuntimeError("official FBS available fields are invalid")
    expected_available = [item["name"] for item in raw_model_available]
    if available != expected_available:
        raise OfficialFbsRuntimeError("official FBS available fields changed")
    values = response["values"]
    non_finite = response["non_finite_fields"]
    if (
        not isinstance(values, dict)
        or any(not isinstance(key, str) for key in values)
        or not isinstance(non_finite, list)
        or any(not isinstance(field, str) for field in non_finite)
        or len(set(non_finite)) != len(non_finite)
    ):
        raise OfficialFbsRuntimeError("official FBS result fields are invalid")
    requested = set(requested_fields)
    if not set(values).issubset(requested) or not set(non_finite).issubset(requested):
        raise OfficialFbsRuntimeError("official FBS result includes unrequested fields")
    if set(values).intersection(non_finite):
        raise OfficialFbsRuntimeError("official FBS result field status is contradictory")
    model_fields = model["requested_fields"]
    if not isinstance(model_fields, dict):  # pragma: no cover - manifest guard
        raise OfficialFbsRuntimeError("official FBS model fields are invalid")
    state_count = model["state_count"]
    node_count = model["node_count"]
    element_count = model["element_count"]
    if not all(isinstance(value, int) for value in (state_count, node_count, element_count)):
        raise OfficialFbsRuntimeError("official FBS model cardinality is invalid")
    for field in requested_fields:
        present = field in available
        has_result = field in values or field in non_finite
        if present is not has_result:
            raise OfficialFbsRuntimeError(f"{field} official FBS result is incomplete")
        if field not in values:
            continue
        summary = values[field]
        model_field = model_fields.get(field)
        if not isinstance(summary, dict) or not isinstance(model_field, dict):
            raise OfficialFbsRuntimeError(f"{field} official FBS summary is invalid")
        _exact_keys(
            summary,
            {
                "components",
                "count",
                "entity_count",
                "field_index",
                "maximum",
                "minimum",
                "state_count",
            },
            f"{field} official FBS summary",
        )
        association = model_field["association"]
        entity_count = node_count if association == "POINT_DATA" else element_count
        components = model_field["components"]
        cardinality_names = (
            "components",
            "count",
            "entity_count",
            "field_index",
            "state_count",
        )
        if any(
            isinstance(summary[name], bool) or not isinstance(summary[name], int)
            for name in cardinality_names
        ):
            raise OfficialFbsRuntimeError(f"{field} official FBS summary is invalid")
        if (
            summary["components"] != components
            or summary["entity_count"] != entity_count
            or summary["field_index"] != model_field["field_index"]
            or summary["state_count"] != state_count
            or summary["count"] != entity_count * components * state_count
        ):
            raise OfficialFbsRuntimeError(f"{field} official FBS cardinality changed")
        minimum = _number(summary["minimum"], f"{field} minimum")
        maximum = _number(summary["maximum"], f"{field} maximum")
        if minimum > maximum:
            raise OfficialFbsRuntimeError(f"{field} official FBS range is invalid")
    return response


def _same_field_values(
    validation: _fbs.FbsValidation,
    raw_result: _OfficialFieldMapping,
) -> bool:
    if tuple(validation.values) != tuple(raw_result):
        return False
    non_finite = set(raw_result._response["non_finite_fields"])
    for field in raw_result:
        actual = validation.values[field]
        expected = raw_result[field]
        if field in non_finite:
            if not (
                isinstance(actual, float)
                and isinstance(expected, float)
                and math.isnan(actual)
                and math.isnan(expected)
            ):
                return False
        elif actual != expected:
            return False
    return True


def _adopt_official_fbs_result(
    validation: _fbs.FbsValidation,
    raw_result: object,
    adapter: object,
) -> OfficialFbsResultReceipt:
    validation_record = _fbs._issued_validation_record(validation)
    authority_record = _fbs._authority_record(validation_record.authority)
    if (
        type(raw_result) is not _OfficialFieldMapping
        or type(adapter) is not _OfficialFbsAdapter
        or raw_result._adapter is not adapter
        or authority_record.adapter is not adapter
        or validation_record.official is not True
        or validation_record.provenance != "official"
        or validation_record.official_result is not None
        or raw_result._requested_fields != validation_record.requested_fields
        or not _same_field_values(validation, raw_result)
    ):
        raise TypeError("official FBS result issuance binding is invalid")
    runtime = validate_official_fbs_runtime(adapter._runtime)
    response = _validate_normalized_result(
        raw_result._response,
        validation_record.requested_fields,
    )
    if (
        response["xplt_sha256"] != validation_record.digest_before
        or response["xplt_sha256"] != validation_record.digest_after
        or validation_record.runtime_identity != runtime.runtime_identity
        or authority_record.profile != runtime.profile
    ):
        raise TypeError("official FBS result context binding is invalid")
    model = response["model_manifest"]
    values = response["values"]
    if not isinstance(model, dict) or not isinstance(values, dict):
        raise TypeError("official FBS result schema is invalid")
    frozen_model = _fbs._freeze_value(model)
    if not isinstance(frozen_model, Mapping):  # pragma: no cover - schema guard
        raise TypeError("official FBS model receipt is invalid")
    receipt = OfficialFbsResultReceipt(_RESULT_TOKEN)
    result_record = _OfficialFbsResultRecord(
        receipt=receipt,
        validation=validation,
        adapter=adapter,
        runtime=runtime,
        xplt_path=validation_record.xplt_path,
        xplt_sha256=validation_record.digest_before,
        model_manifest=frozen_model,
        model_manifest_sha256=str(response["model_sha256"]),
        requested_fields=validation_record.requested_fields,
        values=validation_record.values,
        transport="private-named-pipe",
    )
    object.__setattr__(receipt, "_record", result_record)
    validation_record.official_result = result_record
    return receipt


def _official_result_record(value: object) -> _OfficialFbsResultRecord:
    if type(value) is not OfficialFbsResultReceipt:
        raise TypeError("value is not an exact OfficialFbsResultReceipt instance")
    try:
        record = object.__getattribute__(value, "_record")
    except AttributeError as error:
        raise TypeError("OfficialFbsResultReceipt was not manager-issued") from error
    if not isinstance(record, _OfficialFbsResultRecord) or record.receipt is not value:
        raise TypeError("OfficialFbsResultReceipt binding is invalid")
    try:
        validation_record = _fbs._issued_validation_record(record.validation)
        authority_record = _fbs._authority_record(validation_record.authority)
        _fbs._require_validation_xplt_owner(record.validation)
    except TypeError as error:
        raise TypeError("OfficialFbsResultReceipt authority is unavailable") from error
    if (
        validation_record.official_result is not record
        or validation_record.official is not True
        or validation_record.provenance != "official"
        or validation_record.xplt_path != record.xplt_path
        or validation_record.digest_before != record.xplt_sha256
        or validation_record.digest_after != record.xplt_sha256
        or validation_record.requested_fields != record.requested_fields
        or validation_record.values is not record.values
        or authority_record.adapter is not record.adapter
        or record.adapter._runtime is not record.runtime
    ):
        raise TypeError("OfficialFbsResultReceipt binding is invalid")
    checked = validate_official_fbs_runtime(record.runtime)
    if (
        checked.runtime_identity != validation_record.runtime_identity
        or authority_record.profile != checked.profile
    ):
        raise TypeError("OfficialFbsResultReceipt authority is unavailable")
    return record


def _manifest_count(record: _OfficialFbsResultRecord, name: str) -> int:
    value = record.model_manifest[name]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("OfficialFbsResultReceipt binding is invalid")
    return value


def require_official_fbs_result(
    validation: _fbs.FbsValidation,
) -> OfficialFbsResultReceipt:
    """Return the manager-issued receipt for an exact live official validation."""

    validation_record = _fbs._issued_validation_record(validation)
    record = validation_record.official_result
    if not isinstance(record, _OfficialFbsResultRecord):
        raise TypeError("FbsValidation has no official FBS result receipt")
    _official_result_record(record.receipt)
    return record.receipt


def _official_adapter_identity(
    adapter: object,
    runtime_identity: str,
    attempt_root: Path | None,
) -> str | None:
    """Return the official profile only for this factory's exact live adapter."""

    if type(adapter) is not _OfficialFbsAdapter:
        return None
    if adapter._issuance is not _ADAPTER_TOKEN:
        raise OfficialFbsRuntimeError("official FBS adapter issuance is invalid")
    if attempt_root is None or adapter._attempt_root != attempt_root:
        raise OfficialFbsRuntimeError("official FBS adapter attempt binding changed")
    checked = validate_official_fbs_runtime(adapter._runtime)
    if checked.runtime_identity != runtime_identity:
        raise OfficialFbsRuntimeError("official FBS adapter runtime binding changed")
    return _PROFILE


def _validate_official_fbs_authority(
    runtime: OfficialFbsRuntime,
    authority: _fbs.FbsAdapterAuthority,
    attempt_root: Path,
) -> _fbs.FbsAdapterAuthority:
    """Bind a live manager authority back to its exact probed runtime profile."""

    checked = validate_official_fbs_runtime(runtime)
    record = _fbs._authority_record(authority)
    if (
        record.official is not True
        or record.provenance != "official"
        or record.runtime_identity != checked.runtime_identity
        or record.attempt_root != attempt_root
    ):
        raise OfficialFbsRuntimeError("official FBS authority profile binding differs")
    return authority


def open_official_fbs_manager(
    runtime: OfficialFbsRuntime,
    attempt_root: str | Path,
) -> _fbs.FbsAdapterManager:
    """Bind a live, revalidated official runtime to one attempt root."""

    try:
        checked = validate_official_fbs_runtime(runtime)
    except OfficialFbsRuntimeError as error:
        raise OfficialFbsRuntimeError(
            f"official FBS runtime changed after probe: {error}"
        ) from error
    root = _absolute(attempt_root, "attempt_root")
    return _fbs.FbsAdapterManager(
        _OfficialFbsAdapter(_ADAPTER_TOKEN, checked, root),
        checked.runtime_identity,
        root,
    )


_HELPER_SOURCE = r"""from __future__ import annotations

import ctypes
import hashlib
import json
import math
import os
import pathlib
import secrets
import sys
import threading
from ctypes import wintypes

PROTOCOL = 1
PROFILE = "official-fbs-3.1-cp313"
FIELD_PROFILE = {
    "displacement": ("displacement", "POINT_DATA", 3),
    "stress": ("stress", "CELL_DATA", 9),
    "Lagrange strain": ("Lagrange_strain", "CELL_DATA", 9),
    "pressure": ("pressure", "CELL_DATA", 1),
}
FIELD_COMPONENT_PROFILE = {
    "displacement": ("DATA_VECTOR", "displacement"),
    "stress": ("DATA_TENSOR2", "stress"),
    "Lagrange strain": ("DATA_TENSOR2", "Lagrange strain"),
    "pressure": ("DATA_SCALAR", "pressure"),
}
MAX_TOTAL_VTK_BYTES = 512 * 1024 * 1024

def fail(message):
    raise RuntimeError(message)

def exact_keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != expected:
        fail(label + " schema")

def consume(lines, index, count):
    values = []
    while len(values) < count and index < len(lines):
        tokens = lines[index].split()
        index += 1
        if not tokens:
            continue
        if len(values) + len(tokens) > count:
            fail("VTK array cardinality")
        values.extend(float(token) for token in tokens)
    if len(values) != count:
        fail("VTK array truncated")
    return values, index

def store_array(arrays, name, value):
    if name in arrays:
        fail("duplicate VTK array")
    arrays[name] = value

def parse_vtk(payload):
    try:
        lines = payload.decode("ascii", errors="strict").splitlines()
    except UnicodeDecodeError:
        fail("VTK encoding")
    association = None
    association_count = 0
    arrays = {}
    index = 0
    while index < len(lines):
        parts = lines[index].split()
        index += 1
        if not parts:
            continue
        if parts[0] in {"POINT_DATA", "CELL_DATA"}:
            if len(parts) != 2:
                fail("VTK association")
            association = parts[0]
            association_count = int(parts[1])
            if association_count <= 0:
                fail("VTK association count")
            continue
        if association is None:
            continue
        if parts[0] == "VECTORS":
            if len(parts) != 3:
                fail("VTK vectors")
            values, index = consume(lines, index, association_count * 3)
            store_array(arrays, parts[1], (association, 3, association_count, values))
        elif parts[0] == "TENSORS":
            if len(parts) != 3:
                fail("VTK tensors")
            values, index = consume(lines, index, association_count * 9)
            store_array(arrays, parts[1], (association, 9, association_count, values))
        elif parts[0] == "SCALARS":
            if len(parts) not in {3, 4}:
                fail("VTK scalars")
            components = int(parts[3]) if len(parts) == 4 else 1
            while index < len(lines) and not lines[index].split():
                index += 1
            if index >= len(lines) or lines[index].split()[:1] != ["LOOKUP_TABLE"]:
                fail("VTK lookup table")
            index += 1
            values, index = consume(lines, index, association_count * components)
            store_array(
                arrays,
                parts[1],
                (association, components, association_count, values),
            )
    return arrays

def prime_private_pipe_api():
    thread = threading.Thread(target=lambda: None)
    thread.start()
    thread.join()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_named_pipe = kernel32.CreateNamedPipeW
    create_named_pipe.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
    ]
    create_named_pipe.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    name = r"\\.\pipe\febio-cae-prime-" + secrets.token_hex(32)
    handle = create_named_pipe(
        name,
        0x00000001 | 0x00080000,
        0x00000008,
        1,
        4096,
        4096,
        0,
        None,
    )
    if handle == ctypes.c_void_p(-1).value:
        fail("VTK named pipe creation")
    if not close_handle(handle):
        fail("VTK named pipe close")

def export_vtk_to_private_pipes(fbs, model, state_count):
    if os.name != "nt":
        fail("named pipe export platform")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_named_pipe = kernel32.CreateNamedPipeW
    create_named_pipe.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
    ]
    create_named_pipe.restype = wintypes.HANDLE
    connect_named_pipe = kernel32.ConnectNamedPipe
    connect_named_pipe.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
    connect_named_pipe.restype = wintypes.BOOL
    read_file = kernel32.ReadFile
    read_file.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    read_file.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    base = r"\\.\pipe\febio-cae-" + secrets.token_hex(32)
    labels = ["base"] if state_count == 1 else ["t%d" % index for index in range(state_count)]
    pipe_names = {
        label: base + (".vtk" if label == "base" else "." + label + ".vtk")
        for label in labels
    }
    handles = {}
    payloads = {label: bytearray() for label in labels}
    failures = []
    threads = []
    lock = threading.Lock()
    total_bytes = 0

    def drain(label, handle):
        nonlocal total_bytes
        try:
            connected = connect_named_pipe(handle, None)
            if not connected and ctypes.get_last_error() != 535:
                fail("VTK named pipe connection")
            buffer = ctypes.create_string_buffer(64 * 1024)
            transferred = wintypes.DWORD()
            oversized = False
            while read_file(handle, buffer, len(buffer), ctypes.byref(transferred), None):
                chunk = buffer.raw[: transferred.value]
                with lock:
                    remaining = MAX_TOTAL_VTK_BYTES - total_bytes
                    accepted = chunk[: max(0, remaining)]
                    total_bytes += len(accepted)
                    if len(chunk) > remaining:
                        oversized = True
                payloads[label].extend(accepted)
            if oversized:
                fail("VTK output size")
        except BaseException as error:
            with lock:
                failures.append(error)

    try:
        invalid_handle = ctypes.c_void_p(-1).value
        for label, name in pipe_names.items():
            handle = create_named_pipe(
                name,
                0x00000001 | 0x00080000,
                0x00000008,
                1,
                64 * 1024,
                64 * 1024,
                0,
                None,
            )
            if handle == invalid_handle:
                fail("VTK named pipe creation")
            handles[label] = handle
            thread = threading.Thread(target=drain, args=(label, handle), daemon=True)
            thread.start()
            threads.append(thread)
        exporter = fbs.post.vtkExport()
        exporter.ExportAllStates(True)
        exporter.WriteSeriesFile(False)
        exporter.ExportSelectedElementsOnly(False)
        if exporter.Save(model, base + ".vtk") is not True:
            fail("vtkExport")
        for thread in threads:
            thread.join()
        if failures:
            raise failures[0]
        if any(not payload for payload in payloads.values()):
            fail("VTK output size")
        return [bytes(payloads[label]) for label in labels]
    finally:
        for handle in handles.values():
            close_handle(handle)

def bound_request(request, expected_keys, stage):
    exact_keys(request, expected_keys, "request")
    if request["profile"] != PROFILE:
        fail("profile")
    nonce = request["nonce"]
    request_sha256 = request["request_sha256"]
    xplt_sha256 = request["xplt_sha256"]
    if any(
        not isinstance(value, str) or len(value) != 64
        for value in (nonce, request_sha256, xplt_sha256)
    ):
        fail("request binding")
    request_body = dict(request)
    request_body.pop("request_sha256")
    observed_request_sha256 = hashlib.sha256(
        json.dumps(request_body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
            "ascii"
        )
    ).hexdigest()
    if observed_request_sha256 != request_sha256:
        fail("request digest")
    fields = request["fields"]
    if (
        not isinstance(fields, list)
        or not fields
        or any(not isinstance(field, str) or field not in FIELD_PROFILE for field in fields)
        or len(set(fields)) != len(fields)
    ):
        fail("requested fields")
    xplt_name = request["xplt"]
    if xplt_name != "input.xplt":
        fail("XPLT path")
    xplt = stage / xplt_name
    if hashlib.sha256(xplt.read_bytes()).hexdigest() != xplt_sha256:
        fail("XPLT digest")
    return fields, xplt, nonce, request_sha256, xplt_sha256

def model_manifest(fbs, model, fields):
    state_count = int(model.States())
    if state_count <= 0 or state_count > 10000:
        fail("XPLT states")
    mesh = model.GetFEMesh(0)
    node_count = int(mesh.Nodes())
    element_count = int(mesh.Elements())
    if node_count <= 0 or element_count <= 0:
        fail("XPLT mesh cardinality")
    state_times = []
    for state_index in range(state_count):
        state = model.State(state_index)
        state_time = float(state.time)
        if not math.isfinite(state_time):
            fail("XPLT state time")
        if len(state.nodeData) != node_count or len(state.elemData) != element_count:
            fail("XPLT state cardinality")
        state_times.append(state_time)
    manager = model.GetDataManager()
    available_fields = []
    field_objects = {}
    for field_index in range(int(manager.DataFields())):
        field_object = manager.DataField(field_index)
        name = field_object.name
        if (
            not isinstance(name, str)
            or not name
            or name in field_objects
            or field_index > 10000
        ):
            fail("FBS data fields")
        available_fields.append({"index": field_index, "name": name})
        field_objects[name] = field_object
    requested_manifest = {}
    for field in fields:
        field_object = field_objects.get(field)
        if field_object is None:
            continue
        tensor_name, component_name = FIELD_COMPONENT_PROFILE[field]
        tensor_type = getattr(fbs.post.DataTensorType, tensor_name)
        component_names = [
            field_object.ComponentName(index, tensor_type)
            for index in range(int(field_object.Components(tensor_type)))
        ]
        if component_names != [component_name]:
            fail("FBS field component identity")
        vtk_name, association, components = FIELD_PROFILE[field]
        field_index = next(
            item["index"] for item in available_fields if item["name"] == field
        )
        requested_manifest[field] = {
            "association": association,
            "component_index": 0,
            "component_name": component_name,
            "components": components,
            "field_index": field_index,
            "tensor_type": tensor_name,
            "vtk_name": vtk_name,
        }
    return {
        "available_fields": available_fields,
        "element_count": element_count,
        "node_count": node_count,
        "requested_fields": requested_manifest,
        "state_count": state_count,
        "state_times": state_times,
    }

def inspect_fields(fbs, request, stage):
    fields, xplt, nonce, request_sha256, xplt_sha256 = bound_request(
        request,
        {
            "fields",
            "mode",
            "nonce",
            "profile",
            "protocol",
            "request_sha256",
            "xplt",
            "xplt_sha256",
        },
        stage,
    )
    model = fbs.post.ReadPlotFile(str(xplt))
    if model is None:
        fail("ReadPlotFile")
    return {
        "model": model_manifest(fbs, model, fields),
        "nonce": nonce,
        "profile": PROFILE,
        "protocol": PROTOCOL,
        "request_sha256": request_sha256,
        "xplt_sha256": xplt_sha256,
    }

def read_fields(fbs, request, stage):
    fields, xplt, nonce, request_sha256, xplt_sha256 = bound_request(
        request,
        {
            "fields",
            "mode",
            "model_manifest",
            "model_sha256",
            "nonce",
            "profile",
            "protocol",
            "request_sha256",
            "xplt",
            "xplt_sha256",
        },
        stage,
    )
    expected_model = request["model_manifest"]
    model_sha256 = request["model_sha256"]
    if not isinstance(expected_model, dict) or not isinstance(model_sha256, str):
        fail("model binding")
    observed_model_sha256 = hashlib.sha256(
        json.dumps(
            expected_model,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
    ).hexdigest()
    if observed_model_sha256 != model_sha256:
        fail("model digest")
    model = fbs.post.ReadPlotFile(str(xplt))
    if model is None:
        fail("ReadPlotFile")
    observed_model = model_manifest(fbs, model, fields)
    if observed_model != expected_model:
        fail("model identity changed")
    state_count = expected_model["state_count"]
    parsed = [
        parse_vtk(payload)
        for payload in export_vtk_to_private_pipes(fbs, model, state_count)
    ]
    field_data = {}
    available = [item["name"] for item in expected_model["available_fields"]]
    for field in fields:
        source_name, association, components = FIELD_PROFILE[field]
        if field not in available:
            continue
        states = []
        for state_index, arrays in enumerate(parsed):
            raw = arrays.get(source_name)
            if raw is None:
                fail("missing VTK field")
            actual_association, actual_components, entity_count, state_values = raw
            if (
                actual_association != association
                or actual_components != components
                or entity_count
                != (
                    expected_model["node_count"]
                    if association == "POINT_DATA"
                    else expected_model["element_count"]
                )
                or len(state_values) != entity_count * components
            ):
                fail("VTK field shape")
            states.append(
                {
                    "index": state_index,
                    "time": expected_model["state_times"][state_index],
                    "entity_count": entity_count,
                    "values": [value if math.isfinite(value) else None for value in state_values],
                }
            )
        field_data[field] = {
            "vtk_name": source_name,
            "association": association,
            "components": components,
            "field_index": expected_model["requested_fields"][field]["field_index"],
            "states": states,
        }
    return {
        "protocol": PROTOCOL,
        "profile": PROFILE,
        "nonce": nonce,
        "request_sha256": request_sha256,
        "xplt_sha256": xplt_sha256,
        "available_fields": available,
        "field_data": field_data,
        "model_sha256": model_sha256,
    }

def main():
    if len(sys.argv) != 2:
        fail("arguments")
    request_path = pathlib.Path(sys.argv[1])
    request = json.loads(request_path.read_text(encoding="ascii"))
    if not isinstance(request, dict) or request.get("protocol") != PROTOCOL:
        fail("protocol")
    _dll_cookie = None
    if hasattr(os, "add_dll_directory"):
        _dll_cookie = os.add_dll_directory(str(pathlib.Path(sys.executable).parent))
    import fbs
    prime_private_pipe_api()
    sys.stderr.write("READY\n")
    sys.stderr.flush()
    if sys.stdin.buffer.read(1) != b"1":
        fail("attestation handshake")
    if request.get("mode") == "probe":
        exact_keys(request, {"protocol", "mode"}, "request")
        if not callable(getattr(fbs.post, "ReadPlotFile", None)):
            fail("ReadPlotFile API")
        if not callable(getattr(fbs.post, "vtkExport", None)):
            fail("vtkExport API")
        response = {
            "protocol": PROTOCOL,
            "python": "%d.%d" % sys.version_info[:2],
            "module": fbs.__name__,
            "api": ["ReadPlotFile", "vtkExport"],
        }
    elif request.get("mode") == "inspect_fields":
        response = inspect_fields(fbs, request, request_path.parent)
    elif request.get("mode") == "read_fields":
        response = read_fields(fbs, request, request_path.parent)
    else:
        fail("mode")
    sys.stderr.write("COMPLETE\n")
    sys.stderr.flush()
    if sys.stdin.buffer.read(1) != b"2":
        fail("completion attestation handshake")
    json.dump(response, sys.stdout, allow_nan=False, sort_keys=True, separators=(",", ":"))
    sys.stdout.write("\n")
    sys.stdout.flush()

main()
"""
