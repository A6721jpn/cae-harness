"""Synthetic issued-preview persistence; no native observer or Studio invocation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from test_runner_connection import (
    test_registered_runner_connects_issued_state_and_numeric_reader as _make_result,
)

from febio_cae.domain import EvidenceRef, PreviewReceipt, PreviewStatus, ToolIdentity
from febio_cae.domain.ports import PortError
from febio_cae.storage import CaseStorage, StorageConflictError


@pytest.fixture
def tmp_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Keep nested registered fixture paths within Windows path limits."""
    return tmp_path_factory.mktemp("p")


def _store(tmp_path: Path) -> Any:
    from febio_cae.storage.preview import RegisteredPreviewStore

    _make_result(tmp_path, "none")
    return RegisteredPreviewStore(CaseStorage(tmp_path / "case"))


def _issue(
    store: Any, manifest_id: str = "native-shaped-manifest"
) -> tuple[PreviewReceipt, dict[str, object]]:
    target = store.target(manifest_id)
    receipt = PreviewReceipt(
        "preview-one",
        target.manifest.manifest_id,
        target.entry.digest,
        ToolIdentity("Studio", "synthetic", "f" * 64),
        PreviewStatus.REQUESTED,
        (target.final_state_id,),
        ("displacement",),
        (),
        (),
        (),
    )
    binding = {
        "nonce": "fresh-nonce",
        "source_path": str(target.path),
        "manifest_id": target.manifest.manifest_id,
        "xplt_digest": target.entry.digest,
    }
    store.issue(receipt, binding)
    return receipt, binding


def test_preview_target_uses_registered_numeric_axis_and_source(tmp_path: Path) -> None:
    target = _store(tmp_path).target("native-shaped-manifest")
    assert target.final_state_id == 1
    assert target.final_time == 1.0
    assert target.variable == "displacement" and target.component == "z"
    assert target.unit == "mm"  # preserved synthetic fixture mapping, not native demo units
    assert target.read() == b"synthetic only"


def test_issued_preview_survives_reopen_but_wrong_nonce_cannot_confirm(tmp_path: Path) -> None:
    from febio_cae.storage.preview import RegisteredPreviewStore

    store = _store(tmp_path)
    receipt, _ = _issue(store)
    reopened = RegisteredPreviewStore(CaseStorage(tmp_path / "case"))
    assert reopened.get(receipt.receipt_id)["receipt"] == receipt.to_dict()
    with pytest.raises(StorageConflictError):
        reopened.finish(receipt, "wrong-nonce")
    assert reopened.get(receipt.receipt_id)["receipt"]["status"] == "REQUESTED"


def test_confirmed_preview_rechecks_evidence_and_xplt_on_read(tmp_path: Path) -> None:
    store = _store(tmp_path)
    receipt, _ = _issue(store)
    asset = store.storage.ingest_source(
        asset_id="synthetic-fresh-proof",
        source_kind="registered_document",
        media_type="application/json",
        content=b"synthetic observed proof",
    )
    evidence = EvidenceRef(
        "1", "registered_document", asset.asset_id, "preview.confirmation", asset.content_digest
    )
    confirmed = receipt.confirmed(
        evidence=(evidence,), observed_state_ids=(1,), observed_variables=("displacement",)
    )
    store.finish(confirmed, "fresh-nonce")
    assert store.get(receipt.receipt_id)["receipt"]["status"] == "CONFIRMED"
    store.target("native-shaped-manifest").path.write_bytes(b"changed after confirmation")
    with pytest.raises(PortError):
        store.get(receipt.receipt_id)


