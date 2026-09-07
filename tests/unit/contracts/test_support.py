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


SUPPORT_MODULE = _optional_module("febio_cae.domain.support")
SELECTION_MODULE = _optional_module("febio_cae.domain.selection")
SPATIAL_MODULE = _optional_module("febio_cae.domain.spatial")


def _support() -> ModuleType:
    if SUPPORT_MODULE is None or SELECTION_MODULE is None or SPATIAL_MODULE is None:
        pytest.skip("support API availability is covered by the dedicated assertion")
    return SUPPORT_MODULE


def _support_fields() -> set[str]:
    if SUPPORT_MODULE is None:
        return set()
    support_type = getattr(SUPPORT_MODULE, "SolidSupport", None)
    return set(getattr(support_type, "__dataclass_fields__", {}))


def _selection(
    *,
    role: str = "support_surface",
    frame: Any = None,
    rule: Any = None,
    resolution: Any = None,
) -> Any:
    selection = SELECTION_MODULE
    spatial = SPATIAL_MODULE
    assert selection is not None and spatial is not None
    frame = spatial.FrameId("World") if frame is None else frame
    body = spatial.BodyId("body-A")
    return selection.SelectionRef(
        name="support-selection",
        role=role,
        role_evidence=_evidence("selection.role", "a"),
        geometry_digest="a" * 64,
        body_id=body,
        frame=frame,
        rule=selection.WholeBodyRule(body) if rule is None else rule,
        resolution=resolution,
    )


def _evidence(target_field: str, seed: str = "a") -> EvidenceRef:
    return EvidenceRef(
        schema_version="1",
        source_kind="user_instruction",
        reference="User:support-contract",
        target_field=target_field,
        content_digest=seed * 64,
    )


def _transform(
    source: Any,
    target: Any,
    *,
    translation: tuple[Quantity, Quantity, Quantity] | None = None,
    rotation: tuple[tuple[float, float, float], ...] | None = None,
) -> Any:
    spatial = SPATIAL_MODULE
    assert spatial is not None
    values = (
        (Quantity(0, "m"), Quantity(0, "m"), Quantity(0, "m"))
        if translation is None
        else translation
    )
    matrix = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)) if rotation is None else rotation
    return spatial.RigidTransform(
        source_frame=source,
        target_frame=target,
        translation=spatial.Translation3(
            target,
            *values,
        ),
        rotation=spatial.ProperRotation(matrix),
    )


def _support_value(
    *,
    support_id: str = "support-A",
    selection_frame: Any = None,
    frame: Any = None,
    role: str = "support_surface",
    transform: Any = None,
    x_state: str = "fixed",
    y_state: str = "free",
    z_state: str = "fixed",
    x_evidence: EvidenceRef | None = None,
    y_evidence: EvidenceRef | None = None,
    z_evidence: EvidenceRef | None = None,
    frame_evidence: EvidenceRef | None = None,
    transform_evidence: EvidenceRef | None = None,
    include_frame_evidence: bool = True,
    include_transform_evidence: bool = True,
    selection: Any = None,
) -> Any:
    support = _support()
    spatial = SPATIAL_MODULE
    assert spatial is not None
    frame = spatial.FrameId("World") if frame is None else frame
    selection_frame = frame if selection_frame is None else selection_frame
    kwargs: dict[str, Any] = {
        "support_id": support.SupportId(support_id),
        "selection": _selection(role=role, frame=selection_frame)
        if selection is None
        else selection,
        "frame": frame,
        "x": support.SupportComponent(
            x_state,
            _evidence("support.x", "b") if x_evidence is None else x_evidence,
        ),
        "y": support.SupportComponent(
            y_state,
            _evidence("support.y", "c") if y_evidence is None else y_evidence,
        ),
        "z": support.SupportComponent(
            z_state,
            _evidence("support.z", "d") if z_evidence is None else z_evidence,
        ),
        "transform": transform,
    }
    if include_frame_evidence and "frame_evidence" in _support_fields():
        kwargs["frame_evidence"] = (
            _evidence("support.frame", "e") if frame_evidence is None else frame_evidence
        )
    if include_transform_evidence and "transform_evidence" in _support_fields():
        kwargs["transform_evidence"] = (
            _evidence("support.transform", "f")
            if transform is not None and transform_evidence is None
            else transform_evidence
        )
    return support.SolidSupport(**kwargs)


