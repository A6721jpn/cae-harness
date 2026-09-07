from __future__ import annotations

import importlib
import re
from dataclasses import FrozenInstanceError
from types import ModuleType
from typing import Any

import pytest

from febio_cae.domain import (
    BodyId,
    EvidenceRef,
    FrameId,
    ProperRotation,
    Quantity,
    RigidTransform,
    Translation3,
    canonical_bytes,
)


def _optional_module(name: str) -> ModuleType | None:
    try:
        return importlib.import_module(name)
    except (ImportError, ModuleNotFoundError):
        return None


GEOMETRY_MODULE = _optional_module("febio_cae.domain.geometry")
_MISSING = object()


def _geometry() -> ModuleType:
    if GEOMETRY_MODULE is None:
        pytest.skip("geometry API availability is covered by the dedicated assertion")
    return GEOMETRY_MODULE


def _evidence(target_field: str, seed: str = "a") -> EvidenceRef:
    return EvidenceRef(
        schema_version="1",
        source_kind="user_instruction",
        reference="User:geometry-contract",
        target_field=target_field,
        content_digest=seed * 64,
    )


def _placement(
    *,
    source: FrameId | None = None,
    target: FrameId | None = None,
    translation: tuple[Quantity, Quantity, Quantity] | None = None,
) -> RigidTransform:
    source = FrameId("PartLocal") if source is None else source
    target = FrameId("World") if target is None else target
    values = (
        (Quantity(0, "m"), Quantity(0, "m"), Quantity(0, "m"))
        if translation is None
        else translation
    )
    return RigidTransform(
        source_frame=source,
        target_frame=target,
        translation=Translation3(target, *values),
        rotation=ProperRotation(((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))),
    )


def _value(
    geometry: ModuleType,
    *,
    source_step_digest: object = "a" * 64,
    geometry_digest: object = "b" * 64,
    inspection_digest: object = "c" * 64,
    body_id: object = BodyId("body-A"),
    step_unit: object = "mm",
    placement: object = _MISSING,
    body_evidence: object = _MISSING,
    unit_evidence: object = _MISSING,
    placement_evidence: object = _MISSING,
) -> Any:
    return geometry.GeometryIntent(
        source_step_digest=source_step_digest,
        geometry_digest=geometry_digest,
        inspection_digest=inspection_digest,
        body_id=body_id,
        step_unit=step_unit,
        placement=_placement() if placement is _MISSING else placement,
        body_evidence=(
            _evidence("geometry.body_id", "d") if body_evidence is _MISSING else body_evidence
        ),
        unit_evidence=(
            _evidence("geometry.step_unit", "e") if unit_evidence is _MISSING else unit_evidence
        ),
        placement_evidence=(
            _evidence("geometry.placement", "f")
            if placement_evidence is _MISSING
            else placement_evidence
        ),
    )


def test_geometry_api_is_available() -> None:
    assert GEOMETRY_MODULE is not None, "P1-B7 geometry module is not available"
    for name in ("SCHEMA_VERSION", "GeometryIntent", "GeometryValidationError"):
        assert getattr(GEOMETRY_MODULE, name, None) is not None, name


def test_geometry_preserves_required_fields_and_canonical_projection() -> None:
    geometry = _geometry()
    value = _value(geometry)
    payload = value.to_dict()

    assert payload == {
        "schema_version": "1",
        "source_step_digest": "a" * 64,
        "geometry_digest": "b" * 64,
        "inspection_digest": "c" * 64,
        "body_id": "body-A",
        "step_unit": "mm",
        "placement": {
            "schema_version": "1",
            "source_frame": "PartLocal",
            "target_frame": "World",
            "translation": {
                "schema_version": "1",
                "frame": "World",
                "x": {"value": 0.0, "unit": "m"},
                "y": {"value": 0.0, "unit": "m"},
                "z": {"value": 0.0, "unit": "m"},
            },
            "rotation": {
                "schema_version": "1",
                "matrix": [
                    [1.0, 0.0, 0.0],
                    [0.0, 1.0, 0.0],
                    [0.0, 0.0, 1.0],
                ],
            },
        },
        "body_evidence": _evidence("geometry.body_id", "d").to_dict(),
        "unit_evidence": _evidence("geometry.step_unit", "e").to_dict(),
        "placement_evidence": _evidence("geometry.placement", "f").to_dict(),
    }
    assert value.to_bytes() == canonical_bytes(payload)


