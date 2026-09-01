from __future__ import annotations

import contextlib
import copy
import ctypes
import importlib
import math
import os
import stat
import sys
from collections.abc import Sequence
from ctypes import wintypes
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


def _set_windows_close_protection(value: int, protected: bool) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    set_handle_information = kernel32.SetHandleInformation
    set_handle_information.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    set_handle_information.restype = wintypes.BOOL
    flag = 0x00000002  # HANDLE_FLAG_PROTECT_FROM_CLOSE
    ctypes.set_last_error(0)
    if not set_handle_information(
        wintypes.HANDLE(value),
        wintypes.DWORD(flag),
        wintypes.DWORD(flag if protected else 0),
    ):
        raise ctypes.WinError(ctypes.get_last_error())


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


@pytest.mark.skipif(os.name != "nt", reason="Windows filesystem sharing authority")
def test_windows_validation_blocks_byte_identical_xplt_replacement(
    tmp_path: Path,
) -> None:
    root = tmp_path / "attempt"
    root.mkdir()
    path = root / "attempt.xplt"
    original = b"synthetic-xplt"
    path.write_bytes(original)
    original_identity = (path.stat().st_dev, path.stat().st_ino)
    attempts: list[str] = []

    class ReplacingAdapter:
        def read_fields(self, _path: Path, fields: Sequence[str]) -> dict[str, object]:
            replacement = root / "replacement.xplt"
            replacement.write_bytes(original)
            try:
                os.replace(replacement, path)
            except OSError:
                attempts.append("blocked")
            else:
                attempts.append("replaced")
            return {field: 1.0 for field in fields}

    manager = FbsAdapterManager(ReplacingAdapter(), "synthetic-runtime", root)
    authority = manager.issue_authority()
    result = validate_requested_fields(authority, path, ("stress",))

    assert attempts == ["blocked"] or not result.valid
    if attempts == ["blocked"]:
        assert (path.stat().st_dev, path.stat().st_ino) == original_identity
    else:
        assert (path.stat().st_dev, path.stat().st_ino) != original_identity
    close = getattr(manager, "close", None)
    if callable(close):
        close()


@pytest.mark.parametrize("rename_target", ["root", "ancestor"])
@pytest.mark.skipif(os.name != "nt", reason="Windows filesystem sharing authority")
def test_windows_validation_blocks_or_rejects_root_and_ancestor_substitution(
    tmp_path: Path,
    rename_target: str,
) -> None:
    container = tmp_path / "container"
    ancestor = container / "ancestor"
    root = ancestor / "attempt"
    root.mkdir(parents=True)
    path = root / "attempt.xplt"
    original = b"synthetic-xplt"
    path.write_bytes(original)
    original_identity = (root.stat().st_dev, root.stat().st_ino)
    moved = container / f"{rename_target}-moved"
    attempts: list[str] = []

    class RenamingAdapter:
        def read_fields(self, _path: Path, fields: Sequence[str]) -> dict[str, object]:
            target = root if rename_target == "root" else ancestor
            try:
                target.rename(moved)
            except OSError:
                attempts.append("blocked")
            else:
                attempts.append("renamed")
                if rename_target == "root":
                    root.mkdir()
                else:
                    ancestor.mkdir()
                    root.mkdir()
                (root / "attempt.xplt").write_bytes(original)
            return {field: 1.0 for field in fields}

    manager = FbsAdapterManager(RenamingAdapter(), "synthetic-runtime", root)
    authority = manager.issue_authority()
    result = validate_requested_fields(authority, path, ("stress",))

    assert attempts == ["blocked"] or not result.valid
    if attempts == ["blocked"]:
        assert (root.stat().st_dev, root.stat().st_ino) == original_identity
    else:
        moved_root = moved if rename_target == "root" else moved / "attempt"
        assert (moved_root.stat().st_dev, moved_root.stat().st_ino) == original_identity

    close = getattr(manager, "close", None)
    if callable(close):
        close()
    if moved.exists():
        if rename_target == "root":
            if root.exists():
                (root / "attempt.xplt").unlink(missing_ok=True)
                root.rmdir()
            moved.rename(root)
        else:
            if ancestor.exists():
                (root / "attempt.xplt").unlink(missing_ok=True)
                root.rmdir()
                ancestor.rmdir()
            moved.rename(ancestor)


