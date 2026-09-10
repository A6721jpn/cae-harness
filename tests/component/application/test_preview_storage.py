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

    service, _created, storage, revision = prepared(tmp_path, configure)
    original = storage.ingest_source

    def defer_quality(**kwargs: Any) -> Any:
        # Suppress only the fixture's automatic quality publication, not assessment.
        if str(kwargs.get("asset_id", "")).startswith("quality-"):
            return None
        return original(**kwargs)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(storage, "ingest_source", defer_quality)
        manifest = _result(service, storage, revision, "preview", (0, 0.5, 1), factor)
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
    assert missing["quality"] == quality.to_dict() and quality.overall_status.value == "PASS"
    assert missing["finite_nonzero_tool_force"] is True
    assert "regist" in str(missing["quality_reason"]).lower()
    store.storage.ingest_source(
        asset_id=asset_id,
        source_kind="registered_document",
        media_type="application/json",
        content=encode_record(quality),
    )
    exact = preview_summary(store, preview_id)
    assert exact["quality_status"] == "UNVERIFIED" and exact["task_status"] == "NEEDS_QUALITY"
    assert exact["quality_registration_status"] == "PASS"
    assert exact["quality"] == quality.to_dict()
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
    assert all(
        row["status"] == "UNVERIFIED" and row["reason"] and not row["measured"]
        for row in coverage["numerical"]
    )
    assert coverage["physical_applicability"]["dimension"] == "applicability"
    assert coverage["physical_applicability"]["criterion_id"] not in expected
    target = store.target(quality.manifest_id)
    import hashlib

    assert coverage["bindings"] == {
        "case_id": target.revision.case_id,
        "revision_id": target.revision.revision_id,
        "spec_digest": target.revision.spec_digest,
        "mesh_digest": target.mesh.artifact_digest,
        "profile_id": target.profile.profile_id,
        "profile_digest": hashlib.sha256(target.profile.to_bytes()).hexdigest(),
        "attempt_id": target.manifest.attempt_id,
        "bundle_digest": target.manifest.bundle_digest,
        "manifest_id": target.manifest.manifest_id,
        "manifest_digest": hashlib.sha256(target.manifest.to_bytes()).hexdigest(),
        "assessment_id": quality.assessment_id,
        "policy_digest": quality.policy_digest,
    }
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
    assert result["quality"] == quality.to_dict()
    assert all(
        row["status"] == "UNVERIFIED"
        for row in cast(dict[str, Any], result["required_quality"])["numerical"]
    )
