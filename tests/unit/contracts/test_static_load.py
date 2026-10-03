from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from febio_cae.adapters.febio.compiler import LocalBundleStore
from febio_cae.adapters.febio.static_load import compile_static
from febio_cae.application.static_load import _profile, status
from febio_cae.domain import FrameId
from febio_cae.domain.artifacts import (
    TET10_FACE_NODE_POSITIONS,
    TET10_FACE_ORDER_ID,
    TET10_NODE_ORDER_ID,
    MeshArtifact,
    MeshElement,
    MeshFace,
    MeshNode,
    MeshProvenance,
    MeshSet,
)
from febio_cae.domain.static_load import (
    EdgeTotalForce,
    StaticLoadRequest,
    integrate_edge_totals,
)
from febio_cae.storage.static_load import StaticLoadStore


def request() -> StaticLoadRequest:
    return StaticLoadRequest.from_dict(
        {
            "schema_version": "1",
            "source_sha256": "a" * 64,
            "fixed": {"face_ids": [1], "components": ["x", "y", "z"], "frame": "World"},
            "loads": [
                {
                    "curve_id": tag,
                    "semantics": "TOTAL",
                    "unit": "N",
                    "frame": "World",
                    "vector": [0, 0, 10],
                }
                for tag in (12, 14)
            ],
            "material": {
                "model": "isotropic_linear_elastic",
                "youngs_modulus": {"value": 68000, "unit": "MPa"},
                "poisson_ratio": {"value": 0.33, "unit": "1"},
            },
            "mesh": {
                "global_size": {"value": 1, "unit": "mm"},
                "native_coordinate_unit": "MM",
                "algorithm_2d": 6,
                "algorithm_3d": 1,
                "curvature_points": 20,
                "max_nodes": 1000,
                "max_elements": 1000,
            },
            "budget": {
                "cpu_workers": 1,
                "mesh_wall_seconds": 600,
                "solver_wall_seconds": 600,
                "memory_bytes": 100000000,
            },
        }
    )


def mesh(spec: StaticLoadRequest) -> MeshArtifact:
    points = (
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
        (0.5, 0.0, 0.0),
        (0.5, 0.5, 0.0),
        (0.0, 0.5, 0.0),
        (0.0, 0.0, 0.5),
        (0.5, 0.0, 0.5),
        (0.0, 0.5, 0.5),
    )
    element = MeshElement(1, "tet10", tuple(range(1, 11)), "body-1")
    sets = (
        MeshSet("fixed-face-1", "node", "body-1", (4, 8, 9, 10), spec.selection_digest("face", 1)),
        MeshSet("loaded-curve-12", "node", "body-1", (1, 2, 5), spec.selection_digest("curve", 12)),
        MeshSet("loaded-curve-14", "node", "body-1", (2, 3, 6), spec.selection_digest("curve", 14)),
    )
    provenance = MeshProvenance(
        "b" * 64,
        ("body-1",),
        tuple(s.source_selection_digest for s in sets),
        spec.digest,
        "gmsh-occ",
        "4.15.2",
        "backend-tet10-to-domain-tet10-v1",
        TET10_NODE_ORDER_ID,
        TET10_FACE_ORDER_ID,
    )
    faces = tuple(
        MeshFace(f"face-{i}", "body-1", tuple(element.node_ids[p] for p in positions), (1,), (i,))
        for i, positions in enumerate(TET10_FACE_NODE_POSITIONS)
    )
    return MeshArtifact(
        "test-static-mesh",
        FrameId("World"),
        provenance,
        tuple(MeshNode(i + 1, p) for i, p in enumerate(points)),
        (element,),
        faces,
        sets,
        (),
    )


def test_line3_totals_conserve_each_edge_and_accumulate_shared_endpoint() -> None:
    spec = request()
    artifact = mesh(spec)
    curves: dict[int, tuple[tuple[int, int, int], ...]] = {12: ((1, 2, 5),), 14: ((2, 3, 6),)}
    forces = integrate_edge_totals(spec, artifact, curves, {4, 8, 9, 10})
    assert forces[1] == pytest.approx((0, 0, 10 / 6))
    assert forces[2] == pytest.approx((0, 0, 20 / 6))
    assert forces[3] == pytest.approx((0, 0, 10 / 6))
    assert forces[5] == pytest.approx((0, 0, 20 / 3))
    assert forces[6] == pytest.approx((0, 0, 20 / 3))
    assert sum(f[2] for f in forces.values()) == pytest.approx(20)
    changed = replace(spec, loads=(EdgeTotalForce(12, (0, 0, 3)), EdgeTotalForce(14, (0, 0, -7))))
    result = integrate_edge_totals(changed, artifact, curves, {4})
    assert sum(f[2] for f in result.values()) == pytest.approx(-4)
    assert result[2][2] == pytest.approx(-4 / 6)


