from pathlib import Path

from febio_gmsh_launcher.solver import build_solver_command, run_solver


def test_solver_command_uses_febio_default_input_contract(tmp_path: Path) -> None:
    command = build_solver_command(
        Path(r"C:\Program Files\FEBioStudio\bin\febio4.exe"),
        tmp_path / "job.remeshed.feb",
        ("-g",),
    )

    assert command[-3:] == ["-i", "job.remeshed.feb", "-g"]


def test_run_solver_streams_output_and_returns_exit_code(tmp_path: Path) -> None:
    script = tmp_path / "fake_solver.py"
    script.write_text(
        "import pathlib,sys\n"
        "p=pathlib.Path(sys.argv[sys.argv.index('-i')+1])\n"
        "print('MODEL INITIALIZATION SUCCESS')\n"
        "p.with_suffix('.log').write_text('NORMAL TERMINATION')\n"
        "p.with_suffix('.xplt').write_bytes(b'XPLT')\n",
        encoding="utf-8",
    )
    input_feb = tmp_path / "job.remeshed.feb"
    input_feb.write_text("<febio_spec/>", encoding="utf-8")
    lines: list[str] = []

    result = run_solver(
        Path(__import__("sys").executable),
        input_feb,
        (str(script),),
        lines.append,
        argument_order="prefix",
    )

    assert result.exit_code == 0
    assert "MODEL INITIALIZATION SUCCESS" in "\n".join(lines)
    assert input_feb.with_suffix(".xplt").read_bytes() == b"XPLT"
