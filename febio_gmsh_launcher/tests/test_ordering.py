import numpy as np

from febio_gmsh_launcher.ordering import gmsh_to_febio_tet10, gmsh_to_febio_tri6


def test_gmsh_tet10_order_is_converted_to_febio() -> None:
    gmsh = np.arange(10).reshape(1, 10)

    assert gmsh_to_febio_tet10(gmsh).tolist() == [
        [0, 1, 2, 3, 4, 5, 6, 7, 9, 8]
    ]


def test_gmsh_tri6_order_matches_febio() -> None:
    gmsh = np.arange(6).reshape(1, 6)

    assert gmsh_to_febio_tri6(gmsh).tolist() == [[0, 1, 2, 3, 4, 5]]
