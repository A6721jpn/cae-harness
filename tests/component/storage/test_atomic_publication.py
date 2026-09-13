"""Focused Windows atomic-publication boundary checks."""

from __future__ import annotations

import os
import shutil
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
def test_atomic_publication_replaces_valid_target_near_path_limit(tmp_path: Path) -> None:
    root = tmp_path / ("p" * (239 - len(str(tmp_path))))
    assert len(str(root)) == 240
    root.mkdir()
    target = root / "state.json"
    target.write_bytes(b"original")

    published = _write_atomic(root, target.name, b"replacement", token="boundary01")

    assert published == target
    assert target.read_bytes() == b"replacement"


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


@pytest.mark.skipif(os.name != "nt", reason="requires Windows path semantics")
def test_atomic_publication_preserves_distinct_literal_alias_directory(tmp_path: Path) -> None:
    owned = tmp_path / "owned"
    owned.mkdir()
    alias = tmp_path / "owned."
    literal = Path(rf"\\?\{alias}")
    literal.mkdir()
    try:
        assert os.path.samefile(owned, alias)
        assert not os.path.samefile(owned, literal)
        foreign = literal / "foreign.bin"
        foreign.write_bytes(b"foreign")
        target = owned / "state.json"
        target.write_bytes(b"original")
        os.utime(literal, ns=(10**9, 10**9))
        untouched_time = literal.stat().st_mtime_ns

        _write_atomic(alias, target.name, b"replacement", token="boundary01")

        assert target.read_bytes() == b"replacement"
        assert foreign.read_bytes() == b"foreign"
        assert literal.stat().st_mtime_ns == untouched_time
    finally:
        shutil.rmtree(literal)
