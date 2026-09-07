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
    ExecutionBundle,
    ProcessIdentity,
    RunState,
    ToolIdentity,
    TrustedOwnerContext,
)

from .fixtures import make_mesh, make_profile, make_revision, make_xplt_fixture


def _module(name: str) -> Any:
    return importlib.import_module(name)


def _attempt(
    bundle: ExecutionBundle, revision: Any, cwd: Path, *, state: RunState
) -> AttemptRecord:
    executable = Path(bundle.argv[0])
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    process = ProcessIdentity(
        executable=str(executable),
        executable_digest=digest,
        argv=bundle.argv,
        cwd=str(cwd),
        thread_count=bundle.thread_count,
        start_marker="synthetic-process-start",
    )
    return AttemptRecord(
        attempt_id="attempt-p3",
        run_id="run-p3",
        case_id=revision.case_id,
        revision_id=revision.revision_id,
        owner_generation=1,
        bundle_digest=bundle.bundle_digest,
        state=state,
        process=process,
        settings=bundle.settings,
    )


def _owned_attempt(bundle: ExecutionBundle, revision: Any, cwd: Path) -> AttemptRecord:
    return _attempt(bundle, revision, cwd, state=RunState.VALIDATING)


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
    compiler_module = _module("febio_cae.adapters.febio.compiler")
    revision = make_revision()
    mesh = make_mesh(revision.spec)
    profile = make_profile(sys.executable)
    store = compiler_module.LocalBundleStore(tmp_path / "bundles")
    compiler = compiler_module.CompilerAdapter(store=store, executable=sys.executable)
    bundle = compiler.compile(revision, mesh, profile)
    return revision, mesh, profile, bundle


def test_compiler_emits_owned_deterministic_bundle_and_identity(tmp_path: Path) -> None:
    revision, mesh, profile, bundle = _compiled(tmp_path)
    compiler_module = _module("febio_cae.adapters.febio.compiler")
    store = compiler_module.LocalBundleStore(tmp_path / "bundles")
    # Re-open the staged store to prove the bytes are not merely compiler metadata.
    content = store.resolve(bundle, "input/case.feb")

    assert content.startswith(b'<?xml version="1.0" encoding="UTF-8"?>')
    assert b'<febio_spec version="4.12">' in content
    assert b"<material " in content and b'type="isotropic elastic"' in content
    assert b'<contact type="sliding-elastic"' in content
    assert b"<primary>tool-contact</primary>" in content
    assert b"<secondary>part-contact</secondary>" in content
    assert b'<node id="1"' in content and b'<elem id="2" type="tet10"' in content
    assert b"<include" not in content
    assert bundle.spec_digest == revision.spec_digest
    assert bundle.mesh_digest == mesh.artifact_digest
    assert bundle.profile_id == profile.profile_id
    assert bundle.files[0].logical_path == "input/case.feb"
    assert bundle.files[0].digest == hashlib.sha256(content).hexdigest()


def test_compiler_rejects_unverified_required_capability(tmp_path: Path) -> None:
    compiler_module = _module("febio_cae.adapters.febio.compiler")
    revision = make_revision()
    mesh = make_mesh(revision.spec)
    profile = make_profile(sys.executable)
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
    revision = make_revision()
    mesh = make_mesh(revision.spec)
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
        compiler.compile(revision, broken_mesh, make_profile(sys.executable))


