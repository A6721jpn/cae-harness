from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from febio_cae.domain import MeshArtifact, PortError, PortErrorCategory
from febio_cae.domain.codec import decode_record, encode_record


def test_meshing_first_consumer() -> None:
    source = Path(__file__).resolve().parents[3] / "src"
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            (
                f"import sys; sys.path.insert(0, {str(source)!r}); "
                "import febio_cae.adapters.meshing; import febio_cae.adapters.geometry"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_box_volume_and_complete_planar_boundary(
    mesh_port: Any, synthetic_case_revision: Any
) -> None:
    artifact = mesh_port.mesh(synthetic_case_revision)
    assert decode_record(encode_record(artifact), MeshArtifact) == artifact
    nodes = {n.node_id: n.coordinates_si for n in artifact.nodes}
    tool = [e for e in artifact.elements if e.body_id == "tool-body"]
    total = 0.0
    for element in tool:
        a, b, c, d = (nodes[i] for i in element.node_ids[:4])
        u, v, w = ([p[j] - a[j] for j in range(3)] for p in (b, c, d))
        volume = (
            u[0] * (v[1] * w[2] - v[2] * w[1])
            - u[1] * (v[0] * w[2] - v[2] * w[0])
            + u[2] * (v[0] * w[1] - v[1] * w[0])
        ) / 6
        assert volume > 0
        total += volume
    dims = synthetic_case_revision.spec.rigid_tool.primitive.dimensions
    lengths = [dims[name].to_si().value for name in ("length", "width", "height")]
    assert total == pytest.approx(lengths[0] * lengths[1] * lengths[2])
    points = [nodes[i] for e in tool for i in e.node_ids]
    limits = [(min(p[j] for p in points), max(p[j] for p in points)) for j in range(3)]
    areas: dict[tuple[int, float], float] = {}
    for face in artifact.faces:
        if face.body_id != "tool-body" or len(face.adjacent_element_ids) != 1:
            continue
        corners = [nodes[i] for i in face.node_ids[:3]]
        planes = [
            (axis, limit)
            for axis in range(3)
            for limit in limits[axis]
            if all(abs(p[axis] - limit) < 1e-14 for p in corners)
        ]
        assert len(planes) == 1, "exposed internal cut"
        axis, limit = planes[0]
        j, k = [x for x in range(3) if x != axis]
        a, b, c = corners
        area = abs((b[j] - a[j]) * (c[k] - a[k]) - (b[k] - a[k]) * (c[j] - a[j])) / 2
        areas[axis, limit] = areas.get((axis, limit), 0) + area
    assert len(areas) == 6
    for (axis, _), area in areas.items():
        others = [lengths[j] for j in range(3) if j != axis]
        assert area == pytest.approx(others[0] * others[1])


def test_folded_quadratic_mapping_rejected(
    mesh_port: Any,
    synthetic_backend: Any,
    synthetic_case_revision: Any,
    monkeypatch: Any,
) -> None:
    original = synthetic_backend.mesh

    def folded(*args: Any) -> Any:
        mesh = original(*args)
        edge_node = mesh.elements[0].node_ids[4]
        return replace(
            mesh,
            nodes=tuple(
                replace(n, coordinates_si=(-0.04, 0.0, 0.0)) if n.node_id == edge_node else n
                for n in mesh.nodes
            ),
        )

    monkeypatch.setattr(synthetic_backend, "mesh", folded)
    with pytest.raises(PortError, match="quadratic") as error:
        mesh_port.mesh(synthetic_case_revision)
    assert error.value.category == PortErrorCategory.QUALITY
