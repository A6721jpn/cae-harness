"""Synthetic same-instance observer protocol, not native GUI evidence."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_preview_storage import _store

from febio_cae.domain import PreviewReceipt, PreviewStatus, ToolIdentity


@dataclass(frozen=True)
class Session:
    studio: ToolIdentity
    process_id: int
    process_start_marker: str
    window_id: int


@pytest.mark.parametrize("defect", ["none", "old-capture", "foreign-nonce"])
def test_preview_issues_before_capture_and_rejects_unbound_evidence(
    tmp_path: Path, defect: str
) -> None:
    from febio_cae.application._preview import observe_preview

    store = _store(tmp_path)
    capture_path = tmp_path / "new-capture.png"
    if defect == "old-capture":
        capture_path.write_bytes(b"\x89PNG\r\n\x1a\nsynthetic only")
    session = Session(ToolIdentity("Studio", "synthetic", "f" * 64), 123, "fresh-process", 321)
    observed_ids: list[str] = []

    class Adapter:
        def __init__(self, **kwargs: Any) -> None:
            self.observer = kwargs["observer"]

        def request_observation(self, manifest: Any, request: Any, **kwargs: Any) -> Any:
            target = store.target(manifest.manifest_id)
            self.binding = SimpleNamespace(
                launch_id="new-local-nonce",
                receipt_id=request.preview_id,
                manifest_id=manifest.manifest_id,
                path=target.path,
                studio=session.studio,
                xplt_digest=target.entry.digest,
                requested_state_ids=tuple(request.state_ids),
                requested_variables=tuple(request.variables),
                existing_session=session,
            )
            return PreviewReceipt(
                request.preview_id,
                request.manifest_id,
                target.entry.digest,
                session.studio,
                PreviewStatus.REQUESTED,
                request.state_ids,
                request.variables,
                (),
                (),
                (),
            )

        def observation_binding(self, receipt: Any) -> Any:
            return self.binding

        def confirm(self, receipt: Any, evidence: Any) -> Any:
            observation = self.observer(self.binding)
            assert observation.existing_session == session
            assert observation.binding == self.binding
            assert observation.evidence == evidence
            return receipt.confirmed(
                evidence=evidence,
                observed_state_ids=observation.state_ids,
                observed_variables=observation.variables,
            )

    def capture(issue: dict[str, Any], remaining: float) -> dict[str, Any]:
        assert remaining > 0
        preview_id = issue["receipt"]["receipt_id"]
        assert store.get(preview_id)["receipt"]["status"] == "REQUESTED"
        observed_ids.append(preview_id)
        if defect != "old-capture":
            capture_path.write_bytes(b"\x89PNG\r\n\x1a\nsynthetic only")
        return {
            "preview_id": preview_id,
            "request_nonce": "wrong" if defect == "foreign-nonce" else issue["binding"]["nonce"],
            "manifest_id": issue["binding"]["manifest_id"],
            "loaded_file": issue["binding"]["source_path"],
            "xplt_sha256": issue["binding"]["xplt_digest"],
            "studio": session.studio.to_dict(),
            "session": {
                "process_id": 123,
                "process_start_marker": "fresh-process",
                "window_id": 321,
            },
            "observed_state_id": 1,
            "observed_time_s": 1.0,
            "observed_variable": "displacement",
            "observed_component": "z",
            "observed_frame": "World",
            "observed_unit": "mm",
            "capture_path": str(capture_path),
            "observer": "synthetic trusted operator",
        }

    if defect == "none":
        result = observe_preview(
            store,
            "native-shaped-manifest",
            session,
            session_probe=lambda expected: replace(expected),
            capture=capture,
            timeout_seconds=30,
            adapter_factory=Adapter,
            observation_factory=SimpleNamespace,
        )
        assert result["preview_status"] == "CONFIRMED"
        assert result["task_status"] != "COMPLETE"  # synthetic fixture quality is UNVERIFIED
        assert store.get(observed_ids[0])["receipt"]["status"] == "CONFIRMED"
    else:
        with pytest.raises(ValueError):
            observe_preview(
                store,
                "native-shaped-manifest",
                session,
                session_probe=lambda expected: replace(expected),
                capture=capture,
                timeout_seconds=30,
                adapter_factory=Adapter,
                observation_factory=SimpleNamespace,
            )
        assert store.get(observed_ids[0])["receipt"]["status"] == "FAILED"