def test_support_api_is_available() -> None:
    assert SUPPORT_MODULE is not None, "P1-B3 support module is not available"
    assert SELECTION_MODULE is not None, "P1-B3 selection dependency is not available"
    assert SPATIAL_MODULE is not None, "P1-B3 spatial dependency is not available"
    for name in ("SCHEMA_VERSION", "SupportId", "SupportComponent", "SolidSupport", "SupportSet"):
        assert getattr(SUPPORT_MODULE, name, None) is not None, name


def test_solid_support_preserves_explicit_components_role_frame_and_evidence() -> None:
    support = _support()
    value = _support_value()
    payload = value.to_dict()

    assert payload["schema_version"] == "1"
    assert payload["support_id"] == "support-A"
    assert payload["selection"]["stated_role"] == "support_surface"
    assert payload["frame"] == "World"
    assert payload["x"]["state"] == "fixed"
    assert payload["y"]["state"] == "free"
    assert payload["z"]["state"] == "fixed"
    assert payload["transform"] is None
    assert payload["x"]["evidence"]["target_field"] == "support.x"
    assert payload["y"]["evidence"]["target_field"] == "support.y"
    assert payload["z"]["evidence"]["target_field"] == "support.z"
    assert value.to_bytes() == canonical_bytes(payload)
    assert support.SupportCollection is support.SupportSet


def test_support_free_and_fixed_zero_are_distinct_explicit_intents() -> None:
    support = _support()
    fixed = _support_value(x_state="fixed")
    free = _support_value(x_state="free")
    all_free = _support_value(x_state="free", y_state="free", z_state="free")

    assert fixed.to_dict()["x"]["state"] == "fixed"
    assert free.to_dict()["x"]["state"] == "free"
    assert fixed.to_bytes() != free.to_bytes()
    assert all_free.to_dict()["x"]["state"] == "free"
    assert all_free.to_dict()["y"]["state"] == "free"
    assert all_free.to_dict()["z"]["state"] == "free"
    assert support.SupportComponent("fixed", _evidence("support.x")).to_dict()["state"] == "fixed"


def test_support_requires_support_role_and_frame_consistency_without_transform() -> None:
    spatial = SPATIAL_MODULE
    assert spatial is not None
    with pytest.raises(ValueError):
        _support_value(role="contact_surface")
    with pytest.raises(ValueError):
        _support_value(selection_frame=spatial.FrameId("Other"))

    explicit = _support_value(
        selection_frame=spatial.FrameId("Other"),
        frame=spatial.FrameId("World"),
        transform=_transform(spatial.FrameId("Other"), spatial.FrameId("World")),
    )
    assert explicit.to_dict()["transform"]["source_frame"] == "Other"
    assert explicit.to_dict()["transform"]["target_frame"] == "World"

    with pytest.raises(ValueError):
        _support_value(
            selection_frame=spatial.FrameId("Other"),
            frame=spatial.FrameId("World"),
            transform=_transform(spatial.FrameId("World"), spatial.FrameId("World")),
        )
    with pytest.raises(ValueError):
        _support_value(
            selection_frame=spatial.FrameId("Other"),
            frame=spatial.FrameId("World"),
            transform=_transform(spatial.FrameId("Other"), spatial.FrameId("Other")),
        )


@pytest.mark.parametrize(
    ("field", "wrong_target"),
    [
        ("x_evidence", "support.y"),
        ("y_evidence", "support.z"),
        ("z_evidence", "support.x"),
    ],
)
def test_support_component_evidence_is_bound_to_the_component_field(
    field: str,
    wrong_target: str,
) -> None:
    kwargs: dict[str, Any] = {field: _evidence(wrong_target, "f")}
    with pytest.raises(ValueError):
        _support_value(**kwargs)


@pytest.mark.parametrize("state", ["", "fixed-at-zero", "free-ish", True, None])
def test_support_component_rejects_unknown_states(state: object) -> None:
    support = _support()
    with pytest.raises(ValueError):
        support.SupportComponent(state, _evidence("support.x"))


