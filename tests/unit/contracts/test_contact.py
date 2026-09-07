from __future__ import annotations

import importlib
from dataclasses import FrozenInstanceError
from types import ModuleType
from typing import Any

import pytest

from febio_cae.domain import EvidenceRef, Quantity, UnitDirection, canonical_bytes
from febio_cae.domain.spatial import BodyId, FaceId, FrameId


def _optional_module(name: str) -> ModuleType | None:
    try:
        return importlib.import_module(name)
    except (ImportError, ModuleNotFoundError):
        return None


CONTACT_MODULE = _optional_module("febio_cae.domain.contact")
SELECTION_MODULE = _optional_module("febio_cae.domain.selection")


def _contact() -> ModuleType:
    if CONTACT_MODULE is None or SELECTION_MODULE is None:
        pytest.skip("contact API availability is covered by the dedicated assertion")
    return CONTACT_MODULE


def _evidence(target_field: str, seed: str = "a") -> EvidenceRef:
    return EvidenceRef(
        schema_version="1",
        source_kind="user_instruction",
        reference="User:contact-contract",
        target_field=target_field,
        content_digest=seed * 64,
    )


def _selection(
    *,
    role: str,
    body: BodyId | None = None,
    frame: FrameId | None = None,
    rule: Any = None,
) -> Any:
    selection = SELECTION_MODULE
    assert selection is not None
    body = BodyId("part-body") if body is None else body
    frame = FrameId("ContactFrame") if frame is None else frame
    return selection.SelectionRef(
        name=f"{role}-selection",
        role=role,
        role_evidence=_evidence("selection.role", "a"),
        geometry_digest="a" * 64,
        body_id=body,
        frame=frame,
        rule=selection.WholeBodyRule(body) if rule is None else rule,
    )


def _frictionless(contact: ModuleType) -> Any:
    return contact.Frictionless(model_evidence=_evidence("contact.friction_model", "d"))


def _coulomb(
    contact: ModuleType,
    *,
    coefficient: Quantity | None = None,
    model_evidence: EvidenceRef | None = None,
    coefficient_evidence: EvidenceRef | None = None,
) -> Any:
    return contact.CoulombFriction(
        coefficient=Quantity(0.25, "1") if coefficient is None else coefficient,
        model_evidence=(
            _evidence("contact.friction_model", "d") if model_evidence is None else model_evidence
        ),
        coefficient_evidence=(
            _evidence("contact.friction_coefficient", "e")
            if coefficient_evidence is None
            else coefficient_evidence
        ),
    )


def _as_placed(contact: ModuleType) -> Any:
    return contact.AsPlaced(arrangement_evidence=_evidence("contact.arrangement", "f"))


def _specified_gap(
    contact: ModuleType,
    *,
    gap: Quantity | None = None,
    direction: UnitDirection | None = None,
    arrangement_evidence: EvidenceRef | None = None,
    gap_evidence: EvidenceRef | None = None,
    direction_evidence: EvidenceRef | None = None,
) -> Any:
    frame = FrameId("ContactFrame") if direction is None else direction.frame
    return contact.SpecifiedGap(
        gap=Quantity(2, "mm") if gap is None else gap,
        direction=UnitDirection(frame, 0, 0, 1) if direction is None else direction,
        arrangement_evidence=(
            _evidence("contact.arrangement", "f")
            if arrangement_evidence is None
            else arrangement_evidence
        ),
        gap_evidence=(_evidence("contact.gap", "g") if gap_evidence is None else gap_evidence),
        direction_evidence=(
            _evidence("contact.direction", "h")
            if direction_evidence is None
            else direction_evidence
        ),
    )


