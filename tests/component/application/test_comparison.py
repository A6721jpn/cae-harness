"""Registered synthetic curve/ROI comparison; never native accuracy evidence."""

from __future__ import annotations

import hashlib
import importlib
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from test_persistence_authority import _evidence, _profile
from test_planar_edit_validation import prepared

from febio_cae.adapters.febio import QualityAdapter
from febio_cae.domain import (
    AttemptRecord,
    ComparisonAxis,
    ComparisonInterval,
    ComparisonSpec,
    ExecutionBundle,
    ExecutionSetting,
    FileEntry,
    FrameId,
    MotionSample,
    NumericalProfileRef,
    NumericResultData,
    OutputMapping,
    OutputObservation,
    ProcessIdentity,
    QualityThreshold,
    Quantity,
    ReadResult,
    ReadStatus,
    ResultDataRef,
    ResultManifest,
    RunState,
    UnitDirection,
)
from febio_cae.domain.case_patch import CasePatch, CasePatchEdit
from febio_cae.domain.codec import encode_record
from febio_cae.domain.ports import PortError, TrustedOwnerContext

FIXED = (
    "geometry",
    "support",
    "rigid_tool",
    "motion",
    "contact",
    "mesh_policy",
    "solver_policy",
    "outputs",
    "quality_policy",
    "budget",
    "material.kind",
    "material.poisson_ratio",
    "material.applicability",
    "material.model_evidence",
    "material.poisson_ratio_evidence",
)


def _configure(service: Any, spec: Any) -> Any:
    profile = replace(
        _profile("comparison-synthetic"),
        output_mappings=(
            OutputMapping(
                "displacement",
                "displacement",
                "node",
                "VEC3F",
                "m",
                FrameId("World"),
                1,
                1,
                "value",
            ),
            OutputMapping(
                "contact_force",
                "rigid force",
                "rigid_body",
                "VEC3F",
                "N",
                FrameId("World"),
                -1,
                1,
                "value",
            ),
        ),
    )
    service.register_profile(profile)
    digest = hashlib.sha256(profile.to_bytes()).hexdigest()

    def ref(purpose: str) -> NumericalProfileRef:
        return NumericalProfileRef(profile.profile_id, purpose, digest)

    displacement = replace(
        spec.outputs.requests[0],
        request_id="displacement",
        measure_id="value",
        display_unit="m",
        evidence=_evidence("outputs.requests.displacement"),
    )
    force = replace(
        displacement,
        request_id="contact_force",
        quantity_id="contact_force",
        location="rigid_body",
        selection=spec.rigid_tool.contact_surface,
        display_unit="N",
        evidence=_evidence("outputs.requests.contact_force"),
    )
    evaluation = replace(spec.outputs.evaluations[0], output_request_id="displacement")
    criterion = replace(
        spec.quality_policy.criteria[0],
        metric_id="peak_abs_value",
        thresholds=(QualityThreshold("max_value", Quantity(1e-4, "m")),),
    )
    return replace(
        spec,
        outputs=replace(
            spec.outputs,
            profile=ref("outputs"),
            requests=(displacement, force),
            evaluations=(evaluation,),
        ),
        quality_policy=replace(spec.quality_policy, profile=ref("quality"), criteria=(criterion,)),
        solver_policy=replace(spec.solver_policy, profile=ref("solver")),
        motion=replace(
            spec.motion,
            direction=UnitDirection(FrameId("World"), 0, 0, -1),
            samples=(
                MotionSample(Quantity(0, "s"), Quantity(0, "m")),
                MotionSample(Quantity(1, "s"), Quantity(1e-5, "m")),
            ),
        ),
    )


