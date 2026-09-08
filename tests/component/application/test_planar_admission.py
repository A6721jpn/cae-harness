"""Explicit synthetic planar admission is not approximation qualification."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from test_persistence_authority import _created, _evidence, _populate_complete, complete_spec

from febio_cae.domain import NumericalProfileRef
from febio_cae.storage import mesh_quality


def _record() -> Any:
    cls = getattr(mesh_quality, "PlanarDemoRegistration")
    return cls(
        profile_id="planar-demo",
        source_step_digest=complete_spec().geometry.source_step_digest,
        geometry_digest=complete_spec().geometry.geometry_digest,
        original_mesh_digest="1" * 64,
        original_recipe_digest="2" * 64,
        generation_profile=NumericalProfileRef("generation-only", "mesh_quality", "3" * 64),
        admission_evidence=(_evidence("mesh.admission"),),
    )


def test_planar_record_has_no_fabricated_approximation_limit() -> None:
    record = _record()
    assert record.approximation_status == "UNVERIFIED"
    assert "max_boundary_deviation" not in record.to_bytes().decode()
    restored = mesh_quality.decode_mesh_quality(record.to_bytes())
    assert restored == record
    assert restored.reference != record.generation_profile


def test_planar_admission_is_registered_with_real_evidence_bytes(tmp_path: Path) -> None:
    service, created, storage = _created(tmp_path)
    _populate_complete(service, created)
    record = _record()
    reference = service.register_mesh_quality(created.case_id, record)
    assert storage.resolve_mesh_quality(reference) == record
    with pytest.raises(ValueError):
        replace(record, admission_evidence=(_evidence("mesh_quality.qualification"),))


def test_planar_admission_rejects_curved_tool_and_foreign_source() -> None:
    record = _record()
    spec = complete_spec()
    record.check_spec(spec)
    with pytest.raises(ValueError):
        record.check_spec(replace(spec, geometry=replace(spec.geometry, geometry_digest="f" * 64)))
