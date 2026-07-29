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


def test_repair_expands_to_neighbor_midside_nodes_when_shared_node_distorts_it() -> None:
    # Reduced from 02_Bottom_Frame,0729_CAE.feb elements
    # 55938, 84872, and 99543. The outer two are initially invalid; repairing
    # only their midside nodes makes the initially valid center element invalid.
    points = np.array(
        [
            [4.9571439, 29.5013413, 10.7503862],
            [4.82019215, 29.5392919, 11.3470579],
            [4.96893653, 29.4937584, 10.7002174],
            [5.17473259, 29.7008715, 10.7002174],
            [5.3354816, 29.4327899, 10.3668793],
            [5.07341055, 29.7376908, 11.1660637],
            [4.74469179, 29.5932804, 11.0556632],
            [4.89740556, 29.7106052, 10.7019987],
            [5.06271215, 29.4979823, 10.3909608],
            [4.88865673, 29.5205599, 11.048704],
            [4.96301086, 29.498612, 10.7251342],
            [5.23625132, 29.5991861, 10.4966962],
            [5.0293147, 29.6395644, 10.6798851],
            [4.97014087, 29.6650172, 10.9449424],
            [4.90171265, 29.6839943, 11.2433679],
            [5.02180564, 29.6441584, 10.707476],
            [5.09542049, 29.4753939, 10.4717815],
            [5.11872237, 29.5994269, 10.5455891],
            [5.01582434, 29.4958703, 10.5455891],
            [4.78244197, 29.5662862, 11.2013605],
            [5.03606907, 29.7057383, 10.701108],
            [4.85091784, 29.5473108, 10.9030247],
            [4.90905117, 29.6654856, 11.1108634],
            [5.19909688, 29.4653861, 10.3789201],
            [4.92727473, 29.6059732, 10.7261924],
            [4.93317104, 29.6021818, 10.701108],
        ]
    )
    elements = np.array(
        [
            [2, 4, 8, 3, 16, 23, 18, 12, 11, 17],
            [0, 7, 3, 2, 24, 20, 15, 10, 25, 12],
            [1, 0, 6, 5, 9, 21, 19, 14, 13, 22],
        ]
    )

    fixed, result = relax_invalid_midnodes(
        points,
        elements,
        QualityConfig(
            min_det_j=0,
            min_alpha=0,
            max_corrected_fraction=1,
            max_displacement_mm=10,
        ),
    )

    assert result.corrected_node_count == 17
    assert evaluate_tet10_quality(fixed, elements).invalid_count == 0
