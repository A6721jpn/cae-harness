from __future__ import annotations

import importlib
from dataclasses import FrozenInstanceError
from types import ModuleType
from typing import Any

import pytest

from febio_cae.domain import EvidenceRef, Quantity, canonical_bytes


def _optional_module(name: str) -> ModuleType | None:
    try:
        return importlib.import_module(name)
    except (ImportError, ModuleNotFoundError):
        return None


RIGID_MODULE = _optional_module("febio_cae.domain.rigid")
SPATIAL_MODULE = _optional_module("febio_cae.domain.spatial")


def _rigid() -> ModuleType:
    if RIGID_MODULE is None or SPATIAL_MODULE is None:
        pytest.skip("rigid API availability is covered by the dedicated assertion")
    return RIGID_MODULE


def _evidence(target_field: str, seed: str = "a") -> EvidenceRef:
    return EvidenceRef(
        schema_version="1",
        source_kind="user_instruction",
        reference="User:rigid-contract",
        target_field=target_field,
        content_digest=seed * 64,
    )


def _placement(
    source: Any = None,
    target: Any = None,
    translation: tuple[Quantity, Quantity, Quantity] | None = None,
) -> Any:
    spatial = SPATIAL_MODULE
    assert spatial is not None
    source = spatial.FrameId("ToolLocal") if source is None else source
    target = spatial.FrameId("World") if target is None else target
    values = (
        (Quantity(0, "m"), Quantity(0, "m"), Quantity(0, "m"))
        if translation is None
        else translation
    )
    return spatial.RigidTransform(
        source_frame=source,
        target_frame=target,
        translation=spatial.Translation3(target, *values),
        rotation=spatial.ProperRotation(((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))),
    )


def _primitive(
    *,
    kind: str = "sphere",
    dimensions: dict[str, Any] | None = None,
    dimension_evidence: dict[str, EvidenceRef] | None = None,
    body_id: Any = None,
    local_frame: Any = None,
    placement: Any = None,
    model_evidence: EvidenceRef | None = None,
    placement_evidence: EvidenceRef | None = None,
) -> Any:
    rigid = _rigid()
    spatial = SPATIAL_MODULE
    assert spatial is not None
    defaults: dict[str, dict[str, Quantity]] = {
        "sphere": {"radius": Quantity(10, "mm")},
        "cylinder": {"radius": Quantity(10, "mm"), "height": Quantity(20, "mm")},
        "box": {
            "length": Quantity(10, "mm"),
            "width": Quantity(20, "mm"),
            "height": Quantity(30, "mm"),
        },
    }
    dimensions = (
        defaults.get(kind, {"radius": Quantity(10, "mm")}) if dimensions is None else dimensions
    )
    dimension_evidence = (
        {
            name: _evidence(f"rigid_tool.{name}", chr(98 + index))
            for index, name in enumerate(dimensions)
        }
        if dimension_evidence is None
        else dimension_evidence
    )
    local_frame = spatial.FrameId("ToolLocal") if local_frame is None else local_frame
    body_id = spatial.BodyId("tool-body") if body_id is None else body_id
    placement = _placement(source=local_frame) if placement is None else placement
    return rigid.RigidPrimitive(
        kind=kind,
        body_id=body_id,
        local_frame=local_frame,
        placement=placement,
        dimensions=dimensions,
        dimension_evidence=dimension_evidence,
        model_evidence=(
            _evidence("rigid_tool.model", "a") if model_evidence is None else model_evidence
        ),
        placement_evidence=(
            _evidence("rigid_tool.placement", "z")
            if placement_evidence is None
            else placement_evidence
        ),
    )


def test_rigid_api_is_available() -> None:
    assert RIGID_MODULE is not None, "P1-B3 rigid module is not available"
    assert SPATIAL_MODULE is not None, "P1-B3 spatial dependency is not available"
    for name in ("SCHEMA_VERSION", "RigidPrimitive", "RigidValidationError"):
        assert getattr(RIGID_MODULE, name, None) is not None, name


@pytest.mark.parametrize(
    ("kind", "expected_dimensions", "convention"),
    [
        ("sphere", {"radius"}, "sphere center at local origin"),
        ("cylinder", {"radius", "height"}, "cylinder centered on local z axis"),
        ("box", {"length", "width", "height"}, "box centered on local x/y/z axes"),
    ],
)
def test_rigid_primitives_preserve_kind_dimensions_frames_placement_and_evidence(
    kind: str,
    expected_dimensions: set[str],
    convention: str,
) -> None:
    primitive = _primitive(kind=kind)
    payload = primitive.to_dict()

    assert payload["schema_version"] == "1"
    assert payload["kind"] == kind
    assert set(payload["dimensions"]) == expected_dimensions
    assert payload["body_id"] == "tool-body"
    assert payload["local_frame"] == "ToolLocal"
    assert payload["placement"]["source_frame"] == "ToolLocal"
    assert payload["placement"]["target_frame"] == "World"
    assert payload["convention"] == convention
    assert payload["model_evidence"]["target_field"] == "rigid_tool.model"
    assert payload["placement_evidence"]["target_field"] == "rigid_tool.placement"
    assert primitive.to_bytes() == canonical_bytes(payload)


