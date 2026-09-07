from __future__ import annotations

import importlib
import math
from dataclasses import FrozenInstanceError
from types import ModuleType
from typing import Any

import pytest

from febio_cae.domain import Quantity, canonical_bytes


def _optional_module(name: str) -> ModuleType | None:
    try:
        return importlib.import_module(name)
    except (ImportError, ModuleNotFoundError):
        return None


SPATIAL_MODULE = _optional_module("febio_cae.domain.spatial")


def _spatial() -> ModuleType:
    if SPATIAL_MODULE is None:
        pytest.skip("spatial API availability is covered by the dedicated assertion")
    return SPATIAL_MODULE


def test_spatial_api_is_available() -> None:
    assert SPATIAL_MODULE is not None, "P1-B1 spatial module is not available"
    for name in (
        "SCHEMA_VERSION",
        "OpaqueId",
        "FrameId",
        "GeometryId",
        "BodyId",
        "FaceId",
        "Point3",
        "Translation3",
        "UnitDirection",
        "ProperRotation",
        "RigidTransform",
    ):
        assert getattr(SPATIAL_MODULE, name, None) is not None, name


@pytest.mark.parametrize("name", ["OpaqueId", "FrameId", "GeometryId", "BodyId", "FaceId"])
def test_opaque_ids_preserve_case_and_reject_invalid_values(name: str) -> None:
    spatial = _spatial()
    identifier_type = getattr(spatial, name)

    identifier = identifier_type("Case-Sensitive/ID")
    assert identifier.value == "Case-Sensitive/ID"
    assert identifier.to_dict() == {"schema_version": "1", "value": "Case-Sensitive/ID"}

    for invalid in ["", " ", " leading", "trailing ", "bad\nvalue", None, 1, True]:
        with pytest.raises(ValueError):
            identifier_type(invalid)


def test_point_and_translation_require_explicit_frame_and_length_quantities() -> None:
    spatial = _spatial()
    frame = spatial.FrameId("World")
    point = spatial.Point3(
        frame,
        Quantity(1000, "mm"),
        Quantity(-2, "mm"),
        Quantity(0, "mm"),
    )
    translation = spatial.Translation3(
        frame,
        Quantity(1, "m"),
        Quantity(2, "m"),
        Quantity(3, "m"),
    )

    assert point.to_dict()["schema_version"] == "1"
    assert point.to_dict()["frame"] == "World"
    assert point.to_dict()["x"] == {"value": 1.0, "unit": "m"}
    assert translation.to_dict()["z"] == {"value": 3.0, "unit": "m"}
    assert point.to_bytes() == canonical_bytes(point.to_dict())
    assert translation.to_bytes() == canonical_bytes(translation.to_dict())

    with pytest.raises(ValueError):
        spatial.Point3(frame, Quantity(1, "s"), Quantity(0, "m"), Quantity(0, "m"))
    with pytest.raises(ValueError):
        spatial.Point3("World", Quantity(0, "m"), Quantity(0, "m"), Quantity(0, "m"))


def test_spatial_values_are_immutable_and_copy_nested_rotation_input() -> None:
    spatial = _spatial()
    frame = spatial.FrameId("World")
    matrix = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    rotation = spatial.ProperRotation(matrix)

    matrix[0][0] = 99.0
    assert rotation.matrix[0][0] == 1.0
    with pytest.raises(FrozenInstanceError):
        rotation.matrix = ()
    with pytest.raises(FrozenInstanceError):
        spatial.Point3(frame, Quantity(0, "m"), Quantity(0, "m"), Quantity(0, "m")).x = Quantity(
            1, "m"
        )


@pytest.mark.parametrize(
    "components",
    [
        (0.0, 0.0, 0.0),
        (math.nan, 0.0, 0.0),
        (math.inf, 0.0, 0.0),
        (True, 0.0, 0.0),
    ],
)
def test_unit_direction_requires_finite_nonzero_numeric_components(
    components: tuple[Any, ...],
) -> None:
    spatial = _spatial()

    with pytest.raises(ValueError):
        spatial.UnitDirection(spatial.FrameId("World"), *components)


def test_unit_direction_normalizes_explicit_vector_in_its_explicit_frame() -> None:
    spatial = _spatial()
    direction = spatial.UnitDirection(spatial.FrameId("World"), 3, 4, 0)

    assert direction.frame.value == "World"
    assert direction.x == pytest.approx(0.6)
    assert direction.y == pytest.approx(0.8)
    assert direction.z == 0.0
    assert math.sqrt(direction.x**2 + direction.y**2 + direction.z**2) == pytest.approx(1.0)
    assert direction.to_bytes() == canonical_bytes(direction.to_dict())


@pytest.mark.parametrize(
    "components",
    [
        (math.ulp(0.0), math.ulp(0.0), 0.0),
        (math.ulp(0.0), math.ulp(0.0), math.ulp(0.0)),
        (-math.ulp(0.0), math.ulp(0.0), 0.0),
    ],
)
def test_unit_direction_stably_normalizes_subnormal_components(
    components: tuple[float, float, float],
) -> None:
    spatial = _spatial()
    direction = spatial.UnitDirection(spatial.FrameId("World"), *components)

    assert math.hypot(direction.x, direction.y, direction.z) == pytest.approx(1.0, abs=1.0e-10)


def test_proper_rotation_requires_orthogonal_positive_determinant_matrix() -> None:
    spatial = _spatial()
    accepted = spatial.ProperRotation([[1.0 + 4.0e-11, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    assert accepted.matrix[0][0] == pytest.approx(1.0 + 4.0e-11)

    for invalid in (
        [[-1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        [[2.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        [[1.0, 1.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        [[math.nan, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        [[1.0, 0.0], [0.0, 1.0]],
    ):
        with pytest.raises(ValueError):
            spatial.ProperRotation(invalid)


def test_rigid_transform_requires_explicit_frames_position_and_orientation() -> None:
    spatial = _spatial()
    source = spatial.FrameId("Source")
    target = spatial.FrameId("Target")
    translation = spatial.Translation3(
        target,
        Quantity(1, "mm"),
        Quantity(0, "mm"),
        Quantity(0, "mm"),
    )
    rotation = spatial.ProperRotation(((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)))
    transform = spatial.RigidTransform(source, target, translation, rotation)

    payload = transform.to_dict()
    assert payload["schema_version"] == "1"
    assert payload["source_frame"] == "Source"
    assert payload["target_frame"] == "Target"
    assert transform.to_bytes() == canonical_bytes(payload)

    with pytest.raises(ValueError):
        spatial.RigidTransform(
            source,
            target,
            spatial.Translation3(
                source,
                Quantity(0, "m"),
                Quantity(0, "m"),
                Quantity(0, "m"),
            ),
            rotation,
        )
