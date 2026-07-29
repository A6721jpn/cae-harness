from pathlib import Path

import numpy as np
import pytest

from febio_gmsh_launcher.curvature import CurvatureResult
from febio_gmsh_launcher.errors import LauncherError
from febio_gmsh_launcher.febio_xml import scan_reference_feb
from febio_gmsh_launcher.mesher import MeshData
from febio_gmsh_launcher.quality import evaluate_tet10_quality
from febio_gmsh_launcher.translator import translate_feb


FIXTURES = Path(__file__).parent / "fixtures"


def _mesh() -> MeshData:
    points = np.array(
        [
            [0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1],
            [0.5, 0, 0], [0.5, 0.5, 0], [0, 0.5, 0],
            [0, 0, 0.5], [0.5, 0, 0.5], [0, 0.5, 0.5],
        ],
        dtype=float,
    )
    tet10 = np.arange(10).reshape(1, 10)
    return MeshData(
        points=points,
        node_tags=np.arange(1, 11),
        tet10=tet10,
        element_tags=np.array([1]),
        surfaces={
            "Fixed": np.array([[0, 2, 1, 6, 5, 4]]),
            "Loaded": np.array([[0, 1, 3, 4, 8, 7]]),
        },
        domain_element_indices={"Part1": np.array([0])},
        quality=evaluate_tet10_quality(points, tet10),
        curvature=CurvatureResult(1, 0, 0),
    )


def test_replaces_mesh_but_preserves_analysis_semantics(tmp_path: Path) -> None:
    output = tmp_path / "translated.feb"

    translate_feb(FIXTURES / "reference_tet4.feb", _mesh(), output)
    text = output.read_text(encoding="utf-8")
    rescanned = scan_reference_feb(output)

    assert '<Elements type="tet10" name="Part1">' in text
    assert '<bc name="fix" node_set="@surface:Fixed"' in text
    assert '<bc name="push" surface="Loaded"' in text
    assert rescanned.required_surface_names() == {"Fixed", "Loaded"}
    assert rescanned.domains == {"Part1": "mat"}


def test_missing_required_surface_is_rejected(tmp_path: Path) -> None:
    mesh = _mesh()
    mesh.surfaces.pop("Loaded")

    with pytest.raises(LauncherError, match="Loaded"):
        translate_feb(FIXTURES / "reference_tet4.feb", mesh, tmp_path / "bad.feb")
