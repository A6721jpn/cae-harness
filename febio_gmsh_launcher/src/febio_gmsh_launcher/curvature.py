from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import QualityConfig
from .errors import ExitCode, LauncherError
from .quality import assert_quality_gate, evaluate_tet10_quality


_EDGE_CORNERS = ((0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3))


@dataclass(frozen=True)
class CurvatureResult:
    alpha: float
    corrected_node_count: int
    max_displacement_mm: float


def relax_invalid_midnodes(
    points: np.ndarray,
    tet10: np.ndarray,
    config: QualityConfig,
) -> tuple[np.ndarray, CurvatureResult]:
    original = np.asarray(points, dtype=float)
    elements = np.asarray(tet10, dtype=np.int64)
    initial = evaluate_tet10_quality(
        original, elements, min_det_j=config.min_det_j
    )
    if initial.invalid_count == 0:
        return original.copy(), CurvatureResult(1.0, 0, 0.0)
    if np.any(initial.corner_volumes[initial.invalid_indices] <= 0):
        raise LauncherError(
            "Invalid corner Tet4 cannot be repaired by curvature relaxation",
            ExitCode.QUALITY_ERROR,
        )
    targets: dict[int, list[np.ndarray]] = {}
    for element_index in initial.invalid_indices:
        element = elements[element_index]
        for offset, (left, right) in enumerate(_EDGE_CORNERS, start=4):
            targets.setdefault(int(element[offset]), []).append(
                (original[element[left]] + original[element[right]]) / 2.0
            )
    node_ids = np.array(sorted(targets), dtype=np.int64)
    fraction = len(node_ids) / max(len(original), 1)
    if fraction > config.max_corrected_fraction:
        raise LauncherError(
            f"Required corrected-node fraction {fraction:.6g} exceeds "
            f"{config.max_corrected_fraction:.6g}",
            ExitCode.QUALITY_ERROR,
        )
    straight = np.stack(
        [np.mean(targets[int(node)], axis=0) for node in node_ids], axis=0
    )
    displacement = np.linalg.norm(original[node_ids] - straight, axis=1)
    maximum = float(displacement.max(initial=0.0))
    if maximum > config.max_displacement_mm:
        raise LauncherError(
            f"Required midpoint displacement {maximum:.6g} mm exceeds "
            f"{config.max_displacement_mm:.6g} mm",
            ExitCode.QUALITY_ERROR,
        )

    def candidate(alpha: float) -> np.ndarray:
        result = original.copy()
        result[node_ids] = straight + alpha * (original[node_ids] - straight)
        return result

    fully_straight = candidate(0.0)
    straight_report = evaluate_tet10_quality(
        fully_straight, elements, min_det_j=config.min_det_j
    )
    assert_quality_gate(straight_report)
    low, high = 0.0, 1.0
    for _ in range(45):
        middle = (low + high) / 2.0
        report = evaluate_tet10_quality(
            candidate(middle), elements, min_det_j=config.min_det_j
        )
        if report.invalid_count == 0:
            low = middle
        else:
            high = middle
    if low < config.min_alpha:
        raise LauncherError(
            f"Required curvature alpha {low:.6g} is below {config.min_alpha:.6g}",
            ExitCode.QUALITY_ERROR,
        )
    corrected = candidate(low)
    assert_quality_gate(
        evaluate_tet10_quality(corrected, elements, min_det_j=config.min_det_j)
    )
    return corrected, CurvatureResult(low, len(node_ids), maximum * (1.0 - low))
