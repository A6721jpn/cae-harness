import numpy as np
import pytest

from febio_gmsh_launcher.errors import ExitCode, LauncherError
from febio_gmsh_launcher.selection_transfer import triangle_signature, unique_candidate


def test_triangle_signature_reports_area_centroid_and_normal() -> None:
    points = np.array([[0, 0, 0], [2, 0, 0], [0, 3, 0]], dtype=float)

    signature = triangle_signature(points)

    assert signature.area == pytest.approx(3.0)
    assert signature.centroid.tolist() == pytest.approx([2 / 3, 1, 0])
    assert signature.normal.tolist() == pytest.approx([0, 0, 1])


def test_ambiguous_entity_candidate_is_rejected() -> None:
    with pytest.raises(LauncherError, match="ambiguous") as caught:
        unique_candidate("Loaded", [(4, 1e-8), (7, 1.1e-8)], tolerance=1e-6)

    assert caught.value.exit_code == ExitCode.TRANSFER_ERROR
