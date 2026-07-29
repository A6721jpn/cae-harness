from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import gmsh

from .errors import ExitCode, LauncherError


@dataclass(frozen=True)
class CadEntity:
    tag: int
    bbox: tuple[float, float, float, float, float, float]
    measure: float


@dataclass(frozen=True)
class CadInventory:
    surfaces: dict[int, CadEntity]
    volumes: dict[int, CadEntity]


class GmshSession:
    def __init__(self, model_name: str, terminal: bool = False) -> None:
        self.model_name = model_name
        self.terminal = terminal

    def __enter__(self) -> "GmshSession":
        if gmsh.isInitialized():
            raise RuntimeError("A Gmsh session is already active")
        gmsh.initialize()
        gmsh.option.setNumber("General.Terminal", 1 if self.terminal else 0)
        gmsh.model.add(self.model_name)
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if gmsh.isInitialized():
            gmsh.finalize()


def import_step(path: Path) -> CadInventory:
    source = path.expanduser().resolve()
    try:
        gmsh.model.occ.importShapes(str(source), highestDimOnly=False, format="step")
        gmsh.model.occ.synchronize()
    except Exception as exc:
        raise LauncherError(
            f"Gmsh failed to import STEP {source}: {exc}", ExitCode.TRANSFER_ERROR
        ) from exc
    surfaces = {
        tag: CadEntity(
            tag=tag,
            bbox=tuple(float(value) for value in gmsh.model.getBoundingBox(2, tag)),
            measure=float(gmsh.model.occ.getMass(2, tag)),
        )
        for _, tag in gmsh.model.getEntities(2)
    }
    volumes = {
        tag: CadEntity(
            tag=tag,
            bbox=tuple(float(value) for value in gmsh.model.getBoundingBox(3, tag)),
            measure=float(gmsh.model.occ.getMass(3, tag)),
        )
        for _, tag in gmsh.model.getEntities(3)
    }
    if not volumes:
        raise LauncherError(
            f"STEP did not produce a closed volume: {source}", ExitCode.TRANSFER_ERROR
        )
    return CadInventory(surfaces=surfaces, volumes=volumes)
