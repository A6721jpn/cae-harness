"""Durable, capability-bound authority for one FEBio execution attempt."""

from __future__ import annotations

import atexit
import hashlib
import json
import math
import os
import stat
import threading
import weakref
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn, SupportsIndex, cast

from ..evidence import EvidenceIntegrityError, EvidenceStore, IntentSnapshotAuthority
from ..workspace import (
    AttemptWorkspace,
    CaseWorkspace,
    WorkspaceBoundaryError,
    _ExactCaseTransaction,
    _ExactOwner,
    _expected_native_directory_identity,
    _open_exact_file_descriptor,
    _registered_attempt_stamp,
    _stable_file_state,
)
from .official_fbs import (
    OfficialFbsRuntime,
    OfficialFbsRuntimeError,
    validate_official_fbs_runtime,
)
from .runtime import (
    FebioRuntimeDiagnostic,
    RuntimeProbeError,
    validate_runtime_diagnostic,
)

__all__ = [
    "ExecutionAuthority",
    "ExecutionAuthorityError",
    "ExecutionOutputsAuthority",
    "claim_execution_outputs",
    "issue_execution_authority",
    "record_execution_output_artifacts",
    "reopen_execution_authority",
    "validate_execution_authority",
    "validate_execution_outputs",
]

_SCHEMA = "febio-cae-execution"
_VERSION = 3
_RECORD_NAME = "execution.json"
_RECORD_FACTORY = object()
_OUTPUT_FACTORY = object()

type _FileIdentity = tuple[int, int, int, int, int]


class ExecutionAuthorityError(RuntimeError):
    """Raised when durable execution authority cannot be proven exactly."""


@dataclass(frozen=True, slots=True)
class _LiveContext:
    attempt: AttemptWorkspace
    intent: IntentSnapshotAuthority
    runtime: FebioRuntimeDiagnostic
    case: CaseWorkspace
    case_id: str
    intent_sha256: str
    attempt_id: str
    case_root: Path
    attempt_root: Path
    attempt_relative: Path
    official_fbs_runtime: OfficialFbsRuntime | None


@dataclass(frozen=True, slots=True)
class _OfficialFbsData:
    profile: str
    runtime_identity: str
    module_sha256: str


@dataclass(frozen=True, slots=True)
class _RecordData:
    record_sha256: str
    case_id: str
    case_root: str
    attempt_id: str
    intent_sha256: str
    runtime_path: str
    runtime_sha256: str
    runtime_size: int
    runtime_version: str
    input_relative: str
    input_sha256: str
    input_size: int
    input_identity: _FileIdentity
    log_relative: str
    xplt_relative: str
    requested_fields: tuple[str, ...]
    expected_steps: int | None
    expected_final_time: float | None
    timeout_seconds: float | None
    official_fbs: _OfficialFbsData | None


@dataclass(frozen=True, slots=True)
class _AuthorityBinding:
    token: object
    attempt: AttemptWorkspace
    intent: IntentSnapshotAuthority
    runtime: FebioRuntimeDiagnostic
    official_fbs_runtime: OfficialFbsRuntime | None
    data: _RecordData
    record_bytes: bytes
    record_identity: _FileIdentity


