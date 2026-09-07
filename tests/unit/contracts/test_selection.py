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


SELECTION_MODULE = _optional_module("febio_cae.domain.selection")
SPATIAL_MODULE = _optional_module("febio_cae.domain.spatial")


def _selection() -> ModuleType:
    if SELECTION_MODULE is None or SPATIAL_MODULE is None:
        pytest.skip("selection API availability is covered by the dedicated assertion")
    return SELECTION_MODULE


def _spatial() -> ModuleType:
    if SPATIAL_MODULE is None:
        pytest.skip("spatial API availability is covered by the dedicated assertion")
    return SPATIAL_MODULE


def _evidence(seed: str = "a") -> EvidenceRef:
    return EvidenceRef(
        schema_version="1",
        source_kind="registered_document",
        reference="SelectionSource:1",
        target_field="selection.role",
        content_digest=seed * 64,
    )


def _context() -> tuple[Any, Any, Any, str]:
    spatial = _spatial()
    return (
        spatial.GeometryId("geometry-v1"),
        spatial.BodyId("body-A"),
        spatial.FrameId("World"),
        "b" * 64,
    )


def test_selection_api_is_available() -> None:
    assert SELECTION_MODULE is not None, "P1-B1 selection module is not available"
    assert SPATIAL_MODULE is not None, "P1-B1 spatial dependency is not available"
    for name in (
        "SCHEMA_VERSION",
        "NamedAttributeRule",
        "CoordinatePredicate",
        "CoordinatePredicateRule",
        "FaceSetRule",
        "WholeBodyRule",
        "FaceMeasurement",
        "ResolutionSnapshot",
        "SelectionRef",
    ):
        assert getattr(SELECTION_MODULE, name, None) is not None, name


def test_unresolved_named_attribute_selection_is_explicit_and_deterministic() -> None:
    selection = _selection()
    geometry, body, frame, digest = _context()
    role_evidence = _evidence()
    rule = selection.NamedAttributeRule("upper-contact")
    reference = selection.SelectionRef(
        name="upper-contact-selection",
        role="contact_surface",
        role_evidence=role_evidence,
        geometry_digest=digest,
        body_id=body,
        frame=frame,
        rule=rule,
    )

    payload = reference.to_dict()
    assert geometry.value == "geometry-v1"
    assert payload["schema_version"] == "1"
    assert payload["stated_role"] == "contact_surface"
    assert payload["geometry_digest"] == digest
    assert payload["body_id"] == "body-A"
    assert payload["frame"] == "World"
    assert payload["resolution"] is None
    assert reference.to_bytes() == canonical_bytes(payload)


def test_coordinate_predicate_rule_requires_explicit_body_frame_axis_and_bounds() -> None:
    selection = _selection()
    spatial = _spatial()
    _, body, frame, digest = _context()
    axis = spatial.UnitDirection(frame, 0, 0, 1)
    predicate = selection.CoordinatePredicate(
        axis=axis,
        operator="between",
        value=Quantity(1, "mm"),
        upper=Quantity(2, "mm"),
    )
    rule = selection.CoordinatePredicateRule(body, frame, [predicate])
    reference = selection.SelectionRef(
        name="coordinate-selection",
        role="support_surface",
        role_evidence=_evidence("c"),
        geometry_digest=digest,
        body_id=body,
        frame=frame,
        rule=rule,
    )

    assert reference.to_dict()["rule"]["predicates"][0]["value"] == {
        "value": 0.001,
        "unit": "m",
    }

    with pytest.raises(ValueError):
        selection.CoordinatePredicate(axis, "unknown", Quantity(1, "mm"))
    with pytest.raises(ValueError):
        selection.CoordinatePredicate(axis, "between", Quantity(1, "mm"), None)
    with pytest.raises(ValueError):
        selection.CoordinatePredicate(
            spatial.UnitDirection(spatial.FrameId("Other"), 0, 0, 1),
            "eq",
            Quantity(1, "mm"),
        )


