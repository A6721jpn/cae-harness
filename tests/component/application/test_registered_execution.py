"""Synthetic synchronous producer: service owns and closes every output writer."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from test_persistence_authority import _created, _populate_complete, _profile

from febio_cae.domain import ExecutionBundle, FileEntry, RunState
from febio_cae.domain.artifacts import (
    MeshArtifact,
    MeshElement,
    MeshNode,
    MeshProvenance,
    MeshQualityRecord,
)
from febio_cae.domain.ports import PortError, TrustedOwnerContext
from febio_cae.domain.results import OutputObservation, ReadResult, ReadStatus, ResultManifest
from febio_cae.storage.registry import CaseStorage


def _build(
    revision: Any, destination: Path
) -> tuple[MeshArtifact, ExecutionBundle, dict[str, bytes]]:
    mesh = MeshArtifact(
        "synthetic-mesh",
        revision.spec.geometry.placement.target_frame,
        MeshProvenance(
            revision.spec.geometry.source_step_digest,
            (revision.spec.geometry.body_id.value,),
            (),
            hashlib.sha256(revision.spec.mesh_policy.to_bytes()).hexdigest(),
            "synthetic",
            "1",
            "synthetic",
            "tet10-canonical-v1",
            "tet10-face-canonical-v1",
        ),
        tuple(
            MeshNode(i, xyz)
            for i, xyz in enumerate(
                [
                    (0, 0, 0),
                    (1, 0, 0),
                    (0, 1, 0),
                    (0, 0, 1),
                    (0.5, 0, 0),
                    (0.5, 0.5, 0),
                    (0, 0.5, 0),
                    (0, 0, 0.5),
                    (0.5, 0, 0.5),
                    (0, 0.5, 0.5),
                ],
                start=1,
            )
        ),
        (MeshElement(1, "tet10", tuple(range(1, 11)), revision.spec.geometry.body_id.value),),
        (),
        (),
        (MeshQualityRecord("synthetic-check", 1, "1", 0, "PASS", "structural fixture only"),),
    )
    data = b"synthetic compiled input"
    entry = FileEntry("input.feb", hashlib.sha256(data).hexdigest(), len(data), "input")
    profile = _profile(revision.spec.solver_policy.profile.profile_id)
    bundle = ExecutionBundle(
        "compiled",
        revision.case_id,
        revision.revision_id,
        revision.spec_digest,
        mesh.artifact_digest,
        profile.profile_id,
        profile.solver,
        (entry,),
        ("synthetic",),
        str(destination),
        1,
        (),
    )
    return mesh, bundle, {entry.logical_path: data}


@pytest.mark.parametrize(
    "failure", ["none", "missing-output", "running", "retarget", "tampered", "partial-manifest", "unissued-reader"]
)
def test_registered_execution_publication_boundary(tmp_path: Path, failure: str) -> None:
    service, created, _ = _created(tmp_path)
    _populate_complete(service, created)
    frozen = service.freeze_case(created.case_id).revision
    assert frozen is not None
    required = tuple(item.request_id for item in frozen.spec.outputs.requests)

    def produce(bundle: ExecutionBundle, inputs: dict[str, bytes]) -> dict[str, bytes]:
        assert inputs["input.feb"] == b"synthetic compiled input"
        return {} if failure == "missing-output" else {key: b"0,1,2" for key in required}

    def read(attempt: Any, bundle: Any, files: Any, storage: CaseStorage) -> ResultManifest:
        owner = TrustedOwnerContext(
            attempt.case_id, attempt.run_id, attempt.attempt_id, attempt.owner_generation
        )
        assert attempt.state is RunState.VALIDATING
        storage.validate(owner, attempt)
        if failure in {"running", "retarget"}:
            forged = (
                replace(attempt, state=RunState.RUNNING)
                if failure == "running"
                else replace(attempt, revision_id="invented")
            )
            with pytest.raises(PortError):
                storage.validate(owner, forged)
        for entry in files:
            assert storage.resolve_file(entry, bundle, attempt).content == b"0,1,2"
        if failure == "tampered":
            path = Path(bundle.cwd) / files[0].logical_path
            path.write_bytes(b"changed")
        observations = tuple(
            OutputObservation(
                request.request_id,
                request.location,
                "scalar",
                request.display_unit,
                request.frame,
                request.measure_id,
                len(frozen.spec.outputs.saved_times),
            )
            for request in frozen.spec.outputs.requests
        )
        manifest = ResultManifest(
            "manifest",
            attempt.attempt_id,
            bundle.bundle_digest,
            files[:1] if failure == "partial-manifest" else files,
            ReadResult(
                ReadStatus.VALIDATED,
                _profile(bundle.profile_id).reader,
                () if failure == "partial-manifest" else observations,
                (),
            ),
        )

        if failure == "unissued-reader":
            with pytest.raises(PortError):
                storage.publish_manifest(owner, manifest)
        return manifest

    if failure in {"missing-output", "tampered", "partial-manifest"}:
        with pytest.raises((PortError, ValueError)):
            service._execute_registered(
                created.case_id, frozen.revision_id, build=_build, produce=produce, read=read
            )
    else:
        manifest = service._execute_registered(
            created.case_id, frozen.revision_id, build=_build, produce=produce, read=read
        )
        assert {item.role for item in manifest.files} == set(required)
        reopened = CaseStorage(created.case_root)
        assert reopened.get_manifest(manifest.manifest_id) == manifest
