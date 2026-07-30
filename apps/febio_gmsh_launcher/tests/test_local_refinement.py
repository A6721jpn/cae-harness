from types import SimpleNamespace

import pytest

import febio_gmsh_launcher.local_refinement as local_refinement
from febio_gmsh_launcher.local_refinement import (
    FaceSignature,
    LocalRefinementConfig,
    configure_distance_threshold_field,
    face_signature,
    validate_seed_signatures,
)


def test_local_refinement_config_accepts_approved_sizes() -> None:
    config = LocalRefinementConfig(
        global_size_mm=2.0,
        local_size_mm=0.40,
        transition_mm=3.0,
    )

    assert config.global_size_mm == 2.0
    assert config.local_size_mm == 0.40


def test_local_size_must_be_smaller_than_global_size() -> None:
    with pytest.raises(ValueError, match="local_size_mm"):
        LocalRefinementConfig(2.0, 2.0, 3.0)


def test_seed_signature_validation_rejects_empty_selection() -> None:
    with pytest.raises(ValueError, match="nonempty"):
        validate_seed_signatures([])


def test_seed_signature_validation_rejects_invalid_signatures() -> None:
    valid = FaceSignature(1, 1.0, (0.0, 0.0, 0.0), (0.0,) * 6)
    invalid_cases = [
        [FaceSignature(1, 0.0, (0.0, 0.0, 0.0), (0.0,) * 6)],
        [FaceSignature(1, 1.0, (float("nan"), 0.0, 0.0), (0.0,) * 6)],
        [valid, FaceSignature(1, 2.0, (1.0, 1.0, 1.0), (1.0,) * 6)],
    ]

    for signatures in invalid_cases:
        with pytest.raises(ValueError):
            validate_seed_signatures(signatures)


def test_face_signature_reads_stable_cad_properties(monkeypatch: pytest.MonkeyPatch) -> None:
    model = SimpleNamespace(
        occ=SimpleNamespace(
            getMass=lambda dim, tag: 12.5,
            getCenterOfMass=lambda dim, tag: (1.0, 2.0, 3.0),
        ),
        getBoundingBox=lambda dim, tag: (-1.0, -2.0, -3.0, 4.0, 5.0, 6.0),
    )
    monkeypatch.setattr(local_refinement, "gmsh", SimpleNamespace(model=model))

    assert face_signature(17) == FaceSignature(
        face_tag=17,
        area=12.5,
        centroid=(1.0, 2.0, 3.0),
        bounding_box=(-1.0, -2.0, -3.0, 4.0, 5.0, 6.0),
    )


def test_configure_distance_threshold_field_sets_background_mesh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, tuple[object, ...]]] = []

    class FakeField:
        def add(self, kind: str) -> int:
            calls.append(("add", (kind,)))
            return {"Distance": 4, "Threshold": 5}[kind]

        def setNumbers(self, tag: int, name: str, values: list[int]) -> None:
            calls.append(("setNumbers", (tag, name, values)))

        def setNumber(self, tag: int, name: str, value: float | int) -> None:
            calls.append(("setNumber", (tag, name, value)))

        def setAsBackgroundMesh(self, tag: int) -> None:
            calls.append(("setAsBackgroundMesh", (tag,)))

    monkeypatch.setattr(
        local_refinement,
        "gmsh",
        SimpleNamespace(model=SimpleNamespace(mesh=SimpleNamespace(field=FakeField()))),
    )

    assert configure_distance_threshold_field(
        [11, 12], LocalRefinementConfig(2.0, 0.40, 3.0)
    ) == (4, 5)
    assert calls == [
        ("add", ("Distance",)),
        ("setNumbers", (4, "FacesList", [11, 12])),
        ("setNumber", (4, "Sampling", 200)),
        ("add", ("Threshold",)),
        ("setNumber", (5, "InField", 4)),
        ("setNumber", (5, "SizeMin", 0.40)),
        ("setNumber", (5, "SizeMax", 2.0)),
        ("setNumber", (5, "DistMin", 0.0)),
        ("setNumber", (5, "DistMax", 3.0)),
        ("setAsBackgroundMesh", (5,)),
    ]


def test_configure_distance_threshold_field_rejects_empty_faces() -> None:
    with pytest.raises(ValueError, match="nonempty"):
        configure_distance_threshold_field([], LocalRefinementConfig(2.0, 0.40, 3.0))
