"""Actual-layout synthetic reader behavior through the frozen public codec."""

import hashlib
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from febio_cae.adapters.febio._windows_job import WindowsJobProcess
from febio_cae.adapters.febio.compiler import LocalBundleStore
from febio_cae.adapters.febio.runner import RunnerAdapter
from febio_cae.adapters.febio.xplt_reader import LocalResultDataStore
from febio_cae.domain import (
    FileEntry,
    NumericResultData,
    PortError,
    ReadStatus,
    ResolvedFileContent,
    RunState,
    TrustedOwnerContext,
)
from febio_cae.domain.codec import decode_record, encode_record

from .fixtures import make_xplt_fixture
from .reader_fixture import setup_reader
from .runner_fixture import _Ownership
from .test_compiler_native import _case
from .test_runner_authority import _cleanup_process, _finish


def test_registered_native_layout_maps_nodes_elements_and_rigid_body_then_persists(
    tmp_path: Path,
) -> None:
    reader, attempt, bundle, mesh, _ = setup_reader(tmp_path)
    manifest = reader.read(attempt, bundle)
    assert manifest.read_result.status is ReadStatus.VALIDATED
    displacement = reader.data_store.resolve_manifest_output(manifest.manifest_id, "displacement")
    stress = reader.data_store.resolve_manifest_output(manifest.manifest_id, "stress")
    force = reader.data_store.resolve_manifest_output(manifest.manifest_id, "contact_force")
    assert displacement.entity_ids == tuple(str(node.node_id) for node in mesh.nodes)
    assert stress.entity_ids == ("1",) and stress.component_ids == (
        "xx",
        "yy",
        "zz",
        "xy",
        "yz",
        "xz",
    )
    assert stress.values[-1] == (10.0, 2.0, 3.0, 4.0, 5.0, 6.0)
    assert force.entity_ids == (mesh.elements[1].body_id,)
    assert force.values[-1] == (0.0, 0.0, 2.0)  # explicitly registered raw/canonical sign
    for index, data in enumerate((displacement, stress, force)):
        assert data.reference.codec_id == "numeric-result-v1"
        path = tmp_path / f"numeric-{index}.json"
        path.write_bytes(encode_record(data))
        restored = decode_record(path.read_bytes(), NumericResultData)
        consumer = LocalResultDataStore()
        consumer.register(manifest.manifest_id, data.mapping.canonical_id, restored)
        assert consumer.resolve(data.reference) == data


def test_registered_one_state_policy_is_valid(tmp_path: Path) -> None:
    reader, attempt, bundle, _, _ = setup_reader(tmp_path, times=(0.0,))
    manifest = reader.read(attempt, bundle)
    assert all(observation.state_count == 1 for observation in manifest.read_result.observations)


def test_missing_required_state_is_not_validated(tmp_path: Path) -> None:
    reader, attempt, bundle, _, _ = setup_reader(tmp_path, times=(0.0,), required_times=(0.0, 1.0))
    with pytest.raises(PortError, match="state"):
        reader.read(attempt, bundle)


@pytest.mark.parametrize(
    "defect",
    [
        "unknown-state",
        "unknown-inner",
        "rigid-layout",
        "rigid-region",
        "node-identity",
        "connectivity",
        "nonfinite",
        "version",
        "compression",
    ],
)
def test_registered_unsupported_structure_never_validates(tmp_path: Path, defect: str) -> None:
    reader, attempt, bundle, _, _ = setup_reader(tmp_path, defect=defect)
    # The rejection must name the structure under test, not be an unrelated
    # header rejection masking every deeper parser path.
    expected = {
        "unknown-state": "state",
        "unknown-inner": "state",
        "rigid-layout": "layout",
        "rigid-region": "region",
        "node-identity": "node",
        "connectivity": "connectivity",
        "nonfinite": "nonfinite",
        "version": "version",
        "compression": "compression",
    }[defect]
    with pytest.raises(PortError, match=expected):
        reader.read(attempt, bundle)


def test_missing_registration_is_not_replaced_by_native_header_identity(tmp_path: Path) -> None:
    reader, attempt, bundle, _, _ = setup_reader(tmp_path, register=False)
    # Historical candidate dialect: even embedded matching identities must not
    # replace independent output registration. This was accepted before repair.
    profile = replace(
        reader.profile,
        output_mappings=tuple(
            replace(mapping, native_name="reaction forces")
            if mapping.canonical_id == "contact_force"
            else mapping
            for mapping in reader.profile.output_mappings
            if mapping.canonical_id != "stress"
        ),
    )
    reader = type(reader)(profile=profile)
    assert attempt.process is not None
    (Path(attempt.process.cwd) / "output/results.xplt").write_bytes(
        make_xplt_fixture(
            attempt_id=attempt.attempt_id,
            bundle_digest=bundle.bundle_digest,
            mesh_digest=bundle.mesh_digest,
        )
    )
    with pytest.raises(PortError, match="registered"):
        reader.read(attempt, bundle)


