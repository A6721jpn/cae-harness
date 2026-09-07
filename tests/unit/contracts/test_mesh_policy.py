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
    FaceSetRule,
    FrameId,
    Quantity,
    SelectionRef,
    WholeBodyRule,
    canonical_bytes,
)


def _optional_module(name: str) -> ModuleType | None:
    try:
        return importlib.import_module(name)
    except (ImportError, ModuleNotFoundError):
        return None


MESH_MODULE = _optional_module("febio_cae.domain.mesh_policy")
_MISSING = object()


def _mesh() -> ModuleType:
    if MESH_MODULE is None:
        pytest.skip("mesh policy API availability is covered by the dedicated assertion")
    return MESH_MODULE


def _evidence(target_field: str, seed: str = "a") -> EvidenceRef:
    return EvidenceRef(
        schema_version="1",
        source_kind="user_instruction",
        reference="User:mesh-policy-contract",
        target_field=target_field,
        content_digest=seed * 64,
    )


def _selection(
    *,
    face_ids: tuple[str, ...] | None = None,
    geometry_digest: str = "d" * 64,
    body_id: BodyId | None = None,
    frame: FrameId | None = None,
) -> SelectionRef:
    body = BodyId("body-A") if body_id is None else body_id
    selected_frame = FrameId("World") if frame is None else frame
    if face_ids is None:
        rule: WholeBodyRule | FaceSetRule = WholeBodyRule(body)
    else:
        rule = FaceSetRule(
            geometry_digest=geometry_digest,
            body_id=body,
            frame=selected_frame,
            face_ids=tuple(FaceId(value) for value in face_ids),
            provenance=_evidence("selection.faces", "b"),
        )
    return SelectionRef(
        name="part-selection",
        role="part",
        role_evidence=_evidence("selection.role", "c"),
        geometry_digest=geometry_digest,
        body_id=body,
        frame=selected_frame,
        rule=rule,
    )


def _profile(
    *,
    profile_id: str = "mesh-quality-default",
    purpose: str = "mesh_quality",
    record_digest: str = "e" * 64,
) -> Any:
    mesh = _mesh()
    return mesh.NumericalProfileRef(
        profile_id=profile_id,
        purpose=purpose,
        record_digest=record_digest,
    )


def _local(
    mesh: ModuleType,
    *,
    refinement_id: str = "local-A",
    selection: object = _MISSING,
    size: object = _MISSING,
) -> Any:
    return mesh.LocalRefinement(
        refinement_id=refinement_id,
        selection=_selection() if selection is _MISSING else selection,
        size=Quantity(5, "mm") if size is _MISSING else size,
    )


def _policy(
    mesh: ModuleType,
    *,
    element_type: object = "tet10",
    global_size: object = Quantity(10, "mm"),
    local_refinements: object = _MISSING,
    quality_profile: object = _MISSING,
    max_refinements: object = 0,
) -> Any:
    return mesh.MeshPolicy(
        element_type=element_type,
        global_size=global_size,
        local_refinements=([_local(mesh)] if local_refinements is _MISSING else local_refinements),
        quality_profile=(_profile() if quality_profile is _MISSING else quality_profile),
        max_refinements=max_refinements,
    )


def test_mesh_policy_api_is_available() -> None:
    assert MESH_MODULE is not None, "P1-B8 mesh policy module is not available"
    for name in (
        "SCHEMA_VERSION",
        "NumericalProfileRef",
        "LocalRefinement",
        "MeshPolicy",
        "MeshPolicyValidationError",
    ):
        assert getattr(MESH_MODULE, name, None) is not None, name


def test_numerical_profile_preserves_fields_and_canonical_projection() -> None:
    _mesh()
    profile = _profile()
    payload = profile.to_dict()

    assert payload == {
        "schema_version": "1",
        "profile_id": "mesh-quality-default",
        "purpose": "mesh_quality",
        "record_digest": "e" * 64,
    }
    assert profile.to_bytes() == canonical_bytes(payload)


def test_numerical_profile_requires_all_fields_and_rejects_unknown_fields() -> None:
    mesh = _mesh()
    values: dict[str, object] = {
        "profile_id": "profile-A",
        "purpose": "mesh_quality",
        "record_digest": "a" * 64,
    }
    for missing in tuple(values):
        incomplete = values.copy()
        incomplete.pop(missing)
        with pytest.raises(TypeError):
            mesh.NumericalProfileRef(**incomplete)
    with pytest.raises(TypeError):
        mesh.NumericalProfileRef(**values, unexpected=True)


