"""Synthetic durability tests for the owned solver supervisor."""

from __future__ import annotations

import contextlib
import gc
import hashlib
import inspect
import json
import os
import subprocess
import sys
import weakref
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest

import febio_cae_harness.solver.runtime as runtime_module
import febio_cae_harness.solver.supervisor as supervisor_module
from febio_cae_harness.contracts import IntentContract
from febio_cae_harness.evidence import EvidenceStore
from febio_cae_harness.solver import headless as headless_module
from febio_cae_harness.solver.process_authority import ProcessAuthority, ProcessAuthorityError
from febio_cae_harness.solver.runtime import (
    FebioRuntimeDiagnostic,
    _acquire_runtime_launch_claim,
    probe_febio,
)
from febio_cae_harness.solver.supervisor import SolverSupervisor
from febio_cae_harness.solver.types import (
    SolverConfigurationError,
    SolverLaunchCapability,
    SolverLaunchError,
    SolverLaunchSpec,
    SolverOwnershipError,
    SolverState,
)
from febio_cae_harness.workspace import AttemptWorkspace, ValidatedCaseWorkspace


def _spec(tmp_path: Path) -> SolverLaunchSpec:
    input_path = tmp_path / "input.feb"
    input_path.write_text("synthetic", encoding="utf-8")
    return SolverLaunchSpec(
        executable=Path(sys.executable),
        input_path=input_path,
        attempt_root=tmp_path / "attempt",
        arguments=("-c", "import time; time.sleep(30)"),
    )


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


def _capability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    code: str,
) -> SolverLaunchCapability:
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
    input_path = attempt.write_text("input.feb", code)
    runtime = _issued_runtime(monkeypatch)
    return headless_module._issue_launch_capability(
        attempt,
        intent,
        runtime,
        input_path,
        expected_steps=None,
        expected_final_time=None,
        timeout_seconds=None,
    )


def _windows_process_handle_count() -> int:
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetProcessHandleCount.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    kernel32.GetProcessHandleCount.restype = ctypes.c_int
    count = ctypes.c_uint32()
    if not kernel32.GetProcessHandleCount(kernel32.GetCurrentProcess(), ctypes.byref(count)):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(count.value)


def _assert_windows_record_mutation_blocked(record_path: Path) -> Path:
    try:
        foreign_fd = os.open(os.fspath(record_path), os.O_WRONLY)
    except OSError:
        pass
    else:
        os.close(foreign_fd)
        pytest.fail("foreign write opened the owned process record")

    foreign_path = record_path.with_name(f"{record_path.name}.foreign")
    foreign_path.write_bytes(b'{"foreign": true}')
    try:
        os.replace(os.fspath(foreign_path), os.fspath(record_path))
    except OSError:
        pass
    else:
        pytest.fail("foreign replacement changed the owned process record")
    try:
        os.unlink(os.fspath(record_path))
    except OSError:
        pass
    else:
        pytest.fail("foreign deletion removed the owned process record")
    return foreign_path


def test_start_persists_attempt_owned_process_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capability = _capability(tmp_path, monkeypatch, code="import time; time.sleep(30)")
    supervisor = SolverSupervisor(capability)
    spec = capability.spec
    intent = object.__getattribute__(capability, "_intent_snapshot")

    supervisor.start()
    try:
        record_path = supervisor.process_record_path
        assert record_path == spec.attempt_root / "process.json"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        assert record["case_id"] == "case-a"
        assert record["intent_id"] == intent.intent_sha256
        assert record["attempt_id"] == "attempt-a"
        assert record["executable_path"] == str(spec.executable)
        assert record["pid"] == supervisor.pid
        assert record["process_creation_identity"]
        assert record["start_time"]
        assert record["owned_output_paths"] == {
            "log": str(spec.expected_outputs.log_path),
            "xplt": str(spec.expected_outputs.xplt_path),
        }
        context = record["launch_context"]
        assert isinstance(context, dict)
        assert context["input_path"] == os.path.normcase(
            os.path.realpath(os.path.abspath(os.fspath(spec.input_path)))
        )
        assert context["input_sha256"] == hashlib.sha256(spec.input_path.read_bytes()).hexdigest()
        assert context["runtime_path"] == os.path.normcase(
            os.path.realpath(os.path.abspath(os.fspath(spec.executable)))
        )
        runtime = object.__getattribute__(capability, "_runtime_diagnostic")
        assert context["runtime_sha256"] == runtime.sha256
        assert context["runtime_version"] == runtime.version
        assert context["argv"] == list(spec.command)
        assert context["cwd"] == os.path.normcase(
            os.path.realpath(os.path.abspath(os.fspath(spec.cwd)))
        )
        assert context["output_paths"] == {
            "log": os.path.normcase(
                os.path.realpath(os.path.abspath(os.fspath(spec.expected_outputs.log_path)))
            ),
            "xplt": os.path.normcase(
                os.path.realpath(os.path.abspath(os.fspath(spec.expected_outputs.xplt_path)))
            ),
        }
        assert context["timeout_seconds"] is None
        assert context["expected_steps"] is None
        assert context["expected_final_time"] is None
        assert context["requested_fields"] == []
        assert isinstance(context["environment_digest"], str)
        assert record["launch_context_digest"]
        process_authority = record["process_authority"]
        assert isinstance(process_authority, dict)
        assert process_authority["context_digest"] == record["launch_context_digest"]
        assert process_authority["root_pid"] == record["pid"]
        assert process_authority["root_creation_identity"] == record["process_creation_identity"]
        assert "FEBIO_CAE_HARNESS_AUTHORITY_CONTEXT" not in record_path.read_text(encoding="utf-8")
    finally:
        supervisor.cancel()

    assert supervisor.state is SolverState.CANCELLED


