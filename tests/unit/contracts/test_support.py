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


def _selection(*, role: str = "support_surface", frame: Any = None) -> Any:
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
        rule=selection.WholeBodyRule(body),
    )


def _evidence(target_field: str, seed: str = "a") -> EvidenceRef:
    return EvidenceRef(
        schema_version="1",
        source_kind="user_instruction",
        reference="User:support-contract",
        target_field=target_field,
        content_digest=seed * 64,
    )


def _transform(source: Any, target: Any) -> Any:
    spatial = SPATIAL_MODULE
    assert spatial is not None
    return spatial.RigidTransform(
        source_frame=source,
        target_frame=target,
        translation=spatial.Translation3(
            target,
            Quantity(0, "m"),
            Quantity(0, "m"),
            Quantity(0, "m"),
        ),
        rotation=spatial.ProperRotation(((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))),
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
) -> Any:
    support = _support()
    spatial = SPATIAL_MODULE
    assert spatial is not None
    frame = spatial.FrameId("World") if frame is None else frame
    selection_frame = frame if selection_frame is None else selection_frame
    return support.SolidSupport(
        support_id=support.SupportId(support_id),
        selection=_selection(role=role, frame=selection_frame),
        frame=frame,
        x=support.SupportComponent(
            x_state,
            _evidence("support.x", "b") if x_evidence is None else x_evidence,
        ),
        y=support.SupportComponent(
            y_state,
            _evidence("support.y", "c") if y_evidence is None else y_evidence,
        ),
        z=support.SupportComponent(
            z_state,
            _evidence("support.z", "d") if z_evidence is None else z_evidence,
        ),
        transform=transform,
    )


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
