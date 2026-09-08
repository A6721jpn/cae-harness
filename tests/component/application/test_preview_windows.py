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
