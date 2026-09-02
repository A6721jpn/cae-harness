"""Fail-closed physical-result evaluation for an exact approved FEB input."""

from __future__ import annotations

import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

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
_ELEMENT_PROFILES = {
    "tet4": (10, 4, "gauss1", 1),
    "tet10": (24, 10, "gauss4", 4),
}
_ROI_ASSOCIATIONS = {
    "all_nodes": "POINT_DATA",
    "all_elements": "CELL_DATA",
}
_INTEGER_LIST = re.compile(r"[\s,]+")


@dataclass(frozen=True, slots=True)
class PhysicalEvidenceEvaluation:
    """Detached diagnostic result; this record alone grants no report authority."""

    passed: Mapping[str, bool]
    documents: Mapping[str, Mapping[str, object]]
    failures: Mapping[str, tuple[str, ...]]

    def __post_init__(self) -> None:
        object.__setattr__(self, "passed", MappingProxyType(dict(self.passed)))
        object.__setattr__(self, "documents", MappingProxyType(dict(self.documents)))
        object.__setattr__(self, "failures", MappingProxyType(dict(self.failures)))


@dataclass(frozen=True, slots=True)
class _MeshInventory:
    node_count: int
    element_count: int
    topology_sha256: str
    cell_types: tuple[Mapping[str, object], ...]


def _local_name(value: str) -> str:
    return value.rsplit("}", 1)[-1]


