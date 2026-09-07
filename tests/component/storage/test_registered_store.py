from __future__ import annotations

import gc
import hashlib
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from febio_cae.domain.ports import PortError, PortErrorCategory


def _service_type() -> Any:
    from febio_cae.application.service import RegisteredCaseService

    return RegisteredCaseService


def test_create_registers_case_and_reopens_by_id(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    case_root = tmp_path / "case-root"
    cad_path = tmp_path / "source.step"
    cad_bytes = b"synthetic-step-v1"
    cad_path.write_bytes(cad_bytes)

    first = _service_type()(state_dir=state_dir)
    created = first.create_case(case_root=case_root, cad_path=cad_path)

    assert created.case_id
    assert created.case_id not in str(case_root)
    assert created.source_asset.content_digest == hashlib.sha256(cad_bytes).hexdigest()
    assert (case_root / "registry.sqlite3").is_file()
    assert (case_root / "cases" / created.case_id / "sources").is_dir()

    reopened = _service_type()(state_dir=state_dir)
    draft = reopened.current_draft(created.case_id)
    assert draft.case_id == created.case_id
    assert draft.generation == 0
    assert draft.unresolved_fields
    resolved = reopened.resolve_source(created.case_id, created.source_asset.asset_id)
    assert resolved.content == cad_bytes


def test_source_resolver_rejects_corrupt_registered_file(tmp_path: Path) -> None:
    cad_path = tmp_path / "source.step"
    cad_path.write_bytes(b"original")
    service = _service_type()(state_dir=tmp_path / "state")
    created = service.create_case(case_root=tmp_path / "case", cad_path=cad_path)
    source_path = (
        tmp_path
        / "case"
        / "cases"
        / created.case_id
        / "sources"
        / f"{created.source_asset.asset_id}.bin"
    )
    source_path.write_bytes(b"tampered")

    with pytest.raises(PortError) as error:
        service.resolve_source(created.case_id, created.source_asset.asset_id)

    assert error.value.category is PortErrorCategory.INTEGRITY


def test_create_does_not_modify_original_cad(tmp_path: Path) -> None:
    cad_path = tmp_path / "source.step"
    cad_bytes = b"keep-me"
    cad_path.write_bytes(cad_bytes)
    service = _service_type()(state_dir=tmp_path / "state")

    service.create_case(case_root=tmp_path / "case", cad_path=cad_path)

    assert cad_path.read_bytes() == cad_bytes


def test_catalog_rejects_windows_junction_case_root(tmp_path: Path) -> None:
    from febio_cae.application.service import RegisteredCaseService, ServiceConflictError
    from febio_cae.domain.partial_case_spec import PartialCaseSpec

    cad_path = tmp_path / "source.step"
    cad_path.write_bytes(b"junction-source")
    case_root = tmp_path / "case-root"
    redirected_root = tmp_path / "redirected-root"
    preserved_root = tmp_path / "preserved-root"
    service = RegisteredCaseService(state_dir=tmp_path / "state")
    created = service.create_case(case_root=case_root, cad_path=cad_path)
    shutil.copytree(case_root, redirected_root)
    del service
    gc.collect()
    case_root.rename(preserved_root)

    command = f'mklink /J "{case_root}" "{redirected_root}"'
    result = subprocess.run(
        f"cmd.exe /d /c {command}",
        shell=True,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(f"could not create the required junction: {result.stdout}{result.stderr}")
    try:
        reopened_service = RegisteredCaseService(state_dir=tmp_path / "state")
        with pytest.raises(ServiceConflictError):
            reopened_service.set_spec(
                created.case_id,
                values=PartialCaseSpec(),
                expected_generation=0,
            )
    finally:
        subprocess.run(
            f'cmd.exe /d /c rmdir "{case_root}"',
            shell=True,
            capture_output=True,
            check=False,
        )