@pytest.mark.parametrize("purpose", ["mesh_quality", "solver", "outputs", "quality"])
def test_numerical_profile_accepts_only_declared_purposes(purpose: str) -> None:
    profile = _profile(purpose=purpose)
    assert profile.purpose == purpose


@pytest.mark.parametrize("bad_purpose", ["", "mesh", "solver_quality", None, 1, True])
def test_numerical_profile_rejects_unknown_or_wrong_purposes(bad_purpose: object) -> None:
    mesh = _mesh()
    with pytest.raises(ValueError):
        mesh.NumericalProfileRef(
            profile_id="profile-A",
            purpose=bad_purpose,
            record_digest="a" * 64,
        )


@pytest.mark.parametrize("bad_profile_id", ["", " profile", "profile ", None, 1, True, "p\x00q"])
def test_numerical_profile_requires_strict_identifier(bad_profile_id: object) -> None:
    mesh = _mesh()
    with pytest.raises(ValueError):
        mesh.NumericalProfileRef(
            profile_id=bad_profile_id,
            purpose="mesh_quality",
            record_digest="a" * 64,
        )


@pytest.mark.parametrize("bad_digest", ["", "not-a-digest", "A" * 64, "a" * 63, "a" * 65, None, 1])
def test_numerical_profile_requires_lowercase_record_digest(bad_digest: object) -> None:
    mesh = _mesh()
    with pytest.raises(ValueError):
        mesh.NumericalProfileRef(
            profile_id="profile-A",
            purpose="mesh_quality",
            record_digest=bad_digest,
        )


def test_numerical_profile_identity_changes_for_each_field() -> None:
    baseline = _profile()
    changed_id = _profile(profile_id="mesh-quality-other")
    changed_purpose = _profile(purpose="quality")
    changed_digest = _profile(record_digest="f" * 64)

    assert baseline.to_bytes() != changed_id.to_bytes()
    assert baseline.to_bytes() != changed_purpose.to_bytes()
    assert baseline.to_bytes() != changed_digest.to_bytes()


def test_local_refinement_preserves_selection_and_size_projection() -> None:
    mesh = _mesh()
    local = _local(mesh)
    payload = local.to_dict()

    assert payload["schema_version"] == "1"
    assert payload["refinement_id"] == "local-A"
    assert payload["selection"]["name"] == "part-selection"
    assert payload["selection"]["geometry_digest"] == "d" * 64
    assert payload["size"] == {"value": 0.005, "unit": "m"}
    assert local.to_bytes() == canonical_bytes(payload)


def test_local_refinement_requires_all_fields_and_rejects_unknown_fields() -> None:
    mesh = _mesh()
    values: dict[str, object] = {
        "refinement_id": "local-A",
        "selection": _selection(),
        "size": Quantity(5, "mm"),
    }
    for missing in tuple(values):
        incomplete = values.copy()
        incomplete.pop(missing)
        with pytest.raises(TypeError):
            mesh.LocalRefinement(**incomplete)
    with pytest.raises(TypeError):
        mesh.LocalRefinement(**values, unexpected=True)


@pytest.mark.parametrize("bad_id", ["", " local", "local ", None, 1, True, "l\x00x"])
def test_local_refinement_rejects_bad_identifiers(bad_id: object) -> None:
    mesh = _mesh()
    with pytest.raises(ValueError):
        _local(mesh, refinement_id=bad_id)


@pytest.mark.parametrize("bad_selection", [None, "selection", BodyId("body-A"), object()])
def test_local_refinement_requires_existing_selection_ref(bad_selection: object) -> None:
    mesh = _mesh()
    with pytest.raises(ValueError):
        _local(mesh, selection=bad_selection)


@pytest.mark.parametrize(
    "bad_size",
    [Quantity(0, "mm"), Quantity(-1, "mm"), Quantity(1, "s"), "5 mm", None, True],
)
def test_local_refinement_requires_positive_length_size(bad_size: object) -> None:
    mesh = _mesh()
    with pytest.raises(ValueError):
        _local(mesh, size=bad_size)


def test_local_refinement_selection_face_set_permutation_has_canonical_identity() -> None:
    mesh = _mesh()
    first = _local(mesh, selection=_selection(face_ids=("face-B", "face-A")))
    second = _local(mesh, selection=_selection(face_ids=("face-A", "face-B")))

    assert first.to_bytes() == second.to_bytes()


def test_local_refinement_selection_identity_changes_for_body_frame_and_geometry() -> None:
    mesh = _mesh()
    baseline = _local(mesh)
    changed_body = _local(mesh, selection=_selection(body_id=BodyId("body-B")))
    changed_frame = _local(mesh, selection=_selection(frame=FrameId("Other")))
    changed_geometry = _local(mesh, selection=_selection(geometry_digest="f" * 64))

    assert baseline.to_bytes() != changed_body.to_bytes()
    assert baseline.to_bytes() != changed_frame.to_bytes()
    assert baseline.to_bytes() != changed_geometry.to_bytes()


