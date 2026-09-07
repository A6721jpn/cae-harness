from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any, cast

import pytest

from febio_cae.adapters.geometry import (
    GeometryMeshBackend,
    GmshOCCBackend,
    GmshOCCConfig,
    StepGeometryMeshAdapter,
)
from febio_cae.domain import (
    BodyId,
    EvidenceRef,
    FrameId,
    GeometryInspectionRequest,
    GeometrySelectionRequest,
    SelectionRef,
    SourceAssetContent,
    SourceAssetRef,
    WholeBodyRule,
)


class _FakeOption:
    def __init__(self, owner: _FakeGmsh) -> None:
        self.owner = owner
        self.strings: dict[str, str] = {}

    def setNumber(self, name: str, value: float) -> None:
        del name, value

    def setString(self, name: str, value: str) -> None:
        self.owner.events.append(("setString", name, value))
        self.strings[name] = value


class _FakeOCC:
    def __init__(self, owner: _FakeGmsh) -> None:
        self.owner = owner

    def importShapes(self, path: str) -> None:
        self.owner.events.append(("importShapes", path))
        target_unit = self.owner.option.strings.get("Geometry.OCCTargetUnit")
        if target_unit != "M":
            raise RuntimeError("test fake requires an explicit metre OCC target unit")
        content = Path(path).read_bytes()
        source_scale_to_si = 1.0 if b".METRE." in content and b".MILLI." not in content else 1.0e-3
        source_side = 0.01 if source_scale_to_si == 1.0 else 10.0
        physical_side = source_side * source_scale_to_si
        self.owner.native_scale_to_si = 1.0
        self.owner.side_native = physical_side / self.owner.native_scale_to_si

    def synchronize(self) -> None:
        return None

    def getMass(self, dimension: int, tag: int) -> float:
        side = self.owner.side_native
        if dimension == 3:
            return side**3 / 6.0
        if dimension == 2:
            return self.owner.surface_area_native(tag)
        raise RuntimeError(f"unexpected dimension {dimension}")

    def getCenterOfMass(self, dimension: int, tag: int) -> tuple[float, float, float]:
        if dimension != 2:
            raise RuntimeError(f"unexpected dimension {dimension}")
        return self.owner.surface_centroid_native(tag)


class _FakeModel:
    def __init__(self, owner: _FakeGmsh) -> None:
        self.owner = owner
        self.occ = _FakeOCC(owner)

    def add(self, name: str) -> None:
        del name

    def getEntities(self, dimension: int) -> list[tuple[int, int]]:
        return [(3, 1)] if dimension == 3 else []

    def getBoundary(
        self, entities: list[tuple[int, int]], combined: bool, oriented: bool
    ) -> list[tuple[int, int]]:
        del entities, combined, oriented
        return [(2, index) for index in range(1, 5)]

    def getAdjacencies(self, dimension: int, tag: int) -> tuple[list[int], list[int]]:
        if dimension != 2 or tag not in {1, 2, 3, 4}:
            raise RuntimeError("unexpected adjacency query")
        return [1], []


class _FakeGmsh:
    __version__ = "4.15.2"

    def __init__(self) -> None:
        self.events: list[tuple[Any, ...]] = []
        self.option = _FakeOption(self)
        self.model = _FakeModel(self)
        self.native_scale_to_si = 1.0
        self.side_native = 0.0

    def isInitialized(self) -> bool:
        return False

    def initialize(self) -> None:
        self.events.append(("initialize",))

    def clear(self) -> None:
        self.events.append(("clear",))

    def finalize(self) -> None:
        self.events.append(("finalize",))

    def surface_area_native(self, tag: int) -> float:
        side = self.side_native
        return side**2 * (math.sqrt(3.0) / 2.0 if tag == 3 else 0.5)

    def surface_centroid_native(self, tag: int) -> tuple[float, float, float]:
        side = self.side_native
        vertices = {
            1: ((0.0, 0.0, 0.0), (side, 0.0, 0.0), (0.0, side, 0.0)),
            2: ((0.0, 0.0, 0.0), (side, 0.0, 0.0), (0.0, 0.0, side)),
            3: ((side, 0.0, 0.0), (0.0, side, 0.0), (0.0, 0.0, side)),
            4: ((0.0, 0.0, 0.0), (0.0, 0.0, side), (0.0, side, 0.0)),
        }[tag]
        return cast(
            tuple[float, float, float],
            tuple(sum(point[index] for point in vertices) / 3.0 for index in range(3)),
        )


