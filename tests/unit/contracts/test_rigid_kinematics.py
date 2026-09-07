from __future__ import annotations

import importlib
from dataclasses import FrozenInstanceError
from types import ModuleType
from typing import Any

import pytest

from febio_cae.domain import (
    BodyId,
    EvidenceRef,
    FaceId,
    FrameId,
    MotionApplicability,
    MotionProfile,
    MotionSample,
    Point3,
    ProperRotation,
    Quantity,
    RigidPrimitive,
    RigidTransform,
    Translation3,
    UnitDirection,
    canonical_bytes,
)


def _optional_module(name: str) -> ModuleType | None:
    try:
        return importlib.import_module(name)
    except (ImportError, ModuleNotFoundError):
        return None


KINEMATICS_MODULE = _optional_module("febio_cae.domain.rigid_kinematics")
SELECTION_MODULE = _optional_module("febio_cae.domain.selection")


def _kinematics() -> ModuleType:
    if KINEMATICS_MODULE is None or SELECTION_MODULE is None:
        pytest.skip("rigid kinematics API availability is covered by the dedicated assertion")
    return KINEMATICS_MODULE


def _evidence(target_field: str, seed: str = "a") -> EvidenceRef:
    return EvidenceRef(
        schema_version="1",
        source_kind="user_instruction",
        reference="User:rigid-kinematics-contract",
        target_field=target_field,
        content_digest=seed * 64,
    )


def _primitive(*, body: BodyId | None = None, target: FrameId | None = None) -> RigidPrimitive:
    body = BodyId("tool-body") if body is None else body
    local = FrameId("ToolLocal")
    target = FrameId("World") if target is None else target
    return RigidPrimitive(
        kind="sphere",
        body_id=body,
        local_frame=local,
        placement=RigidTransform(
            source_frame=local,
            target_frame=target,
            translation=Translation3(
                target,
                Quantity(12, "mm"),
                Quantity(-3, "mm"),
                Quantity(8, "mm"),
            ),
            rotation=ProperRotation(((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))),
        ),
        dimensions={"radius": Quantity(10, "mm")},
        dimension_evidence={"radius": _evidence("rigid_tool.radius", "b")},
        model_evidence=_evidence("rigid_tool.model", "c"),
        placement_evidence=_evidence("rigid_tool.placement", "d"),
    )


def _selection(
    *,
    role: str = "tool_contact_surface",
    body: BodyId | None = None,
    frame: FrameId | None = None,
    geometry_digest: str = "e" * 64,
    rule: Any = None,
) -> Any:
    selection = SELECTION_MODULE
    assert selection is not None
    body = BodyId("tool-body") if body is None else body
    frame = FrameId("World") if frame is None else frame
    return selection.SelectionRef(
        name="tool-contact-selection",
        role=role,
        role_evidence=_evidence("selection.role", "f"),
        geometry_digest=geometry_digest,
        body_id=body,
        frame=frame,
        rule=selection.WholeBodyRule(body) if rule is None else rule,
    )


def _component(
    rigid: ModuleType,
    axis: str,
    state: str = "fixed",
    seed: str = "a",
) -> Any:
    return rigid.RigidDofComponent(state, _evidence(f"rigid_tool.{axis}", seed))


def _dofs(
    rigid: ModuleType,
    *,
    frame: FrameId | None = None,
    states: dict[str, str] | None = None,
    frame_evidence: EvidenceRef | None = None,
) -> Any:
    frame = FrameId("World") if frame is None else frame
    states = {} if states is None else states
    return rigid.RigidDofSpecification(
        frame=frame,
        x=_component(rigid, "x", states.get("x", "fixed"), "a"),
        y=_component(rigid, "y", states.get("y", "fixed"), "b"),
        z=_component(rigid, "z", states.get("z", "fixed"), "c"),
        rx=_component(rigid, "rx", states.get("rx", "fixed"), "d"),
        ry=_component(rigid, "ry", states.get("ry", "fixed"), "e"),
        rz=_component(rigid, "rz", states.get("rz", "fixed"), "f"),
        frame_evidence=(
            _evidence("rigid_tool.frame", "7") if frame_evidence is None else frame_evidence
        ),
    )