def _quality_preview(
    tmp_path: Path, *, factor: float = 1, mode: str = "peak"
) -> tuple[Any, str, Any]:
    from dataclasses import replace

    from test_comparison import _configure, _result
    from test_persistence_authority import _evidence
    from test_planar_edit_validation import prepared

    from febio_cae.adapters.febio import QualityAdapter
    from febio_cae.domain import QualityThreshold, Quantity
    from febio_cae.storage.preview import RegisteredPreviewStore

    def configure(service: Any, spec: Any) -> Any:
        spec = _configure(service, spec)
        if mode == "native_time":
            times = (Quantity(0, "s"), Quantity(0.1, "s"))
            spec = replace(
                spec,
                motion=replace(
                    spec.motion,
                    samples=(
                        spec.motion.samples[0],
                        replace(spec.motion.samples[-1], time=times[-1]),
                    ),
                ),
                outputs=replace(
                    spec.outputs,
                    saved_times=times,
                    evaluations=tuple(
                        replace(item, state_times=times) for item in spec.outputs.evaluations
                    ),
                ),
            )
        criterion = spec.quality_policy.criteria[0]
        if mode == "signed":
            criterion = replace(
                criterion,
                metric_id="signed_force_sum",
                thresholds=(QualityThreshold("max_value", Quantity(3, "N")),),
            )
            force = next(r for r in spec.outputs.requests if r.quantity_id == "contact_force")
            spec = replace(
                spec,
                outputs=replace(
                    spec.outputs,
                    evaluations=(
                        replace(
                            spec.outputs.evaluations[0],
                            output_request_id=force.request_id,
                            selection=force.selection,
                            aggregation_id="sum",
                            state_times=spec.outputs.saved_times,
                        ),
                    ),
                ),
            )
        criteria: tuple[Any, ...] = (criterion,)
        if mode == "named_exemption":
            criterion = replace(
                criterion,
                criterion_id="quasistatic_equilibrium",
                evidence=_evidence("quality_policy.criteria.quasistatic_equilibrium"),
            )
            exemption = replace(
                criterion,
                criterion_id="mesh_dependence",
                metric_id="not_applicable",
                applicability_reason="synthetic declared exemption, not qualified coverage",
                evidence=_evidence("quality_policy.criteria.mesh_dependence"),
            )
            criteria = (criterion, exemption)
        return replace(spec, quality_policy=replace(spec.quality_policy, criteria=criteria))

    if mode == "solver_log":
        import hashlib

        from febio_cae.adapters.febio import xplt_reader

        solver = tmp_path / "synthetic-solver"
        solver.write_bytes(b"never executed")
        base_configure = configure

        def configure(service: Any, spec: Any) -> Any:
            spec = base_configure(service, spec)
            profile = service.compatibility.get_profile(spec.solver_policy.profile.profile_id)
            profile = replace(
                profile,
                profile_id="log-demo",
                solver=replace(
                    profile.solver,
                    executable_digest=hashlib.sha256(solver.read_bytes()).hexdigest(),
                ),
                reader=replace(
                    profile.reader,
                    executable_digest=hashlib.sha256(
                        Path(xplt_reader.__file__).read_bytes()
                    ).hexdigest(),
                ),
            )
            service.register_profile(profile)
            digest = hashlib.sha256(profile.to_bytes()).hexdigest()
            return replace(
                spec,
                **{
                    name: replace(
                        getattr(spec, name),
                        profile=replace(
                            getattr(spec, name).profile,
                            profile_id=profile.profile_id,
                            record_digest=digest,
                        ),
                    )
                    for name in ("solver_policy", "outputs", "quality_policy")
                },
            )

    service, _created, storage, revision = prepared(tmp_path, configure)
    original = storage.ingest_source

    def defer_quality(**kwargs: Any) -> Any:
        # Suppress only the fixture's automatic quality publication, not assessment.
        if str(kwargs.get("asset_id", "")).startswith("quality-"):
            return None
        return original(**kwargs)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(storage, "ingest_source", defer_quality)
        if mode == "solver_log":
            manifest = _demo_log_result(service, storage, revision, tmp_path, patch)
        else:
            times = (0.0, 0.10000000149011612) if mode == "native_time" else (0, 0.5, 1)
            manifest = _result(service, storage, revision, "preview", times, factor)
    store = RegisteredPreviewStore(storage)
    target = store.target(manifest.manifest_id)
    quality = QualityAdapter().assess(manifest, revision, target.mesh, target.profile, storage)
    receipt, _ = _issue(store, manifest.manifest_id)
    asset = storage.ingest_source(
        asset_id="preview-proof",
        source_kind="registered_document",
        media_type="application/json",
        content=b"synthetic observation",
    )
    proof = EvidenceRef(
        "1", "registered_document", asset.asset_id, "preview.confirmation", asset.content_digest
    )
    store.finish(
        receipt.confirmed(
            evidence=(proof,),
            observed_state_ids=(target.final_state_id,),
            observed_variables=("displacement",),
        ),
        "fresh-nonce",
    )
    return store, receipt.receipt_id, quality


