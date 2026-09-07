"""Immutable execution bundle and owned-attempt records."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from .artifacts import FileEntry
from .canonical import canonical_bytes
from .compatibility import ToolIdentity
from .lifecycle import RunState, require_transition

SCHEMA_VERSION = "1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ExecutionValidationError(ValueError):
    """Raised when an immutable execution or attempt record is invalid."""


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ExecutionValidationError(
            f"{field_name} must be non-empty text without surrounding whitespace"
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ExecutionValidationError(f"{field_name} contains a control character")
    return value


def _digest(value: object, field_name: str) -> str:
    value = _text(value, field_name)
    if _SHA256.fullmatch(value) is None:
        raise ExecutionValidationError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def _positive_int(value: object, field_name: str, *, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ExecutionValidationError(f"{field_name} must be an integer >= {minimum}")
    return value


def _finite(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExecutionValidationError(f"{field_name} must be finite numeric data")
    result = float(value)
    if not math.isfinite(result):
        raise ExecutionValidationError(f"{field_name} must be finite numeric data")
    return result


@dataclass(frozen=True, slots=True)
class ExecutionSetting:
    """One named primitive execution setting; arbitrary mappings are not allowed."""

    name: str
    value: str | int | float | bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _text(self.name, "name"))
        if isinstance(self.value, float):
            object.__setattr__(self, "value", _finite(self.value, "value"))
        elif not isinstance(self.value, (str, int, bool)):
            raise ExecutionValidationError("setting value must be str, int, float, or bool")
        if isinstance(self.value, int) and not isinstance(self.value, bool) and self.value < 0:
            raise ExecutionValidationError("setting integer values must not be negative")

    def to_dict(self) -> dict[str, object]:
        return {"schema_version": SCHEMA_VERSION, "name": self.name, "value": self.value}


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    """Process provenance; it does not prove ownership or successful drain."""

    executable: str
    executable_digest: str
    argv: Sequence[str]
    cwd: str
    thread_count: int
    start_marker: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "executable", _text(self.executable, "executable"))
        object.__setattr__(
            self, "executable_digest", _digest(self.executable_digest, "executable_digest")
        )
        if isinstance(self.argv, (str, bytes, bytearray)) or not isinstance(self.argv, Sequence):
            raise ExecutionValidationError("argv must be a sequence")
        argv = tuple(_text(item, "argv[]") for item in self.argv)
        if not argv:
            raise ExecutionValidationError("argv must not be empty")
        object.__setattr__(self, "argv", argv)
        object.__setattr__(self, "cwd", _text(self.cwd, "cwd"))
        object.__setattr__(self, "thread_count", _positive_int(self.thread_count, "thread_count"))
        object.__setattr__(self, "start_marker", _text(self.start_marker, "start_marker"))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "executable": self.executable,
            "executable_digest": self.executable_digest,
            "argv": list(self.argv),
            "cwd": self.cwd,
            "thread_count": self.thread_count,
            "start_marker": self.start_marker,
        }


@dataclass(frozen=True, slots=True)
class ExecutionBundle:
    """Fixed pre-run input identity; construction does not grant run permission."""

    bundle_id: str
    case_id: str
    revision_id: str
    spec_digest: str
    mesh_digest: str
    profile_id: str
    tool: ToolIdentity
    files: Sequence[FileEntry]
    argv: Sequence[str]
    cwd: str
    thread_count: int
    settings: Sequence[ExecutionSetting]
    bundle_digest: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        for field_name in ("bundle_id", "case_id", "revision_id", "profile_id"):
            object.__setattr__(self, field_name, _text(getattr(self, field_name), field_name))
        for field_name in ("spec_digest", "mesh_digest"):
            object.__setattr__(self, field_name, _digest(getattr(self, field_name), field_name))
        if not isinstance(self.tool, ToolIdentity):
            raise ExecutionValidationError("tool must be a ToolIdentity")
        files = tuple(self.files)
        if not files or any(not isinstance(item, FileEntry) for item in files):
            raise ExecutionValidationError("files must be a non-empty sequence of FileEntry values")
        if len({item.logical_path for item in files}) != len(files):
            raise ExecutionValidationError("files must not contain duplicate logical paths")
        object.__setattr__(self, "files", tuple(sorted(files, key=lambda item: item.logical_path)))
        if isinstance(self.argv, (str, bytes, bytearray)) or not isinstance(self.argv, Sequence):
            raise ExecutionValidationError("argv must be a sequence")
        argv = tuple(_text(item, "argv[]") for item in self.argv)
        if not argv:
            raise ExecutionValidationError("argv must not be empty")
        object.__setattr__(self, "argv", argv)
        object.__setattr__(self, "cwd", _text(self.cwd, "cwd"))
        object.__setattr__(self, "thread_count", _positive_int(self.thread_count, "thread_count"))
        settings = tuple(self.settings)
        if any(not isinstance(item, ExecutionSetting) for item in settings):
            raise ExecutionValidationError("settings contains an invalid value")
        if len({item.name for item in settings}) != len(settings):
            raise ExecutionValidationError("settings must not contain duplicate names")
        object.__setattr__(self, "settings", tuple(sorted(settings, key=lambda item: item.name)))
        object.__setattr__(
            self,
            "bundle_digest",
            hashlib.sha256(canonical_bytes(self._projection(include_digest=False))).hexdigest(),
        )

    def _projection(self, *, include_digest: bool) -> dict[str, object]:
        result: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "bundle_id": self.bundle_id,
            "case_id": self.case_id,
            "revision_id": self.revision_id,
            "spec_digest": self.spec_digest,
            "mesh_digest": self.mesh_digest,
            "profile_id": self.profile_id,
            "tool": self.tool.to_dict(),
            "files": [item.to_dict() for item in self.files],
            "argv": list(self.argv),
            "cwd": self.cwd,
            "thread_count": self.thread_count,
            "settings": [item.to_dict() for item in self.settings],
        }
        if include_digest:
            result["bundle_digest"] = self.bundle_digest
        return result

    def to_dict(self) -> dict[str, object]:
        return self._projection(include_digest=True)

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    """Append-oriented run snapshot tied to one owner generation and bundle."""

    attempt_id: str
    run_id: str
    case_id: str
    revision_id: str
    owner_generation: int
    bundle_digest: str
    state: RunState
    process: ProcessIdentity | None
    settings: Sequence[ExecutionSetting]

    def __post_init__(self) -> None:
        for field_name in ("attempt_id", "run_id", "case_id", "revision_id"):
            object.__setattr__(self, field_name, _text(getattr(self, field_name), field_name))
        object.__setattr__(
            self,
            "owner_generation",
            _positive_int(self.owner_generation, "owner_generation", minimum=0),
        )
        object.__setattr__(self, "bundle_digest", _digest(self.bundle_digest, "bundle_digest"))
        if not isinstance(self.state, RunState):
            raise ExecutionValidationError("state must be a RunState")
        if self.process is not None and not isinstance(self.process, ProcessIdentity):
            raise ExecutionValidationError("process must be a ProcessIdentity or None")
        settings = tuple(self.settings)
        if any(not isinstance(item, ExecutionSetting) for item in settings):
            raise ExecutionValidationError("settings contains an invalid value")
        object.__setattr__(self, "settings", tuple(sorted(settings, key=lambda item: item.name)))

    def transition_to(self, target: RunState) -> AttemptRecord:
        require_transition(self.state, target)
        return AttemptRecord(
            attempt_id=self.attempt_id,
            run_id=self.run_id,
            case_id=self.case_id,
            revision_id=self.revision_id,
            owner_generation=self.owner_generation,
            bundle_digest=self.bundle_digest,
            state=target,
            process=self.process,
            settings=self.settings,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "attempt_id": self.attempt_id,
            "run_id": self.run_id,
            "case_id": self.case_id,
            "revision_id": self.revision_id,
            "owner_generation": self.owner_generation,
            "bundle_digest": self.bundle_digest,
            "state": self.state.value,
            "process": None if self.process is None else self.process.to_dict(),
            "settings": [item.to_dict() for item in self.settings],
        }

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


__all__ = [
    "SCHEMA_VERSION",
    "AttemptRecord",
    "ExecutionBundle",
    "ExecutionSetting",
    "ExecutionValidationError",
    "FileEntry",
    "ProcessIdentity",
]