def _intent_kwargs(
    contact: ModuleType,
    *,
    contact_id: Any = None,
    part_surface: Any = None,
    tool_surface: Any = None,
    pair_frame: FrameId | None = None,
    part_surface_evidence: EvidenceRef | None = None,
    tool_surface_evidence: EvidenceRef | None = None,
    pair_frame_evidence: EvidenceRef | None = None,
    friction: Any = None,
    arrangement: Any = None,
) -> dict[str, Any]:
    frame = FrameId("ContactFrame") if pair_frame is None else pair_frame
    return {
        "contact_id": contact.ContactId("contact-A") if contact_id is None else contact_id,
        "part_surface": (
            _selection(role="part_contact_surface", body=BodyId("part-body"), frame=frame)
            if part_surface is None
            else part_surface
        ),
        "tool_surface": (
            _selection(role="tool_contact_surface", body=BodyId("tool-body"), frame=frame)
            if tool_surface is None
            else tool_surface
        ),
        "pair_frame": frame,
        "part_surface_evidence": (
            _evidence("contact.part_surface", "b")
            if part_surface_evidence is None
            else part_surface_evidence
        ),
        "tool_surface_evidence": (
            _evidence("contact.tool_surface", "c")
            if tool_surface_evidence is None
            else tool_surface_evidence
        ),
        "pair_frame_evidence": (
            _evidence("contact.frame", "i") if pair_frame_evidence is None else pair_frame_evidence
        ),
        "friction": _frictionless(contact) if friction is None else friction,
        "arrangement": _as_placed(contact) if arrangement is None else arrangement,
    }


def _intent(contact: ModuleType, **overrides: Any) -> Any:
    values = _intent_kwargs(contact)
    values.update(overrides)
    return contact.ContactIntent(**values)


def test_contact_api_is_available() -> None:
    assert CONTACT_MODULE is not None, "P1-B4 contact module is not available"
    assert SELECTION_MODULE is not None, "P1-B4 selection dependency is not available"
    for name in (
        "SCHEMA_VERSION",
        "ContactId",
        "ContactIntent",
        "ContactValidationError",
        "Frictionless",
        "CoulombFriction",
        "AsPlaced",
        "SpecifiedGap",
    ):
        assert getattr(CONTACT_MODULE, name, None) is not None, name


def test_contact_preserves_explicit_pair_roles_frames_evidence_and_canonical_bytes() -> None:
    contact = _contact()
    value = _intent(contact)
    payload = value.to_dict()

    assert payload["schema_version"] == "1"
    assert payload["contact_id"] == "contact-A"
    assert payload["pair_frame"] == "ContactFrame"
    assert payload["part_surface"]["stated_role"] == "part_contact_surface"
    assert payload["tool_surface"]["stated_role"] == "tool_contact_surface"
    assert payload["part_surface"]["body_id"] == "part-body"
    assert payload["tool_surface"]["body_id"] == "tool-body"
    assert payload["part_surface_evidence"]["target_field"] == "contact.part_surface"
    assert payload["tool_surface_evidence"]["target_field"] == "contact.tool_surface"
    assert payload["pair_frame_evidence"]["target_field"] == "contact.frame"
    assert payload["friction"]["kind"] == "frictionless"
    assert payload["arrangement"]["kind"] == "as_placed"
    assert "placement" not in payload
    assert "primary" not in payload
    assert value.to_bytes() == canonical_bytes(payload)


def test_contact_allows_same_source_geometry_digest_but_rejects_body_collision() -> None:
    contact = _contact()
    value = _intent(contact)
    assert value.part_surface.source_geometry_digest == value.tool_surface.source_geometry_digest

    with pytest.raises(ValueError):
        _intent(
            contact,
            tool_surface=_selection(
                role="tool_contact_surface",
                body=BodyId("part-body"),
            ),
        )


def test_contact_requires_exact_part_and_tool_roles_and_matching_pair_frame() -> None:
    contact = _contact()
    with pytest.raises(ValueError):
        _intent(
            contact,
            part_surface=_selection(role="contact_surface", body=BodyId("part-body")),
        )
    with pytest.raises(ValueError):
        _intent(
            contact,
            part_surface=_selection(role="tool_contact_surface", body=BodyId("part-body")),
        )
    with pytest.raises(ValueError):
        _intent(
            contact,
            tool_surface=_selection(role="part_contact_surface", body=BodyId("tool-body")),
        )
    with pytest.raises(ValueError):
        _intent(
            contact,
            part_surface=_selection(
                role="part_contact_surface",
                body=BodyId("part-body"),
                frame=FrameId("OtherFrame"),
            ),
        )


@pytest.mark.parametrize(
    ("field", "wrong_target"),
    [
        ("part_surface_evidence", "contact.tool_surface"),
        ("tool_surface_evidence", "contact.part_surface"),
        ("pair_frame_evidence", "contact.arrangement"),
    ],
)
def test_contact_pair_and_frame_evidence_are_field_bound(
    field: str,
    wrong_target: str,
) -> None:
    contact = _contact()
    with pytest.raises(ValueError):
        _intent(contact, **{field: _evidence(wrong_target, "z")})