def test_geometry_requires_all_fields_and_rejects_unknown_fields() -> None:
    geometry = _geometry()
    values: dict[str, object] = {
        "source_step_digest": "a" * 64,
        "geometry_digest": "b" * 64,
        "inspection_digest": "c" * 64,
        "body_id": BodyId("body-A"),
        "step_unit": "mm",
        "placement": _placement(),
        "body_evidence": _evidence("geometry.body_id"),
        "unit_evidence": _evidence("geometry.step_unit"),
        "placement_evidence": _evidence("geometry.placement"),
    }
    for missing in tuple(values):
        incomplete = values.copy()
        incomplete.pop(missing)
        with pytest.raises(TypeError):
            geometry.GeometryIntent(**incomplete)
    with pytest.raises(TypeError):
        geometry.GeometryIntent(**values, unexpected=True)


@pytest.mark.parametrize("field", ["source_step_digest", "geometry_digest", "inspection_digest"])
@pytest.mark.parametrize(
    "bad_digest",
    ["", "not-a-digest", "A" * 64, "a" * 63, "a" * 65, None, 1],
)
def test_geometry_digest_fields_require_lowercase_sha256(field: str, bad_digest: object) -> None:
    geometry = _geometry()
    with pytest.raises(ValueError):
        _value(geometry, **{field: bad_digest})


def test_geometry_digest_roles_are_distinct_fields_without_invented_relationships() -> None:
    geometry = _geometry()
    value = _value(
        geometry,
        source_step_digest="a" * 64,
        geometry_digest="a" * 64,
        inspection_digest="a" * 64,
    )

    assert value.source_step_digest == value.geometry_digest == value.inspection_digest
    assert value.to_dict()["source_step_digest"] == "a" * 64
    assert value.to_dict()["geometry_digest"] == "a" * 64
    assert value.to_dict()["inspection_digest"] == "a" * 64


@pytest.mark.parametrize(
    "field",
    ["source_step_digest", "geometry_digest", "inspection_digest"],
)
def test_each_digest_role_changes_canonical_bytes(field: str) -> None:
    geometry = _geometry()
    baseline = _value(geometry).to_bytes()
    changed = _value(geometry, **{field: "f" * 64}).to_bytes()
    assert changed != baseline


@pytest.mark.parametrize(
    ("field", "wrong_target"),
    [
        ("body_evidence", "geometry.step_unit"),
        ("unit_evidence", "geometry.placement"),
        ("placement_evidence", "geometry.body_id"),
    ],
)
def test_geometry_evidence_is_bound_to_exact_fields(field: str, wrong_target: str) -> None:
    geometry = _geometry()
    expected_target = {
        "body_evidence": "geometry.body_id",
        "unit_evidence": "geometry.step_unit",
        "placement_evidence": "geometry.placement",
    }[field]
    wrong_evidence = _evidence(wrong_target, "0")

    with pytest.raises(
        geometry.GeometryValidationError,
        match=rf"^{re.escape(field)} must target {re.escape(expected_target)}$",
    ):
        _value(geometry, **{field: wrong_evidence})


@pytest.mark.parametrize("field", ["body_evidence", "unit_evidence", "placement_evidence"])
@pytest.mark.parametrize("bad_evidence", [None, "evidence", object()])
def test_geometry_evidence_fields_require_typed_evidence(field: str, bad_evidence: object) -> None:
    geometry = _geometry()
    with pytest.raises(ValueError):
        _value(geometry, **{field: bad_evidence})


@pytest.mark.parametrize("field", ["body_evidence", "unit_evidence", "placement_evidence"])
def test_each_evidence_role_changes_canonical_bytes(field: str) -> None:
    geometry = _geometry()
    baseline = _value(geometry).to_bytes()
    target = {
        "body_evidence": _evidence("geometry.body_id", "0"),
        "unit_evidence": _evidence("geometry.step_unit", "0"),
        "placement_evidence": _evidence("geometry.placement", "0"),
    }
    changed = _value(geometry, **{field: target[field]}).to_bytes()
    assert changed != baseline


@pytest.mark.parametrize("bad_body", ["body-A", FrameId("body-A"), None, 1])
def test_geometry_requires_typed_body_id(bad_body: object) -> None:
    geometry = _geometry()
    with pytest.raises(ValueError):
        _value(geometry, body_id=bad_body)


