from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from .artifacts import RunArtifacts
from .cli import LaunchRequest
from .config import load_run_config, resolve_config
from .errors import ExitCode, LauncherError
from .febio_xml import scan_reference_feb
from .mesher import mesh_step
from .solver import run_solver
from .translator import translate_feb
from .ui import LogWindow, PreflightInfo, confirm_preflight


@contextmanager
def stage(name: str, emit: Callable[[str], None]) -> Iterator[None]:
    started = time.perf_counter()
    emit(f"[START] {name}")
    try:
        yield
    except Exception as exc:
        emit(f"[FAIL] {name}: {exc}")
        raise
    else:
        emit(f"[DONE] {name} ({time.perf_counter() - started:.2f}s)")


@dataclass(frozen=True)
class PipelineServices:
    mesher: Callable = mesh_step
    translator: Callable = translate_feb
    solver: Callable = run_solver


class _HeadlessLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.cancel_event = threading.Event()
        self._handle = None

    def start(self) -> None:
        self._handle = self.path.open("a", encoding="utf-8", buffering=1)

    def emit(self, message: str) -> None:
        print(message, flush=True)
        self._handle.write(message + "\n")

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()


def run_pipeline(
    request: LaunchRequest, services: PipelineServices | None = None
) -> ExitCode:
    selected = services or PipelineServices()
    input_feb = request.input_feb.expanduser().resolve()
    if not input_feb.is_file():
        raise LauncherError(
            f"Input FEB does not exist: {input_feb}", ExitCode.CONFIG_ERROR
        )
    config_path = resolve_config(input_feb, request.config_path)
    config = load_run_config(config_path, input_feb.stem)
    artifacts = RunArtifacts.create(input_feb)
    reference = scan_reference_feb(input_feb)
    required_surfaces = reference.required_surface_names()
    required_domains = reference.required_domain_names()
    preflight = PreflightInfo(
        model=input_feb,
        step=config.step_path,
        step_hash=config.step_sha256,
        target_size_mm=config.gmsh.target_size_mm,
        min_size_mm=config.gmsh.min_size_mm,
        surfaces=tuple(sorted(required_surfaces)),
        domains=tuple(sorted(required_domains)),
        solver=config.febio_exe,
        run_dir=artifacts.run_dir,
    )
    if not request.non_interactive and not confirm_preflight(preflight):
        raise LauncherError("Preflight cancelled", ExitCode.PREFLIGHT_CANCEL)

    log = (
        _HeadlessLog(artifacts.run_dir / "launcher.log")
        if request.non_interactive
        else LogWindow(f"FEBio Gmsh - {input_feb.stem}", artifacts.run_dir / "launcher.log")
    )
    log.start()
    try:
        with stage("CAD selection transfer and Gmsh Tet10 mesh", log.emit):
            mesh = selected.mesher(
                config.step_path,
                reference,
                required_surfaces,
                required_domains,
                config.gmsh,
                config.quality,
                mapping_tolerance=config.gmsh.mapping_tolerance_mm,
            )
        with stage("FEBio model translation", log.emit):
            selected.translator(input_feb, mesh, artifacts.translated_feb)
        artifacts.archive_existing_outputs()
        with stage("FEBio headless solve", log.emit):
            result = selected.solver(
                config.febio_exe,
                artifacts.translated_feb,
                request.febio_args,
                log.emit,
                log.cancel_event,
            )
            if result.exit_code != 0:
                raise LauncherError(
                    f"FEBio exited with code {result.exit_code}",
                    ExitCode.SOLVER_ERROR,
                )
        report = {
            "status": "success",
            "input_feb": str(input_feb),
            "config": str(config_path),
            "step": str(config.step_path),
            "step_sha256": config.step_sha256,
            "run_dir": str(artifacts.run_dir),
        }
        quality = getattr(mesh, "quality", None)
        if quality is not None:
            report["tet10_count"] = quality.element_count
            report["min_g8_det_j"] = quality.min_det_j
            report["invalid_tet10_count"] = quality.invalid_count
        artifacts.report_json.write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        artifacts.promote_success()
        log.emit(f"[SUCCESS] Result promoted to {artifacts.final_xplt}")
        return ExitCode.SUCCESS
    finally:
        log.close()
