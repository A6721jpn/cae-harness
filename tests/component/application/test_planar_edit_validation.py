"""Isolated synthetic record replay through the ordinary CLI, without native tools."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from test_persistence_authority import _created, _evidence, _populate_complete, _profile
from test_planar_adoption import _fixture

from febio_cae.adapters.geometry import (
    BackendBody,
    BackendFace,
    BackendInspection,
    StepGeometryMeshAdapter,
)
from febio_cae.application._demo import _RecordedInspection
from febio_cae.cli.main import main
from febio_cae.domain import GeometryInspectionRequest, Quantity
from febio_cae.domain.canonical import canonical_bytes
from febio_cae.domain.case_patch import CasePatch, CasePatchEdit
from febio_cae.domain.codec import encode_record
from febio_cae.storage.profiles import SQLiteCompatibilityRegistry


def prepared(tmp_path: Path) -> tuple[Any, ...]:
    service, created, storage = _created(tmp_path)
    record, original, carrier, revision = _fixture(tmp_path)
    frame = carrier.spec.geometry.placement.source_frame
    report = BackendInspection(
        created.source_asset.content_digest,
        carrier.spec.geometry.geometry_digest,
        ("mm",),
        frame,
        (BackendBody("part", True, 1e-6, (BackendFace("face", "part", frame, 1e-4, (0, 0, 0)),)),),
    )
    adapter = StepGeometryMeshAdapter(
        _RecordedInspection(report, original), source_asset=created.source_asset
    )
    service.geometry = adapter
    service._placed_selection = adapter.resolve_placed_selection
    service.compatibility = SQLiteCompatibilityRegistry(service.state.compatibility_path)
    for name in ("solver", "outputs", "quality"):
        service.register_profile(_profile(name))
    inspected = adapter.inspect(
        GeometryInspectionRequest(created.source_asset),
        storage.resolve_source(created.source_asset),
    )
    spec = replace(
        revision.spec,
        geometry=replace(revision.spec.geometry, inspection_digest=inspected.inspection_digest),
    )
    service.register_mesh_quality(created.case_id, record)
    _populate_complete(service, created, spec)
    frozen = service.freeze_case(created.case_id)
    assert frozen.status == "FROZEN", frozen.to_dict()
    parent = frozen.revision
    assert parent is not None
    adopted, receipt = service._adopt_planar_mesh(record, original, carrier, parent)
    payloads = {
        "gm03-mesh": encode_record(original),
        "gm03-carrier": encode_record(carrier),
        "gm03-backend-inspection": canonical_bytes(report.to_dict()),
        "adopted-mesh": encode_record(adopted),
        "adoption-receipt": canonical_bytes(receipt),
    }
    for name, payload in payloads.items():
        storage.ingest_source(
            asset_id=name,
            source_kind="registered_document",
            media_type="application/json",
            content=payload,
        )
    return service, created, storage, parent


def test_normal_cli_revalidates_registered_planar_material_edit(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    service, created, _, parent = prepared(tmp_path)
    monkeypatch.setenv("FEBIO_CAE_STATE_DIR", str(tmp_path / "state"))
    patch = CasePatch(
        parent.revision_id,
        parent.spec_digest,
        (
            CasePatchEdit(
                "material", replace(parent.spec.material, youngs_modulus=Quantity(2e6, "Pa")), True
            ),
        ),
        (_evidence("material.youngs_modulus"),),
    )
    service.apply_patch(created.case_id, patch)
    assert main(["case", "validate", created.case_id, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "VALIDATED"
    assert main(["case", "freeze", created.case_id, "--json"]) == 0
    child = service.get_revision(
        created.case_id, json.loads(capsys.readouterr().out)["revision_id"]
    )
    assert child.parent_revision_id == parent.revision_id
