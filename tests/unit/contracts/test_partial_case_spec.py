from __future__ import annotations

import importlib
import json
from dataclasses import FrozenInstanceError, replace
from types import ModuleType
from typing import Any

import pytest

from febio_cae.domain import CaseSpecValidationError, Quantity, canonical_bytes


def _optional_module(name: str) -> ModuleType | None:
    try:
        return importlib.import_module(name)
    except (ImportError, ModuleNotFoundError):
        return None


PARTIAL_MODULE = _optional_module("febio_cae.domain.partial_case_spec")
FIELD_NAMES = (
    "geometry",
    "material",
    "support",
    "rigid_tool",
    "motion",
    "contact",
    "mesh_policy",
    "solver_policy",
    "outputs",
    "quality_policy",
    "budget",
)


def _partial_module() -> ModuleType:
    if PARTIAL_MODULE is None:
        pytest.skip("PartialCaseSpec API availability is covered by the dedicated assertion")
    return PARTIAL_MODULE


def _partial_kwargs(case_spec: Any, **overrides: object) -> dict[str, object]:
    values = {field: getattr(case_spec, field) for field in FIELD_NAMES}
    values.update(overrides)
    return values


def _partial(case_spec: Any, **overrides: object) -> Any:
    return _partial_module().PartialCaseSpec(**_partial_kwargs(case_spec, **overrides))


def test_partial_case_spec_api_is_available() -> None:
    assert PARTIAL_MODULE is not None, "P1-B14 PartialCaseSpec module is not available"
    for name in ("SCHEMA_VERSION", "PartialCaseSpec", "PartialCaseSpecValidationError"):
        assert getattr(PARTIAL_MODULE, name, None) is not None, name


def test_partial_case_spec_all_none_is_a_valid_initial_snapshot() -> None:
    partial = _partial_module().PartialCaseSpec()
    assert partial.unresolved_fields == tuple(sorted(FIELD_NAMES))
    projection = partial.to_dict()
    assert set(projection) == {"schema_version", *FIELD_NAMES}
    assert projection["schema_version"] == "1"
    assert all(projection[field] is None for field in FIELD_NAMES)
    assert partial.to_bytes() == canonical_bytes(projection)


@pytest.mark.parametrize("omitted", FIELD_NAMES)
def test_partial_case_spec_unresolved_fields_are_the_sorted_absent_complement(
    omitted: str, synthetic_case_spec: Any
) -> None:
    values = _partial_kwargs(synthetic_case_spec)
    values.pop(omitted)
    partial = _partial_module().PartialCaseSpec(**values)
    assert partial.unresolved_fields == (omitted,)
    with pytest.raises(_partial_module().PartialCaseSpecValidationError, match=omitted):
        partial.to_case_spec()


def test_partial_case_spec_all_children_convert_through_case_spec_validator(
    synthetic_case_spec: Any,
) -> None:
    partial = _partial(synthetic_case_spec)
    assert partial.unresolved_fields == ()
    converted = partial.to_case_spec()
    assert converted == synthetic_case_spec
    assert converted is not synthetic_case_spec
    assert partial.to_dict() == synthetic_case_spec.to_dict()


@pytest.mark.parametrize("field", FIELD_NAMES)
def test_partial_case_spec_rejects_wrong_present_child_type(
    field: str, synthetic_case_spec: Any
) -> None:
    with pytest.raises(_partial_module().PartialCaseSpecValidationError, match=field):
        _partial_module().PartialCaseSpec(**{field: object()})


def test_partial_case_spec_preserves_explicit_zero_values(synthetic_case_spec: Any) -> None:
    partial = _partial_module().PartialCaseSpec(
        mesh_policy=synthetic_case_spec.mesh_policy,
        budget=synthetic_case_spec.budget,
    )
    projection = partial.to_dict()
    assert projection["mesh_policy"]["max_refinements"] == 0  # type: ignore[index]
    assert projection["budget"]["max_llm_calls"] == 0  # type: ignore[index]
    assert projection["budget"]["max_llm_tokens"] == 0  # type: ignore[index]
    assert partial.unresolved_fields == tuple(sorted(set(FIELD_NAMES) - {"mesh_policy", "budget"}))


def test_partial_case_spec_conversion_propagates_existing_cross_child_error_without_mutation(
    synthetic_case_spec: Any,
) -> None:
    mismatched_quality = replace(
        synthetic_case_spec.quality_policy,
        criteria=[
            replace(
                synthetic_case_spec.quality_policy.criteria[0],
                evaluation_ids=["missing_evaluation"],
            )
        ],
    )
    partial = _partial(synthetic_case_spec, quality_policy=mismatched_quality)
    before = partial.to_bytes()
    with pytest.raises(CaseSpecValidationError, match="quality_policy"):
        partial.to_case_spec()
    assert partial.to_bytes() == before
    assert partial.unresolved_fields == ()


def test_partial_case_spec_semantic_child_equivalence_retains_canonical_bytes(
    synthetic_case_spec: Any,
) -> None:
    motion = replace(
        synthetic_case_spec.motion,
        samples=[
            replace(synthetic_case_spec.motion.samples[0], time=Quantity(1000, "ms")),
            replace(synthetic_case_spec.motion.samples[1], time=Quantity(2000, "ms")),
        ],
    )
    evaluations = [
        replace(
            item,
            state_times=[Quantity(1000, "ms"), Quantity(2000, "ms")],
        )
        for item in reversed(synthetic_case_spec.outputs.evaluations)
    ]
    outputs = replace(
        synthetic_case_spec.outputs,
        requests=list(reversed(synthetic_case_spec.outputs.requests)),
        saved_times=[Quantity(1000, "ms"), Quantity(2000, "ms")],
        evaluations=evaluations,
    )
    quality = replace(
        synthetic_case_spec.quality_policy,
        criteria=[
            replace(
                synthetic_case_spec.quality_policy.criteria[0],
                evaluation_ids=list(
                    reversed(synthetic_case_spec.quality_policy.criteria[0].evaluation_ids)
                ),
            )
        ],
    )
    equivalent_spec = replace(
        synthetic_case_spec,
        motion=motion,
        outputs=outputs,
        quality_policy=quality,
    )
    first = _partial(synthetic_case_spec)
    second = _partial(equivalent_spec)
    assert first.to_bytes() == second.to_bytes()


def test_partial_case_spec_detaches_projection_and_is_frozen(synthetic_case_spec: Any) -> None:
    partial = _partial(synthetic_case_spec, budget=None, geometry=None)
    before = partial.to_bytes()
    projection = partial.to_dict()
    projection["material"] = {}
    projection["support"] = None
    with pytest.raises(FrozenInstanceError):
        partial.material = None  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        partial.unresolved_fields = ()  # type: ignore[misc]
    assert partial.to_bytes() == before
    assert partial.material is synthetic_case_spec.material
    assert partial.geometry is None


def test_partial_case_spec_projection_contains_complete_present_child_projection(
    synthetic_case_spec: Any,
) -> None:
    partial = _partial_module().PartialCaseSpec(material=synthetic_case_spec.material)
    projection = partial.to_dict()
    assert projection["material"] == json.loads(synthetic_case_spec.material.to_bytes())
    assert projection["geometry"] is None
    assert projection["budget"] is None