def test_reader_validates_binary_xplt_and_exposes_numeric_data(tmp_path: Path) -> None:
    revision, mesh, profile, bundle = _compiled(tmp_path)
    output_root = tmp_path / "attempt-p3"
    output_root.mkdir()
    xplt = make_xplt_fixture(
        attempt_id="attempt-p3",
        bundle_digest=bundle.bundle_digest,
        mesh_digest=mesh.artifact_digest,
    )
    output_path = output_root / "output" / "results.xplt"
    output_path.parent.mkdir()
    output_path.write_bytes(xplt)
    attempt = _owned_attempt(bundle, revision, output_root)

    reader_module = _module("febio_cae.adapters.febio.xplt_reader")
    reader = reader_module.XpltReaderAdapter(profile=profile)
    manifest = reader.read(attempt, bundle)
    assert manifest.attempt_id == attempt.attempt_id
    assert manifest.bundle_digest == bundle.bundle_digest
    assert manifest.read_result.status.value == "VALIDATED"
    assert {item.output_id for item in manifest.read_result.observations} == {
        "displacement",
        "contact_force",
    }
    displacement = reader.data_store.resolve_manifest_output(manifest.manifest_id, "displacement")
    assert displacement.axis_values == (0.0, 1.0)
    assert displacement.entity_ids == ("1", "2")
    assert displacement.values[-1][-1] == pytest.approx(0.2)
    reaction = reader.data_store.resolve_manifest_output(manifest.manifest_id, "contact_force")
    assert reaction.mapping.raw_sign == -1
    assert reaction.values[-1][-1] == pytest.approx(-2.0)

    with pytest.raises(RuntimeError, match="before"):
        reader.read(replace(attempt, state=RunState.RUNNING), bundle)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload[:-7],
        lambda payload: payload.replace(b"attempt-p3", b"attempt-old"),
        lambda payload: payload.replace(b"FEBio 4.12.0", b"FEBio 9.99.0"),
    ],
)
def test_reader_rejects_truncated_or_mutated_or_wrong_attempt_xplt(
    tmp_path: Path, mutation: Any
) -> None:
    revision, mesh, profile, bundle = _compiled(tmp_path)
    output_root = tmp_path / "attempt-p3"
    output_path = output_root / "output" / "results.xplt"
    output_path.parent.mkdir(parents=True)
    output_path.write_bytes(
        mutation(
            make_xplt_fixture(
                attempt_id="attempt-p3",
                bundle_digest=bundle.bundle_digest,
                mesh_digest=mesh.artifact_digest,
            )
        )
    )
    reader = _module("febio_cae.adapters.febio.xplt_reader").XpltReaderAdapter(profile=profile)

    with pytest.raises(RuntimeError):
        reader.read(_owned_attempt(bundle, revision, output_root), bundle)


def test_reader_rejects_nonfinite_numeric_payload(tmp_path: Path) -> None:
    revision, mesh, profile, bundle = _compiled(tmp_path)
    output_root = tmp_path / "attempt-p3"
    output_path = output_root / "output" / "results.xplt"
    output_path.parent.mkdir(parents=True)
    output_path.write_bytes(
        make_xplt_fixture(
            attempt_id="attempt-p3",
            bundle_digest=bundle.bundle_digest,
            mesh_digest=mesh.artifact_digest,
            values={
                "displacement": (
                    ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0)),
                    ((0.0, 0.0, float("nan")), (0.0, 0.0, 0.2)),
                ),
                "reaction forces": (
                    ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0)),
                    ((0.0, 0.0, 1.0), (0.0, 0.0, 2.0)),
                ),
            },
        )
    )
    reader = _module("febio_cae.adapters.febio.xplt_reader").XpltReaderAdapter(profile=profile)

    with pytest.raises(RuntimeError, match="nonfinite"):
        reader.read(_owned_attempt(bundle, revision, output_root), bundle)


def test_runner_uses_owned_process_and_rejects_wrong_owner(tmp_path: Path) -> None:
    revision, _mesh, _profile, bundle = _compiled(tmp_path)
    runner_module = _module("febio_cae.adapters.febio.runner")
    ownership = _Ownership()
    runner = runner_module.RunnerAdapter(ownership=ownership, root=tmp_path / "runs")
    code = "import time; time.sleep(0.15)"
    bundle = replace(bundle, argv=(sys.executable, "-c", code), cwd=str(tmp_path / "runs"))
    owner = _owner()
    started = runner.start(bundle, owner, revision.spec.budget)
    assert started.state is RunState.RUNNING
    assert ownership.claimed == [owner]
    with pytest.raises(RuntimeError):
        runner.poll(started, TrustedOwnerContext("case-p3", "run-p3", "other", 1))

    current = started
    deadline = time.monotonic() + 5.0
    while current.state is RunState.RUNNING and time.monotonic() < deadline:
        time.sleep(0.03)
        current = runner.poll(current, owner).attempt
    assert current.state is RunState.VALIDATING
    assert current.process is not None
    assert current.process.cwd.startswith(str(tmp_path / "runs"))


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
    cancelled = runner.cancel(started, owner).attempt
    assert cancelled.state is RunState.CANCELLED