@pytest.mark.parametrize("bad_placement", [None, "placement", FrameId("World"), object()])
def test_geometry_requires_typed_rigid_transform(bad_placement: object) -> None:
    geometry = _geometry()
    with pytest.raises(ValueError):
        _value(geometry, placement=bad_placement)


@pytest.mark.parametrize("bad_unit", ["", "MM", "mm2", "m3", "s", None, 1, Quantity(1, "mm")])
def test_geometry_step_unit_requires_supported_length_unit_spelling(bad_unit: object) -> None:
    geometry = _geometry()
    with pytest.raises(ValueError):
        _value(geometry, step_unit=bad_unit)


def test_geometry_preserves_explicit_step_unit_spelling_in_identity() -> None:
    geometry = _geometry()
    millimetres = _value(geometry, step_unit="mm")
    metres = _value(geometry, step_unit="m")

    assert millimetres.to_dict()["step_unit"] == "mm"
    assert metres.to_dict()["step_unit"] == "m"
    assert millimetres.to_bytes() != metres.to_bytes()


def test_geometry_does_not_infer_body_or_step_unit_or_placement_defaults() -> None:
    geometry = _geometry()
    with pytest.raises(TypeError):
        geometry.GeometryIntent(
            source_step_digest="a" * 64,
            geometry_digest="b" * 64,
            inspection_digest="c" * 64,
            body_evidence=_evidence("geometry.body_id"),
            unit_evidence=_evidence("geometry.step_unit"),
            placement_evidence=_evidence("geometry.placement"),
        )


def test_geometry_accepts_same_body_in_different_source_geometry_revisions() -> None:
    geometry = _geometry()
    first = _value(geometry, geometry_digest="a" * 64)
    second = _value(geometry, geometry_digest="f" * 64)

    assert first.body_id == second.body_id == BodyId("body-A")
    assert first.geometry_digest != second.geometry_digest


def test_geometry_translation_uses_shared_si_projection_without_second_step_scaling() -> None:
    geometry = _geometry()
    displayed = _value(
        geometry,
        step_unit="mm",
        placement=_placement(
            translation=(Quantity(5, "mm"), Quantity(-2, "mm"), Quantity(0, "mm"))
        ),
    )
    si = _value(
        geometry,
        step_unit="mm",
        placement=_placement(
            translation=(Quantity(0.005, "m"), Quantity(-0.002, "m"), Quantity(0, "m"))
        ),
    )

    assert displayed.to_dict()["step_unit"] == "mm"
    assert displayed.to_dict()["placement"]["translation"]["x"] == {
        "value": 0.005,
        "unit": "m",
    }
    assert displayed.to_bytes() == si.to_bytes()


@pytest.mark.parametrize(
    "bad_translation",
    [Quantity(10**400, "m"), Quantity(5e-324, "mm")],
    ids=["overflow", "underflow"],
)
def test_geometry_rejects_unrepresentable_nested_translation_at_construction(
    bad_translation: Quantity,
) -> None:
    geometry = _geometry()
    with pytest.raises(ValueError, match="outside finite range|underflows"):
        _value(
            geometry,
            placement=_placement(translation=(bad_translation, Quantity(0, "m"), Quantity(0, "m"))),
        )


def test_geometry_preserves_explicit_transform_frames() -> None:
    geometry = _geometry()
    placement = _placement(source=FrameId("CAD-Part"), target=FrameId("Assembly"))
    value = _value(geometry, placement=placement)

    assert value.placement.source_frame == FrameId("CAD-Part")
    assert value.placement.target_frame == FrameId("Assembly")
    assert value.placement.translation.frame == FrameId("Assembly")


def test_geometry_is_immutable_and_projection_isolation_is_preserved() -> None:
    geometry = _geometry()
    value = _value(geometry)
    payload = value.to_dict()
    payload["geometry_digest"] = "f" * 64
    payload["placement"]["translation"]["x"]["value"] = 99.0

    with pytest.raises(FrozenInstanceError):
        value.body_id = BodyId("body-B")  # type: ignore[misc]
    assert value.geometry_digest == "b" * 64
    assert value.to_dict()["placement"]["translation"]["x"] == {
        "value": 0.0,
        "unit": "m",
    }