def _positive_integer(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _parse_identifier(value: object) -> int:
    if not isinstance(value, str):
        raise ValueError("FEB mesh identifier is missing")
    try:
        result = int(value)
    except ValueError as error:
        raise ValueError("FEB mesh identifier is invalid") from error
    if result <= 0:
        raise ValueError("FEB mesh identifier is invalid")
    return result


def _mesh_inventory(input_bytes: bytes) -> _MeshInventory:
    if len(input_bytes) > 512 * 1024 * 1024:
        raise ValueError("FEB input exceeds the physical-evidence limit")
    lowered = input_bytes.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise ValueError("FEB input declarations are not accepted")
    try:
        root = ET.fromstring(input_bytes)
    except ET.ParseError as error:
        raise ValueError("FEB input is not well-formed XML") from error
    if _local_name(root.tag).casefold() != "febio_spec":
        raise ValueError("FEB input root is not febio_spec")

    for element in root.iter():
        local = _local_name(element.tag).casefold().replace("-", "_")
        attribute_names = {
            _local_name(name).casefold().replace("-", "_") for name in element.attrib
        }
        if local in {"integration_rule", "element_type"} or attribute_names.intersection(
            {"integration", "integration_rule"}
        ):
            raise ValueError("FEB input uses a non-default element integration rule")

    node_ids: list[int] = []
    coordinates: dict[int, tuple[float, float, float]] = {}
    for group in root.iter():
        if _local_name(group.tag).casefold() != "nodes":
            continue
        for node in group:
            if _local_name(node.tag).casefold() != "node":
                continue
            identifier = _parse_identifier(node.get("id"))
            if identifier in coordinates:
                raise ValueError("FEB node identifiers are duplicated")
            raw_coordinates = [
                item for item in _INTEGER_LIST.split((node.text or "").strip()) if item
            ]
            if len(raw_coordinates) != 3:
                raise ValueError("FEB node coordinates are invalid")
            try:
                point = tuple(float(item) for item in raw_coordinates)
            except ValueError as error:
                raise ValueError("FEB node coordinates are invalid") from error
            if len(point) != 3 or any(not math.isfinite(item) for item in point):
                raise ValueError("FEB node coordinates are invalid")
            node_ids.append(identifier)
            coordinates[identifier] = (point[0], point[1], point[2])
    if not node_ids:
        raise ValueError("FEB mesh has no nodes")
    node_indexes = {identifier: index for index, identifier in enumerate(node_ids)}

    cells: list[list[int]] = []
    vtk_types: list[int] = []
    counts: dict[str, int] = {}
    element_ids: set[int] = set()
    for group in root.iter():
        if _local_name(group.tag).casefold() != "elements":
            continue
        element_type = group.get("type")
        if not isinstance(element_type, str):
            raise ValueError("FEB element type is missing")
        canonical_type = element_type.strip().casefold()
        profile = _ELEMENT_PROFILES.get(canonical_type)
        if profile is None:
            raise ValueError("FEB element type is unsupported for physical evidence")
        vtk_id, nodes_per_element, _, _ = profile
        for element in group:
            if _local_name(element.tag).casefold() not in {"elem", "element"}:
                continue
            identifier = _parse_identifier(element.get("id"))
            if identifier in element_ids:
                raise ValueError("FEB element identifiers are duplicated")
            element_ids.add(identifier)
            raw_nodes = [item for item in _INTEGER_LIST.split((element.text or "").strip()) if item]
            if len(raw_nodes) != nodes_per_element:
                raise ValueError("FEB element connectivity is invalid")
            try:
                referenced = [_parse_identifier(item) for item in raw_nodes]
                cell = [node_indexes[item] for item in referenced]
            except KeyError as error:
                raise ValueError("FEB element references an unknown node") from error
            if len(set(cell)) != len(cell):
                raise ValueError("FEB element repeats a node")
            cells.append(cell)
            vtk_types.append(vtk_id)
            counts[canonical_type] = counts.get(canonical_type, 0) + 1
    if not cells:
        raise ValueError("FEB mesh has no supported solid elements")

    topology = {"cells": cells, "cell_types": vtk_types}
    topology_sha256 = hashlib.sha256(
        json.dumps(topology, sort_keys=True, separators=(",", ":")).encode("ascii")
    ).hexdigest()
    cell_types = tuple(
        {
            "vtk_id": _ELEMENT_PROFILES[name][0],
            "name": name,
            "nodes": _ELEMENT_PROFILES[name][1],
            "elements": counts[name],
            "integration_rule": _ELEMENT_PROFILES[name][2],
            "integration_points": _ELEMENT_PROFILES[name][3],
        }
        for name in sorted(counts, key=lambda item: _ELEMENT_PROFILES[item][0])
    )
    return _MeshInventory(len(node_ids), len(cells), topology_sha256, cell_types)


def _source_mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return value
    if not isinstance(value, (tuple, list)):
        return {}
    result: dict[str, object] = {}
    for item in value:
        if not isinstance(item, Mapping):
            continue
        name = next(
            (
                item.get(key)
                for key in ("condition", "name", "field")
                if isinstance(item.get(key), str) and str(item[key]).strip()
            ),
            None,
        )
        if isinstance(name, str):
            result[name] = item
    return result


def _input_is_approved(intent: IntentContract, input_sha256: str) -> bool:
    sources = _source_mapping(intent.condition_sources)
    for name in _REQUIRED_FACTS:
        source = sources.get(name)
        if not isinstance(source, Mapping):
            return False
        if (
            source.get("authoritative") is not True
            or source.get("current") is not True
            or source.get("artifact_sha256") != input_sha256
            or not isinstance(source.get("source"), str)
            or not str(source["source"]).strip()
        ):
            return False
    return True


def _geometry_matches(
    inventory: _MeshInventory,
    model_manifest: Mapping[str, object],
    geometry: Mapping[str, object] | None,
) -> bool:
    if geometry is None:
        return False
    if (
        model_manifest.get("node_count") != inventory.node_count
        or model_manifest.get("element_count") != inventory.element_count
        or geometry.get("node_count") != inventory.node_count
        or geometry.get("element_count") != inventory.element_count
        or geometry.get("topology_sha256") != inventory.topology_sha256
    ):
        return False
    raw_cell_types = geometry.get("cell_types")
    if not isinstance(raw_cell_types, (list, tuple)):
        return False
    return list(raw_cell_types) == [dict(item) for item in inventory.cell_types]


def _positive_jacobians(geometry: Mapping[str, object] | None) -> bool:
    if geometry is None:
        return False
    states = geometry.get("states")
    if not isinstance(states, (list, tuple)) or not states:
        return False
    for state in states:
        if not isinstance(state, Mapping):
            return False
        minimum = state.get("minimum_jacobian")
        count = _positive_integer(state.get("integration_point_count"))
        if (
            isinstance(minimum, bool)
            or not isinstance(minimum, (int, float))
            or not math.isfinite(float(minimum))
            or float(minimum) <= 0.0
            or count is None
        ):
            return False
    return True


def _rois(value: object) -> tuple[dict[str, str], ...] | None:
    if not isinstance(value, (tuple, list)) or not value:
        return None
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"id", "scope"}:
            return None
        identifier, scope = item["id"], item["scope"]
        if (
            not isinstance(identifier, str)
            or not identifier.strip()
            or identifier in seen
            or not isinstance(scope, str)
            or scope not in _ROI_ASSOCIATIONS
        ):
            return None
        seen.add(identifier)
        result.append({"id": identifier, "scope": scope})
    return tuple(result)


