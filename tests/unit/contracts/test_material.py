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


MATERIAL_MODULE = _optional_module("febio_cae.domain.material")


def _material() -> ModuleType:
    if MATERIAL_MODULE is None:
        pytest.skip("material API availability is covered by the dedicated assertion")
    return MATERIAL_MODULE


def _evidence(target_field: str, seed: str = "a") -> EvidenceRef:
    return EvidenceRef(
        schema_version="1",
        source_kind="user_instruction",
        reference="User:material-contract",
        target_field=target_field,
        content_digest=seed * 64,
    )


def _applicability(material: ModuleType, *, seed: str = "d") -> Any:
    return material.MaterialApplicability(
        strain_statement="Applicable for the supplied small-strain test condition.",
        strain_evidence=_evidence("material.strain_applicability", seed),
        rate_statement="Applicable for the supplied rate-independent test condition.",
        rate_evidence=_evidence("material.rate_applicability", seed),
    )


def _candidate(
    material: ModuleType,
    candidate_type: str = "IsotropicLinearElastic",
    youngs_modulus: object = Quantity(200, "MPa"),
    poisson_ratio: object = Quantity(0.3, "1"),
) -> Any:
    return getattr(material, candidate_type)(
        youngs_modulus=youngs_modulus,
        poisson_ratio=poisson_ratio,
        model_evidence=_evidence("material.model", "a"),
        youngs_modulus_evidence=_evidence("material.youngs_modulus", "b"),
        poisson_ratio_evidence=_evidence("material.poisson_ratio", "c"),
        applicability=_applicability(material),
    )


def test_material_api_is_available() -> None:
    assert MATERIAL_MODULE is not None, "P1-B2 material module is not available"
    for name in (
        "SCHEMA_VERSION",
        "MaterialApplicability",
        "IsotropicLinearElastic",
        "CompressibleNeoHookean",
    ):
        assert getattr(MATERIAL_MODULE, name, None) is not None, name


def test_material_candidates_are_explicit_and_use_one_e_nu_parameterization() -> None:
    material = _material()
    linear = _candidate(material)
    neo = _candidate(material, "CompressibleNeoHookean")

    assert linear.to_dict()["schema_version"] == "1"
    assert linear.to_dict()["kind"] == "isotropic_linear_elastic"
    assert neo.to_dict()["kind"] == "compressible_neo_hookean"
    assert set(linear.to_dict()) == {
        "schema_version",
        "kind",
        "youngs_modulus",
        "poisson_ratio",
        "model_evidence",
        "youngs_modulus_evidence",
        "poisson_ratio_evidence",
        "applicability",
    }
    assert linear.to_dict()["youngs_modulus"] == {"value": 200_000_000.0, "unit": "Pa"}
    assert linear.to_dict()["poisson_ratio"] == {"value": 0.3, "unit": "1"}
    assert "density" not in linear.to_dict()
    assert "strain_limit" not in linear.to_dict()
    assert linear.to_bytes() == canonical_bytes(linear.to_dict())


def test_equivalent_si_material_quantities_have_identical_canonical_bytes() -> None:
    material = _material()
    displayed = _candidate(material, youngs_modulus=Quantity(200, "MPa"))
    si = _candidate(material, youngs_modulus=Quantity(200_000_000, "Pa"))

    assert displayed.to_bytes() == si.to_bytes()


@pytest.mark.parametrize(
    ("youngs_modulus", "poisson_ratio"),
    [
        (Quantity(0, "Pa"), Quantity(0.3, "1")),
        (Quantity(-1, "Pa"), Quantity(0.3, "1")),
        (Quantity(1, "N"), Quantity(0.3, "1")),
        (Quantity(1, "Pa"), Quantity(-1, "1")),
        (Quantity(1, "Pa"), Quantity(0.5, "1")),
        (Quantity(1, "Pa"), Quantity(0.500001, "1")),
        (Quantity(1, "Pa"), Quantity(-1.000001, "1")),
        ("1 Pa", Quantity(0.3, "1")),
        (Quantity(1, "Pa"), "0.3"),
        (True, Quantity(0.3, "1")),
        (Quantity(1, "Pa"), False),
    ],
)
def test_material_rejects_invalid_parameter_types_dimensions_and_range(
    youngs_modulus: object,
    poisson_ratio: object,
) -> None:
    material = _material()

    with pytest.raises(ValueError):
        _candidate(material, youngs_modulus=youngs_modulus, poisson_ratio=poisson_ratio)


def test_material_rejects_misbound_evidence_and_empty_applicability() -> None:
    material = _material()

    with pytest.raises(ValueError):
        material.MaterialApplicability(
            strain_statement="",
            strain_evidence=_evidence("material.strain_applicability"),
            rate_statement="rate",
            rate_evidence=_evidence("material.rate_applicability"),
        )
    with pytest.raises(ValueError):
        material.MaterialApplicability(
            strain_statement="strain",
            strain_evidence=_evidence("material.youngs_modulus"),
            rate_statement="rate",
            rate_evidence=_evidence("material.rate_applicability"),
        )
    with pytest.raises(ValueError):
        material.MaterialApplicability(
            strain_statement="strain",
            strain_evidence=_evidence("material.strain_applicability"),
            rate_statement="rate",
            rate_evidence=_evidence("motion.rate_independent_applicability"),
        )
    with pytest.raises(ValueError):
        material.IsotropicLinearElastic(
            youngs_modulus=Quantity(200, "MPa"),
            poisson_ratio=Quantity(0.3, "1"),
            model_evidence=_evidence("material.youngs_modulus"),
            youngs_modulus_evidence=_evidence("material.youngs_modulus"),
            poisson_ratio_evidence=_evidence("material.poisson_ratio"),
            applicability=_applicability(material),
        )


def test_material_is_immutable_and_from_dict_is_strict() -> None:
    material = _material()
    candidate = _candidate(material)
    payload = candidate.to_dict()

    restored = material.IsotropicLinearElastic.from_dict(payload)
    assert restored.to_bytes() == candidate.to_bytes()
    with pytest.raises(FrozenInstanceError):
        candidate.youngs_modulus = Quantity(1, "Pa")

    extra = dict(payload)
    extra["unexpected"] = 1
    with pytest.raises(ValueError):
        material.IsotropicLinearElastic.from_dict(extra)
    wrong_schema = dict(payload)
    wrong_schema["schema_version"] = "2"
    with pytest.raises(ValueError):
        material.IsotropicLinearElastic.from_dict(wrong_schema)


def test_material_candidate_from_dict_rejects_wrong_kind() -> None:
    material = _material()
    payload = _candidate(material).to_dict()
    payload["kind"] = "compressible_neo_hookean"

    with pytest.raises(ValueError):
        material.IsotropicLinearElastic.from_dict(payload)