def _tool(
    rigid: ModuleType,
    *,
    primitive: RigidPrimitive | None = None,
    contact_surface: Any = None,
    dofs: Any = None,
    contact_surface_evidence: EvidenceRef | None = None,
) -> Any:
    primitive = _primitive() if primitive is None else primitive
    contact_surface = (
        _selection(frame=primitive.placement.target_frame, body=primitive.body_id)
        if contact_surface is None
        else contact_surface
    )
    dofs = _dofs(rigid, frame=primitive.placement.target_frame) if dofs is None else dofs
    return rigid.RigidToolIntent(
        primitive=primitive,
        contact_surface=contact_surface,
        dofs=dofs,
        contact_surface_evidence=(
            _evidence("rigid_tool.contact_surface", "8")
            if contact_surface_evidence is None
            else contact_surface_evidence
        ),
    )


def _motion(
    *,
    direction: tuple[float, float, float] = (1.0, 0.0, 0.0),
    frame: FrameId | None = None,
    reference: Point3 | None = None,
) -> MotionProfile:
    frame = FrameId("World") if frame is None else frame
    return MotionProfile(
        direction=UnitDirection(frame, *direction),
        initial_reference_point=(
            Point3(
                frame,
                Quantity(100, "mm"),
                Quantity(-20, "mm"),
                Quantity(40, "mm"),
            )
            if reference is None
            else reference
        ),
        samples=(
            MotionSample(Quantity(0, "s"), Quantity(0, "mm")),
            MotionSample(Quantity(1, "s"), Quantity(2, "mm")),
        ),
        applicability=MotionApplicability(
            quasi_static_statement="user states quasi-static loading",
            quasi_static_evidence=_evidence("motion.quasi_static_applicability", "9"),
            rate_independent_statement="user states rate-independent response",
            rate_independent_evidence=_evidence("motion.rate_independent_applicability", "a"),
        ),
        direction_evidence=_evidence("motion.direction", "b"),
        initial_reference_point_evidence=_evidence("motion.initial_reference_point", "c"),
        history_evidence=_evidence("motion.history", "d"),
    )


def test_rigid_kinematics_api_is_available() -> None:
    assert KINEMATICS_MODULE is not None, "P1-B5 rigid kinematics module is not available"
    assert SELECTION_MODULE is not None, "P1-B5 selection dependency is not available"
    for name in (
        "SCHEMA_VERSION",
        "RigidDofComponent",
        "RigidDofSpecification",
        "RigidToolIntent",
        "RigidKinematicsValidationError",
        "KinematicCompatibility",
        "check_translational_indentation_compatibility",
    ):
        assert getattr(KINEMATICS_MODULE, name, None) is not None, name


def test_dof_specification_preserves_all_six_states_frame_evidence_and_canonical_bytes() -> None:
    rigid = _kinematics()
    value = _dofs(
        rigid,
        states={"x": "prescribed", "y": "free", "rx": "prescribed"},
    )
    payload = value.to_dict()

    assert payload["schema_version"] == "1"
    assert payload["frame"] == "World"
    assert payload["frame_evidence"]["target_field"] == "rigid_tool.frame"
    assert payload["x"]["state"] == "prescribed"
    assert payload["y"]["state"] == "free"
    assert payload["z"]["state"] == "fixed"
    assert payload["rx"]["state"] == "prescribed"
    assert payload["ry"]["state"] == "fixed"
    assert payload["rz"]["state"] == "fixed"
    assert payload["x"]["evidence"]["target_field"] == "rigid_tool.x"
    assert payload["rx"]["evidence"]["target_field"] == "rigid_tool.rx"
    assert value.to_bytes() == canonical_bytes(payload)


def test_dof_fixed_free_and_prescribed_are_distinct_explicit_intents() -> None:
    rigid = _kinematics()
    fixed = _dofs(rigid, states={"x": "fixed"})
    free = _dofs(rigid, states={"x": "free"})
    prescribed = _dofs(rigid, states={"x": "prescribed"})

    assert fixed.x.state == "fixed"
    assert free.x.state == "free"
    assert prescribed.x.state == "prescribed"
    assert len({fixed.to_bytes(), free.to_bytes(), prescribed.to_bytes()}) == 3


