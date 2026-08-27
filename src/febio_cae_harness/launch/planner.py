"""Headless command planning for the fixed development deployment."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path

from .deployment import (
    BuildIdentity,
    DeploymentError,
    DeploymentLayout,
    deployment_lock,
    verify_payload_identity,
)


class LaunchError(DeploymentError):
    """Raised when a planned launcher cannot be executed."""


@dataclass(frozen=True, slots=True)
class LaunchPlan:
    """A shell-free command that always runs the fixed latest launcher."""

    layout: DeploymentLayout
    argv: tuple[str, ...]
    cwd: Path
    build_identity: BuildIdentity | None = None

    @property
    def command(self) -> tuple[str, ...]:
        return self.argv

    @property
    def launcher(self) -> Path:
        return self.layout.launcher

    def to_dict(self) -> dict[str, object]:
        identity: dict[str, str | None] | None = None
        if self.build_identity is not None:
            identity = self.build_identity.to_dict()
        return {
            "argv": list(self.argv),
            "build_identity": identity,
            "cwd": os_fspath(self.cwd),
            "launcher": os_fspath(self.launcher),
            "latest_development": os_fspath(self.layout.latest),
        }


def os_fspath(path: Path) -> str:
    """Return a string path while keeping ``LaunchPlan`` JSON-serializable."""

    return str(path)


def read_build_identity(layout: DeploymentLayout) -> BuildIdentity:
    """Read and validate the identity persisted in the current deployment."""

    with deployment_lock(layout):
        return _read_build_identity_unlocked(layout)


def _read_build_identity_unlocked(layout: DeploymentLayout) -> BuildIdentity:
    identity_path = layout.identity_path
    try:
        payload = json.loads(identity_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LaunchError(f"cannot read build identity: {identity_path}") from error
    if not isinstance(payload, dict):
        raise LaunchError(f"build identity is not an object: {identity_path}")
    try:
        identity = BuildIdentity.from_mapping(payload)
        return verify_payload_identity(layout.latest, identity)
    except (TypeError, ValueError) as error:
        raise LaunchError(f"invalid build identity: {identity_path}") from error
    except DeploymentError as error:
        raise LaunchError(str(error)) from error


def _plan_cli_launch_unlocked(
    resolved_layout: DeploymentLayout,
    normalised_arguments: tuple[str, ...],
    *,
    require_published: bool,
) -> LaunchPlan:
    identity: BuildIdentity | None = None
    if require_published:
        if not resolved_layout.latest.is_dir() or not resolved_layout.launcher.is_file():
            raise LaunchError("latest-development launcher is not installed")
        identity = _read_build_identity_unlocked(resolved_layout)
    elif resolved_layout.identity_path.is_file():
        identity = _read_build_identity_unlocked(resolved_layout)

    return LaunchPlan(
        layout=resolved_layout,
        argv=(str(resolved_layout.launcher), *normalised_arguments),
        cwd=resolved_layout.latest,
        build_identity=identity,
    )


def plan_cli_launch(
    layout: DeploymentLayout | str | Path,
    arguments: Sequence[str] = (),
    *,
    require_published: bool = False,
) -> LaunchPlan:
    """Create a shell-free launch plan for ``latest-development``.

    Versioned directories and update/network checks are intentionally absent from
    this function.  ``require_published`` is useful for a real launch request;
    callers that only need to display a plan can leave it false.
    """

    resolved_layout = (
        layout
        if isinstance(layout, DeploymentLayout)
        else DeploymentLayout.from_local_app_data(layout)
    )
    normalised_arguments = tuple(arguments)
    if any(not isinstance(argument, str) for argument in normalised_arguments):
        raise TypeError("launch arguments must be strings")
    if any("\x00" in argument for argument in normalised_arguments):
        raise ValueError("launch arguments cannot contain NUL")

    needs_lock = require_published or resolved_layout.identity_path.is_file()
    with deployment_lock(resolved_layout) if needs_lock else nullcontext():
        return _plan_cli_launch_unlocked(
            resolved_layout,
            normalised_arguments,
            require_published=require_published,
        )


def launch_cli(
    arguments: Sequence[str] = (),
    *,
    layout: DeploymentLayout | None = None,
    local_app_data: str | Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Execute the fixed launcher without a shell or version selection."""

    if layout is not None and local_app_data is not None:
        raise TypeError("pass either layout or local_app_data, not both")
    resolved_layout = layout
    if resolved_layout is None:
        resolved_layout = (
            DeploymentLayout.from_environment()
            if local_app_data is None
            else DeploymentLayout.from_local_app_data(local_app_data)
        )
    with deployment_lock(resolved_layout):
        plan = _plan_cli_launch_unlocked(
            resolved_layout,
            tuple(arguments),
            require_published=True,
        )
        try:
            return subprocess.run(
                plan.argv,
                cwd=os_fspath(plan.cwd),
                check=False,
                shell=False,
                text=True,
                capture_output=False,
            )
        except OSError as error:
            raise LaunchError(f"cannot execute launcher: {plan.launcher}") from error


__all__ = [
    "LaunchError",
    "LaunchPlan",
    "launch_cli",
    "plan_cli_launch",
    "read_build_identity",
]