def test_preview_uses_native_saved_endpoint_for_displacement_and_force(tmp_path: Path) -> None:
    from febio_cae.application._preview import preview_summary

    store, preview_id, _ = _quality_preview(tmp_path, mode="native_time")
    summary = preview_summary(store, preview_id)
    assert summary["run_status"] == "SUCCEEDED"
    assert summary["preview_status"] == "CONFIRMED"
    assert summary["finite_nonzero_tool_force"] is True
    assert summary["task_status"] == "NEEDS_QUALITY"


@pytest.mark.parametrize("mode", ["peak", "signed", "named_exemption"])
def test_mandatory_quality_coverage_registered_arithmetic(tmp_path: Path, mode: str) -> None:
    from febio_cae.application._preview import preview_summary
    from febio_cae.domain.codec import encode_record

    store, preview_id, quality = _quality_preview(tmp_path, mode=mode)
    asset_id = "quality-" + quality.assessment_id[:24]
    missing = preview_summary(store, preview_id)
    with pytest.raises(StorageConflictError):
        store.storage.source_asset(asset_id)  # Reading must not publish quality.
    assert missing["quality_status"] == "UNVERIFIED"
    assert missing["task_status"] == "NEEDS_QUALITY"
    assert missing["preview_status"] == "CONFIRMED" and missing["run_status"] == "SUCCEEDED"
    assert quality.overall_status.value == "PASS"
    assert missing["finite_nonzero_tool_force"] is True
    store.storage.ingest_source(
        asset_id=asset_id,
        source_kind="registered_document",
        media_type="application/json",
        content=encode_record(quality),
    )
    exact = preview_summary(store, preview_id)
    assert exact["quality_status"] == "UNVERIFIED" and exact["task_status"] == "NEEDS_QUALITY"
    assert exact["quality_registration_status"] == "PASS"
    assert exact["preview_status"] == "CONFIRMED" and exact["run_status"] == "SUCCEEDED"
    coverage = cast(dict[str, Any], exact["required_quality"])
    expected = {
        "execution_result_completeness",
        "contact_quality",
        "motion_support_contact_fidelity",
        "quasistatic_equilibrium",
        "solver_residual",
        "mesh_dependence",
    }
    assert {row["criterion_id"] for row in coverage["numerical"]} == expected
    assert coverage["physical_applicability"]["dimension"] == "applicability"
    assert coverage["physical_applicability"]["criterion_id"] not in expected
    assert store.storage.resolve_source(
        store.storage.source_asset(asset_id)
    ).content == encode_record(quality)


@pytest.mark.parametrize("corrupt", [False, True])
def test_registered_quality_disagreement_is_integrity(tmp_path: Path, corrupt: bool) -> None:
    import sqlite3

    from febio_cae.application._preview import preview_summary
    from febio_cae.domain.codec import encode_record
    from febio_cae.domain.ports import PortErrorCategory

    store, preview_id, quality = _quality_preview(tmp_path)
    asset = store.storage.ingest_source(
        asset_id="quality-" + quality.assessment_id[:24],
        source_kind="registered_document",
        media_type="application/json",
        content=encode_record(quality) if corrupt else b"{}",
    )
    if corrupt:
        with sqlite3.connect(store.storage.registry_path) as connection:
            relative = connection.execute(
                "SELECT relative_path FROM sources WHERE asset_id=?", (asset.asset_id,)
            ).fetchone()[0]
        (store.storage.root / relative).write_bytes(b"corrupt")
    with pytest.raises(PortError) as error:
        preview_summary(store, preview_id)
    assert error.value.category is PortErrorCategory.INTEGRITY


