from pathlib import Path

import gmsh

from febio_gmsh_launcher.gmsh_session import GmshSession, import_step
from febio_gmsh_launcher.model import ReferenceModel
from febio_gmsh_launcher.selection_transfer import map_reference_surfaces


def _make_box_step(path: Path) -> None:
    gmsh.initialize()
    try:
        gmsh.model.add("box")
        gmsh.model.occ.addBox(0, 0, 0, 2, 3, 4)
        gmsh.model.occ.synchronize()
        gmsh.write(str(path))
    finally:
        gmsh.finalize()


def test_maps_separate_top_and_bottom_surfaces(tmp_path: Path) -> None:
    step = tmp_path / "box.step"
    _make_box_step(step)
    reference = ReferenceModel(
        nodes={
            1: (0, 0, 0),
            2: (2, 0, 0),
            3: (0, 3, 0),
            4: (2, 3, 0),
            5: (0, 0, 4),
            6: (2, 0, 4),
            7: (0, 3, 4),
            8: (2, 3, 4),
        },
        surfaces={
            "Bottom": [(1, 3, 2), (2, 3, 4)],
            "Top": [(5, 6, 7), (6, 8, 7)],
        },
    )

    with GmshSession("mapping"):
        inventory = import_step(step)
        mapped = map_reference_surfaces(reference, {"Bottom", "Top"}, inventory, 1e-6)

    assert len(mapped["Bottom"]) == 1
    assert len(mapped["Top"]) == 1
    assert mapped["Bottom"] != mapped["Top"]
