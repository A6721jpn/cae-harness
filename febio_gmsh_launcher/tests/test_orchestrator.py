import hashlib
import json
from pathlib import Path

import pytest

from febio_gmsh_launcher.cli import LaunchRequest
from febio_gmsh_launcher.errors import ExitCode, LauncherError
from febio_gmsh_launcher.orchestrator import PipelineServices, run_pipeline, stage
from febio_gmsh_launcher.solver import SolverResult


def test_stage_reports_start_success_and_failure() -> None:
    messages: list[str] = []

    with stage("mesh", messages.append):
        pass
    with pytest.raises(LauncherError):
        with stage("quality", messages.append):
            raise LauncherError("bad jacobian", ExitCode.QUALITY_ERROR)

    assert messages[0] == "[START] mesh"
    assert messages[1].startswith("[DONE] mesh")
    assert messages[2] == "[START] quality"
    assert messages[3].startswith("[FAIL] quality: bad jacobian")


def test_pipeline_promotes_outputs_only_after_solver_success(tmp_path: Path) -> None:
    source = Path(__file__).parent / "fixtures" / "reference_tet4.feb"
    job = tmp_path / "model.feb"
    job.write_bytes(source.read_bytes())
    step = tmp_path / "part.step"
    step.write_bytes(b"STEP")
    config = tmp_path / "model.gmsh-run.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "model_stem": "model",
                "step_path": str(step),
                "step_sha256": hashlib.sha256(step.read_bytes()).hexdigest(),
                "gmsh": {"target_size_mm": 1, "min_size_mm": 0.2},
                "quality": {},
                "febio_exe": "fake-febio.exe",
            }
        ),
        encoding="utf-8",
    )

    def fake_mesher(*args, **kwargs):
        return object()

    def fake_translator(reference, mesh, output):
        output.write_text("<febio_spec/>", encoding="utf-8")
        return output

    def fake_solver(executable, input_feb, extra, emit, cancel):
        emit("NORMAL TERMINATION")
        input_feb.with_suffix(".log").write_text("NORMAL TERMINATION")
        input_feb.with_suffix(".xplt").write_bytes(b"XPLT")
        return SolverResult(0, ("fake",))

    result = run_pipeline(
        LaunchRequest(job, config, True, ()),
        PipelineServices(fake_mesher, fake_translator, fake_solver),
    )

    assert result == ExitCode.SUCCESS
    assert job.with_suffix(".xplt").read_bytes() == b"XPLT"
    assert "NORMAL TERMINATION" in job.with_suffix(".log").read_text()
