from __future__ import annotations

import numpy as np


GMSH_TO_FEBIO_TET10 = np.array([0, 1, 2, 3, 4, 5, 6, 7, 9, 8])
GMSH_TO_FEBIO_TRI6 = np.array([0, 1, 2, 3, 4, 5])


def _permute(connectivity: np.ndarray, permutation: np.ndarray) -> np.ndarray:
    array = np.asarray(connectivity, dtype=np.int64)
    if array.ndim != 2 or array.shape[1] != len(permutation):
        raise ValueError(
            f"connectivity must have shape (element_count, {len(permutation)})"
        )
    return array[:, permutation]


def gmsh_to_febio_tet10(connectivity: np.ndarray) -> np.ndarray:
    return _permute(connectivity, GMSH_TO_FEBIO_TET10)


def gmsh_to_febio_tri6(connectivity: np.ndarray) -> np.ndarray:
    return _permute(connectivity, GMSH_TO_FEBIO_TRI6)
