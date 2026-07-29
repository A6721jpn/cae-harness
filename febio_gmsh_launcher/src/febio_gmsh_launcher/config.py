from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .errors import ExitCode, LauncherError


@dataclass(frozen=True)
class GmshConfig:
    target_size_mm: float
    min_size_mm: float
    mapping_tolerance_mm: float = 1e-5
    algorithm3d: int = 10
    curvature_elements_per_2pi: int = 20
    heal: bool = False
    heal_tolerance_mm: float = 1e-6


@dataclass(frozen=True)
class QualityConfig:
    min_det_j: float = 0.0
    min_alpha: float = 0.0
    max_corrected_fraction: float = 0.01
    max_displacement_mm: float = 1.0


@dataclass(frozen=True)
class RunConfig:
    path: Path
    model_stem: str
    step_path: Path
    step_sha256: str
    gmsh: GmshConfig
    quality: QualityConfig
    febio_exe: Path


def _config_error(message: str) -> LauncherError:
    return LauncherError(message, ExitCode.CONFIG_ERROR)


def resolve_config(
    input_feb: Path, explicit: Path | None = None, max_parents: int = 3
) -> Path:
    if explicit is not None:
        path = explicit.expanduser().resolve()
        if not path.is_file():
            raise _config_error(f"Configuration file does not exist: {path}")
        return path

    input_path = input_feb.expanduser().resolve()
    filename = f"{input_path.stem}.gmsh-run.json"
    candidates: list[Path] = []
    folder = input_path.parent
    for _ in range(max_parents + 1):
        candidate = folder / filename
        if candidate.is_file():
            candidates.append(candidate.resolve())
        if folder.parent == folder:
            break
        folder = folder.parent
    if not candidates:
        raise _config_error(f"No {filename} found beside the FEB or in its parents")
    if len(candidates) > 1:
        listed = ", ".join(str(path) for path in candidates)
        raise _config_error(f"Ambiguous model configuration: {listed}")
    return candidates[0]


def load_run_config(path: Path, expected_model_stem: str) -> RunConfig:
    resolved = path.expanduser().resolve()
    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _config_error(f"Cannot read configuration {resolved}: {exc}") from exc
    if raw.get("schema_version") != 1:
        raise _config_error("Unsupported schema_version; expected 1")
    model_stem = str(raw.get("model_stem", ""))
    if model_stem != expected_model_stem:
        raise _config_error(
            f"Configuration model_stem {model_stem!r} does not match "
            f"{expected_model_stem!r}"
        )
    step_path = Path(str(raw.get("step_path", ""))).expanduser()
    if not step_path.is_absolute():
        step_path = resolved.parent / step_path
    step_path = step_path.resolve()
    if not step_path.is_file():
        raise _config_error(f"STEP file does not exist: {step_path}")
    expected_hash = str(raw.get("step_sha256", "")).lower()
    actual_hash = hashlib.sha256(step_path.read_bytes()).hexdigest()
    if expected_hash != actual_hash:
        raise _config_error(
            f"STEP SHA-256 mismatch: expected {expected_hash}, got {actual_hash}"
        )
    gmsh_raw = raw.get("gmsh", {})
    quality_raw = raw.get("quality", {})
    try:
        gmsh = GmshConfig(
            target_size_mm=float(gmsh_raw["target_size_mm"]),
            min_size_mm=float(gmsh_raw["min_size_mm"]),
            mapping_tolerance_mm=float(
                gmsh_raw.get(
                    "mapping_tolerance_mm",
                    max(1e-7, float(gmsh_raw["min_size_mm"]) * 1e-4),
                )
            ),
            algorithm3d=int(gmsh_raw.get("algorithm3d", 10)),
            curvature_elements_per_2pi=int(
                gmsh_raw.get("curvature_elements_per_2pi", 20)
            ),
            heal=bool(gmsh_raw.get("heal", False)),
            heal_tolerance_mm=float(gmsh_raw.get("heal_tolerance_mm", 1e-6)),
        )
        quality = QualityConfig(
            min_det_j=float(quality_raw.get("min_det_j", 0.0)),
            min_alpha=float(quality_raw.get("min_alpha", 0.0)),
            max_corrected_fraction=float(
                quality_raw.get("max_corrected_fraction", 0.01)
            ),
            max_displacement_mm=float(
                quality_raw.get("max_displacement_mm", 1.0)
            ),
        )
        febio_exe = Path(str(raw["febio_exe"])).expanduser()
    except (KeyError, TypeError, ValueError) as exc:
        raise _config_error(f"Invalid configuration value: {exc}") from exc
    if (
        gmsh.target_size_mm <= 0
        or gmsh.min_size_mm <= 0
        or gmsh.mapping_tolerance_mm <= 0
    ):
        raise _config_error("Mesh sizes must be positive")
    return RunConfig(
        path=resolved,
        model_stem=model_stem,
        step_path=step_path,
        step_sha256=actual_hash,
        gmsh=gmsh,
        quality=quality,
        febio_exe=febio_exe,
    )
