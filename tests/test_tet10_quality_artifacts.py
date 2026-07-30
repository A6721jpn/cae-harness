import sys
import unittest
from pathlib import Path

import numpy as np


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from scripts.tet10.tet10_quality_artifacts import (
    build_quality_records,
    gmsh_to_meshio_tet10,
)


class BuildQualityRecordsTests(unittest.TestCase):
    def test_converts_gmsh_tet10_midside_node_order_for_meshio(self):
        gmsh_order = np.array([[0, 1, 2, 3, 4, 5, 6, 7, 8, 9]])
        meshio_order = gmsh_to_meshio_tet10(gmsh_order)
        np.testing.assert_array_equal(
            meshio_order,
            [[0, 1, 2, 3, 4, 5, 6, 7, 9, 8]],
        )

    def test_centroids_volumes_bands_and_element_ids(self):
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
        tet10 = np.array([[0, 1, 2, 3, 4, 5, 6, 7, 8, 9]] * 4)
        result = build_quality_records(
            points=points,
            tet10=tet10,
            gmsh_element_tags=np.array([101, 102, 103, 104]),
            min_sicn=np.array([0.005, 0.03, 0.08, 0.2]),
            gamma=np.array([0.1, 0.2, 0.3, 0.4]),
            min_sige=np.array([0.01, 0.02, 0.03, 0.04]),
        )

        np.testing.assert_allclose(result["centroids"], [[0.25, 0.25, 0.25]] * 4)
        np.testing.assert_allclose(result["corner_signed_volume_mm3"], [1.0 / 6.0] * 4)
        np.testing.assert_array_equal(result["quality_band"], [0, 1, 2, 3])
        np.testing.assert_array_equal(result["inp_element_id"], [1, 2, 3, 4])
        np.testing.assert_array_equal(result["low_quality_mask"], [True, True, True, False])

    def test_rejects_mismatched_quality_array(self):
        with self.assertRaisesRegex(ValueError, "same element count"):
            build_quality_records(
                points=np.zeros((10, 3)),
                tet10=np.zeros((2, 10), dtype=int),
                gmsh_element_tags=np.array([1, 2]),
                min_sicn=np.array([0.1]),
                gamma=np.array([0.1, 0.2]),
                min_sige=np.array([0.1, 0.2]),
            )


if __name__ == "__main__":
    unittest.main()
