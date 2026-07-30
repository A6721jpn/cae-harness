from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import gmsh
import numpy as np

from .config import GmshConfig, QualityConfig
from .curvature import CurvatureResult, relax_invalid_midnodes
from .errors import ExitCode, LauncherError
from .gmsh_session import GmshSession, import_step
from .model import ReferenceModel
from .ordering import gmsh_to_febio_tet10, gmsh_to_febio_tri6
from .quality import QualityReport, assert_quality_gate, evaluate_tet10_quality
from .selection_transfer import map_reference_domains, map_reference_surfaces


@dataclass(frozen=True)
class MeshData:
    points: np.ndarray
    node_tags: np.ndarray
    tet10: np.ndarray
    element_tags: np.ndarray
    surfaces: dict[str, np.ndarray]
    domain_element_indices: dict[str, np.ndarray]
    quality: QualityReport
    curvature: CurvatureResult


def _connectivity_for_type(
    dimension: int, entity_tags: set[int], element_type: int, width: int
) -> tuple[np.ndarray, np.ndarray]:
    all_element_tags: list[np.ndarray] = []
    all_connectivity: list[np.ndarray] = []
    for entity_tag in sorted(entity_tags):
        types, tag_blocks, node_blocks = gmsh.model.mesh.getElements(
            dimension, entity_tag
        )
        for current_type, tags, nodes in zip(types, tag_blocks, node_blocks):
            if int(current_type) == element_type:
                all_element_tags.append(np.asarray(tags, dtype=np.int64))
                all_connectivity.append(
                    np.asarray(nodes, dtype=np.int64).reshape(-1, width)
                )
    if not all_connectivity:
        raise LauncherError(
            f"No Gmsh element type {element_type} on requested entities",
            ExitCode.TRANSFER_ERROR,
        )
    return np.concatenate(all_element_tags), np.vstack(all_connectivity)


def mesh_step(
    step_path: Path,
    reference: ReferenceModel,
    required_surfaces: set[str],
    required_domains: set[str],
    gmsh_config: GmshConfig,
    quality_config: QualityConfig,
    *,
    mapping_tolerance: float,
) -> MeshData:
    with GmshSession(step_path.stem):
        inventory = import_step(step_path)
        surface_entities = map_reference_surfaces(
            reference, required_surfaces, inventory, mapping_tolerance
        )
        domain_entities = map_reference_domains(
            reference, required_domains, inventory
        )
        for name, tags in surface_entities.items():
            gmsh.model.addPhysicalGroup(2, sorted(tags), name=name)
        for name, tags in domain_entities.items():
            gmsh.model.addPhysicalGroup(3, sorted(tags), name=name)
        gmsh.option.setNumber("Mesh.MeshSizeMin", gmsh_config.min_size_mm)
        gmsh.option.setNumber("Mesh.MeshSizeMax", gmsh_config.target_size_mm)
        gmsh.option.setNumber(
            "Mesh.MeshSizeFromCurvature",
            gmsh_config.curvature_elements_per_2pi,
        )
        gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 1)
        gmsh.option.setNumber("Mesh.Algorithm", 6)
        gmsh.option.setNumber("Mesh.Algorithm3D", gmsh_config.algorithm3d)
        gmsh.option.setNumber("Mesh.ElementOrder", 1)
        gmsh.model.mesh.generate(3)
        gmsh.model.mesh.optimize("Netgen")
        gmsh.option.setNumber("Mesh.SecondOrderLinear", 0)
        gmsh.model.mesh.setOrder(2)
        gmsh.model.mesh.optimize("HighOrder")
        gmsh.model.mesh.optimize("HighOrderElastic")

        node_tags, coordinates, _ = gmsh.model.mesh.getNodes()
        node_tags = np.asarray(node_tags, dtype=np.int64)
        points = np.asarray(coordinates, dtype=float).reshape(-1, 3)
        max_tag = int(node_tags.max(initial=0))
        node_index = np.full(max_tag + 1, -1, dtype=np.int64)
        node_index[node_tags] = np.arange(len(node_tags), dtype=np.int64)

        all_volume_tags = set().union(*domain_entities.values())
        element_tags, gmsh_tet10 = _connectivity_for_type(
            3, all_volume_tags, 11, 10
        )
        tet10 = gmsh_to_febio_tet10(node_index[gmsh_tet10])
        surfaces: dict[str, np.ndarray] = {}
        for name, tags in surface_entities.items():
            _, gmsh_tri6 = _connectivity_for_type(2, tags, 9, 6)
            surfaces[name] = gmsh_to_febio_tri6(node_index[gmsh_tri6])

        element_index_by_tag = {
            int(tag): index for index, tag in enumerate(element_tags)
        }
        domain_indices: dict[str, np.ndarray] = {}
        for name, tags in domain_entities.items():
            domain_element_tags, _ = _connectivity_for_type(3, tags, 11, 10)
            domain_indices[name] = np.array(
                [element_index_by_tag[int(tag)] for tag in domain_element_tags],
                dtype=np.int64,
            )

    quality = evaluate_tet10_quality(
        points, tet10, min_det_j=quality_config.min_det_j
    )
    if quality.invalid_count:
        points, curvature = relax_invalid_midnodes(
            points, tet10, quality_config
        )
        quality = evaluate_tet10_quality(
            points, tet10, min_det_j=quality_config.min_det_j
        )
    else:
        curvature = CurvatureResult(1.0, 0, 0.0)
    assert_quality_gate(quality)
    return MeshData(
        points=points,
        node_tags=node_tags,
        tet10=tet10,
        element_tags=element_tags,
        surfaces=surfaces,
        domain_element_indices=domain_indices,
        quality=quality,
        curvature=curvature,
    )
