from __future__ import annotations

import hashlib
import struct
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from febio_cae.domain import (
    AssessmentStatus,
    CoulombFriction,
    EvidenceRef,
    NumericResultData,
    PortError,
    PreviewStatus,
    ResultDataRef,
    RunState,
    ToolIdentity,
    TrustedOwnerContext,
)
from febio_cae.domain.codec import decode_record, encode_record

from .fixtures import evidence, make_mesh, make_profile, make_revision, make_xplt_fixture


def _compiled(tmp_path: Path) -> tuple[Any, Any, Any, Any, Any]:
    from febio_cae.adapters.febio.compiler import CompilerAdapter, LocalBundleStore

    revision = make_revision()
    mesh = make_mesh(revision.spec)
    profile = make_profile(sys.executable)
    store = LocalBundleStore(tmp_path / "bundles")
    bundle = CompilerAdapter(store=store, executable=sys.executable).compile(
        revision, mesh, profile
    )
    return revision, mesh, profile, bundle, store


def _attempt(bundle: Any, revision: Any, root: Path, *, state: RunState) -> Any:
    from febio_cae.domain import AttemptRecord, ExecutionSetting, ProcessIdentity

    digest = hashlib.sha256(Path(bundle.argv[0]).read_bytes()).hexdigest()
    return AttemptRecord(
        attempt_id="attempt-p3",
        run_id="run-p3",
        case_id=revision.case_id,
        revision_id=revision.revision_id,
        owner_generation=1,
        bundle_digest=bundle.bundle_digest,
        state=state,
        process=ProcessIdentity(
            executable=str(Path(bundle.argv[0])),
            executable_digest=digest,
            argv=bundle.argv,
            cwd=str(root),
            thread_count=1,
            start_marker="r1-process",
        ),
        settings=tuple(bundle.settings) + (ExecutionSetting("attempt_root", str(root)),),
    )


class _Ownership:
    def claim(self, owner: TrustedOwnerContext) -> TrustedOwnerContext:
        return owner

    def validate(self, owner: TrustedOwnerContext, attempt: Any) -> TrustedOwnerContext:
        if (owner.run_id, owner.attempt_id, owner.owner_generation) != (
            attempt.run_id,
            attempt.attempt_id,
            attempt.owner_generation,
        ):
            raise RuntimeError("owner mismatch")
        return owner

    def publish_manifest(self, owner: TrustedOwnerContext, manifest: Any) -> Any:
        return manifest


def _owner(run_id: str = "run-p3") -> TrustedOwnerContext:
    return TrustedOwnerContext("case-p3", run_id, "attempt-p3", 1)


def test_compiler_uses_native_febio_structure_and_semantics(tmp_path: Path) -> None:
    revision, mesh, _profile, bundle, store = _compiled(tmp_path)
    content = store.resolve(bundle, "input/case.feb")

    assert b'<febio_spec version="4.0">' in content
    assert b"<MeshDomains>" in content
    assert b"<Domain" in content
    assert b'<Nodes name="part-nodes">' in content
    assert b'<Nodes name="tool-nodes">' in content
    assert b'<Elements type="tet10" name="part-elements">' in content
    assert b'<Elements type="tet10" name="tool-elements">' in content
    assert b'<plotfile type="xplt"' in content
    assert b"<var type=\"node\">displacement</var>" in content
    assert b"-0.0" not in content
    assert revision.spec.motion.direction.z == 1.0
    assert mesh.artifact_digest == bundle.mesh_digest


def test_compiler_preserves_signed_motion_contact_numbers_and_resolved_set_ids(
    tmp_path: Path,
) -> None:
    revision, mesh, _profile, _bundle, _store = _compiled(tmp_path)
    from febio_cae.adapters.febio.compiler import CompilerAdapter, LocalBundleStore

    friction = CoulombFriction(
        coefficient=revision.spec.contact.friction.coefficient
        if isinstance(revision.spec.contact.friction, CoulombFriction)
        else __import__("febio_cae.domain", fromlist=["Quantity"]).Quantity(0.37, "1"),
        model_evidence=evidence("contact.friction_model", "r1-friction-model"),
        coefficient_evidence=evidence("contact.friction_coefficient", "r1-friction-coefficient"),
    )
    changed_contact = replace(revision.spec.contact, friction=friction)
    changed_motion = replace(
        revision.spec.motion,
        direction=replace(revision.spec.motion.direction, z=-1.0),
    )
    changed_revision = replace(
        revision,
        spec=replace(revision.spec, contact=changed_contact, motion=changed_motion),
    )
    resolved_sets = tuple(
        replace(item, set_id=f"resolved-{item.set_id}") for item in mesh.sets
    )
    resolved_mesh = replace(mesh, sets=resolved_sets)
    profile = make_profile(sys.executable)
    store = LocalBundleStore(tmp_path / "bundles-r1")
    bundle = CompilerAdapter(store=store, executable=sys.executable).compile(
        changed_revision, resolved_mesh, profile
    )
    content = store.resolve(bundle, "input/case.feb")

    assert b"-1" in content
    assert b">0.37<" in content
    assert b"resolved-tool-contact" in content
    assert b"resolved-part-contact" in content
    assert b">explicit-profile<" not in content