@pytest.mark.parametrize("state", ["", "fixed-at-zero", "free-ish", "prescribe", True, None])
def test_dof_component_rejects_unknown_states(state: object) -> None:
    rigid = _kinematics()
    with pytest.raises(ValueError):
        rigid.RigidDofComponent(state, _evidence("rigid_tool.x"))


@pytest.mark.parametrize(
    ("axis", "wrong_target"),
    [
        ("x", "rigid_tool.y"),
        ("y", "rigid_tool.z"),
        ("z", "rigid_tool.rx"),
        ("rx", "rigid_tool.ry"),
        ("ry", "rigid_tool.rz"),
        ("rz", "rigid_tool.x"),
    ],
)
def test_dof_evidence_is_bound_to_each_exact_component(axis: str, wrong_target: str) -> None:
    rigid = _kinematics()
    values = {
        "frame": FrameId("World"),
        "x": _component(rigid, "x"),
        "y": _component(rigid, "y"),
        "z": _component(rigid, "z"),
        "rx": _component(rigid, "rx"),
        "ry": _component(rigid, "ry"),
        "rz": _component(rigid, "rz"),
        "frame_evidence": _evidence("rigid_tool.frame"),
    }
    values[axis] = rigid.RigidDofComponent("fixed", _evidence(wrong_target, "7"))
    with pytest.raises(ValueError):
        rigid.RigidDofSpecification(**values)


def test_dof_component_wrong_target_is_rejected_when_bound_to_specification_axis() -> None:
    rigid = _kinematics()
    components = {
        axis: rigid.RigidDofComponent("fixed", _evidence("rigid_tool.x"))
        for axis in ("x", "y", "z", "rx", "ry", "rz")
    }
    with pytest.raises(ValueError, match="y.*rigid_tool.y"):
        rigid.RigidDofSpecification(
            frame=FrameId("World"),
            x=components["x"],
            y=components["y"],
            z=components["z"],
            rx=components["rx"],
            ry=components["ry"],
            rz=components["rz"],
            frame_evidence=_evidence("rigid_tool.frame"),
        )


def test_dof_frame_evidence_and_frame_are_required_and_strict() -> None:
    rigid = _kinematics()
    values = {
        "frame": FrameId("World"),
        "x": _component(rigid, "x"),
        "y": _component(rigid, "y"),
        "z": _component(rigid, "z"),
        "rx": _component(rigid, "rx"),
        "ry": _component(rigid, "ry"),
        "rz": _component(rigid, "rz"),
        "frame_evidence": _evidence("rigid_tool.frame"),
    }
    with pytest.raises(TypeError):
        values.pop("frame_evidence")
        rigid.RigidDofSpecification(**values)
    values["frame_evidence"] = _evidence("rigid_tool.x")
    with pytest.raises(ValueError):
        rigid.RigidDofSpecification(**values)
    values["frame_evidence"] = _evidence("rigid_tool.frame")
    with pytest.raises(TypeError):
        rigid.RigidDofSpecification(**values, unexpected=True)


def test_rigid_tool_composes_primitive_contact_selection_and_dofs_in_common_target_frame() -> None:
    rigid = _kinematics()
    value = _tool(rigid)
    payload = value.to_dict()

    assert payload["schema_version"] == "1"
    assert payload["primitive"]["body_id"] == "tool-body"
    assert payload["contact_surface"]["stated_role"] == "tool_contact_surface"
    assert payload["contact_surface"]["body_id"] == "tool-body"
    assert payload["contact_surface"]["geometry_digest"] == "e" * 64
    assert payload["dofs"]["frame"] == "World"
    assert payload["contact_surface_evidence"]["target_field"] == "rigid_tool.contact_surface"
    assert "motion" not in payload
    assert value.to_bytes() == canonical_bytes(payload)


@pytest.mark.parametrize(
    "bad_surface",
    [
        _selection(role="support_surface"),
        _selection(body=BodyId("other-body")),
        _selection(frame=FrameId("OtherFrame")),
    ],
    ids=["wrong-role", "wrong-body", "wrong-frame"],
)
def test_rigid_tool_rejects_contact_selection_role_body_and_frame_mismatches(
    bad_surface: Any,
) -> None:
    rigid = _kinematics()
    with pytest.raises(ValueError):
        _tool(rigid, contact_surface=bad_surface)


