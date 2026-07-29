from pathlib import Path

from febio_gmsh_launcher.artifacts import RunArtifacts


def test_run_artifacts_are_unique(tmp_path: Path) -> None:
    job = tmp_path / "model.feb"

    first = RunArtifacts.create(job)
    second = RunArtifacts.create(job)

    assert first.run_dir != second.run_dir
    assert first.run_dir.is_dir()
    assert second.run_dir.is_dir()


def test_success_promotion_is_atomic_and_deferred(tmp_path: Path) -> None:
    job = tmp_path / "model.feb"
    final_xplt = tmp_path / "model.xplt"
    final_log = tmp_path / "model.log"
    final_xplt.write_bytes(b"old-result")
    final_log.write_text("old-log", encoding="utf-8")
    artifacts = RunArtifacts.create(job)
    artifacts.solver_xplt.write_bytes(b"new-result")
    artifacts.solver_log.write_text("new-log", encoding="utf-8")

    assert final_xplt.read_bytes() == b"old-result"
    artifacts.promote_success()

    assert final_xplt.read_bytes() == b"new-result"
    assert final_log.read_text(encoding="utf-8") == "new-log"
