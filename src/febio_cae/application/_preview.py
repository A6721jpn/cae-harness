"""One live observation-only adapter; operator evidence is never a success flag."""

from __future__ import annotations

import hashlib
import math
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from febio_cae.adapters.febio import QualityAdapter
from febio_cae.adapters.preview import studio as preview_adapter
from febio_cae.domain import EvidenceRef, PreviewRequest, PreviewStatus, ToolIdentity
from febio_cae.domain.canonical import canonical_bytes
from febio_cae.domain.codec import encode_record
from febio_cae.domain.lifecycle import TaskStatus
from febio_cae.domain.ports import PortError, PortErrorCategory
from febio_cae.domain.results import numeric_state_indices
from febio_cae.storage import StorageConflictError, StorageIntegrityError
from febio_cae.storage._ownership import pinned_read
from febio_cae.storage.preview import RegisteredPreviewStore

from ._required_quality import required_quality_summary


def preview_summary(store: RegisteredPreviewStore, preview_id: str) -> dict[str, object]:
    record = store.get(preview_id)
    receipt = record["receipt"]
    target = store.target(receipt["manifest_id"])
    quality = QualityAdapter().assess(
        target.manifest, target.revision, target.mesh, target.profile, store.storage
    )
    try:
        asset = store.storage.source_asset("quality-" + quality.assessment_id[:24])
    except StorageConflictError:
        quality_registration_status = "UNVERIFIED"
        quality_reason = "quality assessment registration is required even if preview is confirmed"
    else:
        if store.storage.resolve_source(asset).content != encode_record(quality):
            raise PortError(PortErrorCategory.INTEGRITY, "registered preview quality changed")
        quality_registration_status = quality.overall_status.value
        quality_reason = "registered quality assessment matches the recomputed assessment"
    quality_status, coverage = required_quality_summary(
        target.manifest, target.revision, target.mesh, target.profile, quality, store.storage
    )
    quality_reason += "; mandatory numerical coverage is unverified; see required_quality"
    force_request = next(
        (r for r in target.revision.spec.outputs.requests if r.quantity_id == "contact_force"), None
    )
    connected = False
    if force_request is not None:
        force = store.storage.resolve_manifest_output(
            target.manifest.manifest_id, force_request.request_id
        )
        if force.axis_id != "state_time" or force.axis_unit != "s":
            raise ValueError("registered force must use a seconds state-time axis")
        final_force = force.values[numeric_state_indices(force, (target.final_time,))[0]]
        connected = all(math.isfinite(v) for v in final_force) and any(v != 0 for v in final_force)
    complete = (
        receipt["status"] == "CONFIRMED"
        and quality_status == "PASS"
        and quality_registration_status == "PASS"
        and connected
    )
    return {
        "schema_version": "1",
        "case_id": target.attempt.case_id,
        "revision_id": target.attempt.revision_id,
        "run_id": target.attempt.run_id,
        "preview_id": preview_id,
        "run_status": target.attempt.state.value,
        "quality_status": quality_status,
        "quality_reason": quality_reason,
        "quality_registration_status": quality_registration_status,
        "required_quality": coverage,
        "preview_status": receipt["status"],
        "task_status": "COMPLETE"
        if complete
        else "FAILED"
        if quality_status == "FAIL"
        else TaskStatus.NEEDS_QUALITY.value
        if quality_status == "UNVERIFIED"
        else "NEEDS_PREVIEW",
        "receipt": receipt,
        "quality": quality.to_dict(),
        "finite_nonzero_tool_force": connected,
        "surface_approximation": "UNVERIFIED",
        "scope": "registered synthetic planar demonstration; not scientific or real-model qualification",
    }


