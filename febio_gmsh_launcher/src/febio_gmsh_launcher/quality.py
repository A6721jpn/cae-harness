from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .errors import ExitCode, LauncherError


_A = 0.015835909865720057
_B = (1.0 - _A) / 3.0
_C = 0.6791431782012079
_D = (1.0 - _C) / 3.0
FEBIO_G8_BARYCENTRIC = np.array(
    [
        [_A, _B, _B, _B],
        [_B, _A, _B, _B],
        [_B, _B, _A, _B],
        [_B, _B, _B, _A],
        [_C, _D, _D, _D],
        [_D, _C, _D, _D],
        [_D, _D, _C, _D],
        [_D, _D, _D, _C],
    ],
    dtype=float,
)
_DL = np.array(
    [[-1.0, -1.0, -1.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
)
_EDGES = ((0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3))


@dataclass(frozen=True)
class QualityReport:
    element_count: int
    invalid_count: int
    invalid_indices: np.ndarray
    min_det_j: float
    min_corner_volume: float
    element_min_det_j: np.ndarray
    corner_volumes: np.ndarray


def _shape_derivatives(barycentric: np.ndarray) -> np.ndarray:
    derivatives = np.empty((10, 3), dtype=float)
    for index in range(4):
        derivatives[index] = (4.0 * barycentric[index] - 1.0) * _DL[index]
    for offset, (left, right) in enumerate(_EDGES, start=4):
        derivatives[offset] = 4.0 * (
            barycentric[left] * _DL[right]
            + barycentric[right] * _DL[left]
        )
    return derivatives


_G8_DERIVATIVES = np.stack(
    [_shape_derivatives(point) for point in FEBIO_G8_BARYCENTRIC]
)


def evaluate_tet10_quality(
    points: np.ndarray,
    tet10: np.ndarray,
    *,
    min_det_j: float = 0.0,
    chunk_size: int = 100_000,
) -> QualityReport:
    coordinates = np.asarray(points, dtype=float)
    elements = np.asarray(tet10, dtype=np.int64)
    if coordinates.ndim != 2 or coordinates.shape[1] != 3:
        raise ValueError("points must have shape (node_count, 3)")
    if elements.ndim != 2 or elements.shape[1] != 10:
        raise ValueError("tet10 must have shape (element_count, 10)")
    if elements.size and (
        int(elements.min()) < 0 or int(elements.max()) >= len(coordinates)
    ):
        raise ValueError("tet10 connectivity references an unknown point")
    element_minimum = np.full(len(elements), np.inf, dtype=float)
    corner_volumes = np.empty(len(elements), dtype=float)
    for start in range(0, len(elements), chunk_size):
        stop = min(start + chunk_size, len(elements))
        xyz = coordinates[elements[start:stop]]
        corner_matrix = np.stack(
            (xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0], xyz[:, 3] - xyz[:, 0]),
            axis=1,
        )
        corner_volumes[start:stop] = np.linalg.det(corner_matrix) / 6.0
        determinants = np.empty((stop - start, 8), dtype=float)
        for point_index, derivatives in enumerate(_G8_DERIVATIVES):
            jacobian = np.einsum("eia,ip->eap", xyz, derivatives)
            determinants[:, point_index] = np.linalg.det(jacobian)
        element_minimum[start:stop] = determinants.min(axis=1)
    invalid = (
        ~np.isfinite(element_minimum)
        | ~np.isfinite(corner_volumes)
        | (corner_volumes <= 0.0)
        | (element_minimum <= min_det_j)
    )
    return QualityReport(
        element_count=len(elements),
        invalid_count=int(np.count_nonzero(invalid)),
        invalid_indices=np.flatnonzero(invalid),
        min_det_j=float(element_minimum.min(initial=np.inf)),
        min_corner_volume=float(corner_volumes.min(initial=np.inf)),
        element_min_det_j=element_minimum,
        corner_volumes=corner_volumes,
    )


def assert_quality_gate(report: QualityReport) -> None:
    if report.invalid_count:
        sample = ", ".join(str(int(index) + 1) for index in report.invalid_indices[:10])
        raise LauncherError(
            f"{report.invalid_count} invalid Tet10 elements; "
            f"minimum G8 det(J)={report.min_det_j:.9g}; element IDs {sample}",
            ExitCode.QUALITY_ERROR,
        )
