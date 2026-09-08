from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from febio_cae.application.service import RegisteredCaseService
from febio_cae.storage import CaseStorage, StorageConflictError, StorageIntegrityError


def _reserve() -> Any:
    from febio_cae.storage import demo_budget

    return demo_budget.reserve_demo_attempt


def _case(tmp_path: Path) -> tuple[CaseStorage, str]:
    source = tmp_path / "demo.step"
    source.write_bytes(b"synthetic budget test only")
    created = RegisteredCaseService(state_dir=tmp_path / "state").create_case(
        case_root=tmp_path / "case", cad_path=source
    )
    return CaseStorage(tmp_path / "case"), created.case_id


def test_solver_reservations_survive_reconstruction_and_have_no_refund(tmp_path: Path) -> None:
    storage, case_id = _case(tmp_path)
    reserve = _reserve()
    assert reserve(storage, case_id, "febio", "operation-1") is True
    reopened = CaseStorage(storage.root)
    assert reserve(reopened, case_id, "febio", "operation-2") is True
    with pytest.raises(StorageConflictError, match="exhausted"):
        reserve(CaseStorage(storage.root), case_id, "febio", "operation-3")


def test_duplicate_is_observation_not_permission_to_launch_again(tmp_path: Path) -> None:
    storage, case_id = _case(tmp_path)
    reserve = _reserve()
    assert reserve(storage, case_id, "febio", "same-operation") is True
    assert reserve(CaseStorage(storage.root), case_id, "febio", "same-operation") is False
    assert reserve(storage, case_id, "febio", "second-operation") is True


def test_studio_is_independent_and_single_use(tmp_path: Path) -> None:
    storage, case_id = _case(tmp_path)
    reserve = _reserve()
    assert reserve(storage, case_id, "febio", "solver") is True
    assert reserve(storage, case_id, "studio", "preview") is True
    with pytest.raises(StorageConflictError, match="exhausted"):
        reserve(storage, case_id, "studio", "preview-again")


def test_gmsh_budget_is_already_exhausted(tmp_path: Path) -> None:
    storage, case_id = _case(tmp_path)
    with pytest.raises(StorageConflictError, match="exhausted"):
        _reserve()(storage, case_id, "gmsh", "fourth-mesh")


def test_unknown_case_and_kind_do_not_allocate(tmp_path: Path) -> None:
    storage, case_id = _case(tmp_path)
    reserve = _reserve()
    with pytest.raises(StorageIntegrityError):
        reserve(storage, "case-unknown", "febio", "one")
    with pytest.raises(ValueError):
        reserve(storage, case_id, "new-cohort", "one")
    assert reserve(storage, case_id, "febio", "one") is True


def test_empty_operation_is_rejected(tmp_path: Path) -> None:
    storage, case_id = _case(tmp_path)
    with pytest.raises(ValueError):
        _reserve()(storage, case_id, "febio", "")
