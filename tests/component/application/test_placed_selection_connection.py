"""Synthetic application wiring; the concrete C bridge is tested separately."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from test_persistence_authority import (
    SelectionFailGeometry,
    SyntheticGeometry,
    SyntheticProfiles,
    _created,
    _populate_complete,
)

from febio_cae.application.service import RegisteredCaseService
from febio_cae.domain import BodyId, GeometrySelectionRequest
from febio_cae.storage.registry import StorageIntegrityError


@pytest.mark.parametrize("foreign_result", [False, True])
def test_registered_draft_scopes_placed_selection_dependency(
    tmp_path: Path, foreign_result: bool
) -> None:
    service, created, _ = _created(tmp_path)
    _populate_complete(service, created)
    draft = service.current_draft(created.case_id)
    calls: list[tuple[Any, ...]] = []

    def resolve(source: Any, geometry: Any, tool: Any, selection: Any) -> Any:
        calls.append((source, geometry, tool, selection))
        result = SyntheticGeometry().resolve_selection(
            GeometrySelectionRequest(source.source_asset, selection), source
        )
        return replace(result, body_id=BodyId("foreign")) if foreign_result else result

    connected = RegisteredCaseService(
        state_dir=tmp_path / "state",
        geometry=SelectionFailGeometry(),
        compatibility=SyntheticProfiles(),
        placed_selection=resolve,
    )
    result = connected.validate_case(created.case_id)
    assert calls
    assert {call[3].body_id.value for call in calls} == {"part", "tool"}
    for source, geometry, tool, _ in calls:
        assert source.source_asset == created.source_asset
        assert (
            source.content
            == service.resolve_source(created.case_id, created.source_asset.asset_id).content
        )
        assert geometry == draft.values.geometry
        assert tool == draft.values.rigid_tool
    if foreign_result:
        assert result.status != "VALIDATED"
        assert any("selection resolution" in item.message for item in result.diagnostics)
    else:
        assert result.status == "VALIDATED"


def test_tampered_registered_source_never_reaches_placed_dependency(tmp_path: Path) -> None:
    service, created, storage = _created(tmp_path)
    _populate_complete(service, created)
    calls: list[tuple[Any, ...]] = []

    def resolve(*args: Any) -> Any:
        calls.append(args)
        raise AssertionError("tampered evidence reached adapter")

    connected = RegisteredCaseService(
        state_dir=tmp_path / "state",
        geometry=SelectionFailGeometry(),
        compatibility=SyntheticProfiles(),
        placed_selection=resolve,
    )
    # Tamper the registered copy; the original CAD is deliberately not a live dependency.
    registered = (
        storage.root / f"cases/{created.case_id}/sources/{created.source_asset.asset_id}.bin"
    )
    registered.write_bytes(b"changed source")
    with pytest.raises(StorageIntegrityError, match="source bytes"):
        connected.validate_case(created.case_id)
    assert calls == []
