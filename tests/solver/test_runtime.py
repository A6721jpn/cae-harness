from __future__ import annotations

import contextlib
import ctypes
import hashlib
import os
import stat
import subprocess
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import febio_cae_harness.solver.runtime as runtime_module
from febio_cae_harness.solver.runtime import (
    FebioRuntimeDiagnostic,
    RuntimeProbeError,
    probe_febio,
    validate_runtime_diagnostic,
)


def _fake_file(tmp_path: Path, *, executable: bool = True) -> Path:
    path = tmp_path / "fake-febio"
    path.write_bytes(b"synthetic FEBio executable")
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _patch_lstat_metadata(
    monkeypatch: pytest.MonkeyPatch,
    candidate: Path,
    *,
    file_attributes: int = 0,
    nlink: int = 1,
) -> None:
    original_lstat = Path.lstat

    def fake_lstat(path: Path) -> object:
        metadata = original_lstat(path)
        if path != candidate:
            return metadata
        return SimpleNamespace(
            st_mode=metadata.st_mode,
            st_file_attributes=file_attributes,
            st_nlink=nlink,
        )

    monkeypatch.setattr(Path, "lstat", fake_lstat)


@pytest.mark.parametrize(
    ("last_error", "expected"),
    [(1656, False), (6, False), (5, None)],
)
def test_windows_compare_object_handles_classifies_false_last_error(
    monkeypatch: pytest.MonkeyPatch, last_error: int, expected: bool | None
) -> None:
    """FALSE is definitive only for the two documented object-identity errors."""

    events: list[tuple[str, int | str]] = []

    def compare(first: object, second: object) -> int:
        first_value = getattr(first, "value", first)
        second_value = getattr(second, "value", second)
        assert isinstance(first_value, int)
        assert isinstance(second_value, int)
        events.append(("invoke", first_value))
        events.append(("second", second_value))
        return 0

    fake_library = SimpleNamespace(CompareObjectHandles=compare)

    def fake_windll(name: str, *, use_last_error: bool) -> object:
        events.append(("windll", name))
        assert use_last_error
        return fake_library

    def fake_set_last_error(value: int) -> None:
        events.append(("set", value))

    reads: list[int] = []

    def fake_get_last_error() -> int:
        events.append(("get", last_error))
        reads.append(last_error)
        return last_error

    monkeypatch.setattr(ctypes, "WinDLL", fake_windll, raising=False)
    monkeypatch.setattr(ctypes, "set_last_error", fake_set_last_error, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", fake_get_last_error, raising=False)

    assert runtime_module._windows_compare_object_handles(101, 202) is expected
    assert reads == [last_error]
    assert [event[0] for event in events[-4:]] == ["set", "invoke", "second", "get"]


def test_probe_fake_executable_returns_immutable_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _fake_file(tmp_path)

    class CompletedProcess:
        returncode = 0

        def communicate(self, input: bytes, timeout: float) -> tuple[bytes, bytes]:
            return b"version 4.2.0\n", b""

    monkeypatch.setattr(
        "febio_cae_harness.solver.runtime.subprocess.Popen",
        lambda command, **kwargs: CompletedProcess(),
    )

    diagnostic = probe_febio(executable)

    assert isinstance(diagnostic, FebioRuntimeDiagnostic)
    assert diagnostic.path == executable.absolute()
    assert diagnostic.sha256 == hashlib.sha256(executable.read_bytes()).hexdigest()
    assert diagnostic.size == executable.stat().st_size
    assert diagnostic.version == "4.2.0"
    with pytest.raises(AttributeError):
        diagnostic.version = "4.3.0"  # type: ignore[misc]
    object.__setattr__(diagnostic, "sha256", "b" * 64)
    with pytest.raises(RuntimeProbeError, match="modified"):
        validate_runtime_diagnostic(diagnostic)
    assert diagnostic.to_dict() == {
        "path": str(executable.absolute()),
        "sha256": diagnostic.sha256,
        "size": diagnostic.size,
        "version": "4.2.0",
    }


def test_probe_uses_exact_path_and_no_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _fake_file(tmp_path)
    observed: dict[str, object] = {}

    class CompletedProcess:
        returncode = 0

        def communicate(self, input: bytes, timeout: float) -> tuple[bytes, bytes]:
            observed["input"] = input
            observed["timeout"] = timeout
            return b"version 4.2.0\n", b""

    def fake_popen(command: object, **kwargs: object) -> CompletedProcess:
        observed["command"] = command
        observed.update(kwargs)
        return CompletedProcess()

    monkeypatch.setattr("febio_cae_harness.solver.runtime.subprocess.Popen", fake_popen)

    diagnostic = probe_febio(executable)

    assert diagnostic.version == "4.2.0"
    assert observed["command"] == [str(executable.absolute())]
    assert observed["shell"] is False
    assert observed["stdin"] is not None
    assert observed["stdout"] is not None
    assert observed["stderr"] is not None
    assert observed["input"] == b"quit\n"


@pytest.mark.parametrize(
    "output",
    [
        b"not version 4.2.0\n",
        b"version 4.2.0\nversion 4.2.0\n",
        b"FEBio is ready\n",
    ],
)
def test_probe_rejects_nonexact_version_banner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: bytes
) -> None:
    executable = _fake_file(tmp_path)

    class CompletedProcess:
        returncode = 0

        def communicate(self, input: bytes, timeout: float) -> tuple[bytes, bytes]:
            return output, b""

    monkeypatch.setattr(
        "febio_cae_harness.solver.runtime.subprocess.Popen",
        lambda command, **kwargs: CompletedProcess(),
    )

    with pytest.raises(RuntimeProbeError, match="version banner"):
        probe_febio(executable)


