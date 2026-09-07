from __future__ import annotations

import importlib
from dataclasses import FrozenInstanceError
from types import ModuleType
from typing import Any

import pytest

from febio_cae.domain import EvidenceRef, Point3, Quantity, UnitDirection, canonical_bytes
from febio_cae.domain.spatial import FrameId


def _optional_module(name: str) -> ModuleType | None:
    try:
        return importlib.import_module(name)
    except (ImportError, ModuleNotFoundError):
        return None


MOTION_MODULE = _optional_module("febio_cae.domain.motion")


def _motion() -> ModuleType:
    if MOTION_MODULE is None:
        pytest.skip("motion API availability is covered by the dedicated assertion")
    return MOTION_MODULE


def _evidence(target_field: str, seed: str = "a") -> EvidenceRef:
    return EvidenceRef(
        schema_version="1",
        source_kind="user_instruction",
        reference="User:motion-contract",
        target_field=target_field,
        content_digest=seed * 64,
    )


def _applicability(motion: ModuleType) -> Any:
    return motion.MotionApplicability(
        quasi_static_statement="The supplied test is applicable as quasi-static.",
        quasi_static_evidence=_evidence("motion.quasi_static_applicability", "a"),
        rate_independent_statement="The supplied test is applicable as rate-independent.",
        rate_independent_evidence=_evidence("motion.rate_independent_applicability", "b"),
    )


def _sample(motion: ModuleType, time: Quantity, displacement: Quantity) -> Any:
    return motion.MotionSample(time=time, displacement=displacement)


def _profile(
    motion: ModuleType,
    *,
    frame: FrameId | None = None,
    samples: list[Any] | None = None,
    reference_point: Point3 | None = None,
    direction: UnitDirection | None = None,
) -> Any:
    frame = FrameId("World") if frame is None else frame
    direction = UnitDirection(frame, 0, 0, 1) if direction is None else direction
    reference_point = (
        Point3(frame, Quantity(5, "mm"), Quantity(6, "mm"), Quantity(7, "mm"))
        if reference_point is None
        else reference_point
    )
    samples = (
        [
            _sample(motion, Quantity(2, "s"), Quantity(0, "mm")),
            _sample(motion, Quantity(3, "s"), Quantity(1, "mm")),
            _sample(motion, Quantity(4, "s"), Quantity(1, "mm")),
            _sample(motion, Quantity(5, "s"), Quantity(2, "mm")),
        ]
        if samples is None
        else samples
    )
    return motion.MotionProfile(
        direction=direction,
        initial_reference_point=reference_point,
        samples=samples,
        applicability=_applicability(motion),
    )


def test_motion_api_is_available() -> None:
    assert MOTION_MODULE is not None, "P1-B2 motion module is not available"
    for name in ("SCHEMA_VERSION", "MotionApplicability", "MotionSample", "MotionProfile"):
        assert getattr(MOTION_MODULE, name, None) is not None, name


def test_motion_preserves_explicit_frame_initial_time_and_ordered_plateau() -> None:
    motion = _motion()
    profile = _profile(motion)
    payload = profile.to_dict()

    assert payload["schema_version"] == "1"
    assert payload["direction"]["frame"] == "World"
    assert payload["initial_reference_point"]["frame"] == "World"
    assert payload["samples"][0]["time"] == {"value": 2.0, "unit": "s"}
    assert payload["samples"][0]["displacement"] == {"value": 0.0, "unit": "m"}
    assert [sample["displacement"]["value"] for sample in payload["samples"]] == [
        0.0,
        0.001,
        0.001,
        0.002,
    ]
    assert profile.to_bytes() == canonical_bytes(payload)


def test_equivalent_si_motion_quantities_have_identical_canonical_bytes() -> None:
    motion = _motion()
    displayed = _profile(motion)
    si = _profile(
        motion,
        samples=[
            _sample(motion, Quantity(2, "s"), Quantity(0, "m")),
            _sample(motion, Quantity(3, "s"), Quantity(0.001, "m")),
            _sample(motion, Quantity(4, "s"), Quantity(0.001, "m")),
            _sample(motion, Quantity(5, "s"), Quantity(0.002, "m")),
        ],
        reference_point=Point3(
            FrameId("World"), Quantity(0.005, "m"), Quantity(0.006, "m"), Quantity(0.007, "m")
        ),
    )

    assert displayed.to_bytes() == si.to_bytes()


