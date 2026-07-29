import numpy as np
import pytest

from febio_gmsh_launcher.config import QualityConfig
from febio_gmsh_launcher.curvature import relax_invalid_midnodes
from febio_gmsh_launcher.errors import LauncherError
from febio_gmsh_launcher.quality import evaluate_tet10_quality


def _bad_curved_tet() -> tuple[np.ndarray, np.ndarray]:
    points = np.array(
        [
            [0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1],
            [2, 0, 0], [0.5, 0.5, 0], [0, 0.5, 0],
            [0, 0, 0.5], [0.5, 0, 0.5], [0, 0.5, 0.5],
        ],
        dtype=float,
    )
    return points, np.arange(10).reshape(1, 10)


def test_relaxes_only_midnodes_until_full_g8_gate_passes() -> None:
    points, elements = _bad_curved_tet()
    fixed, result = relax_invalid_midnodes(
        points,
        elements,
        QualityConfig(
            min_det_j=0,
            min_alpha=0,
            max_corrected_fraction=1,
            max_displacement_mm=2,
        ),
    )

    assert 0 <= result.alpha < 1
    assert result.corrected_node_count == 6
    assert np.array_equal(fixed[:4], points[:4])
    assert evaluate_tet10_quality(fixed, elements).invalid_count == 0


def test_rejects_correction_beyond_configured_fraction() -> None:
    points, elements = _bad_curved_tet()

    with pytest.raises(LauncherError, match="fraction"):
        relax_invalid_midnodes(
            points,
            elements,
            QualityConfig(max_corrected_fraction=0.1, max_displacement_mm=2),
        )
