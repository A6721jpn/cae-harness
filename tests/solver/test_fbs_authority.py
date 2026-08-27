from __future__ import annotations

import copy
import importlib
import math
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import pytest

from febio_cae_harness.solver import (
    FbsAdapterAuthority,
    FbsAdapterManager,
    FbsValidation,
    SolverConfigurationError,
    SolverLaunchSpec,
    SolverSupervisor,
    validate_requested_fields,
)


class MappingAdapter:
    def __init__(self, values: dict[str, object] | None = None) -> None:
        self.values = values

    def read_fields(self, _path: Path, fields: Sequence[str]) -> dict[str, object]:
        return dict(self.values or {field: 1.0 for field in fields})


def callable_adapter(_path: Path, fields: Sequence[str]) -> dict[str, object]:
    return {field: 2.0 for field in fields}


def make_authority(
    tmp_path: Path, adapter: object | None = None
) -> tuple[FbsAdapterAuthority, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "attempt.xplt"
    path.write_bytes(b"synthetic-xplt")
    manager = FbsAdapterManager(adapter or MappingAdapter(), "synthetic-runtime", tmp_path)
    return manager.issue_authority(), path


def test_public_fbs_surface_contains_only_canonical_names() -> None:
    module = importlib.import_module("febio_cae_harness.solver.fbs")
    assert set(module.__all__) == {
        "FbsAdapterAuthority",
        "FbsAdapterManager",
        "FbsAdapterProtocol",
        "FbsValidation",
        "validate_requested_fields",
    }
    for name in {
        "FBSAdapter",
        "FBSValidation",
        "FbsAdapterBoundary",
        "FbsResultValidation",
        "OfficialFBSAdapter",
        "OfficialFbsAdapter",
        "OfficialFBSAdapterBoundary",
        "validate_fbs_fields",
    }:
        assert not hasattr(module, name)


def test_manager_requires_runtime_identity_and_accepts_callable(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        FbsAdapterManager(MappingAdapter(), " ")
    with pytest.raises(TypeError):
        FbsAdapterManager(object(), "synthetic")
    authority, path = make_authority(tmp_path, callable_adapter)
    validate_requested_fields(authority, path, ("displacement",))


def test_forged_authority_and_manager_state_are_rejected(tmp_path: Path) -> None:
    authority, path = make_authority(tmp_path)
    forged_authority = object.__new__(FbsAdapterAuthority)
    forged_manager = object.__new__(FbsAdapterManager)
    with pytest.raises(TypeError):
        validate_requested_fields(forged_authority, path, ("stress",))
    with pytest.raises(TypeError):
        forged_manager.issue_authority()
    with pytest.raises(TypeError):
        FbsAdapterAuthority()
    with pytest.raises(TypeError):
        copy.copy(authority)
    with pytest.raises(TypeError):
        copy.deepcopy(authority)
    state_name = "state"
    with pytest.raises(AttributeError):
        setattr(authority, state_name, object())
    state_name = "adapter"
    with pytest.raises(AttributeError):
        setattr(forged_manager, state_name, object())
    with pytest.raises(TypeError):

        class AuthorityChild(FbsAdapterAuthority):
            pass


def test_caller_markers_and_envelopes_do_not_establish_authority(tmp_path: Path) -> None:
    class MarkedAdapter(MappingAdapter):
        is_official = True
        official = True

        def read_fields(self, path: Path, fields: Sequence[str]) -> dict[str, object]:
            del path, fields
            return {"values": {"stress": 1.0}, "official": True}

    authority, path = make_authority(tmp_path, MarkedAdapter())
    result = validate_requested_fields(authority, path, ("stress",))
    assert not result.valid and result.missing_fields == ("stress",)


@pytest.mark.parametrize("fields", [(), ("stress", "stress"), ("",), (" ",)])
def test_requested_fields_must_be_nonempty_and_unique(
    tmp_path: Path, fields: tuple[str, ...]
) -> None:
    authority, path = make_authority(tmp_path)
    with pytest.raises(ValueError):
        validate_requested_fields(authority, path, fields)


def test_path_must_be_live_regular_xplt_inside_attempt_root(tmp_path: Path) -> None:
    authority, path = make_authority(tmp_path / "attempt")
    outside = tmp_path / "outside.xplt"
    outside.write_bytes(b"outside")
    directory = path.with_name("directory.xplt")
    directory.mkdir()
    for invalid in (outside, directory, path.with_suffix(".txt")):
        with pytest.raises(ValueError):
            validate_requested_fields(authority, invalid, ("stress",))


def test_adapter_mutation_invalidates_digest_bound_validation(tmp_path: Path) -> None:
    class MutatingAdapter:
        def read_fields(self, path: Path, fields: Sequence[str]) -> dict[str, object]:
            path.write_bytes(b"mutated")
            return {field: 1.0 for field in fields}

    authority, path = make_authority(tmp_path, MutatingAdapter())
    result = validate_requested_fields(authority, path, ("stress",))
    assert not result.valid and result.digest_before != result.digest_after
    assert any("mutated" in issue for issue in result.issues)


@pytest.mark.parametrize(
    ("values", "missing", "non_finite"),
    [
        ({"other": 1.0}, ("stress",), ()),
        ({"stress": math.nan}, (), ("stress",)),
        ({"stress": [1.0, math.inf]}, (), ("stress",)),
        ({"stress": True}, (), ("stress",)),
    ],
)
def test_requested_values_must_be_present_and_finite(
    tmp_path: Path,
    values: dict[str, object],
    missing: tuple[str, ...],
    non_finite: tuple[str, ...],
) -> None:
    authority, path = make_authority(tmp_path, MappingAdapter(values))
    result = validate_requested_fields(authority, path, ("stress",))
    assert not result.valid
    assert result.missing_fields == missing and result.non_finite_fields == non_finite


def test_validation_binds_authority_runtime_path_digest_and_fields(tmp_path: Path) -> None:
    authority, path = make_authority(tmp_path)
    result = validate_requested_fields(authority, path, ("stress",))
    assert result.authority is authority
    assert result.runtime_identity == "synthetic-runtime" and result.xplt_path == path
    assert result.requested_fields == ("stress",)
    assert result.digest_before == result.digest_after and len(result.digest_before) == 64


def test_supervisor_rejects_arbitrary_adapter_and_caller_validation(tmp_path: Path) -> None:
    input_path = tmp_path / "model.feb"
    input_path.write_text("synthetic", encoding="utf-8")
    spec = SolverLaunchSpec(
        executable=Path(sys.executable),
        input_path=input_path,
        attempt_root=tmp_path,
        log_path=tmp_path / "attempt.log",
        xplt_path=tmp_path / "attempt.xplt",
        arguments=("-c", "pass"),
    )
    with pytest.raises(SolverConfigurationError):
        SolverSupervisor(spec, fbs_adapter=cast(FbsAdapterAuthority, MappingAdapter()))
    forged = cast(FbsAdapterAuthority, object.__new__(FbsValidation))
    with pytest.raises(TypeError):
        validate_requested_fields(forged, tmp_path / "attempt.xplt", ("stress",))
