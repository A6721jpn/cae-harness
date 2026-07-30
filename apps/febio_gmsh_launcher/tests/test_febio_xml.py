from pathlib import Path

import pytest

from febio_gmsh_launcher.errors import ExitCode, LauncherError
from febio_gmsh_launcher.febio_xml import scan_reference_feb


FIXTURES = Path(__file__).parent / "fixtures"


def test_scans_reference_mesh_and_dependencies() -> None:
    model = scan_reference_feb(FIXTURES / "reference_tet4.feb")

    assert model.nodes[1] == (0.0, 0.0, 0.0)
    assert model.tet4 == [(1, 2, 3, 4)]
    assert model.surfaces["Fixed"] == [(1, 3, 2)]
    assert model.surfaces["Loaded"] == [(1, 2, 4)]
    assert model.domains["Part1"] == "mat"
    assert model.required_surface_names() == {"Fixed", "Loaded"}
    assert model.required_domain_names() == {"Part1"}


def test_rejects_non_surface_node_selection() -> None:
    with pytest.raises(LauncherError, match="node-fix.*picked-node") as caught:
        scan_reference_feb(FIXTURES / "unsupported_node_selection.feb")

    assert caught.value.exit_code == ExitCode.TRANSFER_ERROR