@pytest.mark.parametrize(
    "samples",
    [
        [(Quantity(2, "s"), Quantity(0, "mm"))],
        [
            (Quantity(2, "s"), Quantity(1, "mm")),
            (Quantity(3, "s"), Quantity(2, "mm")),
        ],
        [
            (Quantity(2, "s"), Quantity(0, "mm")),
            (Quantity(2, "s"), Quantity(1, "mm")),
        ],
        [
            (Quantity(2, "s"), Quantity(0, "mm")),
            (Quantity(3, "s"), Quantity(-1, "mm")),
        ],
        [
            (Quantity(2, "s"), Quantity(0, "mm")),
            (Quantity(3, "s"), Quantity(1, "mm")),
            (Quantity(4, "s"), Quantity(0.5, "mm")),
        ],
        [
            (Quantity(3, "s"), Quantity(0, "mm")),
            (Quantity(2, "s"), Quantity(1, "mm")),
        ],
    ],
)
def test_motion_rejects_missing_zero_nonmonotonic_or_unordered_history(
    samples: list[tuple[Quantity, Quantity]],
) -> None:
    motion = _motion()

    with pytest.raises(ValueError):
        typed_samples = [_sample(motion, time, displacement) for time, displacement in samples]
        _profile(motion, samples=typed_samples)


def test_motion_rejects_cross_frame_reference_and_misbound_applicability() -> None:
    motion = _motion()
    world = FrameId("World")
    other = FrameId("Other")

    with pytest.raises(ValueError):
        _profile(
            motion,
            direction=UnitDirection(world, 0, 0, 1),
            reference_point=Point3(other, Quantity(0, "m"), Quantity(0, "m"), Quantity(0, "m")),
        )
    with pytest.raises(ValueError):
        motion.MotionApplicability(
            quasi_static_statement="quasi-static",
            quasi_static_evidence=_evidence("material.strain_applicability"),
            rate_independent_statement="rate-independent",
            rate_independent_evidence=_evidence("motion.rate_independent_applicability"),
        )


@pytest.mark.parametrize(
    ("time", "displacement"),
    [
        (Quantity(-1, "s"), Quantity(0, "mm")),
        (Quantity(1, "mm"), Quantity(0, "mm")),
        (Quantity(1, "s"), Quantity(0, "m2")),
        ("1 s", Quantity(0, "mm")),
        (Quantity(1, "s"), "0 mm"),
        (True, Quantity(0, "mm")),
        (Quantity(1, "s"), False),
    ],
)
def test_motion_sample_rejects_invalid_typed_quantities(
    time: object,
    displacement: object,
) -> None:
    motion = _motion()

    with pytest.raises(ValueError):
        motion.MotionSample(time=time, displacement=displacement)


def test_motion_is_immutable_copies_samples_and_from_dict_is_strict() -> None:
    motion = _motion()
    samples = [
        _sample(motion, Quantity(2, "s"), Quantity(0, "mm")),
        _sample(motion, Quantity(3, "s"), Quantity(1, "mm")),
    ]
    profile = _profile(motion, samples=samples)
    samples.clear()

    assert len(profile.samples) == 2
    restored = motion.MotionProfile.from_dict(profile.to_dict())
    assert restored.to_bytes() == profile.to_bytes()
    with pytest.raises(FrozenInstanceError):
        profile.samples = ()

    extra = profile.to_dict()
    extra["unexpected"] = 1
    with pytest.raises(ValueError):
        motion.MotionProfile.from_dict(extra)
    wrong_schema = profile.to_dict()
    wrong_schema["schema_version"] = "2"
    with pytest.raises(ValueError):
        motion.MotionProfile.from_dict(wrong_schema)


def test_motion_applicability_requires_nonempty_statements() -> None:
    motion = _motion()

    with pytest.raises(ValueError):
        motion.MotionApplicability(
            quasi_static_statement="",
            quasi_static_evidence=_evidence("motion.quasi_static_applicability"),
            rate_independent_statement="rate-independent",
            rate_independent_evidence=_evidence("motion.rate_independent_applicability"),
        )
