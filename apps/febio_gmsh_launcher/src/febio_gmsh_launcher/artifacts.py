from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .errors import ExitCode, LauncherError


@dataclass(frozen=True)
class RunArtifacts:
    job_feb: Path
    run_dir: Path
    translated_feb: Path
    solver_xplt: Path
    solver_log: Path
    report_json: Path

    @classmethod
    def create(cls, job_feb: Path) -> "RunArtifacts":
        job = job_feb.expanduser().resolve()
        run_id = (
            datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
            + "-"
            + uuid.uuid4().hex[:8]
        )
        run_dir = job.parent / f"{job.stem}.gmsh-runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        return cls(
            job_feb=job,
            run_dir=run_dir,
            translated_feb=run_dir / f"{job.stem}.remeshed.feb",
            solver_xplt=run_dir / f"{job.stem}.remeshed.xplt",
            solver_log=run_dir / f"{job.stem}.remeshed.log",
            report_json=run_dir / "run-report.json",
        )

    @property
    def final_xplt(self) -> Path:
        return self.job_feb.with_suffix(".xplt")

    @property
    def final_log(self) -> Path:
        return self.job_feb.with_suffix(".log")

    def archive_existing_outputs(self) -> None:
        previous = self.run_dir / "previous"
        for target in (self.final_xplt, self.final_log):
            if target.exists():
                previous.mkdir(parents=True, exist_ok=True)
                shutil.move(str(target), previous / target.name)

    def promote_success(self) -> None:
        for source, target in (
            (self.solver_xplt, self.final_xplt),
            (self.solver_log, self.final_log),
        ):
            if not source.is_file() or source.stat().st_size == 0:
                raise LauncherError(
                    f"Expected solver artifact is missing or empty: {source}",
                    ExitCode.SOLVER_ERROR,
                )
            temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
            shutil.copy2(source, temporary)
            os.replace(temporary, target)