class ExecutionAuthority:
    """Opaque, immutable authority for one exact durable ``execution.json``."""

    __slots__ = ("_token", "__weakref__")

    def __init__(self, *, _factory: object | None = None) -> None:
        if type(self) is not ExecutionAuthority or _factory is not _RECORD_FACTORY:
            raise TypeError("execution authorities are issued by the execution authority module")
        object.__setattr__(self, "_token", object())

    def __init_subclass__(cls, **kwargs: object) -> NoReturn:
        del kwargs
        raise TypeError("execution authorities cannot be subclassed")

    def __repr__(self) -> str:
        return "ExecutionAuthority(<opaque>)"

    __str__ = __repr__

    def __setattr__(self, name: str, value: object) -> NoReturn:
        del name, value
        raise AttributeError("execution authorities are immutable")

    def __delattr__(self, name: str) -> NoReturn:
        del name
        raise AttributeError("execution authorities are immutable")

    def __copy__(self) -> NoReturn:
        raise TypeError("execution authorities cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> NoReturn:
        del memo
        raise TypeError("execution authorities cannot be copied")

    def __reduce__(self) -> NoReturn:
        raise TypeError("execution authorities cannot be pickled")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        del protocol
        raise TypeError("execution authorities cannot be pickled")

    def __getstate__(self) -> NoReturn:
        raise TypeError("execution authorities cannot be serialized")

    @property
    def record_path(self) -> Path:
        state = _validated_binding(self)
        return Path(os.fspath(state.attempt)) / _RECORD_NAME

    @property
    def record_sha256(self) -> str:
        return _validated_binding(self).data.record_sha256

    @property
    def case_id(self) -> str:
        return _validated_binding(self).data.case_id

    @property
    def attempt_id(self) -> str:
        return _validated_binding(self).data.attempt_id

    @property
    def intent_sha256(self) -> str:
        return _validated_binding(self).data.intent_sha256

    @property
    def input_path(self) -> Path:
        state = _validated_binding(self)
        return Path(os.fspath(state.attempt)) / Path(state.data.input_relative)

    @property
    def input_sha256(self) -> str:
        return _validated_binding(self).data.input_sha256

    @property
    def input_size(self) -> int:
        return _validated_binding(self).data.input_size

    @property
    def log_path(self) -> Path:
        state = _validated_binding(self)
        return Path(os.fspath(state.attempt)) / Path(state.data.log_relative)

    @property
    def xplt_path(self) -> Path:
        state = _validated_binding(self)
        return Path(os.fspath(state.attempt)) / Path(state.data.xplt_relative)

    @property
    def requested_fields(self) -> tuple[str, ...]:
        return _validated_binding(self).data.requested_fields

    @property
    def expected_steps(self) -> int | None:
        return _validated_binding(self).data.expected_steps

    @property
    def expected_final_time(self) -> float | None:
        return _validated_binding(self).data.expected_final_time

    @property
    def timeout_seconds(self) -> float | None:
        return _validated_binding(self).data.timeout_seconds

    @property
    def official_fbs_runtime_identity(self) -> str | None:
        profile = _validated_binding(self).data.official_fbs
        return None if profile is None else profile.runtime_identity


@dataclass(slots=True)
class _OutputBinding:
    token: object
    execution: ExecutionAuthority
    exact: _ExactCaseTransaction
    log_owner: _ExactOwner
    log_parts: tuple[str, ...]
    log_path: Path
    log_state: tuple[int, ...]
    log_bytes: bytes
    log_sha256: str
    xplt_owner: _ExactOwner
    xplt_parts: tuple[str, ...]
    xplt_path: Path
    xplt_state: tuple[int, ...]
    xplt_bytes: bytes
    xplt_sha256: str
    closed: bool = False
    finalizer: weakref.finalize[[_OutputBinding], ExecutionOutputsAuthority] | None = None


class ExecutionOutputsAuthority:
    """Opaque live claim over the exact derived LOG and XPLT file objects."""

    __slots__ = ("_token", "__weakref__")

    def __init__(self, *, _factory: object | None = None) -> None:
        if type(self) is not ExecutionOutputsAuthority or _factory is not _OUTPUT_FACTORY:
            raise TypeError("execution output authorities are issued by the execution module")
        object.__setattr__(self, "_token", object())

    def __init_subclass__(cls, **kwargs: object) -> NoReturn:
        del kwargs
        raise TypeError("execution output authorities cannot be subclassed")

    def __repr__(self) -> str:
        return "ExecutionOutputsAuthority(<opaque>)"

    __str__ = __repr__

    def __setattr__(self, name: str, value: object) -> NoReturn:
        del name, value
        raise AttributeError("execution output authorities are immutable")

    def __delattr__(self, name: str) -> NoReturn:
        del name
        raise AttributeError("execution output authorities are immutable")

    def __copy__(self) -> NoReturn:
        raise TypeError("execution output authorities cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> NoReturn:
        del memo
        raise TypeError("execution output authorities cannot be copied")

    def __reduce__(self) -> NoReturn:
        raise TypeError("execution output authorities cannot be pickled")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        del protocol
        raise TypeError("execution output authorities cannot be pickled")

    def __getstate__(self) -> NoReturn:
        raise TypeError("execution output authorities cannot be serialized")

    def __enter__(self) -> ExecutionOutputsAuthority:
        validate_execution_outputs(self)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        del exc_type, exc_value, traceback
        self.close()

    def close(self) -> None:
        """Release the held exact objects once; later use fails closed."""

        _close_execution_outputs(self)

    @property
    def log_path(self) -> Path:
        return _validated_output_binding(self).log_path

    @property
    def xplt_path(self) -> Path:
        return _validated_output_binding(self).xplt_path

    @property
    def log_bytes(self) -> bytes:
        return _validated_output_binding(self).log_bytes

    @property
    def xplt_bytes(self) -> bytes:
        return _validated_output_binding(self).xplt_bytes

    @property
    def log_sha256(self) -> str:
        return _validated_output_binding(self).log_sha256

    @property
    def xplt_sha256(self) -> str:
        return _validated_output_binding(self).xplt_sha256

    @property
    def log_size(self) -> int:
        return len(_validated_output_binding(self).log_bytes)

    @property
    def xplt_size(self) -> int:
        return len(_validated_output_binding(self).xplt_bytes)


_AUTHORITY_STATES: weakref.WeakKeyDictionary[ExecutionAuthority, _AuthorityBinding] = (
    weakref.WeakKeyDictionary()
)
_OUTPUT_STATES: weakref.WeakKeyDictionary[ExecutionOutputsAuthority, _OutputBinding] = (
    weakref.WeakKeyDictionary()
)
_OUTPUT_CLEANUP_LOCK = threading.RLock()
_PENDING_OUTPUT_CLEANUP: list[_ExactCaseTransaction] = []


def _canonical_path(path: Path) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(path))))


def _canonical_payload_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ExecutionAuthorityError("execution record is not canonical JSON") from error


def _record_bytes(payload: dict[str, object]) -> tuple[bytes, str]:
    payload_bytes = _canonical_payload_bytes(payload)
    record_sha256 = hashlib.sha256(payload_bytes).hexdigest()
    record = {**payload, "record_sha256": record_sha256}
    return _canonical_payload_bytes(record) + b"\n", record_sha256


def _normalise_fields(requested_fields: Sequence[str]) -> tuple[str, ...]:
    if isinstance(requested_fields, (str, bytes)):
        raise ExecutionAuthorityError("requested fields must be a sequence of field names")
    try:
        fields = tuple(requested_fields)
    except TypeError as error:
        raise ExecutionAuthorityError(
            "requested fields must be a sequence of field names"
        ) from error
    if any(not isinstance(field, str) or not field.strip() for field in fields):
        raise ExecutionAuthorityError("requested fields must contain non-empty strings")
    if len(set(fields)) != len(fields):
        raise ExecutionAuthorityError("requested fields must not contain duplicates")
    return fields


def _normalise_expectations(
    expected_steps: int | None,
    expected_final_time: float | None,
) -> tuple[int | None, float | None]:
    if expected_steps is not None and (
        isinstance(expected_steps, bool)
        or not isinstance(expected_steps, int)
        or expected_steps < 0
    ):
        raise ExecutionAuthorityError("expected steps must be a non-negative integer")
    if expected_final_time is not None and (
        isinstance(expected_final_time, bool)
        or not isinstance(expected_final_time, (int, float))
        or not math.isfinite(float(expected_final_time))
    ):
        raise ExecutionAuthorityError("expected final time must be finite")
    return expected_steps, (None if expected_final_time is None else float(expected_final_time))


