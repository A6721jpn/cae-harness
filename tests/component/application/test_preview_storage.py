"""Synthetic issued-preview persistence; no native observer or Studio invocation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from test_runner_connection import (
    test_registered_runner_connects_issued_state_and_numeric_reader as _make_result,
)

from febio_cae.domain import EvidenceRef, PreviewReceipt, PreviewStatus, ToolIdentity
from febio_cae.domain.ports import PortError
from febio_cae.storage import CaseStorage, StorageConflictError


def _store(tmp_path: Path) -> Any:
    from febio_cae.storage.preview import RegisteredPreviewStore

    _make_result(tmp_path, "none")
    return RegisteredPreviewStore(CaseStorage(tmp_path / "case"))


def _issue(store: Any) -> tuple[PreviewReceipt, dict[str, object]]:
    target = store.target("native-shaped-manifest")
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
