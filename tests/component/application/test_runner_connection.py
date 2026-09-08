"""Synthetic connected controller; no solver is started by these tests."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from test_persistence_authority import _created, _populate_complete, _profile
from test_registered_execution import _build

from febio_cae.domain import (
    AttemptRecord,
    ExecutionSetting,
    NumericResultData,
    OutputObservation,
    PollResult,
    ProcessIdentity,
    ReadResult,
    ReadStatus,
    ResultDataRef,
    ResultManifest,
    RunState,
)
from febio_cae.domain.ports import PortError


@pytest.mark.parametrize("failure", ["none", "foreign-poll", "bad-geometry"])
def test_registered_runner_connects_issued_state_and_numeric_reader(
    tmp_path: Path, failure: str
) -> None:
    service, created, storage = _created(tmp_path)
    _populate_complete(service, created)
    revision = service.freeze_case(created.case_id).revision
    assert revision is not None
    events: list[str] = []

    def build(rev: Any, destination: Path) -> Any:
        mesh, bundle, inputs = _build(rev, destination)
        mesh = replace(
            mesh,
            provenance=replace(
                mesh.provenance,
                source_geometry_digest="f" * 64
                if failure == "bad-geometry"
                else rev.spec.geometry.geometry_digest,
                source_body_ids=(
                    rev.spec.geometry.body_id.value,
                    rev.spec.rigid_tool.primitive.body_id.value,
                ),
                mesh_recipe_digest=hashlib.sha256(
                    b"trusted concrete mesher recipe, not mesh_policy alone"
                ).hexdigest(),
            ),
        )
        return mesh, replace(bundle, mesh_digest=mesh.artifact_digest), inputs

    class Runner:
        def __init__(self, ownership: Any, root: Path, inputs: Any) -> None:
            self.ownership = ownership
            self.root = root

        def start(self, bundle: Any, owner: Any, budget: Any) -> Any:
            self.ownership.claim(owner)
            events.append("start")
            path = (
                self.root
                / owner.case_id
                / owner.run_id
                / owner.attempt_id
                / str(owner.owner_generation)
            )
            (path / "output").mkdir(parents=True)
            (path / "output/results.xplt").write_bytes(b"synthetic only")
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
                    str(path),
                    bundle.thread_count,
                    "synthetic",
                ),
                (
                    *bundle.settings,
                    ExecutionSetting("attempt_root", str(path)),
                    ExecutionSetting("max_elapsed_seconds", budget.max_elapsed.to_si().value),
                ),
            )

        def poll(self, attempt: Any, owner: Any) -> Any:
            self.ownership.validate(owner, attempt)
            events.append("poll")
            if failure == "foreign-poll":
                return PollResult(replace(attempt, revision_id="foreign"), ())
            return PollResult(
                attempt.transition_to(RunState.DRAINING).transition_to(RunState.VALIDATING), ()
            )

        def reconcile(self, attempt: Any, owner: Any) -> Any:
            raise AssertionError("this issued-runner fixture has no restart path")

        def cancel(self, attempt: Any, owner: Any) -> Any:
            events.append("cancel")
            from febio_cae.domain import CancelResult

            return CancelResult(
                attempt.transition_to(RunState.DRAINING).transition_to(RunState.CANCELLED),
                "synthetic cleanup",
            )

    def read(attempt: Any, bundle: Any, files: Any, registered: Any) -> Any:
        events.append("read")
        assert attempt.process is not None
        assert files[0].logical_path == "output/results.xplt"
        mapping = _profile(bundle.profile_id).output_mappings[0]
        request = revision.spec.outputs.requests[0]
        numeric = NumericResultData(
            ResultDataRef(
                "numeric",
                "0" * 64,
                "numeric-result-v1",
                files[0].logical_path,
                bundle.bundle_digest,
                attempt.attempt_id,
            ),
            mapping,
            "state_time",
            "s",
            (0.0, 1.0),
            ("1",),
            ("z",),
            ((0.0,), (0.001,)),
        )
        numeric = replace(
            numeric,
            reference=replace(numeric.reference, content_digest=numeric.expected_content_digest),
        )
        registered.register_numeric_data(numeric)
        return ResultManifest(
            "native-shaped-manifest",
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
                        2,
                        numeric.reference,
                    ),
                ),
                (),
            ),
        )

    if failure != "none":
        with pytest.raises(PortError):
            service._execute_ports(
                created.case_id, revision.revision_id, build=build, runner_factory=Runner, read=read
            )
        assert "read" not in events
        if failure == "bad-geometry":
            assert not events
        else:
            assert "cancel" in events
    else:
        manifest = service._execute_ports(
            created.case_id, revision.revision_id, build=build, runner_factory=Runner, read=read
        )
        assert events == ["start", "poll", "read"]
        assert storage.get_manifest(manifest.manifest_id) == manifest
        reference = manifest.read_result.observations[0].data_ref
        assert reference is not None
        assert storage.resolve(reference).axis_id == "state_time"
