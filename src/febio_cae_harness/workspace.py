"""Validated case-owned filesystem boundaries.

This module owns case directory creation and path validation only.  It does
not inspect or infer the physical meaning of any input, model, or result.
"""

from __future__ import annotations

import hashlib
import os
import shutil
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


def _resolve_owned_target(
    root: Path,
    relative_path: str | Path,
    *,
    allow_absolute: bool = False,
) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute() or relative.anchor:
        if not allow_absolute:
            raise WorkspaceBoundaryError("write target must be relative to the case workspace")
        target = relative.resolve(strict=False)
    else:
        target = (root / relative).resolve(strict=False)
    root = root.resolve(strict=False)
    if target == root or not target.is_relative_to(root):
        raise WorkspaceBoundaryError("write target is outside the owned case workspace")
    return target


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
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
        expected_root = (case_workspace.temporary_root / "attempts" / attempt_id).resolve(
            strict=False
        )
        actual_root = Path(root).resolve(strict=False)
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
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def write_text(
        self,
        relative_path: str | Path,
        text: str,
        *,
        encoding: str = "utf-8",
    ) -> Path:
        target = _resolve_owned_target(self.root, relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding=encoding)
        return target


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
        expected_root = (manager.cae_root / case_id).resolve(strict=False)
        actual_root = Path(case_root).resolve(strict=False)
        if actual_root != expected_root or not actual_root.is_dir():
            raise WorkspaceBoundaryError("case root is not owned by the validated workspace")

        input_root = (actual_root / "01_Input").resolve(strict=False)
        normalised_originals: list[Path] = []
        for path_value in original_inputs:
            source_path = Path(path_value)
            if source_path.is_symlink():
                raise WorkspaceBoundaryError("original input cannot be a symlink")
            path = source_path.resolve(strict=False)
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
            tuple(Path(path).expanduser().resolve(strict=False) for path in source_inputs),
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
        temporary_root = self.temporary_root.resolve(strict=False)
        if target == temporary_root or not target.is_relative_to(temporary_root):
            input_root = self.input_root.resolve(strict=False)
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
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def write_text(
        self,
        relative_path: str | Path,
        text: str,
        *,
        encoding: str = "utf-8",
    ) -> Path:
        target = self._temporary_write_target(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding=encoding)
        return target

    def _write_control_text(
        self,
        relative_path: str | Path,
        text: str,
        *,
        encoding: str = "utf-8",
    ) -> Path:
        """Write a root-level evidence control file for ``EvidenceStore``."""

        target = self._control_write_target(relative_path)
        target.write_text(text, encoding=encoding)
        return target

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

        target = self._temporary_write_target(relative_path, allow_event_append=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = text.encode(encoding)
        try:
            with target.open("ab") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as error:
            raise WorkspaceBoundaryError(f"cannot append temporary evidence: {target}") from error
        return target

    def promote_verified(
        self,
        source: str | Path,
        destination: str | Path,
        *,
        expected_sha256: str,
    ) -> Path:
        """Copy a verified temporary artifact into a permanent area once.

        The source must be an existing regular file below ``90_Temporary``;
        the destination must be below one of the four permanent roots.  The
        destination is opened with create-new semantics and the copied bytes
        are hashed before the file is fsynced.  A digest mismatch leaves no
        destination file behind.
        """

        expected = _validate_sha256(expected_sha256)
        source_path = _resolve_owned_target(self.case_root, source, allow_absolute=True)
        temporary_root = self.temporary_root.resolve(strict=False)
        if source_path == temporary_root or not source_path.is_relative_to(temporary_root):
            raise WorkspaceBoundaryError("promotion source must be inside 90_Temporary")
        if not source_path.is_file() or source_path.is_symlink():
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

        destination_path.parent.mkdir(parents=True, exist_ok=True)
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

    def promote_artifact(
        self,
        source: str | Path,
        destination: str | Path,
        *,
        expected_sha256: str,
    ) -> Path:
        """Compatibility name for :meth:`promote_verified`."""

        return self.promote_verified(
            source,
            destination,
            expected_sha256=expected_sha256,
        )

    def allocate_attempt(self, attempt_id: str) -> AttemptWorkspace:
        _validate_segment(attempt_id, "attempt_id")
        attempt_root = self.temporary_root / "attempts" / attempt_id
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
        tool_root = Path(self.tool_root).expanduser().resolve()
        cae_root = Path(self.cae_root).expanduser().resolve()
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
            source = Path(candidate).expanduser().resolve(strict=True)
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

        case_path = self._case_path(case_id)
        sources = self._normalise_inputs(original_inputs)
        self.cae_root.mkdir(parents=True, exist_ok=True)
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

        case_path = self._case_path(case_id).resolve(strict=True)
        if not case_path.is_dir() or not case_path.is_relative_to(self.cae_root):
            raise FileNotFoundError(f"case does not exist: {case_id}")
        input_root = case_path / "01_Input"
        original_inputs: tuple[Path, ...]
        if input_root.is_dir():
            entries = tuple(input_root.iterdir())
            if any(entry.is_symlink() or not entry.is_file() for entry in entries):
                raise WorkspaceBoundaryError("original input directory contains an invalid entry")
            original_inputs = tuple(sorted((entry.resolve() for entry in entries), key=str))
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