def test_registered_source_cannot_be_replayed_in_another_run(tmp_path: Path) -> None:
    reader, attempt, bundle, _, _ = setup_reader(tmp_path)
    with pytest.raises(PortError, match="registered"):
        reader.read(replace(attempt, run_id="other-run"), bundle)


def test_resolver_cannot_substitute_a_different_verified_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reader, attempt, bundle, _, _ = setup_reader(tmp_path)
    content = b"different registered-looking file"
    foreign = ResolvedFileContent(
        FileEntry(
            "output/results.xplt", hashlib.sha256(content).hexdigest(), len(content), "result"
        ),
        content,
    )
    monkeypatch.setattr(reader.data_store, "resolve_file", lambda *args: foreign)
    with pytest.raises(PortError, match="registered"):
        reader.read(attempt, bundle)
    assert not reader.data_store._data


def test_native_indices_do_not_replace_noncontiguous_entity_ids(tmp_path: Path) -> None:
    reader, attempt, bundle, mesh, _ = setup_reader(tmp_path, renumber=True)
    manifest = reader.read(attempt, bundle)
    nodes = reader.data_store.resolve_manifest_output(manifest.manifest_id, "displacement")
    stress = reader.data_store.resolve_manifest_output(manifest.manifest_id, "stress")
    force = reader.data_store.resolve_manifest_output(manifest.manifest_id, "contact_force")
    assert nodes.entity_ids == tuple(str(node.node_id) for node in mesh.nodes)
    assert nodes.values[-1][-1] == pytest.approx(mesh.nodes[-1].node_id / 100.0)
    assert stress.entity_ids == ("71",)
    assert force.entity_ids == (mesh.elements[1].body_id,)


def test_registered_snapshot_survives_filesystem_alias_change(tmp_path: Path) -> None:
    reader, attempt, bundle, _, _ = setup_reader(tmp_path)
    assert attempt.process is not None
    (Path(attempt.process.cwd) / "output/results.xplt").write_bytes(b"replacement")
    assert reader.read(attempt, bundle).read_result.status is ReadStatus.VALIDATED


def test_registered_snapshot_cannot_be_overwritten(tmp_path: Path) -> None:
    reader, attempt, bundle, mesh, _ = setup_reader(tmp_path)
    source = reader.data_store.source_for(attempt, bundle)
    with pytest.raises(PortError, match="immutable"):
        reader.data_store.register_source(
            attempt,
            bundle,
            source.raw,
            mesh=mesh,
            state_times=(0.0,),
            part_bodies=dict(source.part_bodies),
            entity_ids=dict(source.entity_ids),
        )


def test_compiler_runner_registered_reader_codec_connection(tmp_path: Path) -> None:
    # Real bounded Python process writes synthetic bytes; this is not FEBio E2E.
    reader, original, compiled, mesh, payload = setup_reader(tmp_path)
    source = reader.data_store.source_for(original, compiled)
    script = (
        "from pathlib import Path;"
        "Path('output').mkdir(exist_ok=True);"
        f"Path('output/results.xplt').write_bytes(bytes.fromhex('{payload.hex()}'))"
    )
    bundle = replace(compiled, argv=(sys.executable, "-c", script))
    runner = RunnerAdapter(
        ownership=_Ownership(),
        root=tmp_path / "runs",
        bundle_store=LocalBundleStore(tmp_path / "bundles"),
    )
    owner = TrustedOwnerContext(bundle.case_id, "connected-run", "connected-attempt", 1)
    attempt = runner.start(bundle, owner, _case()[0].spec.budget)
    managed = next(iter(runner._managed.values()))
    try:
        attempt = _finish(runner, attempt, owner)
        assert attempt.state is RunState.VALIDATING
        assert isinstance(managed.process, WindowsJobProcess)
        assert managed.process.closed and not runner._managed
        assert attempt.process is not None
        content = (Path(attempt.process.cwd) / "output/results.xplt").read_bytes()
        assert content == payload
        reader.data_store.register_source(
            attempt,
            bundle,
            ResolvedFileContent(source.raw.entry, content),
            mesh=mesh,
            state_times=source.state_times,
            part_bodies=dict(source.part_bodies),
            entity_ids=dict(source.entity_ids),
        )
        manifest = reader.read(attempt, bundle)
        data = reader.data_store.resolve_manifest_output(manifest.manifest_id, "stress")
        assert decode_record(encode_record(data), NumericResultData) == data
    finally:
        _cleanup_process(managed.process)
        managed.stdout.close()
        managed.stderr.close()
