"""Validated case-owned filesystem boundaries.

This module owns case directory creation and path validation only.  It does
not inspect or infer the physical meaning of any input, model, or result.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "AttemptWorkspace",
    "CaseWorkspace",
    "ImmutableInputError",
    "ValidatedCaseWorkspace",
    "WorkspaceBoundaryError",
]


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


def _resolve_owned_target(root: Path, relative_path: str | Path) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute() or relative.anchor:
        raise WorkspaceBoundaryError("write target must be relative to the case workspace")

    target = (root / relative).resolve(strict=False)
    if target == root or not target.is_relative_to(root):
        raise WorkspaceBoundaryError("write target is outside the owned case workspace")
    return target


@dataclass(frozen=True, slots=True)
class AttemptWorkspace:
    """A bounded writer for one case-owned temporary attempt directory."""

    case_id: str
    attempt_id: str
    root: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root).resolve())
        _validate_segment(self.case_id, "case_id")
        _validate_segment(self.attempt_id, "attempt_id")

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


@dataclass(frozen=True, slots=True)
class CaseWorkspace:
    """A handle whose writes are restricted to one validated case."""

    _manager: ValidatedCaseWorkspace
    case_id: str
    case_root: Path
    original_inputs: tuple[Path, ...] = ()
    source_inputs: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        _validate_segment(self.case_id, "case_id")
        object.__setattr__(self, "case_root", Path(self.case_root).resolve())
        object.__setattr__(
            self,
            "original_inputs",
            tuple(Path(path).resolve() for path in self.original_inputs),
        )
        object.__setattr__(
            self,
            "source_inputs",
            tuple(Path(path).resolve() for path in self.source_inputs),
        )

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
    def temporary_root(self) -> Path:
        return self.case_root / "90_Temporary"

    def _write_target(self, relative_path: str | Path) -> Path:
        target = _resolve_owned_target(self.case_root, relative_path)
        input_root = (self.case_root / "01_Input").resolve(strict=False)
        if target == input_root or target.is_relative_to(input_root):
            raise ImmutableInputError("01_Input is immutable after case creation")
        return target

    def write_bytes(self, relative_path: str | Path, data: bytes) -> Path:
        target = self._write_target(relative_path)
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
        target = self._write_target(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding=encoding)
        return target

    def allocate_attempt(self, attempt_id: str) -> AttemptWorkspace:
        _validate_segment(attempt_id, "attempt_id")
        attempt_root = self.temporary_root / "attempts" / attempt_id
        attempt_root.mkdir(parents=False, exist_ok=False)
        return AttemptWorkspace(self.case_id, attempt_id, attempt_root)


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

        return CaseWorkspace(self, case_id, case_path, tuple(copied_inputs), sources)

    def open_case(self, case_id: str) -> CaseWorkspace:
        """Open an existing case without granting access outside its root."""

        case_path = self._case_path(case_id).resolve(strict=True)
        if not case_path.is_dir() or not case_path.is_relative_to(self.cae_root):
            raise FileNotFoundError(f"case does not exist: {case_id}")
        input_root = case_path / "01_Input"
        original_inputs = (
            tuple(sorted(path.resolve() for path in input_root.iterdir() if path.is_file()))
            if input_root.is_dir()
            else ()
        )
        return CaseWorkspace(self, case_id, case_path, original_inputs)

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
