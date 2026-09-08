"""Synthetic registered CLI edits; no native execution is exercised."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tests.component.application.test_persistence_authority import _created, _populate_complete
from febio_cae.cli.main import main
from febio_cae.domain import EvidenceRef, PartialCaseSpec, Quantity
from febio_cae.domain.case_patch import CasePatch, CasePatchEdit


def _invoke(args: list[str]) -> int:
    try:
        return main(args)
    except SystemExit as error:
        return int(error.code)


def _prepared(tmp_path: Path, monkeypatch: Any, capsys: Any) -> tuple[Any, ...]:
    service, created, storage = _created(tmp_path)
    _populate_complete(service, created)
    parent = service.freeze_case(created.case_id).revision
    assert parent is not None
    monkeypatch.setattr("febio_cae.cli.case.RegisteredCaseService", lambda **_: service)
    instruction = "Synthetic exercise: E=2e6 Pa, nu=0.3; all other inputs unchanged."
    evidence = EvidenceRef(
        "1",
        "user_instruction",
        "material-edit",
        "material.youngs_modulus",
        hashlib.sha256(instruction.encode()).hexdigest(),
    )
    registration = tmp_path / "instruction.json"
    registration.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "values": PartialCaseSpec().to_dict(),
                "evidence": [],
                "source_declarations": [
                    {
                        "source_kind": evidence.source_kind,
                        "reference": evidence.reference,
                        "target_field": evidence.target_field,
                        "content": instruction,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    generation = service.current_draft(created.case_id).generation
    assert (
        main(
            [
                "case",
                "spec",
                created.case_id,
                "--file",
                str(registration),
                "--expected-generation",
                str(generation),
                "--json",
            ]
        )
        == 0
    )
    capsys.readouterr()
    material = replace(
        parent.spec.material, youngs_modulus=Quantity(2e6, "Pa"), youngs_modulus_evidence=evidence
    )
    patch = CasePatch(
        parent.revision_id,
        parent.spec_digest,
        (CasePatchEdit("material", material, True),),
        (evidence,),
    )
    return service, created, storage, parent, patch


def test_cli_material_patch_validate_freeze_preserves_parent(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    service, created, storage, parent, patch = _prepared(tmp_path, monkeypatch, capsys)
    original = parent.to_bytes()
    source = storage.resolve_source(created.source_asset).content
    path = tmp_path / "patch.json"
    path.write_bytes(patch.to_bytes())
    generation = service.current_draft(created.case_id).generation
    assert (
        _invoke(
            [
                "case",
                "patch",
                created.case_id,
                "--file",
                str(path),
                "--expected-generation",
                str(generation),
                "--json",
            ]
        )
        == 0
    )
    updated = json.loads(capsys.readouterr().out)
    assert updated["draft"]["generation"] == generation + 1
    assert main(["case", "validate", created.case_id, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "VALIDATED"
    assert main(["case", "freeze", created.case_id, "--json"]) == 0
    frozen = json.loads(capsys.readouterr().out)
    child = service.get_revision(created.case_id, frozen["revision_id"])
    assert child.parent_revision_id == parent.revision_id
    assert child.parent_spec_digest == parent.spec_digest
    assert child.spec.material.youngs_modulus == Quantity(2e6, "Pa")
    assert replace(child.spec, material=parent.spec.material) == parent.spec
    assert child.spec_digest != parent.spec_digest
    assert service.get_revision(created.case_id, parent.revision_id).to_bytes() == original
    assert storage.resolve_source(created.source_asset).content == source
    assert storage.resolve_revision_mesh_quality(child) == storage.resolve_revision_mesh_quality(
        parent
    )
    assert (
        _invoke(
            [
                "case",
                "patch",
                created.case_id,
                "--file",
                str(path),
                "--expected-generation",
                str(service.current_draft(created.case_id).generation),
                "--json",
            ]
        )
        == 8
    )


@pytest.mark.parametrize("bad", ["digest", "generation", "evidence", "absent"])
def test_cli_patch_rejects_stale_context_and_blocks_incomplete_material(
    tmp_path: Path, monkeypatch: Any, capsys: Any, bad: str
) -> None:
    service, created, _, parent, patch = _prepared(tmp_path, monkeypatch, capsys)
    before = service.current_draft(created.case_id)
    generation = before.generation
    if bad == "digest":
        patch = replace(patch, parent_spec_digest="0" * 64)
    elif bad == "generation":
        generation -= 1
    elif bad == "evidence":
        patch = replace(patch, evidence=(replace(patch.evidence[0], reference="unregistered"),))
    else:
        patch = replace(patch, edits=(CasePatchEdit("material", None, False),))
    path = tmp_path / "patch.json"
    path.write_bytes(patch.to_bytes())
    assert _invoke(
        [
            "case",
            "patch",
            created.case_id,
            "--file",
            str(path),
            "--expected-generation",
            str(generation),
            "--json",
        ]
    ) == (0 if bad == "absent" else 8)
    capsys.readouterr()
    if bad == "absent":
        assert main(["case", "validate", created.case_id, "--json"]) == 3
        assert json.loads(capsys.readouterr().out)["status"] == "NEEDS_INPUT"
        assert main(["case", "freeze", created.case_id, "--json"]) == 3
    else:
        assert service.current_draft(created.case_id) == before
    assert service.get_revision(created.case_id, parent.revision_id) == parent
