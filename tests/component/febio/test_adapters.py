from __future__ import annotations

import hashlib
import importlib
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from febio_cae.domain import (
    AssessmentStatus,
    AttemptRecord,
    CapabilityStatus,
    FileEntry,
    ResolvedFileContent,
    RunState,
    TrustedOwnerContext,
)

from .mixed_fixture import MixedPreviewCase
from .quality_fixture import quality_case
from .reader_fixture import setup_reader
from .runner_fixture import _compiled as runner_compiled
from .test_compiler_native import _case
from .test_runner_job import _cleanup


def _module(name: str) -> Any:
    return importlib.import_module(name)


def _owner() -> TrustedOwnerContext:
    return TrustedOwnerContext("case-p3", "run-p3", "attempt-p3", 1)


class _Ownership:
    def __init__(self) -> None:
        self.claimed: list[TrustedOwnerContext] = []
        self.validated: list[AttemptRecord] = []

    def claim(self, owner: TrustedOwnerContext) -> TrustedOwnerContext:
        self.claimed.append(owner)
        return owner

    def validate(self, owner: TrustedOwnerContext, attempt: AttemptRecord) -> TrustedOwnerContext:
        if (
            owner.attempt_id != attempt.attempt_id
            or owner.run_id != attempt.run_id
            or owner.owner_generation != attempt.owner_generation
        ):
            raise RuntimeError("owner mismatch")
        self.validated.append(attempt)
        return owner

    def publish_manifest(self, owner: TrustedOwnerContext, manifest: Any) -> Any:
        self.validate(
            owner,
            AttemptRecord(
                attempt_id=owner.attempt_id,
                run_id=owner.run_id,
                case_id=owner.case_id,
                revision_id="revision-p3",
                owner_generation=owner.owner_generation,
                bundle_digest=manifest.bundle_digest,
                state=RunState.VALIDATING,
                process=None,
                settings=(),
            ),
        )
        return manifest


def _compiled(tmp_path: Path) -> tuple[Any, Any, Any, Any]:
    revision, mesh, profile, bundle, _ = runner_compiled(tmp_path)
    return revision, mesh, profile, bundle


def test_compiler_emits_owned_deterministic_bundle_and_identity(tmp_path: Path) -> None:
    revision, mesh, profile, bundle = _compiled(tmp_path)
    compiler_module = _module("febio_cae.adapters.febio.compiler")
    store = compiler_module.LocalBundleStore(tmp_path / "bundles")
    # Re-open the staged store to prove the bytes are not merely compiler metadata.
    content = store.resolve(bundle, "input/case.feb")

    assert content.startswith(b'<?xml version="1.0" encoding="UTF-8"?>')
    assert b'<febio_spec version="4.0">' in content
    assert b"<material " in content and b'type="isotropic elastic"' in content
    assert b'<contact type="sliding-elastic"' in content
    assert b"<primary>tool-contact</primary>" in content
    assert b"<secondary>part-contact</secondary>" in content
    assert b'<node id="1"' in content and b'<elem id="2">' in content
    assert b"<include" not in content
    assert bundle.spec_digest == revision.spec_digest
    assert bundle.mesh_digest == mesh.artifact_digest
    assert bundle.profile_id == profile.profile_id
    assert bundle.files[0].logical_path == "input/case.feb"
    assert bundle.files[0].digest == hashlib.sha256(content).hexdigest()


def test_compiler_rejects_unverified_required_capability(tmp_path: Path) -> None:
    compiler_module = _module("febio_cae.adapters.febio.compiler")
    revision, mesh, profile = _case()
    profile = replace(
        profile,
        capabilities=(replace(profile.capabilities[0], status=CapabilityStatus.UNSUPPORTED),),
    )
    compiler = compiler_module.CompilerAdapter(
        store=compiler_module.LocalBundleStore(tmp_path / "bundles"), executable=sys.executable
    )

    with pytest.raises(RuntimeError, match="capabil"):
        compiler.compile(revision, mesh, profile)


def test_compiler_rejects_selection_mapping_that_loses_cross_record_identity(
    tmp_path: Path,
) -> None:
    compiler_module = _module("febio_cae.adapters.febio.compiler")
    revision, mesh, profile = _case()
    tool_digest = mesh.sets[1].source_selection_digest
    broken_sets = tuple(
        replace(item, source_selection_digest=tool_digest) if item.body_id == "part-body" else item
        for item in mesh.sets
    )
    broken_mesh = replace(mesh, sets=broken_sets)
    compiler = compiler_module.CompilerAdapter(
        store=compiler_module.LocalBundleStore(tmp_path / "bundles"), executable=sys.executable
    )

    with pytest.raises(RuntimeError, match="selection mapping"):
        compiler.compile(revision, broken_mesh, profile)


def test_reader_validates_binary_xplt_and_exposes_numeric_data(tmp_path: Path) -> None:
    reader, attempt, bundle, mesh, _ = setup_reader(tmp_path)
    manifest = reader.read(attempt, bundle)
    assert manifest.attempt_id == attempt.attempt_id
    assert manifest.bundle_digest == bundle.bundle_digest
    assert manifest.read_result.status.value == "VALIDATED"
    assert {item.output_id for item in manifest.read_result.observations} == {
        "displacement",
        "contact_force",
        "stress",
    }
    displacement = reader.data_store.resolve_manifest_output(manifest.manifest_id, "displacement")
    assert displacement.axis_values == (0.0, 1.0)
    assert displacement.entity_ids == tuple(str(n.node_id) for n in mesh.nodes)
    assert len(displacement.entity_ids) == 20
    assert displacement.values[-1][-1] == pytest.approx(0.2)
    reaction = reader.data_store.resolve_manifest_output(manifest.manifest_id, "contact_force")
    assert reaction.mapping.raw_sign == -1
    assert reaction.values[-1][-1] == pytest.approx(2.0)
    with pytest.raises(RuntimeError, match="before"):
        reader.read(replace(attempt, state=RunState.RUNNING), bundle)


