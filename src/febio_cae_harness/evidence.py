"""Append-only evidence and state persistence for a validated case.

The store keeps intent values opaque and records only authoritative data.  It
does not inspect models, infer physical conditions, or run a solver.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .contracts import IntentContract
from .workspace import CaseWorkspace, WorkspaceBoundaryError

__all__ = ["EvidenceIntegrityError", "EvidenceStore"]


MANIFEST_FILE = "CASE_MANIFEST.json"
INTENT_FILE = "intent.json"
EVENTS_FILE = "90_Temporary/events.jsonl"
ATTEMPT_FILE = "ATTEMPT.json"
SCHEMA_VERSION = 1


class EvidenceIntegrityError(RuntimeError):
    """Raised when persisted evidence is missing, changed, or inconsistent."""


def _canonical_bytes(value: object) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as error:
        raise TypeError("evidence values must be JSON serializable") from error
    return encoded.encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise EvidenceIntegrityError(f"cannot read evidence file: {path}") from error
    return digest.hexdigest()


def _json_text(value: object) -> str:
    return _canonical_bytes(value).decode("utf-8") + "\n"


def _as_mapping(value: object, description: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceIntegrityError(f"{description} must be a JSON object")
    return value


def _validate_segment(value: str, label: str) -> None:
    if not isinstance(value, str) or not value or value in {".", ".."}:
        raise ValueError(f"{label} must be a non-empty path segment")
    if value != value.strip() or "\x00" in value or "/" in value or "\\" in value:
        raise ValueError(f"{label} must be a single path segment")


class EvidenceStore:
    """Persist intent, events, attempts, artifacts, and a manifest projection."""

    def __init__(self, case_workspace: CaseWorkspace, intent: IntentContract | None = None) -> None:
        self.case_workspace = case_workspace
        self._intent: IntentContract
        self._artifacts: dict[str, dict[str, object]] = {}
        self._case_sha256 = ""

        if self._has_persisted_state():
            self._load_and_validate(intent)
        elif intent is None:
            raise ValueError("intent is required when creating a new evidence store")
        else:
            self._initialize(intent)

    @classmethod
    def open(cls, case_workspace: CaseWorkspace) -> EvidenceStore:
        """Open and validate an existing store without changing it."""

        return cls(case_workspace)

    @property
    def manifest_path(self) -> Path:
        return self._safe_case_path(MANIFEST_FILE)

    @property
    def intent_path(self) -> Path:
        return self._safe_case_path(INTENT_FILE)

    @property
    def events_path(self) -> Path:
        return self._safe_case_path(EVENTS_FILE)

    @property
    def intent(self) -> IntentContract:
        self.reopen()
        return self._intent

    @property
    def case_sha256(self) -> str:
        self.reopen()
        return self._case_sha256

    @property
    def manifest(self) -> dict[str, Any]:
        self.reopen()
        return self._read_json(self.manifest_path)

    def reopen(self) -> EvidenceStore:
        """Revalidate every persisted link and return this store."""

        self._load_and_validate(None)
        return self

    def append_event(
        self,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, object]:
        """Append one chained event after validating the current chain."""

        if not isinstance(event_type, str) or not event_type.strip():
            raise ValueError("event_type must be a non-empty string")
        self.reopen()
        return self._append_event(event_type, payload)

    def record_attempt(
        self,
        attempt_id: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, object]:
        """Create a new attempt record and link it into the event chain."""

        _validate_segment(attempt_id, "attempt_id")
        self.reopen()
        normalised_payload = self._normalise_payload(payload)
        body: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "case_id": self.case_workspace.case_id,
            "attempt_id": attempt_id,
            "payload": normalised_payload,
        }
        record = dict(body)
        record["sha256"] = _digest(body)

        attempt = self.case_workspace.allocate_attempt(attempt_id)
        attempt.write_text(ATTEMPT_FILE, _json_text(record))
        self._append_event(
            "attempt_recorded", {"attempt_id": attempt_id, "sha256": record["sha256"]}
        )
        return record

    def record_artifact(
        self,
        path: str | Path,
        *,
        attempt_id: str | None = None,
    ) -> dict[str, object]:
        """Record a case-owned artifact digest and reject later mutations."""

        if attempt_id is not None:
            _validate_segment(attempt_id, "attempt_id")
        self.reopen()
        artifact_path = self._safe_case_file(path)
        relative = artifact_path.relative_to(self.case_workspace.case_root).as_posix()
        if relative in {MANIFEST_FILE, INTENT_FILE, EVENTS_FILE, ATTEMPT_FILE} or relative.endswith(
            f"/{ATTEMPT_FILE}"
        ):
            raise ValueError("evidence metadata files cannot be recorded as artifacts")
        if attempt_id is not None:
            attempt_prefix = f"90_Temporary/attempts/{attempt_id}/"
            if not relative.startswith(attempt_prefix):
                raise ValueError("attempt artifact must be inside its attempt directory")

        record: dict[str, object] = {
            "path": relative,
            "sha256": _file_digest(artifact_path),
            "attempt_id": attempt_id,
        }
        existing = self._artifacts.get(relative)
        if existing is not None:
            if existing != record:
                raise EvidenceIntegrityError(f"artifact digest changed: {relative}")
            return dict(existing)
        self._artifacts[relative] = record
        self._refresh_manifest()
        return dict(record)

    def _has_persisted_state(self) -> bool:
        if any(path.exists() for path in (self.manifest_path, self.intent_path, self.events_path)):
            return True
        attempts_root = self._safe_case_path("90_Temporary/attempts")
        return attempts_root.is_dir() and any(attempts_root.iterdir())

    def _safe_case_path(self, relative_path: str | Path) -> Path:
        root = self.case_workspace.case_root.resolve()
        candidate_path = Path(relative_path)
        if candidate_path.is_absolute() or candidate_path.anchor:
            candidate = candidate_path.resolve(strict=False)
        else:
            candidate = (root / candidate_path).resolve(strict=False)
        if candidate == root or not candidate.is_relative_to(root):
            raise WorkspaceBoundaryError("evidence path is outside the case workspace")
        input_root = (root / "01_Input").resolve(strict=False)
        if candidate == input_root or candidate.is_relative_to(input_root):
            raise WorkspaceBoundaryError("evidence path is inside immutable 01_Input")
        return candidate

    def _safe_case_file(self, path: str | Path) -> Path:
        candidate = self._safe_case_path(path)
        if not candidate.is_file():
            raise EvidenceIntegrityError(f"artifact is not a file: {candidate}")
        return candidate

    def _read_json(self, path: Path) -> dict[str, Any]:
        try:
            text = path.read_text(encoding="utf-8")
            value = json.loads(text)
        except (OSError, json.JSONDecodeError) as error:
            raise EvidenceIntegrityError(f"cannot read JSON evidence: {path}") from error
        try:
            if text != _json_text(value):
                raise EvidenceIntegrityError(f"JSON evidence is not canonical: {path}")
        except TypeError as error:
            raise EvidenceIntegrityError(f"JSON evidence is not canonical: {path}") from error
        return _as_mapping(value, str(path))

    def _normalise_payload(self, payload: Mapping[str, Any] | None) -> dict[str, object]:
        if payload is None:
            return {}
        try:
            value = json.loads(_canonical_bytes(dict(payload)))
        except (TypeError, ValueError) as error:
            raise TypeError("event and attempt payloads must be JSON serializable") from error
        return _as_mapping(value, "payload")

    def _initialize(self, intent: IntentContract) -> None:
        self._intent = intent
        self._artifacts = {}
        intent_payload = intent.to_dict()
        self.case_workspace._write_control_text(INTENT_FILE, _json_text(intent_payload))
        self.case_workspace.append_text(EVENTS_FILE, "")
        self._refresh_manifest()

    def _load_and_validate(self, supplied_intent: IntentContract | None) -> None:
        manifest = self._read_json(self.manifest_path)
        persisted_intent, intent_payload, intent_sha256 = self._read_intent()
        if supplied_intent is not None and supplied_intent.to_dict() != intent_payload:
            raise EvidenceIntegrityError("supplied intent differs from persisted intent")
        events, last_event_sha256 = self._read_events()
        attempts = self._read_attempts()
        artifacts = self._read_artifacts(manifest)
        self._intent = persisted_intent
        self._artifacts = artifacts
        expected = self._project_manifest(
            intent_payload,
            intent_sha256,
            events,
            last_event_sha256,
            attempts,
        )
        if manifest != expected:
            raise EvidenceIntegrityError("manifest projection does not match persisted evidence")
        self._case_sha256 = str(expected["case_sha256"])

    def _read_intent(self) -> tuple[IntentContract, dict[str, Any], str]:
        payload = self._read_json(self.intent_path)
        try:
            intent = IntentContract.from_mapping(payload)
        except (TypeError, ValueError) as error:
            raise EvidenceIntegrityError("persisted intent is invalid") from error
        if intent.to_dict() != payload:
            raise EvidenceIntegrityError("persisted intent is not canonical")
        return intent, payload, _digest(payload)

    def _read_events(self) -> tuple[list[dict[str, Any]], str | None]:
        try:
            text = self.events_path.read_text(encoding="utf-8")
        except OSError as error:
            raise EvidenceIntegrityError("event log is missing or unreadable") from error
        if text and not text.endswith("\n"):
            raise EvidenceIntegrityError("event log is truncated")

        events: list[dict[str, Any]] = []
        previous_sha256: str | None = None
        for expected_sequence, line in enumerate(text.splitlines(), start=1):
            try:
                event = _as_mapping(json.loads(line), "event")
            except (json.JSONDecodeError, EvidenceIntegrityError) as error:
                raise EvidenceIntegrityError("event log contains invalid JSON") from error
            if line != _canonical_bytes(event).decode("utf-8"):
                raise EvidenceIntegrityError("event record is not canonical JSON")
            expected_keys = {
                "schema_version",
                "case_id",
                "sequence",
                "event_type",
                "payload",
                "previous_sha256",
                "sha256",
            }
            if set(event) != expected_keys:
                raise EvidenceIntegrityError("event record has unexpected fields")
            if (
                event["schema_version"] != SCHEMA_VERSION
                or event["case_id"] != self.case_workspace.case_id
                or event["sequence"] != expected_sequence
                or event["previous_sha256"] != previous_sha256
                or not isinstance(event["event_type"], str)
                or not event["event_type"].strip()
                or not isinstance(event["payload"], dict)
            ):
                raise EvidenceIntegrityError("event chain is reordered or discontinuous")
            stored_sha256 = event["sha256"]
            body = dict(event)
            del body["sha256"]
            if not isinstance(stored_sha256, str) or _digest(body) != stored_sha256:
                raise EvidenceIntegrityError("event digest mismatch")
            events.append(event)
            previous_sha256 = stored_sha256
        return events, previous_sha256

    def _read_attempts(self) -> list[dict[str, object]]:
        attempts_root = self._safe_case_path("90_Temporary/attempts")
        if not attempts_root.is_dir():
            raise EvidenceIntegrityError("attempts directory is missing")
        records: list[dict[str, object]] = []
        try:
            children = sorted(attempts_root.iterdir(), key=lambda path: path.name)
        except OSError as error:
            raise EvidenceIntegrityError("attempts directory is unreadable") from error
        for child in children:
            if child.is_symlink() or not child.is_dir():
                raise EvidenceIntegrityError("attempts directory contains an invalid entry")
            try:
                _validate_segment(child.name, "attempt_id")
                relative_record = child.joinpath(ATTEMPT_FILE).relative_to(
                    self.case_workspace.case_root
                )
                record_path = self._safe_case_file(relative_record)
            except (OSError, ValueError, WorkspaceBoundaryError, EvidenceIntegrityError) as error:
                raise EvidenceIntegrityError("attempt record path is invalid") from error
            record = self._read_json(record_path)
            expected_keys = {"schema_version", "case_id", "attempt_id", "payload", "sha256"}
            if set(record) != expected_keys:
                raise EvidenceIntegrityError("attempt record has unexpected fields")
            if (
                record["schema_version"] != SCHEMA_VERSION
                or record["case_id"] != self.case_workspace.case_id
                or record["attempt_id"] != child.name
            ):
                raise EvidenceIntegrityError("attempt record identity mismatch")
            stored_sha256 = record["sha256"]
            body = dict(record)
            del body["sha256"]
            if not isinstance(stored_sha256, str) or _digest(body) != stored_sha256:
                raise EvidenceIntegrityError("attempt digest mismatch")
            records.append(
                {
                    "attempt_id": child.name,
                    "path": record_path.relative_to(self.case_workspace.case_root).as_posix(),
                    "sha256": stored_sha256,
                }
            )
        return records

    def _read_artifacts(self, manifest: Mapping[str, Any]) -> dict[str, dict[str, object]]:
        raw_artifacts = manifest.get("artifacts")
        if not isinstance(raw_artifacts, list):
            raise EvidenceIntegrityError("manifest artifacts must be a list")
        artifacts: dict[str, dict[str, object]] = {}
        for raw_record in raw_artifacts:
            record = _as_mapping(raw_record, "artifact record")
            if set(record) != {"path", "sha256", "attempt_id"}:
                raise EvidenceIntegrityError("artifact record has unexpected fields")
            relative = record["path"]
            stored_sha256 = record["sha256"]
            attempt_id = record["attempt_id"]
            if not isinstance(relative, str) or not isinstance(stored_sha256, str):
                raise EvidenceIntegrityError("artifact record has invalid values")
            if attempt_id is not None and not isinstance(attempt_id, str):
                raise EvidenceIntegrityError("artifact attempt identity is invalid")
            try:
                artifact_path = self._safe_case_file(relative)
            except (WorkspaceBoundaryError, EvidenceIntegrityError) as error:
                raise EvidenceIntegrityError("artifact path is invalid") from error
            canonical_relative = artifact_path.relative_to(self.case_workspace.case_root).as_posix()
            if relative != canonical_relative:
                raise EvidenceIntegrityError("artifact path is not canonical")
            if relative in {
                MANIFEST_FILE,
                INTENT_FILE,
                EVENTS_FILE,
                ATTEMPT_FILE,
            } or relative.endswith(f"/{ATTEMPT_FILE}"):
                raise EvidenceIntegrityError("evidence metadata cannot be an artifact")
            if attempt_id is not None:
                try:
                    _validate_segment(attempt_id, "attempt_id")
                except ValueError as error:
                    raise EvidenceIntegrityError("artifact attempt identity is invalid") from error
                attempt_prefix = f"90_Temporary/attempts/{attempt_id}/"
                if not relative.startswith(attempt_prefix):
                    raise EvidenceIntegrityError("artifact is outside its attempt")
            if _file_digest(artifact_path) != stored_sha256:
                raise EvidenceIntegrityError(f"artifact digest mismatch: {relative}")
            if relative in artifacts:
                raise EvidenceIntegrityError(f"duplicate artifact record: {relative}")
            artifacts[relative] = {
                "path": relative,
                "sha256": stored_sha256,
                "attempt_id": attempt_id,
            }
        return artifacts

    def _input_records(self) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
        input_root = (self.case_workspace.case_root / "01_Input").resolve(strict=False)
        try:
            input_entries = tuple(input_root.iterdir())
        except OSError as error:
            raise EvidenceIntegrityError(
                "original input directory is missing or unreadable"
            ) from error
        if any(entry.is_symlink() or not entry.is_file() for entry in input_entries):
            raise EvidenceIntegrityError("original input directory contains an invalid entry")
        expected_paths = {
            Path(input_path).resolve(strict=False)
            for input_path in self.case_workspace.original_inputs
        }
        actual_paths = {entry.resolve(strict=False) for entry in input_entries}
        if expected_paths != actual_paths:
            raise EvidenceIntegrityError("original input set changed")
        for input_path in self.case_workspace.original_inputs:
            path = Path(input_path).resolve(strict=False)
            if not path.is_file() or not path.is_relative_to(input_root):
                raise EvidenceIntegrityError("original input is missing or escaped 01_Input")
            records.append(
                {
                    "path": path.relative_to(self.case_workspace.case_root).as_posix(),
                    "sha256": _file_digest(path),
                }
            )
        return sorted(records, key=lambda record: str(record["path"]))

    def _artifact_records(self) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
        for relative, record in self._artifacts.items():
            try:
                path = self._safe_case_file(relative)
            except (WorkspaceBoundaryError, EvidenceIntegrityError) as error:
                raise EvidenceIntegrityError("stored artifact path is invalid") from error
            digest = _file_digest(path)
            if digest != record.get("sha256"):
                raise EvidenceIntegrityError(f"artifact digest mismatch: {relative}")
            records.append(dict(record))
        return sorted(records, key=lambda record: str(record["path"]))

    def _project_manifest(
        self,
        intent_payload: Mapping[str, Any],
        intent_sha256: str,
        events: list[dict[str, Any]],
        last_event_sha256: str | None,
        attempts: list[dict[str, object]],
    ) -> dict[str, object]:
        inputs = self._input_records()
        artifacts = self._artifact_records()
        case_body = {
            "case_id": self.case_workspace.case_id,
            "intent_sha256": intent_sha256,
            "inputs": inputs,
            "events_last_sha256": last_event_sha256,
            "attempts": attempts,
            "artifacts": artifacts,
        }
        case_sha256 = _digest(case_body)
        return {
            "schema_version": SCHEMA_VERSION,
            "case_id": self.case_workspace.case_id,
            "case_sha256": case_sha256,
            "state": intent_payload["state"],
            "intent": {"path": INTENT_FILE, "sha256": intent_sha256},
            "inputs": inputs,
            "events": {
                "path": EVENTS_FILE,
                "count": len(events),
                "last_sha256": last_event_sha256,
            },
            "attempts": attempts,
            "artifacts": artifacts,
        }

    def _append_event(
        self,
        event_type: str,
        payload: Mapping[str, Any] | None,
    ) -> dict[str, object]:
        events, previous_sha256 = self._read_events()
        body: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "case_id": self.case_workspace.case_id,
            "sequence": len(events) + 1,
            "event_type": event_type,
            "payload": self._normalise_payload(payload),
            "previous_sha256": previous_sha256,
        }
        event = dict(body)
        event["sha256"] = _digest(body)
        try:
            self.case_workspace.append_text(EVENTS_FILE, _json_text(event))
        except OSError as error:
            raise EvidenceIntegrityError("event log is missing or unreadable") from error
        self._refresh_manifest()
        return event

    def _refresh_manifest(self) -> None:
        intent, intent_payload, intent_sha256 = self._read_intent()
        self._intent = intent
        events, last_event_sha256 = self._read_events()
        attempts = self._read_attempts()
        manifest = self._project_manifest(
            intent_payload,
            intent_sha256,
            events,
            last_event_sha256,
            attempts,
        )
        self._case_sha256 = str(manifest["case_sha256"])
        self.case_workspace._write_control_text(MANIFEST_FILE, _json_text(manifest))
