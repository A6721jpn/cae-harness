from pathlib import Path

import pytest

from febio_gmsh_launcher.cli import parse_febio_args
from febio_gmsh_launcher.errors import ExitCode, LauncherError


def test_parse_studio_default_input_argument() -> None:
    request = parse_febio_args(["-i", "Bottom Frame.feb"])

    assert request.input_feb == Path("Bottom Frame.feb")
    assert request.config_path is None
    assert request.non_interactive is False


def test_parse_preserves_supported_febio_passthrough_flags() -> None:
    request = parse_febio_args(
        ["-i", "model.feb", "-g", "-config", "solver config.xml"]
    )

    assert request.febio_args == ("-g", "-config", "solver config.xml")


def test_parse_accepts_launcher_options() -> None:
    request = parse_febio_args(
        ["-i", "model.feb", "--gmsh-config", "custom.json", "--non-interactive"]
    )

    assert request.config_path == Path("custom.json")
    assert request.non_interactive is True


def test_missing_input_has_stable_config_exit_code() -> None:
    with pytest.raises(LauncherError) as caught:
        parse_febio_args([])

    assert caught.value.exit_code == ExitCode.CONFIG_ERROR
