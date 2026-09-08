"""The durable case-id to registered-root catalog."""

from __future__ import annotations

import re
import sqlite3
import stat
from pathlib import Path

from ._ownership import identity, pin_directories
from ._sqlite import connect as _connect


class CaseCatalogError(RuntimeError):
    """Raised when a case cannot be registered or resolved."""


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def validate_case_id(value: object) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise CaseCatalogError("case_id must be a safe registered identifier")
    if value in {".", ".."} or "/" in value or "\\" in value or ":" in value:
        raise CaseCatalogError("case_id must not contain path syntax")
    return value


def _is_reparse(path: Path) -> bool:
    try:
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            return True
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    except FileNotFoundError:
        return False


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
            columns = {row[1] for row in connection.execute("PRAGMA table_info(cases)")}
            if "root_identity" not in columns:
                connection.execute("ALTER TABLE cases ADD COLUMN root_identity TEXT")

    def register(self, case_id: str, case_root: Path, created_at: str) -> None:
        validate_case_id(case_id)
        root = case_root.absolute()
        if not root.is_dir() or _is_reparse(root):
            raise CaseCatalogError("registered case root is unavailable or is a reparse point")
        with pin_directories(root), _connect(self.path) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO cases(case_id,case_root,created_at,root_identity) VALUES(?,?,?,?)",
                    (case_id, str(root), created_at, repr(identity(root))),
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
                "SELECT case_root,root_identity FROM cases WHERE case_id=?", (case_id,)
            ).fetchone()
        if row is None:
            raise CaseCatalogError(f"unknown registered case: {case_id}")
        root = Path(str(row["case_root"])).absolute()
        if not root.is_dir() or _is_reparse(root):
            raise CaseCatalogError(
                "registered case root is unavailable or is a link or reparse point"
            )
        try:
            with pin_directories(root):
                if row["root_identity"] != repr(identity(root)):
                    raise CaseCatalogError("registered root identity changed or requires migration")
        except OSError as error:
            raise CaseCatalogError(str(error)) from error
        return root


__all__ = ["CaseCatalog", "CaseCatalogError", "validate_case_id"]