def test_rigid_tool_rejects_dof_frame_mismatch_and_wrong_selection_assignment_evidence() -> None:
    rigid = _kinematics()
    with pytest.raises(ValueError):
        _tool(rigid, dofs=_dofs(rigid, frame=FrameId("OtherFrame")))
    with pytest.raises(ValueError):
        _tool(rigid, contact_surface_evidence=_evidence("rigid_tool.frame"))


def test_rigid_tool_does_not_invent_geometry_digest_from_primitive_bytes() -> None:
    rigid = _kinematics()
    selection = _selection(geometry_digest="f" * 64)
    value = _tool(rigid, contact_surface=selection)

    assert value.contact_surface.source_geometry_digest == "f" * 64
    assert value.to_dict()["contact_surface"]["geometry_digest"] == "f" * 64
    assert value.primitive.to_bytes() != selection.to_bytes()


def test_rigid_tool_preserves_nested_selection_semantic_set_canonicalization() -> None:
    rigid = _kinematics()
    selection = SELECTION_MODULE
    assert selection is not None
    frame = FrameId("World")
    body = BodyId("tool-body")
    face_a = selection.FaceMeasurement(
        face_id=FaceId("face-A"),
        area=Quantity(1, "m2"),
        centroid=Point3(frame, Quantity(0, "m"), Quantity(0, "m"), Quantity(0, "m")),
    )
    face_b = selection.FaceMeasurement(
        face_id=FaceId("face-B"),
        area=Quantity(2, "m2"),
        centroid=Point3(frame, Quantity(1, "m"), Quantity(0, "m"), Quantity(0, "m")),
    )
    first = _selection(
        body=body,
        frame=frame,
        rule=selection.FaceSetRule(
            "e" * 64,
            body,
            frame,
            [face_a.face_id, face_b.face_id],
            _evidence("selection.faces", "a"),
        ),
    )
    second = _selection(
        body=body,
        frame=frame,
        rule=selection.FaceSetRule(
            "e" * 64,
            body,
            frame,
            [face_b.face_id, face_a.face_id],
            _evidence("selection.faces", "a"),
        ),
    )
    first_tool = _tool(rigid, contact_surface=first)
    second_tool = _tool(rigid, contact_surface=second)

    assert first.to_bytes() == second.to_bytes()
    assert first_tool.to_bytes() == second_tool.to_bytes()


def test_rigid_tool_is_immutable_and_dof_input_is_not_rewritten() -> None:
    rigid = _kinematics()
    dofs = _dofs(rigid, states={"x": "prescribed", "y": "free"})
    value = _tool(rigid, dofs=dofs)

    with pytest.raises(FrozenInstanceError):
        value.dofs = _dofs(rigid)  # type: ignore[misc]
    assert value.dofs.x.state == "prescribed"
    assert value.dofs.y.state == "free"


def test_rigid_tool_forces_nested_canonical_totality_at_construction() -> None:
    rigid = _kinematics()
    bad_primitive = _primitive()
    object.__setattr__(bad_primitive, "dimensions", {"radius": Quantity(10**400, "m")})
    with pytest.raises(ValueError, match="outside finite range|underflows"):
        _tool(rigid, primitive=bad_primitive)


def test_check_axis_aligned_indentation_accepts_prescribed_nonzero_and_fixed_zero_components() -> (
    None
):
    rigid = _kinematics()
    tool = _tool(rigid, dofs=_dofs(rigid, states={"x": "prescribed"}))
    result = rigid.check_translational_indentation_compatibility(tool, _motion())

    assert result.supported is True
    assert result.reason is None


def test_check_oblique_indentation_accepts_multiple_prescribed_global_components() -> None:
    rigid = _kinematics()
    tool = _tool(
        rigid,
        dofs=_dofs(rigid, states={"x": "prescribed", "y": "prescribed"}),
    )
    result = rigid.check_translational_indentation_compatibility(
        tool,
        _motion(direction=(1.0, 1.0, 0.0)),
    )

    assert result.supported is True
    assert result.reason is None


def test_check_zero_direction_component_may_be_explicitly_prescribed() -> None:
    rigid = _kinematics()
    tool = _tool(
        rigid,
        dofs=_dofs(rigid, states={"x": "prescribed", "y": "prescribed"}),
    )
    result = rigid.check_translational_indentation_compatibility(
        tool,
        _motion(direction=(0.0, 1.0, 0.0)),
    )

    assert result.supported is True


