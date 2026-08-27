from __future__ import annotations

from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from febio_cae_harness.contracts import (
    AnalysisIntent,
    ExecutionBudget,
    Intent,
    IntentContract,
    IntentState,
)


def test_intent_states_are_exact_and_string_compatible() -> None:
    assert tuple(IntentState) == (
        IntentState.GATHERING,
        IntentState.BOUND,
        IntentState.ASK_AND_BLOCK,
    )
    assert [state.value for state in IntentState] == [
        "GATHERING",
        "BOUND",
        "ASK_AND_BLOCK",
    ]
    assert IntentState("BOUND") is IntentState.BOUND


def test_contract_aliases_share_one_canonical_type() -> None:
    assert AnalysisIntent is IntentContract
    assert Intent is IntentContract


def test_intent_is_gathering_by_default_and_immutable() -> None:
    intent = IntentContract()

    assert intent.state is IntentState.GATHERING
    assert intent.engineering_question is None
    assert intent.condition_sources == ()
    assert intent.unresolved == ()

    with pytest.raises(FrozenInstanceError):
        intent.state = IntentState.BOUND  # type: ignore[misc]


def test_intent_freezes_authoritative_values_without_interpreting_them() -> None:
    material: dict[str, Any] = {"name": "authoritative material", "properties": {"E": 2.0}}
    loads = [{"kind": "authoritative load", "value": 10.0}]

    intent = IntentContract(
        state="BOUND",  # type: ignore[arg-type]
        engineering_question="What is the displacement?",
        units={"length": "mm", "force": "N"},
        material=material,
        loads=loads,
        constraints=("authoritative constraint",),
        contact={"definition": "authoritative contact"},
        analysis_step={"name": "load step"},
        roi=["authoritative ROI"],
        load_path=["authoritative load path"],
        evaluation_quantities=["displacement"],
        protected_geometry={"part": "protected"},
        invariants=["volume must remain represented"],
        allowed_mesh_changes=["local refinement"],
        allowed_numerical_changes=["time-step reduction"],
        retry_budget=2,
        time_budget_seconds=60.0,
        cpu_budget_seconds=30.0,
        memory_budget_mb=1024,
        condition_sources=["authoritative-input.json"],
        unresolved=["none"],
    )

    assert intent.state is IntentState.BOUND
    assert isinstance(intent.loads, tuple)
    assert isinstance(intent.loads[0], Mapping)
    assert intent.loads[0]["kind"] == "authoritative load"
    assert isinstance(intent.material, Mapping)
    assert isinstance(intent.material["properties"], Mapping)
    assert intent.material["properties"]["E"] == 2.0
    assert intent.condition_sources == ("authoritative-input.json",)

    material["name"] = "changed outside the contract"
    loads[0]["value"] = 99.0
    assert isinstance(intent.material, Mapping)
    assert intent.material["name"] == "authoritative material"
    assert isinstance(intent.loads, tuple)
    assert isinstance(intent.loads[0], Mapping)
    assert intent.loads[0]["value"] == 10.0

    assert isinstance(intent.units, Mapping)
    with pytest.raises(TypeError):
        intent.units["length"] = "m"  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        intent.loads = ()  # type: ignore[misc]


def test_execution_budget_is_immutable_and_validates_limits() -> None:
    budget = ExecutionBudget(
        retry_budget=2,
        time_budget_seconds=60.0,
        cpu_budget_seconds=30.0,
        memory_budget_mb=1024,
    )

    assert budget.retry_budget == 2
    assert budget.time_budget_seconds == 60.0

    with pytest.raises(FrozenInstanceError):
        budget.retry_budget = 3  # type: ignore[misc]

    with pytest.raises(ValueError, match="retry_budget"):
        ExecutionBudget(retry_budget=-1)
    with pytest.raises(ValueError, match="time_budget_seconds"):
        ExecutionBudget(time_budget_seconds=-1.0)
    with pytest.raises(ValueError, match="cpu_budget_seconds"):
        ExecutionBudget(cpu_budget_seconds=-1.0)
    with pytest.raises(ValueError, match="memory_budget_mb"):
        ExecutionBudget(memory_budget_mb=-1)


def test_intent_rejects_unknown_states() -> None:
    with pytest.raises(ValueError, match="ASK_AND_BLOCK"):
        IntentContract(state="NEEDS_GUESSING")  # type: ignore[arg-type]
