"""Validated case-owned filesystem boundaries.

This module owns case directory creation and path validation only.  It does
not inspect or infer the physical meaning of any input, model, or result.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "AttemptWorkspace",
    "CaseWorkspace",
    "ImmutableInputError",
    "ValidatedCaseWorkspace",
    "WorkspaceBoundaryError",
]


_TEMPORARY_ROOT = "90_Temporary"
_PERMANENT_ROOTS = frozenset({"02_Model", "03_Result", "04_Report", "05_Verification"})
_CONTROL_FILES = frozenset({"CASE_MANIFEST.json", "intent.json"})
_EVENTS_FILE = f"{_TEMPORARY_ROOT}/events.jsonl"
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class WorkspaceBoundaryError(PermissionError):
    """Raised when an operation leaves the owned case workspace."""


class ImmutableInputError(WorkspaceBoundaryError):
    """Raised when an operation would modify a preserved original input."""


def _validate_segment(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or value in {".", ".."}:
        raise ValueError(f"{label} must be a non-empty path segment")
    if value != value.strip() or "\x00" in value or "/" in value or "\\" in value:
        raise ValueError(f"{label} must be a single path segment")
    return value


def _lexical_path(value: str | Path) -> Path:
    """Return an absolute path without resolving filesystem aliases."""

    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return Path(os.path.abspath(os.fspath(path)))


def _reject_reparse_alias(path: str | Path, label: str = "path") -> Path:
    """Reject symlinks, junctions, and all other reparse-point ancestors."""

    _reject_parent_segments(Path(path), label)
    absolute = _lexical_path(path)
    ancestors: list[Path] = []
    current = absolute
    while True:
        ancestors.append(current)
        if current == current.parent:
            break
        current = current.parent

    for ancestor in reversed(ancestors):
        try:
            exists = os.path.lexists(os.fspath(ancestor))
        except OSError as error:
            raise WorkspaceBoundaryError(f"cannot inspect {label}: {absolute}") from error
        if not exists:
            continue
        try:
            metadata = ancestor.lstat()
        except OSError as error:
            raise WorkspaceBoundaryError(f"cannot inspect {label}: {absolute}") from error
        if stat.S_ISLNK(metadata.st_mode) or bool(
            getattr(metadata, "st_file_attributes", 0) & _REPARSE_POINT
        ):
            raise WorkspaceBoundaryError(f"{label} cannot contain a reparse-point alias")
    return absolute


def _reject_parent_segments(path: Path, label: str) -> None:
    if ".." in path.parts:
        raise WorkspaceBoundaryError(f"{label} cannot contain parent traversal")


def _resolve_owned_target(
    root: Path,
    relative_path: str | Path,
    *,
    allow_absolute: bool = False,
) -> Path:
    relative = Path(relative_path)
    _reject_parent_segments(relative, "write target")
    root = _reject_reparse_alias(root, "owned root")
    if relative.is_absolute() or relative.anchor:
        if not allow_absolute:
            raise WorkspaceBoundaryError("write target must be relative to the case workspace")
        target = _lexical_path(relative)
    else:
        target = _lexical_path(root / relative)
    target = _reject_reparse_alias(target, "owned target")
    if target == root or not target.is_relative_to(root):
        raise WorkspaceBoundaryError("write target is outside the owned case workspace")
    return target


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    path = _reject_reparse_alias(path, "file")
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise WorkspaceBoundaryError(f"cannot read promotion source: {path}") from error
    return digest.hexdigest()


def _validate_sha256(value: str) -> str:
    if not isinstance(value, str) or len(value) != hashlib.sha256().digest_size * 2:
        raise ValueError("expected_sha256 must be a SHA-256 digest")
    if any(character not in "0123456789abcdefABCDEF" for character in value):
        raise ValueError("expected_sha256 must be a SHA-256 digest")
    return value.casefold()


def _atomic_replace_bytes(target: Path, data: bytes, label: str) -> Path:
    """Write bytes by replacing the owned directory entry atomically.

    A regular file may have a hard-link name outside the case tree.  Opening
    such a target with truncation would mutate that outside inode, so writes
    always land in a fresh file before replacing the target name.
    """

    parent = _reject_reparse_alias(target.parent, f"{label} parent")
    parent.mkdir(parents=True, exist_ok=True)
    _reject_reparse_alias(parent, f"{label} parent")
    _reject_reparse_alias(target, label)

    try:
        existing_mode = stat.S_IMODE(target.stat().st_mode)
    except FileNotFoundError:
        existing_mode = 0o666
    except OSError as error:
        raise WorkspaceBoundaryError(f"cannot inspect {label}: {target}") from error

    descriptor: int | None = None
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            dir=os.fspath(parent),
        )
        temporary_path = Path(temporary_name)
        _reject_reparse_alias(temporary_path, f"{label} temporary file")
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_path, existing_mode)
        os.replace(os.fspath(temporary_path), os.fspath(target))
    except WorkspaceBoundaryError:
        raise
    except OSError as error:
        raise WorkspaceBoundaryError(f"cannot write {label}: {target}") from error
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
        if temporary_path is not None:
            with suppress(OSError):
                temporary_path.unlink()
    return target


def _append_bytes(target: Path, data: bytes, label: str) -> Path:
    """Append bytes only to a file with one directory entry.

    Append-only evidence must retain physical append semantics.  Rejecting a
    multiply-linked inode before writing prevents an append through a case
    name from changing an outside hard-link name.
    """

    parent = _reject_reparse_alias(target.parent, f"{label} parent")
    parent.mkdir(parents=True, exist_ok=True)
    _reject_reparse_alias(parent, f"{label} parent")
    _reject_reparse_alias(target, label)

    descriptor: int | None = None
    try:
        descriptor = os.open(
            os.fspath(target),
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o666,
        )
        with os.fdopen(descriptor, "ab") as stream:
            descriptor = None
            metadata = os.fstat(stream.fileno())
            if metadata.st_nlink != 1:
                raise WorkspaceBoundaryError(f"{label} cannot be a hard link")
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except WorkspaceBoundaryError:
        raise
    except OSError as error:
        raise WorkspaceBoundaryError(f"cannot append {label}: {target}") from error
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
    return target


@dataclass(frozen=True, slots=True, init=False)
class AttemptWorkspace:
    """A bounded writer for one case-owned temporary attempt directory."""

    case_id: str
    attempt_id: str
    root: Path

    @classmethod
    def _from_manager(
        cls,
        case_workspace: CaseWorkspace,
        attempt_id: str,
        root: Path,
    ) -> AttemptWorkspace:
        """Construct a handle only for the manager-owned attempt path."""

        if not isinstance(case_workspace, CaseWorkspace):
            raise TypeError("attempt workspace requires a case workspace authority")
        _validate_segment(case_workspace.case_id, "case_id")
        _validate_segment(attempt_id, "attempt_id")
        _reject_reparse_alias(case_workspace.case_root, "case root")
        expected_root = _reject_reparse_alias(
            case_workspace.temporary_root / "attempts" / attempt_id,
            "attempt root",
        )
        actual_root = _reject_reparse_alias(root, "attempt root")
        if actual_root != expected_root or not actual_root.is_dir():
            raise WorkspaceBoundaryError("attempt root is not owned by the case workspace")

        instance = object.__new__(cls)
        object.__setattr__(instance, "case_id", case_workspace.case_id)
        object.__setattr__(instance, "attempt_id", attempt_id)
        object.__setattr__(instance, "root", actual_root)
        return instance

    def __fspath__(self) -> str:
        return os.fspath(self.root)

    def write_bytes(self, relative_path: str | Path, data: bytes) -> Path:
        target = _resolve_owned_target(self.root, relative_path)
        return _atomic_replace_bytes(target, data, "attempt target")

    def write_text(
        self,
        relative_path: str | Path,
        text: str,
        *,
        encoding: str = "utf-8",
    ) -> Path:
        target = _resolve_owned_target(self.root, relative_path)
        return _atomic_replace_bytes(target, text.encode(encoding), "attempt target")


@dataclass(frozen=True, slots=True, init=False)
class CaseWorkspace:
    """A manager-issued handle with temporary-only normal writes."""

    _manager: ValidatedCaseWorkspace
    case_id: str
    case_root: Path
    original_inputs: tuple[Path, ...] = ()
    source_inputs: tuple[Path, ...] = ()

    @classmethod
    def _from_manager(
        cls,
        manager: ValidatedCaseWorkspace,
        case_id: str,
        case_root: Path,
        original_inputs: Iterable[str | Path] = (),
        source_inputs: Iterable[str | Path] = (),
    ) -> CaseWorkspace:
        """Construct a handle only for a path issued by its manager.

        The public dataclass constructor is disabled.  In addition to making
        accidental construction fail, this factory checks that the supplied
        root is exactly the manager's case path and that original inputs remain
        inside ``01_Input``.
        """

        if not isinstance(manager, ValidatedCaseWorkspace):
            raise TypeError("case workspace requires a validated workspace authority")
        _validate_segment(case_id, "case_id")
        _reject_reparse_alias(manager.cae_root, "cae root")
        expected_root = _reject_reparse_alias(
            manager.cae_root / case_id,
            "case root",
        )
        actual_root = _reject_reparse_alias(case_root, "case root")
        if actual_root != expected_root or not actual_root.is_dir():
            raise WorkspaceBoundaryError("case root is not owned by the validated workspace")

        input_root = _reject_reparse_alias(actual_root / "01_Input", "input root")
        normalised_originals: list[Path] = []
        for path_value in original_inputs:
            path = _reject_reparse_alias(path_value, "original input")
            if not path.is_file() or not path.is_relative_to(input_root):
                raise WorkspaceBoundaryError("original input is outside the owned 01_Input")
            normalised_originals.append(path)

        instance = object.__new__(cls)
        object.__setattr__(instance, "_manager", manager)
        object.__setattr__(instance, "case_id", case_id)
        object.__setattr__(instance, "case_root", actual_root)
        object.__setattr__(instance, "original_inputs", tuple(normalised_originals))
        object.__setattr__(
            instance,
            "source_inputs",
            tuple(_reject_reparse_alias(path, "source input") for path in source_inputs),
        )
        return instance

    @property
    def root(self) -> Path:
        return self.case_root

    @property
    def tool_root(self) -> Path:
        return self._manager.tool_root

    @property
    def cae_root(self) -> Path:
        return self._manager.cae_root

    @property
    def input_root(self) -> Path:
        return self.case_root / "01_Input"

    @property
    def model_root(self) -> Path:
        return self.case_root / "02_Model"

    @property
    def result_root(self) -> Path:
        return self.case_root / "03_Result"

    @property
    def report_root(self) -> Path:
        return self.case_root / "04_Report"

    @property
    def verification_root(self) -> Path:
        return self.case_root / "05_Verification"

    @property
    def temporary_root(self) -> Path:
        return self.case_root / _TEMPORARY_ROOT

    def _temporary_write_target(
        self,
        relative_path: str | Path,
        *,
        allow_event_append: bool = False,
    ) -> Path:
        target = _resolve_owned_target(self.case_root, relative_path)
        temporary_root = _reject_reparse_alias(self.temporary_root, "temporary root")
        if target == temporary_root or not target.is_relative_to(temporary_root):
            input_root = _reject_reparse_alias(self.input_root, "input root")
            if target == input_root or target.is_relative_to(input_root):
                raise ImmutableInputError("01_Input is immutable after case creation")
            raise WorkspaceBoundaryError("normal writes are restricted to 90_Temporary")

        relative = target.relative_to(self.case_root).as_posix()
        if relative == _EVENTS_FILE and not allow_event_append:
            raise WorkspaceBoundaryError("events.jsonl is append-only")
        return target

    def _control_write_target(self, relative_path: str | Path) -> Path:
        target = _resolve_owned_target(self.case_root, relative_path)
        relative = target.relative_to(self.case_root).as_posix()
        if relative not in _CONTROL_FILES:
            raise WorkspaceBoundaryError("only evidence control files may use an internal write")
        return target

    def write_bytes(self, relative_path: str | Path, data: bytes) -> Path:
        target = self._temporary_write_target(relative_path)
        return _atomic_replace_bytes(target, data, "case target")

    def write_text(
        self,
        relative_path: str | Path,
        text: str,
        *,
        encoding: str = "utf-8",
    ) -> Path:
        target = self._temporary_write_target(relative_path)
        return _atomic_replace_bytes(target, text.encode(encoding), "case target")

    def _write_control_text(
        self,
        relative_path: str | Path,
        text: str,
        *,
        encoding: str = "utf-8",
    ) -> Path:
        """Write a root-level evidence control file for ``EvidenceStore``."""

        target = self._control_write_target(relative_path)
        return _atomic_replace_bytes(target, text.encode(encoding), "control target")

    def append_bytes(self, relative_path: str | Path, data: bytes) -> Path:
        """Durably append bytes to a file in the temporary area."""

        target = self._temporary_write_target(relative_path, allow_event_append=True)
        return _append_bytes(target, data, "append target")

    def append_text(
        self,
        relative_path: str | Path,
        text: str,
        *,
        encoding: str = "utf-8",
    ) -> Path:
        """Durably append text to a file in the temporary area.

        The file is opened with append semantics, flushed, and fsynced before
        returning.  ``events.jsonl`` is intentionally excluded from normal
        ``write_text`` so callers cannot accidentally replace the chain.
        """

        return self.append_bytes(relative_path, text.encode(encoding))

    def _copy_create_new(
        self,
        source: str | Path,
        destination: str | Path,
        *,
        expected_sha256: str,
    ) -> Path:
        """Copy an EvidenceStore-authorized artifact into a permanent area once.

        This is an internal hook for ``EvidenceStore``.  Public callers must
        obtain a persisted verification record from that store; a digest alone
        is not a promotion authority.
        """

        expected = _validate_sha256(expected_sha256)
        source_path = _resolve_owned_target(self.case_root, source, allow_absolute=True)
        temporary_root = _reject_reparse_alias(self.temporary_root, "temporary root")
        if source_path == temporary_root or not source_path.is_relative_to(temporary_root):
            raise WorkspaceBoundaryError("promotion source must be inside 90_Temporary")
        if not source_path.is_file():
            raise WorkspaceBoundaryError("promotion source must be a regular file")

        destination_path = _resolve_owned_target(
            self.case_root,
            destination,
            allow_absolute=True,
        )
        relative_destination = destination_path.relative_to(self.case_root)
        if (
            len(relative_destination.parts) < 2
            or relative_destination.parts[0] not in _PERMANENT_ROOTS
        ):
            raise WorkspaceBoundaryError(
                "promotion destination must be inside a permanent case directory"
            )
        if destination_path.exists() or destination_path.is_symlink():
            raise FileExistsError(destination_path)

        if _sha256_file(source_path) != expected:
            raise ValueError("promotion source sha256 does not match expected_sha256")

        _reject_reparse_alias(destination_path.parent, "promotion destination parent")
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        _reject_reparse_alias(destination_path.parent, "promotion destination parent")
        created = False
        try:
            copied_digest = hashlib.sha256()
            with source_path.open("rb") as source_stream, destination_path.open("xb") as target:
                created = True
                for chunk in iter(lambda: source_stream.read(1024 * 1024), b""):
                    target.write(chunk)
                    copied_digest.update(chunk)
                target.flush()
                os.fsync(target.fileno())
            if copied_digest.hexdigest() != expected:
                raise ValueError("promotion source changed during copy")
        except Exception:
            if created:
                with suppress(OSError):
                    destination_path.unlink()
            raise
        return destination_path

    def promote_verified(
        self,
        source: str | Path,
        destination: str | Path,
        *,
        expected_sha256: str,
    ) -> Path:
        """Reject the former raw-digest promotion API.

        Promotion authority belongs to ``EvidenceStore`` and is represented by
        a persisted, opaque verification receipt.  Keeping this guard makes
        accidental calls fail explicitly without exposing a bypass.
        """

        del source, destination, expected_sha256
        raise TypeError("promotion requires EvidenceStore.promote_verified receipt")

    def promote_artifact(
        self,
        source: str | Path,
        destination: str | Path,
        *,
        expected_sha256: str,
    ) -> Path:
        """Reject the legacy raw-digest promotion alias."""

        del source, destination, expected_sha256
        raise TypeError("promotion requires EvidenceStore.promote_verified receipt")

    def allocate_attempt(self, attempt_id: str) -> AttemptWorkspace:
        _validate_segment(attempt_id, "attempt_id")
        _reject_reparse_alias(self.case_root, "case root")
        _reject_reparse_alias(self.temporary_root, "temporary root")
        _reject_reparse_alias(
            self.temporary_root / "attempts",
            "attempts root",
        )
        attempt_root = self.temporary_root / "attempts" / attempt_id
        _reject_reparse_alias(attempt_root, "attempt root")
        attempt_root.mkdir(parents=False, exist_ok=False)
        return AttemptWorkspace._from_manager(self, attempt_id, attempt_root)


@dataclass(frozen=True, slots=True)
class ValidatedCaseWorkspace:
    """Manager for isolated, case-owned workspaces.

    ``tool_root`` is the repository/tool tree and ``cae_root`` is the external
    ``02_CAE`` root.  Neither root itself is a writable case target.
    """

    tool_root: Path
    cae_root: Path

    def __post_init__(self) -> None:
        tool_root = _reject_reparse_alias(self.tool_root, "tool root")
        cae_root = _reject_reparse_alias(self.cae_root, "cae root")
        if tool_root == cae_root:
            raise ValueError("tool_root and cae_root must be different roots")
        if tool_root.is_relative_to(cae_root) or cae_root.is_relative_to(tool_root):
            raise ValueError("tool_root and cae_root must not overlap")
        object.__setattr__(self, "tool_root", tool_root)
        object.__setattr__(self, "cae_root", cae_root)

    def _case_path(self, case_id: str) -> Path:
        _validate_segment(case_id, "case_id")
        return self.cae_root / case_id

    @staticmethod
    def _normalise_inputs(
        original_inputs: Iterable[str | Path] | str | Path,
    ) -> tuple[Path, ...]:
        candidates: tuple[str | Path, ...]
        if isinstance(original_inputs, (str, Path)):
            candidates = (original_inputs,)
        else:
            candidates = tuple(original_inputs)

        sources: list[Path] = []
        names: set[str] = set()
        for candidate in candidates:
            source = _reject_reparse_alias(candidate, "original input")
            if not source.is_file():
                raise ValueError(f"original input is not a file: {source}")
            name_key = source.name.casefold()
            if name_key in names:
                raise ValueError(f"duplicate original input name: {source.name}")
            names.add(name_key)
            sources.append(source)
        return tuple(sources)

    def create_case(
        self,
        case_id: str,
        original_inputs: Iterable[str | Path] | str | Path = (),
    ) -> CaseWorkspace:
        """Create a new case and copy source inputs without modifying them."""

        _reject_reparse_alias(self.tool_root, "tool root")
        _reject_reparse_alias(self.cae_root, "cae root")
        case_path = _reject_reparse_alias(self._case_path(case_id), "case root")
        sources = self._normalise_inputs(original_inputs)
        self.cae_root.mkdir(parents=True, exist_ok=True)
        _reject_reparse_alias(self.cae_root, "cae root")
        _reject_reparse_alias(case_path, "case root")
        case_path.mkdir(parents=False, exist_ok=False)

        try:
            for directory_name in (
                "01_Input",
                "02_Model",
                "03_Result",
                "04_Report",
                "05_Verification",
                "90_Temporary/attempts",
            ):
                (case_path / directory_name).mkdir(parents=True, exist_ok=False)

            copied_inputs: list[Path] = []
            for source in sources:
                destination = case_path / "01_Input" / source.name
                shutil.copy2(source, destination)
                copied_inputs.append(destination.resolve())
        except Exception:
            shutil.rmtree(case_path)
            raise

        return CaseWorkspace._from_manager(self, case_id, case_path, copied_inputs, sources)

    def open_case(self, case_id: str) -> CaseWorkspace:
        """Open an existing case without granting access outside its root."""

        _reject_reparse_alias(self.tool_root, "tool root")
        _reject_reparse_alias(self.cae_root, "cae root")
        case_path = _reject_reparse_alias(self._case_path(case_id), "case root")
        if not case_path.is_dir() or not case_path.is_relative_to(self.cae_root):
            raise FileNotFoundError(f"case does not exist: {case_id}")
        _reject_reparse_alias(case_path, "case root")
        input_root = _reject_reparse_alias(case_path / "01_Input", "input root")
        original_inputs: tuple[Path, ...]
        if input_root.is_dir():
            entries = tuple(input_root.iterdir())
            normalised_entries: list[Path] = []
            for entry in entries:
                try:
                    normalised_entry = _reject_reparse_alias(entry, "original input")
                except WorkspaceBoundaryError as error:
                    raise WorkspaceBoundaryError(
                        "original input directory contains an invalid entry"
                    ) from error
                if not normalised_entry.is_file():
                    raise WorkspaceBoundaryError(
                        "original input directory contains an invalid entry"
                    )
                normalised_entries.append(normalised_entry)
            original_inputs = tuple(sorted(normalised_entries, key=str))
        else:
            original_inputs = ()
        return CaseWorkspace._from_manager(self, case_id, case_path, original_inputs)

    def write_bytes(self, case_id: str, relative_path: str | Path, data: bytes) -> Path:
        return self.open_case(case_id).write_bytes(relative_path, data)

    def write_text(
        self,
        case_id: str,
        relative_path: str | Path,
        text: str,
        *,
        encoding: str = "utf-8",
    ) -> Path:
        return self.open_case(case_id).write_text(relative_path, text, encoding=encoding)
