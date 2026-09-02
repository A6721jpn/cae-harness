from __future__ import annotations

import hashlib
import importlib
import json
from typing import Any

import pytest

from febio_cae_harness.contracts import IntentContract

_REQUIRED_FACTS = (
    "engineering_question",
    "units",
    "material",
    "loads",
    "constraints",
    "contact",
    "analysis_step",
    "roi",
    "evaluation_quantities",
)


def _input() -> bytes:
    return b"""<?xml version='1.0' encoding='UTF-8'?>
<febio_spec version='4.0'>
  <Mesh>
    <Nodes name='body-nodes'>
      <node id='1'>0,0,0</node>
      <node id='2'>1,0,0</node>
      <node id='3'>0,1,0</node>
      <node id='4'>0,0,1</node>
    </Nodes>
    <Elements type='tet4' name='body'>
      <elem id='1'>1,2,3,4</elem>
    </Elements>
  </Mesh>
  <MeshDomains><SolidDomain name='body' mat='1'/></MeshDomains>
</febio_spec>
"""


def _intent(input_sha256: str) -> IntentContract:
    return IntentContract(
        engineering_question="What is the stress range over the approved model?",
        units={"length": "mm", "stress": "MPa"},
        material={"approved_in_input": True},
        loads=({"approved_in_input": True},),
        constraints=({"approved_in_input": True},),
        contact={"approved_in_input": True},
        analysis_step={"approved_in_input": True},
        roi=({"id": "entire-body", "scope": "all_elements"},),
        evaluation_quantities=(
            {
                "field": "stress",
                "roi": "entire-body",
                "reduction": "range",
                "states": "all",
                "require_finite": True,
            },
        ),
        condition_sources={
            name: {
                "artifact_sha256": input_sha256,
                "authoritative": True,
                "current": True,
                "source": "explicit-user-approved-feb",
            }
            for name in _REQUIRED_FACTS
        },
    )


def _model_manifest() -> dict[str, Any]:
    return {
        "available_fields": [{"index": 0, "name": "stress"}],
        "element_count": 1,
        "node_count": 4,
        "requested_fields": {
            "stress": {
                "association": "CELL_DATA",
                "component_index": 0,
                "component_name": "stress",
                "components": 9,
                "field_index": 0,
                "tensor_type": "DATA_TENSOR2",
                "vtk_name": "stress",
            }
        },
        "state_count": 1,
        "state_times": [0.0],
    }


def _geometry() -> dict[str, Any]:
    topology = {"cells": [[0, 1, 2, 3]], "cell_types": [10]}
    topology_sha256 = hashlib.sha256(
        json.dumps(topology, sort_keys=True, separators=(",", ":")).encode("ascii")
    ).hexdigest()
    return {
        "topology_sha256": topology_sha256,
        "node_count": 4,
        "element_count": 1,
        "cell_types": [
            {
                "vtk_id": 10,
                "name": "tet4",
                "nodes": 4,
                "elements": 1,
                "integration_rule": "gauss1",
                "integration_points": 1,
            }
        ],
        "states": [
            {
                "index": 0,
                "time": 0.0,
                "integration_point_count": 1,
                "minimum_jacobian": 1.0,
                "maximum_jacobian": 1.0,
                "minimum_element_index": 0,
                "minimum_vtk_id": 10,
                "minimum_integration_point_index": 0,
            }
        ],
    }


def _evaluate(intent: IntentContract, geometry: dict[str, Any]) -> Any:
    module = importlib.import_module("febio_cae_harness.reporting.physical")
    payload = _input()
    return module._evaluate_physical_payload(
        intent=intent,
        input_bytes=payload,
        input_sha256=hashlib.sha256(payload).hexdigest(),
        model_manifest=_model_manifest(),
        geometry=geometry,
        values={
            "stress": {
                "components": 9,
                "count": 9,
                "entity_count": 1,
                "field_index": 0,
                "minimum": 0.0,
                "maximum": 1.0,
                "state_count": 1,
            }
        },
        requested_fields=("stress",),
    )


def test_exact_approved_feb_and_official_result_satisfy_physical_evidence() -> None:
    payload = _input()
    evaluation = _evaluate(_intent(hashlib.sha256(payload).hexdigest()), _geometry())

    assert evaluation.passed == {
        "mesh": True,
        "jacobian": True,
        "roi": True,
        "evaluation": True,
    }
    assert evaluation.documents["mesh"]["input_sha256"] == hashlib.sha256(payload).hexdigest()
    assert evaluation.documents["jacobian"]["all_integration_points_positive"] is True
    assert evaluation.documents["roi"]["roi_ids"] == ["entire-body"]
    assert evaluation.documents["evaluation"]["fields"] == ["stress"]


@pytest.mark.parametrize(
    "mutation",
    ["unbound-input", "negative-jacobian", "unknown-roi", "topology"],
)
def test_physical_evidence_fails_closed_on_unbound_or_invalid_conditions(
    mutation: str,
) -> None:
    payload = _input()
    input_sha256 = hashlib.sha256(payload).hexdigest()
    intent = _intent(input_sha256)
    geometry = _geometry()
    if mutation == "unbound-input":
        intent_payload: Any = intent.to_dict()
        intent_payload["condition_sources"]["material"]["artifact_sha256"] = "0" * 64
        intent = IntentContract.from_mapping(intent_payload)
    elif mutation == "negative-jacobian":
        geometry["states"][0]["minimum_jacobian"] = -0.1
    elif mutation == "unknown-roi":
        intent_payload = intent.to_dict()
        intent_payload["evaluation_quantities"][0]["roi"] = "not-declared"
        intent = IntentContract.from_mapping(intent_payload)
    else:
        geometry["topology_sha256"] = "0" * 64

    evaluation = _evaluate(intent, geometry)

    assert not all(evaluation.passed.values())
    if mutation == "negative-jacobian":
        assert evaluation.passed["jacobian"] is False
    elif mutation == "unknown-roi":
        assert evaluation.passed["evaluation"] is False
    else:
        assert evaluation.passed["mesh"] is False
