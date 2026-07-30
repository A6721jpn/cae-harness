from pathlib import Path

from febio_gmsh_launcher.ui import PreflightInfo, format_preflight


def test_preflight_text_contains_frozen_snapshot_inputs() -> None:
    text = format_preflight(
        PreflightInfo(
            model=Path("model.feb"),
            step=Path("part.step"),
            step_hash="abcdef123456",
            target_size_mm=2,
            min_size_mm=0.2,
            surfaces=("Fixed", "Loaded"),
            domains=("Part1",),
            solver=Path("febio4.exe"),
            run_dir=Path("run"),
        )
    )

    assert "Fixed, Loaded" in text
    assert "abcdef123456" in text
    assert "Tet10" in text
