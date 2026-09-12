"""Caller-authored supported labels cannot become trusted profile authority."""

import base64
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from febio_cae.adapters.febio import xplt_reader
from febio_cae.application.service import RegisteredCaseService
from febio_cae.domain import EvidenceRef, PortError, Quantity, ToolIdentity
from febio_cae.storage.mesh_quality import MeshQualityRegistration

from .fixtures import make_profile


def test_caller_authored_qualification_cannot_provision_profiles(tmp_path: Path) -> None:
    cad = tmp_path / "source.step"
    cad.write_bytes(b"ISO-10303-21; END-ISO-10303-21;")
    service = RegisteredCaseService(state_dir=tmp_path / "state")
    created = service.create_case(case_root=tmp_path / "case", cad_path=cad)
    before = service.current_draft(created.case_id)
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
    for purpose in ("solver", "outputs", "quality"):
        record = replace(profile, profile_id="forged-" + purpose).to_dict()

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
        "forged-mesh",
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
        "scope": {"purpose": "Caller claims qualification; this is not authority."},
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
        "publication": {"pm_approval": "APPROVED", "review": "ACCEPT"},
    }
    path = tmp_path / "forged.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PortError):
        service.provision_planar_profiles(created.case_id, bundle_path=path)
    assert service.current_draft(created.case_id) == before
    for record in profiles.values():
        with pytest.raises(PortError):
            service.compatibility.get_profile(str(record["profile_id"]))
