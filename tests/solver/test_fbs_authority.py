from __future__ import annotations

import copy
import importlib
import math
import os
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest

import febio_cae_harness.solver.fbs as fbs_module
import febio_cae_harness.solver.supervisor as supervisor_module
from febio_cae_harness.contracts import IntentContract
from febio_cae_harness.evidence import EvidenceStore
from febio_cae_harness.solver import (
    FbsAdapterAuthority,
    FbsAdapterManager,
    FbsValidation,
    SolverConfigurationError,
    SolverLaunchSpec,
    SolverOwnershipError,
    SolverRunResult,
    SolverState,
    SolverSupervisor,
    validate_requested_fields,
)
from febio_cae_harness.solver import headless as headless_module
from febio_cae_harness.solver.process_authority import ProcessAuthorityError
from febio_cae_harness.solver.runtime import FebioRuntimeDiagnostic, probe_febio
from febio_cae_harness.workspace import AttemptWorkspace, ValidatedCaseWorkspace


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


def test_caller_supplied_descriptor_alias_cannot_forge_physical_root_membership(
    tmp_path: Path,
) -> None:
    if os.name != "posix":
        pytest.skip("POSIX descriptor-relative XPLT authority")

    authority, path = make_authority(tmp_path / "attempt")
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_xplt = outside / path.name
    outside_xplt.write_bytes(b"foreign-xplt")
    outside_fd = os.open(os.fspath(outside), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        physical_root = Path(f"/proc/self/fd/{outside_fd}")
        physical_path = physical_root / path.name
        with pytest.raises(ValueError, match="physical|descriptor|authority"):
            validate_requested_fields(
                authority,
                path,
                ("stress",),
                physical_path=physical_path,
                physical_root=physical_root,
            )
    finally:
        os.close(outside_fd)


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


def test_only_validate_issued_validation_crosses_internal_boundary(
    tmp_path: Path,
) -> None:
    authority, path = make_authority(tmp_path)
    issued = validate_requested_fields(authority, path, ("stress",))

    caller_built = FbsValidation(
        xplt_path=path,
        requested_fields=("stress",),
        available_fields=("stress",),
        values={"stress": 1.0},
        missing_fields=(),
        non_finite_fields=(),
        valid=True,
        authority=authority,
        runtime_identity="synthetic-runtime",
        digest_before=issued.digest_before,
        digest_after=issued.digest_after,
    )
    check = fbs_module._require_issued_validation
    with pytest.raises(TypeError, match="adopted|supervisor"):
        check(
            issued,
            authority=authority,
            xplt_path=path,
            requested_fields=("stress",),
        )
    with pytest.raises(TypeError):
        check(
            caller_built,
            authority=authority,
            xplt_path=path,
            requested_fields=("stress",),
        )


def test_former_fbs_registries_cannot_be_authority_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("_MANAGER_REGISTRY", "_AUTHORITY_REGISTRY", "_VALIDATION_REGISTRY"):
        assert not hasattr(fbs_module, name)
        monkeypatch.setattr(fbs_module, name, {}, raising=False)

    authority, path = make_authority(tmp_path)
    issued = validate_requested_fields(authority, path, ("stress",))
    monkeypatch.setattr(
        fbs_module,
        "_VALIDATION_REGISTRY",
        {id(issued): issued},
        raising=False,
    )
    with pytest.raises(TypeError):
        fbs_module._require_issued_validation(
            issued,
            authority,
            path,
            ("stress",),
        )


def test_issued_validation_rejects_context_forgery_and_state_changes(
    tmp_path: Path,
) -> None:
    module = importlib.import_module("febio_cae_harness.solver.fbs")
    authority, path = make_authority(tmp_path)
    issued = validate_requested_fields(authority, path, ("stress",))
    check = module._require_issued_validation
    other_authority, other_path = make_authority(tmp_path / "other")

    for forged, expected_authority, expected_path in (
        (object.__new__(FbsValidation), authority, path),
        (copy.copy(issued), authority, path),
        (replace(issued), authority, path),
        (issued, other_authority, path),
        (issued, authority, other_path),
    ):
        with pytest.raises(TypeError):
            check(
                forged,
                authority=expected_authority,
                xplt_path=expected_path,
                requested_fields=("stress",),
            )

    original_digest = issued.digest_before
    object.__setattr__(issued, "digest_before", "forged")
    try:
        with pytest.raises(TypeError):
            check(
                issued,
                authority=authority,
                xplt_path=path,
                requested_fields=("stress",),
            )
    finally:
        object.__setattr__(issued, "digest_before", original_digest)


def test_validation_values_are_deep_frozen_against_adapter_mutation(
    tmp_path: Path,
) -> None:
    nested = {"component": [1.0]}
    authority, path = make_authority(tmp_path, MappingAdapter({"stress": nested}))
    issued = validate_requested_fields(authority, path, ("stress",))

    nested["component"].append(2.0)
    assert issued.values["stress"] == {"component": (1.0,)}
    with pytest.raises(TypeError):
        cast(dict[str, object], issued.values["stress"])["component"] = ()


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
        SolverSupervisor(
            spec,  # type: ignore[arg-type]
            fbs_adapter=cast(FbsAdapterAuthority, MappingAdapter()),
        )
    forged = cast(FbsAdapterAuthority, object.__new__(FbsValidation))
    with pytest.raises(TypeError):
        validate_requested_fields(forged, tmp_path / "attempt.xplt", ("stress",))


_NORMAL_LOG = """
FEBio run
===== time step 1 =====
time = 0.5
===== time step 2 =====
time = 1.0
N O R M A L   T E R M I N A T I O N
"""


def _issued_runtime(monkeypatch: pytest.MonkeyPatch) -> FebioRuntimeDiagnostic:
    class ProbeProcess:
        returncode = 0

        def communicate(self, input: bytes, timeout: float) -> tuple[bytes, bytes]:
            del timeout
            assert input == b"quit\n"
            return b"version 4.12.0\n", b""

    with monkeypatch.context() as probe_patch:
        probe_patch.setattr(
            "febio_cae_harness.solver.runtime.subprocess.Popen",
            lambda command, **kwargs: ProbeProcess(),
        )
        return probe_febio(Path(sys.executable))


def _supervisor_with_fbs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[SolverSupervisor, FbsAdapterAuthority]:
    manager = ValidatedCaseWorkspace(tmp_path / "tool", tmp_path / "cae")
    case = manager.create_case("case-a")
    store = EvidenceStore(case, IntentContract())
    store.record_attempt("attempt-a")
    attempt = AttemptWorkspace._from_manager(
        case,
        "attempt-a",
        case.temporary_root / "attempts" / "attempt-a",
    )
    intent = store.issue_intent_snapshot()
    code = (
        "from pathlib import Path; import os; "
        f"Path(os.environ['FEBIO_CAE_HARNESS_LOG']).write_text({_NORMAL_LOG!r}, encoding='utf-8'); "
        "Path(os.environ['FEBIO_CAE_HARNESS_XPLT']).write_bytes(b'synthetic-xplt')"
    )
    input_path = attempt.write_text("input.feb", code)
    runtime = _issued_runtime(monkeypatch)
    capability = headless_module._issue_launch_capability(
        attempt,
        intent,
        runtime,
        input_path,
        expected_steps=2,
        expected_final_time=1.0,
        timeout_seconds=None,
    )
    fbs_manager = FbsAdapterManager(MappingAdapter(), "synthetic-runtime", attempt.root)
    authority = fbs_manager.issue_authority()
    return (
        SolverSupervisor(capability, fbs_adapter=authority, requested_fields=("stress",)),
        authority,
    )


@pytest.mark.parametrize("failure", ["result", "guard", "release"])
def test_late_completion_failure_rolls_back_exact_fbs_and_result_issuance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    supervisor, authority = _supervisor_with_fbs(tmp_path, monkeypatch)
    supervisor.start()
    captured: list[SolverRunResult] = []
    original_register = supervisor._register_result

    def capture_register(result: SolverRunResult) -> None:
        captured.append(result)
        if failure == "result":
            raise RuntimeError("synthetic late result registration failure")
        original_register(result)

    monkeypatch.setattr(supervisor, "_register_result", capture_register)

    if failure == "guard":
        original_guard = supervisor._revalidate_launch_binding

        def final_guard() -> None:
            original_guard()
            if captured:
                raise RuntimeError("synthetic final guard failure")

        monkeypatch.setattr(supervisor, "_revalidate_launch_binding", final_guard)
    elif failure == "release":
        process_authority = supervisor._process_authority
        assert process_authority is not None
        monkeypatch.setattr(
            process_authority,
            "drain",
            Mock(side_effect=ProcessAuthorityError("synthetic release failure")),
        )

    with pytest.raises((RuntimeError, SolverOwnershipError)):
        supervisor.wait()

    assert captured
    result = captured[0]
    validation = result.fbs_validation
    assert validation is not None
    assert validation.authority is not None
    with pytest.raises(TypeError):
        fbs_module._require_issued_validation(
            validation,
            validation.authority,
            result.xplt_path,
            validation.requested_fields,
        )
    assert not hasattr(fbs_module, "_VALIDATION_REGISTRY")
    assert not hasattr(supervisor_module, "_RESULT_REGISTRY")
    assert supervisor.result is None
    assert supervisor.state is SolverState.FAILED


def test_validation_rollback_does_not_unregister_foreign_entry(tmp_path: Path) -> None:
    authority, path = make_authority(tmp_path)
    issued = validate_requested_fields(authority, path, ("stress",))
    foreign = replace(issued)

    assert not fbs_module._unregister_validation(foreign)
    assert fbs_module._unregister_validation(issued)
    assert not fbs_module._unregister_validation(issued)