@pytest.mark.skipif(os.name != "nt", reason="Windows native handle ownership")
def test_windows_manager_uses_exact_owned_handle_and_close_revokes_authority(
    tmp_path: Path,
) -> None:
    root = tmp_path / "attempt"
    root.mkdir()
    path = root / "attempt.xplt"
    path.write_bytes(b"synthetic-xplt")
    manager = FbsAdapterManager(MappingAdapter(), "synthetic-runtime", root)
    authority = manager.issue_authority()
    binding = object.__getattribute__(manager, "_record").root_binding
    assert binding is not None and binding.fd is not None
    owner = binding.fd
    assert not isinstance(owner, int)
    assert validate_requested_fields(authority, path, ("stress",)).valid

    manager.close()
    manager.close()
    assert getattr(owner, "closed", False)
    with pytest.raises(TypeError):
        manager.issue_authority()
    with pytest.raises(TypeError):
        validate_requested_fields(authority, path, ("stress",))


@pytest.mark.skipif(os.name != "nt", reason="Windows native handle ownership")
def test_windows_premature_raw_close_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "attempt"
    root.mkdir()
    manager = FbsAdapterManager(MappingAdapter(), "synthetic-runtime", root)
    binding = object.__getattribute__(manager, "_record").root_binding
    assert binding is not None and binding.fd is not None
    owner = binding.fd
    value = owner.value
    assert value is not None

    try:
        with pytest.raises(OSError):
            fbs_module._windows_close_raw(value)
        assert not owner.closed
        assert fbs_module._windows_file_info(value)[1:3] == (owner.device, owner.inode)
    finally:
        manager.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows native handle ownership")
def test_windows_same_object_same_value_reuse_never_closes_foreign_handle(
    tmp_path: Path,
) -> None:
    root = tmp_path / "attempt"
    root.mkdir()
    manager = FbsAdapterManager(MappingAdapter(), "synthetic-runtime", root)
    binding = object.__getattribute__(manager, "_record").root_binding
    assert binding is not None and binding.fd is not None
    root_owner = binding.fd
    owned = fbs_module._windows_duplicate(root_owner)
    owned_value = owned.value
    assert owned_value is not None

    foreign: fbs_module._OwnedHandle | None = None
    try:
        _set_windows_close_protection(owned_value, False)
        fbs_module._windows_close_raw(owned_value)
        for _ in range(256):
            candidate = fbs_module._windows_open_absolute(
                root,
                fbs_module._WINDOWS_FILE_LIST_DIRECTORY
                | fbs_module._WINDOWS_FILE_READ_ATTRIBUTES
                | fbs_module._WINDOWS_READ_CONTROL
                | fbs_module._WINDOWS_SYNCHRONIZE,
                fbs_module._WINDOWS_FILE_SHARE_READ | fbs_module._WINDOWS_FILE_SHARE_WRITE,
                fbs_module._WINDOWS_FILE_FLAG_BACKUP_SEMANTICS
                | fbs_module._WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT,
            )
            if candidate.value == owned_value:
                foreign = candidate
                break
            fbs_module._close_owned_handle(candidate)
        assert foreign is not None, "Windows did not reuse the native handle value"
        assert fbs_module._windows_handle_flags(owned_value) & 0x00000002

        with pytest.raises(ValueError, match="refusing close"):
            fbs_module._close_owned_handle(owned)
        assert fbs_module._windows_file_info(owned_value)[1:3] == (
            root_owner.device,
            root_owner.inode,
        )
    finally:
        if foreign is not None and not foreign.closed and foreign.value is not None:
            with contextlib.suppress(OSError):
                _set_windows_close_protection(foreign.value, False)
            with contextlib.suppress(OSError):
                fbs_module._windows_close_raw(foreign.value)
            fbs_module._retire_owned_handle(foreign)
        if not owned.closed and owned.value is not None:
            with contextlib.suppress(OSError):
                _set_windows_close_protection(owned.value, False)
            with contextlib.suppress(OSError):
                fbs_module._windows_close_raw(owned.value)
            fbs_module._retire_owned_handle(owned)
        manager.close()