def _normalise_timeout(timeout_seconds: float | None) -> float | None:
    if timeout_seconds is None:
        return None
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(float(timeout_seconds))
        or timeout_seconds <= 0
    ):
        raise ExecutionAuthorityError("timeout seconds must be positive and finite")
    return float(timeout_seconds)


def _validate_live_context(
    attempt_workspace: AttemptWorkspace,
    intent_snapshot: IntentSnapshotAuthority,
    runtime_diagnostic: FebioRuntimeDiagnostic,
    official_fbs_runtime: OfficialFbsRuntime | None = None,
) -> _LiveContext:
    if type(attempt_workspace) is not AttemptWorkspace:
        raise ExecutionAuthorityError("execution requires an exact live AttemptWorkspace authority")
    if type(intent_snapshot) is not IntentSnapshotAuthority:
        raise ExecutionAuthorityError(
            "execution requires an exact live IntentSnapshotAuthority authority"
        )
    try:
        attempt_root = Path(os.fspath(attempt_workspace))
        case_id = attempt_workspace.case_id
        attempt_id = attempt_workspace.attempt_id
        intent_case_id = intent_snapshot.case_id
        intent_sha256 = intent_snapshot.intent_sha256
        case = object.__getattribute__(intent_snapshot, "_case_workspace")
        if type(case) is not CaseWorkspace:
            raise ExecutionAuthorityError("intent snapshot case authority is invalid")
        case_root = case.root
        expected_attempt_root = case.temporary_root / "attempts" / attempt_id
    except ExecutionAuthorityError:
        raise
    except Exception as error:
        raise ExecutionAuthorityError(
            "execution requires exact live workspace authorities"
        ) from error
    if case_id != intent_case_id or attempt_root != expected_attempt_root:
        raise ExecutionAuthorityError("attempt and intent authorities must refer to one case")
    try:
        attempt_relative = attempt_root.relative_to(case_root)
    except ValueError as error:
        raise ExecutionAuthorityError("attempt authority is outside its case root") from error
    try:
        runtime = validate_runtime_diagnostic(runtime_diagnostic)
    except (RuntimeProbeError, TypeError, ValueError) as error:
        raise ExecutionAuthorityError(
            "runtime authority is not a current probe-issued identity"
        ) from error
    if official_fbs_runtime is not None:
        try:
            official_fbs_runtime = validate_official_fbs_runtime(official_fbs_runtime)
        except (OfficialFbsRuntimeError, TypeError, ValueError) as error:
            raise ExecutionAuthorityError(
                "official FBS runtime is not a current probe-issued identity"
            ) from error
    return _LiveContext(
        attempt_workspace,
        intent_snapshot,
        runtime,
        case,
        case_id,
        intent_sha256,
        attempt_id,
        case_root,
        attempt_root,
        attempt_relative,
        official_fbs_runtime,
    )


def _input_relative(input_path: str | Path, context: _LiveContext) -> Path:
    if not isinstance(input_path, (str, Path)):
        raise ExecutionAuthorityError("input path must be a string or Path")
    supplied = Path(input_path).expanduser()
    if not supplied.is_absolute():
        raise ExecutionAuthorityError("input path must be absolute")
    lexical = Path(os.path.abspath(os.fspath(supplied)))
    try:
        relative = lexical.relative_to(context.attempt_root)
    except ValueError as error:
        raise ExecutionAuthorityError("input must be inside the exact attempt root") from error
    if not relative.parts or relative == Path("."):
        raise ExecutionAuthorityError("input must be a regular file inside the attempt root")
    return relative


def _exact_attempt(
    exact: _ExactCaseTransaction,
    context: _LiveContext,
) -> None:
    owner = exact._directory(tuple(context.attempt_relative.parts))
    expected = _expected_native_directory_identity(_registered_attempt_stamp(context.attempt))
    if owner.expected != expected:
        raise ExecutionAuthorityError("attempt root exact identity changed")


def _identity(state: tuple[int, ...]) -> _FileIdentity:
    return (state[0], state[1], state[3], state[4], state[5])


