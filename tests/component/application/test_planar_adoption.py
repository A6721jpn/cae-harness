"""Synthetic metadata adoption: topology/coordinates are never regenerated."""

from __future__ import annotations

import hashlib
from dataclasses import fields, replace
from pathlib import Path
from typing import Any

import pytest
from test_persistence_authority import _evidence, complete_spec
from test_registered_execution import _build

from febio_cae.application.service import RegisteredCaseService, _registered_selections
from febio_cae.domain import (
    CaseRevision,
    MeshElement,
    MeshNode,
    MeshQualityRecord,
    MeshSet,
    PartialCaseSpec,
    Quantity,
)
from febio_cae.storage.mesh_quality import PlanarDemoRegistration


def _fixture(tmp_path: Path) -> tuple[Any, ...]:
    spec = complete_spec()
    primitive = replace(
        spec.rigid_tool.primitive,
        kind="box",
        dimensions={k: Quantity(2, "mm") for k in ("length", "width", "height")},
        dimension_evidence={k: _evidence(f"rigid_tool.{k}") for k in ("length", "width", "height")},
    )
    spec = replace(spec, rigid_tool=replace(spec.rigid_tool, primitive=primitive))
    carrier = CaseRevision("case", "carrier", None, None, spec, (_evidence("case_revision.spec"),))
    mesh, _, _ = _build(carrier, tmp_path)
    values = PartialCaseSpec(
        **{f.name: getattr(spec, f.name) for f in fields(PartialCaseSpec) if f.init}
    )
    selections = {
        hashlib.sha256(s.to_bytes()).hexdigest(): s for s in _registered_selections(values)
    }
    mesh = replace(
        mesh,
        nodes=(
            *mesh.nodes,
            *(
                MeshNode(
                    n.node_id + 10,
                    (n.coordinates_si[0], n.coordinates_si[1], n.coordinates_si[2] + 2),
                )
                for n in mesh.nodes
            ),
        ),
        elements=(
            *mesh.elements,
            MeshElement(2, "tet10", tuple(range(11, 21)), spec.rigid_tool.primitive.body_id.value),
        ),
        provenance=replace(
            mesh.provenance,
            source_geometry_digest=spec.geometry.geometry_digest,
            source_body_ids=(spec.geometry.body_id.value, spec.rigid_tool.primitive.body_id.value),
            source_selection_digests=tuple(selections),
        ),
        sets=tuple(
            MeshSet(
                "set-" + digest[:12],
                "node",
                s.body_id.value,
                tuple(range(1, 11) if s.body_id == spec.geometry.body_id else range(11, 21)),
                digest,
            )
            for digest, s in selections.items()
        ),
        quality_records=tuple(
            MeshQualityRecord(k, 1, "1", None, "PASS", "synthetic")
            for k in (
                "tet10-positive-corner-volume",
                "tet10-backend-mapping",
                "part-tool-node-independence",
            )
        )
        + (
            MeshQualityRecord(
                "surface-approximation", 0, "m", None, "UNVERIFIED", "explicitly not qualified"
            ),
        ),
    )
    record = PlanarDemoRegistration(
        "admit",
        spec.geometry.source_step_digest,
        spec.geometry.geometry_digest,
        mesh.artifact_digest,
        mesh.provenance.mesh_recipe_digest,
        spec.mesh_policy.quality_profile,
        (_evidence("mesh.admission"),),
    )
    adopted_spec = replace(
        spec, mesh_policy=replace(spec.mesh_policy, quality_profile=record.reference)
    )
    revision = replace(carrier, revision_id="registered", spec=adopted_spec)
    return record, mesh, carrier, revision


def test_explicit_adoption_preserves_geometry_and_original_identity(tmp_path: Path) -> None:
    record, mesh, carrier, revision = _fixture(tmp_path)
    before = mesh.to_bytes()
    adopted, receipt = RegisteredCaseService._adopt_planar_mesh(record, mesh, carrier, revision)
    assert mesh.to_bytes() == before
    assert (
        adopted.nodes == mesh.nodes
        and adopted.elements == mesh.elements
        and adopted.faces == mesh.faces
    )
    assert adopted.quality_records == mesh.quality_records
    assert adopted.artifact_digest != mesh.artifact_digest
    assert receipt["original_mesh_digest"] == mesh.artifact_digest
    assert receipt["original_recipe_digest"] == mesh.provenance.mesh_recipe_digest
    assert receipt["adopted_mesh_digest"] == adopted.artifact_digest