def test_probe_requires_clean_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executable = _fake_file(tmp_path)

    class CompletedProcess:
        returncode = 7

        def communicate(self, input: bytes, timeout: float) -> tuple[bytes, bytes]:
            return b"version 4.2.0\n", b""

    monkeypatch.setattr(
        "febio_cae_harness.solver.runtime.subprocess.Popen",
        lambda command, **kwargs: CompletedProcess(),
    )

    with pytest.raises(RuntimeProbeError, match="clean exit"):
        probe_febio(executable)


def test_probe_hashes_again_after_process_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _fake_file(tmp_path)

    class CompletedProcess:
        returncode = 0

        def communicate(self, input: bytes, timeout: float) -> tuple[bytes, bytes]:
            executable.write_bytes(b"modified synthetic FEBio executable")
            return b"version 4.2.0\n", b""

    monkeypatch.setattr(
        "febio_cae_harness.solver.runtime.subprocess.Popen",
        lambda command, **kwargs: CompletedProcess(),
    )

    with pytest.raises(RuntimeProbeError, match="changed during probe"):
        probe_febio(executable)


def test_probe_timeout_kills_process_and_does_not_report_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _fake_file(tmp_path)
    observed = {"killed": False}

    class HangingProcess:
        returncode = None

        def communicate(
            self, input: bytes | None = None, timeout: float | None = None
        ) -> tuple[bytes, bytes]:
            if input is not None:
                raise subprocess.TimeoutExpired("synthetic", timeout or 0)
            return b"", b""

        def kill(self) -> None:
            observed["killed"] = True

    monkeypatch.setattr(
        "febio_cae_harness.solver.runtime.subprocess.Popen",
        lambda command, **kwargs: HangingProcess(),
    )

    with pytest.raises(RuntimeProbeError, match="timed out"):
        probe_febio(executable, timeout_seconds=0.01)
    assert observed["killed"] is True