def test_contact_friction_forms_preserve_frictionless_and_coulomb_zero_distinction() -> None:
    contact = _contact()
    frictionless = _intent(contact)
    zero_coulomb = _intent(contact, friction=_coulomb(contact, coefficient=Quantity(0, "1")))

    assert frictionless.to_dict()["friction"] == {
        "schema_version": "1",
        "kind": "frictionless",
        "model_evidence": _evidence("contact.friction_model", "d").to_dict(),
    }
    assert zero_coulomb.to_dict()["friction"]["kind"] == "coulomb"
    assert zero_coulomb.to_dict()["friction"]["coefficient"] == {"value": 0.0, "unit": "1"}
    assert frictionless.to_bytes() != zero_coulomb.to_bytes()


def test_contact_coulomb_requires_dimensionless_nonnegative_coefficient_and_bound_evidence() -> (
    None
):
    contact = _contact()
    for coefficient in (
        Quantity(-1, "1"),
        Quantity(1, "mm"),
        "0.2",
        True,
        None,
    ):
        with pytest.raises((TypeError, ValueError)):
            _coulomb(contact, coefficient=coefficient)  # type: ignore[arg-type]

    with pytest.raises(ValueError):
        _coulomb(contact, model_evidence=_evidence("contact.friction_coefficient", "z"))
    with pytest.raises(ValueError):
        _coulomb(contact, coefficient_evidence=_evidence("contact.friction_model", "z"))
    with pytest.raises((TypeError, ValueError)):
        _intent(contact, friction="penalty")


def test_contact_friction_requires_explicit_model_evidence() -> None:
    contact = _contact()
    with pytest.raises(TypeError):
        contact.Frictionless()  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        contact.CoulombFriction(coefficient=Quantity(0.2, "1"))  # type: ignore[call-arg]


@pytest.mark.parametrize("gap", [Quantity(2, "mm"), Quantity(0, "mm"), Quantity(-2, "mm")])
def test_contact_specified_gap_preserves_signed_intent_and_direction_from_part_to_tool(
    gap: Quantity,
) -> None:
    contact = _contact()
    arrangement = _specified_gap(contact, gap=gap)
    value = _intent(contact, arrangement=arrangement)
    payload = value.to_dict()["arrangement"]

    assert payload["kind"] == "specified_gap"
    assert payload["gap"] == {"value": gap.to_si().value, "unit": "m"}
    assert payload["direction"]["frame"] == "ContactFrame"
    assert payload["arrangement_evidence"]["target_field"] == "contact.arrangement"
    assert payload["gap_evidence"]["target_field"] == "contact.gap"
    assert payload["direction_evidence"]["target_field"] == "contact.direction"


def test_contact_as_placed_requires_arrangement_evidence_and_has_no_gap_or_direction() -> None:
    contact = _contact()
    payload = _intent(contact).to_dict()["arrangement"]
    assert payload["kind"] == "as_placed"
    assert payload["arrangement_evidence"]["target_field"] == "contact.arrangement"
    assert "gap" not in payload
    assert "direction" not in payload

    with pytest.raises(TypeError):
        contact.AsPlaced()  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        contact.AsPlaced(arrangement_evidence=_evidence("contact.gap", "z"))


@pytest.mark.parametrize(
    ("field", "wrong_target"),
    [
        ("arrangement_evidence", "contact.gap"),
        ("gap_evidence", "contact.direction"),
        ("direction_evidence", "contact.arrangement"),
    ],
)
def test_contact_specified_gap_evidence_is_field_bound(field: str, wrong_target: str) -> None:
    contact = _contact()
    kwargs: dict[str, Any] = {field: _evidence(wrong_target, "z")}
    with pytest.raises(ValueError):
        _specified_gap(contact, **kwargs)


def test_contact_specified_gap_direction_must_use_pair_frame() -> None:
    contact = _contact()
    with pytest.raises(ValueError):
        _intent(
            contact,
            arrangement=_specified_gap(
                contact,
                direction=UnitDirection(FrameId("OtherFrame"), 0, 0, 1),
            ),
        )


