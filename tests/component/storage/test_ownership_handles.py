"""Focused Windows ownership-handle lifetime checks."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path

import pytest

from febio_cae.storage import _ownership


@pytest.mark.skipif(os.name != "nt", reason="requires Windows ownership handles")
def test_pinned_read_reuses_loaded_win32_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "owned" / "source.bin"
    path.parent.mkdir()
    path.write_bytes(b"owned bytes")

    with _ownership.pinned_read(path) as stream:
        assert stream.read() == b"owned bytes"

    def unavailable(*args: object, **kwargs: object) -> object:
        raise AssertionError("Win32 loader is unavailable after process initialization")

    monkeypatch.setattr(ctypes, "WinDLL", unavailable)
    with _ownership.pinned_read(path) as stream:
        assert stream.read() == b"owned bytes"
