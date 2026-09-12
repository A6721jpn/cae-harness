"""Case-wide native reservations; failed and interrupted starts are never refunded."""

from __future__ import annotations

from febio_cae.domain import CaseRevision

from ._sqlite import connect
from .registry import CaseStorage, StorageConflictError, _now, _safe_identifier

_LIMITS = {"gmsh": 0, "febio": 2, "studio": 1}


def _reserve(storage: CaseStorage, case_id: str, kind: str, operation_id: str, limit: int) -> bool:
    """Durably consume one start; False means already reserved, never retry permission.

    No revision key or caller-selected cohort is accepted, so reconstruction,
    failed runs and new revisions cannot allocate a fresh allowance. A crash
    after commit and before launch conservatively consumes its reservation.
    """
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
        if count >= limit:
            raise StorageConflictError(f"registered {kind} budget exhausted")
        connection.execute(
            "INSERT INTO prototype_demo_starts VALUES(?,?,?,?)",
            (case_id, kind, operation_id, _now()),
        )
        connection.commit()
        return True


def reserve_demo_attempt(storage: CaseStorage, case_id: str, kind: str, operation_id: str) -> bool:
    """Keep the original registered demo's fixed, case-wide allowance."""
    if kind not in _LIMITS:
        raise ValueError("unknown prototype demo native operation")
    return _reserve(storage, case_id, kind, operation_id, _LIMITS[kind])


def reserve_preparation_mesh_attempt(storage: CaseStorage, case_id: str, operation_id: str) -> bool:
    """Reserve one of the three public preparation generations before spawning."""
    return _reserve(storage, case_id, "gmsh", operation_id, 3)


def reserve_prepared_solver_attempt(
    storage: CaseStorage, revision: CaseRevision, operation_id: str
) -> bool:
    """Bound the prepared study to four starts and the unchanged declared attempt cap."""
    from .mesh_quality import PlanarPreparationRegistration

    with storage.transaction():
        if storage.get_revision(revision.case_id, revision.revision_id) != revision:
            raise StorageConflictError("prepared solver revision differs from registration")
        if not isinstance(
            storage.resolve_revision_mesh_quality(revision), PlanarPreparationRegistration
        ):
            raise StorageConflictError("prepared solver budget requires a generated origin")
        return _reserve(
            storage,
            revision.case_id,
            "febio",
            operation_id,
            min(4, revision.spec.budget.max_attempts),
        )