@pytest.mark.parametrize("registered", [False, True])
def test_mandatory_quality_coverage_preserves_known_fail(tmp_path: Path, registered: bool) -> None:
    from febio_cae.application._preview import preview_summary
    from febio_cae.domain.codec import encode_record

    store, preview_id, quality = _quality_preview(tmp_path, factor=100)
    if registered:
        store.storage.ingest_source(
            asset_id="quality-" + quality.assessment_id[:24],
            source_kind="registered_document",
            media_type="application/json",
            content=encode_record(quality),
        )
    result = preview_summary(store, preview_id)
    assert result["quality_status"] == "FAIL" and result["task_status"] == "FAILED"
    assert result["preview_status"] == "CONFIRMED" and result["run_status"] == "SUCCEEDED"
    assert "required_quality" in result
    assert result["quality_registration_status"] == ("FAIL" if registered else "UNVERIFIED")


def _demo_log_result(service: Any, storage: Any, revision: Any, tmp_path: Path, patch: Any) -> Any:
    """Real demo read closure over the existing synthetic issued storage fixture."""
    from types import SimpleNamespace

    from test_comparison import _result

    from febio_cae.application import _demo

    storage.ingest_source(
        asset_id="registered-reader-source",
        source_kind="registered_document",
        media_type="text/plain",
        content=Path(_demo.xplt_reader.__file__).read_bytes(),
    )
    original_seal = storage._seal_native_output
    original_read = storage._read_candidate
    context: dict[str, Any] = {}
    numeric: dict[str, Any] = {}
    original_register = storage.register_numeric_data

    def register(data: Any) -> Any:
        numeric[data.reference.data_id] = data
        return original_register(data)

    def resolve(reference: Any) -> Any:
        data = numeric[reference.data_id]
        assert data.reference == reference
        return data

    def seal(owner: Any) -> Any:
        attempt = storage._attempt(owner)
        root = Path(storage._native_context(attempt)["process_root"])
        (root / "output/solver.log").write_bytes(b"synthetic opaque log")
        entries = original_seal(owner)
        assert len(entries) == 2
        return entries

    class Reader:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def read(self, attempt: Any, bundle: Any) -> Any:
            _, lineage = storage._lineage(attempt)
            xplt = tuple(e for e in storage._sealed_entries(lineage) if e.role == "result")
            # Existing fixture supplies real registered numeric data, no native parser claim.
            return context["synthetic_read"](attempt, bundle, xplt, storage)

    def execute(case_id: str, revision_id: str, **kwargs: Any) -> Any:
        def read(owner: Any, synthetic_read: Any) -> Any:
            context["synthetic_read"] = synthetic_read
            return original_read(owner, kwargs["read"])

        patch.setattr(storage, "_read_candidate", read)
        return _result(service, storage, revision, "preview", (0, 0.5, 1), 1)

    patch.setattr(storage, "register_numeric_data", register)
    patch.setattr(storage, "_seal_native_output", seal)
    patch.setattr(service, "_execute_ports", execute)
    patch.setattr(_demo, "XpltReaderAdapter", Reader)
    patch.setattr(
        _demo,
        "LocalResultDataStore",
        lambda: SimpleNamespace(
            register_source=lambda *args, **kwargs: None,
            resolve=resolve,
        ),
    )
    response = _demo.run_demo(
        service,
        revision.case_id,
        revision.revision_id,
        executable=str(tmp_path / "synthetic-solver"),
        preflight=False,
    )
    assert response["quality_status"] == "UNVERIFIED"
    assert response["task_status"] == "NEEDS_QUALITY"
    return storage.get_manifest(cast(dict[str, Any], response["manifest"])["manifest_id"])


def test_solver_log_binding_demo_and_confirmed_preview(tmp_path: Path) -> None:
    from febio_cae.domain.ports import PortErrorCategory

    store, preview_id, _ = _quality_preview(tmp_path, mode="solver_log")
    target = store.target(store.get(preview_id)["receipt"]["manifest_id"])
    log = next(e for e in target.manifest.files if e.role == "solver_log")
    assert log.logical_path == "output/solver.log"
    assert store.get(preview_id)["receipt"]["status"] == "CONFIRMED"
    destination = (
        store.storage.root
        / f"cases/{target.attempt.case_id}/runs/{target.attempt.run_id}/attempts/{target.attempt.attempt_id}/{log.logical_path}"
    )
    destination.write_bytes(b"changed log")
    with pytest.raises(PortError) as failure:
        store.get(preview_id)
    assert failure.value.category is PortErrorCategory.INTEGRITY
