"""Private prototype-demo-v1 admission ledger; reservations are never refunded.

This is not a general user-budget configuration API. Its fixed limits reflect
the explicit demo decision: GM03 exhausted mesh allocation, two solver starts,
one Studio start. Every native launcher must reserve against the same registered
case root before launch and must not launch when the return value is False.
"""

from __future__ import annotations

from ._sqlite import connect
from .registry import CaseStorage, StorageConflictError, _now, _safe_identifier

_LIMITS = {"gmsh": 0, "febio": 2, "studio": 1}


def reserve_demo_attempt(storage: CaseStorage, case_id: str, kind: str, operation_id: str) -> bool:
    """Durably consume one start; False means already reserved, never retry permission.

    No revision key or caller-selected cohort is accepted, so reconstruction,
    failed runs and new revisions cannot allocate a fresh allowance. A crash
    after commit and before launch conservatively consumes its reservation.
    """
    if kind not in _LIMITS:
        raise ValueError("unknown prototype demo native operation")
    _safe_identifier(operation_id, "operation_id")
    with storage.transaction(), connect(storage.registry_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        storage._case_row(connection, case_id)
        connection.execute(
            """CREATE TABLE IF NOT EXISTS prototype_demo_starts (
                case_id TEXT NOT NULL REFERENCES case_state(case_id),
                kind TEXT NOT NULL,
                operation_id TEXT NOT NULL,
                reserved_at TEXT NOT NULL,
                PRIMARY KEY(case_id,kind,operation_id)
            )"""
        )
        if connection.execute(
            "SELECT 1 FROM prototype_demo_starts WHERE case_id=? AND kind=? AND operation_id=?",
            (case_id, kind, operation_id),
        ).fetchone():
            connection.commit()
            return False
        count = connection.execute(
            "SELECT COUNT(*) FROM prototype_demo_starts WHERE case_id=? AND kind=?",
            (case_id, kind),
        ).fetchone()[0]
        if count >= _LIMITS[kind]:
            raise StorageConflictError(f"prototype demo {kind} budget exhausted")
        connection.execute(
            "INSERT INTO prototype_demo_starts VALUES(?,?,?,?)",
            (case_id, kind, operation_id, _now()),
        )
        connection.commit()
        return True