def test_mesh_policy_preserves_required_fields_and_canonical_projection() -> None:
    mesh = _mesh()
    value = _policy(mesh, max_refinements=2)
    payload = value.to_dict()

    assert payload["schema_version"] == "1"
    assert payload["element_type"] == "tet10"
    assert payload["global_size"] == {"value": 0.01, "unit": "m"}
    assert [item["refinement_id"] for item in payload["local_refinements"]] == ["local-A"]
    assert payload["quality_profile"]["purpose"] == "mesh_quality"
    assert payload["max_refinements"] == 2
    assert value.to_bytes() == canonical_bytes(payload)


def test_mesh_policy_requires_all_fields_and_rejects_unknown_fields() -> None:
    mesh = _mesh()
    values: dict[str, object] = {
        "element_type": "tet10",
        "global_size": Quantity(10, "mm"),
        "local_refinements": [],
        "quality_profile": _profile(),
        "max_refinements": 0,
    }
    for missing in tuple(values):
        incomplete = values.copy()
        incomplete.pop(missing)
        with pytest.raises(TypeError):
            mesh.MeshPolicy(**incomplete)
    with pytest.raises(TypeError):
        mesh.MeshPolicy(**values, unexpected=True)


@pytest.mark.parametrize("bad_element_type", ["", "tet4", "Tet10", None, 1, True])
def test_mesh_policy_requires_explicit_tet10_element_type(bad_element_type: object) -> None:
    mesh = _mesh()
    with pytest.raises(ValueError):
        _policy(mesh, element_type=bad_element_type)


@pytest.mark.parametrize(
    "bad_global_size",
    [Quantity(0, "mm"), Quantity(-1, "mm"), Quantity(1, "s"), "10 mm", None, True],
)
def test_mesh_policy_requires_positive_length_global_size(bad_global_size: object) -> None:
    mesh = _mesh()
    with pytest.raises(ValueError):
        _policy(mesh, global_size=bad_global_size)


@pytest.mark.parametrize("bad_locals", [None, "local", object(), [object()]])
def test_mesh_policy_requires_sequence_of_local_refinements(bad_locals: object) -> None:
    mesh = _mesh()
    with pytest.raises(ValueError):
        _policy(mesh, local_refinements=bad_locals)


@pytest.mark.parametrize("bad_count", [-1, -10, True, False, 1.0, 1.5, "1", None])
def test_mesh_policy_requires_nonnegative_strict_integer_refinement_cap(
    bad_count: object,
) -> None:
    mesh = _mesh()
    with pytest.raises(ValueError):
        _policy(mesh, max_refinements=bad_count)


def test_mesh_policy_accepts_explicit_zero_refinement_cap_and_empty_collection() -> None:
    mesh = _mesh()
    value = _policy(mesh, local_refinements=[], max_refinements=0)

    assert value.local_refinements == ()
    assert value.max_refinements == 0
    assert value.to_dict()["local_refinements"] == []


def test_mesh_policy_rejects_non_mesh_quality_profile_with_specific_error() -> None:
    mesh = _mesh()
    solver_profile = _profile(profile_id="solver-profile", purpose="solver")

    with pytest.raises(
        mesh.MeshPolicyValidationError,
        match="quality_profile must have mesh_quality purpose",
    ):
        _policy(mesh, quality_profile=solver_profile)


@pytest.mark.parametrize("bad_profile", [None, "profile", object()])
def test_mesh_policy_requires_typed_quality_profile(bad_profile: object) -> None:
    mesh = _mesh()
    with pytest.raises(ValueError):
        _policy(mesh, quality_profile=bad_profile)


def test_mesh_policy_requires_local_size_not_greater_than_global_in_si() -> None:
    mesh = _mesh()
    with pytest.raises(mesh.MeshPolicyValidationError, match="must not exceed global_size"):
        _policy(
            mesh,
            global_size=Quantity(10, "mm"),
            local_refinements=[_local(mesh, size=Quantity(20, "mm"))],
        )


def test_mesh_policy_accepts_equivalent_si_global_and_local_sizes() -> None:
    mesh = _mesh()
    displayed = _policy(
        mesh,
        global_size=Quantity(10, "mm"),
        local_refinements=[_local(mesh, size=Quantity(5, "mm"))],
    )
    si = _policy(
        mesh,
        global_size=Quantity(0.01, "m"),
        local_refinements=[_local(mesh, size=Quantity(0.005, "m"))],
    )

    assert displayed.to_bytes() == si.to_bytes()