def test_support_set_is_an_immutable_sorted_semantic_set() -> None:
    support = _support()
    values = [_support_value(support_id="support-B"), _support_value(support_id="support-A")]
    collection = support.SupportSet(values)
    values.clear()

    assert [item["support_id"] for item in collection.to_dict()["supports"]] == [
        "support-A",
        "support-B",
    ]
    assert (
        collection.to_bytes()
        == support.SupportSet(
            [_support_value(support_id="support-A"), _support_value(support_id="support-B")]
        ).to_bytes()
    )
    with pytest.raises(ValueError):
        support.SupportSet([_support_value(), _support_value()])
    with pytest.raises(FrozenInstanceError):
        collection.supports = ()


def test_support_input_evidence_changes_bytes_without_mutating_the_value() -> None:
    value = _support_value()
    changed = _support_value(x_evidence=_evidence("support.x", "f"))
    payload = value.to_dict()
    payload["x"]["evidence"]["content_digest"] = "f" * 64

    assert value.to_bytes() != changed.to_bytes()
    assert value.x.evidence.content_digest == "b" * 64
    assert value.to_dict()["x"]["evidence"]["content_digest"] == "b" * 64


def test_support_has_explicit_frame_and_transform_evidence_slots() -> None:
    fields = _support_fields()
    assert "frame_evidence" in fields
    assert "transform_evidence" in fields


def test_support_frame_and_transform_evidence_are_required_and_field_bound() -> None:
    if not {"frame_evidence", "transform_evidence"}.issubset(_support_fields()):
        pytest.skip("new frame and transform evidence slots are unavailable")
    spatial = SPATIAL_MODULE
    assert spatial is not None
    with pytest.raises(ValueError):
        _support_value(include_frame_evidence=False)
    with pytest.raises(ValueError):
        _support_value(frame_evidence=_evidence("support.transform", "e"))

    transform = _transform(spatial.FrameId("Other"), spatial.FrameId("World"))
    with pytest.raises(ValueError):
        _support_value(
            selection_frame=spatial.FrameId("Other"),
            frame=spatial.FrameId("World"),
            transform=transform,
            include_transform_evidence=False,
        )
    with pytest.raises(ValueError):
        _support_value(
            selection_frame=spatial.FrameId("Other"),
            frame=spatial.FrameId("World"),
            transform=transform,
            transform_evidence=_evidence("support.frame", "f"),
        )
    with pytest.raises(ValueError):
        _support_value(transform_evidence=_evidence("support.transform", "f"))

    same_frame = _support_value()
    assert same_frame.to_dict()["frame_evidence"]["target_field"] == "support.frame"
    assert same_frame.to_dict()["transform_evidence"] is None


