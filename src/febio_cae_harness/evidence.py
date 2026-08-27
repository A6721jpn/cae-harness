"""Append-only evidence and state persistence for a validated case.

The store keeps intent values opaque and records only authoritative data.  It
does not inspect models, infer physical conditions, or run a solver.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .contracts import IntentContract
from .workspace import (
    CaseWorkspace,
    WorkspaceBoundaryError,
    _lexical_path,
    _reject_reparse_alias,
)

__all__ = ["EvidenceIntegrityError", "EvidenceStore", "VerificationReceipt"]


MANIFEST_FILE = "CASE_MANIFEST.json"
INTENT_FILE = "intent.json"
EVENTS_FILE = "90_Temporary/events.jsonl"
ATTEMPT_FILE = "ATTEMPT.json"
EVENT_LOCK_FILE = "90_Temporary/events.lock"
SCHEMA_VERSION = 1
_VERIFICATION_FIELDS = frozenset(
    {
        "case_id",
        "attempt_id",
        "source",
        "sha256",
        "destination",
        "validator",
        "status",
        "evidence_digest",
    }
)
_VERIFICATION_STATUSES = frozenset({"failed", "passed"})
_RECEIPT_PREFIX = "febio-verification-v1:"
_RECEIPT_FACTORY = object()
_PROMOTION_CONSUMED_EVENT = "artifact_promotion_consumed"

_LOCAL_EVENT_LOCKS: dict[str, threading.RLock] = {}
_LOCAL_EVENT_LOCKS_GUARD = threading.Lock()


class EvidenceIntegrityError(RuntimeError):
    """Raised when persisted evidence is missing, changed, or inconsistent."""


class VerificationReceipt(str):
    """An opaque, store-issued receipt for one passed artifact verification.

    The constructor is intentionally private.  A plain string containing the
    receipt token is not accepted by :meth:`EvidenceStore.promote_verified`;
    callers must retain the object returned by ``record_verification``.
    """

    def __new__(cls, value: str, *, _factory: object | None = None) -> VerificationReceipt:
        if _factory is not _RECEIPT_FACTORY:
            raise TypeError("verification receipts are issued by EvidenceStore")
        if not value.startswith(_RECEIPT_PREFIX):
            raise ValueError("invalid verification receipt")
        token = value.removeprefix(_RECEIPT_PREFIX).split(":")
        if len(token) != 2:
            raise ValueError("invalid verification receipt")
        _validate_digest(token[0], "verification receipt case token")
        _validate_digest(token[1], "verification receipt")
        return str.__new__(cls, value)

    @classmethod
    def _issue(cls, case_token: str, evidence_digest: str) -> VerificationReceipt:
        return cls(
            f"{_RECEIPT_PREFIX}{case_token}:{evidence_digest}",
            _factory=_RECEIPT_FACTORY,
        )

    def __repr__(self) -> str:
        return "VerificationReceipt(<opaque>)"


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


def _validate_digest(value: object, label: str) -> str:
    if not isinstance(value, str) or len(value) != hashlib.sha256().digest_size * 2:
        raise EvidenceIntegrityError(f"{label} must be a SHA-256 digest")
    if any(character not in "0123456789abcdefABCDEF" for character in value):
        raise EvidenceIntegrityError(f"{label} must be a SHA-256 digest")
    return value.casefold()


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        path = _reject_reparse_alias(path, "evidence file")
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except (OSError, WorkspaceBoundaryError) as error:
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


def _local_event_lock(key: str) -> threading.RLock:
    with _LOCAL_EVENT_LOCKS_GUARD:
        lock = _LOCAL_EVENT_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCAL_EVENT_LOCKS[key] = lock
        return lock


@contextmanager
def _exclusive_event_lock(lock_path: Path) -> Iterator[None]:
    """Serialize event-chain transactions across threads and processes."""

    key = str(_lexical_path(lock_path)).casefold()
    local_lock = _local_event_lock(key)
    local_lock.acquire()
    stream = None
    locked = False
    try:
        try:
            stream = lock_path.open("a+b")
            metadata = os.fstat(stream.fileno())
            if metadata.st_nlink != 1:
                raise EvidenceIntegrityError("event log lock cannot be a hard link")
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                deadline = time.monotonic() + 30.0
                while True:
                    try:
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError as error:
                        if time.monotonic() >= deadline:
                            raise EvidenceIntegrityError(
                                "timed out waiting for event log lock"
                            ) from error
                        time.sleep(0.01)
                locked = True
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)  # type: ignore[attr-defined]
                locked = True
        except EvidenceIntegrityError:
            raise
        except OSError as error:
            raise EvidenceIntegrityError("cannot acquire event log lock") from error
        yield
    finally:
        if stream is not None:
            try:
                if locked:
                    if os.name == "nt":
                        import msvcrt

                        stream.seek(0)
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]
            except OSError:
                pass
            stream.close()
        local_lock.release()


class EvidenceStore:
    """Persist intent, events, attempts, artifacts, and a manifest projection."""

    def __init__(self, case_workspace: CaseWorkspace, intent: IntentContract | None = None) -> None:
        self.case_workspace = case_workspace
        self._intent: IntentContract
        self._artifacts: dict[str, dict[str, object]] = {}
        self._case_sha256 = ""

        with self._event_lock():
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

        with self._event_lock():
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
        if event_type in {"artifact_verified", _PROMOTION_CONSUMED_EVENT}:
            raise EvidenceIntegrityError(
                "verification events must be recorded through their dedicated API"
            )
        with self._event_lock():
            self._load_and_validate(None)
            return self._append_event(event_type, payload)

    def record_attempt(
        self,
        attempt_id: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, object]:
        """Create a new attempt record and link it into the event chain."""

        _validate_segment(attempt_id, "attempt_id")
        with self._event_lock():
            self._load_and_validate(None)
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
        with self._event_lock():
            self._load_and_validate(None)
            artifact_path = self._safe_case_file(path)
            relative = artifact_path.relative_to(self.case_workspace.case_root).as_posix()
            if relative in {
                MANIFEST_FILE,
                INTENT_FILE,
                EVENTS_FILE,
                ATTEMPT_FILE,
            } or relative.endswith(f"/{ATTEMPT_FILE}"):
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

    def record_verification(
        self,
        source: str | Path,
        destination: str | Path,
        *,
        attempt_id: str,
        validator: str = "sha256",
        status: str = "passed",
    ) -> VerificationReceipt | None:
        """Persist one artifact verification and issue a receipt only on pass.

        ``source`` is measured at call time and stored as a canonical path
        relative to this case.  The destination is canonicalised but is not
        written here; promotion remains a separate create-new operation.
        """

        _validate_segment(attempt_id, "attempt_id")
        validator = self._validate_validator(validator)
        status = self._validate_verification_status(status)
        with self._event_lock():
            self._load_and_validate(None)
            if not any(record.get("attempt_id") == attempt_id for record in self._read_attempts()):
                raise EvidenceIntegrityError("verification attempt is not recorded")

            source_path, source_relative = self._verification_source(source, attempt_id)
            _, destination_relative = self._verification_destination(destination)
            source_sha256 = _file_digest(source_path)
            body: dict[str, object] = {
                "case_id": self.case_workspace.case_id,
                "attempt_id": attempt_id,
                "source": source_relative,
                "sha256": source_sha256,
                "destination": destination_relative,
                "validator": validator,
                "status": status,
            }
            payload = dict(body)
            payload["evidence_digest"] = _digest(body)
            self._reject_duplicate_verification(payload)
            event = self._append_event("artifact_verified", payload)
            if status != "passed":
                return None
            event_digest = event.get("sha256")
            evidence_digest = payload["evidence_digest"]
            if not isinstance(event_digest, str) or not isinstance(evidence_digest, str):
                raise EvidenceIntegrityError("verification event digest is invalid")
            return VerificationReceipt._issue(self._receipt_case_token(), evidence_digest)

    def verify_for_promotion(
        self,
        source: str | Path,
        destination: str | Path,
        *,
        attempt_id: str,
        validator: str = "sha256",
        status: str = "passed",
    ) -> VerificationReceipt | None:
        """Compatibility alias for :meth:`record_verification`."""

        return self.record_verification(
            source,
            destination,
            attempt_id=attempt_id,
            validator=validator,
            status=status,
        )

    def promote_verified(self, receipt: VerificationReceipt) -> Path:
        """Promote only the exact artifact authorised by a persisted receipt."""

        evidence_digest = self._receipt_digest(receipt)
        with self._event_lock():
            self._load_and_validate(None)
            verification = self._find_verification(evidence_digest)
            if verification is None:
                raise EvidenceIntegrityError("verification receipt is not valid for this case")
            if verification["status"] != "passed":
                raise EvidenceIntegrityError("failed verification cannot be promoted")

            attempt_id = verification["attempt_id"]
            source = verification["source"]
            destination = verification["destination"]
            expected_sha256 = verification["sha256"]
            if not isinstance(attempt_id, str):
                raise EvidenceIntegrityError("verification attempt identity is invalid")
            if not isinstance(source, str):
                raise EvidenceIntegrityError("verification source is invalid")
            if not isinstance(destination, str):
                raise EvidenceIntegrityError("verification destination is invalid")
            if not isinstance(expected_sha256, str):
                raise EvidenceIntegrityError("verification record has invalid values")
            source_path, source_relative = self._verification_source(source, attempt_id)
            destination_path, destination_relative = self._verification_destination(destination)
            if source_relative != source or destination_relative != destination:
                raise EvidenceIntegrityError("verification paths are not canonical")
            if _file_digest(source_path) != expected_sha256:
                raise EvidenceIntegrityError("verification source digest changed")
            consumed = self._find_consumed(evidence_digest)
            if consumed:
                if not destination_path.exists() and not destination_path.is_symlink():
                    raise EvidenceIntegrityError("verification receipt has already been consumed")
                if destination_path.is_file() and _file_digest(destination_path) != expected_sha256:
                    raise EvidenceIntegrityError("promoted destination changed")
                raise FileExistsError(destination_path)
            if destination_path.exists() or destination_path.is_symlink():
                raise FileExistsError(destination_path)
            self._append_event(_PROMOTION_CONSUMED_EVENT, verification)
            return self.case_workspace._copy_create_new(
                source_path,
                destination,
                expected_sha256=expected_sha256,
            )

    def _event_lock(self) -> Any:
        return _exclusive_event_lock(self._safe_case_path(EVENT_LOCK_FILE))

    @staticmethod
    def _validate_validator(validator: str) -> str:
        if not isinstance(validator, str) or not validator.strip():
            raise ValueError("validator must be a non-empty string")
        if validator != validator.strip():
            raise ValueError("validator must not have surrounding whitespace")
        return validator

    @staticmethod
    def _validate_verification_status(status: str) -> str:
        if not isinstance(status, str) or status.casefold() not in _VERIFICATION_STATUSES:
            raise ValueError("status must be passed or failed")
        return status.casefold()

    def _receipt_digest(self, receipt: VerificationReceipt) -> str:
        if type(receipt) is not VerificationReceipt:
            raise EvidenceIntegrityError("promotion requires a store-issued verification receipt")
        token = str(receipt)
        if not token.startswith(_RECEIPT_PREFIX):
            raise EvidenceIntegrityError("promotion receipt is invalid")
        values = token.removeprefix(_RECEIPT_PREFIX).split(":")
        if len(values) != 2:
            raise EvidenceIntegrityError("promotion receipt is invalid")
        case_token = _validate_digest(values[0], "verification receipt case token")
        if case_token != self._receipt_case_token():
            raise EvidenceIntegrityError("verification receipt belongs to another case")
        return _validate_digest(values[1], "verification receipt")

    def _receipt_case_token(self) -> str:
        case_root = _reject_reparse_alias(self.case_workspace.case_root, "case root")
        return _digest(
            {
                "case_id": self.case_workspace.case_id,
                "case_root": os.path.normcase(os.fspath(case_root)),
            }
        )

    def _verification_source(
        self,
        source: str | Path,
        attempt_id: str,
    ) -> tuple[Path, str]:
        source_path = self._safe_case_file(source)
        source_relative = source_path.relative_to(self.case_workspace.case_root).as_posix()
        attempt_prefix = f"90_Temporary/attempts/{attempt_id}/"
        if not source_relative.startswith(attempt_prefix):
            raise EvidenceIntegrityError("verification source is outside its attempt")
        if source_relative.endswith(f"/{ATTEMPT_FILE}"):
            raise EvidenceIntegrityError("verification source cannot be attempt metadata")
        return source_path, source_relative

    def _verification_destination(self, destination: str | Path) -> tuple[Path, str]:
        destination_path = self._safe_case_path(destination)
        relative = destination_path.relative_to(self.case_workspace.case_root)
        if len(relative.parts) < 2 or relative.parts[0] not in {
            "02_Model",
            "03_Result",
            "04_Report",
            "05_Verification",
        }:
            raise EvidenceIntegrityError("verification destination must be permanent")
        return destination_path, relative.as_posix()

    def _find_verification(self, evidence_digest: str) -> dict[str, object] | None:
        for event in self._events_with_verifications():
            payload = event["payload"]
            if isinstance(payload, dict) and payload.get("evidence_digest") == evidence_digest:
                return dict(payload)
        return None

    def _events_with_verifications(self) -> list[dict[str, Any]]:
        events, _ = self._read_events()
        return [event for event in events if event.get("event_type") == "artifact_verified"]

    def _find_consumed(self, evidence_digest: str) -> bool:
        events, _ = self._read_events()
        for event in events:
            if event.get("event_type") != _PROMOTION_CONSUMED_EVENT:
                continue
            payload = event.get("payload")
            if isinstance(payload, dict) and payload.get("evidence_digest") == evidence_digest:
                return True
        return False

    def _reject_duplicate_verification(self, payload: Mapping[str, object]) -> None:
        evidence_digest = payload.get("evidence_digest")
        if not isinstance(evidence_digest, str):
            raise EvidenceIntegrityError("verification digest is invalid")
        if self._find_verification(evidence_digest) is not None:
            raise EvidenceIntegrityError("duplicate artifact verification")

    def _has_persisted_state(self) -> bool:
        if any(path.exists() for path in (self.manifest_path, self.intent_path, self.events_path)):
            return True
        attempts_root = self._safe_case_path("90_Temporary/attempts")
        return attempts_root.is_dir() and any(attempts_root.iterdir())

    def _safe_case_path(self, relative_path: str | Path) -> Path:
        root = _reject_reparse_alias(self.case_workspace.case_root, "case root")
        candidate_path = Path(relative_path)
        if ".." in candidate_path.parts:
            raise WorkspaceBoundaryError("evidence path cannot contain parent traversal")
        if candidate_path.is_absolute() or candidate_path.anchor:
            candidate = _lexical_path(candidate_path)
        else:
            candidate = _lexical_path(root / candidate_path)
        candidate = _reject_reparse_alias(candidate, "evidence path")
        if candidate == root or not candidate.is_relative_to(root):
            raise WorkspaceBoundaryError("evidence path is outside the case workspace")
        input_root = _reject_reparse_alias(root / "01_Input", "input root")
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
        verification_digests: set[str] = set()
        verification_records: dict[str, dict[str, object]] = {}
        consumed_digests: set[str] = set()
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
            if event["event_type"] == "artifact_verified":
                payload = self._validate_verification_payload(event["payload"])
                evidence_digest = payload["evidence_digest"]
                if not isinstance(evidence_digest, str):
                    raise EvidenceIntegrityError("artifact verification digest is invalid")
                if evidence_digest in verification_digests:
                    raise EvidenceIntegrityError("duplicate artifact verification")
                verification_digests.add(evidence_digest)
                verification_records[evidence_digest] = payload
            elif event["event_type"] == _PROMOTION_CONSUMED_EVENT:
                payload = self._validate_promotion_consumed_payload(event["payload"])
                evidence_digest = payload["evidence_digest"]
                if not isinstance(evidence_digest, str):
                    raise EvidenceIntegrityError("consumed verification digest is invalid")
                if evidence_digest in consumed_digests:
                    raise EvidenceIntegrityError("duplicate consumed verification")
                if verification_records.get(evidence_digest) != payload:
                    raise EvidenceIntegrityError("consumed verification binding mismatch")
                consumed_digests.add(evidence_digest)
            events.append(event)
            previous_sha256 = stored_sha256
        return events, previous_sha256

    def _validate_promotion_consumed_payload(self, value: object) -> dict[str, object]:
        payload = self._validate_verification_payload(value)
        if payload["status"] != "passed":
            raise EvidenceIntegrityError("failed verification cannot be consumed")
        return payload

    def _validate_verification_payload(self, value: object) -> dict[str, object]:
        payload = _as_mapping(value, "artifact verification")
        if set(payload) != _VERIFICATION_FIELDS:
            raise EvidenceIntegrityError("artifact verification has unexpected fields")
        case_id = payload["case_id"]
        attempt_id = payload["attempt_id"]
        source = payload["source"]
        source_sha256 = payload["sha256"]
        destination = payload["destination"]
        validator = payload["validator"]
        status = payload["status"]
        evidence_digest = payload["evidence_digest"]
        if not isinstance(case_id, str) or case_id != self.case_workspace.case_id:
            raise EvidenceIntegrityError("artifact verification case identity mismatch")
        if not isinstance(attempt_id, str):
            raise EvidenceIntegrityError("artifact verification attempt identity is invalid")
        try:
            _validate_segment(attempt_id, "attempt_id")
        except ValueError as error:
            raise EvidenceIntegrityError(
                "artifact verification attempt identity is invalid"
            ) from error
        if not isinstance(source, str) or not isinstance(destination, str):
            raise EvidenceIntegrityError("artifact verification paths are invalid")
        if not isinstance(source_sha256, str) or len(source_sha256) != 64:
            raise EvidenceIntegrityError("artifact verification source digest is invalid")
        if source_sha256 != source_sha256.casefold():
            raise EvidenceIntegrityError("artifact verification source digest is not canonical")
        source_sha256 = _validate_digest(source_sha256, "artifact verification source digest")
        if not isinstance(validator, str) or not validator or validator != validator.strip():
            raise EvidenceIntegrityError("artifact verification validator is invalid")
        if not isinstance(status, str) or status not in _VERIFICATION_STATUSES:
            raise EvidenceIntegrityError("artifact verification status is invalid")
        if not isinstance(evidence_digest, str) or evidence_digest != evidence_digest.casefold():
            raise EvidenceIntegrityError("artifact verification digest is not canonical")
        evidence_digest = _validate_digest(evidence_digest, "artifact verification digest")
        body = {
            "case_id": case_id,
            "attempt_id": attempt_id,
            "source": source,
            "sha256": source_sha256,
            "destination": destination,
            "validator": validator,
            "status": status,
        }
        if _digest(body) != evidence_digest:
            raise EvidenceIntegrityError("artifact verification digest mismatch")
        try:
            self._safe_case_file(f"90_Temporary/attempts/{attempt_id}/{ATTEMPT_FILE}")
            source_path, source_relative = self._verification_source(source, attempt_id)
            _, destination_relative = self._verification_destination(destination)
        except (WorkspaceBoundaryError, EvidenceIntegrityError, ValueError) as error:
            raise EvidenceIntegrityError("artifact verification path is invalid") from error
        if source_relative != source or destination_relative != destination:
            raise EvidenceIntegrityError("artifact verification path is not canonical")
        if _file_digest(source_path) != source_sha256:
            raise EvidenceIntegrityError("artifact verification source digest changed")
        return {
            "case_id": case_id,
            "attempt_id": attempt_id,
            "source": source,
            "sha256": source_sha256,
            "destination": destination,
            "validator": validator,
            "status": status,
            "evidence_digest": evidence_digest,
        }

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
            try:
                child = _reject_reparse_alias(child, "attempt root")
            except WorkspaceBoundaryError as error:
                raise EvidenceIntegrityError(
                    "attempts directory contains an invalid entry"
                ) from error
            if not child.is_dir():
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
        try:
            input_root = _reject_reparse_alias(
                self.case_workspace.case_root / "01_Input",
                "input root",
            )
        except WorkspaceBoundaryError as error:
            raise EvidenceIntegrityError("original input directory is invalid") from error
        try:
            input_entries = tuple(input_root.iterdir())
        except OSError as error:
            raise EvidenceIntegrityError(
                "original input directory is missing or unreadable"
            ) from error
        normalised_entries: list[Path] = []
        for entry in input_entries:
            try:
                normalised_entry = _reject_reparse_alias(entry, "original input")
            except WorkspaceBoundaryError as error:
                raise EvidenceIntegrityError(
                    "original input directory contains an invalid entry"
                ) from error
            if not normalised_entry.is_file():
                raise EvidenceIntegrityError("original input directory contains an invalid entry")
            normalised_entries.append(normalised_entry)
        expected_paths = {
            _reject_reparse_alias(input_path, "original input")
            for input_path in self.case_workspace.original_inputs
        }
        actual_paths = set(normalised_entries)
        if expected_paths != actual_paths:
            raise EvidenceIntegrityError("original input set changed")
        for input_path in self.case_workspace.original_inputs:
            try:
                path = _reject_reparse_alias(input_path, "original input")
            except WorkspaceBoundaryError as error:
                raise EvidenceIntegrityError("original input is invalid") from error
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