def _read_exact_file(
    exact: _ExactCaseTransaction,
    relative_path: Path,
    label: str,
) -> tuple[bytes, _FileIdentity]:
    parts = exact._parts(relative_path, label)
    parent = exact._directory(parts[:-1])
    parent_path = exact.root.joinpath(*parts[:-1])
    metadata = exact._entry_state(parts[:-1], parts[-1], label)
    if not stat.S_ISREG(metadata.st_mode):
        raise ExecutionAuthorityError(f"{label} must be a regular file")
    if metadata.st_nlink != 1:
        raise ExecutionAuthorityError(f"{label} cannot be a hard link")
    initial = _stable_file_state(metadata)
    try:
        descriptor = _open_exact_file_descriptor(
            parent.handle,
            parent_path,
            parts[-1],
            flags=os.O_RDONLY,
            access=0x80000000,
            share=0x0001,
            disposition=3,
        )
    except OSError as error:
        raise ExecutionAuthorityError(f"cannot open exact {label}") from error
    owner = exact._own_file(descriptor, label)
    if owner.expected != initial:
        raise ExecutionAuthorityError(f"{label} changed while opening")
    exact._verify_file(parts, owner, label)
    try:
        os.lseek(owner.handle, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        while chunk := os.read(owner.handle, 1024 * 1024):
            chunks.append(chunk)
    except OSError as error:
        raise ExecutionAuthorityError(f"cannot read exact {label}") from error
    owner.validate()
    exact._verify_file(parts, owner, label)
    return b"".join(chunks), _identity(owner.expected)


def _new_exact_record(
    exact: _ExactCaseTransaction,
    relative_path: Path,
) -> tuple[_ExactOwner, tuple[str, ...]]:
    parts = exact._parts(relative_path, "execution record")
    parent = exact._directory(parts[:-1])
    parent_path = exact.root.joinpath(*parts[:-1])
    try:
        descriptor = _open_exact_file_descriptor(
            parent.handle,
            parent_path,
            parts[-1],
            flags=os.O_RDWR | os.O_CREAT | os.O_EXCL,
            access=0xC0010000,
            share=0x0001,
            disposition=1,
        )
    except OSError as error:
        if _is_exists_error(error):
            raise ExecutionAuthorityError(
                "execution record already exists; duplicate issue denied"
            ) from error
        raise ExecutionAuthorityError("cannot create exact execution record") from error
    owner = exact._own_file(descriptor, "execution record")
    exact._verify_file(parts, owner, "execution record")
    return owner, parts


def _is_exists_error(error: BaseException) -> bool:
    current: BaseException | None = error
    while current is not None:
        if isinstance(current, FileExistsError) or getattr(current, "winerror", None) in {80, 183}:
            return True
        current = current.__cause__
    return False


def _relative_key(path: Path) -> str:
    return os.path.normcase(os.fspath(path))


def _derived_outputs(input_relative: Path) -> tuple[Path, Path]:
    log_relative = Path(f"{input_relative.stem}.log")
    xplt_relative = Path(f"{input_relative.stem}.xplt")
    occupied = {
        _relative_key(input_relative),
        _relative_key(Path(_RECORD_NAME)),
    }
    if _relative_key(log_relative) in occupied or _relative_key(xplt_relative) in occupied:
        raise ExecutionAuthorityError("input and execution output names collide")
    if _relative_key(log_relative) == _relative_key(xplt_relative):
        raise ExecutionAuthorityError("LOG and XPLT output names collide")
    return log_relative, xplt_relative


def _ensure_fresh_outputs(
    exact: _ExactCaseTransaction,
    context: _LiveContext,
    log_relative: Path,
    xplt_relative: Path,
) -> None:
    for relative, label in ((log_relative, "LOG"), (xplt_relative, "XPLT")):
        case_relative = context.attempt_relative / relative
        try:
            exists = exact.exists(case_relative)
        except WorkspaceBoundaryError as error:
            raise ExecutionAuthorityError(
                f"{label} output path is not an exact fresh name"
            ) from error
        if exists:
            raise ExecutionAuthorityError(f"{label} output name already exists and is not fresh")


def _payload(
    context: _LiveContext,
    input_relative: Path,
    input_bytes: bytes,
    input_identity: _FileIdentity,
    log_relative: Path,
    xplt_relative: Path,
    requested_fields: tuple[str, ...],
    expected_steps: int | None,
    expected_final_time: float | None,
    timeout_seconds: float | None,
    official_fbs: _OfficialFbsData | None,
) -> dict[str, object]:
    return {
        "case": {
            "attempt_id": context.attempt_id,
            "case_id": context.case_id,
            "root": _canonical_path(context.case_root),
        },
        "expectations": {
            "expected_final_time": expected_final_time,
            "expected_steps": expected_steps,
            "timeout_seconds": timeout_seconds,
        },
        "input": {
            "identity": {
                "device": input_identity[0],
                "inode": input_identity[1],
                "mtime_ns": input_identity[4],
                "nlink": input_identity[2],
            },
            "relative_path": input_relative.as_posix(),
            "sha256": hashlib.sha256(input_bytes).hexdigest(),
            "size": input_identity[3],
        },
        "intent_sha256": context.intent_sha256,
        "official_fbs": (
            None
            if official_fbs is None
            else {
                "module_sha256": official_fbs.module_sha256,
                "profile": official_fbs.profile,
                "runtime_identity": official_fbs.runtime_identity,
            }
        ),
        "outputs": {
            "log": log_relative.as_posix(),
            "xplt": xplt_relative.as_posix(),
        },
        "requested_fields": list(requested_fields),
        "runtime": {
            "path": _canonical_path(context.runtime.path),
            "sha256": context.runtime.sha256,
            "size": context.runtime.size,
            "version": context.runtime.version,
        },
        "schema": _SCHEMA,
        "version": _VERSION,
    }


def _require_mapping(value: object, label: str, keys: set[str]) -> Mapping[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ExecutionAuthorityError(f"execution record {label} has unexpected fields")
    return cast(Mapping[str, object], value)


def _require_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ExecutionAuthorityError(f"execution record {label} is invalid")
    return value


def _require_digest(value: object, label: str) -> str:
    digest = _require_string(value, label)
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ExecutionAuthorityError(f"execution record {label} is invalid")
    return digest


def _require_nonnegative_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ExecutionAuthorityError(f"execution record {label} is invalid")
    return value


def _record_relative(value: object, label: str) -> Path:
    text = _require_string(value, label)
    if "\\" in text:
        raise ExecutionAuthorityError(f"execution record {label} is not canonical")
    path = Path(text)
    if (
        path.is_absolute()
        or path.anchor
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ExecutionAuthorityError(f"execution record {label} is outside the attempt")
    if path.as_posix() != text:
        raise ExecutionAuthorityError(f"execution record {label} is not canonical")
    return path


def _decode_record(raw: bytes) -> _RecordData:
    try:
        decoded = raw.decode("utf-8")
        value = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ExecutionAuthorityError("execution record is not valid canonical JSON") from error
    record = _require_mapping(
        value,
        "root",
        {
            "case",
            "expectations",
            "input",
            "intent_sha256",
            "official_fbs",
            "outputs",
            "record_sha256",
            "requested_fields",
            "runtime",
            "schema",
            "version",
        },
    )
    if raw != _canonical_payload_bytes(value) + b"\n":
        raise ExecutionAuthorityError("execution record is noncanonical")
    record_sha256 = _require_digest(record["record_sha256"], "record SHA-256")
    payload = dict(record)
    del payload["record_sha256"]
    if hashlib.sha256(_canonical_payload_bytes(payload)).hexdigest() != record_sha256:
        raise ExecutionAuthorityError("execution record digest does not match canonical payload")
    if record["schema"] != _SCHEMA or record["version"] != _VERSION:
        raise ExecutionAuthorityError("execution record schema or version is invalid")

    case = _require_mapping(record["case"], "case", {"attempt_id", "case_id", "root"})
    runtime = _require_mapping(
        record["runtime"],
        "runtime",
        {"path", "sha256", "size", "version"},
    )
    input_value = _require_mapping(
        record["input"],
        "input",
        {"identity", "relative_path", "sha256", "size"},
    )
    identity = _require_mapping(
        input_value["identity"],
        "input identity",
        {"device", "inode", "mtime_ns", "nlink"},
    )
    outputs = _require_mapping(record["outputs"], "outputs", {"log", "xplt"})
    expectations = _require_mapping(
        record["expectations"],
        "expectations",
        {"expected_final_time", "expected_steps", "timeout_seconds"},
    )
    official_fbs_value = record["official_fbs"]
    if official_fbs_value is None:
        official_fbs = None
    else:
        official_fbs_record = _require_mapping(
            official_fbs_value,
            "official FBS",
            {"module_sha256", "profile", "runtime_identity"},
        )
        official_fbs = _OfficialFbsData(
            _require_string(official_fbs_record["profile"], "official FBS profile"),
            _require_string(
                official_fbs_record["runtime_identity"],
                "official FBS runtime identity",
            ),
            _require_digest(
                official_fbs_record["module_sha256"],
                "official FBS module SHA-256",
            ),
        )

    fields_value = record["requested_fields"]
    if not isinstance(fields_value, list):
        raise ExecutionAuthorityError("execution record requested fields are invalid")
    fields = _normalise_fields(cast(Sequence[str], fields_value))
    steps_value = expectations["expected_steps"]
    expected_steps = (
        None if steps_value is None else _require_nonnegative_int(steps_value, "expected steps")
    )
    final_time_value = expectations["expected_final_time"]
    if final_time_value is None:
        expected_final_time = None
    elif isinstance(final_time_value, bool) or not isinstance(final_time_value, (int, float)):
        raise ExecutionAuthorityError("execution record expected final time is invalid")
    else:
        expected_final_time = float(final_time_value)
        if not math.isfinite(expected_final_time):
            raise ExecutionAuthorityError("execution record expected final time must be finite")
    timeout_value = expectations["timeout_seconds"]
    if timeout_value is None:
        timeout_seconds = None
    elif isinstance(timeout_value, bool) or not isinstance(timeout_value, (int, float)):
        raise ExecutionAuthorityError("execution record timeout seconds is invalid")
    else:
        timeout_seconds = float(timeout_value)
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ExecutionAuthorityError(
                "execution record timeout seconds must be positive and finite"
            )

    nlink = _require_nonnegative_int(identity["nlink"], "input link count")
    if nlink != 1:
        raise ExecutionAuthorityError("execution record input must be singly linked")
    return _RecordData(
        record_sha256,
        _require_string(case["case_id"], "case id"),
        _require_string(case["root"], "case root"),
        _require_string(case["attempt_id"], "attempt id"),
        _require_digest(record["intent_sha256"], "intent SHA-256"),
        _require_string(runtime["path"], "runtime path"),
        _require_digest(runtime["sha256"], "runtime SHA-256"),
        _require_nonnegative_int(runtime["size"], "runtime size"),
        _require_string(runtime["version"], "runtime version"),
        _record_relative(input_value["relative_path"], "input path").as_posix(),
        _require_digest(input_value["sha256"], "input SHA-256"),
        _require_nonnegative_int(input_value["size"], "input size"),
        (
            _require_nonnegative_int(identity["device"], "input device"),
            _require_nonnegative_int(identity["inode"], "input inode"),
            nlink,
            _require_nonnegative_int(input_value["size"], "input size"),
            _require_nonnegative_int(identity["mtime_ns"], "input mtime"),
        ),
        _record_relative(outputs["log"], "LOG path").as_posix(),
        _record_relative(outputs["xplt"], "XPLT path").as_posix(),
        fields,
        expected_steps,
        expected_final_time,
        timeout_seconds,
        official_fbs,
    )


def _validate_record_context(data: _RecordData, context: _LiveContext) -> Path:
    if (
        data.case_id != context.case_id
        or data.case_root != _canonical_path(context.case_root)
        or data.attempt_id != context.attempt_id
    ):
        raise ExecutionAuthorityError("execution record case or attempt authority binding differs")
    if data.intent_sha256 != context.intent_sha256:
        raise ExecutionAuthorityError("execution record intent authority binding differs")
    if (
        data.runtime_path != _canonical_path(context.runtime.path)
        or data.runtime_sha256 != context.runtime.sha256
        or data.runtime_size != context.runtime.size
        or data.runtime_version != context.runtime.version
    ):
        raise ExecutionAuthorityError("execution record runtime identity differs")
    runtime = context.official_fbs_runtime
    if data.official_fbs is None:
        if runtime is not None:
            raise ExecutionAuthorityError("execution record has no official FBS profile")
    elif runtime is None:
        raise ExecutionAuthorityError("execution record requires an official FBS runtime")
    elif (
        data.official_fbs.profile != runtime.profile
        or data.official_fbs.runtime_identity != runtime.runtime_identity
        or data.official_fbs.module_sha256 != runtime.fbs_module_sha256
    ):
        raise ExecutionAuthorityError("execution record official FBS identity differs")
    input_relative = _record_relative(data.input_relative, "input path")
    log_relative, xplt_relative = _derived_outputs(input_relative)
    if (
        data.log_relative != log_relative.as_posix()
        or data.xplt_relative != xplt_relative.as_posix()
    ):
        raise ExecutionAuthorityError("execution record output names differ from the bound input")
    return input_relative


def _load_existing(
    context: _LiveContext,
) -> tuple[_RecordData, bytes, _FileIdentity]:
    record_relative = context.attempt_relative / _RECORD_NAME
    try:
        with context.case._exact_transaction() as exact:
            _exact_attempt(exact, context)
            record_raw, record_identity = _read_exact_file(
                exact,
                record_relative,
                "execution record",
            )
            data = _decode_record(record_raw)
            input_relative = _validate_record_context(data, context)
            input_bytes, input_identity = _read_exact_file(
                exact,
                context.attempt_relative / input_relative,
                "execution input",
            )
            if (
                input_identity != data.input_identity
                or len(input_bytes) != data.input_size
                or hashlib.sha256(input_bytes).hexdigest() != data.input_sha256
            ):
                raise ExecutionAuthorityError("execution input digest or exact identity changed")
            exact.validate()
    except ExecutionAuthorityError:
        raise
    except (OSError, WorkspaceBoundaryError) as error:
        raise ExecutionAuthorityError("cannot reopen exact execution record authority") from error
    return data, record_raw, record_identity


def _bind_authority(
    context: _LiveContext,
    data: _RecordData,
    record_raw: bytes,
    record_identity: _FileIdentity,
) -> ExecutionAuthority:
    authority = ExecutionAuthority(_factory=_RECORD_FACTORY)
    token = object.__getattribute__(authority, "_token")
    binding = _AuthorityBinding(
        token,
        context.attempt,
        context.intent,
        context.runtime,
        context.official_fbs_runtime,
        data,
        record_raw,
        record_identity,
    )
    _AUTHORITY_STATES[authority] = binding
    return authority


def issue_execution_authority(
    attempt_workspace: AttemptWorkspace,
    intent_snapshot: IntentSnapshotAuthority,
    runtime_diagnostic: FebioRuntimeDiagnostic,
    input_path: str | Path,
    *,
    requested_fields: Sequence[str] = (),
    expected_steps: int | None = None,
    expected_final_time: float | None = None,
    timeout_seconds: float | None = None,
    official_fbs_runtime: OfficialFbsRuntime | None = None,
) -> ExecutionAuthority:
    """Create exactly one durable record and issue its sole in-process capability."""

    context = _validate_live_context(
        attempt_workspace,
        intent_snapshot,
        runtime_diagnostic,
        official_fbs_runtime,
    )
    fields = _normalise_fields(requested_fields)
    steps, final_time = _normalise_expectations(expected_steps, expected_final_time)
    timeout = _normalise_timeout(timeout_seconds)
    official_fbs = (
        None
        if context.official_fbs_runtime is None
        else _OfficialFbsData(
            context.official_fbs_runtime.profile,
            context.official_fbs_runtime.runtime_identity,
            context.official_fbs_runtime.fbs_module_sha256,
        )
    )
    input_relative = _input_relative(input_path, context)
    log_relative, xplt_relative = _derived_outputs(input_relative)
    record_relative = context.attempt_relative / _RECORD_NAME
    record_owner: _ExactOwner | None = None
    record_parts: tuple[str, ...] | None = None
    try:
        with context.case._exact_transaction() as exact:
            _exact_attempt(exact, context)
            input_bytes, input_identity = _read_exact_file(
                exact,
                context.attempt_relative / input_relative,
                "execution input",
            )
            _ensure_fresh_outputs(exact, context, log_relative, xplt_relative)
            payload = _payload(
                context,
                input_relative,
                input_bytes,
                input_identity,
                log_relative,
                xplt_relative,
                fields,
                steps,
                final_time,
                timeout,
                official_fbs,
            )
            try:
                record_raw, record_sha256 = _record_bytes(payload)
            except OSError as error:
                raise ExecutionAuthorityError(
                    "execution input changed during record construction"
                ) from error
            try:
                record_owner, record_parts = _new_exact_record(exact, record_relative)
                exact._write(record_owner, record_raw)
                exact._verify_file(record_parts, record_owner, "execution record")
                exact.validate()
            except BaseException as primary:
                if record_owner is not None and record_parts is not None:
                    exact._discard(record_parts, record_owner, primary)
                raise
            record_identity = _identity(record_owner.expected)
    except ExecutionAuthorityError:
        raise
    except (OSError, WorkspaceBoundaryError) as error:
        raise ExecutionAuthorityError(
            "execution input or rooted record authority is invalid"
        ) from error

    data = _decode_record(record_raw)
    if data.record_sha256 != record_sha256:
        raise ExecutionAuthorityError("created execution record digest changed")
    return _bind_authority(context, data, record_raw, record_identity)


def reopen_execution_authority(
    attempt_workspace: AttemptWorkspace,
    intent_snapshot: IntentSnapshotAuthority,
    runtime_diagnostic: FebioRuntimeDiagnostic,
    *,
    official_fbs_runtime: OfficialFbsRuntime | None = None,
) -> ExecutionAuthority:
    """Revalidate one durable record through current capabilities and issue fresh authority."""

    context = _validate_live_context(
        attempt_workspace,
        intent_snapshot,
        runtime_diagnostic,
        official_fbs_runtime,
    )
    data, record_raw, record_identity = _load_existing(context)
    return _bind_authority(context, data, record_raw, record_identity)


def _binding(value: object) -> _AuthorityBinding:
    if type(value) is not ExecutionAuthority:
        raise ExecutionAuthorityError("value is not an exact issued ExecutionAuthority")
    binding = _AUTHORITY_STATES.get(value)
    if binding is None:
        raise ExecutionAuthorityError("execution authority was not issued")
    try:
        token = object.__getattribute__(value, "_token")
    except AttributeError as error:
        raise ExecutionAuthorityError("execution authority state is invalid") from error
    if token is not binding.token:
        raise ExecutionAuthorityError("execution authority binding was modified")
    return binding


def _validated_binding(value: object) -> _AuthorityBinding:
    binding = _binding(value)
    context = _validate_live_context(
        binding.attempt,
        binding.intent,
        binding.runtime,
        binding.official_fbs_runtime,
    )
    if (
        context.attempt is not binding.attempt
        or context.intent is not binding.intent
        or context.runtime is not binding.runtime
    ):
        raise ExecutionAuthorityError("execution authority capability binding changed")
    data, record_raw, record_identity = _load_existing(context)
    if data != binding.data or record_raw != binding.record_bytes:
        raise ExecutionAuthorityError("execution record content changed")
    if record_identity != binding.record_identity:
        raise ExecutionAuthorityError("execution record exact identity was replaced")
    return binding


def validate_execution_authority(value: object) -> ExecutionAuthority:
    """Fail closed unless ``value`` remains bound to exact live durable evidence."""

    _validated_binding(value)
    return cast(ExecutionAuthority, value)


def _read_claimed_output(
    exact: _ExactCaseTransaction,
    parts: tuple[str, ...],
    owner: _ExactOwner,
    label: str,
) -> bytes:
    owner.validate()
    exact._verify_file(parts, owner, label)
    try:
        os.lseek(owner.handle, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        while chunk := os.read(owner.handle, 1024 * 1024):
            chunks.append(chunk)
    except OSError as error:
        raise ExecutionAuthorityError(f"cannot read exact {label} output") from error
    owner.validate()
    exact._verify_file(parts, owner, label)
    return b"".join(chunks)


def _open_claimed_output(
    exact: _ExactCaseTransaction,
    relative_path: Path,
    label: str,
) -> tuple[_ExactOwner, tuple[str, ...], bytes]:
    parts = exact._parts(relative_path, f"{label} output")
    parent = exact._directory(parts[:-1])
    parent_path = exact.root.joinpath(*parts[:-1])
    metadata = exact._entry_state(parts[:-1], parts[-1], f"{label} output")
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise ExecutionAuthorityError(f"{label} output must be a fresh singly-linked regular file")
    initial = _stable_file_state(metadata)
    try:
        descriptor = _open_exact_file_descriptor(
            parent.handle,
            parent_path,
            parts[-1],
            flags=os.O_RDONLY,
            access=0x80000000,
            share=0x0001 | 0x0002 | 0x0004,
            disposition=3,
        )
    except OSError as error:
        raise ExecutionAuthorityError(f"cannot open exact {label} output") from error
    owner = _ExactOwner(
        descriptor,
        initial,
        lambda value: _stable_file_state(os.fstat(value)),
        lambda value: os.close(value),
        f"{label} output",
    )
    exact._owners.append(owner)
    owner.validate()
    exact._verify_file(parts, owner, f"{label} output")
    exact._files[Path(*parts).as_posix()] = owner
    return owner, parts, _read_claimed_output(exact, parts, owner, label)


def _transaction_released(exact: _ExactCaseTransaction) -> bool:
    return all(owner.released for owner in exact._owners)


def _forget_output_cleanup(exact: _ExactCaseTransaction) -> None:
    with _OUTPUT_CLEANUP_LOCK:
        _PENDING_OUTPUT_CLEANUP[:] = [
            pending for pending in _PENDING_OUTPUT_CLEANUP if pending is not exact
        ]


def _retain_output_cleanup(exact: _ExactCaseTransaction) -> None:
    with _OUTPUT_CLEANUP_LOCK:
        if _transaction_released(exact):
            _forget_output_cleanup(exact)
            return
        if not any(pending is exact for pending in _PENDING_OUTPUT_CLEANUP):
            _PENDING_OUTPUT_CLEANUP.append(exact)


def _release_output_transaction(exact: _ExactCaseTransaction) -> None:
    with _OUTPUT_CLEANUP_LOCK:
        try:
            exact.close()
        except BaseException:
            if _transaction_released(exact):
                _forget_output_cleanup(exact)
            else:
                _retain_output_cleanup(exact)
            raise
        _forget_output_cleanup(exact)


def _close_failed_output_transaction(
    exact: _ExactCaseTransaction,
    primary: BaseException,
) -> None:
    try:
        _release_output_transaction(exact)
    except BaseException as cleanup:
        primary.add_note(f"execution output claim cleanup failed: {cleanup}")


def _drain_pending_output_cleanup() -> None:
    with _OUTPUT_CLEANUP_LOCK:
        pending = tuple(_PENDING_OUTPUT_CLEANUP)
        _PENDING_OUTPUT_CLEANUP.clear()
    for exact in pending:
        try:
            _release_output_transaction(exact)
        except BaseException:
            continue


atexit.register(_drain_pending_output_cleanup)


def _output_binding(
    value: object,
    *,
    allow_closed: bool = False,
) -> _OutputBinding:
    if type(value) is not ExecutionOutputsAuthority:
        raise ExecutionAuthorityError("value is not an exact issued ExecutionOutputsAuthority")
    binding = _OUTPUT_STATES.get(value)
    if binding is None:
        raise ExecutionAuthorityError("execution output authority was not issued")
    try:
        token = object.__getattribute__(value, "_token")
    except AttributeError as error:
        raise ExecutionAuthorityError("execution output authority state is invalid") from error
    if token is not binding.token:
        raise ExecutionAuthorityError("execution output authority binding was modified")
    if binding.closed and not allow_closed:
        raise ExecutionAuthorityError("execution output authority is closed and released")
    return binding


def _validated_output_binding(value: object) -> _OutputBinding:
    binding = _output_binding(value)
    try:
        _validated_binding(binding.execution)
        _validate_held_output_objects(binding)
    except ExecutionAuthorityError:
        raise
    except (OSError, WorkspaceBoundaryError) as error:
        raise ExecutionAuthorityError("execution output exact identity or state changed") from error
    return binding


def _validate_held_output_objects(binding: _OutputBinding) -> None:
    try:
        binding.exact.validate()
        if binding.log_owner.expected != binding.log_state:
            raise ExecutionAuthorityError("LOG output held state changed")
        log_bytes = _read_claimed_output(
            binding.exact,
            binding.log_parts,
            binding.log_owner,
            "LOG",
        )
        if (
            log_bytes != binding.log_bytes
            or hashlib.sha256(log_bytes).hexdigest() != binding.log_sha256
        ):
            raise ExecutionAuthorityError("LOG output bytes or digest changed")
        if binding.xplt_owner.expected != binding.xplt_state:
            raise ExecutionAuthorityError("XPLT output held state changed")
        xplt_bytes = _read_claimed_output(
            binding.exact,
            binding.xplt_parts,
            binding.xplt_owner,
            "XPLT",
        )
        if (
            xplt_bytes != binding.xplt_bytes
            or hashlib.sha256(xplt_bytes).hexdigest() != binding.xplt_sha256
        ):
            raise ExecutionAuthorityError("XPLT output bytes or digest changed")
        binding.exact.validate()
    except ExecutionAuthorityError:
        raise
    except (OSError, WorkspaceBoundaryError) as error:
        raise ExecutionAuthorityError("execution output exact identity or state changed") from error


def _evidence_identity(state: tuple[int, ...]) -> tuple[int, int, int, int]:
    return (state[0], state[1], state[4], state[5])


def _finalize_output_binding(binding: _OutputBinding) -> None:
    binding.closed = True
    try:
        _release_output_transaction(binding.exact)
    except BaseException:
        return


def _close_execution_outputs(value: object) -> None:
    binding = _output_binding(value, allow_closed=True)
    binding.closed = True
    try:
        _release_output_transaction(binding.exact)
    except BaseException as error:
        if _transaction_released(binding.exact) and binding.finalizer is not None:
            binding.finalizer.detach()
        raise ExecutionAuthorityError(
            "execution output cleanup failed; exact ownership was retained or released safely"
        ) from error
    if binding.finalizer is not None:
        binding.finalizer.detach()


def claim_execution_outputs(
    execution_authority: ExecutionAuthority,
) -> ExecutionOutputsAuthority:
    """Claim the exact derived LOG and XPLT objects bound by one execution."""

    execution_binding = _validated_binding(execution_authority)
    context = _validate_live_context(
        execution_binding.attempt,
        execution_binding.intent,
        execution_binding.runtime,
    )
    log_relative = context.attempt_relative / Path(execution_binding.data.log_relative)
    xplt_relative = context.attempt_relative / Path(execution_binding.data.xplt_relative)
    exact = context.case._exact_transaction()
    try:
        exact.__enter__()
        _exact_attempt(exact, context)
        log_owner, log_parts, log_bytes = _open_claimed_output(exact, log_relative, "LOG")
        xplt_owner, xplt_parts, xplt_bytes = _open_claimed_output(
            exact,
            xplt_relative,
            "XPLT",
        )
        exact.validate()
        if _validated_binding(execution_authority) is not execution_binding:
            raise ExecutionAuthorityError("execution authority binding changed during claim")
        if _read_claimed_output(exact, log_parts, log_owner, "LOG") != log_bytes:
            raise ExecutionAuthorityError("LOG output changed during claim")
        if _read_claimed_output(exact, xplt_parts, xplt_owner, "XPLT") != xplt_bytes:
            raise ExecutionAuthorityError("XPLT output changed during claim")
        exact.validate()
        authority = ExecutionOutputsAuthority(_factory=_OUTPUT_FACTORY)
        binding = _OutputBinding(
            object.__getattribute__(authority, "_token"),
            execution_authority,
            exact,
            log_owner,
            log_parts,
            context.attempt_root / Path(execution_binding.data.log_relative),
            tuple(log_owner.expected),
            log_bytes,
            hashlib.sha256(log_bytes).hexdigest(),
            xplt_owner,
            xplt_parts,
            context.attempt_root / Path(execution_binding.data.xplt_relative),
            tuple(xplt_owner.expected),
            xplt_bytes,
            hashlib.sha256(xplt_bytes).hexdigest(),
        )
        binding.finalizer = weakref.finalize(authority, _finalize_output_binding, binding)
        _OUTPUT_STATES[authority] = binding
        return authority
    except BaseException as primary:
        _close_failed_output_transaction(exact, primary)
        if isinstance(primary, ExecutionAuthorityError):
            raise
        if isinstance(primary, (OSError, WorkspaceBoundaryError)):
            raise ExecutionAuthorityError("cannot claim exact execution outputs") from primary
        raise


def record_execution_output_artifacts(
    outputs: ExecutionOutputsAuthority,
    store: EvidenceStore,
) -> tuple[dict[str, object], dict[str, object]]:
    """Record both held output objects and revalidate them after projection."""

    if type(store) is not EvidenceStore:
        raise ExecutionAuthorityError("execution output recording requires an exact EvidenceStore")
    binding = _validated_output_binding(outputs)
    attempt_id = _validated_binding(binding.execution).data.attempt_id
    try:
        log_record = store.record_artifact(
            binding.log_path,
            attempt_id=attempt_id,
            expected_sha256=binding.log_sha256,
            expected_identity=_evidence_identity(binding.log_state),
        )
        xplt_record = store.record_artifact(
            binding.xplt_path,
            attempt_id=attempt_id,
            expected_sha256=binding.xplt_sha256,
            expected_identity=_evidence_identity(binding.xplt_state),
        )
        _validate_held_output_objects(binding)
    except ExecutionAuthorityError:
        raise
    except (EvidenceIntegrityError, OSError, ValueError, WorkspaceBoundaryError) as error:
        raise ExecutionAuthorityError("cannot record exact execution output artifacts") from error
    return log_record, xplt_record


def validate_execution_outputs(value: object) -> ExecutionOutputsAuthority:
    """Fail closed unless both claimed output paths still name the held objects."""

    _validated_output_binding(value)
    return cast(ExecutionOutputsAuthority, value)
