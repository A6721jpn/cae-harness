"""Explicit synthetic criteria registration; no native qualification is implied."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from test_persistence_authority import (
    SyntheticGeometry,
    _created,
    _evidence,
    _populate_complete,
    complete_spec,
)

from febio_cae.domain import GeometrySelectionRequest, Quantity, Translation3
from febio_cae.domain.codec import decode_record, encode_record
from febio_cae.domain.ports import PortError
from febio_cae.storage.registry import CaseStorage


def criteria(**changes: Any) -> Any:
    from febio_cae.storage.mesh_quality import MeshQualityRegistration

    record = MeshQualityRegistration(
        "mesh", Quantity(0.01, "mm"), ("sphere", "cylinder"), "synthetic", "1",
        "synthetic", (_evidence("mesh_quality.qualification"),),
    )
    return replace(record, **changes)


def test_compatibility_record_is_not_mesh_quality_authority(tmp_path: Path) -> None:
    service, created, _ = _created(tmp_path)
    _populate_complete(service, created)
    # Explicitly restore the legacy compatibility-only ref even after fixture migration.
    from test_persistence_authority import _profile_digest
    from febio_cae.domain import NumericalProfileRef
    draft = service.inspect_case(created.case_id).draft
    assert draft is not None and draft.values.mesh_policy is not None
    service.set_spec(
        created.case_id,
        values=replace(draft.values, mesh_policy=replace(draft.values.mesh_policy,
            quality_profile=NumericalProfileRef("mesh", "mesh_quality", _profile_digest("mesh")))),
        expected_generation=draft.generation,
    )
    result = service.validate_case(created.case_id)
    assert result.status != "VALIDATED"
    assert any(d.field == "mesh_policy.quality_profile" for d in result.diagnostics)


def test_service_dispatches_full_authoritative_intent_only_to_matching_part(tmp_path: Path) -> None:
    received: list[Any] = []
    class Receiver(SyntheticGeometry):
        def resolve_selection(self, request: Any, source: Any) -> Any:
            received.append(request)
            return super().resolve_selection(request, source)
    service, created, _ = _created(tmp_path, geometry=Receiver())
    spec = complete_spec()
    placement = replace(spec.geometry.placement, translation=Translation3(
        spec.geometry.placement.target_frame, Quantity(3, "mm"), Quantity(4, "mm"), Quantity(5, "mm")))
    spec = replace(spec, geometry=replace(spec.geometry, placement=placement))
    _populate_complete(service, created, spec)
    service.validate_case(created.case_id)
    assert received
    matched = [r for r in received if r.selection.body_id == spec.geometry.body_id]
    assert matched and all(getattr(r, "geometry_intent", None) == spec.geometry for r in matched)
    assert all(getattr(r, "geometry_intent", None) is None for r in received if r not in matched)


def test_optional_selection_context_strict_roundtrip_and_legacy_bytes(tmp_path: Path) -> None:
    _, created, _ = _created(tmp_path)
    spec = complete_spec()
    legacy = GeometrySelectionRequest(created.source_asset, spec.contact.part_surface)
    encoded = encode_record(legacy)
    assert "geometry_intent" not in json.loads(encoded)
    assert encode_record(replace(legacy, geometry_intent=None)) == encoded
    placed = replace(legacy, geometry_intent=spec.geometry)
    assert decode_record(encode_record(placed), GeometrySelectionRequest) == placed
    assert decode_record(encoded, GeometrySelectionRequest) == legacy
    bad = placed.to_dict() | {"placement_override": {}}
    with pytest.raises(ValueError):
        decode_record(json.dumps(bad).encode(), GeometrySelectionRequest)
    with pytest.raises(ValueError):
        replace(placed, geometry_intent="not an intent")


def test_registered_criteria_reopen_freeze_and_snapshot_tamper(tmp_path: Path) -> None:
    service, created, storage = _created(tmp_path)
    record = criteria()
    ref = service.register_mesh_quality(created.case_id, record)
    assert ref.record_digest == hashlib.sha256(record.to_bytes()).hexdigest()
    assert service.register_mesh_quality(created.case_id, record) == ref
    assert CaseStorage(created.case_root).resolve_mesh_quality(ref) == record
    spec = complete_spec()
    _populate_complete(service, created, replace(spec, mesh_policy=replace(spec.mesh_policy, quality_profile=ref)))
    frozen = service.freeze_case(created.case_id).revision
    assert frozen is not None
    reopened = CaseStorage(created.case_root)
    assert reopened.resolve_revision_mesh_quality(frozen) == record
    with sqlite3.connect(storage.registry_path) as connection:
        connection.execute("UPDATE revision_mesh_quality SET evidence_payload=?", (b"[]",))
    with pytest.raises(PortError):
        reopened.resolve_revision_mesh_quality(frozen)


@pytest.mark.parametrize("failure", ["foreign", "stale", "unqualified", "evidence", "payload"])
def test_criteria_registration_and_resolution_reject_unbound_data(tmp_path: Path, failure: str) -> None:
    service, created, storage = _created(tmp_path)
    record = criteria()
    if failure == "unqualified":
        with pytest.raises((ValueError, PortError)):
            service.register_mesh_quality(created.case_id, replace(record, evidence_scope="native"))
        return
    if failure == "evidence":
        with pytest.raises(PortError):
            service.register_mesh_quality(created.case_id, replace(record,
                qualification_evidence=(replace(record.qualification_evidence[0], reference="missing"),)))
        return
    ref = service.register_mesh_quality(created.case_id, record)
    if failure == "payload":
        with sqlite3.connect(storage.registry_path) as connection:
            connection.execute("UPDATE mesh_quality SET payload=?", (b"{}",))
    elif failure == "foreign":
        ref = replace(ref, profile_id="foreign")
    else:
        ref = replace(ref, record_digest="0" * 64)
    with pytest.raises(PortError):
        storage.resolve_mesh_quality(ref)


def test_no_default_or_nonpositive_criteria() -> None:
    with pytest.raises(ValueError):
        criteria(max_boundary_deviation=Quantity(0, "mm"))
