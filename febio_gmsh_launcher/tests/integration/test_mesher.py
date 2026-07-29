from pathlib import Path

import gmsh

from febio_gmsh_launcher.config import GmshConfig, QualityConfig
from febio_gmsh_launcher.mesher import mesh_step
from febio_gmsh_launcher.model import ReferenceModel
from febio_gmsh_launcher.quality import evaluate_tet10_quality


def _make_box_step(path: Path) -> None:
    gmsh.initialize()
    try:
        gmsh.model.add("box")
        gmsh.model.occ.addBox(0, 0, 0, 1, 1, 1)
        gmsh.model.occ.synchronize()
        gmsh.write(str(path))
    finally:
        gmsh.finalize()


def test_meshes_curved_tet10_and_retains_surface_groups(tmp_path: Path) -> None:
    step = tmp_path / "box.step"
    _make_box_step(step)
    reference = ReferenceModel(
        nodes={
            1: (0, 0, 0), 2: (1, 0, 0), 3: (0, 1, 0), 4: (1, 1, 0),
            5: (0, 0, 1), 6: (1, 0, 1), 7: (0, 1, 1), 8: (1, 1, 1),
        },
        surfaces={
            "Bottom": [(1, 3, 2), (2, 3, 4)],
            "Top": [(5, 6, 7), (6, 8, 7)],
        },
        domains={"Part1": "mat"},
    )

    mesh = mesh_step(
        step,
        reference,
        {"Bottom", "Top"},
        {"Part1"},
        GmshConfig(target_size_mm=0.45, min_size_mm=0.2),
        QualityConfig(max_corrected_fraction=1, max_displacement_mm=1),
        mapping_tolerance=1e-6,
    )

    assert mesh.tet10.shape[1] == 10
    assert mesh.surfaces["Bottom"].shape[1] == 6
    assert mesh.surfaces["Top"].shape[1] == 6
    assert set(mesh.domain_element_indices) == {"Part1"}
    assert evaluate_tet10_quality(mesh.points, mesh.tet10).invalid_count == 0
