"""Draft-scoped composition of the concrete placed-selection dependency."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from febio_cae.domain import (
    GeometryInspection,
    GeometryInspectionRequest,
    GeometryIntent,
    GeometrySelectionRequest,
    ResolutionSnapshot,
    RigidToolIntent,
    SelectionRef,
    SourceAssetContent,
)
from febio_cae.domain.ports import GeometryPort, PortError, PortErrorCategory

PlacedSelection = Callable[
    [SourceAssetContent, GeometryIntent, RigidToolIntent, SelectionRef], ResolutionSnapshot
]


@dataclass(frozen=True, slots=True)
class _PlacedGeometry:
    """No ambient mutable draft: each wrapper belongs to one evidence snapshot."""

    base: GeometryPort
    source: SourceAssetContent
    geometry: GeometryIntent
    tool: RigidToolIntent
    resolve: PlacedSelection

    def inspect(
        self, request: GeometryInspectionRequest, source: SourceAssetContent
    ) -> GeometryInspection:
        return self.base.inspect(request, source)

    def resolve_selection(
        self, request: GeometrySelectionRequest, source: SourceAssetContent
    ) -> ResolutionSnapshot:
        selection = request.selection
        identity = (selection.geometry_digest, selection.body_id, selection.frame)
        part = (
            self.geometry.geometry_digest,
            self.geometry.body_id,
            self.geometry.placement.target_frame,
        )
        tool = (
            self.tool.contact_surface.geometry_digest,
            self.tool.primitive.body_id,
            self.tool.primitive.placement.target_frame,
        )
        if (
            source != self.source
            or request.source_asset != self.source.source_asset
            or self.geometry.source_step_digest != source.source_asset.content_digest
            or identity not in (part, tool)
            or (request.geometry_intent is not None and request.geometry_intent != self.geometry)
        ):
            raise PortError(PortErrorCategory.INTEGRITY, "selection is outside registered scope")
        return self.resolve(source, self.geometry, self.tool, selection)