def test_reader_accepts_observed_header_without_private_identity_and_codec_round_trips(
    tmp_path: Path,
) -> None:
    revision, mesh, profile, bundle, _store = _compiled(tmp_path)
    output_root = tmp_path / "attempt-p3"
    output_path = output_root / "output" / "results.xplt"
    output_path.parent.mkdir(parents=True)
    payload = make_xplt_fixture(
        attempt_id="attempt-p3",
        bundle_digest=bundle.bundle_digest,
        mesh_digest=mesh.artifact_digest,
        include_identity=False,
    )
    # Ownership is supplied by the attempt/bundle, as in the observed native
    # header, rather than by private adapter-authored identity fields.
    output_path.write_bytes(payload)
    reader = __import__(
        "febio_cae.adapters.febio.xplt_reader", fromlist=["XpltReaderAdapter"]
    ).XpltReaderAdapter(profile=profile)

    manifest = reader.read(
        _attempt(bundle, revision, output_root, state=RunState.VALIDATING), bundle
    )
    numeric = reader.data_store.resolve_manifest_output(manifest.manifest_id, "displacement")
    assert numeric.reference.codec_id == "numeric-result-v1"
    assert decode_record(encode_record(numeric), NumericResultData) == numeric


def test_reader_rejects_unknown_state_block_and_allows_one_state(tmp_path: Path) -> None:
    revision, mesh, profile, bundle, _store = _compiled(tmp_path)
    output_root = tmp_path / "attempt-p3"
    output_path = output_root / "output" / "results.xplt"
    output_path.parent.mkdir(parents=True)
    payload = make_xplt_fixture(
        attempt_id="attempt-p3", bundle_digest=bundle.bundle_digest, mesh_digest=mesh.artifact_digest
    )
    offset = 4
    for _ in range(2):
        _identifier, size = struct.unpack_from("<II", payload, offset)
        offset += 8 + size
    broken = payload[:offset] + struct.pack("<I", 0xDEADBEEF) + payload[offset + 4 :]
    output_path.write_bytes(broken)
    reader = __import__(
        "febio_cae.adapters.febio.xplt_reader", fromlist=["XpltReaderAdapter"]
    ).XpltReaderAdapter(profile=profile)
    with pytest.raises(PortError, match="state"):
        reader.read(_attempt(bundle, revision, output_root, state=RunState.VALIDATING), bundle)

    output_path.write_bytes(
        make_xplt_fixture(
            attempt_id="attempt-p3",
            bundle_digest=bundle.bundle_digest,
            mesh_digest=mesh.artifact_digest,
            state_count=1,
        )
    )
    one_state = reader.read(
        _attempt(bundle, revision, output_root, state=RunState.VALIDATING), bundle
    )
    assert one_state.read_result.observations[0].state_count == 1


def test_quality_selects_requested_component_and_rejects_wrong_binding(tmp_path: Path) -> None:
    revision, mesh, profile, bundle, _store = _compiled(tmp_path)
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
                    ((3.0, 0.0, 0.0), (3.0, 0.0, 0.0)),
                    ((3.0, 0.0, 0.2), (3.0, 0.0, 0.2)),
                ),
                "reaction forces": (
                    ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0)),
                    ((0.0, 0.0, 1.0), (0.0, 0.0, 2.0)),
                ),
            },
        )
    )
    attempt = _attempt(bundle, revision, output_root, state=RunState.VALIDATING)
    reader = __import__(
        "febio_cae.adapters.febio.xplt_reader", fromlist=["XpltReaderAdapter"]
    ).XpltReaderAdapter(profile=profile)
    manifest = reader.read(attempt, bundle)
    quality = __import__("febio_cae.adapters.febio.quality", fromlist=["QualityAdapter"]).QualityAdapter()
    assessment = quality.assess(manifest, revision, mesh, profile, reader.data_store)
    assert assessment.overall_status is AssessmentStatus.PASS
    numeric = reader.data_store.resolve_manifest_output(manifest.manifest_id, "displacement")
    wrong_ref = replace(
        numeric.reference,
        content_digest=numeric.reference.content_digest,
        bundle_digest="9" * 64,
        attempt_id="other-attempt",
    )
    wrong_numeric = replace(numeric, reference=wrong_ref)

    class WrongData:
        def resolve_manifest_output(self, _manifest_id: str, _output_id: str) -> NumericResultData:
            return wrong_numeric

    rejected = quality.assess(manifest, revision, mesh, profile, WrongData())
    assert rejected.overall_status is AssessmentStatus.UNVERIFIED


