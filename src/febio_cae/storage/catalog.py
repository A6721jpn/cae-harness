"""The durable case-id to registered-root catalog."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path


class CaseCatalogError(RuntimeError):
    """Raised when a case cannot be registered or resolved."""


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def validate_case_id(value: object) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise CaseCatalogError("case_id must be a safe registered identifier")
    if value in {".", ".."} or "/" in value or "\\" in value or ":" in value:
        raise CaseCatalogError("case_id must not contain path syntax")
    return value


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=30.0, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


class CaseCatalog:
    """Persist case roots; callers never resolve an arbitrary ID as a path."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path).absolute()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _connect(self.path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS cases (
                    case_id TEXT PRIMARY KEY,
                    case_root TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                )
                """
            )

    def register(self, case_id: str, case_root: Path, created_at: str) -> None:
        validate_case_id(case_id)
        root = case_root.absolute()
        with _connect(self.path) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO cases(case_id,case_root,created_at) VALUES(?,?,?)",
                    (case_id, str(root), created_at),
                )
                connection.commit()
            except sqlite3.IntegrityError as error:
                connection.rollback()
                raise CaseCatalogError("case ID or case root is already registered") from error
            except Exception:
                connection.rollback()
                raise

    def resolve(self, case_id: str) -> Path:
        validate_case_id(case_id)
        with _connect(self.path) as connection:
            row = connection.execute(
                "SELECT case_root FROM cases WHERE case_id=?", (case_id,)
            ).fetchone()
        if row is None:
            raise CaseCatalogError(f"unknown registered case: {case_id}")
        root = Path(str(row["case_root"])).absolute()
        if not root.is_dir() or root.is_symlink():
            raise CaseCatalogError("registered case root is unavailable or is a link")
        return root


__all__ = ["CaseCatalog", "CaseCatalogError", "validate_case_id"]