def test_mesh_policy_local_refinements_are_sorted_semantic_set_and_immutable() -> None:
    mesh = _mesh()
    local_a = _local(mesh, refinement_id="local-A")
    local_b = _local(mesh, refinement_id="local-B", size=Quantity(4, "mm"))
    values = [local_b, local_a]
    policy = _policy(mesh, local_refinements=values)
    values.clear()

    assert [item.refinement_id for item in policy.local_refinements] == ["local-A", "local-B"]
    assert policy.to_bytes() == _policy(mesh, local_refinements=[local_a, local_b]).to_bytes()
    assert isinstance(policy.local_refinements, tuple)


def test_mesh_policy_rejects_duplicate_refinement_ids_even_when_identical() -> None:
    mesh = _mesh()
    first = _local(mesh, refinement_id="duplicate")
    second = _local(mesh, refinement_id="duplicate")

    with pytest.raises(mesh.MeshPolicyValidationError, match="duplicate refinement_id"):
        _policy(mesh, local_refinements=[first, second])


def test_mesh_policy_identity_changes_for_variable_top_level_fields() -> None:
    mesh = _mesh()
    baseline = _policy(mesh, local_refinements=[])
    changed_global = _policy(mesh, local_refinements=[], global_size=Quantity(9, "mm"))
    changed_locals = _policy(
        mesh,
        local_refinements=[_local(mesh, refinement_id="local-B")],
    )
    changed_profile = _policy(
        mesh,
        local_refinements=[],
        quality_profile=_profile(profile_id="mesh-quality-other"),
    )
    changed_cap = _policy(mesh, local_refinements=[], max_refinements=1)

    assert baseline.to_dict()["element_type"] == "tet10"
    assert changed_global.to_bytes() != baseline.to_bytes()
    assert changed_locals.to_bytes() != baseline.to_bytes()
    assert changed_profile.to_bytes() != baseline.to_bytes()
    assert changed_cap.to_bytes() != baseline.to_bytes()


@pytest.mark.parametrize(
    "bad_quantity",
    [Quantity(10**400, "m"), Quantity(5e-324, "mm")],
    ids=["overflow", "underflow"],
)
def test_mesh_policy_rejects_unrepresentable_global_size_at_construction(
    bad_quantity: Quantity,
) -> None:
    mesh = _mesh()
    with pytest.raises(ValueError, match="outside finite range|underflows"):
        _policy(mesh, local_refinements=[], global_size=bad_quantity)


@pytest.mark.parametrize(
    "bad_quantity",
    [Quantity(10**400, "m"), Quantity(5e-324, "mm")],
    ids=["overflow", "underflow"],
)
def test_mesh_policy_rejects_unrepresentable_local_size_at_construction(
    bad_quantity: Quantity,
) -> None:
    mesh = _mesh()
    with pytest.raises(ValueError, match="outside finite range|underflows"):
        _policy(mesh, local_refinements=[_local(mesh, size=bad_quantity)])


def test_mesh_policy_rejects_integer_beyond_shared_json_serialization_range() -> None:
    mesh = _mesh()
    with pytest.raises(ValueError, match="serial|digit|canonical"):
        _policy(mesh, local_refinements=[], max_refinements=10**5000)


def test_mesh_policy_preserves_large_but_serializable_count_identity() -> None:
    mesh = _mesh()
    large = 10**1000
    value = _policy(mesh, local_refinements=[], max_refinements=large)

    assert value.max_refinements == large
    assert value.to_dict()["max_refinements"] == large
    assert value.to_bytes() == _policy(mesh, local_refinements=[], max_refinements=large).to_bytes()


def test_mesh_policy_rejects_nested_invalid_unicode_and_accepts_unicode_identity() -> None:
    _mesh()
    with pytest.raises(ValueError):
        _profile(profile_id="profile\x00bad")

    unicode_profile = _profile(profile_id="品質-プロファイル")
    assert unicode_profile.profile_id == "品質-プロファイル"
    assert unicode_profile.to_bytes() != _profile().to_bytes()


def test_mesh_policy_is_immutable_and_projection_isolated() -> None:
    mesh = _mesh()
    value = _policy(mesh, local_refinements=[])
    payload = value.to_dict()
    payload["element_type"] = "tet4"
    payload["quality_profile"]["profile_id"] = "mutated"

    with pytest.raises(FrozenInstanceError):
        value.max_refinements = 9  # type: ignore[misc]
    assert value.element_type == "tet10"
    assert value.quality_profile.profile_id == "mesh-quality-default"
    assert value.to_dict()["element_type"] == "tet10"