@pytest.mark.parametrize("kind", ["directory", "symlink", "hardlink"])
def test_probe_rejects_nonregular_or_aliased_target(
    tmp_path: Path, kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target"
    target.write_bytes(b"synthetic FEBio executable")
    target.chmod(target.stat().st_mode | stat.S_IXUSR)

    if kind == "directory":
        candidate = tmp_path / "directory"
        candidate.mkdir()
    elif kind == "symlink":
        candidate = tmp_path / "alias"
        try:
            candidate.symlink_to(target)
        except (OSError, NotImplementedError):
            candidate.write_bytes(target.read_bytes())
            _patch_lstat_metadata(
                monkeypatch,
                candidate,
                file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
            )
    else:
        candidate = tmp_path / "hardlink"
        try:
            os.link(target, candidate)
        except (OSError, NotImplementedError):
            candidate.write_bytes(target.read_bytes())
            _patch_lstat_metadata(monkeypatch, candidate, nlink=2)

    expected_error = {
        "directory": "not a regular file",
        "symlink": "path contains an alias",
        "hardlink": "must not be a hardlink",
    }[kind]
    with pytest.raises(RuntimeProbeError, match=expected_error):
        probe_febio(candidate)


def test_probe_rejects_missing_or_nonexecutable_target(tmp_path: Path) -> None:
    with pytest.raises(RuntimeProbeError):
        probe_febio(tmp_path / "missing")

    candidate = _fake_file(tmp_path, executable=False)
    if os.name != "nt":
        with pytest.raises(RuntimeProbeError, match="executable"):
            probe_febio(candidate)


def test_probe_requires_exact_executable_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _fake_file(tmp_path)
    observed: dict[str, object] = {}

    class CompletedProcess:
        returncode = 0

        def communicate(self, input: bytes, timeout: float) -> tuple[bytes, bytes]:
            observed["input"] = input
            return b"version 4.2.0\n", b""

    def fake_popen(command: object, **kwargs: object) -> CompletedProcess:
        observed["command"] = command
        return CompletedProcess()

    monkeypatch.setattr("febio_cae_harness.solver.runtime.subprocess.Popen", fake_popen)
    diagnostic = probe_febio(executable)

    assert diagnostic.version == "4.2.0"
    assert observed["command"] == [str(executable.absolute())]


def test_windows_launch_claim_rejects_digest_mismatch_before_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "nt":
        pytest.fail("required Windows runtime image test executed on a non-Windows host")

    executable = _fake_file(tmp_path)

    class CompletedProcess:
        returncode = 0

        def communicate(self, input: bytes, timeout: float) -> tuple[bytes, bytes]:
            del input, timeout
            return b"version 4.2.0\n", b""

    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda command, **kwargs: CompletedProcess(),
    )
    diagnostic = probe_febio(executable)
    claim = runtime_module._acquire_runtime_launch_claim(diagnostic)
    assert claim.handle is not None
    original_snapshot = runtime_module._snapshot_from_handle

    def changed_snapshot(path: Path, handle: int) -> object:
        snapshot = original_snapshot(path, handle)
        return replace(snapshot, sha256="0" * 64)

    monkeypatch.setattr(runtime_module, "_snapshot_from_handle", changed_snapshot)
    try:
        with pytest.raises(RuntimeProbeError, match="digest|identity"):
            claim.authenticate(diagnostic.path)
    finally:
        claim.close()