def observe_preview(
    store: RegisteredPreviewStore,
    manifest_id: str,
    session: Any,
    *,
    session_probe: Callable[[Any], Any],
    capture: Callable[[dict[str, Any], float], dict[str, Any]],
    timeout_seconds: float,
    adapter_factory: Any = None,
    observation_factory: Any = None,
) -> dict[str, object]:
    if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 120:
        raise ValueError("preview observation requires a finite duration up to 120 seconds")
    deadline = time.monotonic() + timeout_seconds
    target = store.target(manifest_id)
    request = PreviewRequest(
        "preview-" + uuid.uuid4().hex, manifest_id, (target.final_state_id,), (target.variable,)
    )
    observations: list[Any] = []

    def observer(binding: Any) -> Any:
        if len(observations) != 1 or observations[0].binding != binding:
            raise ValueError("no exact fresh observation for this binding")
        return observations[0]

    adapter = (adapter_factory or preview_adapter.PreviewAdapter)(
        studio=session.studio,
        source=target,
        session_probe=session_probe,
        observer=observer,
    )
    receipt = adapter.request_observation(
        target.manifest, request, session=session, timeout_seconds=deadline - time.monotonic()
    )
    if receipt.status is not PreviewStatus.REQUESTED:
        raise ValueError("existing Studio session could not issue an observation request")
    binding = adapter.observation_binding(receipt)
    bound = {
        "nonce": binding.launch_id,
        "source_path": str(binding.path),
        "manifest_id": binding.manifest_id,
        "xplt_digest": binding.xplt_digest,
        "studio": binding.studio.to_dict(),
        "session": {
            "process_id": session.process_id,
            "process_start_marker": session.process_start_marker,
            "window_id": session.window_id,
        },
        "required_state_id": target.final_state_id,
        "required_time_s": target.final_time,
        "required_variable": target.variable,
        "required_component": target.component,
        "required_frame": "World",
        "required_unit": target.unit,
    }
    issue = store.issue(receipt, bound)
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("observation issue exhausted its finite duration")
        observed = capture(issue, remaining)
        expected = {
            "preview_id": receipt.receipt_id,
            "request_nonce": binding.launch_id,
            "manifest_id": binding.manifest_id,
            "loaded_file": str(binding.path),
            "xplt_sha256": binding.xplt_digest,
            "studio": bound["studio"],
            "session": bound["session"],
            "observed_state_id": target.final_state_id,
            "observed_time_s": target.final_time,
            "observed_variable": target.variable,
            "observed_component": target.component,
            "observed_frame": "World",
            "observed_unit": target.unit,
        }
        if any(observed.get(key) != value for key, value in expected.items()):
            raise ValueError(
                "fresh observation does not match the issued target or requested display"
            )
        if not isinstance(observed.get("observer"), str) or not observed["observer"].strip():
            raise ValueError("independent operator observation attribution is required")
        path = Path(observed["capture_path"]).absolute()
        with pinned_read(path) as stream:
            info = os.fstat(stream.fileno())
            created_ns = getattr(info, "st_birthtime_ns", info.st_ctime_ns)
            image = stream.read(20 * 1024 * 1024 + 1)
        if created_ns < issue["issued_ns"] or created_ns > time.time_ns():
            raise ValueError("capture must be newly created after the issued request")
        if not image.startswith(b"\x89PNG\r\n\x1a\n") or len(image) > 20 * 1024 * 1024:
            raise ValueError("fresh observation requires a bounded PNG capture")
        if time.monotonic() >= deadline:
            raise ValueError("fresh observation exceeded its finite duration")
        observed_session = type(session)(
            studio=ToolIdentity(
                observed["studio"]["tool_id"],
                observed["studio"]["version"],
                observed["studio"]["executable_digest"],
            ),
            **observed["session"],
        )
        proof = {
            "kind": "independent-operator-preview-observation",
            "observation": observed,
            "capture_sha256": hashlib.sha256(image).hexdigest(),
            "capture_created_ns": created_ns,
            "issued_ns": issue["issued_ns"],
            "received_ns": time.time_ns(),
        }
        evidence: list[EvidenceRef] = []
        for suffix, payload, media_type in (
            ("capture", image, "image/png"),
            ("observation", canonical_bytes(proof), "application/json"),
        ):
            source = store.storage.ingest_source(
                asset_id=f"{receipt.receipt_id}-{suffix}",
                source_kind="registered_document",
                media_type=media_type,
                content=payload,
            )
            evidence.append(
                EvidenceRef(
                    "1",
                    "registered_document",
                    source.asset_id,
                    "preview.confirmation",
                    source.content_digest,
                )
            )
        evidence_tuple = tuple(evidence)
        observation = (observation_factory or preview_adapter.PreviewObservation)(
            state_ids=(observed["observed_state_id"],),
            variables=(observed["observed_variable"],),
            binding=binding,
            existing_session=observed_session,
            evidence=evidence_tuple,
        )
        observations.append(observation)
        confirmed = adapter.confirm(receipt, evidence_tuple)
        store.finish(confirmed, binding.launch_id)
    except (
        OSError,
        ValueError,
        KeyError,
        PortError,
        StorageConflictError,
        StorageIntegrityError,
    ) as error:
        # Failure records cannot confer confirmation; an invalid current source
        # may prevent even recording failure, leaving its durable request pending.
        try:
            store.finish(replace(receipt, status=PreviewStatus.FAILED), binding.launch_id)
        except (
            OSError,
            PortError,
            StorageConflictError,
            StorageIntegrityError,
        ) as persistence_error:
            error.add_note(f"preview failure record remains pending: {persistence_error}")
        raise
    return preview_summary(store, receipt.receipt_id)
