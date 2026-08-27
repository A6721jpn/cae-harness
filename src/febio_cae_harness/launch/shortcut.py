"""Pure Start Menu shortcut descriptors.

This module serializes a descriptor instead of creating a Windows ``.lnk``.
That keeps tests and build planning deterministic while leaving real shortcut
registration to a separately authorized installer integration.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Self

from .deployment import DeploymentError, DeploymentLayout, _write_json_atomic

SHORTCUT_DESCRIPTOR_NAME = "FEBio CAE Workbench.shortcut.json"
START_MENU_RELATIVE_PATH = Path("Microsoft") / "Windows" / "Start Menu" / "Programs"
SHORTCUT_DISPLAY_NAME = "FEBio CAE Workbench"


def default_start_menu_root(environ: Mapping[str, str] | None = None) -> Path:
    """Return the per-user Start Menu Programs directory without creating it."""

    values = os.environ if environ is None else environ
    app_data = values.get("APPDATA")
    if not app_data:
        raise DeploymentError("APPDATA is not set")
    return Path(app_data) / START_MENU_RELATIVE_PATH


def _normalise_arguments(arguments: Sequence[str]) -> tuple[str, ...]:
    normalised = tuple(arguments)
    if any(not isinstance(argument, str) for argument in normalised):
        raise TypeError("shortcut arguments must be strings")
    if any("\x00" in argument for argument in normalised):
        raise ValueError("shortcut arguments cannot contain NUL")
    return normalised


@dataclass(frozen=True, slots=True)
class ShortcutDescriptor:
    """Serializable description of the one fixed Start Menu shortcut."""

    path: Path
    target: Path
    arguments: tuple[str, ...] = ()
    working_directory: Path | None = None
    display_name: str = SHORTCUT_DISPLAY_NAME
    description: str = "Launch the FEBio CAE Harness headlessly"
    fixed_target: bool = True
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", Path(os.path.abspath(os.fspath(self.path))))
        object.__setattr__(self, "target", Path(os.path.abspath(os.fspath(self.target))))
        if self.working_directory is not None:
            object.__setattr__(
                self,
                "working_directory",
                Path(os.path.abspath(os.fspath(self.working_directory))),
            )
        object.__setattr__(self, "arguments", _normalise_arguments(self.arguments))
        if not self.display_name.strip():
            raise ValueError("display_name must be non-empty")
        if not self.description.strip():
            raise ValueError("description must be non-empty")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    def to_dict(self) -> dict[str, object]:
        return {
            "arguments": list(self.arguments),
            "description": self.description,
            "display_name": self.display_name,
            "fixed_target": self.fixed_target,
            "metadata": dict(self.metadata),
            "path": str(self.path),
            "schema_version": 1,
            "target": str(self.target),
            "working_directory": (
                None if self.working_directory is None else str(self.working_directory)
            ),
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> Self:
        required = {"arguments", "description", "display_name", "fixed_target", "path", "target"}
        missing = required - set(payload)
        if missing:
            names = ", ".join(sorted(missing))
            raise ValueError(f"missing shortcut descriptor field(s): {names}")
        arguments = payload["arguments"]
        if not isinstance(arguments, (list, tuple)):
            raise TypeError("shortcut arguments must be a list")
        working_directory = payload.get("working_directory")
        if working_directory is not None and not isinstance(working_directory, str):
            raise TypeError("working_directory must be a string or None")
        metadata = payload.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise TypeError("metadata must be an object")
        return cls(
            path=Path(payload["path"]),
            target=Path(payload["target"]),
            arguments=tuple(arguments),
            working_directory=(None if working_directory is None else Path(working_directory)),
            display_name=payload["display_name"],
            description=payload["description"],
            fixed_target=payload["fixed_target"],
            metadata=dict(metadata),
        )

    def write(self) -> Path:
        """Write this descriptor atomically to its explicitly supplied path."""

        _write_json_atomic(self.path, self.to_dict())
        return self.path


def fixed_shortcut_descriptor(
    layout: DeploymentLayout,
    start_menu_root: str | Path | None = None,
    arguments: Sequence[str] = (),
    *,
    metadata: Mapping[str, str] | None = None,
) -> ShortcutDescriptor:
    """Plan the fixed shortcut pointing only to ``layout.latest / LAUNCHER_NAME``."""

    root = (
        default_start_menu_root()
        if start_menu_root is None
        else Path(os.path.abspath(os.fspath(start_menu_root)))
    )
    return ShortcutDescriptor(
        path=root / SHORTCUT_DESCRIPTOR_NAME,
        target=layout.launcher,
        arguments=tuple(arguments),
        working_directory=layout.latest,
        metadata={} if metadata is None else metadata,
    )


def write_shortcut_descriptor(descriptor: ShortcutDescriptor) -> Path:
    """Explicitly write a descriptor; no default Start Menu path is inferred."""

    return descriptor.write()


shortcut_descriptor = fixed_shortcut_descriptor


__all__ = [
    "SHORTCUT_DESCRIPTOR_NAME",
    "SHORTCUT_DISPLAY_NAME",
    "START_MENU_RELATIVE_PATH",
    "ShortcutDescriptor",
    "default_start_menu_root",
    "fixed_shortcut_descriptor",
    "shortcut_descriptor",
    "write_shortcut_descriptor",
]
