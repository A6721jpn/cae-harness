"""Actual-layout synthetic reader behavior through the frozen public codec."""

from dataclasses import replace
from pathlib import Path

import pytest

from febio_cae.adapters.febio.xplt_reader import LocalResultDataStore
from febio_cae.domain import NumericResultData, PortError, ReadStatus
from febio_cae.domain.codec import decode_record, encode_record

from .reader_fixture import setup_reader
from .fixtures import make_xplt_fixture


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
