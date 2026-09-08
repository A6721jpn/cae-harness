"""Pin SQLite identities and reject aliases at connection/statement entry.

Windows permits link creation while these handles are held. This is not an
isolation boundary against aliases introduced inside a native SQLite call;
that remaining storage-commit design issue is explicitly reported to the PM.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

from ._ownership import _open, identity, lease, pin_directories, pinned_read


class _Connection(sqlite3.Connection):
    verify_files: Callable[[], None]

    def execute(self, *args: Any, **kwargs: Any) -> sqlite3.Cursor:
        self.verify_files()
        return super().execute(*args, **kwargs)

    def executemany(self, *args: Any, **kwargs: Any) -> sqlite3.Cursor:
        self.verify_files()
        return super().executemany(*args, **kwargs)

    def executescript(self, *args: Any, **kwargs: Any) -> sqlite3.Cursor:
        self.verify_files()
        return super().executescript(*args, **kwargs)

    def commit(self) -> None:
        self.verify_files()
        super().commit()


@contextmanager
def _pin_file(path: Path) -> Iterator[int]:
    descriptor = _open(path, writable=True)
    try:
        info = os.fstat(descriptor)
        if info.st_nlink != 1 or (info.st_dev, info.st_ino) != identity(path):
            raise OSError(f"SQLite file is aliased or substituted: {path}")
        yield descriptor
    finally:
        os.close(descriptor)


def _bind_identity(path: Path) -> None:
    anchor = path.with_name(path.name + ".identity")
    expected = repr(identity(path)).encode("ascii")
    if not anchor.exists():
        # Only first adoption needs serialization; ordinary live openers do not
        # wait for the publication lease just to inspect their database.
        with lease(path.parent):
            if not anchor.exists():
                with anchor.open("xb") as stream:
                    stream.write(expected)
                    stream.flush()
                    os.fsync(stream.fileno())
    with pinned_read(anchor) as stream:
        if stream.read() != expected:
            raise OSError("SQLite database identity differs from registered file")


@contextmanager
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    path = path.absolute()
    with pin_directories(path.parent), ExitStack() as pins:
        descriptor = pins.enter_context(_pin_file(path))
        descriptors = [(path, descriptor, identity(path))]
        _bind_identity(path)
        header = os.read(descriptor, 20)
        # New databases use persistent rollback journals, so first schema writes
        # need not unlink a pinned journal. Existing WAL databases stay WAL.
        # Both modes retain FULL synchronous durability and SQLite locking.
        mode = "WAL" if header[18:20] == b"\x02\x02" else "PERSIST"
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = Path(str(path) + suffix)
            fd = pins.enter_context(_pin_file(sidecar))
            descriptors.append((sidecar, fd, identity(sidecar)))

        def verify() -> None:
            for target, fd, expected in descriptors:
                info = os.fstat(fd)
                if (
                    info.st_nlink != 1
                    or (info.st_dev, info.st_ino) != expected
                    or identity(target) != expected
                ):
                    raise OSError("SQLite file identity/link count changed during connection")

        verify()
        connection = sqlite3.connect(path, timeout=30.0, isolation_level=None, factory=_Connection)
        connection.verify_files = verify
        try:
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA journal_mode={mode}")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=30000")
            yield connection
        finally:
            # File pins outlive close/checkpoint and are then released. SQLite
            # may retain its owned empty sidecars instead of unlinking them.
            connection.close()