@pytest.mark.parametrize("fixed", [{1}, {5}])
def test_support_overlap_rejects_corner_and_midside(fixed: set[int]) -> None:
    spec = request()
    with pytest.raises(ValueError, match="overlaps"):
        integrate_edge_totals(spec, mesh(spec), {12: ((1, 2, 5),), 14: ((2, 3, 6),)}, fixed)


def test_density_units_and_ambiguous_force_semantics_are_rejected() -> None:
    raw = request().to_dict()
    raw["loads"][0]["unit"] = "N/mm"
    with pytest.raises(ValueError, match="TOTAL"):
        StaticLoadRequest.from_dict(raw)
    raw = request().to_dict()
    raw["loads"][0]["semantics"] = "DENSITY"
    with pytest.raises(ValueError, match="TOTAL"):
        StaticLoadRequest.from_dict(raw)


def test_oversized_json_integer_is_rejected_as_invalid_material() -> None:
    raw = request().to_dict()
    raw["material"]["youngs_modulus"]["value"] = 10**400
    with pytest.raises(ValueError, match="youngs_modulus must be finite"):
        StaticLoadRequest.from_dict(raw)


def test_compiler_rejects_changed_source_recipe_and_curve_selection(tmp_path: Path) -> None:
    spec = request()
    artifact = mesh(spec)
    profile = _profile()
    store = LocalBundleStore(tmp_path)
    curves: dict[int, tuple[tuple[int, int, int], ...]] = {12: ((1, 2, 5),), 14: ((2, 3, 6),)}
    with pytest.raises(ValueError, match="recipe"):
        compile_static(
            replace(spec, source_sha256="c" * 64),
            artifact,
            curves,
            profile,
            store,
            tmp_path / "FEBio.exe",
        )
    bad = replace(
        artifact,
        sets=tuple(
            replace(s, set_id="wrong-curve") if s.set_id == "loaded-curve-12" else s
            for s in artifact.sets
        ),
    )
    with pytest.raises(ValueError, match="curve source binding"):
        compile_static(spec, bad, curves, profile, store, tmp_path / "FEBio.exe")


def test_local_refinement_requires_length_and_binds_physical_region() -> None:
    raw = request().to_dict()
    raw["mesh"]["local_refinements"] = [
        {
            "region": {
                "schema_version": "1",
                "kind": "source_local_ball",
                "center": {
                    "schema_version": "1",
                    "frame": "World",
                    "x": {"value": 1, "unit": "mm"},
                    "y": {"value": 2, "unit": "mm"},
                    "z": {"value": 3, "unit": "mm"},
                },
                "radius": {"value": 0.5, "unit": "mm"},
            },
            "size": {"value": 0.2, "unit": "mm"},
        }
    ]
    refined = StaticLoadRequest.from_dict(raw)
    normalized = refined.to_dict()
    assert normalized["mesh"]["local_refinements"][0]["region"]["center"]["x"] == {
        "value": 0.001,
        "unit": "m",
    }
    assert StaticLoadRequest.from_dict(normalized).digest == refined.digest
    shifted = refined.to_dict()
    shifted["mesh"]["local_refinements"][0]["region"]["center"]["x"]["value"] = 0.002
    assert StaticLoadRequest.from_dict(shifted).digest != refined.digest
    raw["mesh"]["local_refinements"][0]["size"] = {"value": 1, "unit": "N"}
    with pytest.raises(ValueError, match="m-compatible"):
        StaticLoadRequest.from_dict(raw)


def test_status_diagnoses_abandoned_preparation_without_restarting(tmp_path: Path) -> None:
    store = StaticLoadStore(tmp_path)
    store.write_record(
        "preparation-status.json", {"status": "PREPARING", "request_digest": "a" * 64}
    )
    assert status(tmp_path)["status"] == "INTERRUPTED"
    assert store.read_record("preparation-status.json")["status"] == "PREPARING"