def test_face_rule_copies_ids_and_requires_full_context() -> None:
    selection = _selection()
    spatial = _spatial()
    _, body, frame, digest = _context()
    face_ids = [spatial.FaceId("face-B"), spatial.FaceId("face-A")]
    rule = selection.FaceSetRule(
        geometry_digest=digest,
        body_id=body,
        frame=frame,
        face_ids=face_ids,
        provenance=_evidence("d"),
    )
    face_ids.append(spatial.FaceId("mutated-after-construction"))

    assert [item["value"] for item in rule.to_dict()["face_ids"]] == ["face-B", "face-A"]
    with pytest.raises(FrozenInstanceError):
        rule.face_ids = ()
    with pytest.raises(ValueError):
        selection.FaceSetRule(digest, body, frame, [], _evidence())
    with pytest.raises(ValueError):
        selection.FaceSetRule(
            digest,
            body,
            frame,
            [spatial.FaceId("same"), spatial.FaceId("same")],
            _evidence(),
        )


def test_resolution_snapshot_copies_faces_and_requires_matching_si_measurements() -> None:
    selection = _selection()
    spatial = _spatial()
    geometry, body, frame, digest = _context()
    face = selection.FaceMeasurement(
        spatial.FaceId("face-A"),
        Quantity(2, "mm2"),
        spatial.Point3(frame, Quantity(1, "mm"), Quantity(2, "mm"), Quantity(0, "mm")),
    )
    faces = [face]
    snapshot = selection.ResolutionSnapshot(digest, body, frame, faces)
    faces.clear()

    assert geometry.value == "geometry-v1"
    assert snapshot.to_dict()["faces"][0]["area"] == {"value": 2.0e-6, "unit": "m2"}
    with pytest.raises(FrozenInstanceError):
        snapshot.faces = ()

    mismatched = selection.FaceMeasurement(
        spatial.FaceId("face-B"),
        Quantity(1, "mm2"),
        spatial.Point3(
            spatial.FrameId("Other"), Quantity(0, "mm"), Quantity(0, "mm"), Quantity(0, "mm")
        ),
    )
    with pytest.raises(ValueError):
        selection.ResolutionSnapshot(digest, body, frame, [mismatched])


def test_selection_rejects_mismatching_rule_or_resolution_context() -> None:
    selection = _selection()
    spatial = _spatial()
    _, body, frame, digest = _context()
    other_body = spatial.BodyId("body-B")
    rule = selection.WholeBodyRule(other_body)

    with pytest.raises(ValueError):
        selection.SelectionRef(
            name="bad-body",
            role="support_surface",
            role_evidence=_evidence(),
            geometry_digest=digest,
            body_id=body,
            frame=frame,
            rule=rule,
        )

    snapshot = selection.ResolutionSnapshot(
        digest,
        body,
        frame,
        [
            selection.FaceMeasurement(
                spatial.FaceId("face-A"),
                Quantity(1, "mm2"),
                spatial.Point3(frame, Quantity(0, "mm"), Quantity(0, "mm"), Quantity(0, "mm")),
            )
        ],
    )
    with pytest.raises(ValueError):
        selection.SelectionRef(
            name="bad-resolution",
            role="support_surface",
            role_evidence=_evidence(),
            geometry_digest="c" * 64,
            body_id=body,
            frame=frame,
            rule=selection.WholeBodyRule(body),
            resolution=snapshot,
        )


def test_whole_body_rule_is_explicit_and_not_an_implicit_default() -> None:
    selection = _selection()
    _, body, frame, digest = _context()
    rule = selection.WholeBodyRule(body)
    reference = selection.SelectionRef(
        name="whole-body-selection",
        role="analysis_body",
        role_evidence=_evidence("e"),
        geometry_digest=digest,
        body_id=body,
        frame=frame,
        rule=rule,
    )

    assert reference.to_dict()["rule"] == {
        "schema_version": "1",
        "kind": "whole_body",
        "body_id": "body-A",
    }
