"""Append-only evidence and state persistence for a validated case.

The store keeps intent values opaque and records only authoritative data.  It
does not inspect models, infer physical conditions, or run a solver.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn, SupportsIndex, cast

from .contracts import IntentContract
from .workspace import (
    CaseWorkspace,
    WorkspaceBoundaryError,
    _ExactCaseTransaction,
    _lexical_path,
    _reject_reparse_alias,
)

__all__ = [
    "EvidenceIntegrityError",
    "EvidenceStore",
    "IntentSnapshotAuthority",
    "ValidatorAuthority",
    "ValidatorAuthorityManager",
    "ValidatorExecution",
    "VerificationReceipt",
]


MANIFEST_FILE = "CASE_MANIFEST.json"
INTENT_FILE = "intent.json"
EVENTS_FILE = "90_Temporary/events.jsonl"
ATTEMPT_FILE = "ATTEMPT.json"
EVENT_LOCK_FILE = "90_Temporary/events.lock"
SCHEMA_VERSION = 1
_VERIFICATION_FIELDS = frozenset(
    {
        "case_id",
        "attempt_id",
        "source",
        "sha256",
        "destination",
        "validator",
        "runtime",
        "result",
        "evidence_digest",
    }
)
_RECEIPT_PREFIX = "febio-verification-v1:"
_RECEIPT_FACTORY = object()
_INTENT_SNAPSHOT_FACTORY = object()
_DIAGNOSTIC_EVENT = "artifact_validation_diagnostic"
_PROMOTION_CONSUMED_EVENT = "artifact_promotion_consumed"
_INTENT_REVISED_EVENT = "intent_revised"
_INTENT_REVISION_FIELDS = frozenset(
    {
        "previous_intent",
        "previous_intent_sha256",
        "new_intent",
        "new_intent_sha256",
    }
)

_LOCAL_EVENT_LOCKS: dict[str, threading.RLock] = {}
_LOCAL_EVENT_LOCKS_GUARD = threading.Lock()
_ACTIVE_EXACT_TRANSACTION: ContextVar[tuple[object, _ExactCaseTransaction] | None] = ContextVar(
    "active_evidence_exact_transaction", default=None
)


class _AuthorityRegistry:
    __slots__ = ()


@dataclass(slots=True)
class _ValidatorRegistration:
    authority: ValidatorAuthority
    validator: Callable[[Path], object]
    identity: str
    runtime: str
    registry: _AuthorityRegistry
    capability: object


@dataclass(slots=True)
class _ManagerState:
    owner: ValidatorAuthorityManager
    registry: _AuthorityRegistry
    construction_token: object
    registrations: dict[str, _ValidatorRegistration]


_PENDING_MANAGER_CONSTRUCTIONS: dict[int, object] = {}
_MANAGER_STATES: dict[int, _ManagerState] = {}
_AUTHORITY_STATES: dict[int, tuple[object, _AuthorityRegistry, object]] = {}
_PENDING_STORE_CONSTRUCTIONS: dict[int, object] = {}
_STORE_BINDINGS: dict[int, tuple[object, ValidatorAuthorityManager, _AuthorityRegistry, bool]] = {}
_INTENT_SNAPSHOT_STATES: dict[
    int,
    tuple[
        object,
        EvidenceStore,
        tuple[object, ValidatorAuthorityManager, _AuthorityRegistry, bool],
        CaseWorkspace,
        str,
        str,
        str,
        IntentContract,
    ],
] = {}
_RECEIPT_STATES: dict[int, _ReceiptBinding] = {}


class EvidenceIntegrityError(RuntimeError):
    """Raised when persisted evidence is missing, changed, or inconsistent."""


@dataclass(frozen=True, slots=True)
class ValidatorExecution:
    validator: str
    runtime: str
    result: object


class ValidatorAuthority:
    """Opaque capability issued for one registered validator."""

    __slots__ = ("_registry", "_capability")

    def __new__(cls, *args: object, **kwargs: object) -> ValidatorAuthority:
        del args, kwargs
        raise TypeError("validator authorities are issued by ValidatorAuthorityManager")

    @classmethod
    def _create(cls, registry: _AuthorityRegistry) -> ValidatorAuthority:
        authority = object.__new__(cls)
        capability = object()
        object.__setattr__(authority, "_registry", registry)
        object.__setattr__(authority, "_capability", capability)
        _AUTHORITY_STATES[id(authority)] = (authority, registry, capability)
        return authority

    def __repr__(self) -> str:
        return "ValidatorAuthority(<opaque>)"

    __str__ = __repr__

    def __setattr__(self, name: str, value: object) -> None:
        del name, value
        raise AttributeError("validator authorities are immutable")

    def __copy__(self) -> ValidatorAuthority:
        raise TypeError("validator authorities cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> ValidatorAuthority:
        del memo
        raise TypeError("validator authorities cannot be copied")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        del protocol
        raise TypeError("validator authorities cannot be pickled")


class ValidatorAuthorityManager:
    """Own validator registrations and issue exact, store-bound authorities."""

    __slots__ = ("_registry", "_construction_token")

    def __new__(cls, *args: object, **kwargs: object) -> ValidatorAuthorityManager:
        del args, kwargs
        if cls is not ValidatorAuthorityManager:
            raise TypeError("validator authority managers cannot be subclassed")
        manager = object.__new__(cls)
        _PENDING_MANAGER_CONSTRUCTIONS[id(manager)] = manager
        return manager

    def __init__(self, *, _default: bool = False) -> None:
        del _default
        if _PENDING_MANAGER_CONSTRUCTIONS.pop(id(self), None) is not self:
            raise TypeError("invalid validator authority manager construction")
        registry = _AuthorityRegistry()
        construction_token = object()
        _MANAGER_STATES[id(self)] = _ManagerState(
            owner=self,
            registry=registry,
            construction_token=construction_token,
            registrations={},
        )
        object.__setattr__(self, "_registry", registry)
        object.__setattr__(self, "_construction_token", construction_token)

    def register_validator(
        self,
        identity: str,
        validator: Callable[[Path], object],
        *,
        runtime: str = "python",
    ) -> ValidatorAuthority:
        """Register one executable validator and issue its authority."""

        state = self._state()
        identity = self._validate_label(identity, "validator identity")
        runtime = self._validate_label(runtime, "validator runtime")
        if not callable(validator):
            raise TypeError("validator must be callable")
        if identity in state.registrations:
            raise ValueError(f"validator identity is already registered: {identity}")
        authority = ValidatorAuthority._create(state.registry)
        capability = object.__getattribute__(authority, "_capability")
        state.registrations[identity] = _ValidatorRegistration(
            authority=authority,
            validator=validator,
            identity=identity,
            runtime=runtime,
            registry=state.registry,
            capability=capability,
        )
        return authority

    def __setattr__(self, name: str, value: object) -> None:
        del name, value
        raise AttributeError("validator authority managers are immutable")

    def __copy__(self) -> ValidatorAuthorityManager:
        raise TypeError("validator authority managers cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> ValidatorAuthorityManager:
        del memo
        raise TypeError("validator authority managers cannot be copied")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        del protocol
        raise TypeError("validator authority managers cannot be pickled")

    @staticmethod
    def _validate_label(value: str, label: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} must be a non-empty string")
        if value != value.strip():
            raise ValueError(f"{label} must not have surrounding whitespace")
        return value

    def _state(self) -> _ManagerState:
        if type(self) is not ValidatorAuthorityManager:
            raise EvidenceIntegrityError("validator authority manager identity is invalid")
        state = _MANAGER_STATES.get(id(self))
        if state is None or state.owner is not self:
            raise EvidenceIntegrityError("validator authority manager identity is invalid")
        try:
            registry = object.__getattribute__(self, "_registry")
            token = object.__getattribute__(self, "_construction_token")
        except AttributeError as error:
            raise EvidenceIntegrityError("validator authority manager state is invalid") from error
        if registry is not state.registry or token is not state.construction_token:
            raise EvidenceIntegrityError("validator authority manager state is invalid")
        return state

    def _execute(
        self,
        authority: ValidatorAuthority,
        source: Path,
    ) -> ValidatorExecution:
        state = self._state()
        if type(authority) is not ValidatorAuthority:
            raise EvidenceIntegrityError("verification requires a manager-issued authority")
        try:
            authority_state = _AUTHORITY_STATES[id(authority)]
            authority_owner, authority_registry, authority_capability = authority_state
            authority_registry_value = object.__getattribute__(authority, "_registry")
            authority_capability_value = object.__getattribute__(authority, "_capability")
        except (AttributeError, KeyError) as error:
            raise EvidenceIntegrityError("verification authority is invalid") from error
        if authority_owner is not authority:
            raise EvidenceIntegrityError("verification authority is invalid")
        if (
            authority_registry is not state.registry
            or authority_registry_value is not state.registry
        ):
            raise EvidenceIntegrityError("verification authority belongs to another manager")
        if authority_capability_value is not authority_capability:
            raise EvidenceIntegrityError("verification authority state is invalid")
        registration = next(
            (
                item
                for item in state.registrations.values()
                if item.authority is authority and item.capability is authority_capability
            ),
            None,
        )
        if registration is None or registration.registry is not state.registry:
            raise EvidenceIntegrityError("verification authority is not registered")
        try:
            result = registration.validator(source)
        except Exception as error:
            raise EvidenceIntegrityError("validator execution failed") from error
        return ValidatorExecution(registration.identity, registration.runtime, result)


class VerificationReceipt(str):
    """An opaque, store-issued receipt for one passed artifact verification.

    The constructor is intentionally private.  A plain string containing the
    receipt token is not accepted by :meth:`EvidenceStore.promote_verified`;
    callers must retain the exact live object issued by its store.
    """

    def __new__(cls, value: str, *, _factory: object | None = None) -> VerificationReceipt:
        if cls is not VerificationReceipt:
            raise TypeError("verification receipts cannot be subclassed")
        if _factory is not _RECEIPT_FACTORY:
            raise TypeError("verification receipts are issued by EvidenceStore")
        if not value.startswith(_RECEIPT_PREFIX):
            raise ValueError("invalid verification receipt")
        token = value.removeprefix(_RECEIPT_PREFIX).split(":")
        if len(token) != 2:
            raise ValueError("invalid verification receipt")
        _validate_digest(token[0], "verification receipt case token")
        _validate_digest(token[1], "verification receipt")
        return str.__new__(cls, value)

    def __init_subclass__(cls, **kwargs: object) -> None:
        del kwargs
        raise TypeError("verification receipts cannot be subclassed")

    @classmethod
    def _issue(cls, case_token: str, evidence_digest: str) -> VerificationReceipt:
        return cls(
            f"{_RECEIPT_PREFIX}{case_token}:{evidence_digest}",
            _factory=_RECEIPT_FACTORY,
        )

    def __repr__(self) -> str:
        return "VerificationReceipt(<opaque>)"

    def __copy__(self) -> NoReturn:
        raise TypeError("verification receipts cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> NoReturn:
        del memo
        raise TypeError("verification receipts cannot be copied")

    def __reduce__(self) -> NoReturn:
        raise TypeError("verification receipts cannot be pickled")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        del protocol
        raise TypeError("verification receipts cannot be pickled")

    def __getstate__(self) -> NoReturn:
        raise TypeError("verification receipts cannot be serialized")


@dataclass(frozen=True, slots=True)
class _ReceiptBinding:
    receipt: VerificationReceipt
    store: EvidenceStore
    store_binding: tuple[object, ValidatorAuthorityManager, _AuthorityRegistry, bool]
    case_workspace: CaseWorkspace
    case_id: str
    attempt_id: str
    attempt_identity: tuple[int, int, int, int]
    source: str
    source_identity: tuple[int, int, int, int]
    source_sha256: str
    destination: str
    validator: str
    runtime: str
    authority: ValidatorAuthority
    authority_capability: object
    evidence_digest: str
    event_sha256: str
    store_state_sha256: str


class IntentSnapshotAuthority:
    """Opaque, store-owned authority for one immutable intent snapshot."""

    __slots__ = (
        "_store",
        "_store_binding",
        "_case_workspace",
        "_case_id",
        "_case_sha256",
        "_intent_sha256",
        "_intent",
    )

    def __new__(
        cls,
        *args: object,
        _factory: object | None = None,
        **kwargs: object,
    ) -> IntentSnapshotAuthority:
        del args, kwargs
        if cls is not IntentSnapshotAuthority:
            raise TypeError("intent snapshot authorities cannot be subclassed")
        if _factory is not _INTENT_SNAPSHOT_FACTORY:
            raise TypeError("intent snapshot authorities are issued by EvidenceStore")
        return object.__new__(cls)

    def __init__(
        self,
        *args: object,
        _factory: object | None = None,
        **kwargs: object,
    ) -> None:
        del args, kwargs
        if _factory is not _INTENT_SNAPSHOT_FACTORY:
            raise TypeError("intent snapshot authorities are issued by EvidenceStore")

    def __init_subclass__(cls, **kwargs: object) -> None:
        del kwargs
        raise TypeError("intent snapshot authorities cannot be subclassed")

    @classmethod
    def _issue(
        cls,
        store: EvidenceStore,
    ) -> IntentSnapshotAuthority:
        if cls is not IntentSnapshotAuthority:
            raise TypeError("intent snapshot authorities cannot be subclassed")
        binding = _STORE_BINDINGS.get(id(store))
        if binding is None or binding[0] is not store:
            raise EvidenceIntegrityError("intent snapshot requires a registered evidence store")
        store.reopen()
        intent = store._intent
        case_id = store.case_workspace.case_id
        case_sha256 = store._case_sha256
        intent_sha256 = _digest(intent.to_dict())
        authority = cls(_factory=_INTENT_SNAPSHOT_FACTORY)
        object.__setattr__(authority, "_store", store)
        object.__setattr__(authority, "_store_binding", binding)
        object.__setattr__(authority, "_case_workspace", store.case_workspace)
        object.__setattr__(authority, "_case_id", case_id)
        object.__setattr__(authority, "_case_sha256", case_sha256)
        object.__setattr__(authority, "_intent_sha256", intent_sha256)
        object.__setattr__(authority, "_intent", intent)
        _INTENT_SNAPSHOT_STATES[id(authority)] = (
            authority,
            store,
            binding,
            store.case_workspace,
            case_id,
            case_sha256,
            intent_sha256,
            intent,
        )
        return authority

    def __repr__(self) -> str:
        return "IntentSnapshotAuthority(<opaque>)"

    __str__ = __repr__

    def __setattr__(self, name: str, value: object) -> None:
        del name, value
        raise AttributeError("intent snapshot authorities are immutable")

    def __delattr__(self, name: str) -> None:
        del name
        raise AttributeError("intent snapshot authorities are immutable")

    def __copy__(self) -> IntentSnapshotAuthority:
        raise TypeError("intent snapshot authorities cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> IntentSnapshotAuthority:
        del memo
        raise TypeError("intent snapshot authorities cannot be copied")

    def __reduce__(self) -> NoReturn:
        raise TypeError("intent snapshot authorities cannot be pickled")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        del protocol
        raise TypeError("intent snapshot authorities cannot be pickled")

    def __getstate__(self) -> NoReturn:
        raise TypeError("intent snapshot authorities cannot be serialized")

    def _validated_intent(self) -> IntentContract:
        try:
            store = object.__getattribute__(self, "_store")
        except AttributeError as error:
            raise EvidenceIntegrityError("intent snapshot authority is invalid") from error
        if type(store) is not EvidenceStore:
            raise EvidenceIntegrityError("intent snapshot authority store is invalid")
        return store._validate_intent_snapshot(self)

    @property
    def case_id(self) -> str:
        self._validated_intent()
        return cast(str, object.__getattribute__(self, "_case_id"))

    @property
    def case_sha256(self) -> str:
        self._validated_intent()
        return cast(str, object.__getattribute__(self, "_case_sha256"))

    @property
    def intent_sha256(self) -> str:
        self._validated_intent()
        return cast(str, object.__getattribute__(self, "_intent_sha256"))

    @property
    def intent(self) -> IntentContract:
        self._validated_intent()
        return cast(IntentContract, object.__getattribute__(self, "_intent"))

    @property
    def contract(self) -> IntentContract:
        return self.intent


def _canonical_bytes(value: object) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as error:
        raise TypeError("evidence values must be JSON serializable") from error
    return encoded.encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _validate_digest(value: object, label: str) -> str:
    if not isinstance(value, str) or len(value) != hashlib.sha256().digest_size * 2:
        raise EvidenceIntegrityError(f"{label} must be a SHA-256 digest")
    if any(character not in "0123456789abcdefABCDEF" for character in value):
        raise EvidenceIntegrityError(f"{label} must be a SHA-256 digest")
    return value.casefold()


def _json_text(value: object) -> str:
    return _canonical_bytes(value).decode("utf-8") + "\n"


def _as_mapping(value: object, description: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceIntegrityError(f"{description} must be a JSON object")
    return value


def _validate_segment(value: str, label: str) -> None:
    if not isinstance(value, str) or not value or value in {".", ".."}:
        raise ValueError(f"{label} must be a non-empty path segment")
    if value != value.strip() or "\x00" in value or "/" in value or "\\" in value:
        raise ValueError(f"{label} must be a single path segment")


def _local_event_lock(key: str) -> threading.RLock:
    with _LOCAL_EVENT_LOCKS_GUARD:
        lock = _LOCAL_EVENT_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCAL_EVENT_LOCKS[key] = lock
        return lock


def _event_lock_identity(case_root: Path, case_id: str) -> str:
    """Return the stable OS-lock key for one validated case identity."""

    root_text = str(_lexical_path(case_root))
    if os.name == "nt":
        root_text = root_text.casefold()
    return _digest({"case_id": case_id, "case_root": root_text})


def _open_posix_lock_descriptor(
    root_descriptor: int,
    *,
    opener: Callable[..., int] = os.open,
) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    return opener(".", flags, dir_fd=root_descriptor)


def _acquire_posix_event_lock(root_descriptor: int) -> int:
    """Lock the validated case directory, whose inode is not events.lock."""

    descriptor = -1
    try:
        import fcntl

        descriptor = _open_posix_lock_descriptor(root_descriptor)
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise EvidenceIntegrityError("evidence case lock target is not a directory")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)  # type: ignore[attr-defined]
        except OSError:
            os.close(descriptor)
            raise
        return descriptor
    except EvidenceIntegrityError:
        if descriptor >= 0:
            with suppress(OSError):
                os.close(descriptor)
        raise
    except OSError as error:
        if descriptor >= 0:
            with suppress(OSError):
                os.close(descriptor)
        raise EvidenceIntegrityError("cannot acquire case event lock") from error


def _release_posix_event_lock(descriptor: int) -> None:
    try:
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_UN)  # type: ignore[attr-defined]
    except OSError:
        pass
    with suppress(OSError):
        os.close(descriptor)


def _acquire_windows_event_lock(identity: str) -> tuple[Any, Any]:
    """Acquire a named mutex so replacement of events.lock is irrelevant."""

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel32.ReleaseMutex.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    mutex_name = f"Global\\FEBioCaeHarnessEventLock-{identity}"
    handle = kernel32.CreateMutexW(None, False, mutex_name)
    if not handle:
        error = ctypes.get_last_error()
        raise EvidenceIntegrityError(f"cannot create case event lock ({error})")
    wait_result = kernel32.WaitForSingleObject(handle, 30_000)
    if wait_result not in {0x00000000, 0x00000080}:  # WAIT_OBJECT_0/WAIT_ABANDONED
        kernel32.CloseHandle(handle)
        if wait_result == 0x00000102:  # WAIT_TIMEOUT
            raise EvidenceIntegrityError("timed out waiting for case event lock")
        raise EvidenceIntegrityError(f"cannot acquire case event lock ({wait_result})")
    return kernel32, handle


def _release_windows_event_lock(lock: tuple[Any, Any]) -> None:
    kernel32, handle = lock
    with suppress(AttributeError, OSError):
        kernel32.ReleaseMutex(handle)
    with suppress(OSError):
        kernel32.CloseHandle(handle)


@contextmanager
def _exclusive_event_lock(
    exact: _ExactCaseTransaction,
    *,
    case_identity: str,
) -> Iterator[None]:
    """Serialize event-chain transactions with a case-scoped OS authority."""

    key = case_identity
    local_lock = _local_event_lock(key)
    local_lock.acquire()
    os_lock: Any = None
    body_error: BaseException | None = None
    try:
        try:
            if os.name == "nt":
                os_lock = _acquire_windows_event_lock(case_identity)
            else:
                os_lock = _acquire_posix_event_lock(exact.root_handle)
            exact.ensure_file(EVENT_LOCK_FILE)
        except EvidenceIntegrityError:
            raise
        except (OSError, WorkspaceBoundaryError) as error:
            raise EvidenceIntegrityError("cannot acquire event log lock") from error
        try:
            yield
        except BaseException as error:
            body_error = error
            raise
    finally:
        cleanup_error: BaseException | None = None
        if os_lock is not None:
            try:
                if os.name == "nt":
                    _release_windows_event_lock(os_lock)
                else:
                    _release_posix_event_lock(os_lock)
            except BaseException as error:
                cleanup_error = error
        local_lock.release()
        if cleanup_error is not None:
            if body_error is not None:
                body_error.add_note(f"event lock cleanup failed: {cleanup_error}")
            else:
                raise EvidenceIntegrityError("event lock cleanup failed") from cleanup_error


class EvidenceStore:
    """Persist intent, events, attempts, artifacts, and a manifest projection."""

    __slots__ = (
        "case_workspace",
        "_intent",
        "_artifacts",
        "_case_sha256",
        "_authority_manager",
        "_authority_registry",
    )

    def __new__(cls, *args: object, **kwargs: object) -> EvidenceStore:
        del args, kwargs
        if cls is not EvidenceStore:
            raise TypeError("evidence stores cannot be subclassed")
        store = object.__new__(cls)
        _PENDING_STORE_CONSTRUCTIONS[id(store)] = store
        return store

    def __init__(
        self,
        case_workspace: CaseWorkspace,
        intent: IntentContract | None = None,
        authority_manager: ValidatorAuthorityManager | None = None,
    ) -> None:
        if _PENDING_STORE_CONSTRUCTIONS.pop(id(self), None) is not self:
            raise TypeError("invalid evidence store construction")
        supplied_manager = authority_manager is not None
        if authority_manager is None:
            authority_manager = ValidatorAuthorityManager()
        if type(authority_manager) is not ValidatorAuthorityManager:
            raise TypeError("evidence store requires ValidatorAuthorityManager")
        manager_state = authority_manager._state()
        self.case_workspace = case_workspace
        object.__setattr__(self, "_authority_manager", authority_manager)
        object.__setattr__(self, "_authority_registry", manager_state.registry)
        _STORE_BINDINGS[id(self)] = (
            self,
            authority_manager,
            manager_state.registry,
            supplied_manager,
        )
        self._intent: IntentContract
        self._artifacts: dict[str, dict[str, object]] = {}
        self._case_sha256 = ""

        with self._transaction():
            if self._has_persisted_state():
                self._load_and_validate(intent)
            elif intent is None:
                raise ValueError("intent is required when creating a new evidence store")
            else:
                self._initialize(intent)

    @classmethod
    def open(
        cls,
        case_workspace: CaseWorkspace,
        authority_manager: ValidatorAuthorityManager | None = None,
    ) -> EvidenceStore:
        """Open, validate, and recover exact interrupted revision projections."""

        return cls(case_workspace, authority_manager=authority_manager)

    def __setattr__(self, name: str, value: object) -> None:
        if name == "case_workspace" and id(self) in _STORE_BINDINGS:
            raise AttributeError("evidence store case workspace binding is immutable")
        if name in {"_authority_manager", "_authority_registry"} and id(self) in _STORE_BINDINGS:
            raise AttributeError("evidence store authority binding is immutable")
        object.__setattr__(self, name, value)

    def __copy__(self) -> EvidenceStore:
        raise TypeError("evidence stores cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> EvidenceStore:
        del memo
        raise TypeError("evidence stores cannot be copied")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        del protocol
        raise TypeError("evidence stores cannot be pickled")

    @property
    def manifest_path(self) -> Path:
        return self._safe_case_path(MANIFEST_FILE)

    @property
    def intent_path(self) -> Path:
        return self._safe_case_path(INTENT_FILE)

    @property
    def events_path(self) -> Path:
        return self._safe_case_path(EVENTS_FILE)

    @property
    def intent(self) -> IntentContract:
        self.reopen()
        return self._intent

    @property
    def case_sha256(self) -> str:
        self.reopen()
        return self._case_sha256

    @property
    def manifest(self) -> dict[str, Any]:
        with self._transaction():
            self._load_and_validate(None)
            return self._read_json(self.manifest_path)

    def reopen(self) -> EvidenceStore:
        """Revalidate every link, recover exact revision projections, and return this store."""

        with self._transaction():
            self._load_and_validate(None)
        return self

    def issue_intent_snapshot(self) -> IntentSnapshotAuthority:
        """Issue a live, store-bound authority for the current intent."""

        return IntentSnapshotAuthority._issue(self)

    def _validate_intent_snapshot(
        self,
        authority: IntentSnapshotAuthority,
    ) -> IntentContract:
        """Revalidate and resolve only an authority issued by this store."""

        try:
            self.reopen()
        except EvidenceIntegrityError:
            raise
        except Exception as error:
            raise EvidenceIntegrityError("intent snapshot evidence is invalid") from error

        if type(authority) is not IntentSnapshotAuthority:
            raise EvidenceIntegrityError("intent snapshot authority is invalid")
        state = _INTENT_SNAPSHOT_STATES.get(id(authority))
        binding = _STORE_BINDINGS.get(id(self))
        if state is None or state[0] is not authority or binding is None or binding[0] is not self:
            raise EvidenceIntegrityError("intent snapshot authority is invalid")
        (
            _,
            bound_store,
            bound_binding,
            bound_case_workspace,
            bound_case_id,
            bound_case_sha256,
            bound_intent_sha256,
            bound_intent,
        ) = state
        try:
            authority_store = object.__getattribute__(authority, "_store")
            authority_binding = object.__getattribute__(authority, "_store_binding")
            authority_case_workspace = object.__getattribute__(authority, "_case_workspace")
            authority_case_id = object.__getattribute__(authority, "_case_id")
            authority_case_sha256 = object.__getattribute__(authority, "_case_sha256")
            authority_intent_sha256 = object.__getattribute__(authority, "_intent_sha256")
            authority_intent = object.__getattribute__(authority, "_intent")
        except AttributeError as error:
            raise EvidenceIntegrityError("intent snapshot authority state is invalid") from error
        if (
            bound_store is not self
            or bound_binding is not binding
            or bound_case_workspace is not self.case_workspace
            or authority_store is not self
            or authority_binding is not binding
            or authority_case_workspace is not bound_case_workspace
            or authority_case_id != bound_case_id
            or authority_case_sha256 != bound_case_sha256
            or authority_intent_sha256 != bound_intent_sha256
            or authority_intent is not bound_intent
        ):
            raise EvidenceIntegrityError("intent snapshot authority binding changed")

        current_intent = self._intent
        current_intent_sha256 = _digest(current_intent.to_dict())
        if (
            self.case_workspace.case_id != bound_case_id
            or self._case_sha256 != bound_case_sha256
            or current_intent_sha256 != bound_intent_sha256
            or current_intent.to_dict() != bound_intent.to_dict()
        ):
            raise EvidenceIntegrityError("intent snapshot is stale")
        return cast(IntentContract, bound_intent)

    def append_event(
        self,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, object]:
        """Append one chained event after validating the current chain."""

        if not isinstance(event_type, str) or not event_type.strip():
            raise ValueError("event_type must be a non-empty string")
        if event_type == _INTENT_REVISED_EVENT:
            raise EvidenceIntegrityError(
                "intent revision events must be recorded through their dedicated API"
            )
        if event_type in {"artifact_verified", _DIAGNOSTIC_EVENT, _PROMOTION_CONSUMED_EVENT}:
            raise EvidenceIntegrityError(
                "verification events must be recorded through their dedicated API"
            )
        with self._transaction():
            self._load_and_validate(None)
            return self._append_event(event_type, payload)

    def revise_intent(self, new_intent: IntentContract) -> dict[str, object]:
        """Append a complete intent revision and project its newest contract."""

        if not isinstance(new_intent, IntentContract):
            raise TypeError("new_intent must be a complete IntentContract")
        with self._transaction():
            self._load_and_validate(None)
            previous_payload = self._intent.to_dict()
            new_payload = new_intent.to_dict()
            event = self._append_event(
                _INTENT_REVISED_EVENT,
                {
                    "previous_intent": previous_payload,
                    "previous_intent_sha256": _digest(previous_payload),
                    "new_intent": new_payload,
                    "new_intent_sha256": _digest(new_payload),
                },
                refresh_manifest=False,
            )
            try:
                self._exact().replace_bytes(
                    INTENT_FILE,
                    _json_text(new_payload).encode("utf-8"),
                )
                self._intent = new_intent
                self._refresh_manifest()
            except (OSError, WorkspaceBoundaryError, EvidenceIntegrityError) as error:
                raise EvidenceIntegrityError(
                    "intent revision projection was interrupted"
                ) from error
            return event

    def record_attempt(
        self,
        attempt_id: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, object]:
        """Create a new attempt record and link it into the event chain."""

        _validate_segment(attempt_id, "attempt_id")
        with self._transaction():
            self._load_and_validate(None)
            normalised_payload = self._normalise_payload(payload)
            body: dict[str, object] = {
                "schema_version": SCHEMA_VERSION,
                "case_id": self.case_workspace.case_id,
                "attempt_id": attempt_id,
                "payload": normalised_payload,
            }
            record = dict(body)
            record["sha256"] = _digest(body)

            attempt_root = f"90_Temporary/attempts/{attempt_id}"
            self._exact().make_directory(attempt_root)
            self._exact().replace_bytes(
                f"{attempt_root}/{ATTEMPT_FILE}",
                _json_text(record).encode("utf-8"),
            )
            self._append_event(
                "attempt_recorded", {"attempt_id": attempt_id, "sha256": record["sha256"]}
            )
            return record

    def record_artifact(
        self,
        path: str | Path,
        *,
        attempt_id: str | None = None,
    ) -> dict[str, object]:
        """Record a case-owned artifact digest and reject later mutations."""

        if attempt_id is not None:
            _validate_segment(attempt_id, "attempt_id")
        with self._transaction():
            self._load_and_validate(None)
            artifact_path = self._safe_case_file(path)
            relative = artifact_path.relative_to(self.case_workspace.case_root).as_posix()
            if relative in {
                MANIFEST_FILE,
                INTENT_FILE,
                EVENTS_FILE,
                ATTEMPT_FILE,
            } or relative.endswith(f"/{ATTEMPT_FILE}"):
                raise ValueError("evidence metadata files cannot be recorded as artifacts")
            if attempt_id is not None:
                attempt_prefix = f"90_Temporary/attempts/{attempt_id}/"
                if not relative.startswith(attempt_prefix):
                    raise ValueError("attempt artifact must be inside its attempt directory")

            record: dict[str, object] = {
                "path": relative,
                "sha256": self._exact().digest(relative),
                "attempt_id": attempt_id,
            }
            existing = self._artifacts.get(relative)
            if existing is not None:
                if existing != record:
                    raise EvidenceIntegrityError(f"artifact digest changed: {relative}")
                return dict(existing)
            self._artifacts[relative] = record
            self._refresh_manifest()
            return dict(record)

    def record_verification(
        self,
        source: str | Path,
        destination: str | Path,
        *,
        attempt_id: str,
        authority: ValidatorAuthority | None = None,
    ) -> VerificationReceipt | None:
        """Persist one diagnostic artifact verification.

        The manager may execute a registered diagnostic callable against a
        live regular source.  Phase 1 has no closed canonical validator, so
        this API never issues a promotion receipt.
        """

        _validate_segment(attempt_id, "attempt_id")
        with self._transaction():
            self._load_and_validate(None)
            if not any(record.get("attempt_id") == attempt_id for record in self._read_attempts()):
                raise EvidenceIntegrityError("verification attempt is not recorded")

            source_path, source_relative = self._verification_source(source, attempt_id)
            _, destination_relative = self._verification_destination(destination)
            exact = self._exact()
            source_identity = exact.identity(source_relative)
            source_sha256 = exact.digest(source_relative)
            manager = self._bound_authority_manager()
            if authority is None:
                if _STORE_BINDINGS[id(self)][3]:
                    raise EvidenceIntegrityError("verification requires a manager-issued authority")
                execution = ValidatorExecution("unverified", "phase1", False)
            else:
                execution = manager._execute(authority, source_path)
            try:
                after_identity = exact.identity(source_relative)
                after_sha256 = exact.digest(source_relative)
            except WorkspaceBoundaryError as error:
                raise EvidenceIntegrityError(
                    "verification source mutated during validation"
                ) from error
            if source_identity != after_identity or source_sha256 != after_sha256:
                raise EvidenceIntegrityError("verification source mutated during validation")
            if type(execution.result) is not bool:
                raise EvidenceIntegrityError("validator result must be an actual bool")
            body: dict[str, object] = {
                "case_id": self.case_workspace.case_id,
                "attempt_id": attempt_id,
                "source": source_relative,
                "sha256": source_sha256,
                "destination": destination_relative,
                "validator": execution.validator,
                "runtime": execution.runtime,
                "result": execution.result,
            }
            payload = dict(body)
            payload["evidence_digest"] = _digest(body)
            payload["authority"] = "unverified"
            payload["promotable"] = False
            self._reject_duplicate_verification(payload)
            self._append_event(_DIAGNOSTIC_EVENT, payload)
            return None

    def promote_verified(self, receipt: VerificationReceipt) -> Path:
        """Promote only the exact artifact authorised by a persisted receipt."""

        with self._transaction():
            self._load_and_validate(None)
            receipt_state = self._receipt_state(receipt)
            if receipt_state.store_state_sha256 != self._case_sha256:
                raise EvidenceIntegrityError("verification receipt is stale")
            event = self._find_verification_event(receipt_state.evidence_digest)
            if event is None or event.get("sha256") != receipt_state.event_sha256:
                raise EvidenceIntegrityError("verification receipt is not current")
            verification = event.get("payload")
            if not isinstance(verification, dict):
                raise EvidenceIntegrityError("verification receipt payload is invalid")
            if verification != self._receipt_payload(receipt_state):
                raise EvidenceIntegrityError("verification receipt binding changed")
            if verification["result"] is not True:
                raise EvidenceIntegrityError("failed verification cannot be promoted")

            attempt_id = verification["attempt_id"]
            source = verification["source"]
            destination = verification["destination"]
            expected_sha256 = verification["sha256"]
            if not isinstance(attempt_id, str):
                raise EvidenceIntegrityError("verification attempt identity is invalid")
            if not isinstance(source, str):
                raise EvidenceIntegrityError("verification source is invalid")
            if not isinstance(destination, str):
                raise EvidenceIntegrityError("verification destination is invalid")
            if not isinstance(expected_sha256, str):
                raise EvidenceIntegrityError("verification record has invalid values")
            source_path, source_relative = self._verification_source(source, attempt_id)
            destination_path, destination_relative = self._verification_destination(destination)
            if source_relative != source or destination_relative != destination:
                raise EvidenceIntegrityError("verification paths are not canonical")
            if self._attempt_record_identity(attempt_id) != receipt_state.attempt_identity:
                raise EvidenceIntegrityError("verification attempt identity changed")
            if source_relative != receipt_state.source:
                raise EvidenceIntegrityError("verification source binding changed")
            if self._exact().identity(source_relative) != receipt_state.source_identity:
                raise EvidenceIntegrityError("verification source identity changed")
            if (
                self._exact().digest(source_relative) != expected_sha256
                or expected_sha256 != receipt_state.source_sha256
            ):
                raise EvidenceIntegrityError("verification source digest changed")
            if destination_relative != receipt_state.destination:
                raise EvidenceIntegrityError("verification destination binding changed")
            self._receipt_authority_registration(
                receipt_state.authority,
                receipt_state.validator,
                receipt_state.runtime,
            )
            evidence_digest = receipt_state.evidence_digest
            consumed = self._find_consumed(evidence_digest)
            if consumed:
                if not self._exact().exists(destination_relative):
                    raise EvidenceIntegrityError("verification receipt has already been consumed")
                if self._exact().digest(destination_relative) != expected_sha256:
                    raise EvidenceIntegrityError("promoted destination changed")
                raise FileExistsError(destination_path)
            if self._exact().exists(destination_relative):
                raise FileExistsError(destination_path)
            self._append_event(_PROMOTION_CONSUMED_EVENT, verification)
            return self._exact().copy_create_new(
                source_relative,
                destination_relative,
                expected_sha256,
            )

    def _bound_authority_manager(self) -> ValidatorAuthorityManager:
        binding = _STORE_BINDINGS.get(id(self))
        if binding is None or binding[0] is not self:
            raise EvidenceIntegrityError("evidence store authority binding is invalid")
        manager = object.__getattribute__(self, "_authority_manager")
        registry = object.__getattribute__(self, "_authority_registry")
        if manager is not binding[1] or registry is not binding[2]:
            raise EvidenceIntegrityError("evidence store authority binding changed")
        state = manager._state()
        if state.registry is not registry:
            raise EvidenceIntegrityError("evidence store authority registry changed")
        return manager

    @contextmanager
    def _transaction(self) -> Iterator[_ExactCaseTransaction]:
        """Hold the exact case, lock, files, and directories through return."""

        with self.case_workspace._exact_transaction() as exact:
            token = _ACTIVE_EXACT_TRANSACTION.set((self, exact))
            try:
                with self._event_lock(exact):
                    yield exact
            finally:
                _ACTIVE_EXACT_TRANSACTION.reset(token)

    def _exact(self) -> _ExactCaseTransaction:
        active = _ACTIVE_EXACT_TRANSACTION.get()
        if active is None or active[0] is not self:
            raise EvidenceIntegrityError("evidence operation lacks an exact transaction")
        exact = active[1]
        exact.validate()
        return exact

    @contextmanager
    def _event_lock(self, exact: _ExactCaseTransaction) -> Iterator[None]:
        case_identity = _event_lock_identity(exact.root, self.case_workspace.case_id)
        with _exclusive_event_lock(exact, case_identity=case_identity):
            yield

    @staticmethod
    def _validate_validator(validator: str) -> str:
        if not isinstance(validator, str) or not validator.strip():
            raise ValueError("validator must be a non-empty string")
        if validator != validator.strip():
            raise ValueError("validator must not have surrounding whitespace")
        return validator

    def _receipt_state(self, receipt: VerificationReceipt) -> _ReceiptBinding:
        if type(receipt) is not VerificationReceipt:
            raise EvidenceIntegrityError("promotion requires a store-issued verification receipt")
        state = _RECEIPT_STATES.get(id(receipt))
        binding = _STORE_BINDINGS.get(id(self))
        if (
            state is None
            or state.receipt is not receipt
            or state.store is not self
            or state.case_workspace is not self.case_workspace
            or binding is None
            or state.store_binding is not binding
        ):
            raise EvidenceIntegrityError("verification receipt is not live for this store")
        token = str.__str__(receipt)
        if not token.startswith(_RECEIPT_PREFIX):
            raise EvidenceIntegrityError("promotion receipt is invalid")
        values = token.removeprefix(_RECEIPT_PREFIX).split(":")
        if len(values) != 2:
            raise EvidenceIntegrityError("promotion receipt is invalid")
        case_token = _validate_digest(values[0], "verification receipt case token")
        if case_token != self._receipt_case_token():
            raise EvidenceIntegrityError("verification receipt belongs to another case")
        evidence_digest = _validate_digest(values[1], "verification receipt")
        if evidence_digest != state.evidence_digest:
            raise EvidenceIntegrityError("verification receipt token binding changed")
        if state.case_id != self.case_workspace.case_id:
            raise EvidenceIntegrityError("verification receipt case binding changed")
        return state

    @staticmethod
    def _receipt_payload(state: _ReceiptBinding) -> dict[str, object]:
        return {
            "case_id": state.case_id,
            "attempt_id": state.attempt_id,
            "source": state.source,
            "sha256": state.source_sha256,
            "destination": state.destination,
            "validator": state.validator,
            "runtime": state.runtime,
            "result": True,
            "evidence_digest": state.evidence_digest,
        }

    def _receipt_authority_registration(
        self,
        authority: ValidatorAuthority,
        validator: str,
        runtime: str,
    ) -> _ValidatorRegistration:
        if type(authority) is not ValidatorAuthority:
            raise EvidenceIntegrityError("verification requires a manager-issued authority")
        manager = self._bound_authority_manager()
        manager_state = manager._state()
        try:
            authority_state = _AUTHORITY_STATES[id(authority)]
            authority_owner, authority_registry, authority_capability = authority_state
            authority_registry_value = object.__getattribute__(authority, "_registry")
            authority_capability_value = object.__getattribute__(authority, "_capability")
        except (AttributeError, KeyError) as error:
            raise EvidenceIntegrityError("verification authority is invalid") from error
        if (
            authority_owner is not authority
            or authority_registry is not manager_state.registry
            or authority_registry_value is not manager_state.registry
            or authority_capability_value is not authority_capability
        ):
            raise EvidenceIntegrityError("verification authority state is invalid")
        registration = next(
            (
                item
                for item in manager_state.registrations.values()
                if item.authority is authority and item.capability is authority_capability
            ),
            None,
        )
        if (
            registration is None
            or registration.registry is not manager_state.registry
            or registration.identity != validator
            or registration.runtime != runtime
        ):
            raise EvidenceIntegrityError("verification authority registration changed")
        return registration

    def _attempt_record_identity(self, attempt_id: str) -> tuple[int, int, int, int]:
        relative = f"90_Temporary/attempts/{attempt_id}/{ATTEMPT_FILE}"
        self._safe_case_file(relative)
        return self._exact().identity(relative)

    def _receipt_digest(self, receipt: VerificationReceipt) -> str:
        return self._receipt_state(receipt).evidence_digest

    def _receipt_case_token(self) -> str:
        case_root = _reject_reparse_alias(self.case_workspace.case_root, "case root")
        return _digest(
            {
                "case_id": self.case_workspace.case_id,
                "case_root": os.path.normcase(os.fspath(case_root)),
            }
        )

    def _verification_source(
        self,
        source: str | Path,
        attempt_id: str,
    ) -> tuple[Path, str]:
        source_path = self._safe_case_file(source)
        source_relative = source_path.relative_to(self.case_workspace.case_root).as_posix()
        attempt_prefix = f"90_Temporary/attempts/{attempt_id}/"
        if not source_relative.startswith(attempt_prefix):
            raise EvidenceIntegrityError("verification source is outside its attempt")
        if source_relative.endswith(f"/{ATTEMPT_FILE}"):
            raise EvidenceIntegrityError("verification source cannot be attempt metadata")
        return source_path, source_relative

    def _verification_destination(self, destination: str | Path) -> tuple[Path, str]:
        destination_path = self._safe_case_path(destination)
        relative = destination_path.relative_to(self.case_workspace.case_root)
        if len(relative.parts) < 2 or relative.parts[0] not in {
            "02_Model",
            "03_Result",
            "04_Report",
            "05_Verification",
        }:
            raise EvidenceIntegrityError("verification destination must be permanent")
        return destination_path, relative.as_posix()

    def _find_verification(self, evidence_digest: str) -> dict[str, object] | None:
        event = self._find_verification_event(evidence_digest)
        if event is None:
            return None
        payload = event["payload"]
        return dict(payload) if isinstance(payload, dict) else None

    def _find_verification_event(self, evidence_digest: str) -> dict[str, object] | None:
        for event in self._events_with_verifications():
            payload = event["payload"]
            if isinstance(payload, dict) and payload.get("evidence_digest") == evidence_digest:
                return dict(event)
        return None

    def _events_with_verifications(self) -> list[dict[str, Any]]:
        events, _ = self._read_events()
        return [event for event in events if event.get("event_type") == "artifact_verified"]

    def _find_consumed(self, evidence_digest: str) -> bool:
        events, _ = self._read_events()
        for event in events:
            if event.get("event_type") != _PROMOTION_CONSUMED_EVENT:
                continue
            payload = event.get("payload")
            if isinstance(payload, dict) and payload.get("evidence_digest") == evidence_digest:
                return True
        return False

    def _reject_duplicate_verification(self, payload: Mapping[str, object]) -> None:
        evidence_digest = payload.get("evidence_digest")
        if not isinstance(evidence_digest, str):
            raise EvidenceIntegrityError("verification digest is invalid")
        if (
            self._find_verification(evidence_digest) is not None
            or self._find_diagnostic(evidence_digest) is not None
        ):
            raise EvidenceIntegrityError("duplicate artifact verification")

    def _find_diagnostic(self, evidence_digest: str) -> dict[str, object] | None:
        events, _ = self._read_events()
        for event in events:
            if event.get("event_type") != _DIAGNOSTIC_EVENT:
                continue
            payload = event.get("payload")
            if isinstance(payload, dict) and payload.get("evidence_digest") == evidence_digest:
                return dict(payload)
        return None

    def _has_persisted_state(self) -> bool:
        exact = self._exact()
        if any(exact.exists(path) for path in (MANIFEST_FILE, INTENT_FILE, EVENTS_FILE)):
            return True
        return bool(exact.list_directory("90_Temporary/attempts"))

    def _relative_case_path(self, path: str | Path) -> tuple[Path, str]:
        root = _lexical_path(self.case_workspace.case_root)
        candidate_path = Path(path)
        if ".." in candidate_path.parts:
            raise WorkspaceBoundaryError("evidence path cannot contain parent traversal")
        if candidate_path.is_absolute() or candidate_path.anchor:
            candidate = _lexical_path(candidate_path)
        else:
            candidate = _lexical_path(root / candidate_path)
        if candidate == root or not candidate.is_relative_to(root):
            raise WorkspaceBoundaryError("evidence path is outside the case workspace")
        return candidate, candidate.relative_to(root).as_posix()

    def _safe_case_path(self, relative_path: str | Path) -> Path:
        candidate, relative = self._relative_case_path(relative_path)
        root = _lexical_path(self.case_workspace.case_root)
        if _ACTIVE_EXACT_TRANSACTION.get() is None:
            root = _reject_reparse_alias(root, "case root")
            candidate = _reject_reparse_alias(candidate, "evidence path")
        input_root = root / "01_Input"
        if candidate == input_root or candidate.is_relative_to(input_root):
            raise WorkspaceBoundaryError("evidence path is inside immutable 01_Input")
        if relative != candidate.relative_to(root).as_posix():  # pragma: no cover - defensive
            raise WorkspaceBoundaryError("evidence path is not canonical")
        return candidate

    def _safe_case_file(self, path: str | Path) -> Path:
        candidate = self._safe_case_path(path)
        active = _ACTIVE_EXACT_TRANSACTION.get()
        if active is not None and active[0] is self:
            _, relative = self._relative_case_path(candidate)
            try:
                self._exact().read_bytes(relative)
            except WorkspaceBoundaryError as error:
                raise EvidenceIntegrityError(f"artifact is not a file: {candidate}") from error
        elif not candidate.is_file():
            raise EvidenceIntegrityError(f"artifact is not a file: {candidate}")
        return candidate

    def _read_json(self, path: Path) -> dict[str, Any]:
        try:
            _, relative = self._relative_case_path(path)
            text = self._exact().read_bytes(relative).decode("utf-8")
            value = json.loads(text)
        except (OSError, UnicodeDecodeError, WorkspaceBoundaryError, json.JSONDecodeError) as error:
            raise EvidenceIntegrityError(f"cannot read JSON evidence: {path}") from error
        try:
            if text != _json_text(value):
                raise EvidenceIntegrityError(f"JSON evidence is not canonical: {path}")
        except TypeError as error:
            raise EvidenceIntegrityError(f"JSON evidence is not canonical: {path}") from error
        return _as_mapping(value, str(path))

    def _normalise_payload(self, payload: Mapping[str, Any] | None) -> dict[str, object]:
        if payload is None:
            return {}
        try:
            value = json.loads(_canonical_bytes(dict(payload)))
        except (TypeError, ValueError) as error:
            raise TypeError("event and attempt payloads must be JSON serializable") from error
        return _as_mapping(value, "payload")

    def _initialize(self, intent: IntentContract) -> None:
        self._intent = intent
        self._artifacts = {}
        intent_payload = intent.to_dict()
        exact = self._exact()
        exact.replace_bytes(INTENT_FILE, _json_text(intent_payload).encode("utf-8"))
        exact.ensure_file(EVENTS_FILE)
        if exact.read_bytes(EVENTS_FILE):
            raise EvidenceIntegrityError("new event log is not empty")
        self._refresh_manifest()

    def _load_and_validate(self, supplied_intent: IntentContract | None) -> None:
        manifest = self._read_json(self.manifest_path)
        persisted_intent, persisted_payload, _ = self._read_intent()
        events, last_event_sha256 = self._read_events()
        revision = self._latest_intent_revision(events)
        if revision is None:
            intent = persisted_intent
            intent_payload = persisted_payload
        else:
            intent_payload = cast(dict[str, Any], revision["new_intent"])
            try:
                intent = IntentContract.from_mapping(intent_payload)
            except (TypeError, ValueError) as error:  # pragma: no cover - validated earlier
                raise EvidenceIntegrityError("event-backed intent is invalid") from error
        intent_sha256 = _digest(intent_payload)
        if supplied_intent is not None and supplied_intent.to_dict() != intent_payload:
            raise EvidenceIntegrityError("supplied intent differs from current event-backed intent")
        attempts = self._read_attempts()
        artifacts = self._read_artifacts(manifest)
        self._intent = intent
        self._artifacts = artifacts
        expected = self._project_manifest(
            intent_payload,
            intent_sha256,
            events,
            last_event_sha256,
            attempts,
        )
        if persisted_payload == intent_payload and manifest == expected:
            self._case_sha256 = str(expected["case_sha256"])
            return

        if revision is None or not events or events[-1]["event_type"] != _INTENT_REVISED_EVENT:
            raise EvidenceIntegrityError("intent or manifest projection does not match evidence")

        previous_payload = cast(dict[str, Any], revision["previous_intent"])
        previous_events = events[:-1]
        previous_event_sha256 = (
            cast(str, previous_events[-1]["sha256"]) if previous_events else None
        )
        previous_manifest = self._project_manifest(
            previous_payload,
            _digest(previous_payload),
            previous_events,
            previous_event_sha256,
            attempts,
        )
        if (
            persisted_payload not in (previous_payload, intent_payload)
            or manifest != previous_manifest
        ):
            raise EvidenceIntegrityError("intent revision projections are split-brain")

        try:
            if persisted_payload != intent_payload:
                self._exact().replace_bytes(
                    INTENT_FILE,
                    _json_text(intent_payload).encode("utf-8"),
                )
            self._exact().replace_bytes(
                MANIFEST_FILE,
                _json_text(expected).encode("utf-8"),
            )
        except (OSError, WorkspaceBoundaryError) as error:
            raise EvidenceIntegrityError(
                "intent revision projections could not be recovered"
            ) from error
        self._case_sha256 = str(expected["case_sha256"])

    def _read_intent(self) -> tuple[IntentContract, dict[str, Any], str]:
        payload = self._read_json(self.intent_path)
        try:
            intent = IntentContract.from_mapping(payload)
        except (TypeError, ValueError) as error:
            raise EvidenceIntegrityError("persisted intent is invalid") from error
        if intent.to_dict() != payload:
            raise EvidenceIntegrityError("persisted intent is not canonical")
        return intent, payload, _digest(payload)

    def _read_events(self) -> tuple[list[dict[str, Any]], str | None]:
        try:
            text = self._exact().read_bytes(EVENTS_FILE).decode("utf-8")
        except (OSError, UnicodeDecodeError, WorkspaceBoundaryError) as error:
            raise EvidenceIntegrityError("event log is missing or unreadable") from error
        if text and not text.endswith("\n"):
            raise EvidenceIntegrityError("event log is truncated")

        events: list[dict[str, Any]] = []
        previous_sha256: str | None = None
        verification_digests: set[str] = set()
        verification_records: dict[str, dict[str, object]] = {}
        consumed_digests: set[str] = set()
        revised_intent: dict[str, object] | None = None
        for expected_sequence, line in enumerate(text.splitlines(), start=1):
            try:
                event = _as_mapping(json.loads(line), "event")
            except (json.JSONDecodeError, EvidenceIntegrityError) as error:
                raise EvidenceIntegrityError("event log contains invalid JSON") from error
            if line != _canonical_bytes(event).decode("utf-8"):
                raise EvidenceIntegrityError("event record is not canonical JSON")
            expected_keys = {
                "schema_version",
                "case_id",
                "sequence",
                "event_type",
                "payload",
                "previous_sha256",
                "sha256",
            }
            if set(event) != expected_keys:
                raise EvidenceIntegrityError("event record has unexpected fields")
            if (
                event["schema_version"] != SCHEMA_VERSION
                or event["case_id"] != self.case_workspace.case_id
                or event["sequence"] != expected_sequence
                or event["previous_sha256"] != previous_sha256
                or not isinstance(event["event_type"], str)
                or not event["event_type"].strip()
                or not isinstance(event["payload"], dict)
            ):
                raise EvidenceIntegrityError("event chain is reordered or discontinuous")
            stored_sha256 = event["sha256"]
            body = dict(event)
            del body["sha256"]
            if not isinstance(stored_sha256, str) or _digest(body) != stored_sha256:
                raise EvidenceIntegrityError("event digest mismatch")
            if event["event_type"] == "artifact_verified":
                payload = self._validate_verification_payload(event["payload"])
                evidence_digest = payload["evidence_digest"]
                if not isinstance(evidence_digest, str):
                    raise EvidenceIntegrityError("artifact verification digest is invalid")
                if evidence_digest in verification_digests:
                    raise EvidenceIntegrityError("duplicate artifact verification")
                verification_digests.add(evidence_digest)
                verification_records[evidence_digest] = payload
            elif event["event_type"] == _PROMOTION_CONSUMED_EVENT:
                payload = self._validate_promotion_consumed_payload(event["payload"])
                evidence_digest = payload["evidence_digest"]
                if not isinstance(evidence_digest, str):
                    raise EvidenceIntegrityError("consumed verification digest is invalid")
                if evidence_digest in consumed_digests:
                    raise EvidenceIntegrityError("duplicate consumed verification")
                if verification_records.get(evidence_digest) != payload:
                    raise EvidenceIntegrityError("consumed verification binding mismatch")
                consumed_digests.add(evidence_digest)
            elif event["event_type"] == _INTENT_REVISED_EVENT:
                payload = self._validate_intent_revision_payload(event["payload"])
                previous_intent = cast(dict[str, object], payload["previous_intent"])
                if revised_intent is not None and previous_intent != revised_intent:
                    raise EvidenceIntegrityError("intent revision chain is discontinuous")
                revised_intent = cast(dict[str, object], payload["new_intent"])
            events.append(event)
            previous_sha256 = stored_sha256
        return events, previous_sha256

    def _latest_intent_revision(
        self,
        events: list[dict[str, Any]],
    ) -> dict[str, object] | None:
        for event in reversed(events):
            if event["event_type"] == _INTENT_REVISED_EVENT:
                return self._validate_intent_revision_payload(event["payload"])
        return None

    def _validate_intent_revision_payload(self, value: object) -> dict[str, object]:
        payload = _as_mapping(value, "intent revision")
        if set(payload) != _INTENT_REVISION_FIELDS:
            raise EvidenceIntegrityError("intent revision has unexpected fields")
        previous_payload = _as_mapping(payload["previous_intent"], "previous intent")
        new_payload = _as_mapping(payload["new_intent"], "new intent")
        try:
            previous_intent = IntentContract.from_mapping(previous_payload)
            new_intent = IntentContract.from_mapping(new_payload)
        except (TypeError, ValueError) as error:
            raise EvidenceIntegrityError("intent revision snapshot is invalid") from error
        if previous_intent.to_dict() != previous_payload or new_intent.to_dict() != new_payload:
            raise EvidenceIntegrityError("intent revision snapshot is incomplete or non-canonical")
        previous_sha256 = _validate_digest(payload["previous_intent_sha256"], "previous intent")
        new_sha256 = _validate_digest(payload["new_intent_sha256"], "new intent")
        if previous_sha256 != _digest(previous_payload) or new_sha256 != _digest(new_payload):
            raise EvidenceIntegrityError("intent revision snapshot digest mismatch")
        return {
            "previous_intent": previous_payload,
            "previous_intent_sha256": previous_sha256,
            "new_intent": new_payload,
            "new_intent_sha256": new_sha256,
        }

    def _validate_promotion_consumed_payload(self, value: object) -> dict[str, object]:
        payload = self._validate_verification_payload(value)
        if payload["result"] is not True:
            raise EvidenceIntegrityError("failed verification cannot be consumed")
        return payload

    def _validate_verification_payload(self, value: object) -> dict[str, object]:
        payload = _as_mapping(value, "artifact verification")
        if set(payload) != _VERIFICATION_FIELDS:
            raise EvidenceIntegrityError("artifact verification has unexpected fields")
        case_id = payload["case_id"]
        attempt_id = payload["attempt_id"]
        source = payload["source"]
        source_sha256 = payload["sha256"]
        destination = payload["destination"]
        validator = payload["validator"]
        runtime = payload["runtime"]
        result = payload["result"]
        evidence_digest = payload["evidence_digest"]
        if not isinstance(case_id, str) or case_id != self.case_workspace.case_id:
            raise EvidenceIntegrityError("artifact verification case identity mismatch")
        if not isinstance(attempt_id, str):
            raise EvidenceIntegrityError("artifact verification attempt identity is invalid")
        try:
            _validate_segment(attempt_id, "attempt_id")
        except ValueError as error:
            raise EvidenceIntegrityError(
                "artifact verification attempt identity is invalid"
            ) from error
        if not isinstance(source, str) or not isinstance(destination, str):
            raise EvidenceIntegrityError("artifact verification paths are invalid")
        if not isinstance(source_sha256, str) or len(source_sha256) != 64:
            raise EvidenceIntegrityError("artifact verification source digest is invalid")
        if source_sha256 != source_sha256.casefold():
            raise EvidenceIntegrityError("artifact verification source digest is not canonical")
        source_sha256 = _validate_digest(source_sha256, "artifact verification source digest")
        if not isinstance(validator, str) or not validator or validator != validator.strip():
            raise EvidenceIntegrityError("artifact verification validator is invalid")
        if not isinstance(runtime, str) or not runtime or runtime != runtime.strip():
            raise EvidenceIntegrityError("artifact verification runtime is invalid")
        if type(result) is not bool:
            raise EvidenceIntegrityError("artifact verification result is invalid")
        if not isinstance(evidence_digest, str) or evidence_digest != evidence_digest.casefold():
            raise EvidenceIntegrityError("artifact verification digest is not canonical")
        evidence_digest = _validate_digest(evidence_digest, "artifact verification digest")
        body = {
            "case_id": case_id,
            "attempt_id": attempt_id,
            "source": source,
            "sha256": source_sha256,
            "destination": destination,
            "validator": validator,
            "runtime": runtime,
            "result": result,
        }
        if _digest(body) != evidence_digest:
            raise EvidenceIntegrityError("artifact verification digest mismatch")
        try:
            self._safe_case_file(f"90_Temporary/attempts/{attempt_id}/{ATTEMPT_FILE}")
            source_path, source_relative = self._verification_source(source, attempt_id)
            _, destination_relative = self._verification_destination(destination)
        except (WorkspaceBoundaryError, EvidenceIntegrityError, ValueError) as error:
            raise EvidenceIntegrityError("artifact verification path is invalid") from error
        if source_relative != source or destination_relative != destination:
            raise EvidenceIntegrityError("artifact verification path is not canonical")
        _, exact_source = self._relative_case_path(source_path)
        if self._exact().digest(exact_source) != source_sha256:
            raise EvidenceIntegrityError("artifact verification source digest changed")
        return {
            "case_id": case_id,
            "attempt_id": attempt_id,
            "source": source,
            "sha256": source_sha256,
            "destination": destination,
            "validator": validator,
            "runtime": runtime,
            "result": result,
            "evidence_digest": evidence_digest,
        }

    def _read_attempts(self) -> list[dict[str, object]]:
        exact = self._exact()
        records: list[dict[str, object]] = []
        try:
            children = exact.list_directory("90_Temporary/attempts")
        except WorkspaceBoundaryError as error:
            raise EvidenceIntegrityError("attempts directory is unreadable") from error
        for child_name in children:
            try:
                _validate_segment(child_name, "attempt_id")
                relative_record = f"90_Temporary/attempts/{child_name}/{ATTEMPT_FILE}"
                exact.list_directory(f"90_Temporary/attempts/{child_name}")
                record_path = self._safe_case_file(relative_record)
            except (OSError, ValueError, WorkspaceBoundaryError, EvidenceIntegrityError) as error:
                raise EvidenceIntegrityError("attempt record path is invalid") from error
            record = self._read_json(record_path)
            expected_keys = {"schema_version", "case_id", "attempt_id", "payload", "sha256"}
            if set(record) != expected_keys:
                raise EvidenceIntegrityError("attempt record has unexpected fields")
            if (
                record["schema_version"] != SCHEMA_VERSION
                or record["case_id"] != self.case_workspace.case_id
                or record["attempt_id"] != child_name
            ):
                raise EvidenceIntegrityError("attempt record identity mismatch")
            stored_sha256 = record["sha256"]
            body = dict(record)
            del body["sha256"]
            if not isinstance(stored_sha256, str) or _digest(body) != stored_sha256:
                raise EvidenceIntegrityError("attempt digest mismatch")
            records.append(
                {
                    "attempt_id": child_name,
                    "path": relative_record,
                    "sha256": stored_sha256,
                }
            )
        return records

    def _read_artifacts(self, manifest: Mapping[str, Any]) -> dict[str, dict[str, object]]:
        raw_artifacts = manifest.get("artifacts")
        if not isinstance(raw_artifacts, list):
            raise EvidenceIntegrityError("manifest artifacts must be a list")
        artifacts: dict[str, dict[str, object]] = {}
        for raw_record in raw_artifacts:
            record = _as_mapping(raw_record, "artifact record")
            if set(record) != {"path", "sha256", "attempt_id"}:
                raise EvidenceIntegrityError("artifact record has unexpected fields")
            relative = record["path"]
            stored_sha256 = record["sha256"]
            attempt_id = record["attempt_id"]
            if not isinstance(relative, str) or not isinstance(stored_sha256, str):
                raise EvidenceIntegrityError("artifact record has invalid values")
            if attempt_id is not None and not isinstance(attempt_id, str):
                raise EvidenceIntegrityError("artifact attempt identity is invalid")
            try:
                artifact_path = self._safe_case_file(relative)
            except (WorkspaceBoundaryError, EvidenceIntegrityError) as error:
                raise EvidenceIntegrityError("artifact path is invalid") from error
            canonical_relative = artifact_path.relative_to(self.case_workspace.case_root).as_posix()
            if relative != canonical_relative:
                raise EvidenceIntegrityError("artifact path is not canonical")
            if relative in {
                MANIFEST_FILE,
                INTENT_FILE,
                EVENTS_FILE,
                ATTEMPT_FILE,
            } or relative.endswith(f"/{ATTEMPT_FILE}"):
                raise EvidenceIntegrityError("evidence metadata cannot be an artifact")
            if attempt_id is not None:
                try:
                    _validate_segment(attempt_id, "attempt_id")
                except ValueError as error:
                    raise EvidenceIntegrityError("artifact attempt identity is invalid") from error
                attempt_prefix = f"90_Temporary/attempts/{attempt_id}/"
                if not relative.startswith(attempt_prefix):
                    raise EvidenceIntegrityError("artifact is outside its attempt")
            if self._exact().digest(relative) != stored_sha256:
                raise EvidenceIntegrityError(f"artifact digest mismatch: {relative}")
            if relative in artifacts:
                raise EvidenceIntegrityError(f"duplicate artifact record: {relative}")
            artifacts[relative] = {
                "path": relative,
                "sha256": stored_sha256,
                "attempt_id": attempt_id,
            }
        return artifacts

    def _input_records(self) -> list[dict[str, object]]:
        exact = self._exact()
        records: list[dict[str, object]] = []
        try:
            input_names = exact.list_directory("01_Input")
        except WorkspaceBoundaryError as error:
            raise EvidenceIntegrityError(
                "original input directory is missing or unreadable"
            ) from error
        expected_names = {input_path.name for input_path in self.case_workspace.original_inputs}
        if expected_names != set(input_names):
            raise EvidenceIntegrityError("original input set changed")
        for input_path in self.case_workspace.original_inputs:
            relative = f"01_Input/{input_path.name}"
            try:
                digest = exact.digest(relative)
            except WorkspaceBoundaryError as error:
                raise EvidenceIntegrityError("original input is invalid") from error
            records.append(
                {
                    "path": relative,
                    "sha256": digest,
                }
            )
        return sorted(records, key=lambda record: str(record["path"]))

    def _artifact_records(self) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
        for relative, record in self._artifacts.items():
            try:
                path = self._safe_case_file(relative)
            except (WorkspaceBoundaryError, EvidenceIntegrityError) as error:
                raise EvidenceIntegrityError("stored artifact path is invalid") from error
            _, relative_path = self._relative_case_path(path)
            digest = self._exact().digest(relative_path)
            if digest != record.get("sha256"):
                raise EvidenceIntegrityError(f"artifact digest mismatch: {relative}")
            records.append(dict(record))
        return sorted(records, key=lambda record: str(record["path"]))

    def _project_manifest(
        self,
        intent_payload: Mapping[str, Any],
        intent_sha256: str,
        events: list[dict[str, Any]],
        last_event_sha256: str | None,
        attempts: list[dict[str, object]],
    ) -> dict[str, object]:
        inputs = self._input_records()
        artifacts = self._artifact_records()
        case_body = {
            "case_id": self.case_workspace.case_id,
            "intent_sha256": intent_sha256,
            "inputs": inputs,
            "events_last_sha256": last_event_sha256,
            "attempts": attempts,
            "artifacts": artifacts,
        }
        case_sha256 = _digest(case_body)
        return {
            "schema_version": SCHEMA_VERSION,
            "case_id": self.case_workspace.case_id,
            "case_sha256": case_sha256,
            "state": intent_payload["state"],
            "intent": {"path": INTENT_FILE, "sha256": intent_sha256},
            "inputs": inputs,
            "events": {
                "path": EVENTS_FILE,
                "count": len(events),
                "last_sha256": last_event_sha256,
            },
            "attempts": attempts,
            "artifacts": artifacts,
        }

    def _append_event(
        self,
        event_type: str,
        payload: Mapping[str, Any] | None,
        *,
        refresh_manifest: bool = True,
    ) -> dict[str, object]:
        events, previous_sha256 = self._read_events()
        body: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "case_id": self.case_workspace.case_id,
            "sequence": len(events) + 1,
            "event_type": event_type,
            "payload": self._normalise_payload(payload),
            "previous_sha256": previous_sha256,
        }
        event = dict(body)
        event["sha256"] = _digest(body)
        try:
            self._exact().append_bytes(EVENTS_FILE, _json_text(event).encode("utf-8"))
        except (OSError, WorkspaceBoundaryError) as error:
            raise EvidenceIntegrityError("event log is missing or unreadable") from error
        if refresh_manifest:
            self._refresh_manifest()
        return event

    def _refresh_manifest(self) -> None:
        intent, intent_payload, intent_sha256 = self._read_intent()
        self._intent = intent
        events, last_event_sha256 = self._read_events()
        attempts = self._read_attempts()
        manifest = self._project_manifest(
            intent_payload,
            intent_sha256,
            events,
            last_event_sha256,
            attempts,
        )
        self._case_sha256 = str(manifest["case_sha256"])
        self._exact().replace_bytes(
            MANIFEST_FILE,
            _json_text(manifest).encode("utf-8"),
        )