@pytest.mark.parametrize("axis", ["x", "y", "z"])
def test_check_nonzero_direction_requires_prescribed_translation(axis: str) -> None:
    rigid = _kinematics()
    tool = _tool(rigid, dofs=_dofs(rigid, states={axis: "fixed"}))
    direction = {"x": 0.0, "y": 0.0, "z": 0.0}
    direction[axis] = 1.0
    result = rigid.check_translational_indentation_compatibility(
        tool,
        _motion(direction=tuple(direction.values())),
    )

    assert result.supported is False
    assert result.reason is not None
    assert axis in result.reason
    assert "prescribed" in result.reason


@pytest.mark.parametrize("axis", ["x", "y", "z"])
def test_check_zero_direction_rejects_free_translation(axis: str) -> None:
    rigid = _kinematics()
    tool = _tool(rigid, dofs=_dofs(rigid, states={axis: "free", "x": "prescribed"}))
    direction = {"x": 1.0, "y": 0.0, "z": 0.0}
    direction[axis] = 0.0
    if axis == "x":
        direction["y"] = 1.0
        tool = _tool(rigid, dofs=_dofs(rigid, states={"x": "free", "y": "prescribed"}))
    result = rigid.check_translational_indentation_compatibility(
        tool,
        _motion(direction=tuple(direction.values())),
    )

    assert result.supported is False
    assert result.reason is not None
    assert axis in result.reason
    assert "free" in result.reason


@pytest.mark.parametrize("axis", ["x", "y", "z"])
def test_check_tiny_nonzero_direction_is_not_classified_as_zero(axis: str) -> None:
    rigid = _kinematics()
    direction = {"x": 1.0, "y": 1.0, "z": 1.0}
    direction[axis] = 1.0e-20
    states = {"x": "prescribed", "y": "prescribed", "z": "prescribed"}
    states[axis] = "fixed"
    tool = _tool(rigid, dofs=_dofs(rigid, states=states))
    result = rigid.check_translational_indentation_compatibility(
        tool,
        _motion(direction=tuple(direction.values())),
    )

    assert result.supported is False
    assert result.reason is not None
    assert axis in result.reason


@pytest.mark.parametrize("axis", ["rx", "ry", "rz"])
@pytest.mark.parametrize("state", ["free", "prescribed"])
def test_check_rejects_nonfixed_rotation_without_rewriting_the_dof_tag(
    axis: str,
    state: str,
) -> None:
    rigid = _kinematics()
    tool = _tool(rigid, dofs=_dofs(rigid, states={"x": "prescribed", axis: state}))
    before = tool.dofs.to_bytes()
    result = rigid.check_translational_indentation_compatibility(tool, _motion())

    assert result.supported is False
    assert result.reason is not None
    assert axis in result.reason
    assert tool.dofs.to_bytes() == before
    assert getattr(tool.dofs, axis).state == state


def test_check_rejects_motion_frame_mismatch_with_precise_reason() -> None:
    rigid = _kinematics()
    tool = _tool(rigid, dofs=_dofs(rigid, frame=FrameId("World")))
    result = rigid.check_translational_indentation_compatibility(
        tool,
        _motion(frame=FrameId("OtherFrame")),
    )

    assert result.supported is False
    assert result.reason == "motion frame must match rigid-tool DOF frame"


def test_check_allows_reference_marker_independent_of_primitive_translation() -> None:
    rigid = _kinematics()
    tool = _tool(rigid, dofs=_dofs(rigid, states={"x": "prescribed"}))
    reference = Point3(
        FrameId("World"),
        Quantity(-900, "mm"),
        Quantity(700, "mm"),
        Quantity(11, "mm"),
    )
    result = rigid.check_translational_indentation_compatibility(
        tool,
        _motion(reference=reference),
    )

    assert result.supported is True


def test_check_requires_typed_tool_and_motion_profile() -> None:
    rigid = _kinematics()
    with pytest.raises(TypeError):
        rigid.check_translational_indentation_compatibility(object(), _motion())
    with pytest.raises(TypeError):
        rigid.check_translational_indentation_compatibility(_tool(rigid), object())
