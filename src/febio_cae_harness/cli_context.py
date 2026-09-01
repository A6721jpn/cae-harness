"""Registered-root case context for the installed headless CLI.

Only initial root registration accepts a filesystem root.  Every later operation
reconstructs a case from the durable registry, revalidates its exact directory
identities, and then delegates evidence changes to the existing authority owners.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, NoReturn, cast

from .contracts import IntentContract, JSONInput
from .evidence import EvidenceIntegrityError, EvidenceStore
from .intent_lifecycle import IntentLifecycle, IntentLifecycleResult
from .workspace import (
    CaseWorkspace,
    ValidatedCaseWorkspace,
    WorkspaceBoundaryError,
    _identity_stamp,
    _registered_case_stamp,
    _reject_reparse_alias,
)

__all__ = ["CaseContextError", "CaseContextService"]

_CLI_SCHEMA = "febio-cae-cli/v1"
_REGISTRY_SCHEMA = "case-registry-v1"
_REGISTRY_FILE = "registry.json"
_REGISTRY_LOCK_FILE = "registry.lock"
_ROOT_PREFIX = "root-"
_CASE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_REGISTRY_THREAD_LOCK = threading.RLock()
_TEST_FACTORY = object()


class CaseContextError(RuntimeError):
    """Stable CLI-context failure with a machine-readable code."""

    __slots__ = ("code", "retryable")

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class _OpenedContext:
    root_id: str
    case: CaseWorkspace
    store: EvidenceStore
    lifecycle: IntentLifecycle
    result: IntentLifecycleResult


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise CaseContextError("INVALID_INPUT", "value is not canonical JSON") from error


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _root_id_for(path: Path, stamp: tuple[int, int]) -> str:
    projection = {
        "schema_version": _REGISTRY_SCHEMA,
        "path": os.path.normcase(os.fspath(path)),
        "stamp": [stamp[0], stamp[1]],
    }
    return _ROOT_PREFIX + _digest(projection)


def _validate_case_id(value: str) -> str:
    if not isinstance(value, str) or _CASE_ID.fullmatch(value) is None:
        raise CaseContextError(
            "INVALID_INPUT",
            "case_id must be a 1-64 character ASCII identifier",
        )
    return value


def _validate_sha256(value: str, label: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise CaseContextError("INVALID_INPUT", f"{label} must be a lowercase SHA-256 digest")
    return value


def _normal_path(value: Path) -> Path:
    return Path(os.path.abspath(os.fspath(value)))


def _is_exact_ancestor(ancestor: Path, candidate: Path, label: str) -> bool:
    ancestor_stamp = _identity_stamp(ancestor, f"{label} ancestor")
    current = _reject_reparse_alias(candidate, f"{label} candidate")
    while True:
        if _identity_stamp(current, f"{label} candidate ancestor") == ancestor_stamp:
            return True
        if current == current.parent:
            return False
        current = current.parent


def _exact_trees_overlap(first: Path, second: Path, label: str) -> bool:
    return _is_exact_ancestor(first, second, label) or _is_exact_ancestor(second, first, label)


def _local_app_data_known_folder() -> Path:
    if os.name != "nt":
        raise CaseContextError(
            "REGISTRY_AUTHORITY_REQUIRED",
            "LocalAppData Known Folder is unavailable on this platform",
        )
    import ctypes
    import uuid

    class GUID(ctypes.Structure):
        _fields_ = [
            ("data1", ctypes.c_uint32),
            ("data2", ctypes.c_uint16),
            ("data3", ctypes.c_uint16),
            ("data4", ctypes.c_ubyte * 8),
        ]

    raw = uuid.UUID("f1b32785-6fba-4fcf-9d55-7b8e7f157091").bytes_le
    identifier = GUID.from_buffer_copy(raw)
    path_pointer = ctypes.c_wchar_p()
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    get_path = shell32.SHGetKnownFolderPath
    get_path.argtypes = [
        ctypes.POINTER(GUID),
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_wchar_p),
    ]
    get_path.restype = ctypes.c_long
    result = int(get_path(ctypes.byref(identifier), 0, None, ctypes.byref(path_pointer)))
    if result != 0 or not path_pointer.value:
        raise CaseContextError(
            "REGISTRY_AUTHORITY_REQUIRED",
            f"cannot resolve LocalAppData Known Folder ({result})",
        )
    try:
        return _normal_path(Path(path_pointer.value))
    finally:
        ctypes.windll.ole32.CoTaskMemFree(path_pointer)


def _initial_registry() -> dict[str, object]:
    return {"schema_version": _REGISTRY_SCHEMA, "roots": [], "cases": []}


def _registry_document(body: Mapping[str, object]) -> dict[str, object]:
    canonical = dict(body)
    canonical["sha256"] = _digest(body)
    return canonical


def _file_identity(metadata: os.stat_result) -> tuple[int, int]:
    return int(metadata.st_dev), int(metadata.st_ino)


def _read_exact_bytes(path: Path, label: str) -> bytes:
    absolute = _reject_reparse_alias(path, label)
    try:
        path_before = absolute.lstat()
    except OSError as error:
        raise CaseContextError("IO_OR_LOCK_FAILURE", f"cannot read {label}") from error
    if not stat.S_ISREG(path_before.st_mode) or int(path_before.st_nlink) != 1:
        raise CaseContextError("EVIDENCE_INTEGRITY_FAILURE", f"{label} is not one regular file")
    try:
        with absolute.open("rb") as stream:
            held_before = os.fstat(stream.fileno())
            if _file_identity(held_before) != _file_identity(path_before):
                raise CaseContextError(
                    "EVIDENCE_INTEGRITY_FAILURE", f"{label} identity changed before read"
                )
            data = stream.read()
            held_after = os.fstat(stream.fileno())
            path_after = absolute.lstat()
    except CaseContextError:
        raise
    except OSError as error:
        raise CaseContextError("IO_OR_LOCK_FAILURE", f"cannot read {label}") from error
    stable_fields = (
        "st_dev",
        "st_ino",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
        "st_nlink",
    )
    if _file_identity(held_after) != _file_identity(path_after) or any(
        int(getattr(held_before, name)) != int(getattr(held_after, name))
        or int(getattr(path_before, name)) != int(getattr(path_after, name))
        for name in stable_fields
    ):
        raise CaseContextError("EVIDENCE_INTEGRITY_FAILURE", f"{label} changed during read")
    return data


def _parse_json(data: bytes, label: str) -> object:
    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for name, value in pairs:
            if name in result:
                raise CaseContextError("INVALID_INPUT", f"{label} contains a duplicate field")
            result[name] = value
        return result

    try:
        text = data.decode("utf-8")
        return json.loads(
            text,
            parse_constant=lambda value: _reject_nonfinite(value, label),
            object_pairs_hook=unique_object,
        )
    except CaseContextError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CaseContextError("INVALID_INPUT", f"{label} is not valid UTF-8 JSON") from error


def _reject_nonfinite(value: str, label: str) -> NoReturn:
    raise CaseContextError("INVALID_INPUT", f"{label} contains non-finite JSON: {value}")


def load_intent_document(path: Path) -> IntentContract:
    data = _read_exact_bytes(path, "intent file")
    payload = _parse_json(data, "intent file")
    if not isinstance(payload, dict):
        raise CaseContextError("INVALID_INPUT", "intent file must contain one JSON object")
    if data != _canonical_bytes(payload) + b"\n":
        raise CaseContextError("INVALID_INPUT", "intent file bytes are not canonical")
    try:
        intent = IntentContract.from_mapping(payload)
    except (TypeError, ValueError) as error:
        raise CaseContextError(
            "INVALID_INPUT", "intent file violates the intent contract"
        ) from error
    if intent.to_dict() != payload:
        raise CaseContextError("INVALID_INPUT", "intent file is incomplete or non-canonical")
    return intent


def load_answer_document(path: Path) -> tuple[JSONInput, str, str | None]:
    data = _read_exact_bytes(path, "answer file")
    payload = _parse_json(data, "answer file")
    if not isinstance(payload, dict) or set(payload) - {"value", "source", "detail"}:
        raise CaseContextError(
            "INVALID_INPUT", "answer file must contain only value, source, and optional detail"
        )
    if set(payload) < {"value", "source"}:
        raise CaseContextError("INVALID_INPUT", "answer file requires value and source")
    if data != _canonical_bytes(payload) + b"\n":
        raise CaseContextError("INVALID_INPUT", "answer file bytes are not canonical")
    source = payload["source"]
    detail = payload.get("detail")
    if not isinstance(source, str) or not source.strip():
        raise CaseContextError("INVALID_INPUT", "answer source must be non-empty")
    if detail is not None and not isinstance(detail, str):
        raise CaseContextError("INVALID_INPUT", "answer detail must be a string or null")
    return payload["value"], source, detail


def _validate_registry(body: object) -> dict[str, object]:
    if not isinstance(body, dict) or set(body) != {"schema_version", "roots", "cases"}:
        raise CaseContextError("EVIDENCE_INTEGRITY_FAILURE", "case registry body is invalid")
    if body["schema_version"] != _REGISTRY_SCHEMA:
        raise CaseContextError("EVIDENCE_INTEGRITY_FAILURE", "case registry schema is invalid")
    roots = body["roots"]
    cases = body["cases"]
    if not isinstance(roots, list) or not isinstance(cases, list):
        raise CaseContextError("EVIDENCE_INTEGRITY_FAILURE", "case registry lists are invalid")
    root_ids: set[str] = set()
    root_paths: set[str] = set()
    for record in roots:
        if not isinstance(record, dict) or set(record) != {"root_id", "path", "stamp"}:
            raise CaseContextError("EVIDENCE_INTEGRITY_FAILURE", "registered root is invalid")
        root_id = record["root_id"]
        path = record["path"]
        stamp = record["stamp"]
        if (
            not isinstance(root_id, str)
            or not root_id.startswith(_ROOT_PREFIX)
            or _DIGEST.fullmatch(root_id[len(_ROOT_PREFIX) :]) is None
            or not isinstance(path, str)
            or not Path(path).is_absolute()
            or not isinstance(stamp, list)
            or len(stamp) != 2
            or any(type(part) is not int or part < 0 for part in stamp)
        ):
            raise CaseContextError("EVIDENCE_INTEGRITY_FAILURE", "registered root is invalid")
        path_key = os.path.normcase(path)
        if root_id in root_ids or path_key in root_paths:
            raise CaseContextError("EVIDENCE_INTEGRITY_FAILURE", "registered root is duplicated")
        root_ids.add(root_id)
        root_paths.add(path_key)
    case_ids: set[str] = set()
    for record in cases:
        if not isinstance(record, dict) or set(record) != {
            "case_id",
            "root_id",
            "state",
            "stamp",
            "reservation_id",
        }:
            raise CaseContextError("EVIDENCE_INTEGRITY_FAILURE", "registered case is invalid")
        case_id = record["case_id"]
        root_id = record["root_id"]
        state = record["state"]
        stamp = record["stamp"]
        reservation_id = record["reservation_id"]
        if (
            not isinstance(case_id, str)
            or _CASE_ID.fullmatch(case_id) is None
            or not isinstance(root_id, str)
            or root_id not in root_ids
            or state not in {"PREPARING", "ACTIVE"}
            or not isinstance(stamp, list)
            or (
                state == "PREPARING"
                and (
                    stamp
                    or not isinstance(reservation_id, str)
                    or _DIGEST.fullmatch(reservation_id) is None
                )
            )
            or (
                state == "ACTIVE"
                and (
                    reservation_id is not None
                    or len(stamp) != 2
                    or any(type(part) is not int or part < 0 for part in stamp)
                )
            )
        ):
            raise CaseContextError("EVIDENCE_INTEGRITY_FAILURE", "registered case is invalid")
        key = case_id.casefold()
        if key in case_ids:
            raise CaseContextError("EVIDENCE_INTEGRITY_FAILURE", "registered case is duplicated")
        case_ids.add(key)
    return {"schema_version": _REGISTRY_SCHEMA, "roots": roots, "cases": cases}


def _read_registry(path: Path) -> dict[str, object]:
    try:
        path.lstat()
    except FileNotFoundError:
        return _initial_registry()
    except OSError as error:
        raise CaseContextError("IO_OR_LOCK_FAILURE", "cannot inspect case registry") from error
    data = _read_exact_bytes(path, "case registry")
    document = _parse_json(data, "case registry")
    if not isinstance(document, dict) or set(document) != {
        "schema_version",
        "roots",
        "cases",
        "sha256",
    }:
        raise CaseContextError("EVIDENCE_INTEGRITY_FAILURE", "case registry is invalid")
    if data != _canonical_bytes(document) + b"\n":
        raise CaseContextError(
            "EVIDENCE_INTEGRITY_FAILURE", "case registry bytes are not canonical"
        )
    stored_digest = document["sha256"]
    body = {name: document[name] for name in ("schema_version", "roots", "cases")}
    if not isinstance(stored_digest, str) or stored_digest != _digest(body):
        raise CaseContextError("EVIDENCE_INTEGRITY_FAILURE", "case registry digest mismatch")
    return _validate_registry(body)


def _remove_owned_temp(path: Path, identity: tuple[int, int]) -> None:
    try:
        metadata = path.lstat()
        if _file_identity(metadata) == identity and stat.S_ISREG(metadata.st_mode):
            path.unlink()
    except FileNotFoundError:
        return


def _write_registry(root: Path, body: Mapping[str, object]) -> None:
    document = _registry_document(_validate_registry(dict(body)))
    data = _canonical_bytes(document) + b"\n"
    target = root / _REGISTRY_FILE
    temporary = root / f".{_REGISTRY_FILE}.{secrets.token_hex(16)}.tmp"
    identity: tuple[int, int] | None = None
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            identity = _file_identity(os.fstat(stream.fileno()))
        os.replace(temporary, target)
        _read_registry(target)
    except CaseContextError:
        raise
    except OSError as error:
        raise CaseContextError(
            "IO_OR_LOCK_FAILURE", "cannot atomically update case registry", retryable=True
        ) from error
    finally:
        if identity is not None:
            _remove_owned_temp(temporary, identity)


@contextmanager
def _registry_lock(root: Path) -> Iterator[Path]:
    try:
        root.parent.mkdir(parents=True, exist_ok=True)
        root.mkdir(parents=False, exist_ok=True)
        exact_root = _reject_reparse_alias(root, "case registry root")
        root_stamp = _identity_stamp(exact_root, "case registry root")
    except (OSError, WorkspaceBoundaryError) as error:
        raise CaseContextError(
            "REGISTRY_AUTHORITY_REQUIRED", "case registry root is unavailable"
        ) from error
    lock_path = _reject_reparse_alias(exact_root / _REGISTRY_LOCK_FILE, "case registry lock")
    with _REGISTRY_THREAD_LOCK:
        try:
            lock_stream: BinaryIO = lock_path.open("a+b")
        except OSError as error:
            raise CaseContextError(
                "IO_OR_LOCK_FAILURE", "cannot open case registry lock", retryable=True
            ) from error
        locked = False
        try:
            held_lock = os.fstat(lock_stream.fileno())
            path_lock = lock_path.lstat()
            if (
                not stat.S_ISREG(held_lock.st_mode)
                or not stat.S_ISREG(path_lock.st_mode)
                or int(held_lock.st_nlink) != 1
                or int(path_lock.st_nlink) != 1
                or _file_identity(held_lock) != _file_identity(path_lock)
                or int(held_lock.st_size) not in {0, 1}
            ):
                raise CaseContextError(
                    "EVIDENCE_INTEGRITY_FAILURE", "case registry lock identity is invalid"
                )
            lock_identity = _file_identity(held_lock)
            lock_stream.seek(0, os.SEEK_END)
            if lock_stream.tell() == 0:
                lock_stream.write(b"\0")
                lock_stream.flush()
                os.fsync(lock_stream.fileno())
            lock_stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(lock_stream.fileno(), msvcrt.LK_LOCK, 1)
            else:  # pragma: no cover - Windows is the product target
                import fcntl

                fcntl.flock(  # type: ignore[attr-defined]
                    lock_stream.fileno(),
                    fcntl.LOCK_EX,  # type: ignore[attr-defined]
                )
            locked = True
            if _file_identity(lock_path.lstat()) != lock_identity:
                raise CaseContextError(
                    "EVIDENCE_INTEGRITY_FAILURE", "case registry lock identity changed"
                )
            _identity_stamp(exact_root, "case registry root", root_stamp)
            yield exact_root
            _identity_stamp(exact_root, "case registry root", root_stamp)
            if _file_identity(lock_path.lstat()) != lock_identity:
                raise CaseContextError(
                    "EVIDENCE_INTEGRITY_FAILURE", "case registry lock identity changed"
                )
        except CaseContextError:
            raise
        except (OSError, WorkspaceBoundaryError) as error:
            raise CaseContextError(
                "IO_OR_LOCK_FAILURE", "case registry lock failed", retryable=True
            ) from error
        finally:
            if locked:
                try:
                    lock_stream.seek(0)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(lock_stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:  # pragma: no cover - Windows is the product target
                        import fcntl

                        fcntl.flock(  # type: ignore[attr-defined]
                            lock_stream.fileno(),
                            fcntl.LOCK_UN,  # type: ignore[attr-defined]
                        )
                except OSError:
                    pass
            lock_stream.close()


class CaseContextService:
    """Resolve durable case contexts without accepting reopen paths from callers."""

    __slots__ = ("_registry_root", "_tool_root")

    _registry_root: Path
    _tool_root: Path

    def __init__(
        self,
        *,
        _registry_root: Path | None = None,
        _tool_root: Path | None = None,
        _factory: object | None = None,
    ) -> None:
        if _factory is _TEST_FACTORY:
            if _registry_root is None or _tool_root is None:
                raise TypeError("test case context requires exact roots")
            registry_root = _normal_path(_registry_root)
            tool_root = _normal_path(_tool_root)
        elif _factory is None and _registry_root is None and _tool_root is None:
            registry_root = (
                _local_app_data_known_folder() / "FEBioCaeWorkbench" / "case-registry-v1"
            )
            tool_root = _normal_path(Path(__file__).parents[2])
        else:
            raise TypeError("case context roots are not caller configurable")
        object.__setattr__(self, "_registry_root", registry_root)
        object.__setattr__(self, "_tool_root", tool_root)

    @classmethod
    def _for_tests(cls, *, registry_root: Path, tool_root: Path) -> CaseContextService:
        return cls(
            _registry_root=registry_root,
            _tool_root=tool_root,
            _factory=_TEST_FACTORY,
        )

    @contextmanager
    def _registry(self) -> Iterator[tuple[Path, dict[str, object]]]:
        with _registry_lock(self._registry_root) as root:
            yield root, _read_registry(root / _REGISTRY_FILE)

    @staticmethod
    def _roots(registry: Mapping[str, object]) -> list[dict[str, object]]:
        records = registry["roots"]
        if not isinstance(records, list):
            raise CaseContextError("EVIDENCE_INTEGRITY_FAILURE", "registered roots are invalid")
        return [cast(dict[str, object], record) for record in records]

    @staticmethod
    def _cases(registry: Mapping[str, object]) -> list[dict[str, object]]:
        records = registry["cases"]
        if not isinstance(records, list):
            raise CaseContextError("EVIDENCE_INTEGRITY_FAILURE", "registered cases are invalid")
        return [cast(dict[str, object], record) for record in records]

    @staticmethod
    def _root_record(registry: Mapping[str, object], root_id: str) -> dict[str, object]:
        for record in CaseContextService._roots(registry):
            if record["root_id"] == root_id:
                return record
        raise CaseContextError("REGISTRY_AUTHORITY_REQUIRED", "root_id is not registered")

    @staticmethod
    def _case_record(registry: Mapping[str, object], case_id: str) -> dict[str, object]:
        matches = [
            record
            for record in CaseContextService._cases(registry)
            if str(record["case_id"]).casefold() == case_id.casefold()
        ]
        if not matches or matches[0]["state"] != "ACTIVE":
            raise CaseContextError("CASE_NOT_REGISTERED", f"case is not registered: {case_id}")
        if matches[0]["case_id"] != case_id:
            raise CaseContextError(
                "BOUNDARY_OR_IDENTITY_VIOLATION", "case identifier spelling changed"
            )
        return matches[0]

    def register_root(self, cae_root: Path) -> dict[str, object]:
        if not isinstance(cae_root, Path) or not cae_root.is_absolute():
            raise CaseContextError("INVALID_INPUT", "cae_root must be an absolute path")
        try:
            exact_root = _reject_reparse_alias(cae_root, "02_CAE root")
            if exact_root.name.casefold() != "02_cae" or not exact_root.is_dir():
                raise CaseContextError(
                    "INVALID_INPUT", "cae_root must be an existing 02_CAE directory"
                )
            stamp = _identity_stamp(exact_root, "02_CAE root")
        except CaseContextError:
            raise
        except WorkspaceBoundaryError as error:
            raise CaseContextError(
                "BOUNDARY_OR_IDENTITY_VIOLATION", "02_CAE root identity is invalid"
            ) from error
        root_id = _root_id_for(exact_root, stamp)
        with self._registry() as (registry_root, registry):
            try:
                if _exact_trees_overlap(exact_root, registry_root, "02_CAE and registry") or (
                    _exact_trees_overlap(exact_root, self._tool_root, "02_CAE and tool")
                ):
                    raise CaseContextError(
                        "BOUNDARY_OR_IDENTITY_VIOLATION",
                        "02_CAE root overlaps the tool or registry tree",
                    )
            except WorkspaceBoundaryError as error:
                raise CaseContextError(
                    "BOUNDARY_OR_IDENTITY_VIOLATION", "root tree identity is invalid"
                ) from error
            roots = self._roots(registry)
            for record in roots:
                if os.path.normcase(str(record["path"])) == os.path.normcase(os.fspath(exact_root)):
                    if record["root_id"] != root_id or record["stamp"] != [*stamp]:
                        raise CaseContextError(
                            "REGISTRATION_CONFLICT", "registered 02_CAE identity changed"
                        )
                    return {"root_id": root_id}
            roots.append({"root_id": root_id, "path": os.fspath(exact_root), "stamp": [*stamp]})
            updated = {**registry, "roots": roots}
            _write_registry(registry_root, updated)
        return {"root_id": root_id}

    def _validated_manager(
        self, registry: Mapping[str, object], root_id: str
    ) -> ValidatedCaseWorkspace:
        record = self._root_record(registry, root_id)
        root = Path(str(record["path"]))
        raw_stamp = record["stamp"]
        if not isinstance(raw_stamp, list) or len(raw_stamp) != 2:
            raise CaseContextError("EVIDENCE_INTEGRITY_FAILURE", "registered root stamp is invalid")
        expected = (int(raw_stamp[0]), int(raw_stamp[1]))
        try:
            exact_root = _reject_reparse_alias(root, "registered 02_CAE root")
            if exact_root.name.casefold() != "02_cae" or not exact_root.is_dir():
                raise WorkspaceBoundaryError("registered root is not an exact 02_CAE directory")
            stamp = _identity_stamp(exact_root, "registered 02_CAE root", expected)
            if _root_id_for(exact_root, stamp) != root_id:
                raise WorkspaceBoundaryError("registered root identifier does not match")
            if _exact_trees_overlap(
                exact_root, self._registry_root, "registered 02_CAE and registry"
            ) or _exact_trees_overlap(exact_root, self._tool_root, "registered 02_CAE and tool"):
                raise WorkspaceBoundaryError("registered root overlaps a private tree")
            return ValidatedCaseWorkspace(self._tool_root, exact_root)
        except (OSError, ValueError, WorkspaceBoundaryError) as error:
            raise CaseContextError(
                "BOUNDARY_OR_IDENTITY_VIOLATION", "registered 02_CAE root identity changed"
            ) from error

    def _release_untouched_reservation(
        self,
        *,
        root_id: str,
        case_id: str,
        reservation_id: str,
    ) -> None:
        with self._registry() as (registry_root, registry):
            manager = self._validated_manager(registry, root_id)
            case_path = manager.cae_root / case_id
            try:
                checked = _reject_reparse_alias(case_path, "failed case reservation")
                checked.lstat()
            except FileNotFoundError:
                pass
            except (OSError, WorkspaceBoundaryError):
                return
            else:
                return
            cases = self._cases(registry)
            matches = [
                record
                for record in cases
                if record["case_id"] == case_id
                and record["root_id"] == root_id
                and record["state"] == "PREPARING"
                and record["reservation_id"] == reservation_id
            ]
            if len(matches) != 1:
                raise CaseContextError(
                    "EVIDENCE_INTEGRITY_FAILURE", "case reservation changed during rollback"
                )
            cases.remove(matches[0])
            _write_registry(registry_root, {**registry, "cases": cases})

    def create_case(
        self,
        *,
        root_id: str,
        case_id: str,
        sources: Sequence[Path],
        intent: IntentContract,
    ) -> dict[str, object]:
        _validate_case_id(case_id)
        if not isinstance(root_id, str) or not root_id.startswith(_ROOT_PREFIX):
            raise CaseContextError("INVALID_INPUT", "root_id is invalid")
        if type(intent) is not IntentContract:
            raise CaseContextError("INVALID_INPUT", "intent must be a complete IntentContract")
        source_paths = tuple(sources)
        if any(not isinstance(source, Path) for source in source_paths):
            raise CaseContextError("INVALID_INPUT", "case sources must be paths")
        reservation_id = secrets.token_hex(32)
        with self._registry() as (registry_root, registry):
            manager = self._validated_manager(registry, root_id)
            if any(
                str(record["case_id"]).casefold() == case_id.casefold()
                for record in self._cases(registry)
            ):
                raise CaseContextError("REGISTRATION_CONFLICT", "case_id is already reserved")
            cases = self._cases(registry)
            cases.append(
                {
                    "case_id": case_id,
                    "root_id": root_id,
                    "state": "PREPARING",
                    "stamp": [],
                    "reservation_id": reservation_id,
                }
            )
            _write_registry(registry_root, {**registry, "cases": cases})
        try:
            case = manager.create_case(case_id, source_paths)
            store = EvidenceStore(case, intent)
            lifecycle = IntentLifecycle(store)
            result = lifecycle.reconcile()
            case_stamp = _registered_case_stamp(case)
        except FileExistsError as error:
            self._release_untouched_reservation(
                root_id=root_id, case_id=case_id, reservation_id=reservation_id
            )
            raise CaseContextError(
                "REGISTRATION_CONFLICT", "case directory already exists and cannot be adopted"
            ) from error
        except WorkspaceBoundaryError as error:
            self._release_untouched_reservation(
                root_id=root_id, case_id=case_id, reservation_id=reservation_id
            )
            raise CaseContextError(
                "BOUNDARY_OR_IDENTITY_VIOLATION", "case creation violated an identity boundary"
            ) from error
        except EvidenceIntegrityError as error:
            self._release_untouched_reservation(
                root_id=root_id, case_id=case_id, reservation_id=reservation_id
            )
            raise CaseContextError(
                "EVIDENCE_INTEGRITY_FAILURE", "case evidence creation failed"
            ) from error
        except (OSError, TypeError, ValueError) as error:
            self._release_untouched_reservation(
                root_id=root_id, case_id=case_id, reservation_id=reservation_id
            )
            raise CaseContextError("INVALID_INPUT", "case input or intent is invalid") from error
        with self._registry() as (registry_root, registry):
            cases = self._cases(registry)
            reservation = next(
                (
                    record
                    for record in cases
                    if record["case_id"] == case_id
                    and record["root_id"] == root_id
                    and record["state"] == "PREPARING"
                    and record["reservation_id"] == reservation_id
                ),
                None,
            )
            if reservation is None:
                raise CaseContextError(
                    "EVIDENCE_INTEGRITY_FAILURE", "case reservation changed during creation"
                )
            reservation["state"] = "ACTIVE"
            reservation["stamp"] = [*case_stamp]
            reservation["reservation_id"] = None
            _write_registry(registry_root, {**registry, "cases": cases})
        return self._project(root_id, case, store, lifecycle, result)

    def _open_store(self, case_id: str) -> tuple[str, CaseWorkspace, EvidenceStore]:
        _validate_case_id(case_id)
        with self._registry() as (_, registry):
            record = self._case_record(registry, case_id)
            root_id = str(record["root_id"])
            manager = self._validated_manager(registry, root_id)
            try:
                case = manager.open_case(case_id)
                raw_stamp = record["stamp"]
                if not isinstance(raw_stamp, list) or len(raw_stamp) != 2:
                    raise WorkspaceBoundaryError("registered case stamp is invalid")
                if _registered_case_stamp(case) != (int(raw_stamp[0]), int(raw_stamp[1])):
                    raise WorkspaceBoundaryError("registered case identity changed")
                store = EvidenceStore.open(case)
            except FileNotFoundError as error:
                raise CaseContextError(
                    "CASE_NOT_REGISTERED", "registered case directory is unavailable"
                ) from error
            except WorkspaceBoundaryError as error:
                raise CaseContextError(
                    "BOUNDARY_OR_IDENTITY_VIOLATION", "registered case identity changed"
                ) from error
            except EvidenceIntegrityError as error:
                raise CaseContextError(
                    "EVIDENCE_INTEGRITY_FAILURE", "registered case evidence is invalid"
                ) from error
        return root_id, case, store

    def _open_context(self, case_id: str) -> _OpenedContext:
        root_id, case, store = self._open_store(case_id)
        lifecycle = IntentLifecycle(store)
        try:
            result = lifecycle.reconcile()
        except EvidenceIntegrityError as error:
            raise CaseContextError(
                "EVIDENCE_INTEGRITY_FAILURE", "case intent evidence is invalid"
            ) from error
        return _OpenedContext(root_id, case, store, lifecycle, result)

    def open_case(self, case_id: str) -> dict[str, object]:
        opened = self._open_context(case_id)
        return self._project(
            opened.root_id,
            opened.case,
            opened.store,
            opened.lifecycle,
            opened.result,
        )

    def revise_case(
        self,
        *,
        case_id: str,
        expected_intent_sha256: str,
        intent: IntentContract,
    ) -> dict[str, object]:
        expected = _validate_sha256(expected_intent_sha256, "expected intent")
        if type(intent) is not IntentContract:
            raise CaseContextError("INVALID_INPUT", "intent must be a complete IntentContract")
        root_id, case, store = self._open_store(case_id)
        try:
            snapshot = store.issue_intent_snapshot()
            if snapshot.intent_sha256 != expected:
                raise CaseContextError("STALE_INTENT_OR_QUESTION", "expected intent is not current")
            store.revise_intent(intent, expected_snapshot=snapshot)
            lifecycle = IntentLifecycle(store)
            result = lifecycle.reconcile()
        except CaseContextError:
            raise
        except EvidenceIntegrityError as error:
            raise CaseContextError(
                "STALE_INTENT_OR_QUESTION", "intent changed or its evidence is invalid"
            ) from error
        return self._project(root_id, case, store, lifecycle, result)

    def answer_case(
        self,
        *,
        case_id: str,
        expected_intent_sha256: str,
        question_id: str,
        value: JSONInput,
        source: str,
        detail: str | None = None,
    ) -> dict[str, object]:
        expected = _validate_sha256(expected_intent_sha256, "expected intent")
        supplied_question = _validate_sha256(question_id, "question_id")
        root_id, case, store = self._open_store(case_id)
        try:
            snapshot = store.issue_intent_snapshot()
            if snapshot.intent_sha256 != expected:
                raise CaseContextError("STALE_INTENT_OR_QUESTION", "expected intent is not current")
            lifecycle = IntentLifecycle(store)
            result = lifecycle.reconcile(snapshot)
            question = result.question
            if question is None or question.question_id != supplied_question:
                raise CaseContextError(
                    "STALE_INTENT_OR_QUESTION", "question is not the exact current question"
                )
            result = lifecycle.answer(question, value, source, detail=detail)
        except CaseContextError:
            raise
        except (TypeError, ValueError) as error:
            raise CaseContextError("INVALID_INPUT", "answer is invalid") from error
        except EvidenceIntegrityError as error:
            raise CaseContextError(
                "STALE_INTENT_OR_QUESTION", "intent or question evidence changed"
            ) from error
        return self._project(root_id, case, store, lifecycle, result)

    @staticmethod
    def _project(
        root_id: str,
        case: CaseWorkspace,
        store: EvidenceStore,
        lifecycle: IntentLifecycle,
        result: IntentLifecycleResult,
    ) -> dict[str, object]:
        del lifecycle
        try:
            snapshot = result.snapshot
            case_sha256 = snapshot.case_sha256
            intent_sha256 = snapshot.intent_sha256
            intent_document = snapshot.intent.to_dict()
            manifest = store.manifest
            questions = [] if result.question is None else [result.question.to_dict()]
            inputs = [
                {
                    "case_path": record["path"],
                    "name": Path(str(record["path"])).name,
                    "sha256": record["sha256"],
                }
                for record in manifest["inputs"]
            ]
            return {
                "case_id": case.case_id,
                "root_id": root_id,
                "case_sha256": case_sha256,
                "inputs": inputs,
                "intent": {
                    "sha256": intent_sha256,
                    "document": intent_document,
                },
                "lifecycle": {
                    "state": result.state.value,
                    "event_sequence": manifest["events"]["count"],
                    "event_sha256": manifest["events"]["last_sha256"],
                },
                "pending_questions": questions,
            }
        except (EvidenceIntegrityError, WorkspaceBoundaryError) as error:
            raise CaseContextError(
                "EVIDENCE_INTEGRITY_FAILURE", "case projection evidence is invalid"
            ) from error


def cli_success(
    command: str, *, root: object | None = None, case: object | None = None
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": _CLI_SCHEMA,
        "ok": True,
        "command": command,
    }
    if root is not None:
        payload["root"] = root
    if case is not None:
        payload["case"] = _redact_case(case)
    return payload


def _redact_case(value: object) -> object:
    if not isinstance(value, dict):
        raise CaseContextError("INTERNAL_ERROR", "case projection is invalid")
    redacted = dict(value)
    intent = redacted.get("intent")
    if isinstance(intent, dict):
        redacted["intent"] = {"sha256": intent.get("sha256")}
    questions = redacted.get("pending_questions")
    if isinstance(questions, list):
        redacted["pending_questions"] = [
            {
                "question_id": question.get("question_id"),
                "condition": question.get("condition"),
                "prompt": question.get("prompt"),
            }
            for question in questions
            if isinstance(question, dict)
        ]
    return redacted


def cli_failure(command: str, error: CaseContextError) -> dict[str, object]:
    return {
        "schema_version": _CLI_SCHEMA,
        "ok": False,
        "command": command,
        "error": {
            "code": error.code,
            "message": " ".join(str(error).splitlines()),
            "retryable": error.retryable,
        },
    }
