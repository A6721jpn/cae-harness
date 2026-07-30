import hashlib
import json
from pathlib import Path

import pytest

from febio_gmsh_launcher.config import load_run_config, resolve_config
from febio_gmsh_launcher.errors import ExitCode, LauncherError


def _write_config(path: Path, step: Path, model_stem: str = "model") -> Path:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "model_stem": model_stem,
                "step_path": str(step),
                "step_sha256": hashlib.sha256(step.read_bytes()).hexdigest(),
                "gmsh": {
                    "target_size_mm": 2.0,
                    "min_size_mm": 0.2,
                    "mapping_tolerance_mm": 0.002,
                },
                "quality": {"min_det_j": 0.0},
                "febio_exe": r"C:\Program Files\FEBioStudio\bin\febio4.exe",
            }
        ),
        encoding="utf-8",
    )
    return path


def test_explicit_config_wins(tmp_path: Path) -> None:
    step = tmp_path / "part.step"
    step.write_bytes(b"step")
    explicit = _write_config(tmp_path / "explicit.json", step)

    assert resolve_config(tmp_path / "model.feb", explicit) == explicit.resolve()


def test_loads_explicit_surface_mapping_tolerance(tmp_path: Path) -> None:
    step = tmp_path / "part.step"
    step.write_bytes(b"step")
    path = _write_config(tmp_path / "model.gmsh-run.json", step)

    assert load_run_config(path, "model").gmsh.mapping_tolerance_mm == 0.002


def test_config_is_found_up_to_three_parents(tmp_path: Path) -> None:
    step = tmp_path / "part.step"
    step.write_bytes(b"step")
    config = _write_config(tmp_path / "model.gmsh-run.json", step)
    feb = tmp_path / "a" / "b" / "c" / "model.feb"
    feb.parent.mkdir(parents=True)

    assert resolve_config(feb) == config.resolve()


def test_ambiguous_parent_configs_are_rejected(tmp_path: Path) -> None:
    step = tmp_path / "part.step"
    step.write_bytes(b"step")
    _write_config(tmp_path / "model.gmsh-run.json", step)
    nested = tmp_path / "a"
    nested.mkdir()
    _write_config(nested / "model.gmsh-run.json", step)
    feb = nested / "b" / "model.feb"
    feb.parent.mkdir()

    with pytest.raises(LauncherError, match="Ambiguous") as caught:
        resolve_config(feb)

    assert caught.value.exit_code == ExitCode.CONFIG_ERROR


def test_hash_mismatch_is_rejected(tmp_path: Path) -> None:
    step = tmp_path / "part.step"
    step.write_bytes(b"original")
    config_path = _write_config(tmp_path / "model.gmsh-run.json", step)
    step.write_bytes(b"changed")

    with pytest.raises(LauncherError, match="SHA-256"):
        load_run_config(config_path, expected_model_stem="model")


def test_model_stem_mismatch_is_rejected(tmp_path: Path) -> None:
    step = tmp_path / "part.step"
    step.write_bytes(b"step")
    config_path = _write_config(tmp_path / "wrong.gmsh-run.json", step, "wrong")

    with pytest.raises(LauncherError, match="model_stem"):
        load_run_config(config_path, expected_model_stem="model")