@pytest.mark.parametrize(
    ("kind", "dimensions"),
    [
        ("sphere", {}),
        ("sphere", {"radius": Quantity(1, "mm"), "height": Quantity(2, "mm")}),
        ("cylinder", {"radius": Quantity(1, "mm")}),
        ("box", {"length": Quantity(1, "mm"), "width": Quantity(2, "mm")}),
        ("unknown", {"radius": Quantity(1, "mm")}),
    ],
)
def test_rigid_kind_selects_an_exact_dimension_set(kind: str, dimensions: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        _primitive(kind=kind, dimensions=dimensions)


@pytest.mark.parametrize(
    "bad_dimension",
    [Quantity(0, "mm"), Quantity(-1, "mm"), Quantity(1, "s"), "1 mm", True, None],
)
def test_rigid_dimensions_require_positive_length_quantities(bad_dimension: object) -> None:
    with pytest.raises(ValueError):
        _primitive(dimensions={"radius": bad_dimension})


@pytest.mark.parametrize(
    ("field", "wrong_target"),
    [
        ("model_evidence", "rigid_tool.placement"),
        ("placement_evidence", "rigid_tool.model"),
    ],
)
def test_rigid_model_and_placement_evidence_are_field_bound(
    field: str,
    wrong_target: str,
) -> None:
    kwargs: dict[str, Any] = {field: _evidence(wrong_target, "f")}
    with pytest.raises(ValueError):
        _primitive(**kwargs)


def test_rigid_dimension_evidence_requires_exact_dimension_targets() -> None:
    with pytest.raises(ValueError):
        _primitive(
            dimension_evidence={"radius": _evidence("rigid_tool.height", "f")},
        )
    with pytest.raises(ValueError):
        _primitive(
            dimension_evidence={
                "radius": _evidence("rigid_tool.radius", "b"),
                "height": _evidence("rigid_tool.height", "c"),
            },
        )


def test_rigid_local_frame_must_match_placement_source_and_identity_pose_is_explicit() -> None:
    spatial = SPATIAL_MODULE
    assert spatial is not None
    with pytest.raises(ValueError):
        _primitive(
            local_frame=spatial.FrameId("OtherLocal"),
            placement=_placement(source=spatial.FrameId("ToolLocal")),
        )
    with pytest.raises(ValueError):
        _primitive(placement_evidence=_evidence("rigid_tool.model", "z"))

    identity = _primitive()
    assert identity.to_dict()["placement"]["translation"]["x"] == {
        "value": 0.0,
        "unit": "m",
    }


@pytest.mark.parametrize(
    "bad_quantity",
    [Quantity(10**400, "m"), Quantity(5e-324, "mm")],
    ids=["dimension-range", "dimension-underflow"],
)
def test_rigid_dimensions_reject_unrepresentable_si_values(bad_quantity: Quantity) -> None:
    with pytest.raises(ValueError, match="outside finite range|underflows"):
        _primitive(dimensions={"radius": bad_quantity})


@pytest.mark.parametrize(
    "bad_quantity",
    [Quantity(10**400, "m"), Quantity(5e-324, "mm")],
    ids=["placement-range", "placement-underflow"],
)
def test_rigid_placement_translation_rejects_unrepresentable_si_values(
    bad_quantity: Quantity,
) -> None:
    with pytest.raises(ValueError, match="outside finite range|underflows"):
        _primitive(
            placement=_placement(translation=(bad_quantity, Quantity(0, "m"), Quantity(0, "m")))
        )


def test_rigid_si_equivalent_dimensions_and_translation_have_identical_bytes() -> None:
    spatial = SPATIAL_MODULE
    assert spatial is not None
    displayed = _primitive(
        dimensions={"radius": Quantity(10, "mm")},
        placement=_placement(translation=(Quantity(5, "mm"), Quantity(0, "mm"), Quantity(0, "mm"))),
    )
    si = _primitive(
        dimensions={"radius": Quantity(0.01, "m")},
        placement=_placement(
            translation=(Quantity(0.005, "m"), Quantity(0, "m"), Quantity(0, "m"))
        ),
    )

    assert displayed.to_bytes() == si.to_bytes()


def test_rigid_input_mappings_are_copied_and_evidence_identity_changes_bytes() -> None:
    dimensions = {"radius": Quantity(10, "mm")}
    dimension_evidence = {"radius": _evidence("rigid_tool.radius", "b")}
    primitive = _primitive(dimensions=dimensions, dimension_evidence=dimension_evidence)
    dimensions["radius"] = Quantity(99, "mm")
    dimension_evidence["radius"] = _evidence("rigid_tool.radius", "f")
    changed = _primitive(
        dimension_evidence={"radius": _evidence("rigid_tool.radius", "f")},
    )

    assert primitive.to_dict()["dimensions"]["radius"] == {"value": 0.01, "unit": "m"}
    assert primitive.to_bytes() != changed.to_bytes()
    with pytest.raises(FrozenInstanceError):
        primitive.dimensions = {}