def test_windows_runtime_claim_partial_close_retires_reused_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A partial CRT close must not retry after the descriptor number is reused."""

    if os.name != "nt":
        pytest.fail("required Windows runtime claim test executed on a non-Windows host")

    executable = _fake_file(tmp_path)

    class CompletedProcess:
        returncode = 0

        def communicate(self, input: bytes, timeout: float) -> tuple[bytes, bytes]:
            del input, timeout
            return b"version 4.2.0\n", b""

    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda command, **kwargs: CompletedProcess(),
    )
    claim = runtime_module._acquire_runtime_launch_claim(probe_febio(executable))
    assert claim.handle is not None
    original_close = os.close
    original_open = os.open
    target_fd = claim.handle
    unrelated_path = tmp_path / "unrelated.bin"
    unrelated_fd: int | None = None

    def partial_close(fd: int) -> None:
        nonlocal unrelated_fd
        if fd == target_fd and unrelated_fd is None:
            original_close(fd)
            unrelated_fd = original_open(os.fspath(unrelated_path), os.O_RDWR | os.O_CREAT, 0o600)
            assert unrelated_fd == target_fd
            raise OSError("synthetic close reported failure after retiring the CRT descriptor")
        original_close(fd)

    try:
        with monkeypatch.context() as close_patch:
            close_patch.setattr(os, "close", partial_close)
            with contextlib.suppress(RuntimeProbeError):
                claim.close()

        assert claim.handle is None
        assert unrelated_fd is not None
        os.write(unrelated_fd, b"still-owned-by-test")
    finally:
        if unrelated_fd is not None:
            with contextlib.suppress(OSError):
                original_close(unrelated_fd)
        claim.handle = None


def test_windows_fd_identity_rejects_same_file_descriptor_number_reuse(
    tmp_path: Path,
) -> None:
    """A reused CRT slot for the same path is not the old kernel object."""

    if os.name != "nt":
        pytest.fail("required Windows descriptor identity test executed on a non-Windows host")

    path = tmp_path / "same-file.bin"
    path.write_bytes(b"same-file descriptor reuse")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    original_fd = os.open(os.fspath(path), flags)
    guard = runtime_module._windows_duplicate_fd_handle(original_fd)
    reused_fd: int | None = None
    owner = SimpleNamespace(handle=None, native_handle=guard)
    try:
        os.close(original_fd)
        reused_fd = os.open(os.fspath(path), flags)
        assert reused_fd == original_fd
        owner.handle = reused_fd

        assert runtime_module._windows_fd_identity_matches(reused_fd, guard) is False
        runtime_module._windows_close_owned_fd(owner, "handle", "native_handle")
        assert owner.handle is None
        assert owner.native_handle is None
        os.fstat(reused_fd)
    finally:
        if reused_fd is not None:
            with contextlib.suppress(OSError):
                os.close(reused_fd)
        if owner.native_handle is not None:
            with contextlib.suppress(OSError):
                runtime_module._windows_close_native_handle(owner.native_handle)


def test_windows_runtime_open_duplicates_raw_handle_before_crt_transfer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CreateFileW H1 must be guarded by DuplicateHandle H2 before open_osfhandle."""

    if os.name != "nt":
        pytest.fail("required Windows runtime acquisition test executed on a non-Windows host")

    import msvcrt

    executable = _fake_file(tmp_path)
    events: list[tuple[str, int]] = []
    original_duplicate = runtime_module._windows_duplicate_native_handle
    original_open_osfhandle = msvcrt.open_osfhandle
    claim_fd: int | None = None

    def duplicate(raw_handle: int) -> int:
        events.append(("duplicate", raw_handle))
        return original_duplicate(raw_handle)

    def open_osfhandle(raw_handle: int, descriptor_flags: int) -> int:
        events.append(("open_osfhandle", int(raw_handle)))
        return original_open_osfhandle(raw_handle, descriptor_flags)

    monkeypatch.setattr(
        runtime_module,
        "_windows_duplicate_native_handle",
        duplicate,
        raising=False,
    )
    monkeypatch.setattr(msvcrt, "open_osfhandle", open_osfhandle)
    try:
        claim_fd = runtime_module._windows_open_runtime_claim(executable)
        assert events[:2] == [
            ("duplicate", events[0][1]),
            ("open_osfhandle", events[0][1]),
        ]
        assert getattr(claim_fd, "native_handle", None) is not None
    finally:
        if claim_fd is not None:
            native_handle = getattr(claim_fd, "native_handle", None)
            with contextlib.suppress(OSError):
                os.close(int(claim_fd))
            if native_handle is not None:
                with contextlib.suppress(OSError):
                    runtime_module._windows_close_native_handle(native_handle)