def test_support_transform_evidence_covers_changed_coordinate_interpretation() -> None:
    if not {"frame_evidence", "transform_evidence"}.issubset(_support_fields()):
        pytest.skip("new frame and transform evidence slots are unavailable")
    spatial = SPATIAL_MODULE
    assert spatial is not None
    source = spatial.FrameId("Other")
    target = spatial.FrameId("World")
    identity = _support_value(
        selection_frame=source,
        frame=target,
        transform=_transform(source, target),
    )
    quarter_turn = _support_value(
        selection_frame=source,
        frame=target,
        transform=_transform(
            source,
            target,
            rotation=((0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
        ),
    )
    assert identity.x.evidence == quarter_turn.x.evidence
    assert identity.y.evidence == quarter_turn.y.evidence
    assert identity.z.evidence == quarter_turn.z.evidence
    assert identity.to_bytes() != quarter_turn.to_bytes()


def test_support_composes_selection_semantic_sets_through_nested_serialization() -> None:
    support = _support()
    selection = SELECTION_MODULE
    spatial = SPATIAL_MODULE
    assert selection is not None and spatial is not None
    frame = spatial.FrameId("World")
    body = spatial.BodyId("body-A")
    face_a = spatial.FaceId("face-A")
    face_b = spatial.FaceId("face-B")
    measurement_a = selection.FaceMeasurement(
        face_a,
        Quantity(1, "m2"),
        spatial.Point3(frame, Quantity(0, "m"), Quantity(0, "m"), Quantity(0, "m")),
    )
    measurement_b = selection.FaceMeasurement(
        face_b,
        Quantity(2, "m2"),
        spatial.Point3(frame, Quantity(1, "m"), Quantity(0, "m"), Quantity(0, "m")),
    )
    first = _selection(
        frame=frame,
        rule=selection.FaceSetRule(
            "a" * 64,
            body,
            frame,
            [face_a, face_b],
            _evidence("selection.faces", "a"),
        ),
        resolution=selection.ResolutionSnapshot(
            "a" * 64, body, frame, [measurement_a, measurement_b]
        ),
    )
    second = _selection(
        frame=frame,
        rule=selection.FaceSetRule(
            "a" * 64,
            body,
            frame,
            [face_b, face_a],
            _evidence("selection.faces", "a"),
        ),
        resolution=selection.ResolutionSnapshot(
            "a" * 64, body, frame, [measurement_b, measurement_a]
        ),
    )
    assert first.to_bytes() == second.to_bytes()
    first_support = _support_value(selection=first)
    second_support = _support_value(selection=second)
    assert first_support.to_bytes() == second_support.to_bytes()
    assert (
        support.SupportSet([first_support]).to_bytes()
        == support.SupportSet([second_support]).to_bytes()
    )


@pytest.mark.parametrize(
    "bad_quantity",
    [Quantity(10**400, "m"), Quantity(5e-324, "mm")],
    ids=["overflow", "underflow"],
)
def test_support_rejects_unrepresentable_nested_quantities_at_construction(
    bad_quantity: Quantity,
) -> None:
    spatial = SPATIAL_MODULE
    selection = SELECTION_MODULE
    assert spatial is not None and selection is not None
    source = spatial.FrameId("Other")
    target = spatial.FrameId("World")
    with pytest.raises(ValueError, match="outside finite range|underflows"):
        _support_value(
            selection_frame=source,
            frame=target,
            transform=_transform(
                source,
                target,
                translation=(bad_quantity, Quantity(0, "m"), Quantity(0, "m")),
            ),
        )
    with pytest.raises(ValueError, match="outside finite range|underflows"):
        _support_value(
            selection=selection.SelectionRef(
                name="predicate-selection",
                role="support_surface",
                role_evidence=_evidence("selection.role", "a"),
                geometry_digest="a" * 64,
                body_id=spatial.BodyId("body-A"),
                frame=target,
                rule=selection.CoordinatePredicateRule(
                    spatial.BodyId("body-A"),
                    target,
                    [
                        selection.CoordinatePredicate(
                            spatial.UnitDirection(target, 1, 0, 0), "eq", bad_quantity
                        )
                    ],
                ),
            )
        )

    centroid_selection = selection.SelectionRef(
        name="centroid-selection",
        role="support_surface",
        role_evidence=_evidence("selection.role", "a"),
        geometry_digest="a" * 64,
        body_id=spatial.BodyId("body-A"),
        frame=target,
        rule=selection.WholeBodyRule(spatial.BodyId("body-A")),
        resolution=selection.ResolutionSnapshot(
            "a" * 64,
            spatial.BodyId("body-A"),
            target,
            [
                selection.FaceMeasurement(
                    spatial.FaceId("face-A"),
                    Quantity(1, "m2"),
                    spatial.Point3(
                        target,
                        bad_quantity,
                        Quantity(0, "m"),
                        Quantity(0, "m"),
                    ),
                )
            ],
        ),
    )
    with pytest.raises(ValueError, match="outside finite range|underflows"):
        _support_value(selection=centroid_selection)


@pytest.mark.parametrize(
    "bad_area",
    [Quantity(10**400, "m2"), Quantity(5e-324, "mm2")],
    ids=["area-overflow", "area-underflow"],
)
def test_support_rejects_unrepresentable_nested_area_at_construction(
    bad_area: Quantity,
) -> None:
    selection = SELECTION_MODULE
    spatial = SPATIAL_MODULE
    assert selection is not None and spatial is not None
    frame = spatial.FrameId("World")
    body = spatial.BodyId("body-A")
    value = _selection(
        frame=frame,
        rule=selection.WholeBodyRule(body),
        resolution=selection.ResolutionSnapshot(
            "a" * 64,
            body,
            frame,
            [
                selection.FaceMeasurement(
                    spatial.FaceId("face-A"),
                    bad_area,
                    spatial.Point3(frame, Quantity(0, "m"), Quantity(0, "m"), Quantity(0, "m")),
                )
            ],
        ),
    )
    with pytest.raises(ValueError, match="outside finite range|underflows"):
        _support_value(selection=value)
