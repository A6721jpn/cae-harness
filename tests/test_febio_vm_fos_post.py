import importlib.util
from pathlib import Path
import tempfile
import unittest

import numpy as np

from scripts.post.febio_vm_fos_post import (
    average_element_node_values,
    export_final_vtu,
    rough_safety_factor,
    von_mises_from_symmetric,
)


class VonMisesTests(unittest.TestCase):
    def test_uniaxial_stress_returns_absolute_axial_stress(self):
        stress = np.array([[12.5, 0.0, 0.0, 0.0, 0.0, 0.0]])

        actual = von_mises_from_symmetric(stress)

        np.testing.assert_allclose(actual, [12.5])

    def test_pure_shear_returns_sqrt_three_times_shear(self):
        stress = np.array([[0.0, 0.0, 0.0, 7.0, 0.0, 0.0]])

        actual = von_mises_from_symmetric(stress)

        np.testing.assert_allclose(actual, [12.12435565298214])


class RoughSafetyFactorTests(unittest.TestCase):
    def test_uses_provisional_strength_and_stress_floor(self):
        von_mises = np.array([46.17, 23.085, 0.0])

        actual = rough_safety_factor(von_mises)

        np.testing.assert_allclose(actual, [1.0, 2.0, 4617.0])


class ProjectionTests(unittest.TestCase):
    def test_averages_element_node_values_at_shared_global_nodes(self):
        connectivity = np.array([[0, 1, 2], [1, 3, 2]])
        values = np.array([[10.0, 20.0, 30.0], [40.0, 50.0, 70.0]])

        actual = average_element_node_values(
            connectivity=connectivity,
            element_node_values=values,
            point_count=4,
        )

        np.testing.assert_allclose(actual, [10.0, 30.0, 50.0, 50.0])

    def test_ignores_nonfinite_element_node_values(self):
        connectivity = np.array([[0, 1], [0, 1]])
        values = np.array([[np.nan, 4.0], [6.0, np.inf]])

        actual = average_element_node_values(
            connectivity=connectivity,
            element_node_values=values,
            point_count=2,
        )

        np.testing.assert_allclose(actual, [6.0, 4.0])


ACTUAL_XPLT = Path(
    r"C:\dev\FEBio\jobs\jobs\02_Bottom_Frame,0729_CAE.xplt"
)
HAS_POST_RUNTIME = (
    importlib.util.find_spec("pyfebiopt") is not None
    and importlib.util.find_spec("pyvista") is not None
)


@unittest.skipUnless(
    HAS_POST_RUNTIME and ACTUAL_XPLT.exists(),
    "requires the dedicated FEBio post runtime and completed XPLT",
)
class ActualXpltExportTests(unittest.TestCase):
    def test_exports_main_body_with_persistent_vm_and_fos_arrays(self):
        import pyvista as pv

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "bottom_frame_vm_fos.vtu"

            summary = export_final_vtu(
                xplt_path=ACTUAL_XPLT,
                output_path=output,
                domain="Part2",
            )
            grid = pv.read(output)

        self.assertEqual(grid.n_cells, 109712)
        self.assertEqual(grid.n_points, 161411)
        np.testing.assert_array_equal(np.unique(grid.celltypes), [24])
        self.assertIn("von_Mises_stress_MPa", grid.point_data)
        self.assertIn("rough_FOS_46p17MPa", grid.point_data)
        self.assertIn("displacement_mm", grid.point_data)
        self.assertIn("von_Mises_stress_element_MPa", grid.cell_data)
        self.assertIn("rough_FOS_element_46p17MPa", grid.cell_data)
        self.assertAlmostEqual(summary["time"], 1.0, places=7)
        self.assertAlmostEqual(summary["element_vm_max_mpa"], 22.7877849933, places=5)
        self.assertAlmostEqual(summary["element_fos_min"], 2.0260854670, places=5)
        self.assertEqual(grid.active_scalars_name, "von_Mises_stress_MPa")


if __name__ == "__main__":
    unittest.main()