def test_windows_runtime_validation_failure_before_close_retains_exact_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pre-close failure keeps the exact runtime claim for a later retry."""

    if os.name != "nt":
        pytest.fail("required Windows runtime acquisition test executed on a non-Windows host")

    executable = _fake_file(tmp_path)

    class CompletedProcess:
        returncode = 0

        def communicate(self, input: bytes, timeout: float) -> tuple[bytes, bytes]:
            del input, timeout
            return b"version 4.2.0\n", b""

    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda command, **kwargs: CompletedProcess(),
    )
    diagnostic = probe_febio(executable)
    opened: list[int] = []
    original_open = runtime_module._windows_open_runtime_claim

    def capture_open(path: Path) -> int:
        claim_fd = original_open(path)
        opened.append(claim_fd)
        return claim_fd

    monkeypatch.setattr(runtime_module, "_windows_open_runtime_claim", capture_open)
    monkeypatch.setattr(
        runtime_module,
        "_snapshot_from_handle",
        lambda path, handle: (_ for _ in ()).throw(
            RuntimeProbeError("synthetic launch validation failure")
        ),
    )
    close_attempts: list[int] = []
    original_close = os.close
    fail_close = True

    def fail_before_close(fd: int) -> None:
        if opened and fd == int(opened[0]) and fail_close:
            close_attempts.append(fd)
            raise OSError("synthetic CRT close failed before closing")
        original_close(fd)

    try:
        with monkeypatch.context() as close_patch:
            close_patch.setattr(os, "close", fail_before_close)
            with pytest.raises(RuntimeProbeError, match="validation"):
                runtime_module._acquire_runtime_launch_claim(diagnostic)

        assert opened
        target_fd = int(opened[0])
        assert close_attempts == [target_fd]
        durable = getattr(runtime_module, "_RUNTIME_DURABLE_CLAIMS", ())
        assert any(getattr(claim, "handle", None) == target_fd for claim in durable)

        fail_close = False
        runtime_module._drain_runtime_claims()
        with pytest.raises(OSError):
            os.fstat(target_fd)
        assert not any(getattr(claim, "handle", None) == target_fd for claim in durable)
    finally:
        fail_close = False
        with contextlib.suppress(BaseException):
            runtime_module._drain_runtime_claims()


def test_windows_runtime_validation_failure_partial_close_retires_reused_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A partial CRT close retires a same-file reused slot before guard cleanup."""

    if os.name != "nt":
        pytest.fail("required Windows runtime acquisition test executed on a non-Windows host")

    executable = _fake_file(tmp_path)

    class CompletedProcess:
        returncode = 0

        def communicate(self, input: bytes, timeout: float) -> tuple[bytes, bytes]:
            del input, timeout
            return b"version 4.2.0\n", b""

    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda command, **kwargs: CompletedProcess(),
    )
    diagnostic = probe_febio(executable)
    opened: list[int] = []
    original_open = runtime_module._windows_open_runtime_claim

    def capture_open(path: Path) -> int:
        claim_fd = original_open(path)
        opened.append(claim_fd)
        return claim_fd

    monkeypatch.setattr(runtime_module, "_windows_open_runtime_claim", capture_open)
    monkeypatch.setattr(
        runtime_module,
        "_snapshot_from_handle",
        lambda path, handle: (_ for _ in ()).throw(
            RuntimeProbeError("synthetic launch validation failure")
        ),
    )
    events: list[str] = []
    original_close = os.close
    original_open_fd = os.open
    original_close_native = runtime_module._windows_close_native_handle
    reused_fd: int | None = None

    def partial_close(fd: int) -> None:
        nonlocal reused_fd
        if opened and fd == int(opened[0]) and reused_fd is None:
            events.append("crt")
            original_close(fd)
            reused_fd = original_open_fd(
                os.fspath(executable), os.O_RDONLY | getattr(os, "O_BINARY", 0)
            )
            assert reused_fd == fd
            raise OSError("synthetic CRT close reported failure after retiring the slot")
        original_close(fd)

    def close_native(native_handle: int) -> None:
        events.append("native")
        original_close_native(native_handle)

    monkeypatch.setattr(runtime_module, "_windows_close_native_handle", close_native)
    try:
        with monkeypatch.context() as close_patch:
            close_patch.setattr(os, "close", partial_close)
            with pytest.raises(RuntimeProbeError, match="validation"):
                runtime_module._acquire_runtime_launch_claim(diagnostic)

        assert events[:2] == ["crt", "native"]
        assert reused_fd is not None
        os.fstat(reused_fd)
    finally:
        if reused_fd is not None:
            with contextlib.suppress(OSError):
                original_close(reused_fd)
        with contextlib.suppress(BaseException):
            runtime_module._drain_runtime_claims()