def _result(
    service: Any, storage: Any, revision: Any, label: str, times: tuple[float, ...], factor: float
) -> Any:
    """Synthetic issued-runner snapshots and reader; no native process is started."""
    record = storage.resolve_revision_mesh_quality(revision)
    mesh = service._planar_execution_mesh(storage, record, revision)
    profile = service.compatibility.get_profile(revision.spec.solver_policy.profile.profile_id)
    owner = TrustedOwnerContext(
        revision.case_id,
        "run-" + label,
        "attempt-" + label,
        service.current_draft(revision.case_id).generation,
    )
    destination = (
        storage.root / f"cases/{revision.case_id}/runs/{owner.run_id}/attempts/{owner.attempt_id}"
    )
    payload = b"synthetic compiled input " + label.encode()
    entry = FileEntry("input.feb", hashlib.sha256(payload).hexdigest(), len(payload), "input")
    bundle = ExecutionBundle(
        "bundle-" + label,
        revision.case_id,
        revision.revision_id,
        revision.spec_digest,
        mesh.artifact_digest,
        profile.profile_id,
        profile.solver,
        (entry,),
        ("synthetic-no-process",),
        str(destination),
        1,
        (),
    )
    storage.claim(owner)
    storage._register_execution(owner, bundle, mesh, profile, {entry.logical_path: payload})
    process_root = (
        storage.root
        / "native"
        / owner.case_id
        / owner.run_id
        / owner.attempt_id
        / str(owner.owner_generation)
    )
    storage._prepare_runner(owner, process_root)
    (process_root / "output").mkdir(parents=True)
    (process_root / "output/results.xplt").write_bytes(
        b"synthetic result bytes; no native execution"
    )
    issued = AttemptRecord(
        owner.attempt_id,
        owner.run_id,
        owner.case_id,
        revision.revision_id,
        owner.owner_generation,
        bundle.bundle_digest,
        RunState.RUNNING,
        ProcessIdentity(
            bundle.argv[0],
            profile.solver.executable_digest,
            bundle.argv,
            str(process_root),
            1,
            "synthetic-only",
        ),
        (
            ExecutionSetting("attempt_root", str(process_root)),
            ExecutionSetting("max_elapsed_seconds", revision.spec.budget.max_elapsed.to_si().value),
        ),
    )
    storage._accept_runner_start(owner, issued)
    storage._accept_runner_poll(
        owner, issued, issued.transition_to(RunState.DRAINING).transition_to(RunState.VALIDATING)
    )
    storage._seal_native_output(owner)

    def read(attempt: Any, bundle: Any, files: Any, registered: Any) -> Any:
        observations = []
        for mapping in profile.output_mappings:
            is_force = mapping.canonical_id == "contact_force"
            entities = (
                (revision.spec.rigid_tool.primitive.body_id.value,)
                if is_force
                else tuple(str(n.node_id) for n in mesh.nodes)
            )
            rows: list[tuple[float, ...]] = []
            for t in times:
                if is_force:
                    rows.append((0.0, 0.0, -2.0 * t * factor))
                else:
                    # Part nodes1..10 peak at node10; tool nodes have a deliberately
                    # larger value, proving the explicit part-only ROI is honored.
                    rows.append(
                        tuple(
                            v
                            for node in mesh.nodes
                            for v in (
                                0.0,
                                0.0,
                                -t
                                * 1e-5
                                * factor
                                * (node.node_id / 10 if node.node_id <= 10 else 100),
                            )
                        )
                    )
            data = NumericResultData(
                ResultDataRef(
                    label + "-" + mapping.canonical_id,
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
                entities,
                ("x", "y", "z"),
                rows,
            )
            data = replace(
                data, reference=replace(data.reference, content_digest=data.expected_content_digest)
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
        decoded = {item.output_id: item for item in observations}
        observations = [
            replace(decoded[request.quantity_id], output_id=request.request_id)
            for request in revision.spec.outputs.requests
        ]
        return ResultManifest(
            "manifest-" + label,
            attempt.attempt_id,
            bundle.bundle_digest,
            files,
            ReadResult(ReadStatus.VALIDATED, profile.reader, observations, ()),
        )

    manifest = storage.publish_manifest(owner, storage._read_candidate(owner, read))
    storage._transition_attempt(owner, RunState.SUCCEEDED)
    quality = QualityAdapter().assess(manifest, revision, mesh, profile, storage)
    assert quality.overall_status.value == ("FAIL" if factor == 100 else "PASS"), quality.to_dict()
    storage.ingest_source(
        asset_id="quality-" + quality.assessment_id[:24],
        source_kind="registered_document",
        media_type="application/json",
        content=encode_record(quality),
    )
    return manifest


def comparison_fixture(
    tmp_path: Path,
    *,
    changed_nu: bool = False,
    quality_fail: bool = False,
    tool_displacement: bool = False,
) -> tuple[Any, ...]:
    def configure(service: Any, spec: Any) -> Any:
        spec = _configure(service, spec)
        if tool_displacement:
            part = next(r for r in spec.outputs.requests if r.quantity_id == "displacement")
            tool = replace(
                part,
                request_id="tool_displacement",
                selection=spec.rigid_tool.contact_surface,
                evidence=_evidence("outputs.requests.tool_displacement"),
            )
            spec = replace(
                spec, outputs=replace(spec.outputs, requests=(*spec.outputs.requests, tool))
            )
        return spec

    service, created, storage, parent = prepared(tmp_path, configure)
    baseline = _result(service, storage, parent, "baseline", (0, 0.5, 1), 1)
    material = replace(
        parent.spec.material,
        youngs_modulus=Quantity(2e6, "Pa"),
        poisson_ratio=Quantity(0.31 if changed_nu else 0.3, "1"),
    )
    service.apply_patch(
        created.case_id,
        CasePatch(
            parent.revision_id,
            parent.spec_digest,
            (CasePatchEdit("material", material, True),),
            (_evidence("material.youngs_modulus"),),
        ),
    )
    child = service.freeze_case(created.case_id).revision
    assert child is not None
    candidate = _result(
        service, storage, child, "candidate", (0, 0.25, 0.75, 1), 100 if quality_fail else 1.5
    )
    interval = ComparisonInterval("m", 0, 1e-5)
    spec = ComparisonSpec(
        "comparison-one",
        baseline.manifest_id,
        candidate.manifest_id,
        ("material.youngs_modulus",),
        FIXED,
        (
            ComparisonAxis(
                "tool_compression.force_z",
                "m",
                child.spec.rigid_tool.primitive.body_id.value,
                "contact_force.world_z",
                "identity",
                interval,
                "linear",
            ),
            ComparisonAxis(
                "tool_compression.part_peak_abs_displacement_z",
                "m",
                child.spec.geometry.body_id.value,
                "displacement.world_z",
                "peak_abs",
                interval,
                "linear",
            ),
        ),
    )
    return service, created, storage, spec


def _compare(service: Any, created: Any, spec: Any) -> Any:
    operation = getattr(service, "compare_case", None)
    assert callable(operation), "normal registered comparison consumer is missing"
    return operation(
        created.case_id, spec, baseline_run_id="run-baseline", candidate_run_id="run-candidate"
    )


@pytest.mark.parametrize("tool_displacement", [False, True])
def test_registered_comparison_interpolates_signed_curve_and_part_roi(
    tmp_path: Path, tool_displacement: bool
) -> None:
    service, created, storage, spec = comparison_fixture(
        tmp_path, tool_displacement=tool_displacement
    )
    result = _compare(service, created, spec)
    assert result["status"] == "COMPARED"
    comparison = result["comparison"]
    assert comparison["common_axis"]["values"] == pytest.approx([0, 2.5e-6, 5e-6, 7.5e-6, 1e-5])
    series = {row["measure_id"]: row for row in comparison["series"]}
    force = series["contact_force.world_z"]
    assert force["baseline"] == pytest.approx([0, -0.5, -1, -1.5, -2])
    assert force["candidate"] == pytest.approx([0, -0.75, -1.5, -2.25, -3])
    assert force["difference"] == pytest.approx([0, -0.25, -0.5, -0.75, -1])
    assert force["relative_difference"][0] is None
    assert force["relative_difference"][1:] == pytest.approx([0.5, 0.5, 0.5, 0.5])
    assert force["relative_status"][0] == "undefined_zero_baseline"
    assert series["displacement.world_z"]["candidate"][-1] == pytest.approx(1.5e-5)
    assert comparison["surface_approximation"] == "UNVERIFIED"
    record = Path(result["record_path"])
    before = record.read_bytes()
    assert _compare(service, created, spec) == result
    assert record.read_bytes() == before
    assert storage.get_manifest(spec.baseline_manifest_id).manifest_id == spec.baseline_manifest_id


@pytest.mark.parametrize(
    "defect",
    ["fixed-declaration", "nonmaterial-change", "extrapolation", "roi", "quality", "numeric"],
)
def test_comparison_refuses_incompatible_or_ineligible_results(tmp_path: Path, defect: str) -> None:
    service, created, storage, spec = comparison_fixture(
        tmp_path, changed_nu=defect == "nonmaterial-change", quality_fail=defect == "quality"
    )
    if defect == "fixed-declaration":
        spec = replace(spec, fixed_conditions=FIXED[:-1])
    elif defect == "extrapolation":
        spec = replace(
            spec,
            axes=tuple(replace(a, interval=ComparisonInterval("m", 0, 2e-5)) for a in spec.axes),
        )
    elif defect == "roi":
        spec = replace(spec, axes=(replace(spec.axes[0], roi_id="foreign-body"), spec.axes[1]))
    elif defect == "numeric":
        with sqlite3.connect(storage.registry_path) as connection:
            connection.execute(
                "DELETE FROM numeric_data WHERE data_id=?", ("candidate-contact_force",)
            )
    operation = getattr(service, "compare_case", None)
    assert callable(operation), "normal registered comparison consumer is missing"
    reason = {
        "fixed-declaration": "fixed condition",
        "nonmaterial-change": "fixed-condition",
        "extrapolation": "extrapolation",
        "roi": "ROI",
        "quality": "quality",
        "numeric": "numeric",
    }[defect]
    with pytest.raises((ValueError, PortError), match=reason):
        operation(
            created.case_id, spec, baseline_run_id="run-baseline", candidate_run_id="run-candidate"
        )
    assert not (
        created.case_root / f"cases/{created.case_id}/comparisons/comparison-one/comparison.json"
    ).exists()


def test_current_prepared_box_is_an_explicit_comparison_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from febio_cae.application import _comparison
    from febio_cae.domain import AsPlaced, PortError
    from febio_cae.storage.mesh_quality import CurrentPreparationRegistration

    source_digest = "a" * 64
    geometry_digest = "b" * 64
    spec = SimpleNamespace(
        geometry=SimpleNamespace(
            source_step_digest=source_digest,
            geometry_digest=geometry_digest,
        ),
        rigid_tool=SimpleNamespace(primitive=SimpleNamespace(kind="box")),
        contact=SimpleNamespace(arrangement=AsPlaced(_evidence("contact.arrangement"))),
    )
    current = CurrentPreparationRegistration(
        "current-prepared",
        source_digest,
        geometry_digest,
        "c" * 64,
        "d" * 64,
        NumericalProfileRef("mesh", "mesh_quality", "e" * 64),
        (_evidence("mesh.admission"),),
        "0" * 32,
    )

    class _Quality:
        assessment_id = "quality-current"
        overall_status = SimpleNamespace(value="PASS")

        @staticmethod
        def to_dict() -> dict[str, str]:
            return {"status": "PASS"}

    quality = _Quality()
    revision = SimpleNamespace(
        case_id="case-current",
        revision_id="revision-current",
        spec_digest="f" * 64,
        spec=spec,
        to_bytes=lambda: b"revision-current",
    )
    item = SimpleNamespace(
        profile=SimpleNamespace(
            profile_id="comparison-profile",
            evidence=(),
            capabilities=(),
            to_bytes=lambda: b"comparison-profile",
        ),
        revision=revision,
        mesh=SimpleNamespace(artifact_digest="1" * 64),
        manifest=SimpleNamespace(
            manifest_id="manifest-current", to_bytes=lambda: b"manifest-current"
        ),
        attempt=SimpleNamespace(
            attempt_id="attempt-current",
            run_id="run-current",
            state=SimpleNamespace(value="SUCCEEDED"),
        ),
        bundle=SimpleNamespace(bundle_digest="2" * 64),
    )
    storage = SimpleNamespace(
        resolve_revision_mesh_quality=lambda _revision: current,
        source_asset=lambda _asset_id: SimpleNamespace(asset_id="quality-current"),
        resolve_source=lambda _asset: SimpleNamespace(content=b"quality-record"),
    )
    service = SimpleNamespace(
        compatibility=SimpleNamespace(get_profile=lambda _profile_id: item.profile),
        _resolve_declarations=lambda *_args: None,
    )
    verified: list[str] = []

    def verify(_storage: Any, registration: Any, revision: Any, mesh: Any) -> None:
        assert registration is current
        assert mesh is not None
        verified.append(revision.revision_id)

    service._verify_execution_mesh = verify
    monkeypatch.setattr(
        _comparison,
        "QualityAdapter",
        lambda: SimpleNamespace(assess=lambda *args: quality),
    )
    required = importlib.import_module("febio_cae.application._required_quality")
    monkeypatch.setattr(required, "required_quality_summary", lambda *args: ("PASS", {}))
    monkeypatch.setattr(_comparison, "encode_record", lambda _value: b"quality-record")

    identity = _comparison._eligible(service, storage, item)
    assert identity["root_mesh_digest"] == current.original_mesh_digest
    assert verified == [revision.revision_id]
    curved_spec = SimpleNamespace(
        geometry=spec.geometry,
        rigid_tool=SimpleNamespace(primitive=SimpleNamespace(kind="sphere")),
        contact=spec.contact,
    )
    curved_revision = SimpleNamespace(**{**vars(revision), "spec": curved_spec})
    curved_item = SimpleNamespace(**{**vars(item), "revision": curved_revision})
    with pytest.raises(PortError, match="explicit planar box"):
        _comparison._eligible(service, storage, curved_item)
    assert verified == [revision.revision_id]
