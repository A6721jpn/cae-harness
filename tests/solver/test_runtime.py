from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

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


def test_probe_rejects_arbitrary_runner_arguments(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        probe_febio(tmp_path / "missing", runner_arguments=("--arbitrary",))  # type: ignore[call-arg]
