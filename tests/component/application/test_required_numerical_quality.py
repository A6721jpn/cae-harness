"""Registered numerical completeness coverage at the public run-status boundary."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
import test_comparison as comparison
from test_comparison import _configure, _result
from test_persistence_authority import _created, _populate_complete, _profile
from test_planar_edit_validation import prepared
from test_registered_execution import _build

from febio_cae.domain import (
    NumericResultData,
    OutputObservation,
    ReadResult,
    ReadStatus,
    ResultDataRef,
    ResultManifest,
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