@pytest.mark.parametrize("mutation", ["truncated", "wrong-attempt", "wrong-tool"])
def test_reader_rejects_truncated_or_mutated_or_wrong_attempt_xplt(
    tmp_path: Path, mutation: str
) -> None:
    reader, attempt, bundle, mesh, payload = setup_reader(tmp_path, register=False)
    if mutation == "truncated":
        payload = payload[:-7]
    elif mutation == "wrong-tool":
        changed = payload.replace(b"FEBio 4.12.0", b"FEBio 9.99.0")
        assert changed != payload
        payload = changed
    # Hash the changed bytes correctly: these probes must reach the parser,
    # not pass accidentally because their registered source is absent.
    reader.data_store.register_source(
        attempt,
        bundle,
        ResolvedFileContent(
            FileEntry(
                "output/results.xplt", hashlib.sha256(payload).hexdigest(), len(payload), "result"
            ),
            payload,
        ),
        mesh=mesh,
        state_times=(0.0, 1.0),
        part_bodies={1: mesh.elements[0].body_id, 2: mesh.elements[1].body_id},
        entity_ids={
            "displacement": tuple(str(n.node_id) for n in mesh.nodes),
            "stress": (str(mesh.elements[0].element_id),),
            "contact_force": (mesh.elements[1].body_id,),
        },
    )
    if mutation == "wrong-attempt":
        attempt = replace(attempt, attempt_id="foreign-attempt")
    reason = {
        "truncated": "truncat|bounds|length",
        "wrong-tool": "tool|version",
        "wrong-attempt": "registered source",
    }[mutation]
    with pytest.raises(RuntimeError, match=reason):
        reader.read(attempt, bundle)


def test_reader_rejects_nonfinite_numeric_payload(tmp_path: Path) -> None:
    reader, attempt, bundle, _, _ = setup_reader(tmp_path, defect="nonfinite")
    with pytest.raises(RuntimeError, match="nonfinite"):
        reader.read(attempt, bundle)


def test_runner_uses_owned_process_and_rejects_wrong_owner(tmp_path: Path) -> None:
    revision, _mesh, _profile, bundle = _compiled(tmp_path)
    runner_module = _module("febio_cae.adapters.febio.runner")
    ownership = _Ownership()
    runner = runner_module.RunnerAdapter(ownership=ownership, root=tmp_path / "runs")
    code = "import time; time.sleep(0.15)"
    bundle = replace(bundle, argv=(sys.executable, "-c", code), cwd=str(tmp_path / "runs"))
    owner = _owner()
    started = runner.start(bundle, owner, revision.spec.budget)
    try:
        assert started.state is RunState.RUNNING
        assert ownership.claimed == [owner]
        with pytest.raises(RuntimeError):
            runner.poll(started, TrustedOwnerContext("case-p3", "run-p3", "other", 1))

        current = started
        deadline = time.monotonic() + 5.0
        while (
            current.state in {RunState.RUNNING, RunState.DRAINING} and time.monotonic() < deadline
        ):
            time.sleep(0.03)
            current = runner.poll(current, owner).attempt
        assert current.state is RunState.VALIDATING
        assert current.process is not None
        assert current.process.cwd.startswith(str(tmp_path / "runs"))
    finally:
        _cleanup(runner, started)


def test_runner_cancel_does_not_report_success(tmp_path: Path) -> None:
    revision, _mesh, _profile, bundle = _compiled(tmp_path)
    runner_module = _module("febio_cae.adapters.febio.runner")
    runner = runner_module.RunnerAdapter(ownership=_Ownership(), root=tmp_path / "runs")
    bundle = replace(
        bundle,
        argv=(sys.executable, "-c", "import time; time.sleep(30)"),
        cwd=str(tmp_path / "runs"),
    )
    owner = _owner()
    started = runner.start(bundle, owner, revision.spec.budget)
    try:
        cancelled = runner.cancel(started, owner).attempt
        assert cancelled.state is RunState.CANCELLED
        assert not runner._managed
    finally:
        _cleanup(runner, started)


def test_quality_is_data_driven_and_keeps_missing_data_unverified(tmp_path: Path) -> None:
    case = quality_case(tmp_path)
    quality = _module("febio_cae.adapters.febio.quality").QualityAdapter()
    assessment = quality.assess(case.manifest, case.revision, case.mesh, case.profile, case.store)
    assert assessment.overall_status is AssessmentStatus.PASS
    assert assessment.criteria[0].status is AssessmentStatus.PASS
    assert assessment.criteria[0].measured
    missing = quality.assess(
        replace(case.manifest, read_result=replace(case.manifest.read_result, observations=())),
        case.revision,
        case.mesh,
        case.profile,
        case.store,
    )
    assert missing.overall_status is AssessmentStatus.UNVERIFIED
    assert missing.criteria[0].status is AssessmentStatus.UNVERIFIED
    assert not missing.criteria[0].measured


def test_preview_requires_independent_observation_and_invalidates_mutation(tmp_path: Path) -> None:
    case = MixedPreviewCase(tmp_path)
    adapter = case.adapter()
    receipt = adapter.request(case.manifest, case.request)
    assert receipt.status.value == "LAUNCHED"
    assert case.observed == 0
    confirmed = adapter.confirm(receipt, case.evidence)
    assert confirmed.status.value == "CONFIRMED"
    assert case.launched == [case.path] and case.observed == 1
    case.path.write_bytes(case.path.read_bytes() + b"mutation")
    assert adapter.confirm(confirmed, case.evidence).status.value == "FAILED"
