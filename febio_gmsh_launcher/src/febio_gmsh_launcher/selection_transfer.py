from __future__ import annotations

from dataclasses import dataclass

import gmsh
import numpy as np

from .errors import ExitCode, LauncherError
from .gmsh_session import CadInventory
from .model import ReferenceModel


@dataclass(frozen=True)
class TriangleSignature:
    area: float
    centroid: np.ndarray
    normal: np.ndarray


def triangle_signature(points: np.ndarray) -> TriangleSignature:
    xyz = np.asarray(points, dtype=float)
    if xyz.shape != (3, 3):
        raise ValueError("triangle points must have shape (3, 3)")
    cross = np.cross(xyz[1] - xyz[0], xyz[2] - xyz[0])
    length = float(np.linalg.norm(cross))
    if length == 0:
        raise LauncherError("Degenerate reference triangle", ExitCode.TRANSFER_ERROR)
    return TriangleSignature(
        area=length / 2.0,
        centroid=xyz.mean(axis=0),
        normal=cross / length,
    )


def unique_candidate(
    selection_name: str, candidates: list[tuple[int, float]], tolerance: float
) -> int:
    valid = sorted((tag, distance) for tag, distance in candidates if distance <= tolerance)
    valid.sort(key=lambda item: item[1])
    if not valid:
        raise LauncherError(
            f"Surface {selection_name!r} has no CAD entity within {tolerance:g}",
            ExitCode.TRANSFER_ERROR,
        )
    if len(valid) > 1 and valid[1][1] - valid[0][1] <= max(tolerance * 0.1, 1e-12):
        raise LauncherError(
            f"Surface {selection_name!r} mapping is ambiguous between "
            f"CAD entities {valid[0][0]} and {valid[1][0]}",
            ExitCode.TRANSFER_ERROR,
        )
    return valid[0][0]


def _bbox_contains(
    bbox: tuple[float, float, float, float, float, float],
    point: np.ndarray,
    tolerance: float,
) -> bool:
    return all(
        bbox[index] - tolerance <= point[index] <= bbox[index + 3] + tolerance
        for index in range(3)
    )


def map_reference_surfaces(
    reference: ReferenceModel,
    required_names: set[str],
    inventory: CadInventory,
    tolerance: float,
) -> dict[str, set[int]]:
    mapped: dict[str, set[int]] = {}
    for name in sorted(required_names):
        triangles = reference.surfaces.get(name)
        if not triangles:
            raise LauncherError(
                f"Required Surface {name!r} is empty or missing",
                ExitCode.TRANSFER_ERROR,
            )
        tags: set[int] = set()
        reference_area = 0.0
        for triangle in triangles:
            signature = triangle_signature(
                np.array([reference.nodes[node] for node in triangle], dtype=float)
            )
            reference_area += signature.area
            distances: list[tuple[int, float]] = []
            for tag, entity in inventory.surfaces.items():
                if not _bbox_contains(entity.bbox, signature.centroid, tolerance):
                    continue
                closest, _ = gmsh.model.getClosestPoint(
                    2, tag, signature.centroid.tolist()
                )
                distance = float(
                    np.linalg.norm(np.asarray(closest).reshape(-1, 3)[0] - signature.centroid)
                )
                distances.append((tag, distance))
            tags.add(unique_candidate(name, distances, tolerance))
        cad_area = sum(inventory.surfaces[tag].measure for tag in tags)
        area_error = abs(cad_area - reference_area) / max(cad_area, reference_area, 1e-30)
        if area_error > 0.05:
            raise LauncherError(
                f"Surface {name!r} area mismatch: reference={reference_area:g}, "
                f"CAD={cad_area:g}",
                ExitCode.TRANSFER_ERROR,
            )
        mapped[name] = tags
    return mapped


def map_reference_domains(
    reference: ReferenceModel, required_names: set[str], inventory: CadInventory
) -> dict[str, set[int]]:
    if len(required_names) == 1 and len(inventory.volumes) == 1:
        return {next(iter(required_names)): {next(iter(inventory.volumes))}}
    raise LauncherError(
        "Multiple CAD volume/domain mapping is not uniquely supported in schema 1",
        ExitCode.TRANSFER_ERROR,
    )