def _source(unit: str) -> SourceAssetContent:
    unit_expression = "$,.METRE." if unit == "m" else ".MILLI.,.METRE."
    source = (
        "ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\n"
        f"#1=SI_UNIT({unit_expression});\n"
        "ENDSEC;\nEND-ISO-10303-21;\n"
    ).encode("ascii")
    reference = SourceAssetRef(
        f"synthetic-{unit}-step",
        hashlib.sha256(source).hexdigest(),
        "model/step",
    )
    return SourceAssetContent(reference, source)


def _selection(geometry_digest: str) -> SelectionRef:
    evidence = EvidenceRef(
        schema_version="1",
        source_kind="registered_document",
        reference="P2GmshUnitTest:1",
        target_field="selection.role",
        content_digest=hashlib.sha256(b"selection").hexdigest(),
    )
    body = BodyId("body-1")
    return SelectionRef(
        name="whole-body",
        role="support_surface",
        role_evidence=evidence,
        geometry_digest=geometry_digest,
        body_id=body,
        frame=FrameId("World"),
        rule=WholeBodyRule(body),
    )


def test_gmsh_occ_target_unit_keeps_m_and_mm_equivalent_through_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeGmsh()
    monkeypatch.setattr(
        "febio_cae.adapters.geometry.gmsh_occ.importlib.import_module",
        lambda name: fake,
    )
    backend = GmshOCCBackend(
        GmshOCCConfig(
            module_name="fake-gmsh",
            expected_version="4.15.2",
            geometry_kernel="OpenCASCADE",
            frame_id="World",
        )
    )
    observations: list[tuple[float, tuple[tuple[float, tuple[float, ...]], ...]]] = []
    declared_units: list[str] = []

    for source in (_source("m"), _source("mm")):
        adapter = StepGeometryMeshAdapter(cast(GeometryMeshBackend, backend))
        inspection = adapter.inspect(GeometryInspectionRequest(source.source_asset), source)
        declared_units.append(inspection.declared_unit)
        selection = _selection(adapter.inspection_details(inspection).geometry_digest)
        resolution = adapter.resolve_selection(
            GeometrySelectionRequest(source.source_asset, selection), source
        )
        observations.append(
            (
                inspection.body_facts[0].volume_si,
                tuple(
                    (
                        face.area.to_si().value,
                        tuple(
                            coordinate.to_si().value
                            for coordinate in (
                                face.centroid.x,
                                face.centroid.y,
                                face.centroid.z,
                            )
                        ),
                    )
                    for face in resolution.faces
                ),
            )
        )

    assert observations[0][0] == pytest.approx(observations[1][0])
    assert len(observations[0][1]) == len(observations[1][1])
    for first_face, second_face in zip(observations[0][1], observations[1][1], strict=True):
        assert first_face[0] == pytest.approx(second_face[0])
        assert first_face[1] == pytest.approx(second_face[1])
    assert declared_units == ["m", "mm"]
    import_indices = [
        index for index, event in enumerate(fake.events) if event[0] == "importShapes"
    ]
    assert import_indices
    assert all(
        any(
            event[0] == "setString" and event[1] == "Geometry.OCCTargetUnit" and event[2] == "M"
            for event in fake.events[:import_index]
        )
        for import_index in import_indices
    )
