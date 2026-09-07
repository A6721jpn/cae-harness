"""SQLite-backed registered case state and owned immutable file publication."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import stat
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from febio_cae.domain.artifacts import (
    FileEntry,
    ResolvedFileContent,
    SourceAssetContent,
    SourceAssetRef,
)
from febio_cae.domain.case_draft import CaseDraft
from febio_cae.domain.case_revision import CaseRevision
from febio_cae.domain.codec import decode_record, encode_record
from febio_cae.domain.execution import AttemptRecord, ExecutionBundle
from febio_cae.domain.partial_case_spec import PartialCaseSpec
from febio_cae.domain.ports import (
    PortError,
    PortErrorCategory,
    TrustedOwnerContext,
)
from febio_cae.domain.questions import IssuedQuestion
from febio_cae.domain.results import NumericResultData, ResultDataRef, ResultManifest

from .catalog import validate_case_id


class StorageConflictError(RuntimeError):
    """Raised when a durable compare-and-swap or owner claim loses."""


class StorageIntegrityError(RuntimeError):
    """Raised when immutable bytes no longer match their registered identity."""


class InjectedStorageFailure(RuntimeError):
    """Test-only crash injection at a named real persistence boundary."""


FailureInjector = Callable[[str], None]
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=30.0, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


def _safe_identifier(value: str, field: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None or value in {".", ".."}:
        raise ValueError(f"{field} must be a safe identifier")
    if "/" in value or "\\" in value or ":" in value:
        raise ValueError(f"{field} must not contain path syntax")
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


def _assert_no_links(path: Path, stop: Path) -> None:
    current = path
    stop_absolute = stop.absolute()
    while True:
        if current.exists() and _is_reparse(current):
            raise StorageIntegrityError(
                f"link or reparse point is outside owned storage: {current}"
            )
        if current == stop_absolute:
            return
        parent = current.parent
        if parent == current:
            raise StorageIntegrityError("path escaped the owned storage root")
        current = parent


def _owned_path(root: Path, relative: str) -> Path:
    if not relative or relative.startswith("/") or "\\" in relative:
        raise StorageIntegrityError("owned relative path is not lexical")
    parts = relative.split("/")
    if any(not part or part in {".", ".."} or ":" in part for part in parts):
        raise StorageIntegrityError("owned relative path contains an escape")
    candidate = root.joinpath(*parts)
    try:
        candidate.absolute().relative_to(root.absolute())
    except ValueError as error:
        raise StorageIntegrityError("owned path escaped its case root") from error
    _assert_no_links(candidate.parent, root)
    if candidate.exists() and _is_reparse(candidate):
        raise StorageIntegrityError("owned output path is a link or reparse point")
    return candidate


def _write_atomic(root: Path, relative: str, content: bytes, *, token: str | None = None) -> Path:
    target = _owned_path(root, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_links(target.parent, root)
    suffix = token or uuid.uuid4().hex
    temporary = target.with_name(f".{target.name}.{suffix}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        try:
            directory_handle = os.open(target.parent, os.O_RDONLY)
        except OSError:
            directory_handle = None
        if directory_handle is not None:
            try:
                os.fsync(directory_handle)
            finally:
                os.close(directory_handle)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target


def _read_owned(root: Path, relative: str) -> bytes:
    target = _owned_path(root, relative)
    if not target.is_file():
        raise StorageIntegrityError(f"registered file is missing: {relative}")
    _assert_no_links(target, root)
    return target.read_bytes()


class CaseStorage:
    """One registered case root. All durable IDs are looked up in its SQLite registry."""

    def __init__(
        self, root: Path | str, *, failure_injector: FailureInjector | None = None
    ) -> None:
        self.root = Path(root).absolute()
        if not self.root.is_dir() or self.root.is_symlink():
            raise StorageIntegrityError("registered case root is unavailable")
        self.registry_path = self.root / "registry.sqlite3"
        self.failure_injector = failure_injector
        self._initialize_schema()
        self._recover_publications()

    @classmethod
    def initialize(
        cls,
        root: Path | str,
        *,
        case_id: str,
        source_asset: SourceAssetRef,
        source_kind: str,
        source_content: bytes,
        created_at: str,
    ) -> CaseStorage:
        validate_case_id(case_id)
        root_path = Path(root).absolute()
        if root_path.exists() and _is_reparse(root_path):
            raise StorageIntegrityError("case root must not be a link or reparse point")
        root_path.mkdir(parents=True, exist_ok=True)
        if not root_path.is_dir() or _is_reparse(root_path):
            raise StorageIntegrityError("case root is not an owned directory")
        case_dir = root_path / "cases" / case_id
        for directory in (
            case_dir,
            case_dir / "sources",
            case_dir / "revisions",
            case_dir / "runs",
            case_dir / "geometry",
            case_dir / "meshes",
            case_dir / "assessments",
            case_dir / "comparisons",
            case_dir / "previews",
        ):
            directory.mkdir(parents=True, exist_ok=True)
            _assert_no_links(directory, root_path)
        storage = cls(root_path)
        draft = CaseDraft(
            case_id=case_id,
            draft_id=f"draft-{uuid.uuid4().hex}",
            generation=0,
            input_intent="",
            parent_revision_id=None,
            parent_spec_digest=None,
            values=_empty_partial(),
            evidence=(),
        )
        relative = f"cases/{case_id}/sources/{source_asset.asset_id}.bin"
        _write_atomic(root_path, relative, source_content)
        payload = encode_record(draft)
        with _connect(storage.registry_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO case_state(
                    case_id,cad_asset_id,current_draft_id,current_generation,created_at
                ) VALUES(?,?,?,?,?)
                """,
                (case_id, source_asset.asset_id, draft.draft_id, draft.generation, created_at),
            )
            connection.execute(
                "INSERT INTO drafts(draft_id,case_id,generation,payload) VALUES(?,?,?,?)",
                (draft.draft_id, case_id, draft.generation, payload),
            )
            connection.execute(
                """
                INSERT INTO sources(
                    asset_id,case_id,source_kind,media_type,content_digest,relative_path
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    source_asset.asset_id,
                    case_id,
                    source_kind,
                    source_asset.media_type,
                    source_asset.content_digest,
                    relative,
                ),
            )
            connection.commit()
        return storage

    def _initialize_schema(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with _connect(self.registry_path) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS case_state (
                    case_id TEXT PRIMARY KEY,
                    cad_asset_id TEXT NOT NULL,
                    current_draft_id TEXT NOT NULL,
                    current_generation INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS drafts (
                    draft_id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    payload BLOB NOT NULL,
                    UNIQUE(case_id,generation)
                );
                CREATE TABLE IF NOT EXISTS sources (
                    asset_id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    content_digest TEXT NOT NULL,
                    relative_path TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS revisions (
                    revision_id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    parent_revision_id TEXT,
                    parent_spec_digest TEXT,
                    spec_digest TEXT NOT NULL,
                    payload BLOB NOT NULL,
                    relative_path TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS questions (
                    question_id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    draft_id TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    payload BLOB NOT NULL,
                    consumed INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS publications (
                    transaction_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    payload BLOB NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS owners (
                    run_id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    attempt_id TEXT NOT NULL UNIQUE,
                    owner_generation INTEGER NOT NULL,
                    payload BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS manifests (
                    manifest_id TEXT PRIMARY KEY,
                    attempt_id TEXT NOT NULL,
                    payload BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS numeric_data (
                    data_id TEXT PRIMARY KEY,
                    payload BLOB NOT NULL
                );
                """
            )

    def _fault(self, boundary: str) -> None:
        if self.failure_injector is not None:
            self.failure_injector(boundary)

    def _recover_publications(self) -> None:
        with _connect(self.registry_path) as connection:
            rows = connection.execute(
                "SELECT * FROM publications ORDER BY created_at,transaction_id"
            ).fetchall()
            if not rows:
                return
            connection.execute("BEGIN IMMEDIATE")
            try:
                for row in rows:
                    relative = str(row["relative_path"])
                    final = _owned_path(self.root, relative)
                    payload = bytes(row["payload"])
                    if row["state"] == "COMMITTED":
                        connection.execute(
                            "DELETE FROM publications WHERE transaction_id=?",
                            (row["transaction_id"],),
                        )
                        continue
                    if final.exists():
                        if final.read_bytes() != payload:
                            raise StorageIntegrityError(
                                f"publication target does not match prepared bytes: {relative}"
                            )
                        revision = decode_record(payload, CaseRevision)
                        connection.execute(
                            """
                            INSERT OR IGNORE INTO revisions(
                                revision_id,case_id,parent_revision_id,parent_spec_digest,
                                spec_digest,payload,relative_path,created_at
                            ) VALUES(?,?,?,?,?,?,?,?)
                            """,
                            (
                                revision.revision_id,
                                revision.case_id,
                                revision.parent_revision_id,
                                revision.parent_spec_digest,
                                revision.spec_digest,
                                payload,
                                relative,
                                str(row["created_at"]),
                            ),
                        )
                    temporary = final.with_name(f".{final.name}.{row['transaction_id']}.tmp")
                    if temporary.exists():
                        temporary.unlink()
                    connection.execute(
                        "DELETE FROM publications WHERE transaction_id=?",
                        (row["transaction_id"],),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def _case_row(self, connection: sqlite3.Connection, case_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM case_state WHERE case_id=?", (case_id,)).fetchone()
        if row is None:
            raise StorageIntegrityError(f"case registry has no state for {case_id!r}")
        return row

    def current_draft(self, case_id: str) -> CaseDraft:
        validate_case_id(case_id)
        with _connect(self.registry_path) as connection:
            row = self._case_row(connection, case_id)
            draft_row = connection.execute(
                "SELECT payload FROM drafts WHERE draft_id=?", (row["current_draft_id"],)
            ).fetchone()
        if draft_row is None:
            raise StorageIntegrityError("current draft row is missing")
        try:
            return decode_record(bytes(draft_row["payload"]), CaseDraft)
        except Exception as error:
            raise StorageIntegrityError("current draft payload is corrupt") from error

    def get_revision(self, case_id: str, revision_id: str) -> CaseRevision:
        validate_case_id(case_id)
        _safe_identifier(revision_id, "revision_id")
        with _connect(self.registry_path) as connection:
            row = connection.execute(
                "SELECT * FROM revisions WHERE case_id=? AND revision_id=?",
                (case_id, revision_id),
            ).fetchone()
        if row is None:
            raise StorageConflictError(f"unknown revision {revision_id!r} for case {case_id!r}")
        payload = bytes(row["payload"])
        try:
            revision = decode_record(payload, CaseRevision)
        except Exception as error:
            raise StorageIntegrityError("registered revision payload is corrupt") from error
        if revision.case_id != case_id or revision.revision_id != revision_id:
            raise StorageIntegrityError("registered revision identity does not match its row")
        if _read_owned(self.root, str(row["relative_path"])) != payload:
            raise StorageIntegrityError("registered revision file does not match SQLite")
        return revision

    def set_draft(self, draft: CaseDraft, *, expected_generation: int) -> CaseDraft:
        payload = encode_record(draft)
        with _connect(self.registry_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                current = self._case_row(connection, draft.case_id)
                if current["current_generation"] != expected_generation:
                    raise StorageConflictError(
                        f"expected draft generation {expected_generation}, "
                        f"current is {current['current_generation']}"
                    )
                if draft.generation != expected_generation + 1:
                    raise StorageConflictError("new draft generation is not the next generation")
                connection.execute(
                    "INSERT INTO drafts(draft_id,case_id,generation,payload) VALUES(?,?,?,?)",
                    (draft.draft_id, draft.case_id, draft.generation, payload),
                )
                updated = connection.execute(
                    """
                    UPDATE case_state SET current_draft_id=?,current_generation=?
                    WHERE case_id=? AND current_generation=?
                    """,
                    (draft.draft_id, draft.generation, draft.case_id, expected_generation),
                )
                if updated.rowcount != 1:
                    raise StorageConflictError("draft generation changed during update")
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return draft

    def issue_question(self, question: IssuedQuestion) -> IssuedQuestion:
        payload = encode_record(question)
        case_id = question.case_id
        question_id = question.question_id
        with _connect(self.registry_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._case_row(connection, case_id)
                connection.execute(
                    "INSERT INTO questions(question_id,case_id,draft_id,generation,payload) VALUES(?,?,?,?,?)",
                    (
                        question_id,
                        case_id,
                        question.draft_id,
                        question.generation,
                        payload,
                    ),
                )
                connection.commit()
            except sqlite3.IntegrityError as error:
                connection.rollback()
                raise StorageConflictError("question ID is already registered") from error
            except Exception:
                connection.rollback()
                raise
        return question

    def get_question(self, question_id: str) -> IssuedQuestion:
        _safe_identifier(question_id, "question_id")
        with _connect(self.registry_path) as connection:
            row = connection.execute(
                "SELECT payload FROM questions WHERE question_id=?", (question_id,)
            ).fetchone()
        if row is None:
            raise StorageConflictError(f"unknown question {question_id!r}")
        try:
            return decode_record(bytes(row["payload"]), IssuedQuestion)
        except Exception as error:
            raise StorageIntegrityError("issued question payload is corrupt") from error

    def consume_question(
        self,
        question_id: str,
        draft: CaseDraft,
        *,
        expected_generation: int,
    ) -> CaseDraft:
        payload = encode_record(draft)
        with _connect(self.registry_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                current = self._case_row(connection, draft.case_id)
                row = connection.execute(
                    "SELECT * FROM questions WHERE question_id=? AND case_id=?",
                    (question_id, draft.case_id),
                ).fetchone()
                if row is None or row["consumed"]:
                    raise StorageConflictError("question is unknown or already consumed")
                if (
                    current["current_generation"] != expected_generation
                    or row["generation"] != expected_generation
                    or row["draft_id"] != current["current_draft_id"]
                ):
                    raise StorageConflictError("question is stale for the current draft")
                if draft.generation != expected_generation + 1:
                    raise StorageConflictError(
                        "answered draft generation is not the next generation"
                    )
                consumed = connection.execute(
                    "UPDATE questions SET consumed=1 WHERE question_id=? AND consumed=0",
                    (question_id,),
                )
                if consumed.rowcount != 1:
                    raise StorageConflictError("question was consumed by another writer")
                connection.execute(
                    "INSERT INTO drafts(draft_id,case_id,generation,payload) VALUES(?,?,?,?)",
                    (draft.draft_id, draft.case_id, draft.generation, payload),
                )
                connection.execute(
                    "UPDATE case_state SET current_draft_id=?,current_generation=? WHERE case_id=?",
                    (draft.draft_id, draft.generation, draft.case_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return draft

    def register_revision(self, revision: CaseRevision) -> CaseRevision:
        return self._publish_revision(revision, expected_generation=None)

    def register_revision_if_current(
        self, revision: CaseRevision, *, expected_generation: int
    ) -> CaseRevision:
        return self._publish_revision(revision, expected_generation=expected_generation)

    def _publish_revision(
        self, revision: CaseRevision, *, expected_generation: int | None
    ) -> CaseRevision:
        payload = encode_record(revision)
        relative = f"cases/{revision.case_id}/revisions/{revision.revision_id}/revision.json"
        transaction_id = uuid.uuid4().hex[:12]
        with _connect(self.registry_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                current = self._case_row(connection, revision.case_id)
                if (
                    expected_generation is not None
                    and current["current_generation"] != expected_generation
                ):
                    raise StorageConflictError("draft changed before freeze")
                connection.execute(
                    """
                    INSERT INTO publications(
                        transaction_id,kind,record_id,relative_path,payload,state,created_at
                    ) VALUES(?,?,?,?,?,?,?)
                    """,
                    (
                        transaction_id,
                        "revision",
                        revision.revision_id,
                        relative,
                        payload,
                        "PREPARED",
                        _now(),
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        self._fault("after_prepare")
        target = _owned_path(self.root, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{transaction_id}.tmp")
        temporary.parent.mkdir(parents=True, exist_ok=True)
        _assert_no_links(target.parent, self.root)
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        self._fault("after_file_write")
        os.replace(temporary, target)
        self._fault("after_file_replace")
        with _connect(self.registry_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if expected_generation is not None:
                    current = self._case_row(connection, revision.case_id)
                    if current["current_generation"] != expected_generation:
                        raise StorageConflictError("draft changed before revision finalization")
                connection.execute(
                    """
                    INSERT INTO revisions(
                        revision_id,case_id,parent_revision_id,parent_spec_digest,
                        spec_digest,payload,relative_path,created_at
                    ) VALUES(?,?,?,?,?,?,?,?)
                    """,
                    (
                        revision.revision_id,
                        revision.case_id,
                        revision.parent_revision_id,
                        revision.parent_spec_digest,
                        revision.spec_digest,
                        payload,
                        relative,
                        _now(),
                    ),
                )
                connection.execute(
                    "UPDATE publications SET state='COMMITTED' WHERE transaction_id=?",
                    (transaction_id,),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        self._fault("after_commit")
        with _connect(self.registry_path) as connection:
            connection.execute("DELETE FROM publications WHERE transaction_id=?", (transaction_id,))
        return revision

    def source_asset(self, asset_id: str) -> SourceAssetRef:
        _safe_identifier(asset_id, "asset_id")
        with _connect(self.registry_path) as connection:
            row = connection.execute(
                "SELECT * FROM sources WHERE asset_id=?", (asset_id,)
            ).fetchone()
        if row is None:
            raise StorageConflictError(f"unknown source asset {asset_id!r}")
        return SourceAssetRef(
            asset_id=asset_id,
            content_digest=str(row["content_digest"]),
            media_type=str(row["media_type"]),
        )

    def source_kind(self, asset_id: str) -> str:
        _safe_identifier(asset_id, "asset_id")
        with _connect(self.registry_path) as connection:
            row = connection.execute(
                "SELECT source_kind FROM sources WHERE asset_id=?", (asset_id,)
            ).fetchone()
        if row is None:
            raise StorageConflictError(f"unknown source asset {asset_id!r}")
        return str(row["source_kind"])

    def ingest_source(
        self,
        *,
        asset_id: str,
        source_kind: str,
        media_type: str,
        content: bytes,
        expected_digest: str | None = None,
    ) -> SourceAssetRef:
        _safe_identifier(asset_id, "asset_id")
        if not isinstance(content, bytes):
            raise TypeError("source content must be bytes")
        digest = hashlib.sha256(content).hexdigest()
        if expected_digest is not None and expected_digest != digest:
            raise StorageIntegrityError("source declaration digest does not match its bytes")
        reference = SourceAssetRef(asset_id, digest, media_type)
        relative = f"cases/{self._case_id()}/sources/{asset_id}.bin"
        with _connect(self.registry_path) as connection:
            row = connection.execute(
                "SELECT * FROM sources WHERE asset_id=?", (asset_id,)
            ).fetchone()
            if row is not None:
                if (
                    row["content_digest"] != digest
                    or row["source_kind"] != source_kind
                    or row["media_type"] != media_type
                ):
                    raise StorageConflictError("source identity is already registered differently")
                return reference
        _write_atomic(self.root, relative, content)
        with _connect(self.registry_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "INSERT INTO sources(asset_id,case_id,source_kind,media_type,content_digest,relative_path) VALUES(?,?,?,?,?,?)",
                    (asset_id, self._case_id(), source_kind, media_type, digest, relative),
                )
                connection.commit()
            except sqlite3.IntegrityError:
                connection.rollback()
                existing = self.source_asset(asset_id)
                if existing != reference:
                    raise StorageConflictError("source identity changed during registration")
            except Exception:
                connection.rollback()
                raise
        return reference

    def _case_id(self) -> str:
        with _connect(self.registry_path) as connection:
            row = connection.execute("SELECT case_id FROM case_state LIMIT 1").fetchone()
        if row is None:
            raise StorageIntegrityError("case registry is not initialized")
        return str(row["case_id"])

    def resolve_source(self, source_asset: SourceAssetRef) -> SourceAssetContent:
        if not isinstance(source_asset, SourceAssetRef):
            raise PortError(
                PortErrorCategory.INVALID_INPUT, "source_asset must be registered identity"
            )
        with _connect(self.registry_path) as connection:
            row = connection.execute(
                "SELECT * FROM sources WHERE asset_id=?", (source_asset.asset_id,)
            ).fetchone()
        if row is None or row["content_digest"] != source_asset.content_digest:
            raise PortError(PortErrorCategory.INTEGRITY, "source identity is not registered")
        if row["media_type"] != source_asset.media_type:
            raise PortError(PortErrorCategory.INTEGRITY, "source media type changed")
        try:
            content = _read_owned(self.root, str(row["relative_path"]))
        except StorageIntegrityError as error:
            raise PortError(PortErrorCategory.INTEGRITY, str(error)) from error
        if hashlib.sha256(content).hexdigest() != source_asset.content_digest:
            raise PortError(PortErrorCategory.INTEGRITY, "registered source bytes were modified")
        return SourceAssetContent(source_asset, content)

    def claim(self, owner: TrustedOwnerContext) -> TrustedOwnerContext:
        if owner.case_id != self._case_id():
            raise PortError(PortErrorCategory.CONFLICT, "owner case is not this registered case")
        payload = owner.__class__.__name__.encode("utf-8") + b":" + owner.run_id.encode("utf-8")
        with _connect(self.registry_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "INSERT INTO owners(run_id,case_id,attempt_id,owner_generation,payload) VALUES(?,?,?,?,?)",
                    (
                        owner.run_id,
                        owner.case_id,
                        owner.attempt_id,
                        owner.owner_generation,
                        payload,
                    ),
                )
                connection.commit()
            except sqlite3.IntegrityError as error:
                connection.rollback()
                raise PortError(
                    PortErrorCategory.CONFLICT, "run or attempt ownership already claimed"
                ) from error
            except Exception:
                connection.rollback()
                raise
        return owner

    def validate(self, owner: TrustedOwnerContext, attempt: AttemptRecord) -> TrustedOwnerContext:
        if attempt.case_id != owner.case_id or attempt.run_id != owner.run_id:
            raise PortError(
                PortErrorCategory.CONFLICT, "attempt is outside the trusted owner scope"
            )
        with _connect(self.registry_path) as connection:
            row = connection.execute(
                "SELECT * FROM owners WHERE run_id=? AND attempt_id=?",
                (owner.run_id, owner.attempt_id),
            ).fetchone()
        if row is None or row["owner_generation"] != owner.owner_generation:
            raise PortError(PortErrorCategory.CONFLICT, "owner claim is not registered")
        return owner

    def publish_manifest(
        self, owner: TrustedOwnerContext, manifest: ResultManifest
    ) -> ResultManifest:
        if manifest.attempt_id != owner.attempt_id:
            raise PortError(PortErrorCategory.CONFLICT, "manifest attempt is outside owner scope")
        payload = encode_record(manifest)
        with _connect(self.registry_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT 1 FROM owners WHERE run_id=? AND attempt_id=? AND owner_generation=?",
                    (owner.run_id, owner.attempt_id, owner.owner_generation),
                ).fetchone()
                if row is None:
                    raise PortError(PortErrorCategory.CONFLICT, "owner claim is not registered")
                connection.execute(
                    "INSERT INTO manifests(manifest_id,attempt_id,payload) VALUES(?,?,?)",
                    (manifest.manifest_id, manifest.attempt_id, payload),
                )
                connection.commit()
            except sqlite3.IntegrityError as error:
                connection.rollback()
                raise PortError(
                    PortErrorCategory.CONFLICT, "manifest is already registered"
                ) from error
            except Exception:
                connection.rollback()
                raise
        return manifest

    def resolve_file(
        self, entry: FileEntry, bundle: ExecutionBundle, attempt: AttemptRecord
    ) -> ResolvedFileContent:
        if bundle.case_id != self._case_id() or attempt.case_id != bundle.case_id:
            raise PortError(PortErrorCategory.CONFLICT, "bundle or attempt is outside this case")
        if attempt.bundle_digest != bundle.bundle_digest:
            raise PortError(
                PortErrorCategory.INTEGRITY, "attempt bundle digest does not match bundle"
            )
        if not any(item == entry for item in bundle.files):
            raise PortError(
                PortErrorCategory.INTEGRITY, "file entry is not registered in the bundle"
            )
        relative = (
            f"cases/{bundle.case_id}/runs/{attempt.run_id}/attempts/"
            f"{attempt.attempt_id}/{entry.logical_path}"
        )
        try:
            content = _read_owned(self.root, relative)
            return ResolvedFileContent(entry, content)
        except (StorageIntegrityError, ValueError) as error:
            raise PortError(PortErrorCategory.INTEGRITY, str(error)) from error

    def register_numeric_data(self, data: NumericResultData) -> NumericResultData:
        data.verify_content_digest()
        payload = encode_record(data)
        with _connect(self.registry_path) as connection:
            connection.execute(
                "INSERT OR REPLACE INTO numeric_data(data_id,payload) VALUES(?,?)",
                (data.reference.data_id, payload),
            )
        return data

    def resolve(self, reference: ResultDataRef) -> NumericResultData:
        with _connect(self.registry_path) as connection:
            row = connection.execute(
                "SELECT payload FROM numeric_data WHERE data_id=?", (reference.data_id,)
            ).fetchone()
        if row is None:
            raise PortError(PortErrorCategory.INTEGRITY, "numeric result data is not registered")
        try:
            result = decode_record(bytes(row["payload"]), NumericResultData)
            result.verify_content_digest()
        except Exception as error:
            raise PortError(
                PortErrorCategory.INTEGRITY, "numeric result data is corrupt"
            ) from error
        if result.reference != reference:
            raise PortError(PortErrorCategory.INTEGRITY, "numeric result identity does not match")
        return result

    def resolve_manifest_output(self, manifest_id: str, output_id: str) -> NumericResultData:
        with _connect(self.registry_path) as connection:
            row = connection.execute(
                "SELECT payload FROM manifests WHERE manifest_id=?", (manifest_id,)
            ).fetchone()
        if row is None:
            raise PortError(PortErrorCategory.INTEGRITY, "result manifest is not registered")
        try:
            manifest = decode_record(bytes(row["payload"]), ResultManifest)
        except Exception as error:
            raise PortError(PortErrorCategory.INTEGRITY, "result manifest is corrupt") from error
        for observation in manifest.read_result.observations:
            if observation.output_id == output_id and observation.data_ref is not None:
                return self.resolve(observation.data_ref)
        raise PortError(
            PortErrorCategory.INVALID_INPUT, "manifest output has no numeric data reference"
        )


def _empty_partial() -> PartialCaseSpec:
    return PartialCaseSpec()


__all__ = [
    "CaseStorage",
    "FailureInjector",
    "InjectedStorageFailure",
    "RegisteredSource",
    "StorageConflictError",
    "StorageIntegrityError",
]


class RegisteredSource:
    """Compatibility name for future source metadata consumers."""

    def __init__(self, asset: SourceAssetRef, source_kind: str) -> None:
        self.asset = asset
        self.source_kind = source_kind
