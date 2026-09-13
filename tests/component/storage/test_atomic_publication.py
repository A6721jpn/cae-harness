"""Focused Windows atomic-publication boundary checks."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from febio_cae.storage.registry import _write_atomic


@pytest.mark.skipif(os.name != "nt", reason="requires Windows path semantics")
def test_atomic_publication_keeps_long_valid_target_component(tmp_path: Path) -> None:
    root = tmp_path / "atomic-root"
    root.mkdir()
    extended_root = Path(rf"\\?\{root}")
    relative = "a" * 240
    payload = b"published through the bounded Windows path"

    target = _write_atomic(extended_root, relative, payload, token="123456789012")

    assert target.name == relative
    assert target.read_bytes() == payload


@pytest.mark.skipif(os.name != "nt", reason="requires Windows path semantics")
def test_atomic_publication_collision_preserves_foreign_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "atomic-root"
    root.mkdir()
    original_open = Path.open
    collision: Path | None = None

    def collide(path: Path, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        nonlocal collision
        if mode == "xb":
            collision = path
            with original_open(path, "wb") as handle:
                handle.write(b"foreign temporary")
            return original_open(path, mode, *args, **kwargs)
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", collide)
    with pytest.raises(FileExistsError):
        _write_atomic(root, "artifact.bin", b"intended", token="collision")

    assert collision is not None
    with original_open(collision, "rb") as handle:
        assert handle.read() == b"foreign temporary"
    assert not (root / "artifact.bin").exists()