def test_descriptor_generation_reuse_never_closes_current_foreign_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "synthetic.xplt"
    stale = fbs_module._OwnedHandle(37, 11, 23, path, False)
    foreign = fbs_module._OwnedHandle(37, 11, 23, path, False)
    metadata = Mock(st_dev=11, st_ino=23)
    close = Mock()
    monkeypatch.setattr(os, "fstat", lambda _value: metadata)
    monkeypatch.setattr(os, "close", close)

    try:
        with pytest.raises(ValueError, match="stale|ownership|refusing"):
            fbs_module._close_owned_handle(stale)
        assert stale.closed and stale.value is None
        assert not foreign.closed and foreign.value == 37
        close.assert_not_called()
    finally:
        fbs_module._retire_owned_handle(stale)
        fbs_module._retire_owned_handle(foreign)


@pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor reuse")
def test_posix_descriptor_reuse_never_closes_current_foreign_owner(tmp_path: Path) -> None:
    path = tmp_path / "same-object.xplt"
    path.write_bytes(b"synthetic-xplt")
    first_fd = os.open(os.fspath(path), os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    metadata = os.fstat(first_fd)
    stale = fbs_module._OwnedHandle(
        first_fd,
        int(metadata.st_dev),
        int(metadata.st_ino),
        path,
        False,
    )
    os.close(first_fd)
    foreign_fd: int | None = None
    foreign: fbs_module._OwnedHandle | None = None
    try:
        for _ in range(256):
            candidate_fd = os.open(os.fspath(path), os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
            if candidate_fd == first_fd:
                foreign_fd = candidate_fd
                break
            os.close(candidate_fd)
        assert foreign_fd is not None, "POSIX did not reuse the descriptor value"
        foreign = fbs_module._OwnedHandle(
            foreign_fd,
            int(metadata.st_dev),
            int(metadata.st_ino),
            path,
            False,
        )

        with pytest.raises(ValueError, match="stale|ownership|refusing"):
            fbs_module._close_owned_handle(stale)
        assert os.fstat(foreign_fd).st_ino == metadata.st_ino
    finally:
        fbs_module._retire_owned_handle(stale)
        if foreign is not None:
            fbs_module._close_owned_handle(foreign)
        elif foreign_fd is not None:
            with contextlib.suppress(OSError):
                os.close(foreign_fd)


def test_root_binding_rejects_path_identity_change_even_with_held_fd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "attempt"
    path.mkdir()
    owner = fbs_module._OwnedHandle(41, 11, 23, path, False)
    binding = fbs_module._RootBinding(path, owner, 11, 23)
    held_metadata = Mock(st_mode=stat.S_IFDIR, st_dev=11, st_ino=23)
    replaced_path_metadata = Mock(st_mode=stat.S_IFDIR, st_dev=31, st_ino=47)
    monkeypatch.setattr(os, "fstat", lambda _value: held_metadata)
    monkeypatch.setattr(
        os,
        "stat",
        lambda _candidate, follow_symlinks=False: replaced_path_metadata,
    )

    try:
        with pytest.raises(TypeError, match="changed"):
            fbs_module._verify_root(binding)
    finally:
        fbs_module._retire_owned_handle(owner)


@pytest.mark.parametrize("rename_target", ["root", "ancestor"])
@pytest.mark.skipif(os.name != "posix", reason="POSIX pathname binding")
def test_posix_validation_rejects_root_or_ancestor_namespace_replacement(
    tmp_path: Path,
    rename_target: str,
) -> None:
    container = tmp_path / "container"
    ancestor = container / "ancestor"
    root = ancestor / "attempt"
    root.mkdir(parents=True)
    path = root / "attempt.xplt"
    path.write_bytes(b"synthetic-xplt")
    moved = container / f"{rename_target}-moved"
    attempts: list[str] = []

    class RenamingAdapter:
        def read_fields(self, _path: Path, fields: Sequence[str]) -> dict[str, object]:
            target = root if rename_target == "root" else ancestor
            target.rename(moved)
            attempts.append("renamed")
            if rename_target == "root":
                root.mkdir()
            else:
                ancestor.mkdir()
                root.mkdir()
            (root / "attempt.xplt").write_bytes(b"synthetic-xplt")
            return {field: 1.0 for field in fields}

    manager = FbsAdapterManager(RenamingAdapter(), "synthetic-runtime", root)
    authority = manager.issue_authority()
    try:
        result = validate_requested_fields(authority, path, ("stress",))
        assert attempts == ["renamed"]
        assert not result.valid
        assert any("root" in issue.lower() or "changed" in issue.lower() for issue in result.issues)
    finally:
        manager.close()
        if moved.exists():
            if rename_target == "root":
                if root.exists():
                    (root / "attempt.xplt").unlink(missing_ok=True)
                    root.rmdir()
                moved.rename(root)
            else:
                if ancestor.exists():
                    (root / "attempt.xplt").unlink(missing_ok=True)
                    root.rmdir()
                    ancestor.rmdir()
                moved.rename(ancestor)


def test_validation_rechecks_opened_namespace_after_digest_before_issuance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "attempt.xplt"
    path.write_bytes(b"synthetic-xplt")
    manager = FbsAdapterManager(MappingAdapter(), "synthetic-runtime", tmp_path)
    authority = manager.issue_authority()
    events: list[str] = []

    def record_verify(_opened: object) -> None:
        events.append("verify")

    def record_digest(_opened: object) -> str:
        events.append("digest")
        return "0" * 64

    monkeypatch.setattr(fbs_module, "_verify_opened_xplt", record_verify)
    monkeypatch.setattr(fbs_module, "_digest_fd", record_digest)
    try:
        result = validate_requested_fields(authority, path, ("stress",))
        expected_before = (
            ("verify", "digest", "verify", "digest")
            if os.name == "nt"
            else ("digest", "verify", "digest")
        )
        assert result.valid
        assert tuple(events) == expected_before + ("verify",)
    finally:
        manager.close()


@pytest.mark.skipif(os.name != "posix", reason="POSIX pathname binding")
def test_posix_namespace_replacement_during_digest_invalidates_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = tmp_path / "container"
    ancestor = container / "ancestor"
    root = ancestor / "attempt"
    root.mkdir(parents=True)
    path = root / "attempt.xplt"
    path.write_bytes(b"synthetic-xplt")
    moved = container / "attempt-moved"
    manager = FbsAdapterManager(MappingAdapter(), "synthetic-runtime", root)
    authority = manager.issue_authority()
    original_digest = fbs_module._digest_fd
    digest_calls = 0

    def digest_then_replace(owner: fbs_module._OwnedHandle) -> str:
        nonlocal digest_calls
        digest = original_digest(owner)
        digest_calls += 1
        if digest_calls == 2:
            root.rename(moved)
            root.mkdir()
            (root / "attempt.xplt").write_bytes(b"synthetic-xplt")
        return digest

    monkeypatch.setattr(fbs_module, "_digest_fd", digest_then_replace)
    try:
        result = validate_requested_fields(authority, path, ("stress",))
        assert digest_calls == 2
        assert not result.valid
    finally:
        manager.close()
        if moved.exists():
            if root.exists():
                (root / "attempt.xplt").unlink(missing_ok=True)
                root.rmdir()
            moved.rename(root)


@pytest.mark.skipif(os.name != "nt", reason="Windows native handle ownership")
def test_windows_protected_acquisition_failure_closes_without_identity_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "attempt"
    root.mkdir()
    original_protect = fbs_module._windows_set_close_protection
    original_close = fbs_module._windows_close_raw
    original_best_effort = fbs_module._best_effort_close
    captured: list[fbs_module._OwnedHandle] = []
    captured_values: list[int | None] = []

    def capture_best_effort(owner: fbs_module._OwnedHandle) -> None:
        captured.append(owner)
        captured_values.append(owner.value)
        original_best_effort(owner)

    metadata = Mock(side_effect=OSError(1234, "synthetic metadata failure"))
    monkeypatch.setattr(fbs_module, "_best_effort_close", capture_best_effort)
    monkeypatch.setattr(fbs_module, "_windows_file_info", metadata)

    try:
        with pytest.raises(OSError, match="synthetic metadata failure"):
            fbs_module._windows_open_absolute(
                root,
                fbs_module._WINDOWS_FILE_LIST_DIRECTORY
                | fbs_module._WINDOWS_FILE_READ_ATTRIBUTES
                | fbs_module._WINDOWS_READ_CONTROL
                | fbs_module._WINDOWS_SYNCHRONIZE,
                fbs_module._WINDOWS_FILE_SHARE_READ | fbs_module._WINDOWS_FILE_SHARE_WRITE,
                fbs_module._WINDOWS_FILE_FLAG_BACKUP_SEMANTICS
                | fbs_module._WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT,
            )
        assert len(captured) == 1
        owner = captured[0]
        value = captured_values[0]
        assert value is not None
        assert owner.identity_known is False

        live_flags: int | None = None
        with contextlib.suppress(OSError):
            live_flags = fbs_module._windows_handle_flags(value)
        pending = any(candidate is owner for candidate in fbs_module._PENDING_CLEANUP)
        assert not (
            live_flags is not None
            or not owner.closed
            or owner.value is not None
            or pending
            or metadata.call_count != 1
        ), (
            "protected acquisition cleanup retained a live owner: "
            f"value={value}, flags={live_flags}, closed={owner.closed}, "
            f"owner_value={owner.value}, pending={pending}, "
            f"metadata_calls={metadata.call_count}"
        )
    finally:
        if captured and not captured[0].closed and captured[0].value is not None:
            value = captured[0].value
            original_protect(value, False)
            original_close(value)
            fbs_module._retire_owned_handle(captured[0])


@pytest.mark.skipif(os.name != "nt", reason="Windows native handle ownership")
def test_windows_close_retry_reprotects_exact_owner_after_reprotect_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "attempt"
    root.mkdir()
    manager = FbsAdapterManager(MappingAdapter(), "synthetic-runtime", root)
    binding = object.__getattribute__(manager, "_record").root_binding
    assert binding is not None and binding.fd is not None
    owner = binding.fd
    value = owner.value
    assert value is not None
    original_close = fbs_module._windows_close_raw
    original_protect = fbs_module._windows_set_close_protection
    close_values: list[int] = []
    protection_calls: list[tuple[int, bool]] = []
    reprotect_failed = False

    def close_once(value_to_close: int) -> None:
        close_values.append(value_to_close)
        if len(close_values) == 1:
            raise OSError(32, "synthetic sharing violation")
        original_close(value_to_close)

    def fail_first_reprotect(value_to_protect: int, protected: bool) -> None:
        nonlocal reprotect_failed
        protection_calls.append((value_to_protect, protected))
        if protected and not reprotect_failed:
            reprotect_failed = True
            raise OSError(32, "synthetic re-protect failure")
        original_protect(value_to_protect, protected)

    monkeypatch.setattr(fbs_module, "_windows_close_raw", close_once)
    monkeypatch.setattr(fbs_module, "_windows_set_close_protection", fail_first_reprotect)

    try:
        with pytest.raises(OSError, match="synthetic sharing violation"):
            manager.close()
        assert owner.value == value and not owner.closed
        assert not owner.windows_protected
        assert owner.windows_unprotected_for_close
        assert any(candidate is owner for candidate in fbs_module._PENDING_CLEANUP)

        fbs_module._drain_pending_cleanup()

        assert owner.closed and owner.value is None
        assert not any(candidate is owner for candidate in fbs_module._PENDING_CLEANUP)
        assert reprotect_failed
        assert close_values == [value, value]
        assert all(call_value == value for call_value, _ in protection_calls)
        assert protection_calls == [
            (value, False),
            (value, True),
            (value, True),
            (value, False),
        ]
    finally:
        monkeypatch.setattr(fbs_module, "_windows_close_raw", original_close)
        monkeypatch.setattr(fbs_module, "_windows_set_close_protection", original_protect)
        if not owner.closed and owner.value is not None:
            original_protect(owner.value, False)
            original_close(owner.value)
            fbs_module._retire_owned_handle(owner)
        manager.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows native handle ownership")
def test_windows_unprotected_retry_never_closes_reused_protected_foreign_handle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "attempt"
    root.mkdir()
    manager = FbsAdapterManager(MappingAdapter(), "synthetic-runtime", root)
    binding = object.__getattribute__(manager, "_record").root_binding
    assert binding is not None and binding.fd is not None
    owner = fbs_module._windows_duplicate(binding.fd)
    value = owner.value
    assert value is not None
    original_close = fbs_module._windows_close_raw
    original_protect = fbs_module._windows_set_close_protection
    close_failed = False
    reprotect_failed = False

    def fail_close_once(value_to_close: int) -> None:
        nonlocal close_failed
        if not close_failed:
            close_failed = True
            raise OSError(32, "synthetic sharing violation")
        original_close(value_to_close)

    def fail_reprotect_once(value_to_protect: int, protected: bool) -> None:
        nonlocal reprotect_failed
        if protected and not reprotect_failed:
            reprotect_failed = True
            raise OSError(32, "synthetic re-protect failure")
        original_protect(value_to_protect, protected)

    monkeypatch.setattr(fbs_module, "_windows_close_raw", fail_close_once)
    monkeypatch.setattr(fbs_module, "_windows_set_close_protection", fail_reprotect_once)
    foreign: fbs_module._OwnedHandle | None = None
    try:
        with pytest.raises(OSError, match="sharing violation"):
            fbs_module._close_owned_handle(owner)
        assert owner.value == value and owner.windows_unprotected_for_close
        assert not owner.windows_protected and reprotect_failed

        original_close(value)
        for _ in range(256):
            candidate = fbs_module._windows_open_absolute(
                root,
                fbs_module._WINDOWS_FILE_LIST_DIRECTORY
                | fbs_module._WINDOWS_FILE_READ_ATTRIBUTES
                | fbs_module._WINDOWS_READ_CONTROL
                | fbs_module._WINDOWS_SYNCHRONIZE,
                fbs_module._WINDOWS_FILE_SHARE_READ | fbs_module._WINDOWS_FILE_SHARE_WRITE,
                fbs_module._WINDOWS_FILE_FLAG_BACKUP_SEMANTICS
                | fbs_module._WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT,
            )
            if candidate.value == value:
                foreign = candidate
                break
            fbs_module._close_owned_handle(candidate)
        assert foreign is not None, "Windows did not reuse the native handle value"

        with pytest.raises(ValueError, match="stale|refusing"):
            fbs_module._close_owned_handle(owner)
        assert not foreign.closed and foreign.value == value
        assert (
            fbs_module._windows_handle_flags(value)
            & fbs_module._WINDOWS_HANDLE_FLAG_PROTECT_FROM_CLOSE
        )
        assert fbs_module._windows_file_info(value)[1:3] == (owner.device, owner.inode)
    finally:
        if foreign is not None and not foreign.closed and foreign.value is not None:
            with contextlib.suppress(OSError):
                original_protect(foreign.value, False)
            with contextlib.suppress(OSError):
                original_close(foreign.value)
            fbs_module._retire_owned_handle(foreign)
        if not owner.closed and owner.value is not None:
            with contextlib.suppress(OSError):
                original_protect(owner.value, False)
            with contextlib.suppress(OSError):
                original_close(owner.value)
            fbs_module._retire_owned_handle(owner)
        manager.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows native handle ownership")
def test_windows_close_failure_retains_exact_owner_for_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "attempt"
    root.mkdir()
    manager = FbsAdapterManager(MappingAdapter(), "synthetic-runtime", root)
    binding = object.__getattribute__(manager, "_record").root_binding
    assert binding is not None and binding.fd is not None
    owner = binding.fd
    original_value = owner.value
    original_close = fbs_module._windows_close_raw
    monkeypatch.setattr(
        fbs_module,
        "_windows_close_raw",
        Mock(side_effect=OSError(32, "synthetic sharing violation")),
    )

    with pytest.raises(OSError):
        manager.close()
    assert owner.value == original_value and not owner.closed
    assert any(candidate is owner for candidate in fbs_module._PENDING_CLEANUP)

    monkeypatch.setattr(fbs_module, "_windows_close_raw", original_close)
    manager.close()
    assert owner.closed and owner.value is None


@pytest.mark.skipif(os.name != "nt", reason="Windows native handle ownership")
def test_windows_finalizer_requeues_pending_owner_until_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "attempt"
    root.mkdir()
    manager = FbsAdapterManager(MappingAdapter(), "synthetic-runtime", root)
    binding = object.__getattribute__(manager, "_record").root_binding
    assert binding is not None and binding.fd is not None
    owner = binding.fd
    original_value = owner.value
    original_close = fbs_module._windows_close_raw
    monkeypatch.setattr(
        fbs_module,
        "_windows_close_raw",
        Mock(side_effect=OSError(32, "synthetic sharing violation")),
    )

    fbs_module._finalize_root_binding(binding)
    assert owner.value == original_value and not owner.closed
    assert any(candidate is owner for candidate in fbs_module._PENDING_CLEANUP)

    monkeypatch.setattr(fbs_module, "_windows_close_raw", original_close)
    fbs_module._drain_pending_cleanup()
    assert owner.closed and owner.value is None
    assert not any(candidate is owner for candidate in fbs_module._PENDING_CLEANUP)
    manager.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows native handle ownership")
def test_windows_close_refuses_reused_native_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "attempt"
    root.mkdir()
    manager = FbsAdapterManager(MappingAdapter(), "synthetic-runtime", root)
    binding = object.__getattribute__(manager, "_record").root_binding
    assert binding is not None and binding.fd is not None
    owner = binding.fd
    close_raw = Mock()
    original_info = fbs_module._windows_file_info
    monkeypatch.setattr(
        fbs_module,
        "_windows_file_info",
        lambda _value: (owner.attributes, owner.device + 1, owner.inode, 1),
    )
    monkeypatch.setattr(fbs_module, "_windows_close_raw", close_raw)

    with pytest.raises(ValueError, match="refusing close"):
        fbs_module._close_owned_handle(owner)
    assert close_raw.call_count == 0
    assert owner.closed and owner.value is None
    monkeypatch.setattr(fbs_module, "_windows_file_info", original_info)
    manager.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows native handle ownership")
def test_manager_context_close_revokes_existing_authority(tmp_path: Path) -> None:
    root = tmp_path / "attempt"
    root.mkdir()
    path = root / "attempt.xplt"
    path.write_bytes(b"synthetic-xplt")
    with FbsAdapterManager(MappingAdapter(), "synthetic-runtime", root) as manager:
        authority = manager.issue_authority()
        assert validate_requested_fields(authority, path, ("stress",)).valid
    with pytest.raises(TypeError):
        validate_requested_fields(authority, path, ("stress",))


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
    assert not result.valid
    assert result.digest_before != result.digest_after or any(
        "adapter execution failed" in issue for issue in result.issues
    )
    assert any("mutated" in issue or "adapter execution failed" in issue for issue in result.issues)


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
        validate_requested_fields = Mock(wraps=supervisor_module.validate_requested_fields)
        monkeypatch.setattr(
            supervisor_module,
            "validate_requested_fields",
            validate_requested_fields,
        )
        monkeypatch.setattr(
            process_authority,
            "drain",
            Mock(side_effect=ProcessAuthorityError("synthetic release failure")),
        )

    with pytest.raises((RuntimeError, SolverOwnershipError)):
        supervisor.wait()

    if failure == "release":
        assert not captured
        validate_requested_fields.assert_not_called()
        assert supervisor.result is None
        assert supervisor._result_latch is None
        assert supervisor._result_issuance is None
        assert supervisor.state is SolverState.FAILED
        return

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
