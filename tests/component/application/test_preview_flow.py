"""Synthetic same-instance observer protocol, not native GUI evidence."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_preview_storage import _store

from febio_cae.adapters.preview.studio import ExistingStudioSession as Session
from febio_cae.domain import PreviewReceipt, PreviewStatus, ToolIdentity


@pytest.mark.parametrize("defect", ["none", "real-adapter", "old-capture", "foreign-nonce"])
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

    if defect in {"none", "real-adapter"}:
        result = observe_preview(
            store,
            "native-shaped-manifest",
            session,
            session_probe=lambda expected: replace(expected),
            capture=capture,
            timeout_seconds=30,
            adapter_factory=None if defect == "real-adapter" else Adapter,
            observation_factory=None if defect == "real-adapter" else SimpleNamespace,
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


@pytest.mark.parametrize("defect", ["none", "missing-version", "changed-xplt", "launch-error"])
def test_cli_launch_persists_current_target_and_gates_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    defect: str,
) -> None:
    import hashlib
    import json
    import subprocess

    from test_preview_storage import _quality_preview

    from febio_cae.adapters.febio import QualityAdapter
    from febio_cae.application import _preview, _preview_windows
    from febio_cae.application.service import RegisteredCaseService
    from febio_cae.cli.main import main
    from febio_cae.domain.codec import encode_record
    from febio_cae.storage.preview import RegisteredPreviewStore

    store, existing_preview_id, _ = _quality_preview(tmp_path)
    target = store.target(store.get(existing_preview_id)["receipt"]["manifest_id"])
    studio = tmp_path / "synthetic Studio.exe"
    studio.write_bytes(b"synthetic executable identity; Popen is mocked")
    calls: list[Any] = []

    def launch(argv: Any, **kwargs: Any) -> Any:
        calls.append((argv, kwargs))
        assert argv == [str(studio.resolve()), str(target.path)]
        assert kwargs["shell"] is False
        assert not Path(kwargs["cwd"]).is_relative_to(store.storage.root)
        if defect == "launch-error":
            raise OSError("synthetic launch failure")
        return SimpleNamespace(pid=4321)

    monkeypatch.setattr(subprocess, "Popen", launch)
    monkeypatch.setattr(
        _preview_windows,
        "_file_version",
        lambda path: None if defect == "missing-version" else "synthetic-1",
    )
    monkeypatch.setattr(RegisteredCaseService, "_storage", lambda self, case_id: store.storage)
    quality = QualityAdapter().assess(
        target.manifest, target.revision, target.mesh, target.profile, store.storage
    )
    store.storage.ingest_source(
        asset_id="quality-" + quality.assessment_id[:24],
        source_kind="registered_document",
        media_type="application/json",
        content=encode_record(quality),
    )
    monkeypatch.setattr(_preview, "required_quality_summary", lambda *args: ("PASS", {}))
    if defect == "changed-xplt":
        target.path.write_bytes(b"changed before launch")
    code = main(
        [
            "case",
            "--state-dir",
            str(tmp_path / "state"),
            "preview",
            target.attempt.case_id,
            "--manifest-id",
            target.manifest.manifest_id,
            "--studio",
            str(studio),
            "--json",
        ]
    )
    result = json.loads(capsys.readouterr().out)
    if defect in {"changed-xplt", "launch-error"}:
        assert code != 0
        assert len(calls) == (0 if defect == "changed-xplt" else 1)
        assert result.get("preview_status") != "LAUNCHED"
        return
    assert code == 0
    assert result["preview_status"] == "LAUNCHED"
    assert result["task_status"] == "COMPLETE" and result["run_status"] == "SUCCEEDED"
    record = RegisteredPreviewStore(store.storage).get(result["preview_id"])
    assert (
        record["receipt"]["studio"]["executable_digest"]
        == hashlib.sha256(studio.read_bytes()).hexdigest()
    )
    assert record["receipt"]["studio"]["version"] == (
        "UNVERIFIED" if defect == "missing-version" else "synthetic-1"
    )
    if defect == "missing-version":
        assert record["binding"]["version_reason"] == "native file metadata has no version"
    assert record["binding"]["process_id"] == 4321
    assert record["binding"]["launched_ns"] > 0
    assert record["binding"]["studio_path"] == str(studio.resolve())
    assert not record["receipt"]["confirmation_evidence"]
    for quality_status, task_status in [("UNVERIFIED", "NEEDS_QUALITY"), ("FAIL", "FAILED")]:
        monkeypatch.setattr(
            _preview, "required_quality_summary", lambda *args, status=quality_status: (status, {})
        )
        assert _preview.preview_summary(store, result["preview_id"])["task_status"] == task_status