def test_raw_launch_spec_is_rejected_before_popen_or_attempt_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path = tmp_path / "model.feb"
    attempt_root = tmp_path / "caller-selected-attempt"
    input_path.write_text("synthetic", encoding="utf-8")
    spec = SolverLaunchSpec(
        executable=Path(sys.executable),
        input_path=input_path,
        attempt_root=attempt_root,
    )

    def unexpected_popen(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("raw launch spec reached Popen")

    monkeypatch.setattr(subprocess, "Popen", unexpected_popen)
    with pytest.raises(SolverConfigurationError, match="capability"):
        SolverSupervisor(spec)  # type: ignore[arg-type]

    assert not attempt_root.exists()
    assert not (attempt_root / "process.json").exists()


def test_forged_fully_populated_launch_capability_is_rejected_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path = tmp_path / "model.feb"
    attempt_root = tmp_path / "forged-attempt"
    input_path.write_text("synthetic", encoding="utf-8")
    spec = SolverLaunchSpec(
        executable=Path(sys.executable),
        input_path=input_path,
        attempt_root=attempt_root,
    )
    forged = object.__new__(SolverLaunchCapability)
    object.__setattr__(forged, "_spec", spec)
    object.__setattr__(forged, "_attempt_workspace", object())
    object.__setattr__(forged, "_intent_snapshot", object())
    object.__setattr__(forged, "_runtime_diagnostic", object())

    def unexpected_popen(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("forged launch capability reached Popen")

    monkeypatch.setattr(subprocess, "Popen", unexpected_popen)
    with pytest.raises(SolverConfigurationError, match="issued|capability"):
        SolverSupervisor(forged)

    assert not attempt_root.exists()
    assert not (attempt_root / "process.json").exists()


def test_start_revalidates_after_prepare_outputs_before_popen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability = _capability(tmp_path, monkeypatch, code="pass")
    attempt = object.__getattribute__(capability, "_attempt_workspace")
    assert type(attempt) is AttemptWorkspace
    original_prepare_outputs = SolverSupervisor._prepare_outputs

    def racing_prepare_outputs(supervisor: SolverSupervisor) -> None:
        original_prepare_outputs(supervisor)
        object.__setattr__(attempt, "attempt_id", "attempt-b")

    monkeypatch.setattr(SolverSupervisor, "_prepare_outputs", racing_prepare_outputs)
    popen_reached = False

    def unexpected_popen(*args: object, **kwargs: object) -> None:
        nonlocal popen_reached
        del args, kwargs
        popen_reached = True
        raise AssertionError("raced launch reached Popen")

    monkeypatch.setattr("febio_cae_harness.solver.supervisor.subprocess.Popen", unexpected_popen)
    supervisor = SolverSupervisor(capability)
    with pytest.raises(SolverConfigurationError, match="authority|binding|live"):
        supervisor.start()

    assert not popen_reached


def test_posix_launch_anchors_preparation_and_child_paths_across_attempt_root_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "posix":
        pytest.skip("POSIX-only launch path anchoring")

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
    attempt_root = Path(os.fspath(attempt))
    code = (
        "from pathlib import Path; import os; "
        "Path(os.environ['FEBIO_CAE_HARNESS_LOG']).write_text('replacement', encoding='utf-8'); "
        "Path(os.environ['FEBIO_CAE_HARNESS_XPLT']).write_bytes(b'replacement')"
    )
    input_path = attempt.write_text("input.feb", code)
    runtime = _issued_runtime(monkeypatch)
    capability = headless_module._issue_launch_capability(
        attempt,
        intent,
        runtime,
        input_path,
        expected_steps=None,
        expected_final_time=None,
        timeout_seconds=None,
    )
    supervisor = SolverSupervisor(capability)
    moved_root = attempt_root.with_name(attempt_root.name + "-moved")
    attempt_root.rename(moved_root)
    replacement_root = attempt_root
    replacement_root.mkdir()

    result = supervisor.run()
    assert result.state is SolverState.NORMAL_EXIT

    assert not (replacement_root / "input.log").exists()
    assert (moved_root / "input.log").read_text(encoding="utf-8") == "replacement"
    assert not (replacement_root / "process.json").exists()
    assert (moved_root / "process.json").is_file()


def test_rollback_process_record_has_no_check_then_path_unlink_window() -> None:
    source = inspect.getsource(SolverSupervisor._rollback_process_record)
    assert "_record_claim_matches" not in source
    assert "claim.path.unlink" not in source


def test_posix_rollback_preserves_replacement_at_former_unlink_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name != "posix":
        pytest.skip("POSIX atomic record transaction")

    capability = _capability(tmp_path, monkeypatch, code="pass")
    supervisor = SolverSupervisor(capability)
    supervisor.start()
    claim = supervisor._process_record_claim
    assert claim is not None
    replaced = False

    original_exchange = supervisor_module._posix_rename_exchange

    def racing_exchange(parent_fd: int, left: str, right: str) -> None:
        nonlocal replaced
        original_exchange(parent_fd, left, right)
        if not replaced and left == claim.name:
            replacement = supervisor.process_record_path.with_name("foreign.json")
            replacement.write_text('{"foreign": true}', encoding="utf-8")
            os.replace(os.fspath(replacement), os.fspath(supervisor.process_record_path))
            replaced = True

    monkeypatch.setattr(supervisor_module, "_posix_rename_exchange", racing_exchange)
    try:
        assert supervisor._rollback_process_record() == ()
        assert replaced
        assert supervisor.process_record_path.read_text(encoding="utf-8") == '{"foreign": true}'
    finally:
        supervisor.cancel()


def test_posix_rollback_uncertainty_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name != "posix":
        pytest.skip("POSIX atomic record transaction")

    capability = _capability(tmp_path, monkeypatch, code="pass")
    supervisor = SolverSupervisor(capability)
    supervisor.start()
    try:
        with monkeypatch.context() as transaction_patch:
            transaction_patch.setattr(
                supervisor_module,
                "_posix_rename_exchange",
                Mock(side_effect=OSError("synthetic exchange uncertainty")),
            )
            failures = supervisor._rollback_process_record()
        assert failures
        assert supervisor.process_record_path.is_file()
    finally:
        supervisor.cancel()
    assert not supervisor.process_record_path.exists()


def test_wait_replays_only_supervisor_latched_result_and_ignores_registry_injection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability = _capability(tmp_path, monkeypatch, code="pass")
    supervisor = SolverSupervisor(capability)
    result = supervisor.run()
    forged = replace(result)
    object.__setattr__(supervisor, "_result", forged)
    monkeypatch.setattr(
        supervisor_module,
        "_RESULT_REGISTRY",
        {id(forged): forged},
        raising=False,
    )
    assert supervisor.wait() is result
    assert supervisor.result is result


def test_windows_directory_authority_blocks_root_and_ancestor_rename(tmp_path: Path) -> None:
    if os.name != "nt":
        pytest.skip("Windows-only directory-handle authority")

    root = tmp_path / "attempt"
    root.mkdir()
    authority = supervisor_module._FilesystemAuthority(root)
    try:
        for target in (root, root.parent):
            with pytest.raises(OSError):
                target.rename(target.with_name(target.name + "-renamed"))
    finally:
        authority.close()

    moved = root.with_name(root.name + "-moved")
    root.rename(moved)
    assert moved.is_dir()


def test_windows_active_supervisor_finalizer_releases_process_record_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "nt":
        pytest.fail("required Windows finalizer transaction test executed on a non-Windows host")

    capability = _capability(tmp_path, monkeypatch, code="import time; time.sleep(30)")
    supervisor = SolverSupervisor(capability)
    process: subprocess.Popen[bytes] | None = None
    process_authority: ProcessAuthority | None = None
    filesystem_authority: supervisor_module._FilesystemAuthority | None = None
    record_fd: int | None = None
    record_path = capability.spec.attempt_root / "process.json"
    record_key = supervisor_module._windows_record_claim_key(record_path)
    record_claim_entry: tuple[int, int, int] | None = None
    record_content = b""
    supervisor_ref: weakref.ReferenceType[SolverSupervisor] | None = None
    original_open = supervisor_module._windows_open_process_record
    original_delete = supervisor_module._windows_delete_process_record
    original_complete = SolverSupervisor._complete
    original_rollback = SolverSupervisor._rollback_process_record
    reopen_calls: list[Path] = []
    terminal_actions: list[str] = []
    delete_calls: list[int] = []

    def track_open(path: Path, *, create: bool, delete_access: bool = True) -> int:
        reopen_calls.append(Path(os.fspath(path)))
        return original_open(path, create=create, delete_access=delete_access)

    def track_delete(fd: int) -> None:
        delete_calls.append(fd)
        original_delete(fd)

    def unexpected_complete(
        instance: SolverSupervisor, state: SolverState, return_code: int | None
    ) -> object:
        terminal_actions.append("complete")
        return original_complete(instance, state, return_code)

    def unexpected_rollback(instance: SolverSupervisor) -> tuple[BaseException, ...]:
        terminal_actions.append("rollback")
        return original_rollback(instance)

    try:
        supervisor.start()
        assert supervisor._process is not None
        process = cast(subprocess.Popen[bytes], supervisor._process)
        process_authority = supervisor._process_authority
        filesystem_authority = supervisor._filesystem_authority
        claim = supervisor._process_record_claim
        assert process_authority is not None
        assert filesystem_authority is not None
        assert claim is not None and claim.handle is not None
        record_fd = claim.handle
        record_claim_entry = (record_fd, claim.device, claim.inode)
        record_path = Path(os.fspath(claim.path))
        record_key = supervisor_module._windows_record_claim_key(record_path)
        record_content = supervisor_module._read_record_fd(record_fd)
        record = json.loads(record_content.decode("utf-8"))
        assert record["state"] == SolverState.RUNNING.value
        assert process.poll() is None
        assert process.returncode is None
        assert supervisor_module._WINDOWS_RECORD_CLAIMS.get(record_key) == [record_claim_entry]
        del claim

        supervisor_ref = weakref.ref(supervisor)
        monkeypatch.setattr(supervisor_module, "_windows_open_process_record", track_open)
        monkeypatch.setattr(supervisor_module, "_windows_delete_process_record", track_delete)
        monkeypatch.setattr(SolverSupervisor, "_complete", unexpected_complete)
        monkeypatch.setattr(SolverSupervisor, "_rollback_process_record", unexpected_rollback)
        del supervisor
        for _ in range(3):
            gc.collect()
            if supervisor_ref() is None:
                break

        assert supervisor_ref() is None
        assert process.poll() is None
        assert process.returncode is None
        process_authority.verify(process.pid)
        with pytest.raises(SolverOwnershipError, match="closed"):
            filesystem_authority.verify()
        with pytest.raises(OSError):
            os.fstat(record_fd)
        assert record_key not in supervisor_module._WINDOWS_RECORD_CLAIMS
        assert record_path.is_file()
        assert record_path.read_bytes() == record_content
        assert json.loads(record_content.decode("utf-8"))["state"] == SolverState.RUNNING.value
        assert delete_calls == []
        assert terminal_actions == []
        assert reopen_calls == []

        reconnect_fd = supervisor_module._windows_open_process_record(record_path, create=False)
        try:
            assert reopen_calls == [record_path]
            assert supervisor_module._read_record_fd(reconnect_fd) == record_content
            supervisor_module._windows_delete_process_record(reconnect_fd)
        finally:
            os.close(reconnect_fd)
        assert not record_path.exists()
    finally:
        if process is not None:
            try:
                if process.poll() is None:
                    try:
                        if process_authority is not None:
                            process_authority.terminate(process.pid)
                        else:
                            process.kill()
                    except BaseException:
                        process.kill()
                process.wait(timeout=5.0)
            finally:
                if process_authority is not None:
                    process_authority.close()

        if record_fd is not None:
            try:
                os.fstat(record_fd)
            except OSError:
                pass
            else:
                if record_path.exists():
                    original_delete(record_fd)
                supervisor_module._windows_unregister_record_claim(record_path, record_fd)
                os.close(record_fd)
        if record_path.exists():
            cleanup_fd = original_open(record_path, create=False)
            try:
                original_delete(cleanup_fd)
            finally:
                os.close(cleanup_fd)
        if filesystem_authority is not None:
            filesystem_authority.close()


def test_windows_active_record_read_validation_failure_retains_exact_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "nt":
        pytest.fail("required Windows active-record validation test executed on a non-Windows host")

    capability = _capability(tmp_path, monkeypatch, code="import time; time.sleep(30)")
    supervisor = SolverSupervisor(capability)
    supervisor.start()
    process = cast(subprocess.Popen[bytes], supervisor._process)
    claim = supervisor._process_record_claim
    assert claim is not None and claim.handle is not None
    original_claim = claim
    original_fd = claim.handle
    original_content = supervisor_module._read_record_fd(original_fd)
    record_path = Path(os.fspath(claim.path))
    record_key = supervisor_module._windows_record_claim_key(record_path)
    record_claim_entry = (original_fd, claim.device, claim.inode)
    assert supervisor_module._WINDOWS_RECORD_CLAIMS.get(record_key) == [record_claim_entry]

    probe_paths: list[Path] = []
    path_reopens: list[Path] = []
    lifecycle_actions: list[str] = []
    original_probe = supervisor_module._windows_probe_process_record
    original_open = supervisor_module._windows_open_process_record

    def fail_second_probe(path: Path) -> int:
        probe_paths.append(Path(os.fspath(path)))
        if len(probe_paths) == 2:
            raise OSError("synthetic second process-record read probe failure")
        return original_probe(path)

    def track_path_open(path: Path, *, create: bool, delete_access: bool = True) -> int:
        path_reopens.append(Path(os.fspath(path)))
        return original_open(path, create=create, delete_access=delete_access)

    def forbidden_complete(
        instance: SolverSupervisor, state: SolverState, return_code: int | None
    ) -> object:
        del instance, state, return_code
        lifecycle_actions.append("complete")
        raise AssertionError("active record read completed the supervisor")

    def forbidden_rollback(instance: SolverSupervisor) -> tuple[BaseException, ...]:
        del instance
        lifecycle_actions.append("rollback")
        raise AssertionError("active record read rolled back the supervisor")

    def forbidden_delete(fd: int) -> None:
        del fd
        lifecycle_actions.append("delete")
        raise AssertionError("active record read deleted the process record")

    def forbidden_terminate(
        instance: SolverSupervisor,
        owned_process: subprocess.Popen[bytes] | supervisor_module._ReconnectedProcess,
    ) -> None:
        del instance, owned_process
        lifecycle_actions.append("terminate")
        raise AssertionError("active record read terminated the solver")

    foreign_path: Path | None = None
    try:
        with monkeypatch.context() as read_patch:
            read_patch.setattr(
                supervisor_module,
                "_windows_probe_process_record",
                fail_second_probe,
            )
            read_patch.setattr(
                supervisor_module,
                "_windows_open_process_record",
                track_path_open,
            )
            read_patch.setattr(SolverSupervisor, "_complete", forbidden_complete)
            read_patch.setattr(
                SolverSupervisor,
                "_rollback_process_record",
                forbidden_rollback,
            )
            read_patch.setattr(
                supervisor_module,
                "_windows_delete_process_record",
                forbidden_delete,
            )
            read_patch.setattr(
                SolverSupervisor,
                "_terminate_owned_process",
                forbidden_terminate,
            )
            with pytest.raises(SolverOwnershipError):
                supervisor._read_process_record()

        assert probe_paths == [record_path, record_path]
        assert path_reopens == []
        assert lifecycle_actions == []
        assert supervisor.state is SolverState.RUNNING
        assert process.poll() is None
        assert process.returncode is None
        assert supervisor.result is None
        retained_claim = supervisor._process_record_claim
        assert retained_claim is original_claim or (
            retained_claim is not None
            and retained_claim.handle == original_fd
            and retained_claim.path == original_claim.path
            and retained_claim.device == original_claim.device
            and retained_claim.inode == original_claim.inode
        )
        assert os.fstat(original_fd).st_size == len(original_content)
        assert supervisor_module._read_record_fd(original_fd) == original_content
        assert supervisor_module._WINDOWS_RECORD_CLAIMS.get(record_key) == [record_claim_entry]
        foreign_path = _assert_windows_record_mutation_blocked(record_path)
    finally:
        try:
            if supervisor.process_id is not None:
                supervisor.cancel()
        finally:
            for path in (foreign_path, record_path):
                if path is not None and path.exists():
                    path.unlink()


def test_windows_active_record_read_reuses_exact_claim_without_rotation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "nt":
        pytest.fail("required Windows active-record reuse test executed on a non-Windows host")

    capability = _capability(tmp_path, monkeypatch, code="import time; time.sleep(30)")
    supervisor = SolverSupervisor(capability)
    supervisor.start()
    process = cast(subprocess.Popen[bytes], supervisor._process)
    claim = supervisor._process_record_claim
    assert claim is not None and claim.handle is not None
    original_claim = claim
    original_fd = claim.handle
    original_content = supervisor_module._read_record_fd(original_fd)
    original_record = json.loads(original_content.decode("utf-8"))
    record_path = Path(os.fspath(claim.path))
    record_key = supervisor_module._windows_record_claim_key(record_path)
    record_claim_entry = (original_fd, claim.device, claim.inode)
    assert supervisor_module._WINDOWS_RECORD_CLAIMS.get(record_key) == [record_claim_entry]
    handle_count_before = _windows_process_handle_count()

    release_fds: list[int] = []
    closed_fds: list[int] = []
    path_reopens: list[Path] = []
    probe_paths: list[Path] = []
    original_release = SolverSupervisor._release_process_record_claim
    original_open = supervisor_module._windows_open_process_record
    original_probe = supervisor_module._windows_probe_process_record
    original_close = os.close

    def track_release(
        instance: SolverSupervisor,
        candidate: supervisor_module._ProcessRecordClaim | None,
    ) -> tuple[BaseException, ...]:
        if candidate is not None and candidate.handle == original_fd:
            release_fds.append(original_fd)
        return original_release(instance, candidate)

    def track_close(fd: int) -> None:
        if fd == original_fd:
            closed_fds.append(fd)
        original_close(fd)

    def track_path_open(path: Path, *, create: bool, delete_access: bool = True) -> int:
        path_reopens.append(Path(os.fspath(path)))
        return original_open(path, create=create, delete_access=delete_access)

    def track_probe(path: Path) -> int:
        probe_paths.append(Path(os.fspath(path)))
        return original_probe(path)

    try:
        with monkeypatch.context() as read_patch:
            read_patch.setattr(SolverSupervisor, "_release_process_record_claim", track_release)
            read_patch.setattr(os, "close", track_close)
            read_patch.setattr(
                supervisor_module,
                "_windows_open_process_record",
                track_path_open,
            )
            read_patch.setattr(supervisor_module, "_windows_probe_process_record", track_probe)
            records = [supervisor._read_process_record() for _ in range(5)]

        assert records == [original_record] * 5
        assert len(probe_paths) == 10
        assert path_reopens == []
        assert release_fds == []
        assert closed_fds == []
        assert _windows_process_handle_count() == handle_count_before
        assert supervisor.state is SolverState.RUNNING
        assert process.poll() is None
        assert process.returncode is None
        assert supervisor.result is None
        retained_claim = supervisor._process_record_claim
        assert retained_claim is original_claim or (
            retained_claim is not None
            and retained_claim.handle == original_fd
            and retained_claim.path == original_claim.path
            and retained_claim.device == original_claim.device
            and retained_claim.inode == original_claim.inode
        )
        assert os.fstat(original_fd).st_size == len(original_content)
        assert supervisor_module._read_record_fd(original_fd) == original_content
        assert supervisor_module._WINDOWS_RECORD_CLAIMS.get(record_key) == [record_claim_entry]
    finally:
        if supervisor.process_id is not None:
            supervisor.cancel()


def test_windows_reconnect_validation_failure_preserves_existing_record_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "nt":
        pytest.fail("required Windows reconnect validation test executed on a non-Windows host")

    capability = _capability(tmp_path, monkeypatch, code="import time; time.sleep(30)")
    original_supervisor = SolverSupervisor(capability)
    original_supervisor.start()
    original_process = cast(subprocess.Popen[bytes], original_supervisor._process)
    original_claim = original_supervisor._process_record_claim
    assert original_claim is not None and original_claim.handle is not None
    original_fd = original_claim.handle
    original_content = supervisor_module._read_record_fd(original_fd)
    record_path = Path(os.fspath(original_claim.path))
    record_key = supervisor_module._windows_record_claim_key(record_path)
    record_claim_entry = (original_fd, original_claim.device, original_claim.inode)
    assert supervisor_module._WINDOWS_RECORD_CLAIMS.get(record_key) == [record_claim_entry]

    candidate_fds: list[int] = []
    original_duplicate = supervisor_module._windows_duplicate_record_claim
    original_identity = SolverSupervisor._record_claim_identity_matches

    def track_duplicate(path: Path) -> int | None:
        duplicate = original_duplicate(path)
        if duplicate is not None:
            candidate_fds.append(duplicate)
        return duplicate

    def fail_candidate_identity(
        instance: SolverSupervisor,
        candidate: supervisor_module._ProcessRecordClaim,
    ) -> bool:
        if instance is not original_supervisor:
            assert candidate.handle in candidate_fds
            return False
        return original_identity(instance, candidate)

    foreign_path: Path | None = None
    try:
        with monkeypatch.context() as reconnect_patch:
            reconnect_patch.setattr(
                supervisor_module,
                "_windows_duplicate_record_claim",
                track_duplicate,
            )
            reconnect_patch.setattr(
                SolverSupervisor,
                "_record_claim_identity_matches",
                fail_candidate_identity,
            )
            with pytest.raises(SolverOwnershipError):
                SolverSupervisor.reconnect(capability)

        assert candidate_fds
        assert len(candidate_fds) == len(set(candidate_fds))
        for candidate_fd in candidate_fds:
            with pytest.raises(OSError):
                os.fstat(candidate_fd)
        assert original_supervisor.state is SolverState.RUNNING
        assert original_process.poll() is None
        assert original_process.returncode is None
        assert original_supervisor.result is None
        assert original_supervisor._process_record_claim is original_claim
        assert os.fstat(original_fd).st_size == len(original_content)
        assert supervisor_module._read_record_fd(original_fd) == original_content
        assert supervisor_module._WINDOWS_RECORD_CLAIMS.get(record_key) == [record_claim_entry]
        foreign_path = _assert_windows_record_mutation_blocked(record_path)
    finally:
        try:
            if original_supervisor.process_id is not None:
                original_supervisor.cancel()
        finally:
            for path in (foreign_path, record_path):
                if path is not None and path.exists():
                    path.unlink()


def test_windows_process_record_lease_blocks_replacement_through_running_transition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "nt":
        pytest.fail("required Windows transaction test executed on a non-Windows host")

    capability = _capability(tmp_path, monkeypatch, code="import time; time.sleep(30)")
    handle_number = 0

    def permissive_directory_open(path: Path) -> supervisor_module._WindowsDirectoryHandle:
        nonlocal handle_number
        handle_number += 1
        return supervisor_module._WindowsDirectoryHandle(0, 0, handle_number)

    monkeypatch.setattr(supervisor_module, "_windows_open_directory", permissive_directory_open)
    monkeypatch.setattr(supervisor_module, "_windows_verify_directory", lambda handle: None)
    monkeypatch.setattr(supervisor_module, "_windows_close_directory", lambda handle: None)
    supervisor = SolverSupervisor(capability)
    original_write = SolverSupervisor._write_process_record
    foreign_write_errors: list[OSError] = []
    foreign_write_succeeded = False
    replacement_errors: list[OSError] = []
    replacement_succeeded = False
    delete_errors: list[OSError] = []
    delete_succeeded = False

    def write_record(instance: SolverSupervisor, record: dict[str, object]) -> None:
        nonlocal delete_succeeded, foreign_write_succeeded, replacement_succeeded
        original_write(instance, record)
        if record.get("state") != "BOUND_SUSPENDED":
            return
        try:
            foreign_fd = os.open(os.fspath(instance.process_record_path), os.O_WRONLY)
        except OSError as error:
            foreign_write_errors.append(error)
        else:
            foreign_write_succeeded = True
            os.close(foreign_fd)
        foreign_path = instance.process_record_path.with_name("foreign.json")
        foreign_path.write_text('{"foreign": true}', encoding="utf-8")
        try:
            os.replace(os.fspath(foreign_path), os.fspath(instance.process_record_path))
        except OSError as error:
            replacement_errors.append(error)
        else:
            replacement_succeeded = True
        try:
            os.unlink(os.fspath(instance.process_record_path))
        except OSError as error:
            delete_errors.append(error)
        else:
            delete_succeeded = True

    monkeypatch.setattr(SolverSupervisor, "_write_process_record", write_record)
    try:
        supervisor.start()
        assert foreign_write_errors
        assert not foreign_write_succeeded
        assert replacement_errors
        assert not replacement_succeeded
        assert delete_errors
        assert not delete_succeeded
        claim = supervisor._process_record_claim
        assert claim is not None and claim.handle is not None
        record = json.loads(supervisor_module._read_record_fd(claim.handle).decode("utf-8"))
        assert record["state"] == SolverState.RUNNING.value
        assert record.get("foreign") is None
    finally:
        if supervisor.process_id is not None:
            supervisor.cancel()


def test_windows_running_record_transition_rejects_lost_directory_entry_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "nt":
        pytest.fail("required Windows transaction test executed on a non-Windows host")

    capability = _capability(tmp_path, monkeypatch, code="import time; time.sleep(30)")
    supervisor = SolverSupervisor(capability)
    original_identity_check = SolverSupervisor._record_claim_identity_matches
    identity_checks = 0
    persisted_states: list[object] = []

    def lost_directory_entry(
        instance: SolverSupervisor, claim: supervisor_module._ProcessRecordClaim
    ) -> bool:
        nonlocal identity_checks
        if claim.state == "BOUND_SUSPENDED":
            identity_checks += 1
            return False
        return original_identity_check(instance, claim)

    original_write = SolverSupervisor._write_process_record

    def track_write(instance: SolverSupervisor, record: dict[str, object]) -> None:
        persisted_states.append(record.get("state"))
        original_write(instance, record)

    monkeypatch.setattr(SolverSupervisor, "_record_claim_identity_matches", lost_directory_entry)
    monkeypatch.setattr(SolverSupervisor, "_write_process_record", track_write)
    try:
        with pytest.raises(SolverOwnershipError, match="record|directory|replaced"):
            supervisor.start()
        assert identity_checks >= 1
        assert SolverState.RUNNING.value not in persisted_states
        assert supervisor.state is SolverState.FAILED
        assert supervisor.process_id is None
        assert not supervisor.process_record_path.exists()
    finally:
        if supervisor.process_id is not None:
            supervisor.cancel()


def test_windows_owned_record_rollback_uses_held_handle_without_path_reopen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "nt":
        pytest.fail("required Windows transaction test executed on a non-Windows host")

    capability = _capability(tmp_path, monkeypatch, code="import time; time.sleep(30)")
    supervisor = SolverSupervisor(capability)
    supervisor.start()
    claim = supervisor._process_record_claim
    assert claim is not None
    original_open = supervisor_module._windows_open_process_record
    reopen_attempts: list[Path] = []

    def reject_path_reopen(path: Path, *, create: bool, delete_access: bool = False) -> int:
        if not create:
            reopen_attempts.append(path)
            raise AssertionError("rollback reopened process record by path")
        return original_open(path, create=create, delete_access=delete_access)

    try:
        with monkeypatch.context() as rollback_patch:
            rollback_patch.setattr(
                supervisor_module,
                "_windows_open_process_record",
                reject_path_reopen,
            )
            failures = supervisor._rollback_process_record()
        assert failures == ()
        assert not reopen_attempts
    finally:
        process = supervisor._process
        if process is not None:
            supervisor._terminate_owned_process(process)
        supervisor._release_process_authority()
        supervisor._process = None
        supervisor._process_record = None
        supervisor._close_filesystem_authority()
    assert not supervisor.process_record_path.exists()


class _OrderingProcess:
    pid = 4242

    def __init__(self, events: list[str]) -> None:
        self._events = events
        self._return_code: int | None = None

    def poll(self) -> int | None:
        return self._return_code

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self._return_code = self._return_code if self._return_code is not None else -15
        return self._return_code

    def kill(self) -> None:
        self._events.append("process-kill")
        self._return_code = -9


class _OrderingAuthority:
    def __init__(self, events: list[str]) -> None:
        self._events = events
        self._process: _OrderingProcess | None = None
        self._context_digest = "f" * 64

    @property
    def claim(self) -> dict[str, object]:
        return {
            "kind": "windows-job",
            "attempt_binding": "test",
            "name": "test-job",
            "context_digest": self._context_digest,
            "root_pid": None if self._process is None else self._process.pid,
            "root_creation_identity": "windows:test",
        }

    def child_environment(self) -> dict[str, str]:
        return {}

    def child_handle(self) -> int:
        return 9876

    def bind(self, pid: int, expected_creation_identity: str | None = None) -> None:
        assert pid == _OrderingProcess.pid
        assert expected_creation_identity == "windows:test"
        self._events.append("bind")

    def resume(self, pid: int) -> None:
        assert pid == _OrderingProcess.pid
        self._events.append("resume")

    def terminate(self, pid: int, *, force: bool = False) -> None:
        del force
        assert pid == _OrderingProcess.pid
        self._events.append("terminate")
        if self._process is not None:
            self._process._return_code = -15

    def close(self) -> None:
        self._events.append("close")

    def drain(self) -> None:
        self._events.append("drain")


class _CleanupFailureAuthority(_OrderingAuthority):
    def terminate(self, pid: int, *, force: bool = False) -> None:
        assert pid == _OrderingProcess.pid
        self._events.append("terminate-force" if force else "terminate")
        raise ProcessAuthorityError("synthetic termination failure")

    def drain(self) -> None:
        self._events.append("drain")
        raise ProcessAuthorityError("synthetic drain failure")

    def close(self) -> None:
        self._events.append("close")
        raise ProcessAuthorityError("synthetic close failure")


def test_windows_persists_bound_record_before_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ordering probe must fail on the old resume-before-record sequence."""

    capability = _capability(tmp_path, monkeypatch, code="pass")
    events: list[str] = []
    authority = _OrderingAuthority(events)

    class StartupInfo:
        lpAttributeList: object | None = None

    def fake_popen(*args: object, **kwargs: object) -> _OrderingProcess:
        del args
        creation_flags = kwargs["creationflags"]
        assert isinstance(creation_flags, int)
        assert creation_flags & 0x00000004
        events.append("created-suspended")
        process = _OrderingProcess(events)
        authority._process = process
        return process

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(subprocess, "STARTUPINFO", StartupInfo, raising=False)

    def fake_create(
        attempt_root: Path,
        context_digest: str | None = None,
        *,
        root_pid: int | None = None,
        root_creation_identity: str | None = None,
    ) -> _OrderingAuthority:
        del attempt_root
        del root_pid, root_creation_identity
        if context_digest is not None:
            authority._context_digest = context_digest
        return authority

    monkeypatch.setattr(ProcessAuthority, "create", fake_create)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        supervisor_module,
        "_process_metadata",
        lambda pid: supervisor_module._ProcessMetadata(
            str(Path(sys.executable)),
            "windows:test",
            True,
            None,
            datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )
    original_write = SolverSupervisor._write_process_record

    def record_write(supervisor: SolverSupervisor, record: dict[str, object]) -> None:
        events.append(f"persist:{record.get('state')}")
        original_write(supervisor, record)

    monkeypatch.setattr(SolverSupervisor, "_write_process_record", record_write)
    supervisor = SolverSupervisor(capability)
    try:
        supervisor.start()
        first_persist = next(
            index for index, event in enumerate(events) if event.startswith("persist:")
        )
        resume = events.index("resume")
        assert first_persist < resume
        assert events[first_persist] == "persist:BOUND_SUSPENDED"
        assert events.index("persist:RUNNING") > resume
    finally:
        supervisor.cancel()


def test_windows_process_image_replacement_fails_before_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A suspended child with a replaced image must never become RUNNING."""

    capability = _capability(tmp_path, monkeypatch, code="pass")
    events: list[str] = []
    authority = _OrderingAuthority(events)

    class StartupInfo:
        lpAttributeList: object | None = None

    def fake_popen(*args: object, **kwargs: object) -> _OrderingProcess:
        del args
        creation_flags = kwargs["creationflags"]
        assert isinstance(creation_flags, int)
        assert creation_flags & 0x00000004
        events.append("created-suspended")
        process = _OrderingProcess(events)
        authority._process = process
        return process

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(subprocess, "STARTUPINFO", StartupInfo, raising=False)

    def fake_create(
        attempt_root: Path,
        context_digest: str | None = None,
        *,
        root_pid: int | None = None,
        root_creation_identity: str | None = None,
    ) -> _OrderingAuthority:
        del attempt_root
        del root_pid, root_creation_identity
        if context_digest is not None:
            authority._context_digest = context_digest
        return authority

    replacement = tmp_path / "replacement-febio.exe"
    replacement.write_bytes(b"replacement image")
    metadata_calls = 0

    def process_metadata(pid: int) -> supervisor_module._ProcessMetadata:
        nonlocal metadata_calls
        assert pid == _OrderingProcess.pid
        metadata_calls += 1
        executable = Path(sys.executable) if metadata_calls == 1 else replacement
        return supervisor_module._ProcessMetadata(
            str(executable),
            "windows:test",
            True,
            None,
            datetime(2026, 1, 1, tzinfo=UTC),
        )

    monkeypatch.setattr(ProcessAuthority, "create", fake_create)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(supervisor_module, "_process_metadata", process_metadata)
    original_write = SolverSupervisor._write_process_record

    def record_write(supervisor: SolverSupervisor, record: dict[str, object]) -> None:
        events.append(f"persist:{record.get('state')}")
        original_write(supervisor, record)

    monkeypatch.setattr(SolverSupervisor, "_write_process_record", record_write)
    supervisor = SolverSupervisor(capability)

    with pytest.raises(SolverLaunchError, match="executable|image|runtime"):
        supervisor.start()

    assert "resume" not in events
    assert "persist:RUNNING" not in events
    assert events.count("terminate") == 1
    assert events.count("close") == 1
    assert supervisor.state is SolverState.FAILED
    assert supervisor.process_id is None
    assert not supervisor.process_record_path.exists()


def test_windows_runtime_claim_close_failure_after_resume_retains_exact_retry_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A post-resume runtime claim close failure keeps its exact handle reachable."""

    if os.name != "nt":
        pytest.fail("required Windows runtime claim test executed on a non-Windows host")

    capability = _capability(tmp_path, monkeypatch, code="import time; time.sleep(30)")
    captured_claims: list[runtime_module._RuntimeLaunchClaim] = []
    original_acquire = _acquire_runtime_launch_claim

    def capture_claim(value: object) -> runtime_module._RuntimeLaunchClaim:
        claim = original_acquire(value)
        captured_claims.append(claim)
        return claim

    monkeypatch.setattr(supervisor_module, "_acquire_runtime_launch_claim", capture_claim)
    launched: list[subprocess.Popen[bytes]] = []
    original_cleanup = SolverSupervisor._cleanup_failed_start

    def capture_cleanup(
        instance: SolverSupervisor,
        process: subprocess.Popen[bytes] | None,
        authority: ProcessAuthority | None,
        bound: bool,
    ) -> tuple[BaseException, ...]:
        if process is not None:
            launched.append(process)
        return original_cleanup(instance, process, authority, bound)

    monkeypatch.setattr(SolverSupervisor, "_cleanup_failed_start", capture_cleanup)
    close_failures = True
    close_attempts: list[int] = []
    original_claim_close = runtime_module._RuntimeLaunchClaim.close

    def flaky_close(claim: runtime_module._RuntimeLaunchClaim) -> None:
        handle = claim.handle
        assert handle is not None
        close_attempts.append(handle)
        if close_failures:
            raise runtime_module.RuntimeProbeError("synthetic persistent runtime close failure")
        original_claim_close(claim)

    monkeypatch.setattr(runtime_module._RuntimeLaunchClaim, "close", flaky_close)
    supervisor = SolverSupervisor(capability)
    try:
        with pytest.raises(SolverOwnershipError, match="cleanup|closed|authority"):
            supervisor.start()

        assert captured_claims
        claim = captured_claims[0]
        assert claim.handle is not None
        runtime_fd = claim.handle
        assert close_attempts
        assert close_attempts == [runtime_fd] * len(close_attempts)
        assert supervisor._runtime_launch_claim is claim
        assert supervisor.state is SolverState.FAILED
        assert supervisor._process is None
        assert launched
        child = launched[0]
        assert child.poll() is not None
        assert child.wait(timeout=0) == child.returncode

        close_failures = False
        supervisor._close_filesystem_authority()
        assert supervisor._runtime_launch_claim is None
        with pytest.raises(OSError):
            os.fstat(runtime_fd)
    finally:
        close_failures = False
        if captured_claims and captured_claims[0].handle is not None:
            original_claim_close(captured_claims[0])
        for child in launched:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5.0)


def test_windows_runtime_claim_close_failure_before_bind_retains_exact_retry_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed pre-bind start keeps a runtime claim for exact later cleanup."""

    if os.name != "nt":
        pytest.fail("required Windows runtime claim test executed on a non-Windows host")

    capability = _capability(tmp_path, monkeypatch, code="import time; time.sleep(30)")
    captured_claims: list[runtime_module._RuntimeLaunchClaim] = []
    original_acquire = _acquire_runtime_launch_claim

    def capture_claim(value: object) -> runtime_module._RuntimeLaunchClaim:
        claim = original_acquire(value)
        captured_claims.append(claim)
        return claim

    monkeypatch.setattr(supervisor_module, "_acquire_runtime_launch_claim", capture_claim)
    launched: list[subprocess.Popen[bytes]] = []
    original_cleanup = SolverSupervisor._cleanup_failed_start

    def capture_cleanup(
        instance: SolverSupervisor,
        process: subprocess.Popen[bytes] | None,
        authority: ProcessAuthority | None,
        bound: bool,
    ) -> tuple[BaseException, ...]:
        if process is not None:
            launched.append(process)
        return original_cleanup(instance, process, authority, bound)

    monkeypatch.setattr(SolverSupervisor, "_cleanup_failed_start", capture_cleanup)
    metadata_calls = 0
    original_metadata = supervisor_module._process_metadata

    def fail_before_bind(pid: int) -> supervisor_module._ProcessMetadata:
        nonlocal metadata_calls
        metadata_calls += 1
        if metadata_calls == 1:
            raise OSError("synthetic pre-bind metadata failure")
        return original_metadata(pid)

    monkeypatch.setattr(supervisor_module, "_process_metadata", fail_before_bind)
    close_failures = True
    close_attempts: list[int] = []
    original_claim_close = runtime_module._RuntimeLaunchClaim.close

    def flaky_close(claim: runtime_module._RuntimeLaunchClaim) -> None:
        handle = claim.handle
        assert handle is not None
        close_attempts.append(handle)
        if close_failures:
            raise runtime_module.RuntimeProbeError("synthetic persistent runtime close failure")
        original_claim_close(claim)

    monkeypatch.setattr(runtime_module._RuntimeLaunchClaim, "close", flaky_close)
    supervisor = SolverSupervisor(capability)
    try:
        with pytest.raises(SolverOwnershipError, match="cleanup|closed|authority"):
            supervisor.start()

        assert captured_claims
        claim = captured_claims[0]
        assert claim.handle is not None
        runtime_fd = claim.handle
        assert close_attempts
        assert close_attempts == [runtime_fd] * len(close_attempts)
        assert supervisor._runtime_launch_claim is claim
        assert supervisor.state is SolverState.FAILED
        assert supervisor._process is None
        assert launched
        child = launched[0]
        assert child.poll() is not None
        assert child.wait(timeout=0) == child.returncode
        assert supervisor._process_record is None

        close_failures = False
        supervisor._close_filesystem_authority()
        assert supervisor._runtime_launch_claim is None
        with pytest.raises(OSError):
            os.fstat(runtime_fd)
    finally:
        close_failures = False
        if captured_claims and captured_claims[0].handle is not None:
            original_claim_close(captured_claims[0])
        for child in launched:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5.0)


def test_windows_invalid_process_record_candidate_close_failure_retains_exact_retry_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Malformed record candidates remain owned when their close keeps failing."""

    if os.name != "nt":
        pytest.fail("required Windows process-record test executed on a non-Windows host")

    capability = _capability(tmp_path, monkeypatch, code="pass")
    supervisor = SolverSupervisor(capability)
    record_path = Path(os.fspath(supervisor.process_record_path))
    record_path.write_bytes(b"{")
    candidate_fds: list[int] = []
    original_open = supervisor_module._windows_open_process_record

    def capture_open(path: Path, *, create: bool, delete_access: bool = True) -> int:
        fd = original_open(path, create=create, delete_access=delete_access)
        candidate_fds.append(fd)
        return fd

    monkeypatch.setattr(supervisor_module, "_windows_open_process_record", capture_open)
    close_failures = True
    close_attempts: list[int] = []
    original_close = os.close

    def fail_candidate_close(fd: int) -> None:
        if fd in candidate_fds and close_failures:
            close_attempts.append(fd)
            raise OSError("synthetic persistent process-record close failure")
        original_close(fd)

    try:
        with monkeypatch.context() as read_patch:
            read_patch.setattr(os, "close", fail_candidate_close)
            with pytest.raises(SolverOwnershipError, match="invalid|closed"):
                supervisor._read_process_record()

        assert candidate_fds
        candidate_fd = candidate_fds[0]
        assert close_attempts == [candidate_fd]
        owned_candidates = getattr(supervisor, "_process_record_candidates", ())
        assert any(candidate.handle == candidate_fd for candidate in owned_candidates)

        close_failures = False
        supervisor._close_filesystem_authority()
        assert not getattr(supervisor, "_process_record_candidates", ())
        with pytest.raises(OSError):
            os.fstat(candidate_fd)
    finally:
        close_failures = False
        for candidate_fd in candidate_fds:
            try:
                os.fstat(candidate_fd)
            except OSError:
                continue
            else:
                original_close(candidate_fd)
        if record_path.exists():
            record_path.unlink()


def test_windows_process_record_candidate_validation_and_close_failure_preserves_prior_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A replacement candidate cannot displace a live prior claim before validation."""

    if os.name != "nt":
        pytest.fail("required Windows process-record test executed on a non-Windows host")

    capability = _capability(tmp_path, monkeypatch, code="import time; time.sleep(30)")
    supervisor = SolverSupervisor(capability)
    supervisor.start()
    prior_claim = supervisor._process_record_claim
    assert prior_claim is not None and prior_claim.handle is not None
    prior_fd = prior_claim.handle
    record_path = Path(os.fspath(prior_claim.path))
    record_key = supervisor_module._windows_record_claim_key(record_path)
    prior_entry = (prior_fd, prior_claim.device, prior_claim.inode)
    original_open = supervisor_module._windows_open_process_record
    candidate_fds: list[int] = []

    def capture_open(path: Path, *, create: bool, delete_access: bool = True) -> int:
        fd = original_open(path, create=create, delete_access=delete_access)
        candidate_fds.append(fd)
        return fd

    supervisor._process_record_claim = None
    candidate_from_installed: supervisor_module._ProcessRecordClaim | None = None
    try:
        with monkeypatch.context() as read_patch:
            read_patch.setattr(supervisor_module, "_windows_open_process_record", capture_open)
            record = supervisor._read_process_record()
        candidate_from_installed = supervisor._process_record_claim
        supervisor._process_record_claim = prior_claim

        assert candidate_fds
        candidate_fd = candidate_fds[0]
        owned_candidates = getattr(supervisor, "_process_record_candidates", ())
        assert any(candidate.handle == candidate_fd for candidate in owned_candidates)
        candidate = next(
            candidate for candidate in owned_candidates if candidate.handle == candidate_fd
        )
        assert supervisor._process_record_claim is prior_claim
        invalid_record = dict(record)
        invalid_record["state"] = "NOT_RUNNING"
        with pytest.raises(SolverOwnershipError, match="reconnectable|RUNNING"):
            supervisor._validate_process_record(invalid_record)
        assert supervisor._process_record_claim is prior_claim
        assert os.fstat(prior_fd).st_size > 0
        assert supervisor_module._WINDOWS_RECORD_CLAIMS.get(record_key) == [
            prior_entry,
            (candidate_fd, candidate.device, candidate.inode),
        ]

        close_failures = True
        close_attempts: list[int] = []
        original_close = os.close

        def fail_candidate_close(fd: int) -> None:
            if fd == candidate_fd:
                close_attempts.append(fd)
                if close_failures:
                    raise OSError("synthetic persistent candidate close failure")
            original_close(fd)

        with monkeypatch.context() as close_patch:
            close_patch.setattr(os, "close", fail_candidate_close)
            failures = supervisor._release_process_record_claim(candidate)
            assert failures
            assert candidate.handle == candidate_fd
            assert supervisor._process_record_claim is prior_claim
            assert os.fstat(prior_fd).st_size > 0
            assert supervisor_module._WINDOWS_RECORD_CLAIMS.get(record_key) == [
                prior_entry,
                (candidate_fd, candidate.device, candidate.inode),
            ]
            close_failures = False
            assert supervisor._release_process_record_claim(candidate) == ()
            assert supervisor._release_process_record_claim(candidate) == ()

        assert close_attempts == [candidate_fd, candidate_fd]
        assert supervisor._process_record_claim is prior_claim
        assert supervisor_module._WINDOWS_RECORD_CLAIMS.get(record_key) == [prior_entry]
    finally:
        if candidate_from_installed is not None and candidate_from_installed.handle is not None:
            with contextlib.suppress(OSError):
                os.close(candidate_from_installed.handle)
        supervisor._process_record_claim = prior_claim
        if supervisor.process_id is not None:
            supervisor.cancel()


def test_windows_record_claim_close_failure_retains_exact_retry_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed record close keeps the exact claim for a later close retry."""

    if os.name != "nt":
        pytest.fail("required Windows record claim test executed on a non-Windows host")

    capability = _capability(tmp_path, monkeypatch, code="import time; time.sleep(30)")
    supervisor = SolverSupervisor(capability)
    supervisor.start()
    process = supervisor._process
    claim = supervisor._process_record_claim
    assert process is not None
    assert claim is not None and claim.handle is not None
    record_fd = claim.handle
    record_path = Path(os.fspath(claim.path))
    record_key = supervisor_module._windows_record_claim_key(record_path)
    record_entry = (record_fd, claim.device, claim.inode)
    process_record = supervisor_module._read_record_fd(record_fd)
    close_attempts = 0
    original_close = os.close

    def fail_once(fd: int) -> None:
        nonlocal close_attempts
        if fd == record_fd and close_attempts == 0:
            close_attempts += 1
            raise OSError("synthetic process record close failure")
        original_close(fd)

    try:
        with monkeypatch.context() as close_patch:
            close_patch.setattr(os, "close", fail_once)
            with pytest.raises(SolverOwnershipError, match="handles|closed"):
                supervisor._close_filesystem_authority()

        assert supervisor.state is SolverState.RUNNING
        assert supervisor._process is process
        assert supervisor._process_record_claim is claim
        assert claim.handle == record_fd
        assert supervisor_module._WINDOWS_RECORD_CLAIMS.get(record_key) == [record_entry]
        assert supervisor_module._read_record_fd(record_fd) == process_record
        assert process.poll() is None

        supervisor._close_filesystem_authority()
        assert supervisor._process_record_claim is None
        assert record_key not in supervisor_module._WINDOWS_RECORD_CLAIMS
        with pytest.raises(OSError):
            os.fstat(record_fd)
        assert process.poll() is None
        assert json.loads(record_path.read_text(encoding="utf-8"))["state"] == "RUNNING"
    finally:
        active_process = supervisor._process
        if active_process is not None and active_process.poll() is None:
            supervisor._terminate_owned_process(active_process)
        supervisor._release_process_authority()
        supervisor._process = None
        supervisor._process_record = None
        if record_path.exists():
            record_path.unlink()


def test_windows_late_failure_preserves_replaced_process_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capability = _capability(tmp_path, monkeypatch, code="pass")
    events: list[str] = []
    authority = _OrderingAuthority(events)

    class StartupInfo:
        lpAttributeList: object | None = None

    def fake_popen(*args: object, **kwargs: object) -> _OrderingProcess:
        del args
        creation_flags = kwargs["creationflags"]
        assert isinstance(creation_flags, int)
        assert creation_flags & 0x00000004
        events.append("created-suspended")
        process = _OrderingProcess(events)
        authority._process = process
        return process

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(subprocess, "STARTUPINFO", StartupInfo, raising=False)

    def fake_create(
        attempt_root: Path,
        context_digest: str | None = None,
        *,
        root_pid: int | None = None,
        root_creation_identity: str | None = None,
    ) -> _OrderingAuthority:
        del attempt_root
        del root_pid, root_creation_identity
        if context_digest is not None:
            authority._context_digest = context_digest
        return authority

    def fail_resume(pid: int) -> None:
        assert pid == _OrderingProcess.pid
        events.append("resume-failure")
        raise OSError("synthetic late resume failure")

    monkeypatch.setattr(ProcessAuthority, "create", fake_create)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        supervisor_module,
        "_process_metadata",
        lambda pid: supervisor_module._ProcessMetadata(
            str(Path(sys.executable)),
            "windows:test",
            True,
            None,
            datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )
    monkeypatch.setattr(authority, "resume", fail_resume)
    original_write = SolverSupervisor._write_process_record
    replacement_installed = False

    def record_write(supervisor: SolverSupervisor, record: dict[str, object]) -> None:
        nonlocal replacement_installed
        original_write(supervisor, record)
        if record.get("state") == "BOUND_SUSPENDED":
            replacement = dict(record)
            replacement["replacement_marker"] = "foreign-regular-file"
            replacement_path = supervisor.process_record_path.with_name("foreign.json")
            replacement_path.write_text(json.dumps(replacement, sort_keys=True), encoding="utf-8")
            try:
                os.replace(os.fspath(replacement_path), os.fspath(supervisor.process_record_path))
            except PermissionError:
                events.append("foreign-replacement-blocked")
            else:
                replacement_installed = True
                events.append("replaced-process-record")

    monkeypatch.setattr(SolverSupervisor, "_write_process_record", record_write)
    supervisor = SolverSupervisor(capability)

    with pytest.raises(SolverLaunchError, match="late resume failure"):
        supervisor.start()

    record_path = supervisor.process_record_path
    if replacement_installed:
        replacement = json.loads(record_path.read_text(encoding="utf-8"))
        assert record_path.is_file()
        assert replacement["replacement_marker"] == "foreign-regular-file"
        assert replacement["state"] == "BOUND_SUSPENDED"
    else:
        assert not record_path.exists()
    assert supervisor.state is SolverState.FAILED
    replacement_event = (
        "replaced-process-record" if replacement_installed else "foreign-replacement-blocked"
    )
    assert events.index(replacement_event) < events.index("resume-failure")
    assert events.count("terminate") == 1
    assert events.count("close") == 1
    assert events.index("terminate") < events.index("close")

    with pytest.raises(SolverOwnershipError, match="invalid|reconnectable|RUNNING"):
        SolverSupervisor.reconnect(capability)


@pytest.mark.parametrize("replace_with_foreign", [False, True])
def test_windows_final_guard_rolls_back_owned_record_without_foreign_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replace_with_foreign: bool,
) -> None:
    capability = _capability(tmp_path, monkeypatch, code="pass")
    events: list[str] = []
    authority = _OrderingAuthority(events)

    class StartupInfo:
        lpAttributeList: object | None = None

    def fake_popen(*args: object, **kwargs: object) -> _OrderingProcess:
        del args
        creation_flags = kwargs["creationflags"]
        assert isinstance(creation_flags, int)
        assert creation_flags & 0x00000004
        events.append("created-suspended")
        process = _OrderingProcess(events)
        authority._process = process
        return process

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(subprocess, "STARTUPINFO", StartupInfo, raising=False)

    def fake_create(
        attempt_root: Path,
        context_digest: str | None = None,
        *,
        root_pid: int | None = None,
        root_creation_identity: str | None = None,
    ) -> _OrderingAuthority:
        del attempt_root
        del root_pid, root_creation_identity
        if context_digest is not None:
            authority._context_digest = context_digest
        return authority

    monkeypatch.setattr(ProcessAuthority, "create", fake_create)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        supervisor_module,
        "_process_metadata",
        lambda pid: supervisor_module._ProcessMetadata(
            str(Path(sys.executable)),
            "windows:test",
            True,
            None,
            datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )
    original_write = SolverSupervisor._write_process_record
    replacement_installed = False

    def record_write(supervisor: SolverSupervisor, record: dict[str, object]) -> None:
        nonlocal replacement_installed
        original_write(supervisor, record)
        if replace_with_foreign and record.get("state") == "RUNNING":
            replacement = dict(record)
            replacement["replacement_marker"] = "foreign-final-guard"
            replacement_path = supervisor.process_record_path.with_name("foreign.json")
            replacement_path.write_text(json.dumps(replacement, sort_keys=True), encoding="utf-8")
            try:
                os.replace(os.fspath(replacement_path), os.fspath(supervisor.process_record_path))
            except PermissionError:
                return
            replacement_installed = True

    monkeypatch.setattr(SolverSupervisor, "_write_process_record", record_write)
    original_revalidate = SolverSupervisor._revalidate_launch_binding
    guard_failed = False

    def final_guard(supervisor: SolverSupervisor) -> None:
        nonlocal guard_failed
        original_revalidate(supervisor)
        if any(event == "persist:RUNNING" for event in events) and not guard_failed:
            guard_failed = True
            raise SolverConfigurationError("synthetic final guard failure")

    def record_events(supervisor: SolverSupervisor, record: dict[str, object]) -> None:
        events.append(f"persist:{record.get('state')}")
        record_write(supervisor, record)

    monkeypatch.setattr(SolverSupervisor, "_revalidate_launch_binding", final_guard)
    monkeypatch.setattr(SolverSupervisor, "_write_process_record", record_events)
    supervisor = SolverSupervisor(capability)

    with pytest.raises(SolverConfigurationError, match="final guard"):
        supervisor.start()

    assert supervisor.state is SolverState.FAILED
    assert supervisor._process is None
    assert supervisor._process_authority is None
    record_path = supervisor.process_record_path
    if replacement_installed:
        replacement = json.loads(record_path.read_text(encoding="utf-8"))
        assert replacement["replacement_marker"] == "foreign-final-guard"
    else:
        assert not record_path.exists()


@pytest.mark.parametrize("replace_with_foreign", [False, True])
def test_windows_bound_suspended_rollback_removes_owned_record_without_foreign_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replace_with_foreign: bool,
) -> None:
    capability = _capability(tmp_path, monkeypatch, code="pass")
    events: list[str] = []
    authority = _OrderingAuthority(events)

    class StartupInfo:
        lpAttributeList: object | None = None

    def fake_popen(*args: object, **kwargs: object) -> _OrderingProcess:
        del args
        creation_flags = kwargs["creationflags"]
        assert isinstance(creation_flags, int)
        assert creation_flags & 0x00000004
        events.append("created-suspended")
        process = _OrderingProcess(events)
        authority._process = process
        return process

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(subprocess, "STARTUPINFO", StartupInfo, raising=False)

    def fake_create(
        attempt_root: Path,
        context_digest: str | None = None,
        *,
        root_pid: int | None = None,
        root_creation_identity: str | None = None,
    ) -> _OrderingAuthority:
        del attempt_root
        del root_pid, root_creation_identity
        if context_digest is not None:
            authority._context_digest = context_digest
        return authority

    monkeypatch.setattr(ProcessAuthority, "create", fake_create)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        supervisor_module,
        "_process_metadata",
        lambda pid: supervisor_module._ProcessMetadata(
            str(Path(sys.executable)),
            "windows:test",
            True,
            None,
            datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )
    original_write = SolverSupervisor._write_process_record
    replacement_installed = False

    def record_write(supervisor: SolverSupervisor, record: dict[str, object]) -> None:
        nonlocal replacement_installed
        original_write(supervisor, record)
        if replace_with_foreign and record.get("state") == "BOUND_SUSPENDED":
            replacement = dict(record)
            replacement["replacement_marker"] = "foreign-bound-record"
            replacement_path = supervisor.process_record_path.with_name("foreign.json")
            replacement_path.write_text(json.dumps(replacement, sort_keys=True), encoding="utf-8")
            try:
                os.replace(os.fspath(replacement_path), os.fspath(supervisor.process_record_path))
            except PermissionError:
                return
            replacement_installed = True

    def fail_resume(pid: int) -> None:
        assert pid == _OrderingProcess.pid
        events.append("resume-failure")
        raise OSError("synthetic late resume failure")

    monkeypatch.setattr(authority, "resume", fail_resume)

    def record_events(supervisor: SolverSupervisor, record: dict[str, object]) -> None:
        events.append(f"persist:{record.get('state')}")
        record_write(supervisor, record)

    monkeypatch.setattr(SolverSupervisor, "_write_process_record", record_events)
    supervisor = SolverSupervisor(capability)

    with pytest.raises(SolverLaunchError, match="late resume failure"):
        supervisor.start()

    assert supervisor.state is SolverState.FAILED
    assert supervisor._process is None
    assert supervisor._process_authority is None
    record_path = supervisor.process_record_path
    if replacement_installed:
        replacement = json.loads(record_path.read_text(encoding="utf-8"))
        assert replacement["replacement_marker"] == "foreign-bound-record"
    else:
        assert not record_path.exists()


def test_failed_start_cleanup_errors_propagate_and_roll_back_owned_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capability = _capability(tmp_path, monkeypatch, code="pass")
    events: list[str] = []
    authority = _CleanupFailureAuthority(events)

    class StartupInfo:
        lpAttributeList: object | None = None

    def fake_popen(*args: object, **kwargs: object) -> _OrderingProcess:
        del args
        creation_flags = kwargs["creationflags"]
        assert isinstance(creation_flags, int)
        assert creation_flags & supervisor_module._CREATE_SUSPENDED
        events.append("created-suspended")
        process = _OrderingProcess(events)
        authority._process = process
        return process

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(subprocess, "STARTUPINFO", StartupInfo, raising=False)

    def fake_create(
        attempt_root: Path,
        context_digest: str | None = None,
        *,
        root_pid: int | None = None,
        root_creation_identity: str | None = None,
    ) -> _CleanupFailureAuthority:
        del attempt_root, root_pid, root_creation_identity
        if context_digest is not None:
            authority._context_digest = context_digest
        return authority

    def fail_resume(pid: int) -> None:
        assert pid == _OrderingProcess.pid
        events.append("resume-failure")
        raise OSError("synthetic late resume failure")

    monkeypatch.setattr(ProcessAuthority, "create", fake_create)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        supervisor_module,
        "_process_metadata",
        lambda pid: supervisor_module._ProcessMetadata(
            str(Path(sys.executable)),
            "windows:test",
            True,
            None,
            datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )
    monkeypatch.setattr(authority, "resume", fail_resume)

    supervisor = SolverSupervisor(capability)
    with pytest.raises(SolverOwnershipError, match="cleanup|termination|drain|close"):
        supervisor.start()

    assert supervisor.state is SolverState.FAILED
    assert supervisor._process is None
    assert supervisor._process_authority is None
    assert not supervisor.process_record_path.exists()
    assert events.index("terminate") < events.index("terminate-force")
    assert events.index("terminate-force") < events.index("drain")
    assert events.index("drain") < events.index("close")
