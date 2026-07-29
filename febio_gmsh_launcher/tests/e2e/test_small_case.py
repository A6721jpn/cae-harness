from pathlib import Path

from febio_gmsh_launcher.cli import LaunchRequest
from febio_gmsh_launcher.errors import ExitCode
from febio_gmsh_launcher.orchestrator import run_pipeline

from .make_small_case import make_small_case


def test_real_gmsh_to_febio_small_case(tmp_path: Path) -> None:
    febio = Path(r"C:\Program Files\FEBioStudio\bin\febio4.exe")
    assert febio.is_file()
    feb = make_small_case(tmp_path, febio)

    result = run_pipeline(LaunchRequest(feb, None, True, ()))

    assert result == ExitCode.SUCCESS
    assert feb.with_suffix(".xplt").stat().st_size > 100
    assert "N O R M A L   T E R M I N A T I O N" in feb.with_suffix(".log").read_text(
        encoding="utf-8", errors="replace"
    )
