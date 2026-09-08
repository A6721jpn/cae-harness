from dataclasses import replace
from typing import Any

import pytest

from febio_cae.domain import (
    BodyId,
    CaseRevision,
    CoordinatePredicate,
    CoordinatePredicateRule,
    PortError,
    Quantity,
    SourceAssetContent,
    Translation3,
    UnitDirection,
    WholeBodyRule,
)


def test_resolves_explicitly_placed_part(
    adapter: Any, source_content: SourceAssetContent, synthetic_case_revision: CaseRevision
) -> None:
    spec = synthetic_case_revision.spec
    frame = spec.geometry.placement.target_frame
    geometry = replace(
        spec.geometry,
        placement=replace(
            spec.geometry.placement,
            translation=Translation3(frame, Quantity(0, "m"), Quantity(0, "m"), Quantity(0.1, "m")),
        ),
    )
    selection = replace(
        spec.contact.part_surface,
        rule=CoordinatePredicateRule(
            geometry.body_id,
            frame,
            (CoordinatePredicate(UnitDirection(frame, 0.0, 0.0, 1.0), "eq", Quantity(0.11, "m")),),
        ),
    )
    result = adapter.resolve_placed_selection(source_content, geometry, spec.rigid_tool, selection)
    assert [face.face_id.value for face in result.faces] == ["top-face"]


def test_resolves_generated_tool(
    adapter: Any, source_content: SourceAssetContent, synthetic_case_revision: CaseRevision
) -> None:
    spec = synthetic_case_revision.spec
    selection = spec.rigid_tool.contact_surface
    result = adapter.resolve_placed_selection(
        source_content, spec.geometry, spec.rigid_tool, selection
    )
    assert result.faces
    assert result.body_id == spec.rigid_tool.primitive.body_id


@pytest.mark.parametrize(
    "corruption", ["source", "inspection", "geometry", "unit", "foreign", "stale_tool", "overlap"]
)
def test_rejects_stale_or_foreign_identity(
    adapter: Any,
    source_content: SourceAssetContent,
    synthetic_case_revision: CaseRevision,
    corruption: str,
) -> None:
    spec = synthetic_case_revision.spec
    geometry, tool, selection = spec.geometry, spec.rigid_tool, spec.rigid_tool.contact_surface
    if corruption in {"source", "inspection", "geometry"}:
        field = {
            "source": "source_step_digest",
            "inspection": "inspection_digest",
            "geometry": "geometry_digest",
        }[corruption]
        geometry = replace(geometry, **{field: "9" * 64})
    elif corruption == "unit":
        geometry = replace(geometry, step_unit="m")
    elif corruption == "foreign":
        selection = replace(
            selection, body_id=BodyId("foreign"), rule=WholeBodyRule(BodyId("foreign"))
        )
    elif corruption == "stale_tool":
        selection = replace(selection, geometry_digest="9" * 64)
    else:
        contact_surface = replace(
            tool.contact_surface, body_id=geometry.body_id, rule=WholeBodyRule(geometry.body_id)
        )
        tool = replace(
            tool,
            primitive=replace(tool.primitive, body_id=geometry.body_id),
            contact_surface=contact_surface,
        )
    with pytest.raises(PortError):
        adapter.resolve_placed_selection(source_content, geometry, tool, selection)