@pytest.mark.parametrize("bad", ["pose", "artifact", "quality"])
def test_adoption_rejects_unapproved_changes(tmp_path: Path, bad: str) -> None:
    record, mesh, carrier, revision = _fixture(tmp_path)
    if bad == "artifact":
        record = replace(record, original_mesh_digest="f" * 64)
    elif bad == "quality":
        mesh = replace(mesh, quality_records=())
        record = replace(record, original_mesh_digest=mesh.artifact_digest)
    else:
        primitive = revision.spec.rigid_tool.primitive
        revision = replace(
            revision,
            spec=replace(
                revision.spec,
                rigid_tool=replace(
                    revision.spec.rigid_tool,
                    primitive=replace(
                        primitive, dimensions={**primitive.dimensions, "height": Quantity(3, "mm")}
                    ),
                ),
            ),
        )
    with pytest.raises(ValueError):
        RegisteredCaseService._adopt_planar_mesh(record, mesh, carrier, revision)


@pytest.mark.parametrize("bad", ["none", "candidate", "receipt", "registered-mesh"])
def test_execution_admission_recomputes_from_registered_sources(tmp_path: Path, bad: str) -> None:
    from test_persistence_authority import _created

    from febio_cae.domain.canonical import canonical_bytes
    from febio_cae.domain.codec import encode_record

    service, _, storage = _created(tmp_path)
    record, original, carrier, revision = _fixture(tmp_path)
    adopted, receipt = service._adopt_planar_mesh(record, original, carrier, revision)
    for name, payload in (
        ("gm03-mesh", encode_record(original)),
        ("gm03-carrier", encode_record(carrier)),
        ("adopted-mesh", encode_record(original if bad == "registered-mesh" else adopted)),
        ("adoption-receipt", canonical_bytes({} if bad == "receipt" else receipt)),
    ):
        storage.ingest_source(
            asset_id=name,
            source_kind="registered_document",
            media_type="application/json",
            content=payload,
        )
    candidate = original if bad == "candidate" else adopted
    if bad == "none":
        service._verify_execution_mesh(storage, record, revision, candidate)
    else:
        with pytest.raises(ValueError, match="adoption"):
            service._verify_execution_mesh(storage, record, revision, candidate)


def test_material_child_rebinds_mesh_without_overwriting_parent_sources(tmp_path: Path) -> None:
    from test_persistence_authority import _created

    from febio_cae.domain.canonical import canonical_bytes
    from febio_cae.domain.codec import encode_record

    service, created, storage = _created(tmp_path)
    record, original, carrier, parent = _fixture(tmp_path)
    parent = replace(parent, case_id=created.case_id)
    storage.register_revision(parent)
    adopted, receipt = service._adopt_planar_mesh(record, original, carrier, parent)
    payloads = {
        "gm03-mesh": encode_record(original),
        "gm03-carrier": encode_record(carrier),
        "adopted-mesh": encode_record(adopted),
        "adoption-receipt": canonical_bytes(receipt),
        "original-result": b"synthetic result placeholder; not native evidence",
        "original-preview": b"synthetic preview placeholder; not UI evidence",
    }
    for name, payload in payloads.items():
        storage.ingest_source(
            asset_id=name,
            source_kind="registered_document",
            media_type="application/octet-stream",
            content=payload,
        )
    child = replace(
        parent,
        revision_id="material-child",
        parent_revision_id=parent.revision_id,
        parent_spec_digest=parent.spec_digest,
        spec=replace(
            parent.spec, material=replace(parent.spec.material, youngs_modulus=Quantity(2e6, "Pa"))
        ),
    )
    expected, _ = service._adopt_planar_mesh(record, original, carrier, child)
    try:
        service._verify_execution_mesh(storage, record, child, expected)
    except ValueError as error:
        pytest.fail(f"material-only child must rebind original mesh: {error}")
    assert expected.nodes == adopted.nodes and expected.elements == adopted.elements
    assert expected.artifact_digest != adopted.artifact_digest
    assert expected.quality_records == adopted.quality_records
    for name, payload in payloads.items():
        assert storage.resolve_source(storage.source_asset(name)).content == payload
    with pytest.raises(ValueError):
        service._verify_execution_mesh(storage, record, child, adopted)
    stale = replace(child, parent_spec_digest="0" * 64)
    with pytest.raises(ValueError):
        service._verify_execution_mesh(storage, record, stale, expected)
    moved = replace(
        child,
        spec=replace(
            child.spec, mesh_policy=replace(child.spec.mesh_policy, global_size=Quantity(3, "mm"))
        ),
    )
    with pytest.raises(ValueError):
        service._verify_execution_mesh(storage, record, moved, expected)
