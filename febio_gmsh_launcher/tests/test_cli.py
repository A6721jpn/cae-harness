from pathlib import Path

import pytest

import febio_gmsh_launcher.cli as cli
from febio_gmsh_launcher.cli import main, parse_febio_args
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


def test_main_returns_stable_code_for_missing_input() -> None:
    assert main([]) == ExitCode.CONFIG_ERROR


def test_main_dispatches_valid_request_to_pipeline(monkeypatch) -> None:
    captured = []
    monkeypatch.setattr(cli, "run_pipeline", lambda request: captured.append(request) or 0)

    assert main(["-i", "model.feb", "--non-interactive"]) == 0
    assert captured[0].input_feb == Path("model.feb")
