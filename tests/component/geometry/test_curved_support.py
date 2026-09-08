from __future__ import annotations

import importlib
import inspect
import json
import math
from collections import Counter
from dataclasses import replace
from itertools import combinations
from typing import Any

import pytest

from febio_cae.adapters.geometry import StepGeometryMeshAdapter
from febio_cae.domain import PortError, PortErrorCategory, Quantity

from .conftest import SyntheticSourceResolver, _evidence


def _case(revision: Any, kind: str, *, size: float = 0.005, refinements: int = 5) -> Any:
    primitive = revision.spec.rigid_tool.primitive
    dims = {"radius": Quantity(0.002, "m")}
    if kind == "cylinder":
        dims["height"] = Quantity(0.004, "m")
    primitive = replace(
        primitive,
        kind=kind,
        dimensions=dims,
        dimension_evidence={n: _evidence(f"rigid_tool.{n}", n) for n in dims},
    )
    return replace(
        revision,
        spec=replace(
            revision.spec,
            rigid_tool=replace(revision.spec.rigid_tool, primitive=primitive),
            mesh_policy=replace(
                revision.spec.mesh_policy,
                global_size=Quantity(size, "m"),
                max_refinements=refinements,
            ),
        ),
    )


def _adapter(backend: Any, source: Any, revision: Any, error: float, bad: str = "") -> Any:
    kwargs: dict[str, Any] = {
        "source_resolver": SyntheticSourceResolver(source),
        "source_asset": source.source_asset,
    }
    # Keep pre-implementation RED behavioral: exercise the existing curved refusal,
    # not a missing import or an unknown constructor keyword.
    if "resolve_mesh_quality" in inspect.signature(StepGeometryMeshAdapter).parameters:
        module = importlib.import_module("febio_cae.adapters.meshing.approximation")
        ref = revision.spec.mesh_policy.quality_profile
        if bad == "foreign":
            ref = replace(ref, profile_id="foreign")
        if bad == "stale":
            ref = replace(ref, record_digest="e" * 64)
        criteria = module.ApproximationCriteria(
            profile=ref,
            max_boundary_deviation=Quantity(error, "m"),
            supported_kinds=("sphere", "cylinder"),
            algorithm_id="radial-polyhedron-affine-tet10-v1" if bad != "unqualified" else "unknown",
            evidence_scope="synthetic-component-only",
            max_elements=20000 if bad != "elements" else 1,
        )
        kwargs["resolve_mesh_quality"] = lambda requested: None if bad == "missing" else criteria
    return StepGeometryMeshAdapter(backend, **kwargs)


@pytest.mark.parametrize("kind", ["sphere", "cylinder"])
def test_supported_curved_mesh_has_bounded_closed_contact_boundary(
    synthetic_backend: Any,
    source_content: Any,
    synthetic_case_revision: Any,
    kind: str,
) -> None:
    revision = _case(synthetic_case_revision, kind)
    artifacts = []
    for allowance in (0.0007, 0.0001):
        artifact = _adapter(synthetic_backend, source_content, revision, allowance).mesh(revision)
        artifacts.append(artifact)
        record = next(
            q
            for q in artifact.quality_records
            if q.metric_id == "tool-boundary-deviation-upper-bound"
        )
        assert record.status == "PASS"
        assert 0 < record.value <= allowance
        assert json.loads(record.reason)["criteria"]["evidence_scope"] == "synthetic-component-only"
        nodes = {n.node_id: n.coordinates_si for n in artifact.nodes}
        faces = [
            f
            for f in artifact.faces
            if f.body_id == "tool-body" and len(f.adjacent_element_ids) == 1
        ]
        edges: Counter[tuple[int, int]] = Counter()
        sampled_error = 0.0
        for face in faces:
            a, b, c = face.node_ids[:3]
            edges.update(((a, b), (b, c), (c, a)))
            # Independent dense barycentric points, including curved-side midsides
            # and cap/contact-facing facets. Sampling verifies, it is not the bound.
            for i in range(7):
                for j in range(7 - i):
                    p = tuple(
                        (i * nodes[a][k] + j * nodes[b][k] + (6 - i - j) * nodes[c][k]) / 6
                        for k in range(3)
                    )
                    x, y, z = p[0], p[1], p[2] - 0.02
                    if kind == "sphere":
                        distance = abs(math.sqrt(x * x + y * y + z * z) - 0.002)
                    else:
                        distance = min(abs(math.hypot(x, y) - 0.002), abs(abs(z) - 0.002))
                    sampled_error = max(sampled_error, distance)
        assert sampled_error <= record.value + 1e-15
        triangles = [
            tuple(
                tuple(nodes[i][j] - (0.02 if j == 2 else 0) for j in range(3))
                for i in f.node_ids[:3]
            )
            for f in faces
        ]
        # Independently intersect rays from the center toward exact analytic
        # surface points, checking the reverse direction on side AND end caps.
        for index in range(24):
            angle = 2 * math.pi * (index + 0.37) / 24
            for axial in (-1.0, -0.7, 0.0, 0.4, 1.0):
                r = 0.002 * (math.sqrt(1 - axial * axial) if kind == "sphere" else 1)
                target = (r * math.cos(angle), r * math.sin(angle), 0.002 * axial)
                hits = [_ray_fraction(target, triangle) for triangle in triangles]
                hit = next((t for t in hits if t is not None), None)
                assert hit is not None, "closed radial boundary has a hole"
                assert abs(1 - hit) * math.sqrt(sum(x * x for x in target)) <= record.value + 1e-14
        assert edges and all(count == 1 and edges[v, u] == 1 for (u, v), count in edges.items())
        tool_faces = {f.face_id for f in faces}
        assert all(
            set(s.member_ids) == tool_faces for s in artifact.sets if s.body_id == "tool-body"
        )
        for element in artifact.elements:
            if element.body_id != "tool-body":
                continue
            corners = element.node_ids[:4]
            assert (
                max(math.dist(nodes[a], nodes[b]) for a, b in combinations(corners, 2))
                <= 0.005 + 1e-15
            )
            for mid, (a, b) in zip(
                element.node_ids[4:], ((0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3)), strict=True
            ):
                assert nodes[mid] == pytest.approx(
                    tuple((nodes[corners[a]][k] + nodes[corners[b]][k]) / 2 for k in range(3))
                )
    assert len(artifacts[1].elements) > len(artifacts[0].elements)
    assert artifacts[1].provenance.mesh_recipe_digest != artifacts[0].provenance.mesh_recipe_digest