def test_runner_rejects_path_escape_and_unregistered_executable(tmp_path: Path) -> None:
    revision, _mesh, _profile, bundle, _store = _compiled(tmp_path)
    from febio_cae.adapters.febio.runner import RunnerAdapter

    runner = RunnerAdapter(ownership=_Ownership(), root=tmp_path / "runs")
    with pytest.raises(PortError, match="run_id"):
        runner.start(bundle, _owner("../escaped"), revision.spec.budget)

    marker = tmp_path / "ran.txt"
    unregistered = replace(
        bundle,
        tool=replace(bundle.tool, executable_digest="0" * 64),
        argv=(sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')"),
    )
    with pytest.raises(PortError, match="executable"):
        runner.start(unregistered, _owner(), revision.spec.budget)
    time.sleep(0.05)
    assert not marker.exists()


def test_runner_drain_reconciles_and_cancel_does_not_publish_before_drain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    revision, _mesh, _profile, bundle, _store = _compiled(tmp_path)
    from febio_cae.adapters.febio.runner import RunnerAdapter

    runner = RunnerAdapter(ownership=_Ownership(), root=tmp_path / "runs")
    bundle = replace(bundle, argv=(sys.executable, "-c", "pass"))
    started = runner.start(bundle, _owner(), revision.spec.budget)
    descendant_live = True
    monkeypatch.setattr(
        RunnerAdapter,
        "_descendant_pids",
        staticmethod(lambda _pid: (999,) if descendant_live else ()),
    )
    time.sleep(0.05)
    draining = runner.poll(started, _owner()).attempt
    assert draining.state is RunState.DRAINING
    descendant_live = False
    reconciled = runner.poll(draining, _owner()).attempt
    assert reconciled.state is RunState.VALIDATING

    long_runner = RunnerAdapter(ownership=_Ownership(), root=tmp_path / "runs-long")
    long_bundle = replace(bundle, argv=(sys.executable, "-c", "import time; time.sleep(30)"))
    long_started = long_runner.start(long_bundle, _owner(), revision.spec.budget)
    monkeypatch.setattr(long_runner, "_wait_for_drain", lambda _managed, force: False)
    cancelled = long_runner.cancel(long_started, _owner()).attempt
    assert cancelled.state is RunState.DRAINING
    # The test owns the process; clean it up after the nonterminal assertion.
    monkeypatch.undo()
    long_runner.cancel(long_started, _owner())


def test_preview_requires_real_launch_bound_studio_and_final_digest(tmp_path: Path) -> None:
    revision, mesh, profile, bundle, _store = _compiled(tmp_path)
    output_root = tmp_path / "attempt-p3"
    output_path = output_root / "output" / "results.xplt"
    output_path.parent.mkdir(parents=True)
    output_path.write_bytes(
        make_xplt_fixture(
            attempt_id="attempt-p3", bundle_digest=bundle.bundle_digest, mesh_digest=mesh.artifact_digest
        )
    )
    reader = __import__(
        "febio_cae.adapters.febio.xplt_reader", fromlist=["XpltReaderAdapter"]
    ).XpltReaderAdapter(profile=profile)
    manifest = reader.read(
        _attempt(bundle, revision, output_root, state=RunState.VALIDATING), bundle
    )
    preview_module = __import__("febio_cae.adapters.preview.studio", fromlist=["PreviewAdapter"])
    studio = ToolIdentity("febio-studio", "2.8.0", "f" * 64)
    request = preview_module.PreviewRequest("preview-r1", manifest.manifest_id, (0,), ("displacement",))
    preview = preview_module.PreviewAdapter(
        studio=studio,
        source=preview_module.FileSystemPreviewSource(output_path),
        observer=lambda _path, _identity: preview_module.PreviewObservation((0,), ("displacement",)),
    )
    receipt = preview.request(manifest, request)
    assert receipt.status is PreviewStatus.FAILED

    launched = preview_module.PreviewAdapter(
        studio=studio,
        source=preview_module.FileSystemPreviewSource(output_path),
        launcher=lambda _path, _identity: True,
        observer=lambda path, _identity: (
            path.write_bytes(path.read_bytes() + b"mutation")
            or preview_module.PreviewObservation((0,), ("displacement",))
        ),
    )
    receipt = launched.request(manifest, request)
    wrong_studio = replace(receipt, studio=ToolIdentity("febio-studio", "99.0.0", "0" * 64))
    confirmed = launched.confirm(wrong_studio, (EvidenceRef("1", "registered_document", "r1", "preview", "1" * 64),))
    assert confirmed.status is PreviewStatus.FAILED