def test_contact_rejects_unrepresentable_gap_and_coefficient_at_construction() -> None:
    contact = _contact()
    with pytest.raises(ValueError, match="outside finite range|underflows"):
        _intent(
            contact,
            arrangement=_specified_gap(contact, gap=Quantity(10**400, "m")),
        )
    with pytest.raises(ValueError, match="outside finite range|underflows"):
        _intent(
            contact,
            arrangement=_specified_gap(contact, gap=Quantity(5e-324, "mm")),
        )
    with pytest.raises(ValueError, match="outside finite range|underflows"):
        _intent(contact, friction=_coulomb(contact, coefficient=Quantity(10**400, "1")))


def test_contact_rejects_nested_selection_range_errors_at_construction() -> None:
    contact = _contact()
    selection = SELECTION_MODULE
    assert selection is not None
    frame = FrameId("ContactFrame")
    body = BodyId("part-body")
    bad_rule = selection.CoordinatePredicateRule(
        body,
        frame,
        [
            selection.CoordinatePredicate(
                UnitDirection(frame, 1, 0, 0),
                "eq",
                Quantity(10**400, "m"),
            )
        ],
    )
    with pytest.raises(ValueError, match="outside finite range|underflows"):
        _intent(
            contact,
            part_surface=_selection(
                role="part_contact_surface",
                body=body,
                frame=frame,
                rule=bad_rule,
            ),
        )


def test_contact_composes_selection_semantic_set_canonicalization() -> None:
    contact = _contact()
    selection = SELECTION_MODULE
    assert selection is not None
    frame = FrameId("ContactFrame")
    body = BodyId("part-body")

    def face_selection(face_ids: list[str]) -> Any:
        rule = selection.FaceSetRule(
            geometry_digest="a" * 64,
            body_id=body,
            frame=frame,
            face_ids=[FaceId(face_id) for face_id in face_ids],
            provenance=_evidence("selection.face_set", "j"),
        )
        return _selection(
            role="part_contact_surface",
            body=body,
            frame=frame,
            rule=rule,
        )

    first = _intent(contact, part_surface=face_selection(["face-a", "face-b"]))
    second = _intent(contact, part_surface=face_selection(["face-b", "face-a"]))
    assert first.to_bytes() == second.to_bytes()


def test_contact_copies_nested_face_collections_and_is_immutable() -> None:
    contact = _contact()
    selection = SELECTION_MODULE
    assert selection is not None
    frame = FrameId("ContactFrame")
    body = BodyId("part-body")
    faces = [FaceId("face-a"), FaceId("face-b")]
    rule = selection.FaceSetRule(
        geometry_digest="a" * 64,
        body_id=body,
        frame=frame,
        face_ids=faces,
        provenance=_evidence("selection.face_set", "j"),
    )
    value = _intent(
        contact,
        part_surface=_selection(
            role="part_contact_surface",
            body=body,
            frame=frame,
            rule=rule,
        ),
    )
    before = value.to_bytes()
    faces.clear()

    assert value.to_bytes() == before
    with pytest.raises(FrozenInstanceError):
        value.contact_id = contact.ContactId("other")


def test_contact_rejects_unknown_fields_and_has_no_partial_decoder() -> None:
    contact = _contact()
    values = _intent_kwargs(contact)
    values["unexpected"] = 1
    with pytest.raises(TypeError):
        contact.ContactIntent(**values)
    assert not hasattr(contact.ContactIntent, "from_dict")


def test_contact_bytes_change_for_identity_evidence_and_pair_frame_changes() -> None:
    contact = _contact()
    original = _intent(contact)
    changed_identity = _intent(contact, contact_id=contact.ContactId("contact-B"))
    changed_evidence = _intent(
        contact,
        part_surface_evidence=_evidence("contact.part_surface", "z"),
    )
    other_frame = FrameId("OtherFrame")
    changed_frame = _intent(
        contact,
        pair_frame=other_frame,
        part_surface=_selection(
            role="part_contact_surface",
            body=BodyId("part-body"),
            frame=other_frame,
        ),
        tool_surface=_selection(
            role="tool_contact_surface",
            body=BodyId("tool-body"),
            frame=other_frame,
        ),
    )

    assert original.to_bytes() != changed_identity.to_bytes()
    assert original.to_bytes() != changed_evidence.to_bytes()
    assert original.to_bytes() != changed_frame.to_bytes()
