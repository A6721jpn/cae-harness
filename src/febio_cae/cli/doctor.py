"""Environment capability diagnostics for the bootstrap CLI."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from febio_cae import __version__


@dataclass(frozen=True)
class CapabilitySpec:
    name: str
    label: str
    environment_variable: str
    commands: tuple[str, ...]


@dataclass(frozen=True)
class CapabilityState:
    name: str
    label: str
    status: str
    path: str | None
    source: str | None
    message: str


CAPABILITY_SPECS: tuple[CapabilitySpec, ...] = (
    CapabilitySpec(
        name="febio",
        label="FEBio solver",
        environment_variable="FEBIO_CAE_FEBIO_PATH",
        commands=("febio4.exe", "febio4", "febio.exe", "febio"),
    ),
    CapabilitySpec(
        name="febio_studio",
        label="FEBio Studio",
        environment_variable="FEBIO_CAE_STUDIO_PATH",
        commands=(
            "febio-studio.exe",
            "febio-studio",
            "febioStudio.exe",
            "febioStudio",
            "febio4studio.exe",
            "febio4studio",
        ),
    ),
    CapabilitySpec(
        name="gmsh",
        label="Gmsh",
        environment_variable="FEBIO_CAE_GMSH_PATH",
        commands=("gmsh.exe", "gmsh"),
    ),
)


def _state_for(spec: CapabilitySpec) -> CapabilityState:
    configured = os.environ.get(spec.environment_variable, "").strip()
    if configured:
        configured_path = Path(configured).expanduser()
        if configured_path.is_file():
            return CapabilityState(
                name=spec.name,
                label=spec.label,
                status="FOUND_UNVERIFIED",
                path=str(configured_path.resolve()),
                source=spec.environment_variable,
                message=(
                    f"{spec.label} was found at the configured path; native compatibility "
                    "is not verified by the bootstrap doctor."
                ),
            )
        return CapabilityState(
            name=spec.name,
            label=spec.label,
            status="MISSING",
            path=str(configured_path),
            source=spec.environment_variable,
            message=f"Configured {spec.label} path does not exist or is not a file.",
        )

    for command in spec.commands:
        resolved = shutil.which(command)
        if resolved:
            return CapabilityState(
                name=spec.name,
                label=spec.label,
                status="FOUND_UNVERIFIED",
                path=str(Path(resolved).resolve()),
                source="PATH",
                message=(
                    f"{spec.label} was found on PATH; native compatibility is not verified "
                    "by the bootstrap doctor."
                ),
            )

    return CapabilityState(
        name=spec.name,
        label=spec.label,
        status="MISSING",
        path=None,
        source=None,
        message=(
            f"{spec.label} was not found. Configure {spec.environment_variable} or add a "
            "supported executable to PATH."
        ),
    )


def doctor_payload() -> dict[str, object]:
    states = tuple(_state_for(spec) for spec in CAPABILITY_SPECS)
    missing = tuple(state for state in states if state.status == "MISSING")
    diagnostics: list[dict[str, object]] = []
    next_actions: list[str] = []

    for state in states:
        if state.status == "MISSING":
            diagnostics.append(
                {
                    "code": "CAPABILITY_MISSING",
                    "severity": "error",
                    "capability": state.name,
                    "message": state.message,
                }
            )
            next_actions.append(
                f"Configure {next(spec.environment_variable for spec in CAPABILITY_SPECS if spec.name == state.name)}"
            )
        else:
            diagnostics.append(
                {
                    "code": "CAPABILITY_UNVERIFIED",
                    "severity": "warning",
                    "capability": state.name,
                    "message": state.message,
                }
            )

    return {
        "schema_version": "1",
        "status": "UNAVAILABLE" if missing else "UNVERIFIED",
        "version": __version__,
        "capabilities": {state.name: asdict(state) for state in states},
        "diagnostics": diagnostics,
        "next_actions": next_actions,
    }


def run_doctor(*, json_output: bool) -> int:
    """Print the structured capability report and return its CLI exit code."""

    del json_output  # Both modes use the same stable machine-readable contract in P0.
    payload = doctor_payload()
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 4 if payload["status"] == "UNAVAILABLE" else 0
