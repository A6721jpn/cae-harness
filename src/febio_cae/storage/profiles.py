"""Registered compatibility profiles stored outside individual case roots."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from febio_cae.domain.codec import decode_record, encode_record
from febio_cae.domain.compatibility import CompatibilityProfile
from febio_cae.domain.ports import PortError, PortErrorCategory


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=30.0, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


class SQLiteCompatibilityRegistry:
    """A small explicit registry; missing profiles remain unsupported."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path).absolute()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _connect(self.path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS profiles(profile_id TEXT PRIMARY KEY, payload BLOB NOT NULL)"
            )

    def register(self, profile: CompatibilityProfile) -> CompatibilityProfile:
        payload = encode_record(profile)
        with _connect(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR REPLACE INTO profiles(profile_id,payload) VALUES(?,?)",
                (profile.profile_id, payload),
            )
            connection.commit()
        return profile

    def get_profile(self, profile_id: str) -> CompatibilityProfile:
        with _connect(self.path) as connection:
            row = connection.execute(
                "SELECT payload FROM profiles WHERE profile_id=?", (profile_id,)
            ).fetchone()
        if row is None:
            raise PortError(
                PortErrorCategory.UNSUPPORTED_CAPABILITY,
                f"compatibility profile {profile_id!r} is not registered",
            )
        try:
            return decode_record(bytes(row["payload"]), CompatibilityProfile)
        except Exception as error:
            raise PortError(
                PortErrorCategory.INTEGRITY,
                f"registered compatibility profile {profile_id!r} is corrupt",
            ) from error


__all__ = ["SQLiteCompatibilityRegistry"]
