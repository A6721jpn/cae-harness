"""Fail-closed physical-result evaluation for an exact approved FEB input."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import weakref
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import NoReturn

from febio_cae_harness.contracts import IntentContract
from febio_cae_harness.evidence import IntentSnapshotAuthority
from febio_cae_harness.solver.official_fbs import (
    OfficialFbsResultReceipt,
    require_official_fbs_result,
)
from febio_cae_harness.solver.supervisor import SolverSupervisor
from febio_cae_harness.solver.types import (
    SolverRunResult,
    _validate_launch_capability,
)

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
class _PhysicalAuthorityBinding:
    token: object
    supervisor: SolverSupervisor
    result: SolverRunResult
    receipt: OfficialFbsResultReceipt
    snapshot: IntentSnapshotAuthority
    input_snapshot: tuple[object, ...]
    evaluation_sha256: str


class PhysicalEvidenceAuthority:
    """Opaque live authority for physical evidence from one exact solver result."""

    __slots__ = ("_token", "__weakref__")

    def __new__(cls, *args: object, **kwargs: object) -> PhysicalEvidenceAuthority:
        del args, kwargs
        raise TypeError("physical evidence authorities are issued by the reporting boundary")

    def __init_subclass__(cls, **kwargs: object) -> None:
        del kwargs
        raise TypeError("PhysicalEvidenceAuthority cannot be subclassed")

    def __setattr__(self, name: str, value: object) -> None:
        del name, value
        raise AttributeError("physical evidence authorities are immutable")

    def __delattr__(self, name: str) -> None:
        del name
        raise AttributeError("physical evidence authorities are immutable")

    def __copy__(self) -> PhysicalEvidenceAuthority:
        raise TypeError("physical evidence authorities cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> PhysicalEvidenceAuthority:
        del memo
        raise TypeError("physical evidence authorities cannot be copied")

    def __reduce__(self) -> NoReturn:
        raise TypeError("physical evidence authorities cannot be serialized")

    @property
    def case_id(self) -> str:
        binding, _ = _validated_physical_evidence(self)
        return binding.snapshot.case_id

    @property
    def intent_sha256(self) -> str:
        binding, _ = _validated_physical_evidence(self)
        return binding.snapshot.intent_sha256

    @property
    def attempt_id(self) -> str:
        binding, _ = _validated_physical_evidence(self)
        return binding.supervisor._attempt_id

    @property
    def passed(self) -> Mapping[str, bool]:
        _, evaluation = _validated_physical_evidence(self)
        return MappingProxyType(dict(evaluation.passed))

    def authoritative(self, kind: str) -> bool:
        _, evaluation = _validated_physical_evidence(self)
        if kind not in evaluation.passed:
            raise ValueError("unknown physical evidence kind")
        return evaluation.passed[kind]

    def document(self, kind: str) -> dict[str, object]:
        binding, evaluation = _validated_physical_evidence(self)
        if kind not in evaluation.documents:
            raise ValueError("unknown physical evidence kind")
        document = _plain(evaluation.documents[kind])
        if not isinstance(document, dict):  # pragma: no cover - evaluation guard
            raise TypeError("physical evidence document is invalid")
        return {
            "authority": "official" if evaluation.passed[kind] else "unverified",
            "case_id": binding.snapshot.case_id,
            "intent_sha256": binding.snapshot.intent_sha256,
            "attempt_id": binding.supervisor._attempt_id,
            "kind": kind,
            "satisfies_intent": evaluation.passed[kind],
            "schema_version": "febio-cae-physical-evidence/v1",
            "verified": evaluation.passed[kind],
            **document,
        }


_PHYSICAL_AUTHORITIES: weakref.WeakKeyDictionary[
    PhysicalEvidenceAuthority, _PhysicalAuthorityBinding
] = weakref.WeakKeyDictionary()


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


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _evaluation_sha256(evaluation: PhysicalEvidenceEvaluation) -> str:
    payload = {
        "passed": _plain(evaluation.passed),
        "documents": _plain(evaluation.documents),
        "failures": _plain(evaluation.failures),
    }
    try:
        raw = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, ValueError) as error:  # pragma: no cover - evaluator guard
        raise TypeError("physical evidence evaluation is invalid") from error
    return hashlib.sha256(raw).hexdigest()


def _read_bound_input(path: object, expected: tuple[object, ...]) -> bytes:
    if not isinstance(path, Path) or len(expected) != 6:
        raise TypeError("physical evidence input binding is invalid")
    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise TypeError("physical evidence input is not an exact regular file")
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            opened_identity = (
                opened.st_dev,
                opened.st_ino,
                opened.st_nlink,
                opened.st_size,
                opened.st_mtime_ns,
            )
            expected_identity = tuple(expected[:5])
            metadata_identity = (
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_nlink,
                metadata.st_size,
                metadata.st_mtime_ns,
            )
            if opened_identity != metadata_identity or opened_identity != expected_identity:
                raise TypeError("physical evidence input identity changed")
            payload = stream.read()
            finished = os.fstat(stream.fileno())
    except TypeError:
        raise
    except OSError as error:
        raise TypeError("physical evidence input is unavailable") from error
    if (
        finished.st_dev,
        finished.st_ino,
        finished.st_nlink,
        finished.st_size,
        finished.st_mtime_ns,
    ) != tuple(expected[:5]):
        raise TypeError("physical evidence input changed while reading")
    digest = hashlib.sha256(payload).hexdigest()
    if digest != expected[5]:
        raise TypeError("physical evidence input digest changed")
    return payload


def _live_physical_evaluation(
    supervisor: SolverSupervisor,
    result: SolverRunResult,
    receipt: OfficialFbsResultReceipt,
) -> tuple[IntentSnapshotAuthority, tuple[object, ...], PhysicalEvidenceEvaluation]:
    if (
        type(supervisor) is not SolverSupervisor
        or type(result) is not SolverRunResult
        or type(receipt) is not OfficialFbsResultReceipt
    ):
        raise TypeError("physical evidence requires exact solver and FBS authorities")
    if supervisor._validate_result(result) is not result:
        raise TypeError("physical evidence solver result binding is invalid")
    capability_record, _, intent_id, attempt_id, _, _, _ = _validate_launch_capability(
        supervisor._launch_capability
    )
    snapshot = capability_record.intent_snapshot
    if type(snapshot) is not IntentSnapshotAuthority:
        raise TypeError("physical evidence intent snapshot is invalid")
    input_snapshot = capability_record.input_snapshot
    if not isinstance(input_snapshot, tuple):  # pragma: no cover - issuance guard
        raise TypeError("physical evidence input snapshot is invalid")
    if (
        snapshot.intent_sha256 != intent_id
        or supervisor._attempt_id != attempt_id
        or result.fbs_validation is not receipt.validation
        or require_official_fbs_result(receipt.validation) is not receipt
        or receipt.xplt_path != result.xplt_path
        or receipt.requested_fields != supervisor.spec.requested_fields
    ):
        raise TypeError("physical evidence authority binding differs")
    input_bytes = _read_bound_input(supervisor.spec.input_path, input_snapshot)
    final_record, _, final_intent_id, final_attempt_id, _, _, _ = _validate_launch_capability(
        supervisor._launch_capability
    )
    if (
        final_record is not capability_record
        or final_record.intent_snapshot is not snapshot
        or final_record.input_snapshot != input_snapshot
        or final_intent_id != intent_id
        or final_attempt_id != attempt_id
        or supervisor._validate_result(result) is not result
    ):
        raise TypeError("physical evidence authority changed during evaluation")
    model_manifest = receipt.model_manifest
    geometry = receipt.geometry_summary
    values = receipt.values
    if not isinstance(model_manifest, Mapping) or not isinstance(values, Mapping):
        raise TypeError("physical evidence official FBS payload is invalid")
    evaluation = _evaluate_physical_payload(
        intent=snapshot.intent,
        input_bytes=input_bytes,
        input_sha256=str(input_snapshot[5]),
        model_manifest=model_manifest,
        geometry=geometry,
        values=values,
        requested_fields=receipt.requested_fields,
    )
    return snapshot, input_snapshot, evaluation


def issue_physical_evidence(
    supervisor: SolverSupervisor,
    result: SolverRunResult,
    receipt: OfficialFbsResultReceipt,
) -> PhysicalEvidenceAuthority:
    """Issue a live authority after evaluating one exact input/result binding."""

    snapshot, input_snapshot, evaluation = _live_physical_evaluation(supervisor, result, receipt)
    authority = object.__new__(PhysicalEvidenceAuthority)
    token = object()
    object.__setattr__(authority, "_token", token)
    _PHYSICAL_AUTHORITIES[authority] = _PhysicalAuthorityBinding(
        token,
        supervisor,
        result,
        receipt,
        snapshot,
        input_snapshot,
        _evaluation_sha256(evaluation),
    )
    return authority


def _validated_physical_evidence(
    value: object,
    *,
    supervisor: SolverSupervisor | None = None,
    result: SolverRunResult | None = None,
    receipt: OfficialFbsResultReceipt | None = None,
) -> tuple[_PhysicalAuthorityBinding, PhysicalEvidenceEvaluation]:
    if type(value) is not PhysicalEvidenceAuthority:
        raise TypeError("value is not an exact PhysicalEvidenceAuthority")
    binding = _PHYSICAL_AUTHORITIES.get(value)
    try:
        token = object.__getattribute__(value, "_token")
    except AttributeError as error:
        raise TypeError("PhysicalEvidenceAuthority was not issued") from error
    if binding is None or binding.token is not token:
        raise TypeError("PhysicalEvidenceAuthority was not issued")
    if (
        (supervisor is not None and supervisor is not binding.supervisor)
        or (result is not None and result is not binding.result)
        or (receipt is not None and receipt is not binding.receipt)
    ):
        raise TypeError("PhysicalEvidenceAuthority belongs to another result")
    snapshot, input_snapshot, evaluation = _live_physical_evaluation(
        binding.supervisor,
        binding.result,
        binding.receipt,
    )
    if (
        snapshot is not binding.snapshot
        or input_snapshot != binding.input_snapshot
        or _evaluation_sha256(evaluation) != binding.evaluation_sha256
    ):
        raise TypeError("PhysicalEvidenceAuthority binding changed")
    return binding, evaluation


__all__ = [
    "PhysicalEvidenceAuthority",
    "PhysicalEvidenceEvaluation",
    "issue_physical_evidence",
]
