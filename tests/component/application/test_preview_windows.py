"""Passive Windows queries only; never opens or operates a Studio window."""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from typing import Any

import pytest


def test_actual_current_process_identity_is_stable() -> None:
    from febio_cae.application._preview_windows import process_snapshot

    first = process_snapshot(os.getpid())
    second = process_snapshot(os.getpid())
    assert first == second
    assert first.process_id == os.getpid()
    assert first.executable == Path(sys.executable).absolute()
    assert first.executable_digest == hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()
    assert first.version and first.start_marker.startswith("windows-filetime:")


def test_window_probe_observes_actual_snapshot_not_expected_echo(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from febio_cae.application import _preview_windows as module

    executable = tmp_path / "FEBioStudio.exe"
    calls: list[int] = []
    start = ["windows-filetime:111"]

    def snapshot(pid: int) -> Any:
        calls.append(pid)
        return module.ProcessSnapshot(pid, start[0], executable, "3.1.0", "a" * 64)

    monkeypatch.setattr(module, "_window_pid", lambda hwnd: 123)
    monkeypatch.setattr(module, "process_snapshot", snapshot)
    probe = module.WindowsStudioProbe(executable)
    expected = probe.identify(321)
    assert expected.process_id == 123 and expected.window_id == 321
    assert probe(expected) == expected
    start[0] = "windows-filetime:222"
    assert probe(expected) != expected
    assert calls == [123, 123, 123]


def test_probe_rejects_changed_window_and_wrong_executable(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from febio_cae.application import _preview_windows as module

    executable = tmp_path / "FEBioStudio.exe"
    monkeypatch.setattr(module, "_window_pid", lambda hwnd: 123)
    monkeypatch.setattr(
        module,
        "process_snapshot",
        lambda pid: module.ProcessSnapshot(
            pid,
            "windows-filetime:111",
            tmp_path / "different.exe",
            "3.1.0",
            "a" * 64,
        ),
    )
    with pytest.raises(ValueError):
        module.WindowsStudioProbe(executable).identify(321)
    monkeypatch.setattr(
        module,
        "process_snapshot",
        lambda pid: module.ProcessSnapshot(
            pid,
            "windows-filetime:111",
            executable,
            "3.1.0",
            "a" * 64,
        ),
    )
    pids = iter((123, 456))
    monkeypatch.setattr(module, "_window_pid", lambda hwnd: next(pids))
    with pytest.raises(OSError):
        module.WindowsStudioProbe(executable).identify(321)


def test_missing_pe_version_uses_verified_runtime_window_title(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from febio_cae.application import _preview_windows as module

    executable = tmp_path / "FEBioStudio.exe"
    monkeypatch.setattr(module, "_window_pid", lambda hwnd: 123)
    monkeypatch.setattr(module, "_window_title", lambda hwnd: "FEBio Studio 3.1.0", raising=False)
    monkeypatch.setattr(
        module,
        "process_snapshot",
        lambda pid: module.ProcessSnapshot(
            pid,
            "windows-filetime:111",
            executable,
            None,
            "a" * 64,
        ),
    )
    probe = module.WindowsStudioProbe(executable)
    session = probe.identify(321)
    assert session.studio.version == "3.1.0"
    assert probe.version_evidence is not None
    assert probe.version_evidence["source"] == "runtime-window-title"
    assert probe.version_evidence["window_title"] == "FEBio Studio 3.1.0"
    assert probe.version_evidence["process_start_marker"] == session.process_start_marker


@pytest.mark.parametrize(
    "title",
    [
        "",
        "Untitled",
        "document FEBio Studio 3.1.0",
        "FEBio Studio unknown",
        "FEBio Studio 3.1.0 modified",
    ],
)
def test_absent_pe_rejects_unknown_or_malformed_runtime_version(
    tmp_path: Path, monkeypatch: Any, title: str
) -> None:
    from febio_cae.application import _preview_windows as module

    executable = tmp_path / "FEBioStudio.exe"
    monkeypatch.setattr(module, "_window_pid", lambda hwnd: 123)
    monkeypatch.setattr(module, "_window_title", lambda hwnd: title, raising=False)
    monkeypatch.setattr(
        module,
        "process_snapshot",
        lambda pid: module.ProcessSnapshot(
            pid,
            "windows-filetime:111",
            executable,
            None,
            "a" * 64,
        ),
    )
    with pytest.raises(ValueError):
        module.WindowsStudioProbe(executable).identify(321)


@pytest.mark.parametrize("changed", ["process", "title"])
def test_runtime_fallback_rechecks_same_process_and_title(
    tmp_path: Path, monkeypatch: Any, changed: str
) -> None:
    from febio_cae.application import _preview_windows as module

    executable = tmp_path / "FEBioStudio.exe"
    snapshots = iter(
        (
            "windows-filetime:111",
            "windows-filetime:222" if changed == "process" else "windows-filetime:111",
        )
    )
    titles = iter(
        ("FEBio Studio 3.1.0", "FEBio Studio 3.2.0" if changed == "title" else "FEBio Studio 3.1.0")
    )
    monkeypatch.setattr(module, "_window_pid", lambda hwnd: 123)
    monkeypatch.setattr(module, "_window_title", lambda hwnd: next(titles), raising=False)
    monkeypatch.setattr(
        module,
        "process_snapshot",
        lambda pid: module.ProcessSnapshot(
            pid,
            next(snapshots),
            executable,
            None,
            "a" * 64,
        ),
    )
    with pytest.raises(OSError):
        module.WindowsStudioProbe(executable).identify(321)


def test_only_absent_pe_resource_becomes_optional(monkeypatch: Any) -> None:
    import ctypes
    from types import SimpleNamespace

    from febio_cae.application import _preview_windows as module

    error = [1813]

    def size(*args: Any) -> int:
        ctypes.set_last_error(error[0])
        return 0

    fake = SimpleNamespace(
        GetFileVersionInfoSizeW=size, GetFileVersionInfoW=lambda *a: 0, VerQueryValueW=lambda *a: 0
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: fake)
    assert module._file_version(Path("unused.exe")) is None
    error[0] = 5  # access denied is not a missing resource fallback
    with pytest.raises(OSError):
        module._file_version(Path("unused.exe"))
