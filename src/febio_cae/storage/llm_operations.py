"""Case-owned durable LLM operation claims, reservations and exact re-entry."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from febio_cae.application.intent_contracts import strict_json
from febio_cae.domain.canonical import canonical_bytes

from .registry import CaseStorage, StorageConflictError, StorageIntegrityError, _connect


class LLMOperations:
    def __init__(self, storage: CaseStorage) -> None:
        self.storage = storage
        with storage.transaction(), _connect(storage.registry_path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS llm_operations (operation_id TEXT PRIMARY KEY, identity TEXT NOT NULL, payload BLOB NOT NULL)"
            )
            connection.commit()

    def get(self, operation_id: str) -> dict[str, Any] | None:
        with self.storage.transaction(), _connect(self.storage.registry_path) as connection:
            row = connection.execute(
                "SELECT payload FROM llm_operations WHERE operation_id=?", (operation_id,)
            ).fetchone()
            if row is None:
                return None
            value = strict_json(bytes(row["payload"]))
            if not isinstance(value, dict) or value.get("operation_id") != operation_id:
                raise StorageIntegrityError("LLM operation journal is corrupt")
            return value

    def start(
        self, operation_id: str, identity: str, entry: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]:
        with self.storage.transaction(), _connect(self.storage.registry_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT identity,payload FROM llm_operations WHERE operation_id=?", (operation_id,)
            ).fetchone()
            if row is not None:
                if row["identity"] != identity:
                    raise StorageConflictError("operation ID already binds another payload")
                return False, strict_json(bytes(row["payload"]))
            value = {
                **entry,
                "operation_id": operation_id,
                "identity": identity,
                "state": "RESERVED",
                "attempted_requests": 0,
            }
            connection.execute(
                "INSERT INTO llm_operations VALUES(?,?,?)",
                (operation_id, identity, canonical_bytes(value)),
            )
            connection.commit()
            return True, value

    def update(self, operation_id: str, **changes: Any) -> dict[str, Any]:
        with self.storage.transaction(), _connect(self.storage.registry_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM llm_operations WHERE operation_id=?", (operation_id,)
            ).fetchone()
            if row is None:
                raise StorageIntegrityError("missing LLM operation claim")
            value = strict_json(bytes(row["payload"]))
            if "state" in changes:
                value.setdefault("transitions", []).append(
                    {
                        "from": value["state"],
                        "to": changes["state"],
                        "utc": datetime.now(UTC).isoformat(),
                    }
                )
            value.update(changes)
            connection.execute(
                "UPDATE llm_operations SET payload=? WHERE operation_id=?",
                (canonical_bytes(value), operation_id),
            )
            connection.commit()
            return value