@pytest.mark.parametrize("failure", ["duplicate", "open_osfhandle"])
def test_windows_runtime_conversion_failure_retains_exact_raw_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """DuplicateHandle/open_osfhandle failures cannot strand or lose H1/H2 ownership."""

    if os.name != "nt":
        pytest.fail("required Windows runtime conversion test executed on a non-Windows host")

    import msvcrt

    executable = _fake_file(tmp_path)
    captured: list[int] = []
    result: int | None = None
    original_duplicate = runtime_module._windows_duplicate_native_handle

    def fail_duplicate(raw_handle: int) -> int:
        captured.append(int(raw_handle))
        raise OSError("synthetic DuplicateHandle failure")

    def fail_open(raw_handle: int, descriptor_flags: int) -> int:
        captured.append(int(raw_handle))
        raise OSError("synthetic open_osfhandle failure")

    def fail_close(native_handle: int) -> None:
        del native_handle
        raise OSError("synthetic CloseHandle failure")

    if failure == "duplicate":
        monkeypatch.setattr(
            runtime_module,
            "_windows_duplicate_native_handle",
            fail_duplicate,
            raising=False,
        )
    else:
        monkeypatch.setattr(
            runtime_module,
            "_windows_duplicate_native_handle",
            lambda raw_handle: original_duplicate(raw_handle),
            raising=False,
        )
        monkeypatch.setattr(msvcrt, "open_osfhandle", fail_open)
    monkeypatch.setattr(runtime_module, "_windows_close_native_handle", fail_close)

    try:
        with pytest.raises(OSError, match="failure"):
            result = runtime_module._windows_open_runtime_claim(executable)
        assert captured
        durable = getattr(runtime_module, "_RUNTIME_DURABLE_CLAIMS", ())
        assert any(getattr(claim, "raw_handle", None) in captured for claim in durable)
    finally:
        if result is not None:
            with contextlib.suppress(OSError):
                os.close(result)
        with contextlib.suppress(BaseException):
            runtime_module._drain_runtime_claims()


def test_probe_rejects_arbitrary_runner_arguments(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        probe_febio(tmp_path / "missing", runner_arguments=("--arbitrary",))  # type: ignore[call-arg]


def test_windows_concurrent_durable_raw_handle_drains_are_serialized_and_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent drains cannot close one exact raw handle at the same time."""

    if os.name != "nt":
        pytest.fail("required Windows durable raw-handle test executed on a non-Windows host")

    raw_handle = 707070
    attempts: list[int] = []
    state_lock = threading.Lock()
    rendezvous = threading.Barrier(2)
    active = 0
    max_active = 0
    fail = True

    def close_native(handle: int) -> None:
        nonlocal active, max_active
        with state_lock:
            attempts.append(handle)
            active += 1
            max_active = max(max_active, active)
        try:
            if fail:
                with contextlib.suppress(threading.BrokenBarrierError):
                    rendezvous.wait(timeout=0.5)
                raise OSError("synthetic transient raw-handle close failure")
        finally:
            with state_lock:
                active -= 1

    owner = runtime_module._WindowsHandleOwner(
        raw_handle=raw_handle,
        close_native_handle=close_native,
    )
    durable = runtime_module._RUNTIME_DURABLE_CLAIMS
    prior_claims = list(durable)
    durable[:] = [owner]
    threads = [threading.Thread(target=runtime_module._drain_runtime_claims) for _ in range(2)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3.0)
            assert not thread.is_alive()

        assert max_active == 1
        assert attempts
        assert all(handle == raw_handle for handle in attempts)
        assert owner.raw_handle == raw_handle
        assert any(held is owner for held in durable)

        fail = False
        runtime_module._drain_runtime_claims()
        assert owner.raw_handle is None
        assert not any(held is owner for held in durable)
        assert attempts[-1] == raw_handle
    finally:
        durable[:] = [held for held in prior_claims if held is not owner]