def test_quality_is_data_driven_and_keeps_missing_data_unverified(tmp_path: Path) -> None:
    revision, mesh, profile, bundle = _compiled(tmp_path)
    output_root = tmp_path / "attempt-p3"
    output_path = output_root / "output" / "results.xplt"
    output_path.parent.mkdir(parents=True)
    output_path.write_bytes(
        make_xplt_fixture(
            attempt_id="attempt-p3",
            bundle_digest=bundle.bundle_digest,
            mesh_digest=mesh.artifact_digest,
        )
    )
    attempt = _owned_attempt(bundle, revision, output_root)
    reader = _module("febio_cae.adapters.febio.xplt_reader").XpltReaderAdapter(profile=profile)
    manifest = reader.read(attempt, bundle)
    quality_module = _module("febio_cae.adapters.febio.quality")
    quality = quality_module.QualityAdapter()
    assessment = quality.assess(manifest, revision, mesh, profile, reader.data_store)
    assert assessment.overall_status is AssessmentStatus.PASS
    assert assessment.criteria[0].status is AssessmentStatus.PASS
    assert assessment.criteria[0].measured

    missing = quality.assess(
        replace(manifest, read_result=replace(manifest.read_result, observations=())),
        revision,
        mesh,
        profile,
        reader.data_store,
    )
    assert missing.overall_status is AssessmentStatus.UNVERIFIED
    assert missing.criteria[0].status is AssessmentStatus.UNVERIFIED


def test_preview_requires_independent_observation_and_invalidates_mutation(tmp_path: Path) -> None:
    revision, mesh, profile, bundle = _compiled(tmp_path)
    output_root = tmp_path / "attempt-p3"
    output_path = output_root / "output" / "results.xplt"
    output_path.parent.mkdir(parents=True)
    output_path.write_bytes(
        make_xplt_fixture(
            attempt_id="attempt-p3",
            bundle_digest=bundle.bundle_digest,
            mesh_digest=mesh.artifact_digest,
        )
    )
    attempt = _owned_attempt(bundle, revision, output_root)
    reader = _module("febio_cae.adapters.febio.xplt_reader").XpltReaderAdapter(profile=profile)
    manifest = reader.read(attempt, bundle)
    preview_module = _module("febio_cae.adapters.preview.studio")
    studio = ToolIdentity("febio-studio", "2.8.0", "f" * 64)
    launched: list[Path] = []

    def launch(path: Path, identity: ToolIdentity) -> bool:
        launched.append(path)
        return identity == studio

    preview = preview_module.PreviewAdapter(
        studio=studio,
        source=preview_module.FileSystemPreviewSource(output_path),
        launcher=launch,
        observer=lambda path, identity: preview_module.PreviewObservation(
            state_ids=(0, 1), variables=("displacement", "reaction forces")
        ),
    )
    request = preview_module.PreviewRequest(
        preview_id="preview-p3",
        manifest_id=manifest.manifest_id,
        state_ids=(0, 1),
        variables=("displacement", "reaction forces"),
    )
    receipt = preview.request(manifest, request)
    assert receipt.status.value == "LAUNCHED"
    confirmed = preview.confirm(receipt, (reader_module_evidence(),))
    assert confirmed.status.value == "CONFIRMED"
    assert launched == [output_path]

    output_path.write_bytes(output_path.read_bytes() + b"mutation")
    invalidated = preview.confirm(confirmed, (reader_module_evidence(),))
    assert invalidated.status.value == "FAILED"


def reader_module_evidence() -> Any:
    from .fixtures import evidence

    return evidence("preview.confirmation", "independent-observation")
