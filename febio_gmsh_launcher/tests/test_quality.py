import numpy as np
import pytest

from febio_gmsh_launcher.errors import ExitCode, LauncherError
from febio_gmsh_launcher.quality import assert_quality_gate, evaluate_tet10_quality


def _unit_tet10() -> tuple[np.ndarray, np.ndarray]:
    points = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
            [0.5, 0.0, 0.0],
            [0.5, 0.5, 0.0],
            [0.0, 0.5, 0.0],
            [0.0, 0.0, 0.5],
            [0.5, 0.0, 0.5],
            [0.0, 0.5, 0.5],
        ]
    )
    return points, np.arange(10).reshape(1, 10)


def test_straight_unit_tet10_has_positive_unit_jacobian() -> None:
    points, elements = _unit_tet10()

    report = evaluate_tet10_quality(points, elements)

    assert report.invalid_count == 0
    assert report.min_det_j == pytest.approx(1.0)
    assert report.min_corner_volume == pytest.approx(1.0 / 6.0)


def test_inverted_corner_tet_is_rejected() -> None:
    points, elements = _unit_tet10()
    elements = elements.copy()
    elements[:, [1, 2]] = elements[:, [2, 1]]

    report = evaluate_tet10_quality(points, elements)

    assert report.invalid_count == 1
    with pytest.raises(LauncherError) as caught:
        assert_quality_gate(report)
    assert caught.value.exit_code == ExitCode.QUALITY_ERROR


def test_curved_midnode_can_fail_g8_while_corners_stay_positive() -> None:
    points, elements = _unit_tet10()
    points[4] = [2.0, 0.0, 0.0]

    report = evaluate_tet10_quality(points, elements)

    assert report.min_corner_volume > 0
    assert report.min_det_j < 0
    assert report.invalid_count == 1