def _evaluations(
    value: object,
    rois: Sequence[Mapping[str, str]],
    model_manifest: Mapping[str, object],
    values: Mapping[str, object],
    requested_fields: tuple[str, ...],
) -> tuple[str, ...] | None:
    if not isinstance(value, (tuple, list)) or not value:
        return None
    roi_by_id = {item["id"]: item["scope"] for item in rois}
    model_fields = model_manifest.get("requested_fields")
    if not isinstance(model_fields, Mapping):
        return None
    result: list[str] = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {
            "field",
            "roi",
            "reduction",
            "states",
            "require_finite",
        }:
            return None
        field, roi = item["field"], item["roi"]
        if (
            not isinstance(field, str)
            or field not in requested_fields
            or field in result
            or not isinstance(roi, str)
            or roi not in roi_by_id
            or item["reduction"] != "range"
            or item["states"] != "all"
            or item["require_finite"] is not True
        ):
            return None
        model_field = model_fields.get(field)
        summary = values.get(field)
        if not isinstance(model_field, Mapping) or not isinstance(summary, Mapping):
            return None
        if model_field.get("association") != _ROI_ASSOCIATIONS[roi_by_id[roi]]:
            return None
        minimum, maximum = summary.get("minimum"), summary.get("maximum")
        if (
            isinstance(minimum, bool)
            or not isinstance(minimum, (int, float))
            or isinstance(maximum, bool)
            or not isinstance(maximum, (int, float))
            or not math.isfinite(float(minimum))
            or not math.isfinite(float(maximum))
            or float(minimum) > float(maximum)
        ):
            return None
        result.append(field)
    return tuple(result)


def _evaluate_physical_payload(
    *,
    intent: IntentContract,
    input_bytes: bytes,
    input_sha256: str,
    model_manifest: Mapping[str, object],
    geometry: Mapping[str, object] | None,
    values: Mapping[str, object],
    requested_fields: tuple[str, ...],
) -> PhysicalEvidenceEvaluation:
    """Evaluate already-bound data; callers must not treat this record as authority."""

    if type(intent) is not IntentContract:
        raise TypeError("physical evidence requires an exact IntentContract")
    if hashlib.sha256(input_bytes).hexdigest() != input_sha256:
        raise ValueError("physical evidence input digest differs")
    failures: dict[str, list[str]] = {
        name: [] for name in ("mesh", "jacobian", "roi", "evaluation")
    }
    try:
        inventory = _mesh_inventory(input_bytes)
    except ValueError as error:
        inventory = None
        failures["mesh"].append(str(error))
    approved = _input_is_approved(intent, input_sha256)
    if not approved:
        failures["mesh"].append("intent sources do not approve the exact FEB input")
    mesh_matches = inventory is not None and _geometry_matches(inventory, model_manifest, geometry)
    if inventory is not None and not mesh_matches:
        failures["mesh"].append("FEB and official FBS mesh identities differ")
    mesh_passed = approved and mesh_matches

    jacobian_passed = mesh_passed and _positive_jacobians(geometry)
    if not jacobian_passed:
        failures["jacobian"].append("all integration-point Jacobians are not positive")

    rois = _rois(intent.roi)
    roi_passed = mesh_passed and rois is not None
    if not roi_passed:
        failures["roi"].append("ROI intent is not an exact supported full-model selection")

    evaluations = (
        None
        if rois is None
        else _evaluations(
            intent.evaluation_quantities,
            rois,
            model_manifest,
            values,
            requested_fields,
        )
    )
    evaluation_passed = roi_passed and evaluations is not None
    if not evaluation_passed:
        failures["evaluation"].append(
            "evaluation intent is not bound to finite official FBS fields"
        )

    passed = {
        "mesh": mesh_passed,
        "jacobian": jacobian_passed,
        "roi": roi_passed,
        "evaluation": evaluation_passed,
    }
    raw_states = None if geometry is None else geometry.get("states")
    document_states = list(raw_states) if isinstance(raw_states, (list, tuple)) else []
    documents: dict[str, Mapping[str, object]] = {
        "mesh": {
            "input_sha256": input_sha256,
            "node_count": None if inventory is None else inventory.node_count,
            "element_count": None if inventory is None else inventory.element_count,
            "topology_sha256": None if inventory is None else inventory.topology_sha256,
        },
        "jacobian": {
            "all_integration_points_positive": jacobian_passed,
            "states": document_states,
        },
        "roi": {"roi_ids": [] if rois is None else [item["id"] for item in rois]},
        "evaluation": {"fields": [] if evaluations is None else list(evaluations)},
    }
    return PhysicalEvidenceEvaluation(
        passed,
        documents,
        {name: tuple(items) for name, items in failures.items()},
    )


__all__ = ["PhysicalEvidenceEvaluation"]
