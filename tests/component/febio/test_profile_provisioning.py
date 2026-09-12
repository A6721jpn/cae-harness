"""Caller-authored supported labels cannot become trusted profile authority."""

import base64
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from febio_cae.adapters.febio import xplt_reader
from febio_cae.application import _profile_provisioning
from febio_cae.application.service import RegisteredCaseService
from febio_cae.domain import EvidenceRef, PortError, PortErrorCategory, Quantity, ToolIdentity
from febio_cae.storage.mesh_quality import MeshQualityRegistration
from febio_cae.storage.registry import StorageConflictError

from .fixtures import make_profile


def _synthetic_bundle(tmp_path: Path) -> tuple[Path, tuple[str, ...]]:
    note = b"Synthetic caller-authored note; no native qualification exists."
    note_digest = hashlib.sha256(note).hexdigest()
    reader_digest = hashlib.sha256(Path(xplt_reader.__file__).read_bytes()).hexdigest()
    original = make_profile()
    profile = replace(
        original,
        solver=ToolIdentity(
            "febio", "4.12.0", "03b9db12c4b3e2ed0cf027be6b8b5d0cef2d4edc9eab26f5a2bd8193efb770c9"
        ),
        reader=ToolIdentity("febio-cae-xplt-reader", "0.1.0", reader_digest),
        capabilities=(
            *original.capabilities,
            replace(
                original.capabilities[0],
                capability_id="febio.scope.planar_linear_frictionless_fixed_xyz",
            ),
        ),
    )
    profiles = {}
    for purpose, profile_id in _profile_provisioning._PROFILE_IDS.items():
        record = replace(profile, profile_id=profile_id).to_dict()

        def rebind(value: object) -> None:
            if isinstance(value, dict):
                if {"reference", "target_field", "content_digest"} <= value.keys():
                    value.update(
                        source_kind="registered_document",
                        reference="forged-qualification",
                        content_digest=note_digest,
                    )
                for child in value.values():
                    rebind(child)
            elif isinstance(value, list):
                for child in value:
                    rebind(child)

        rebind(record)
        profiles[purpose] = record
    mesh = MeshQualityRegistration(
        _profile_provisioning._MESH_PROFILE_ID,
        Quantity(1e-8, "m"),
        ("box",),
        "synthetic",
        "1",
        "synthetic",
        (
            EvidenceRef(
                "1",
                "registered_document",
                "forged-qualification",
                "mesh_quality.qualification",
                note_digest,
            ),
        ),
    )
    payload = {
        "schema_version": "1",
        "kind": "REVIEWED_PLANAR_COMPATIBILITY_BUNDLE",
        "authority_status": "PM_APPROVED_FOR_SCOPED_PROVISIONING",
        "scope": {"enforced_scope_capability": _profile_provisioning._SCOPE_CAPABILITY},
        "profiles": profiles,
        "mesh_quality": json.loads(mesh.to_bytes()),
        "source_documents": [
            {
                "asset_id": "forged-qualification",
                "source_kind": "registered_document",
                "media_type": "application/json",
                "content_digest": note_digest,
                "content_base64": base64.b64encode(note).decode("ascii"),
            }
        ],
        "publication": {"native_operations": 0},
        "supersedes": "synthetic fixture only; not a native qualification",
    }
    path = tmp_path / "forged.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path, tuple(str(record["profile_id"]) for record in profiles.values())


def test_caller_authored_qualification_cannot_provision_profiles(tmp_path: Path) -> None:
    path, profile_ids = _synthetic_bundle(tmp_path)
    cad = tmp_path / "source.step"
    cad.write_bytes(b"ISO-10303-21; END-ISO-10303-21;")
    service = RegisteredCaseService(state_dir=tmp_path / "state")
    created = service.create_case(case_root=tmp_path / "case", cad_path=cad)
    before = service.current_draft(created.case_id)
    with pytest.raises(PortError):
        service.provision_planar_profiles(created.case_id, bundle_path=path)
    assert service.current_draft(created.case_id) == before
    for profile_id in profile_ids:
        with pytest.raises(PortError):
            service.compatibility.get_profile(profile_id)


def test_unqualified_installed_reader_cannot_publish_trusted_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, profile_ids = _synthetic_bundle(tmp_path)
    content = path.read_bytes()
    # Trust only this synthetic release fixture; production trust remains pinned.
    monkeypatch.setattr(
        _profile_provisioning, "_APPROVED_BUNDLE_SHA256", hashlib.sha256(content).hexdigest()
    )
    monkeypatch.setattr(_profile_provisioning, "_APPROVED_BUNDLE_SIZE", len(content))
    different_reader = tmp_path / "different_reader.py"
    different_reader.write_bytes(b"unqualified reader bytes")
    monkeypatch.setattr(xplt_reader, "__file__", str(different_reader))
    cad = tmp_path / "source.step"
    cad.write_bytes(b"ISO-10303-21; END-ISO-10303-21;")
    service = RegisteredCaseService(state_dir=tmp_path / "state")
    created = service.create_case(case_root=tmp_path / "case", cad_path=cad)
    before = service.current_draft(created.case_id)
    with pytest.raises(PortError) as rejected:
        service.provision_planar_profiles(created.case_id, bundle_path=path)
    assert rejected.value.category is PortErrorCategory.INTEGRITY
    assert service.current_draft(created.case_id) == before
    with pytest.raises(StorageConflictError):
        service._storage(created.case_id).source_asset("forged-qualification")
    for profile_id in profile_ids:
        with pytest.raises(PortError):
            service.compatibility.get_profile(profile_id)
