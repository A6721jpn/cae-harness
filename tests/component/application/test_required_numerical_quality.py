"""Registered numerical completeness coverage at the public run-status boundary."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import test_comparison as comparison
from test_comparison import _configure, _result
from test_persistence_authority import _created, _populate_complete, _profile
from test_planar_edit_validation import prepared
from test_registered_execution import _build
from test_planar_preparation import _isolate, _mesh_study_request, prepared_input
from test_reported_solver_norms import _input as _reported_input
from test_reported_solver_norms import _log as _reported_log

from febio_cae.domain import (
    AttemptRecord,
    ExecutionBundle,
    ExecutionSetting,
    FileEntry,
    NumericResultData,
    OutputObservation,
    PollResult,
    ProcessIdentity,
    OutputMapping,
    ReadResult,
    ReadStatus,
    ResultDataRef,
    ResultManifest,
    RunState,
    SolverControl,
    Quantity,
)
from febio_cae.domain.ports import PortError


def _numerical_rows(result: dict[str, object]) -> tuple[dict[str, Any], dict[str, Any]]:
    coverage = cast(dict[str, Any], result["required_quality"])
    rows = {row["criterion_id"]: row for row in coverage["numerical"]}
    return rows, coverage


def _complete_summary(
    tmp_path: Path,
    *,
    factor: float = 1.0,
    configure: Any = _configure,
) -> dict[str, object]:
    service, created, storage, revision = prepared(tmp_path, configure)
    _result(service, storage, revision, "completeness", (0.0, 0.5, 1.0), factor)
    return service.run_status(created.case_id, "run-completeness")


def _configure_unsupported_force_type(service: Any, spec: Any) -> Any:
    configured = _configure(service, spec)
    registered = service.compatibility.get_profile(configured.outputs.profile.profile_id)
    profile = replace(
        registered,
        profile_id="comparison-unsupported-force-type",
        output_mappings=tuple(
            replace(mapping, value_type="UNSUPPORTED_VECTOR")
            if mapping.canonical_id == "contact_force"
            else mapping
            for mapping in registered.output_mappings
        ),
    )
    service.register_profile(profile)
    digest = hashlib.sha256(profile.to_bytes()).hexdigest()

    def rebind(reference: Any) -> Any:
        return replace(reference, profile_id=profile.profile_id, record_digest=digest)

    return replace(
        configured,
        outputs=replace(configured.outputs, profile=rebind(configured.outputs.profile)),
        quality_policy=replace(
            configured.quality_policy,
            profile=rebind(configured.quality_policy.profile),
        ),
        solver_policy=replace(
            configured.solver_policy,
            profile=rebind(configured.solver_policy.profile),
        ),
    )


def _patch_force_numeric(
    monkeypatch: pytest.MonkeyPatch,
    *,
    entity_ids: tuple[str, ...] | None = None,
    component_ids: tuple[str, ...] | None = None,
) -> None:
    original = comparison.NumericResultData

    def construct(
        reference: Any,
        mapping: Any,
        axis_id: Any,
        axis_unit: Any,
        axis_values: Any,
        current_entity_ids: Any,
        current_component_ids: Any,
        values: Any,
    ) -> NumericResultData:
        if mapping.canonical_id == "contact_force":
            current_entity_ids = entity_ids or current_entity_ids
            current_component_ids = component_ids or current_component_ids
            width = len(current_entity_ids) * len(current_component_ids)
            values = tuple(tuple(row[:width]) for row in values)
        return original(
            reference,
            mapping,
            axis_id,
            axis_unit,
            axis_values,
            current_entity_ids,
            current_component_ids,
            values,
        )

    monkeypatch.setattr(comparison, "NumericResultData", construct)


def test_registered_complete_result_passes_only_execution_completeness(
    tmp_path: Path,
) -> None:
    result = _complete_summary(tmp_path)
    rows, coverage = _numerical_rows(result)

    assert rows["execution_result_completeness"]["status"] == "PASS"
    remaining = {
        criterion_id: row["status"]
        for criterion_id, row in rows.items()
        if criterion_id != "execution_result_completeness"
    }
    assert remaining == {
        "contact_quality": "UNVERIFIED",
        "motion_support_contact_fidelity": "UNVERIFIED",
        "quasistatic_equilibrium": "UNVERIFIED",
        "solver_residual": "UNVERIFIED",
        "mesh_dependence": "UNVERIFIED",
    }
    assert result["quality_status"] == "UNVERIFIED"
    assert result["task_status"] == "NEEDS_QUALITY"
    assert coverage["physical_applicability"]["status"] == "UNVERIFIED"
    assert coverage["reported_solver_norms"]["native_qualification"] == "UNVERIFIED"


def test_known_trusted_quality_fail_still_wins_over_complete_result(
    tmp_path: Path,
) -> None:
    result = _complete_summary(tmp_path, factor=100.0)
    rows, _ = _numerical_rows(result)

    assert rows["execution_result_completeness"]["status"] == "PASS"
    assert result["quality_status"] == "FAIL"
    assert result["task_status"] == "FAILED"


def _registered_publication_summary(
    tmp_path: Path,
    *,
    times: tuple[float, ...] = (0.0, 1.0),
    include_output: bool = True,
) -> dict[str, object]:
    service, created, storage = _created(tmp_path)
    _populate_complete(service, created)
    frozen = service.freeze_case(created.case_id).revision
    assert frozen is not None
    request = frozen.spec.outputs.requests[0]

    def produce(bundle: Any, inputs: dict[str, bytes]) -> dict[str, bytes]:
        del bundle, inputs
        return {request.request_id: b"synthetic result"} if include_output else {}

    def read(attempt: Any, bundle: Any, files: Any, registered: Any) -> ResultManifest:
        mapping = _profile(bundle.profile_id).output_mappings[0]
        entity_ids = ("1",)
        component_ids = ("z",)
        width = len(entity_ids) * len(component_ids)
        numeric = NumericResultData(
            ResultDataRef(
                "numeric-shape",
                "0" * 64,
                "numeric-result-v1",
                files[0].logical_path,
                bundle.bundle_digest,
                attempt.attempt_id,
            ),
            mapping,
            "state_time",
            "s",
            times,
            entity_ids,
            component_ids,
            tuple(tuple(float(index) for _ in range(width)) for index in range(len(times))),
        )
        numeric = replace(
            numeric,
            reference=replace(numeric.reference, content_digest=numeric.expected_content_digest),
        )
        registered.register_numeric_data(numeric)
        return ResultManifest(
            "manifest-shape",
            attempt.attempt_id,
            bundle.bundle_digest,
            files,
            ReadResult(
                ReadStatus.VALIDATED,
                _profile(bundle.profile_id).reader,
                (
                    OutputObservation(
                        request.request_id,
                        mapping.location,
                        mapping.value_type,
                        mapping.unit,
                        mapping.frame,
                        mapping.measure_id,
                        len(times),
                        numeric.reference,
                    ),
                ),
                (),
            ),
        )

    manifest = service._execute_registered(
        created.case_id,
        frozen.revision_id,
        build=_build,
        produce=produce,
        read=read,
    )
    with sqlite3.connect(storage.registry_path) as connection:
        row = connection.execute(
            "SELECT run_id FROM owners WHERE attempt_id=?", (manifest.attempt_id,)
        ).fetchone()
    assert row is not None
    return service.run_status(created.case_id, str(row[0]))


def test_synchronous_results_cannot_claim_a_native_runtime_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from febio_cae.application import _required_quality

    monkeypatch.setattr(_required_quality, "has_qualified_runtime", lambda bundle: True)
    summary = _registered_publication_summary(tmp_path)
    rows, coverage = _numerical_rows(summary)
    assert coverage["native_runtime_binding"]["status"] == "UNVERIFIED"
    assert all(
        row["status"] == "UNVERIFIED"
        for identifier, row in rows.items()
        if identifier != "execution_result_completeness"
    )


def test_missing_required_entity_is_unverified_after_observable_valid_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = _complete_summary(tmp_path)
    baseline_rows, _ = _numerical_rows(baseline)
    assert baseline_rows["execution_result_completeness"]["status"] == "PASS"

    defect_root = tmp_path / "missing-required-entity"
    defect_root.mkdir()
    _patch_force_numeric(monkeypatch, entity_ids=("part",))
    result = _complete_summary(defect_root)
    rows, _ = _numerical_rows(result)

    assert rows["execution_result_completeness"]["status"] == "UNVERIFIED"


def test_missing_requested_component_is_unverified_after_observable_valid_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = _complete_summary(tmp_path)
    baseline_rows, _ = _numerical_rows(baseline)
    assert baseline_rows["execution_result_completeness"]["status"] == "PASS"

    defect_root = tmp_path / "missing-requested-component"
    defect_root.mkdir()
    _patch_force_numeric(monkeypatch, component_ids=("x", "y"))
    result = _complete_summary(defect_root)
    rows, _ = _numerical_rows(result)

    assert rows["execution_result_completeness"]["status"] == "UNVERIFIED"


def test_unsupported_mapping_value_type_cannot_pass_complete_result(tmp_path: Path) -> None:
    baseline = _complete_summary(tmp_path)
    baseline_rows, _ = _numerical_rows(baseline)
    assert baseline_rows["execution_result_completeness"]["status"] == "PASS"

    defect_root = tmp_path / "unsupported-value-type"
    defect_root.mkdir()
    result = _complete_summary(defect_root, configure=_configure_unsupported_force_type)
    rows, _ = _numerical_rows(result)

    assert rows["execution_result_completeness"]["status"] == "UNVERIFIED"


@pytest.mark.parametrize("defect", ["missing-output", "missing-endpoint"])
def test_registered_missing_output_or_endpoint_is_rejected_before_summary(
    tmp_path: Path,
    defect: str,
) -> None:
    with pytest.raises((PortError, ValueError)):
        _registered_publication_summary(
            tmp_path,
            times=(0.0,) if defect == "missing-endpoint" else (0.0, 1.0),
            include_output=defect != "missing-output",
        )


def _scaled_mesh_backend(fixtures: Any) -> Any:
    from febio_cae.adapters.geometry import (
        BackendElement,
        BackendMesh,
        BackendMeshFace,
        BackendNode,
    )

    class MeshStudyBackend(fixtures.SyntheticBackend):
        def mesh(
            self,
            content: bytes,
            body_id: str,
            global_size_si: float,
            *,
            local_refinements: tuple[Any, ...] = (),
        ) -> BackendMesh:
            base = super().mesh(
                content,
                body_id,
                global_size_si,
                local_refinements=local_refinements,
            )
            stage = {0.002: 1, 0.001: 2, 0.0005: 3}.get(global_size_si)
            if stage is None:
                raise ValueError(f"unexpected synthetic study size: {global_size_si}")
            scale = global_size_si / 0.002
            nodes: list[BackendNode] = []
            elements: list[BackendElement] = []
            faces: list[BackendMeshFace] = []
            for copy_index in range(stage):
                node_offset = copy_index * len(base.nodes)
                element_offset = copy_index * len(base.elements)
                x_offset = copy_index * 0.02
                for node in base.nodes:
                    nodes.append(
                        BackendNode(
                            node.node_id + node_offset,
                            (
                                node.coordinates_si[0] * scale + x_offset,
                                node.coordinates_si[1] * scale,
                                node.coordinates_si[2] * scale,
                            ),
                        )
                    )
                for element in base.elements:
                    elements.append(
                        BackendElement(
                            element.element_id + element_offset,
                            element.element_type,
                            tuple(node_id + node_offset for node_id in element.node_ids),
                            element.body_id,
                            element.ordering_id,
                        )
                    )
                for face in base.faces:
                    faces.append(
                        BackendMeshFace(
                            f"{face.face_id}-copy-{copy_index}",
                            tuple(
                                element_id + element_offset
                                for element_id in face.adjacent_element_ids
                            ),
                            face.local_face_ids,
                            face.area_si,
                            face.centroid_si,
                            face.boundary_points_si,
                            face.source_face_id if copy_index == 0 else None,
                        )
                    )
            return BackendMesh(
                base.source_digest,
                base.geometry_digest,
                base.frame,
                base.body_id,
                nodes,
                elements,
                faces,
                base.ordering_id,
            )

    return MeshStudyBackend(geometry_digest="0" * 64)


def _source_local_mesh_backend(fixtures: Any) -> Any:
    from febio_cae.adapters.geometry import (
        BackendElement,
        BackendMesh,
        BackendMeshFace,
        BackendNode,
    )

    class SourceLocalStudyBackend(fixtures.SyntheticBackend):
        def mesh(
            self,
            content: bytes,
            body_id: str,
            global_size_si: float,
            *,
            local_refinements: tuple[Any, ...] = (),
        ) -> BackendMesh:
            if len(local_refinements) != 1:
                raise ValueError("source-local synthetic study requires one ball")
            base = super().mesh(
                content,
                body_id,
                global_size_si,
                local_refinements=(),
            )
            target = round(float(local_refinements[0].size_si), 10)
            stage_copies = {0.002: 1, 0.001: 2, 0.0005: 3}.get(target)
            if stage_copies is None:
                raise ValueError(f"unexpected synthetic local study size: {target}")
            nodes: list[BackendNode] = []
            elements: list[BackendElement] = []
            faces: list[BackendMeshFace] = []

            def add_copy(copy_index: int, scale: float, x_offset: float) -> None:
                node_offset = copy_index * len(base.nodes)
                element_offset = copy_index * len(base.elements)
                for node in base.nodes:
                    nodes.append(
                        BackendNode(
                            node.node_id + node_offset,
                            (
                                node.coordinates_si[0] * scale + x_offset,
                                node.coordinates_si[1] * scale,
                                node.coordinates_si[2] * scale,
                            ),
                        )
                    )
                for element in base.elements:
                    elements.append(
                        BackendElement(
                            element.element_id + element_offset,
                            element.element_type,
                            tuple(node_id + node_offset for node_id in element.node_ids),
                            element.body_id,
                            element.ordering_id,
                        )
                    )
                for face in base.faces:
                    faces.append(
                        BackendMeshFace(
                            f"{face.face_id}-copy-{copy_index}",
                            tuple(
                                element_id + element_offset
                                for element_id in face.adjacent_element_ids
                            ),
                            face.local_face_ids,
                            face.area_si,
                            face.centroid_si,
                            face.boundary_points_si,
                            face.source_face_id if copy_index == 0 else None,
                        )
                    )

            for copy_index in range(stage_copies):
                add_copy(copy_index, target / 0.002, 0.0)
            # A fixed, separated copy supplies the far-field control measurement.
            add_copy(stage_copies, 0.8, 0.02)
            return BackendMesh(
                base.source_digest,
                base.geometry_digest,
                base.frame,
                base.body_id,
                nodes,
                elements,
                faces,
                base.ordering_id,
            )

    return SourceLocalStudyBackend(geometry_digest="0" * 64)


def _mesh_study_payload(service: Any, request: dict[str, Any]) -> dict[str, Any]:
    from febio_cae.application._preparation_request import normalize_request
    from febio_cae.domain import PartialCaseSpec, QualityThreshold

    import copy

    base = normalize_request(request).values.to_case_spec()
    configured = comparison._configure(service, base)
    generation_profile = configured.mesh_policy.quality_profile
    profile = service.compatibility.get_profile(configured.solver_policy.profile.profile_id)
    frame = next(item.frame for item in profile.output_mappings)
    profile = replace(
        profile,
        output_mappings=tuple(profile.output_mappings)
        + (
            OutputMapping(
                "reaction", "reaction forces", "node", "VEC3F", "N", frame, -1, 1, "value"
            ),
            OutputMapping(
                "rigid_position", "rigid position", "rigid_body", "VEC3F", "m", frame, 1, 1, "value"
            ),
        ),
    )
    profile = replace(
        profile,
        profile_id="mesh-study-synthetic",
        solver=replace(profile.solver, version="4.12.0"),
    )
    service.register_profile(profile)
    profile_digest = hashlib.sha256(profile.to_bytes()).hexdigest()
    configured = replace(
        configured,
        rigid_tool=replace(
            configured.rigid_tool,
            primitive=replace(
                configured.rigid_tool.primitive,
                dimensions={
                    **configured.rigid_tool.primitive.dimensions,
                    "length": Quantity(20.0, "mm"),
                    "width": Quantity(20.0, "mm"),
                },
                placement=replace(
                    configured.rigid_tool.primitive.placement,
                    translation=replace(
                        configured.rigid_tool.primitive.placement.translation,
                        z=Quantity(2.0001, "mm"),
                    ),
                ),
            ),
        ),
        mesh_policy=replace(configured.mesh_policy, quality_profile=generation_profile),
        # This study exercises registered evidence, not the fixture's short runner deadline.
        budget=replace(configured.budget, max_elapsed=Quantity(120, "s")),
        **{
            name: replace(
                getattr(configured, name),
                profile=replace(
                    getattr(configured, name).profile,
                    profile_id=profile.profile_id,
                    record_digest=profile_digest,
                ),
            )
            for name in ("solver_policy", "outputs", "quality_policy")
        },
    )
    controls = {control.name: control.value for control in configured.solver_policy.controls} | {
        "dtol": Quantity(0.001, "1"),
        "etol": Quantity(0.01, "1"),
        "rtol": Quantity(0.001, "1"),
        "min_residual": Quantity(0, "1"),
        "max_refs": 50,
        "reform_augment": True,
        "max_ups": 10,
        "minaug": 0,
        "maxaug": 10,
        "laugon": 1,
        "tolerance": Quantity(0.01, "1"),
        "gaptol": Quantity(1e-8, "m"),
        "two_pass": False,
    }
    configured = replace(
        configured,
        solver_policy=replace(
            configured.solver_policy,
            controls=tuple(SolverControl(name, value) for name, value in controls.items()),
            increments=replace(
                configured.solver_policy.increments,
                initial_step=Quantity(0.1, "s"),
                minimum_step=Quantity(0.1, "s"),
                maximum_step=Quantity(0.1, "s"),
                adaptive=False,
                max_steps=10,
                max_step_retries=0,
            ),
        ),
    )
    partial = PartialCaseSpec(
        **{
            field: getattr(configured, field)
            for field in (
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
        }
    )
    payload = {
        "schema_version": "1",
        "values": partial.to_dict(),
        "evidence": request.get("evidence", []),
        "source_declarations": request.get("source_declarations", []),
    }
    values = payload["values"]
    assert isinstance(values, dict)
    source_digest = request["values"]["geometry"]["source_step_digest"]
    values["motion"]["samples"][-1]["displacement"] = {"value": 1.0e-7, "unit": "m"}

    def select_contact_faces(
        surface: dict[str, Any], evidence: dict[str, Any], face_ids: tuple[str, ...]
    ) -> None:
        surface["rule"] = {
            "schema_version": "1",
            "kind": "face_set",
            "geometry_digest": surface["geometry_digest"],
            "body_id": surface["body_id"],
            "frame": surface["frame"],
            "face_ids": [{"schema_version": "1", "value": face_id} for face_id in face_ids],
            "provenance": copy.deepcopy(evidence),
        }
        surface["resolution"] = None

    select_contact_faces(
        values["contact"]["part_surface"],
        values["contact"]["part_surface_evidence"],
        ("bottom-face",),
    )
    select_contact_faces(
        values["contact"]["tool_surface"],
        values["contact"]["tool_surface_evidence"],
        ("tool-body:face:1-3-4", "tool-body:face:1-2-3"),
    )
    select_contact_faces(
        values["rigid_tool"]["contact_surface"],
        values["contact"]["tool_surface_evidence"],
        ("tool-body:face:1-3-4", "tool-body:face:1-2-3"),
    )

    def bind_registered_evidence(value: Any) -> None:
        if isinstance(value, dict):
            if {"reference", "target_field", "content_digest"} <= value.keys():
                value["reference"] = "cad"
                value["content_digest"] = source_digest
            for child in value.values():
                bind_registered_evidence(child)
        elif isinstance(value, list):
            for child in value:
                bind_registered_evidence(child)

    bind_registered_evidence(values)
    values["geometry"]["geometry_digest"] = None
    values["geometry"]["inspection_digest"] = None

    def clear_part_geometry_digests(value: Any) -> None:
        if isinstance(value, dict):
            if (
                value.get("body_id") == values["geometry"]["body_id"]
                and "geometry_digest" in value
                and value.get("kind") != "face_set"
            ):
                value["geometry_digest"] = None
            for child in value.values():
                clear_part_geometry_digests(child)
        elif isinstance(value, list):
            for child in value:
                clear_part_geometry_digests(child)

    clear_part_geometry_digests(values)
    payload = _mesh_study_request(payload)
    values = payload["values"]
    outputs = values["outputs"]
    outputs["saved_times"] = [
        {"value": 0.0, "unit": "s"},
        {"value": 0.5, "unit": "s"},
        {"value": 1.0, "unit": "s"},
    ]
    saved_times = copy.deepcopy(outputs["saved_times"])
    part = next(item for item in outputs["requests"] if item["quantity_id"] == "displacement")
    force = next(item for item in outputs["requests"] if item["quantity_id"] == "contact_force")
    support = values["support"]["supports"][0]["selection"]

    def request_copy(template: dict[str, Any], **updates: Any) -> dict[str, Any]:
        result = copy.deepcopy(template)
        result.update(updates)
        result["evidence"]["target_field"] = f"outputs.requests.{result['request_id']}"
        return result

    part = request_copy(part, request_id="part_displacement")
    tool = request_copy(
        part,
        request_id="tool_displacement",
        selection=copy.deepcopy(force["selection"]),
    )
    reaction = request_copy(
        part,
        request_id="support_reaction",
        quantity_id="reaction",
        selection=copy.deepcopy(support),
        display_unit="N",
    )
    position = request_copy(
        force,
        request_id="tool_position",
        quantity_id="rigid_position",
        display_unit="m",
    )
    outputs["requests"] = [part, tool, reaction, force, position]

    def evaluation(
        evaluation_id: str,
        output_request_id: str,
        selection: dict[str, Any],
        aggregation_id: str = "peak",
    ) -> dict[str, Any]:
        result = copy.deepcopy(outputs["evaluations"][0])
        result.update(
            evaluation_id=evaluation_id,
            output_request_id=output_request_id,
            aggregation_id=aggregation_id,
            selection=copy.deepcopy(selection),
            state_times=copy.deepcopy(saved_times),
        )
        result["evidence"]["target_field"] = f"outputs.evaluations.{evaluation_id}"
        return result

    outputs["evaluations"] = [
        evaluation("ev_part_displacement", part["request_id"], part["selection"]),
        evaluation("ev_tool_displacement", tool["request_id"], tool["selection"]),
        evaluation("ev_support_reaction", reaction["request_id"], reaction["selection"]),
        evaluation("ev_tool_force", force["request_id"], force["selection"]),
        evaluation(
            "ev_mesh_force", force["request_id"], force["selection"], aggregation_id="identity"
        ),
        evaluation("ev_tool_position", position["request_id"], position["selection"]),
    ]

    criterion_main = next(
        item
        for item in values["quality_policy"]["criteria"]
        if item["metric_id"] == "peak_abs_value"
    )
    criterion_main["criterion_id"] = "part_displacement_limit"
    criterion_main["evaluation_ids"] = ["ev_part_displacement"]
    criterion_main["evidence"]["target_field"] = "quality_policy.criteria.part_displacement_limit"

    def criterion(
        criterion_id: str,
        metric_id: str,
        evaluation_ids: list[str],
        thresholds: list[tuple[str, float, str]],
    ) -> dict[str, Any]:
        evidence = copy.deepcopy(criterion_main["evidence"])
        evidence["target_field"] = f"quality_policy.criteria.{criterion_id}"
        return {
            "schema_version": "1",
            "criterion_id": criterion_id,
            "metric_id": metric_id,
            "evaluation_ids": evaluation_ids,
            "thresholds": [
                QualityThreshold(parameter, Quantity(value, unit)).to_dict()
                for parameter, value, unit in thresholds
            ],
            "applicability_reason": "registered synthetic planar quality fixture",
            "evidence": evidence,
        }

    values["quality_policy"]["criteria"] = [
        criterion_main,
        criterion(
            "contact_quality",
            "planar_contact",
            ["ev_part_displacement", "ev_tool_displacement", "ev_tool_position", "ev_tool_force"],
            [
                ("initial_interference_max", 1e-9, "m"),
                ("contact_gap_max", 1e-6, "m"),
                ("penetration_max", 1e-6, "m"),
                ("force_absolute_floor", 1e-3, "N"),
                ("contact_start", 0.5, "s"),
                ("contact_end", 1.0, "s"),
            ],
        ),
        criterion(
            "motion_support_contact_fidelity",
            "motion_support_contact_fidelity",
            ["ev_part_displacement", "ev_tool_displacement", "ev_tool_position"],
            [("motion_error_max", 1e-4, "m"), ("support_displacement_max", 1e-8, "m")],
        ),
        criterion(
            "quasistatic_equilibrium",
            "quasistatic_equilibrium",
            ["ev_support_reaction", "ev_tool_force"],
            [("relative_max", 1e-6, "1"), ("absolute_floor", 1e-6, "N")],
        ),
        criterion(
            "mesh_dependence",
            "mesh_dependence",
            ["ev_mesh_force"],
            [
                ("coarse_size", 2.0, "mm"),
                ("refined_size", 1.0, "mm"),
                ("fine_size", 0.5, "mm"),
                ("relative_max", 0.02, "1"),
                ("absolute_floor", 0.001, "N"),
            ],
        ),
    ]
    assert len(outputs["evaluations"]) == 6
    return payload


def _source_local_study_payload(service: Any, request: dict[str, Any]) -> dict[str, Any]:
    import copy

    payload = _mesh_study_payload(service, request)
    values = payload["values"]
    assert isinstance(values, dict)
    values["mesh_policy"]["global_size"] = {"value": 5.0, "unit": "mm"}
    part_selection = copy.deepcopy(values["contact"]["part_surface"])
    part_selection["name"] = "source-local-body"
    part_selection["stated_role"] = "mesh_refinement"
    part_selection["rule"] = {
        "schema_version": "1",
        "kind": "whole_body",
        "body_id": values["geometry"]["body_id"],
    }
    part_selection["resolution"] = None
    source_frame = values["geometry"]["placement"]["source_frame"]
    values["mesh_policy"]["local_refinements"] = [
        {
            "schema_version": "1",
            "refinement_id": "part-ball",
            "selection": part_selection,
            "size": {"value": 2.0, "unit": "mm"},
            "region": {
                "schema_version": "1",
                "kind": "source_local_ball",
                "center": {
                    "schema_version": "1",
                    "frame": source_frame,
                    "x": {"value": 0.0, "unit": "mm"},
                    "y": {"value": 0.0, "unit": "mm"},
                    "z": {"value": 0.0, "unit": "mm"},
                },
                "radius": {"value": 6.0, "unit": "mm"},
            },
        }
    ]
    criterion = next(
        item
        for item in values["quality_policy"]["criteria"]
        if item["metric_id"] == "mesh_dependence"
    )
    criterion["criterion_id"] = "source_local_mesh_study"
    criterion["metric_id"] = "source_local_mesh_dependence"
    criterion["evidence"]["target_field"] = "quality_policy.criteria.source_local_mesh_study"
    return payload


def _registered_mesh_result(
    service: Any,
    created: Any,
    revision: Any,
    *,
    label: str,
    factor: float,
    include_log: bool,
) -> str:
    from febio_cae.adapters.febio import QualityAdapter
    from febio_cae.domain.codec import encode_record

    storage = service._storage(created.case_id)
    times = (0.0, 0.5, 1.0)

    def build(current: Any, destination: Path) -> tuple[Any, ExecutionBundle, dict[str, bytes]]:
        registration = storage.resolve_revision_mesh_quality(current)
        mesh = service._planar_execution_mesh(storage, registration, current)
        profile = service.compatibility.get_profile(current.spec.solver_policy.profile.profile_id)
        input_payload = _reported_input(SimpleNamespace(spec=current.spec))
        entry = FileEntry(
            "input/case.feb",
            hashlib.sha256(input_payload).hexdigest(),
            len(input_payload),
            "input",
        )
        bundle = ExecutionBundle(
            "mesh-study-" + label,
            current.case_id,
            current.revision_id,
            current.spec_digest,
            mesh.artifact_digest,
            profile.profile_id,
            profile.solver,
            (entry,),
            (
                "synthetic-febio",
                "-i",
                entry.logical_path,
                "-o",
                "output/solver.log",
                "-p",
                "output/results.xplt",
                "-noappend",
                "-noconfig",
            ),
            str(destination),
            1,
            (),
        )
        return mesh, bundle, {entry.logical_path: input_payload}

    class Runner:
        def __init__(self, ownership: Any, root: Path, inputs: Any) -> None:
            self.ownership = ownership
            self.root = root

        def start(self, bundle: Any, owner: Any, budget: Any) -> AttemptRecord:
            self.ownership.claim(owner)
            process_root = (
                self.root
                / owner.case_id
                / owner.run_id
                / owner.attempt_id
                / str(owner.owner_generation)
            )
            (process_root / "output").mkdir(parents=True)
            (process_root / "output/results.xplt").write_bytes(b"registered synthetic XPLT result")
            if include_log:
                log = _reported_log("pass").replace(
                    b"2.000000e+00 1.000000e-02 ", b"2.000000e+00 1.000000e-03 "
                )
                (process_root / "output/solver.log").write_bytes(log)
            return AttemptRecord(
                owner.attempt_id,
                owner.run_id,
                owner.case_id,
                bundle.revision_id,
                owner.owner_generation,
                bundle.bundle_digest,
                RunState.RUNNING,
                ProcessIdentity(
                    bundle.argv[0],
                    bundle.tool.executable_digest,
                    bundle.argv,
                    str(process_root),
                    bundle.thread_count,
                    "registered-synthetic-runner",
                ),
                (
                    *bundle.settings,
                    ExecutionSetting("attempt_root", str(process_root)),
                    ExecutionSetting("max_elapsed_seconds", budget.max_elapsed.to_si().value),
                ),
            )

        def poll(self, attempt: Any, owner: Any) -> PollResult:
            self.ownership.validate(owner, attempt)
            return PollResult(
                attempt.transition_to(RunState.DRAINING).transition_to(RunState.VALIDATING),
                (),
            )

        def cancel(self, attempt: Any, owner: Any) -> Any:
            raise AssertionError("successful registered study does not cancel")

        def reconcile(self, attempt: Any, owner: Any) -> Any:
            raise AssertionError("successful registered study has no restart path")

    def read(
        attempt: Any, bundle: Any, files: tuple[FileEntry, ...], registered: Any
    ) -> ResultManifest:
        profile = service.compatibility.get_profile(bundle.profile_id)
        result_entry = next(item for item in files if item.role == "result")
        mesh = service._planar_execution_mesh(
            storage,
            storage.resolve_revision_mesh_quality(revision),
            revision,
        )
        observations: list[OutputObservation] = []
        part_body = revision.spec.geometry.body_id.value
        part_nodes = {
            str(node_id)
            for element in mesh.elements
            if element.body_id == part_body
            for node_id in element.node_ids
        }
        travel = float(revision.spec.motion.samples[-1].displacement.to_si().value)
        for mapping in profile.output_mappings:
            if mapping.canonical_id == "displacement":
                entities = tuple(str(node.node_id) for node in mesh.nodes)
                rows = tuple(
                    tuple(
                        component
                        for node_id in entities
                        for component in (
                            0.0,
                            0.0,
                            -time * travel if node_id not in part_nodes else 0.0,
                        )
                    )
                    for time in times
                )
            elif mapping.canonical_id == "reaction":
                entities = tuple(
                    str(node.node_id) for node in mesh.nodes if str(node.node_id) in part_nodes
                )
                part_count = len(entities)
                rows = tuple(
                    tuple(
                        component
                        for _ in entities
                        for component in (0.0, 0.0, 2.0 * time * factor / part_count)
                    )
                    for time in times
                )
            elif mapping.canonical_id == "contact_force":
                entities = (revision.spec.rigid_tool.primitive.body_id.value,)
                # The registered numeric record is canonical; the profile's -1 raw sign
                # restores FEBio's applied force for equilibrium and remains visible to
                # the mesh-study curve consumer.
                rows = tuple((0.0, 0.0, 2.0 * time * factor) for time in times)
            elif mapping.canonical_id == "rigid_position":
                entities = (revision.spec.rigid_tool.primitive.body_id.value,)
                base_z = revision.spec.rigid_tool.primitive.placement.translation.z.to_si().value
                rows = tuple((0.0, 0.0, base_z - time * travel) for time in times)
            else:
                raise AssertionError(f"unexpected synthetic mapping: {mapping.canonical_id}")
            data = NumericResultData(
                ResultDataRef(
                    f"{label}-{mapping.canonical_id}",
                    "0" * 64,
                    "numeric-result-v1",
                    result_entry.logical_path,
                    bundle.bundle_digest,
                    attempt.attempt_id,
                ),
                mapping,
                "state_time",
                "s",
                times,
                entities,
                ("x", "y", "z"),
                rows,
            )
            data = replace(
                data,
                reference=replace(data.reference, content_digest=data.expected_content_digest),
            )
            registered.register_numeric_data(data)
            observations.append(
                OutputObservation(
                    mapping.canonical_id,
                    mapping.location,
                    mapping.value_type,
                    mapping.unit,
                    mapping.frame,
                    mapping.measure_id,
                    len(times),
                    data.reference,
                )
            )
        by_quantity = {item.output_id: item for item in observations}
        bound = tuple(
            replace(by_quantity[request.quantity_id], output_id=request.request_id)
            for request in revision.spec.outputs.requests
        )
        return ResultManifest(
            "mesh-study-manifest-" + label,
            attempt.attempt_id,
            bundle.bundle_digest,
            files,
            ReadResult(ReadStatus.VALIDATED, profile.reader, bound, ()),
        )

    manifest = service._execute_ports(
        created.case_id,
        revision.revision_id,
        build=build,
        runner_factory=Runner,
        read=read,
    )
    registration = storage.resolve_revision_mesh_quality(revision)
    mesh = service._planar_execution_mesh(storage, registration, revision)
    profile = service.compatibility.get_profile(revision.spec.solver_policy.profile.profile_id)
    quality = QualityAdapter().assess(manifest, revision, mesh, profile, storage)
    storage.ingest_source(
        asset_id="quality-" + quality.assessment_id[:24],
        source_kind="registered_document",
        media_type="application/json",
        content=encode_record(quality),
    )
    with sqlite3.connect(storage.registry_path) as connection:
        row = connection.execute(
            "SELECT run_id FROM owners WHERE attempt_id=?", (manifest.attempt_id,)
        ).fetchone()
    assert row is not None
    return str(row[0])


def _registered_mesh_study_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    factors: tuple[float, float, float],
    logs: tuple[bool, bool, bool],
) -> dict[str, object]:
    import copy
    import importlib

    service, created, request, _, _ = prepared_input.__wrapped__(tmp_path, monkeypatch)
    fixtures = importlib.import_module("geometry.conftest")
    backend = _scaled_mesh_backend(fixtures)
    _isolate(monkeypatch, backend)
    payload = _mesh_study_payload(service, request)
    prepared = service.prepare_planar(created.case_id, payload, expected_generation=0)
    storage = service._storage(created.case_id)
    registrations = []
    revision = service.get_revision(created.case_id, str(prepared["revision_id"]))
    final_run_id = ""
    for index, (factor, include_log) in enumerate(zip(factors, logs, strict=True)):
        registrations.append(storage.resolve_revision_mesh_quality(revision))
        final_run_id = _registered_mesh_result(
            service,
            created,
            revision,
            label=f"stage-{index}",
            factor=factor,
            include_log=include_log,
        )
        if index < 2:
            child_payload = copy.deepcopy(payload)
            child_payload["values"]["mesh_policy"]["global_size"] = {
                "value": 1.0 if index == 0 else 0.5,
                "unit": "mm",
            }
            prepared = service.prepare_planar(
                created.case_id,
                child_payload,
                expected_generation=int(prepared["generation"]),
                parent_revision_id=revision.revision_id,
            )
            revision = service.get_revision(created.case_id, str(prepared["revision_id"]))
    assert len({item.preparation_id for item in registrations}) == 3
    from febio_cae.application import _mesh_refinement, _required_quality

    monkeypatch.setattr(_required_quality, "has_qualified_runtime", lambda bundle: True)
    monkeypatch.setattr(_mesh_refinement, "has_qualified_runtime", lambda bundle: True)
    assert final_run_id
    return service.run_status(created.case_id, final_run_id)


def _far_field_maximum(mesh: Any, body_id: str) -> float:
    import math

    coordinates = {node.node_id: node.coordinates_si for node in mesh.nodes}
    lengths = []
    for element in mesh.elements:
        if element.body_id != body_id:
            continue
        corners = tuple(coordinates[node_id] for node_id in element.node_ids[:4])
        if min(point[0] for point in corners) < 0.019:
            continue
        lengths.extend(
            math.dist(corners[left], corners[right])
            for left in range(4)
            for right in range(left + 1, 4)
        )
    if not lengths:
        raise AssertionError("source-local fixture has no far-field control element")
    return max(lengths)


def _registered_source_local_study_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    factors: tuple[float, float, float],
    logs: tuple[bool, bool, bool],
    delete_current_receipt: bool = False,
) -> dict[str, object]:
    import copy
    import importlib

    from febio_cae.application import _mesh_refinement, _required_quality
    from febio_cae.application._mesh_refinement import _local_mesh_measurements
    from febio_cae.storage.preparation import PreparationStore

    service, created, request, _, _ = prepared_input.__wrapped__(tmp_path, monkeypatch)
    fixtures = importlib.import_module("geometry.conftest")
    backend = _source_local_mesh_backend(fixtures)
    _isolate(monkeypatch, backend)
    payload = _source_local_study_payload(service, request)
    prepared = service.prepare_planar(created.case_id, payload, expected_generation=0)
    storage = service._storage(created.case_id)
    revision = service.get_revision(created.case_id, str(prepared["revision_id"]))
    registrations = []
    meshes = []
    stage_revisions = [revision]
    final_run_id = ""
    for index, (factor, include_log) in enumerate(zip(factors, logs, strict=True)):
        registration = storage.resolve_revision_mesh_quality(revision)
        registrations.append(registration)
        mesh = service._planar_execution_mesh(storage, registration, revision)
        meshes.append(mesh)
        final_run_id = _registered_mesh_result(
            service,
            created,
            revision,
            label=f"source-local-stage-{index}",
            factor=factor,
            include_log=include_log,
        )
        if index < 2:
            child_payload = copy.deepcopy(payload)
            child_payload["values"]["mesh_policy"]["local_refinements"][0]["size"] = {
                "value": 1.0 if index == 0 else 0.5,
                "unit": "mm",
            }
            prepared = service.prepare_planar(
                created.case_id,
                child_payload,
                expected_generation=int(prepared["generation"]),
                parent_revision_id=revision.revision_id,
            )
            revision = service.get_revision(created.case_id, str(prepared["revision_id"]))
            stage_revisions.append(revision)
    assert len({item.preparation_id for item in registrations}) == 3
    assert len({item.generation_profile for item in registrations}) == 1
    far_maxima = [
        _far_field_maximum(mesh, stage_revision.spec.geometry.body_id.value)
        for mesh, stage_revision in zip(meshes, stage_revisions, strict=True)
    ]
    assert far_maxima == pytest.approx([far_maxima[0]] * 3)
    observed = [
        _local_mesh_measurements(stage_revision, mesh)
        for stage_revision, mesh in zip(stage_revisions, meshes, strict=True)
    ]
    values = [item.balls["part-ball"].maximum_edge_m for item in observed]
    counts = [item.balls["part-ball"].corner_edge_count for item in observed]
    assert values[0] > values[1] > values[2]
    assert counts[0] < counts[1] < counts[2]
    monkeypatch.setattr(_required_quality, "has_qualified_runtime", lambda bundle: True)
    monkeypatch.setattr(_mesh_refinement, "has_qualified_runtime", lambda bundle: True)
    assert final_run_id
    if delete_current_receipt:
        current_registration = storage.resolve_revision_mesh_quality(revision)
        path = storage.root / "preparation" / current_registration.preparation_id / "prepared.json"
        path.unlink()
        assert not path.exists()
    return service.run_status(created.case_id, final_run_id)


@pytest.mark.parametrize(
    ("factors", "logs", "expected"),
    [
        ((1.0, 1.0, 1.0), (True, True, True), "PASS"),
        ((1.0, 1.1, 1.2), (True, True, True), "FAIL"),
        ((1.0, 1.0, 1.0), (True, True, False), "UNVERIFIED"),
    ],
)
def test_registered_mesh_refinement_consumer_routes_observed_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    factors: tuple[float, float, float],
    logs: tuple[bool, bool, bool],
    expected: str,
) -> None:
    result = _registered_mesh_study_summary(
        tmp_path,
        monkeypatch,
        factors=factors,
        logs=logs,
    )
    rows, coverage = _numerical_rows(result)
    assert rows["mesh_dependence"]["status"] == expected
    assert result["quality_status"] == expected
    assert coverage["mesh_refinement"].get("status", expected) == expected


@pytest.mark.parametrize(
    ("factors", "logs", "expected"),
    [
        ((1.0, 1.0, 1.0), (True, True, True), "PASS"),
        ((1.0, 1.1, 1.2), (True, True, True), "FAIL"),
        ((1.0, 1.0, 1.0), (True, True, False), "UNVERIFIED"),
    ],
)
def test_registered_source_local_refinement_consumer_routes_observed_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    factors: tuple[float, float, float],
    logs: tuple[bool, bool, bool],
    expected: str,
) -> None:
    result = _registered_source_local_study_summary(
        tmp_path,
        monkeypatch,
        factors=factors,
        logs=logs,
    )
    rows, coverage = _numerical_rows(result)

    assert rows["source_local_mesh_study"]["status"] == expected
    assert result["quality_status"] == expected
    assert coverage["mesh_refinement"]["scope"] == "three_declared_source_local_tet10_sizes"


def test_registered_source_local_missing_receipt_is_unverified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _registered_source_local_study_summary(
        tmp_path,
        monkeypatch,
        factors=(1.0, 1.0, 1.0),
        logs=(True, True, True),
        delete_current_receipt=True,
    )
    rows, _ = _numerical_rows(result)

    assert rows["source_local_mesh_study"]["status"] == "UNVERIFIED"
    assert result["quality_status"] == "UNVERIFIED"