@pytest.mark.parametrize("bad", ["foreign", "stale", "unqualified", "missing", "elements"])
def test_curved_provider_rejects_bad_criteria(
    synthetic_backend: Any,
    source_content: Any,
    synthetic_case_revision: Any,
    bad: str,
) -> None:
    revision = _case(synthetic_case_revision, "sphere")
    with pytest.raises(PortError, match="criteria|element budget") as error:
        _adapter(synthetic_backend, source_content, revision, 0.0001, bad).mesh(revision)
    assert error.value.category in {
        PortErrorCategory.INTEGRITY,
        PortErrorCategory.QUALITY,
        PortErrorCategory.UNSUPPORTED_CAPABILITY,
    }


@pytest.mark.parametrize("kind", ["sphere", "cylinder"])
def test_curved_refinement_budget_exhaustion(
    synthetic_backend: Any,
    source_content: Any,
    synthetic_case_revision: Any,
    kind: str,
) -> None:
    revision = _case(synthetic_case_revision, kind, refinements=0)
    with pytest.raises(PortError, match="refinement budget") as error:
        _adapter(synthetic_backend, source_content, revision, 1e-8).mesh(revision)
    assert error.value.category == PortErrorCategory.QUALITY


def test_global_size_refines_volume_not_only_surface(
    synthetic_backend: Any,
    source_content: Any,
    synthetic_case_revision: Any,
) -> None:
    counts = []
    for size in (0.005, 0.0015):
        revision = _case(synthetic_case_revision, "cylinder", size=size)
        artifact = _adapter(synthetic_backend, source_content, revision, 0.0002).mesh(revision)
        nodes = {n.node_id: n.coordinates_si for n in artifact.nodes}
        elements = [e for e in artifact.elements if e.body_id == "tool-body"]
        counts.append(len(elements))
        assert all(
            math.dist(nodes[a], nodes[b]) <= size + 1e-15
            for e in elements
            for a, b in combinations(e.node_ids[:4], 2)
        )
    assert counts[1] > counts[0]


def _ray_fraction(target: Any, triangle: Any) -> float | None:
    a, b, c = triangle

    def sub(u: Any, v: Any) -> tuple[float, ...]:
        return tuple(u[j] - v[j] for j in range(3))

    def cross(u: Any, v: Any) -> tuple[float, ...]:
        return (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])

    def dot(u: Any, v: Any) -> float:
        return sum(u[j] * v[j] for j in range(3))

    e, f = sub(b, a), sub(c, a)
    p = cross(target, f)
    det = dot(e, p)
    if abs(det) < 1e-25:
        return None
    s = tuple(-x for x in a)
    u = dot(s, p) / det
    q = cross(s, e)
    v = dot(target, q) / det
    t = dot(f, q) / det
    return t if u >= -1e-10 and v >= -1e-10 and u + v <= 1 + 1e-10 and t > 0 else None


def test_elapsed_budget_not_reset_after_provider(
    synthetic_backend: Any,
    source_content: Any,
    synthetic_case_revision: Any,
    monkeypatch: Any,
) -> None:
    revision = _case(synthetic_case_revision, "sphere")
    adapter = _adapter(synthetic_backend, source_content, revision, 0.0002)
    provider = adapter._resolve_mesh_quality

    def delayed(ref: Any) -> Any:
        criteria = provider(ref)
        monkeypatch.setattr("time.monotonic", lambda: 1e20)
        return criteria

    adapter._resolve_mesh_quality = delayed
    with pytest.raises(PortError, match="elapsed budget"):
        adapter.mesh(revision)
