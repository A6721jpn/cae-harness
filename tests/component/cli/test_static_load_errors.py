"""Public static CLI error boundaries over isolated, synthetic durable records.

Seeded publication records exercise status attestation, not native CAD or solver
success. No external native software is launched by these tests.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from febio_cae.adapters.febio.compiler import LocalBundleStore
from febio_cae.adapters.febio.static_load import compile_static
from febio_cae.application.static_load import _profile, _summary
from febio_cae.domain import AttemptRecord, FileEntry, FrameId, ProcessIdentity, RunState
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
from febio_cae.domain.results import (
    NumericResultData,
    OutputObservation,
    ReadResult,
    ReadStatus,
    ResultDataRef,
    ResultManifest,
)
from febio_cae.domain.static_load import EdgeTotalForce, StaticLoadRequest, integrate_edge_totals
from febio_cae.storage.static_load import StaticLoadStore


def _cli(root: Path, action: str) -> tuple[int, dict[str, Any]]:
    repo = Path(__file__).resolve().parents[3]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(repo / "src")
    environment.pop("PYTHONHOME", None)
    command = [
        sys.executable,
        "-m",
        "febio_cae",
        "static-load",
        action,
        "--root",
        str(root),
        "--json",
    ]
    if action == "run":
        command.extend(["--solver", str(root.parent / "absent-FEBio.exe")])
    completed = subprocess.run(
        command,
        cwd=root.parent,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert not completed.stderr, completed.stderr
    return completed.returncode, json.loads(completed.stdout)


def _preserved(root: Path) -> dict[str, bytes | None]:
    return {
        str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
        for path in root.rglob("*")
        if path.name != ".publication.lock"
    }


def _prepared(
    root: Path,
) -> tuple[
    StaticLoadStore, StaticLoadRequest, MeshArtifact, dict[int, tuple[tuple[int, int, int], ...]]
]:
    source = b"synthetic source snapshot; not STEP runtime evidence"
    request = StaticLoadRequest(
        source_sha256=hashlib.sha256(source).hexdigest(),
        fixed_face_ids=(1,),
        loads=(EdgeTotalForce(12, (0, 0, -10)), EdgeTotalForce(14, (0, 0, -10))),
        youngs_modulus_pa=68e9,
        poisson_ratio=0.33,
        global_size_m=0.001,
        native_coordinate_unit="MM",
        algorithm_2d=6,
        algorithm_3d=1,
        curvature_points=20,
        cpu_workers=1,
        mesh_wall_seconds=600.0,
        solver_wall_seconds=600.0,
        memory_bytes=100000000,
        max_nodes=1000,
        max_elements=1000,
    )
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
        MeshSet(
            "fixed-face-1", "node", "body-1", (4, 8, 9, 10), request.selection_digest("face", 1)
        ),
        MeshSet(
            "loaded-curve-12", "node", "body-1", (1, 2, 5), request.selection_digest("curve", 12)
        ),
        MeshSet(
            "loaded-curve-14", "node", "body-1", (2, 3, 6), request.selection_digest("curve", 14)
        ),
    )
    provenance = MeshProvenance(
        "b" * 64,
        ("body-1",),
        tuple(item.source_selection_digest for item in sets),
        request.digest,
        "gmsh-occ",
        "4.15.2",
        "backend-tet10-to-domain-tet10-v1",
        TET10_NODE_ORDER_ID,
        TET10_FACE_ORDER_ID,
    )
    mesh = MeshArtifact(
        "synthetic-static-mesh",
        FrameId("World"),
        provenance,
        tuple(MeshNode(index + 1, point) for index, point in enumerate(points)),
        (element,),
        tuple(
            MeshFace(
                f"face-{index}",
                "body-1",
                tuple(element.node_ids[position] for position in positions),
                (1,),
                (index,),
            )
            for index, positions in enumerate(TET10_FACE_NODE_POSITIONS)
        ),
        sets,
        (),
    )
    curves: dict[int, tuple[tuple[int, int, int], ...]] = {12: ((1, 2, 5),), 14: ((2, 3, 6),)}
    forces = integrate_edge_totals(request, mesh, curves, {4, 8, 9, 10})
    store = StaticLoadStore(root)
    store.write_bytes("source.step", source)
    store.write_record("request.json", request.to_dict())
    store.write_record("mesh.json", mesh.to_dict())
    store.write_record(
        "preparation.json",
        {
            "source_sha256": request.source_sha256,
            "request_digest": request.digest,
            "mesh": mesh.to_dict(),
            "inspection_geometry_digest": provenance.source_geometry_digest,
            "curves": {str(tag): [list(line) for line in lines] for tag, lines in curves.items()},
            "nodal_forces_n": {str(node): list(force) for node, force in sorted(forces.items())},
            "cad_approximation_status": "UNVERIFIED",
            "cad_approximation_reason": "No certified arbitrary-CAD approximation bound",
        },
    )
    store.write_record(
        "preparation-status.json", {"status": "PREPARED", "request_digest": request.digest}
    )
    return store, request, mesh, curves


def _publication(root: Path) -> StaticLoadStore:
    store, request, mesh, curves = _prepared(root)
    profile = _profile()
    bundle = compile_static(
        request, mesh, curves, profile, LocalBundleStore(root / "bundles"), Path(sys.executable)
    )
    owner = store.issue(bundle)
    attempt_cwd = (
        root / "attempts" / owner.case_id / owner.run_id / owner.attempt_id / "0"
    ).resolve()
    attempt = AttemptRecord(
        owner.attempt_id,
        owner.run_id,
        owner.case_id,
        bundle.revision_id,
        owner.owner_generation,
        bundle.bundle_digest,
        RunState.SUCCEEDED,
        ProcessIdentity(
            bundle.argv[0],
            bundle.tool.executable_digest,
            bundle.argv,
            str(attempt_cwd),
            bundle.thread_count,
            "synthetic-seeded-publication",
        ),
        bundle.settings,
    )
    run_record = store.read_record("run.json")
    store.write_record(
        "run.json",
        {**run_record, "status": "SUCCEEDED", "claimed": True, "attempt": attempt.to_dict()},
    )
    store.write_bytes("profile.json", profile.to_bytes())
    raw = b"synthetic sealed result identity; not native XPLT runtime evidence"
    log = b"synthetic sealed terminal identity; not native solver runtime evidence"
    store.write_bytes("sealed-results.xplt", raw)
    store.write_bytes("sealed-solver.log", log)
    data: dict[str, NumericResultData] = {}
    observations = []
    entities: tuple[str, ...]
    components: tuple[str, ...]
    values: tuple[float, ...]
    for mapping in profile.output_mappings:
        if mapping.canonical_id == "stress":
            entities = ("1",)
            components = ("xx", "yy", "zz", "xy", "yz", "xz")
            values = (6.0, 2.0, 1.0, 3.0, 4.0, 5.0)
        else:
            entities = tuple(str(node.node_id) for node in mesh.nodes)
            components = ("x", "y", "z")
            vectors = (
                {1: (0.0, 0.0, -0.001)}
                if mapping.canonical_id == "displacement"
                else {
                    4: (0.0, 0.0, -10.0),
                    9: (0.0, 0.0, 20.0),
                    10: (0.0, 0.0, 10.0),
                }
            )
            values = tuple(
                value for node in mesh.nodes for value in vectors.get(node.node_id, (0.0, 0.0, 0.0))
            )
        numeric = NumericResultData(
            ResultDataRef(
                f"synthetic-{mapping.canonical_id}",
                "0" * 64,
                "numeric-result-v1",
                f"numeric-{mapping.canonical_id}.json",
                bundle.bundle_digest,
                owner.attempt_id,
            ),
            mapping,
            "time",
            "s",
            (1.0,),
            entities,
            components,
            (values,),
        )
        numeric = replace(
            numeric,
            reference=replace(numeric.reference, content_digest=numeric.expected_content_digest),
        )
        data[mapping.canonical_id] = numeric
        store.write_bytes(f"numeric-{mapping.canonical_id}.json", numeric.to_bytes())
        observations.append(
            OutputObservation(
                mapping.canonical_id,
                mapping.location,
                mapping.value_type,
                mapping.unit,
                mapping.frame,
                mapping.measure_id,
                1,
                numeric.reference,
            )
        )
    manifest = ResultManifest(
        "synthetic-static-manifest",
        owner.attempt_id,
        bundle.bundle_digest,
        (FileEntry("output/results.xplt", hashlib.sha256(raw).hexdigest(), len(raw), "result"),),
        ReadResult(ReadStatus.VALIDATED, profile.reader, observations, ()),
    )
    store.write_bytes("manifest.json", manifest.to_bytes())
    summary = _summary(request, mesh, curves, data)
    store.write_record(
        "summary.json",
        {
            **summary,
            "terminal_log_sha256": hashlib.sha256(log).hexdigest(),
            "xplt_sha256": hashlib.sha256(raw).hexdigest(),
            "xplt_path": str(root / "sealed-results.xplt"),
        },
    )
    return store


def _failed(code: int, result: dict[str, Any], expected: int) -> None:
    assert code == expected, result
    assert result["schema_version"] == "1" and result["status"] == "FAILED"
    assert isinstance(result["error"], str) and result["error"]


def test_run_occupied_live_lease_is_conflict_before_any_preparation_read(tmp_path: Path) -> None:
    root = tmp_path / "static"
    store = StaticLoadStore(root)
    before = _preserved(root)
    with store.operation() as owned:
        assert owned
        _failed(*_cli(root, "run"), 8)
    assert _preserved(root) == before


def test_reused_run_root_is_conflict_before_changed_profile_or_bundle_publication(
    tmp_path: Path,
) -> None:
    root = tmp_path / "static"
    store, _, _, _ = _prepared(root)
    store.write_record("run.json", {"status": "FAILED"})
    store.write_bytes("profile.json", b"historical different immutable profile")
    store.write_bytes("bundle.json", b"historical immutable bundle")
    before = _preserved(root)
    _failed(*_cli(root, "run"), 8)
    assert _preserved(root) == before


def test_status_missing_root_is_invalid_input_without_creating_directories(tmp_path: Path) -> None:
    root = tmp_path / "mistyped"
    _failed(*_cli(root, "status"), 2)
    assert not root.exists()


@pytest.mark.parametrize(
    "content",
    [
        b"{",
        b"[]",
        b'{"schema_version":"1"}',
        b'{"schema_version":"1","status":"PREPARED","status":"FAILED"}',
        b'{"schema_version":"1","status":"PREPARED","bad":NaN}',
    ],
)
def test_status_malformed_stored_record_is_integrity_json(tmp_path: Path, content: bytes) -> None:
    root = tmp_path / "static"
    store = StaticLoadStore(root)
    store.write_bytes("preparation-status.json", content)
    before = _preserved(root)
    _failed(*_cli(root, "status"), 6)
    assert _preserved(root) == before


def test_status_numerical_stress_is_explicitly_element_average_and_unverified(
    tmp_path: Path,
) -> None:
    root = tmp_path / "static"
    _publication(root)
    code, result = _cli(root, "status")
    assert code == 0, result
    assert result["run"]["status"] == "SUCCEEDED"
    summary = result["summary"]
    assert summary["maximum_von_mises_pa"] == pytest.approx(math.sqrt(171.0))
    assert summary["maximum_displacement_m"] == pytest.approx(0.001)
    assert summary["applied_force_n"] == pytest.approx([0.0, 0.0, -20.0])
    assert summary["stress_basis"] == "element_average_cauchy"
    assert "peak stress recovery" in summary["unverified"]
    assert "material yield/safety" in summary["unverified"]
    assert summary["quality_status"] == "UNVERIFIED"


@pytest.mark.parametrize(
    "missing",
    [
        "manifest.json",
        "numeric-stress.json",
        "summary.json",
        "sealed-results.xplt",
        "sealed-solver.log",
    ],
)
def test_status_missing_successful_publication_evidence_is_integrity_json(
    tmp_path: Path, missing: str
) -> None:
    root = tmp_path / "static"
    _publication(root)
    (root / missing).unlink()
    before = _preserved(root)
    _failed(*_cli(root, "status"), 6)
    assert _preserved(root) == before


@pytest.mark.parametrize("record,key", [("run.json", "owner"), ("summary.json", "stress_basis")])
def test_status_missing_successful_publication_key_is_integrity_json(
    tmp_path: Path, record: str, key: str
) -> None:
    root = tmp_path / "static"
    store = _publication(root)
    value = store.read_record(record)
    del value[key]
    store.write_record(record, value)
    before = _preserved(root)
    _failed(*_cli(root, "status"), 6)
    assert _preserved(root) == before


def test_status_inaccessible_record_is_environment_json(tmp_path: Path) -> None:
    root = tmp_path / "static"
    StaticLoadStore(root)
    (root / "preparation-status.json").mkdir()
    before = _preserved(root)
    _failed(*_cli(root, "status"), 4)
    assert _preserved(root) == before
