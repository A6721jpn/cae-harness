"""SQLite-backed registered case state and owned immutable file publication."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import fields, is_dataclass, replace
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import cast

from febio_cae.domain.artifacts import (
    FileEntry,
    MeshArtifact,
    ResolvedFileContent,
    SourceAssetContent,
    SourceAssetRef,
)
from febio_cae.domain.canonical import canonical_bytes
from febio_cae.domain.case_draft import CaseDraft
from febio_cae.domain.case_revision import CaseRevision
from febio_cae.domain.codec import decode_record, encode_record
from febio_cae.domain.compatibility import CapabilityStatus, CompatibilityProfile
from febio_cae.domain.evidence import EvidenceRef
from febio_cae.domain.execution import AttemptRecord, ExecutionBundle, ExecutionSetting
from febio_cae.domain.lifecycle import RunState
from febio_cae.domain.mesh_policy import NumericalProfileRef
from febio_cae.domain.partial_case_spec import PartialCaseSpec
from febio_cae.domain.ports import (
    PortError,
    PortErrorCategory,
    TrustedOwnerContext,
)
from febio_cae.domain.questions import IssuedQuestion
from febio_cae.domain.results import NumericResultData, ReadStatus, ResultDataRef, ResultManifest
from febio_cae.domain.units import Dimension, Quantity

from ._ownership import identity, lease, pin_directories, pinned_read
from ._sqlite import connect as _connect
from .catalog import validate_case_id
from .mesh_quality import (
    MeshQualityRecord,
    MeshQualityRegistration,
    PlanarDemoRegistration,
    decode_mesh_quality,
)


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


def _serialized[**P, R](method: Callable[P, R]) -> Callable[P, R]:
    @wraps(method)
    def call(*args: P.args, **kwargs: P.kwargs) -> R:
        storage = cast("CaseStorage", args[0])
        with storage.transaction():
            return method(*args, **kwargs)

    return call


def nested_evidence(value: object) -> tuple[EvidenceRef, ...]:
    if isinstance(value, EvidenceRef):
        return (value,)
    if isinstance(value, Mapping):
        return tuple(item for child in value.values() for item in nested_evidence(child))
    if isinstance(value, (tuple, list)):
        return tuple(item for child in value for item in nested_evidence(child))
    if is_dataclass(value):
        return tuple(
            item for field in fields(value) for item in nested_evidence(getattr(value, field.name))
        )
    return ()


def check_answer_values(
    current: PartialCaseSpec, incoming: PartialCaseSpec, targets: tuple[str, ...]
) -> None:
    def check(before: object, after: object, path: str) -> None:
        if before == after or any(
            path == target or path.startswith(target + ".") for target in targets
        ):
            return
        if isinstance(before, dict) and isinstance(after, dict):
            for key in before.keys() | after.keys():
                check(before.get(key), after.get(key), f"{path}.{key}" if path else key)
            return
        if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
            for index, (old, new) in enumerate(zip(before, after, strict=True)):
                check(old, new, f"{path}.{index}")
            return
        raise StorageConflictError(f"answer changes unissued target {path}")

    for field in fields(current):
        if field.init:
            before = getattr(current, field.name)
            after = getattr(incoming, field.name)
            check(
                None if before is None else before.to_dict(),
                None if after is None else after.to_dict(),
                field.name,
            )


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
    with pin_directories(root):
        target = _owned_path(root, relative)
        with pin_directories(target.parent, create=True):
            return _write_pinned(root, relative, content, token=token)


def _write_pinned(root: Path, relative: str, content: bytes, *, token: str | None = None) -> Path:
    target = _owned_path(root, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_links(target.parent, root)
    suffix = token or uuid.uuid4().hex[:12]
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
    with pinned_read(target) as stream:
        return stream.read()


class CaseStorage:
    """One registered case root. All durable IDs are looked up in its SQLite registry."""

    def __init__(
        self, root: Path | str, *, failure_injector: FailureInjector | None = None
    ) -> None:
        self.root = Path(root).absolute()
        if not self.root.is_dir() or _is_reparse(self.root):
            raise StorageIntegrityError("registered case root is unavailable")
        self._root_identity = identity(self.root)
        self.registry_path = self.root / "registry.sqlite3"
        _assert_no_links(self.registry_path, self.root)
        self.failure_injector = failure_injector
        self._reading_attempt: AttemptRecord | None = None
        with pin_directories(self.root):
            self._initialize_schema()
        with self.transaction(blocking=False, recovery=True) as acquired:
            if acquired:
                self._recover_publications()

    @contextmanager
    def transaction(self, *, blocking: bool = True, recovery: bool = False) -> Iterator[bool]:
        try:
            with pin_directories(self.root):
                if identity(self.root) != self._root_identity:
                    raise StorageIntegrityError("registered root identity changed")
                with lease(self.root, blocking=blocking, recovery=recovery) as acquired:
                    yield acquired
        except OSError as error:
            raise StorageIntegrityError(str(error)) from error

    @contextmanager
    def evidence_snapshot(self) -> Iterator[None]:
        """Deny writes/deletes to every registered source through freeze/recovery."""
        with self.transaction(), ExitStack() as stack:
            with _connect(self.registry_path) as connection:
                rows = connection.execute("SELECT * FROM sources").fetchall()
            for row in rows:
                stream = stack.enter_context(
                    pinned_read(_owned_path(self.root, row["relative_path"]))
                )
                if hashlib.sha256(stream.read()).hexdigest() != row["content_digest"]:
                    raise StorageIntegrityError("registered source bytes were modified")
            yield

    @contextmanager
    def revision_snapshot(self, case_id: str, revision_id: str) -> Iterator[CaseRevision]:
        with self.transaction():
            relative = f"cases/{case_id}/revisions/{revision_id}/revision.json"
            with pinned_read(_owned_path(self.root, relative)):
                yield self.get_revision(case_id, revision_id)

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
        with pin_directories(Path(root).absolute(), create=True), lease(Path(root).absolute()):
            return cls._initialize_pinned(
                root,
                case_id=case_id,
                source_asset=source_asset,
                source_kind=source_kind,
                source_content=source_content,
                created_at=created_at,
            )

    @classmethod
    def _initialize_pinned(
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
        payload = encode_record(draft)
        with _connect(storage.registry_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM case_state LIMIT 1").fetchone():
                raise StorageConflictError("case root is already initialized")
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
            _write_atomic(root_path, relative, source_content)
            connection.commit()
        return storage

    def _initialize_schema(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with _connect(self.registry_path) as connection:
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name='root_identity'"
            ).fetchone():
                recorded = connection.execute(
                    "SELECT identity FROM root_identity WHERE singleton=1"
                ).fetchone()
                if recorded is not None:
                    if recorded["identity"] != repr(self._root_identity):
                        raise StorageIntegrityError("registered root identity changed")
                    return
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
                    expected_generation INTEGER,
                    expected_draft_id TEXT,
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
                CREATE TABLE IF NOT EXISTS root_identity (singleton INTEGER PRIMARY KEY, identity TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS mesh_quality (
                    profile_id TEXT NOT NULL, digest TEXT NOT NULL, payload BLOB NOT NULL,
                    evidence_payload BLOB NOT NULL, PRIMARY KEY(profile_id,digest)
                );
                CREATE TABLE IF NOT EXISTS revision_mesh_quality (
                    revision_id TEXT PRIMARY KEY, generation INTEGER NOT NULL,
                    payload BLOB NOT NULL, evidence_payload BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS applied_patches (digest TEXT PRIMARY KEY, generation INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS current_frozen (
                    case_id TEXT PRIMARY KEY, revision_id TEXT NOT NULL,
                    generation INTEGER NOT NULL, draft_id TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS revision_contexts (
                    revision_id TEXT PRIMARY KEY, generation INTEGER, draft_id TEXT
                );
                CREATE TABLE IF NOT EXISTS execution_lineage (
                    attempt_id TEXT PRIMARY KEY, bundle BLOB NOT NULL,
                    mesh BLOB NOT NULL, profile BLOB NOT NULL,
                    required_outputs BLOB NOT NULL, sealed_files BLOB,
                    read_candidate BLOB,
                    writer_closed INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS attempt_history (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, attempt_id TEXT NOT NULL,
                    payload BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS native_execution (
                    attempt_id TEXT PRIMARY KEY, process_root TEXT NOT NULL UNIQUE,
                    drained INTEGER NOT NULL DEFAULT 0
                );
                """
            )
            recorded = connection.execute(
                "SELECT identity FROM root_identity WHERE singleton=1"
            ).fetchone()
            root_id = repr(self._root_identity)
            if recorded is None:
                connection.execute("INSERT INTO root_identity VALUES(1,?)", (root_id,))
            elif recorded["identity"] != root_id:
                raise StorageIntegrityError("registered root identity changed")
            for column, definition in (
                ("expected_generation", "INTEGER"),
                ("expected_draft_id", "TEXT"),
                ("mesh_quality_required", "INTEGER NOT NULL DEFAULT 0"),
            ):
                try:
                    connection.execute(f"ALTER TABLE publications ADD COLUMN {column} {definition}")
                except sqlite3.OperationalError as error:
                    if "duplicate column name" not in str(error).casefold():
                        raise

    def _fault(self, boundary: str) -> None:
        if self.failure_injector is not None:
            self.failure_injector(boundary)

    def _discard_prepared_publication(
        self, transaction_id: str, relative: str, payload: bytes
    ) -> None:
        target = _owned_path(self.root, relative)
        if target.exists():
            if target.read_bytes() != payload:
                raise StorageIntegrityError(
                    f"publication target does not match prepared bytes: {relative}"
                )
            target.unlink()
        temporary = target.with_name(f".{target.name}.{transaction_id}.tmp")
        if temporary.exists():
            temporary.unlink()
        with _connect(self.registry_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "DELETE FROM revision_mesh_quality WHERE revision_id IN (SELECT record_id FROM publications WHERE transaction_id=?) AND revision_id NOT IN (SELECT revision_id FROM revisions)",
                    (transaction_id,),
                )
                connection.execute(
                    "DELETE FROM publications WHERE transaction_id=?", (transaction_id,)
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def _recover_publications(self) -> None:
        with _connect(self.registry_path) as connection:
            if not connection.execute("SELECT 1 FROM publications LIMIT 1").fetchone():
                return
        with self.evidence_snapshot():
            self._recover_publications_pinned()

    def _recover_publications_pinned(self) -> None:
        with _connect(self.registry_path) as connection, ExitStack() as paths:
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
                    paths.enter_context(pin_directories(final.parent, create=True))
                    payload = bytes(row["payload"])
                    if row["state"] == "COMMITTED":
                        connection.execute(
                            "DELETE FROM publications WHERE transaction_id=?",
                            (row["transaction_id"],),
                        )
                        continue
                    revision = decode_record(payload, CaseRevision)
                    self._verify_revision_sources(revision)
                    expected_generation = row["expected_generation"]
                    if row["mesh_quality_required"]:
                        self._verify_mesh_quality_snapshot(
                            connection, revision, expected_generation
                        )
                    if expected_generation is not None:
                        current = self._case_row(connection, revision.case_id)
                        if current["current_generation"] != expected_generation or (
                            row["expected_draft_id"] is not None
                            and current["current_draft_id"] != row["expected_draft_id"]
                        ):
                            if final.exists():
                                if final.read_bytes() != payload:
                                    raise StorageIntegrityError(
                                        f"publication target does not match prepared bytes: {relative}"
                                    )
                                final.unlink()
                            temporary = final.with_name(
                                f".{final.name}.{row['transaction_id']}.tmp"
                            )
                            if temporary.exists():
                                temporary.unlink()
                            connection.execute(
                                "DELETE FROM revision_mesh_quality WHERE revision_id=? AND revision_id NOT IN (SELECT revision_id FROM revisions)",
                                (revision.revision_id,),
                            )
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
                        try:
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
                                    str(row["created_at"]),
                                ),
                            )
                        except sqlite3.IntegrityError as error:
                            existing = connection.execute(
                                "SELECT payload,relative_path FROM revisions WHERE revision_id=?",
                                (revision.revision_id,),
                            ).fetchone()
                            if (
                                existing is None
                                or bytes(existing["payload"]) != payload
                                or str(existing["relative_path"]) != relative
                            ):
                                raise StorageIntegrityError(
                                    "prepared revision conflicts with an existing registered revision"
                                ) from error
                        connection.execute(
                            "INSERT OR IGNORE INTO revision_contexts VALUES(?,?,?)",
                            (revision.revision_id, expected_generation, row["expected_draft_id"]),
                        )
                        if expected_generation is not None:
                            connection.execute(
                                "INSERT OR REPLACE INTO current_frozen VALUES(?,?,?,?)",
                                (
                                    revision.case_id,
                                    revision.revision_id,
                                    expected_generation,
                                    row["expected_draft_id"],
                                ),
                            )
                    temporary = final.with_name(f".{final.name}.{row['transaction_id']}.tmp")
                    if not final.exists():
                        connection.execute(
                            "DELETE FROM revision_mesh_quality WHERE revision_id=? AND revision_id NOT IN (SELECT revision_id FROM revisions)",
                            (revision.revision_id,),
                        )
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

    @_serialized
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

    @_serialized
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

    @_serialized
    def set_draft(
        self, draft: CaseDraft, *, expected_generation: int, patch_digest: str | None = None
    ) -> CaseDraft:
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
                if patch_digest is not None:
                    frozen = connection.execute(
                        "SELECT revision_id FROM current_frozen WHERE case_id=?", (draft.case_id,)
                    ).fetchone()
                    if frozen is None or frozen["revision_id"] != draft.parent_revision_id:
                        raise StorageConflictError(
                            "patch parent is not the current frozen revision"
                        )
                    if connection.execute(
                        "SELECT 1 FROM applied_patches WHERE digest=?", (patch_digest,)
                    ).fetchone():
                        raise StorageConflictError("patch was already applied")
                    connection.execute(
                        "INSERT INTO applied_patches VALUES(?,?)",
                        (patch_digest, expected_generation),
                    )
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

    @_serialized
    def issue_question(self, question: IssuedQuestion) -> IssuedQuestion:
        payload = encode_record(question)
        case_id = question.case_id
        question_id = question.question_id
        with _connect(self.registry_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                current = self._case_row(connection, case_id)
                if (
                    current["current_generation"] != question.generation
                    or current["current_draft_id"] != question.draft_id
                ):
                    raise StorageConflictError("question context is stale")
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

    @_serialized
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

    @_serialized
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
                previous = connection.execute(
                    "SELECT payload FROM drafts WHERE draft_id=?", (current["current_draft_id"],)
                ).fetchone()
                assert previous is not None
                old_draft = decode_record(bytes(previous["payload"]), CaseDraft)
                issued = decode_record(bytes(row["payload"]), IssuedQuestion)
                check_answer_values(old_draft.values, draft.values, tuple(issued.target_fields))
                if (draft.parent_revision_id, draft.parent_spec_digest, draft.input_intent) != (
                    old_draft.parent_revision_id,
                    old_draft.parent_spec_digest,
                    old_draft.input_intent,
                ):
                    raise StorageConflictError("answer changed unissued draft context")
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

    @_serialized
    def revision_generation(self, revision_id: str) -> int | None:
        with _connect(self.registry_path) as connection:
            row = connection.execute(
                "SELECT generation FROM revision_contexts WHERE revision_id=?", (revision_id,)
            ).fetchone()
        return None if row is None else row["generation"]

    def register_revision_if_current(
        self,
        revision: CaseRevision,
        *,
        expected_generation: int,
        mesh_quality_required: bool = False,
    ) -> CaseRevision:
        return self._publish_revision(
            revision,
            expected_generation=expected_generation,
            mesh_quality_required=mesh_quality_required,
        )

    @_serialized
    def current_frozen_revision(self, case_id: str) -> str | None:
        with _connect(self.registry_path) as connection:
            row = connection.execute(
                "SELECT revision_id FROM current_frozen WHERE case_id=?", (case_id,)
            ).fetchone()
        return None if row is None else str(row["revision_id"])

    def _publish_revision(
        self,
        revision: CaseRevision,
        *,
        expected_generation: int | None,
        mesh_quality_required: bool = False,
    ) -> CaseRevision:
        with self.evidence_snapshot():
            self._verify_revision_sources(revision)
            parent = _owned_path(
                self.root,
                f"cases/{revision.case_id}/revisions/{revision.revision_id}/revision.json",
            ).parent
            with pin_directories(parent, create=True):
                return self._publish_revision_pinned(
                    revision,
                    expected_generation=expected_generation,
                    mesh_quality_required=mesh_quality_required,
                )

    def _verify_revision_sources(self, revision: CaseRevision) -> None:
        for evidence in nested_evidence(revision):
            asset = self.source_asset(evidence.reference)
            if (
                asset.content_digest != evidence.content_digest
                or self.source_kind(evidence.reference) != evidence.source_kind
            ):
                raise StorageIntegrityError("revision evidence differs from registered source")
            self.resolve_source(asset)
        if self.source_asset("cad").content_digest != revision.spec.geometry.source_step_digest:
            raise StorageIntegrityError("revision CAD identity differs from registration")

    def _publish_revision_pinned(
        self,
        revision: CaseRevision,
        *,
        expected_generation: int | None,
        mesh_quality_required: bool = False,
    ) -> CaseRevision:
        self._recover_publications()
        payload = encode_record(revision)
        relative = f"cases/{revision.case_id}/revisions/{revision.revision_id}/revision.json"
        transaction_id = uuid.uuid4().hex[:12]
        duplicate = False
        quality = (
            self.resolve_mesh_quality(revision.spec.mesh_policy.quality_profile)
            if mesh_quality_required
            else None
        )
        with _connect(self.registry_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                current = self._case_row(connection, revision.case_id)
                if (
                    expected_generation is not None
                    and current["current_generation"] != expected_generation
                ):
                    raise StorageConflictError("draft changed before freeze")
                existing = connection.execute(
                    """
                    SELECT revision_id,relative_path,payload
                    FROM revisions
                    WHERE revision_id=? OR relative_path=?
                    LIMIT 1
                    """,
                    (revision.revision_id, relative),
                ).fetchone()
                if existing is not None:
                    if (
                        existing["revision_id"] != revision.revision_id
                        or existing["relative_path"] != relative
                        or bytes(existing["payload"]) != payload
                    ):
                        raise StorageConflictError(
                            "revision identity is already registered with different bytes"
                        )
                    duplicate = True
                    if mesh_quality_required:
                        self._verify_mesh_quality_snapshot(
                            connection, revision, expected_generation
                        )
                else:
                    target = _owned_path(self.root, relative)
                    if target.exists():
                        raise StorageConflictError(
                            "revision target already exists without a matching registry record"
                        )
                    expected_draft_id = (
                        str(current["current_draft_id"])
                        if expected_generation is not None
                        else None
                    )
                    if quality is not None:
                        connection.execute(
                            "INSERT INTO revision_mesh_quality VALUES(?,?,?,?)",
                            (
                                revision.revision_id,
                                expected_generation,
                                quality.to_bytes(),
                                self._mesh_quality_evidence(quality),
                            ),
                        )
                    connection.execute(
                        """
                        INSERT INTO publications(
                            transaction_id,kind,record_id,relative_path,payload,state,
                            expected_generation,expected_draft_id,created_at
                        ) VALUES(?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            transaction_id,
                            "revision",
                            revision.revision_id,
                            relative,
                            payload,
                            "PREPARED",
                            expected_generation,
                            expected_draft_id,
                            _now(),
                        ),
                    )
                    if mesh_quality_required:
                        connection.execute(
                            "UPDATE publications SET mesh_quality_required=1 WHERE transaction_id=?",
                            (transaction_id,),
                        )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        if duplicate:
            if _read_owned(self.root, relative) != payload:
                raise StorageIntegrityError("registered revision file does not match SQLite")
            return revision
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
                    if (
                        current["current_generation"] != expected_generation
                        or current["current_draft_id"] != expected_draft_id
                    ):
                        raise StorageConflictError("draft changed before revision finalization")
                if mesh_quality_required:
                    self._verify_mesh_quality_snapshot(connection, revision, expected_generation)
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
                connection.execute(
                    "INSERT INTO revision_contexts VALUES(?,?,?)",
                    (
                        revision.revision_id,
                        expected_generation,
                        expected_draft_id if expected_generation is not None else None,
                    ),
                )
                if expected_generation is not None:
                    connection.execute(
                        "INSERT OR REPLACE INTO current_frozen VALUES(?,?,?,?)",
                        (
                            revision.case_id,
                            revision.revision_id,
                            expected_generation,
                            expected_draft_id,
                        ),
                    )
                connection.commit()
            except StorageConflictError:
                connection.rollback()
                self._discard_prepared_publication(transaction_id, relative, payload)
                raise
            except Exception:
                connection.rollback()
                raise
        self._fault("after_commit")
        with _connect(self.registry_path) as connection:
            connection.execute("DELETE FROM publications WHERE transaction_id=?", (transaction_id,))
        return revision

    @_serialized
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

    def _mesh_quality_evidence(self, record: MeshQualityRecord) -> bytes:
        items: list[dict[str, object]] = []
        try:
            for evidence in (
                record.admission_evidence
                if isinstance(record, PlanarDemoRegistration)
                else record.qualification_evidence
            ):
                asset = self.source_asset(evidence.reference)
                if (
                    asset.content_digest != evidence.content_digest
                    or self.source_kind(evidence.reference) != evidence.source_kind
                ):
                    raise StorageIntegrityError("qualification evidence identity differs")
                content = self.resolve_source(asset).content
                items.append(
                    {
                        "evidence": evidence.to_dict(),
                        "source": asset.to_dict(),
                        "content_hex": content.hex(),
                    }
                )
        except (StorageConflictError, StorageIntegrityError) as error:
            raise PortError(PortErrorCategory.INTEGRITY, str(error)) from error
        return canonical_bytes(items)

    def _verified_mesh_quality(
        self, payload: bytes, evidence_payload: bytes, ref: NumericalProfileRef
    ) -> MeshQualityRecord:
        try:
            record = decode_mesh_quality(payload)
            if record.reference != ref:
                raise ValueError("mesh quality identity/digest differs")
            if self._mesh_quality_evidence(record) != evidence_payload:
                raise ValueError("mesh quality qualification snapshot differs")
            return record
        except (ValueError, TypeError, KeyError, StorageIntegrityError) as error:
            raise PortError(PortErrorCategory.INTEGRITY, str(error)) from error

    def register_mesh_quality(self, record: MeshQualityRecord) -> NumericalProfileRef:
        """Trusted case-local registration; never exposed as a public JSON import."""
        if not isinstance(record, (MeshQualityRegistration, PlanarDemoRegistration)):
            raise PortError(
                PortErrorCategory.INVALID_INPUT, "explicit mesh quality record required"
            )
        with self.evidence_snapshot():
            evidence = self._mesh_quality_evidence(record)
            ref = record.reference
            with _connect(self.registry_path) as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM mesh_quality WHERE profile_id=? AND digest=?",
                    (ref.profile_id, ref.record_digest),
                ).fetchone()
                if row is not None:
                    self._verified_mesh_quality(
                        bytes(row["payload"]), bytes(row["evidence_payload"]), ref
                    )
                else:
                    connection.execute(
                        "INSERT INTO mesh_quality VALUES(?,?,?,?)",
                        (ref.profile_id, ref.record_digest, record.to_bytes(), evidence),
                    )
                connection.commit()
            return ref

    def resolve_mesh_quality(self, ref: NumericalProfileRef) -> MeshQualityRecord:
        with self.evidence_snapshot():
            if ref.purpose != "mesh_quality":
                raise PortError(PortErrorCategory.INTEGRITY, "wrong numerical profile purpose")
            with _connect(self.registry_path) as connection:
                row = connection.execute(
                    "SELECT * FROM mesh_quality WHERE profile_id=? AND digest=?",
                    (ref.profile_id, ref.record_digest),
                ).fetchone()
            if row is None:
                raise PortError(
                    PortErrorCategory.UNSUPPORTED_CAPABILITY,
                    "explicit mesh quality criteria are not registered",
                )
            return self._verified_mesh_quality(
                bytes(row["payload"]), bytes(row["evidence_payload"]), ref
            )

    def _verify_mesh_quality_snapshot(
        self, connection: sqlite3.Connection, revision: CaseRevision, generation: int | None
    ) -> MeshQualityRecord:
        row = connection.execute(
            "SELECT * FROM revision_mesh_quality WHERE revision_id=?", (revision.revision_id,)
        ).fetchone()
        if row is None or generation is None or row["generation"] != generation:
            raise PortError(
                PortErrorCategory.INTEGRITY,
                "registered revision criteria context is missing or stale",
            )
        return self._verified_mesh_quality(
            bytes(row["payload"]),
            bytes(row["evidence_payload"]),
            revision.spec.mesh_policy.quality_profile,
        )

    def resolve_revision_mesh_quality(self, revision: CaseRevision) -> MeshQualityRecord:
        with self.evidence_snapshot():
            if (
                self.get_revision(revision.case_id, revision.revision_id).to_bytes()
                != revision.to_bytes()
            ):
                raise PortError(
                    PortErrorCategory.INTEGRITY, "mesh quality revision is not registered"
                )
            with _connect(self.registry_path) as connection:
                return self._verify_mesh_quality_snapshot(
                    connection, revision, self.revision_generation(revision.revision_id)
                )

    @_serialized
    def source_kind(self, asset_id: str) -> str:
        _safe_identifier(asset_id, "asset_id")
        with _connect(self.registry_path) as connection:
            row = connection.execute(
                "SELECT source_kind FROM sources WHERE asset_id=?", (asset_id,)
            ).fetchone()
        if row is None:
            raise StorageConflictError(f"unknown source asset {asset_id!r}")
        return str(row["source_kind"])

    @_serialized
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
            connection.execute("BEGIN IMMEDIATE")
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
                self.resolve_source(reference)
                connection.commit()
                return reference
            try:
                connection.execute(
                    "INSERT INTO sources(asset_id,case_id,source_kind,media_type,content_digest,relative_path) VALUES(?,?,?,?,?,?)",
                    (asset_id, self._case_id(), source_kind, media_type, digest, relative),
                )
                _write_atomic(self.root, relative, content)
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

    @_serialized
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

    @_serialized
    def claim(self, owner: TrustedOwnerContext) -> TrustedOwnerContext:
        if owner.case_id != self._case_id():
            raise PortError(PortErrorCategory.CONFLICT, "owner case is not this registered case")
        payload = owner.__class__.__name__.encode("utf-8") + b":" + owner.run_id.encode("utf-8")
        with _connect(self.registry_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                current = self._case_row(connection, owner.case_id)
                if owner.owner_generation != current["current_generation"]:
                    raise PortError(PortErrorCategory.CONFLICT, "owner generation is stale")
                for field in ("run_id", "attempt_id"):
                    _safe_identifier(getattr(owner, field), field)
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

    @_serialized
    def validate(self, owner: TrustedOwnerContext, attempt: AttemptRecord) -> TrustedOwnerContext:
        if owner.case_id != self._case_id():
            raise PortError(PortErrorCategory.CONFLICT, "owner case is not this registered case")
        if (
            attempt.case_id != owner.case_id
            or attempt.run_id != owner.run_id
            or attempt.attempt_id != owner.attempt_id
            or attempt.owner_generation != owner.owner_generation
        ):
            raise PortError(
                PortErrorCategory.CONFLICT, "attempt is outside the trusted owner scope"
            )
        with _connect(self.registry_path) as connection:
            row = connection.execute(
                """
                SELECT * FROM owners
                WHERE case_id=? AND run_id=? AND attempt_id=? AND owner_generation=?
                """,
                (owner.case_id, owner.run_id, owner.attempt_id, owner.owner_generation),
            ).fetchone()
        if row is None:
            raise PortError(PortErrorCategory.CONFLICT, "owner claim is not registered")
        if bytes(row["payload"]) != encode_record(attempt):
            raise PortError(
                PortErrorCategory.CONFLICT, "attempt is not the registered lifecycle snapshot"
            )
        self._lineage(attempt)
        return owner

    def _attempt(self, owner: TrustedOwnerContext) -> AttemptRecord:
        with _connect(self.registry_path) as connection:
            row = connection.execute(
                "SELECT payload FROM owners WHERE run_id=? AND attempt_id=? AND case_id=? AND owner_generation=?",
                (owner.run_id, owner.attempt_id, owner.case_id, owner.owner_generation),
            ).fetchone()
        try:
            if row is None:
                raise ValueError("unknown owner")
            return decode_record(bytes(row["payload"]), AttemptRecord)
        except ValueError as error:
            raise PortError(
                PortErrorCategory.CONFLICT, "no registered execution attempt"
            ) from error

    def _lineage(self, attempt: AttemptRecord) -> tuple[ExecutionBundle, sqlite3.Row]:
        with _connect(self.registry_path) as connection:
            row = connection.execute(
                "SELECT * FROM execution_lineage WHERE attempt_id=?", (attempt.attempt_id,)
            ).fetchone()
        if row is None:
            raise PortError(PortErrorCategory.CONFLICT, "execution lineage is not registered")
        bundle = decode_record(bytes(row["bundle"]), ExecutionBundle)
        revision = self.get_revision(attempt.case_id, attempt.revision_id)
        mesh = decode_record(bytes(row["mesh"]), MeshArtifact)
        if (
            bundle.bundle_digest != attempt.bundle_digest
            or bundle.revision_id != revision.revision_id
            or bundle.case_id != revision.case_id
            or bundle.spec_digest != revision.spec_digest
            or bundle.mesh_digest != mesh.artifact_digest
        ):
            raise PortError(PortErrorCategory.INTEGRITY, "execution lineage changed")
        self._verify_revision_sources(revision)
        for entry in bundle.files:
            self._file_content(attempt, entry)
        return bundle, row

    def _file_content(self, attempt: AttemptRecord, entry: FileEntry) -> ResolvedFileContent:
        relative = f"cases/{attempt.case_id}/runs/{attempt.run_id}/attempts/{attempt.attempt_id}/{entry.logical_path}"
        try:
            return ResolvedFileContent(entry, _read_owned(self.root, relative))
        except (StorageIntegrityError, OSError, ValueError) as error:
            raise PortError(PortErrorCategory.INTEGRITY, str(error)) from error

    @_serialized
    def _register_execution(
        self,
        owner: TrustedOwnerContext,
        bundle: ExecutionBundle,
        mesh: MeshArtifact,
        profile: CompatibilityProfile,
        inputs: Mapping[str, bytes],
    ) -> AttemptRecord:
        revision = self.get_revision(owner.case_id, bundle.revision_id)
        if (
            self.current_draft(owner.case_id).generation != owner.owner_generation
            or self.revision_generation(revision.revision_id) != owner.owner_generation
            or bundle.case_id != owner.case_id
            or bundle.spec_digest != revision.spec_digest
            or bundle.mesh_digest != mesh.artifact_digest
            or bundle.tool != profile.solver
            or bundle.profile_id != profile.profile_id
            or hashlib.sha256(profile.to_bytes()).hexdigest()
            != revision.spec.solver_policy.profile.record_digest
            or any(item.status is not CapabilityStatus.SUPPORTED for item in profile.capabilities)
        ):
            raise PortError(PortErrorCategory.CONFLICT, "execution preparation lineage is stale")
        self._verify_revision_sources(revision)
        if set(inputs) != {entry.logical_path for entry in bundle.files}:
            raise PortError(PortErrorCategory.INTEGRITY, "compiled input membership differs")
        base = f"cases/{owner.case_id}/runs/{owner.run_id}/attempts/{owner.attempt_id}"
        destination = _owned_path(self.root, base)
        if Path(bundle.cwd).absolute() != destination or destination.exists():
            raise PortError(
                PortErrorCategory.CONFLICT, "execution requires a fresh owned destination"
            )
        attempt = AttemptRecord(
            owner.attempt_id,
            owner.run_id,
            owner.case_id,
            bundle.revision_id,
            owner.owner_generation,
            bundle.bundle_digest,
            RunState.CREATED,
            None,
            bundle.settings,
        )
        with _connect(self.registry_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            owner_row = connection.execute(
                "SELECT payload FROM owners WHERE run_id=? AND attempt_id=?",
                (owner.run_id, owner.attempt_id),
            ).fetchone()
            if (
                owner_row is None
                or connection.execute(
                    "SELECT 1 FROM execution_lineage WHERE attempt_id=?", (owner.attempt_id,)
                ).fetchone()
            ):
                raise PortError(
                    PortErrorCategory.CONFLICT, "execution owner is absent or already prepared"
                )
            connection.execute(
                "INSERT INTO execution_lineage(attempt_id,bundle,mesh,profile,required_outputs) VALUES(?,?,?,?,?)",
                (
                    owner.attempt_id,
                    encode_record(bundle),
                    encode_record(mesh),
                    encode_record(profile),
                    canonical_bytes(revision.spec.outputs.to_dict()),
                ),
            )
            for entry in bundle.files:
                ResolvedFileContent(entry, inputs[entry.logical_path])
                _write_atomic(self.root, f"{base}/{entry.logical_path}", inputs[entry.logical_path])
            connection.execute(
                "UPDATE owners SET payload=? WHERE run_id=?", (encode_record(attempt), owner.run_id)
            )
            connection.execute(
                "INSERT INTO attempt_history(attempt_id,payload) VALUES(?,?)",
                (attempt.attempt_id, encode_record(attempt)),
            )
            connection.commit()
        return attempt

    @_serialized
    def _prepare_runner(self, owner: TrustedOwnerContext, process_root: Path) -> None:
        """Private controller preparation, before any runner launch or root creation."""
        attempt = self._attempt(owner)
        self.validate(owner, attempt)
        if attempt.state is not RunState.CREATED:
            raise PortError(PortErrorCategory.CONFLICT, "runner preparation requires CREATED")
        try:
            relative = process_root.absolute().relative_to(self.root).as_posix()
            checked = _owned_path(self.root, relative)
        except (ValueError, OSError, StorageIntegrityError) as error:
            raise PortError(
                PortErrorCategory.INTEGRITY, "runner root is outside storage"
            ) from error
        if checked.exists():
            raise PortError(PortErrorCategory.CONFLICT, "runner root must be fresh")
        with _connect(self.registry_path) as connection:
            try:
                connection.execute(
                    "INSERT INTO native_execution(attempt_id,process_root) VALUES(?,?)",
                    (attempt.attempt_id, str(checked)),
                )
            except sqlite3.IntegrityError as error:
                raise PortError(PortErrorCategory.CONFLICT, "runner is already prepared") from error

    def _native_context(self, attempt: AttemptRecord) -> sqlite3.Row:
        with _connect(self.registry_path) as connection:
            row = connection.execute(
                "SELECT * FROM native_execution WHERE attempt_id=?", (attempt.attempt_id,)
            ).fetchone()
        if row is None:
            raise PortError(PortErrorCategory.CONFLICT, "no prepared native execution")
        return row

    def _persist_issued(self, owner: TrustedOwnerContext, issued: AttemptRecord) -> None:
        with _connect(self.registry_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE owners SET payload=? WHERE run_id=?", (encode_record(issued), owner.run_id)
            )
            connection.execute(
                "INSERT INTO attempt_history(attempt_id,payload) VALUES(?,?)",
                (issued.attempt_id, encode_record(issued)),
            )
            connection.commit()

    @_serialized
    def _accept_runner_start(self, owner: TrustedOwnerContext, issued: AttemptRecord) -> None:
        """Called only by the controller holding the actual Runner.start return value."""
        current = self._attempt(owner)
        self.validate(owner, current)
        native = self._native_context(current)
        bundle, _ = self._lineage(current)
        revision = self.get_revision(owner.case_id, current.revision_id)
        expected_settings = tuple(
            sorted(
                (
                    *[
                        s
                        for s in bundle.settings
                        if s.name not in {"attempt_root", "max_elapsed_seconds"}
                    ],
                    ExecutionSetting("attempt_root", native["process_root"]),
                    ExecutionSetting(
                        "max_elapsed_seconds", revision.spec.budget.max_elapsed.to_si().value
                    ),
                ),
                key=lambda s: s.name,
            )
        )
        process = issued.process
        if (
            current.state is not RunState.CREATED
            or issued.state is not RunState.RUNNING
            or replace(issued, state=current.state, process=None, settings=current.settings)
            != current
            or tuple(issued.settings) != expected_settings
            or process is None
            or process.cwd != native["process_root"]
            or tuple(process.argv) != tuple(bundle.argv)
            or process.executable != bundle.argv[0]
            or process.executable_digest != bundle.tool.executable_digest
            or process.thread_count != bundle.thread_count
        ):
            raise PortError(PortErrorCategory.CONFLICT, "issued runner differs from preparation")
        self._persist_issued(owner, current.transition_to(RunState.PREPARING))
        self._persist_issued(owner, issued)

    @_serialized
    def _accept_runner_poll(
        self, owner: TrustedOwnerContext, previous: AttemptRecord, issued: AttemptRecord
    ) -> None:
        """Persist an actual issued poll, never adopt a snapshot during validate()."""
        current = self._attempt(owner)
        self.validate(owner, current)
        self._native_context(current)
        if current != previous or replace(issued, state=current.state) != current:
            raise PortError(PortErrorCategory.CONFLICT, "issued poll snapshot is stale or foreign")
        if current.state not in {RunState.RUNNING, RunState.DRAINING}:
            raise PortError(PortErrorCategory.CONFLICT, "runner poll requires a live lifecycle")
        if current.state is issued.state:
            return
        if current.state is RunState.RUNNING and issued.state in {
            RunState.VALIDATING,
            RunState.FAILED,
            RunState.CANCELLED,
        }:
            current = current.transition_to(RunState.DRAINING)
            self._persist_issued(owner, current)
        current.transition_to(issued.state)
        self._persist_issued(owner, issued)
        if issued.state is RunState.VALIDATING:
            with _connect(self.registry_path) as connection:
                connection.execute(
                    "UPDATE native_execution SET drained=1 WHERE attempt_id=?", (issued.attempt_id,)
                )

    @_serialized
    def _seal_native_output(self, owner: TrustedOwnerContext) -> tuple[FileEntry, ...]:
        """Freeze the fixed XPLT file only after controller-recorded owned drain."""
        attempt = self._attempt(owner)
        self.validate(owner, attempt)
        native = self._native_context(attempt)
        _, lineage = self._lineage(attempt)
        if (
            attempt.state is not RunState.VALIDATING
            or not native["drained"]
            or attempt.process is None
            or lineage["sealed_files"] is not None
        ):
            raise PortError(
                PortErrorCategory.CONFLICT, "native output requires unsealed owned drain"
            )
        relative = (
            (Path(native["process_root"]) / "output/results.xplt").relative_to(self.root).as_posix()
        )
        with pinned_read(_owned_path(self.root, relative)) as stream:
            if os.fstat(stream.fileno()).st_size > 32 * 1024 * 1024:
                raise PortError(PortErrorCategory.INTEGRITY, "native XPLT exceeds reader limit")
            content = stream.read()
        entry = FileEntry(
            "output/results.xplt", hashlib.sha256(content).hexdigest(), len(content), "result"
        )
        base = f"cases/{attempt.case_id}/runs/{attempt.run_id}/attempts/{attempt.attempt_id}"
        _write_atomic(self.root, f"{base}/{entry.logical_path}", content)
        with _connect(self.registry_path) as connection:
            connection.execute(
                "UPDATE execution_lineage SET sealed_files=?,writer_closed=1 WHERE attempt_id=?",
                (canonical_bytes([entry.to_dict()]), attempt.attempt_id),
            )
        return (entry,)

    @_serialized
    def _transition_attempt(self, owner: TrustedOwnerContext, target: RunState) -> AttemptRecord:
        attempt = self._attempt(owner)
        self.validate(owner, attempt)
        if target is RunState.SUCCEEDED:
            with _connect(self.registry_path) as connection:
                rows = connection.execute(
                    "SELECT manifest_id FROM manifests WHERE attempt_id=?", (attempt.attempt_id,)
                ).fetchall()
            if len(rows) != 1:
                raise PortError(PortErrorCategory.CONFLICT, "success requires one published result")
            self.get_manifest(rows[0]["manifest_id"])
        updated = attempt.transition_to(target)
        with _connect(self.registry_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE owners SET payload=? WHERE run_id=?", (encode_record(updated), owner.run_id)
            )
            connection.execute(
                "INSERT INTO attempt_history(attempt_id,payload) VALUES(?,?)",
                (attempt.attempt_id, encode_record(updated)),
            )
            connection.commit()
        return updated

    @_serialized
    def _seal_outputs(
        self, owner: TrustedOwnerContext, outputs: Mapping[str, bytes]
    ) -> tuple[FileEntry, ...]:
        """Internal synchronous byte producer route; only this service opens writers."""
        attempt = self._attempt(owner)
        if attempt.state is not RunState.DRAINING:
            raise PortError(
                PortErrorCategory.CONFLICT, "outputs require a draining registered attempt"
            )
        _, row = self._lineage(attempt)
        required = json.loads(bytes(row["required_outputs"]))
        ids = {item["request_id"] for item in required["requests"]}
        if set(outputs) != ids or any(not isinstance(value, bytes) for value in outputs.values()):
            raise PortError(
                PortErrorCategory.INTEGRITY, "producer omitted required output membership"
            )
        if row["sealed_files"] is not None:
            raise PortError(PortErrorCategory.CONFLICT, "outputs are already sealed")
        entries = []
        for output_id in sorted(ids):
            _safe_identifier(output_id, "output_id")
            data = outputs[output_id]
            entry = FileEntry(
                f"outputs/{output_id}.bin", hashlib.sha256(data).hexdigest(), len(data), output_id
            )
            relative = f"cases/{attempt.case_id}/runs/{attempt.run_id}/attempts/{attempt.attempt_id}/{entry.logical_path}"
            _write_atomic(self.root, relative, data)
            entries.append(entry)
        with _connect(self.registry_path) as connection:
            connection.execute(
                "UPDATE execution_lineage SET sealed_files=?,writer_closed=1 WHERE attempt_id=?",
                (canonical_bytes([entry.to_dict() for entry in entries]), attempt.attempt_id),
            )
        return tuple(entries)

    def _sealed_entries(self, row: sqlite3.Row) -> tuple[FileEntry, ...]:
        if not row["writer_closed"] or row["sealed_files"] is None:
            raise PortError(PortErrorCategory.CONFLICT, "registered output writers are not closed")
        return tuple(
            decode_record(canonical_bytes(item), FileEntry)
            for item in json.loads(bytes(row["sealed_files"]))
        )

    @_serialized
    def _read_candidate(
        self,
        owner: TrustedOwnerContext,
        reader: Callable[
            [AttemptRecord, ExecutionBundle, tuple[FileEntry, ...], CaseStorage], ResultManifest
        ],
    ) -> ResultManifest:
        attempt = self._attempt(owner)
        bundle, lineage = self._lineage(attempt)
        if attempt.state is not RunState.VALIDATING or lineage["read_candidate"] is not None:
            raise PortError(
                PortErrorCategory.CONFLICT, "reader requires an unread validating attempt"
            )
        self._reading_attempt = attempt
        try:
            manifest = reader(attempt, bundle, self._sealed_entries(lineage), self)
        finally:
            self._reading_attempt = None
        with _connect(self.registry_path) as connection:
            connection.execute(
                "UPDATE execution_lineage SET read_candidate=? WHERE attempt_id=?",
                (encode_record(manifest), attempt.attempt_id),
            )
        return manifest

    @_serialized
    def publish_manifest(
        self, owner: TrustedOwnerContext, manifest: ResultManifest
    ) -> ResultManifest:
        with self.evidence_snapshot(), ExitStack() as stack:
            attempt = self._attempt(owner)
            bundle, lineage = self._lineage(attempt)
            for entry in (*bundle.files, *self._sealed_entries(lineage)):
                relative = f"cases/{attempt.case_id}/runs/{attempt.run_id}/attempts/{attempt.attempt_id}/{entry.logical_path}"
                stack.enter_context(pinned_read(_owned_path(self.root, relative)))
            return self._publish_manifest_pinned(owner, manifest)

    def _publish_manifest_pinned(
        self, owner: TrustedOwnerContext, manifest: ResultManifest
    ) -> ResultManifest:
        if owner.case_id != self._case_id():
            raise PortError(PortErrorCategory.CONFLICT, "owner case is not this registered case")
        if manifest.attempt_id != owner.attempt_id:
            raise PortError(PortErrorCategory.CONFLICT, "manifest attempt is outside owner scope")
        if manifest.read_result.status is not ReadStatus.VALIDATED:
            raise PortError(PortErrorCategory.INTEGRITY, "manifest read result is not validated")
        payload = encode_record(manifest)
        attempt = self._attempt(owner)
        self.validate(owner, attempt)
        _, lineage = self._lineage(attempt)
        if lineage["read_candidate"] is None or bytes(lineage["read_candidate"]) != payload:
            raise PortError(
                PortErrorCategory.CONFLICT, "manifest was not returned by the registered reader"
            )
        sealed = self._sealed_entries(lineage)
        if attempt.state is not RunState.VALIDATING or (
            attempt.process is not None and not self._native_context(attempt)["drained"]
        ):
            raise PortError(
                PortErrorCategory.CONFLICT, "registered terminal writer state is required"
            )
        if set(manifest.files) != set(sealed):
            raise PortError(PortErrorCategory.INTEGRITY, "manifest omits or changes sealed outputs")
        required = json.loads(bytes(lineage["required_outputs"]))
        requests = {item["request_id"]: item for item in required["requests"]}
        observations = {item.output_id: item for item in manifest.read_result.observations}
        if set(observations) != set(requests):
            raise PortError(PortErrorCategory.INTEGRITY, "manifest omits required observations")
        for output_id, request in requests.items():
            observation = observations[output_id]
            if (
                observation.location != request["location"]
                or observation.measure_id != request["measure_id"]
                or observation.frame.value != request["frame"]
                or observation.state_count < len(required["saved_times"])
            ):
                raise PortError(
                    PortErrorCategory.INTEGRITY,
                    "observation differs from registered output request",
                )
        profile = decode_record(bytes(lineage["profile"]), CompatibilityProfile)
        if manifest.read_result.reader != profile.reader:
            raise PortError(
                PortErrorCategory.INTEGRITY, "manifest reader differs from registered profile"
            )
        mappings = {item.canonical_id: item for item in profile.output_mappings}
        for output_id, request in requests.items():
            mapping = mappings.get(request["quantity_id"])
            observation = observations[output_id]
            if mapping is None or (
                observation.unit,
                observation.value_type,
                observation.measure_id,
                observation.location,
                observation.frame,
            ) != (
                mapping.unit,
                mapping.value_type,
                mapping.measure_id,
                mapping.location,
                mapping.frame,
            ):
                raise PortError(
                    PortErrorCategory.INTEGRITY,
                    "reader observation differs from registered mapping",
                )
        with _connect(self.registry_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    """
                    SELECT * FROM owners
                    WHERE case_id=? AND run_id=? AND attempt_id=? AND owner_generation=?
                    """,
                    (owner.case_id, owner.run_id, owner.attempt_id, owner.owner_generation),
                ).fetchone()
                if row is None:
                    raise PortError(PortErrorCategory.CONFLICT, "owner claim is not registered")
                try:
                    attempt = decode_record(bytes(row["payload"]), AttemptRecord)
                except Exception as error:
                    raise PortError(
                        PortErrorCategory.CONFLICT,
                        "owner has no validated registered attempt",
                    ) from error
                if (
                    attempt.case_id != owner.case_id
                    or attempt.run_id != owner.run_id
                    or attempt.attempt_id != owner.attempt_id
                    or attempt.owner_generation != owner.owner_generation
                ):
                    raise PortError(
                        PortErrorCategory.CONFLICT,
                        "registered attempt is outside the trusted owner scope",
                    )
                if manifest.bundle_digest != attempt.bundle_digest:
                    raise PortError(
                        PortErrorCategory.INTEGRITY,
                        "manifest bundle is not the registered attempt bundle",
                    )
                for entry in manifest.files:
                    relative = (
                        f"cases/{attempt.case_id}/runs/{attempt.run_id}/attempts/"
                        f"{attempt.attempt_id}/{entry.logical_path}"
                    )
                    try:
                        content = _read_owned(self.root, relative)
                    except StorageIntegrityError as error:
                        raise PortError(PortErrorCategory.INTEGRITY, str(error)) from error
                    if (
                        len(content) != entry.size_bytes
                        or hashlib.sha256(content).hexdigest() != entry.digest
                    ):
                        raise PortError(
                            PortErrorCategory.INTEGRITY,
                            f"manifest file bytes do not match the registered output: {entry.logical_path}",
                        )
                self._verify_numeric_observations(connection, manifest, attempt, lineage)
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

    def _verify_numeric_observations(
        self,
        connection: sqlite3.Connection,
        manifest: ResultManifest,
        attempt: AttemptRecord,
        lineage: sqlite3.Row,
    ) -> None:
        """Validate the actual reader-issued rows in the publishing transaction."""
        required = json.loads(bytes(lineage["required_outputs"]))
        requests = {item["request_id"]: item for item in required["requests"]}
        observations = {item.output_id: item for item in manifest.read_result.observations}
        profile = decode_record(bytes(lineage["profile"]), CompatibilityProfile)
        mappings = {item.canonical_id: item for item in profile.output_mappings}
        if set(observations) != set(requests):
            raise PortError(PortErrorCategory.INTEGRITY, "required numeric outputs differ")
        try:
            for output_id, request in requests.items():
                observation = observations[output_id]
                ref = observation.data_ref
                if (
                    ref is None
                    or ref.attempt_id != attempt.attempt_id
                    or ref.bundle_digest != attempt.bundle_digest
                ):
                    raise ValueError("numeric observation is outside the registered attempt/bundle")
                row = connection.execute(
                    "SELECT payload FROM numeric_data WHERE data_id=?", (ref.data_id,)
                ).fetchone()
                if row is None:
                    raise ValueError("numeric observation was not registered by the reader")
                data = decode_record(bytes(row["payload"]), NumericResultData)
                data.verify_content_digest()
                mapping = mappings.get(request["quantity_id"])
                if data.reference != ref or data.mapping != mapping:
                    raise ValueError("numeric payload differs from observation reference/mapping")
                if (
                    observation.state_count != len(data.axis_values)
                    or data.axis_id not in {"time", "state_time"}
                    or Quantity(1, data.axis_unit).dimension != Dimension(time=1)
                    or (data.mapping.value_type == "scalar" and len(data.component_ids) != 1)
                    or (
                        observation.location,
                        observation.value_type,
                        observation.unit,
                        observation.frame,
                        observation.measure_id,
                    )
                    != (
                        data.mapping.location,
                        data.mapping.value_type,
                        data.mapping.unit,
                        data.mapping.frame,
                        data.mapping.measure_id,
                    )
                ):
                    raise ValueError("observation contradicts actual numeric axes/count/mapping")
                actual = {
                    Quantity(value, data.axis_unit).to_si().value for value in data.axis_values
                }
                expected = {
                    Quantity(item["value"], item["unit"]).to_si().value
                    for item in required["saved_times"]
                }
                if not expected.issubset(actual):
                    raise ValueError("actual numeric states omit required saved times")
        except (ValueError, TypeError, KeyError) as error:
            raise PortError(PortErrorCategory.INTEGRITY, str(error)) from error

    @_serialized
    def resolve_file(
        self, entry: FileEntry, bundle: ExecutionBundle, attempt: AttemptRecord
    ) -> ResolvedFileContent:
        owner = TrustedOwnerContext(
            attempt.case_id, attempt.run_id, attempt.attempt_id, attempt.owner_generation
        )
        self.validate(owner, attempt)
        registered, lineage = self._lineage(attempt)
        if registered.to_bytes() != bundle.to_bytes():
            raise PortError(PortErrorCategory.INTEGRITY, "bundle differs from registered lineage")
        if bundle.case_id != self._case_id() or attempt.case_id != bundle.case_id:
            raise PortError(PortErrorCategory.CONFLICT, "bundle or attempt is outside this case")
        if attempt.bundle_digest != bundle.bundle_digest:
            raise PortError(
                PortErrorCategory.INTEGRITY, "attempt bundle digest does not match bundle"
            )
        members = tuple(bundle.files) + (
            self._sealed_entries(lineage) if lineage["writer_closed"] else ()
        )
        if entry not in members:
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

    @_serialized
    def get_manifest(self, manifest_id: str) -> ResultManifest:
        with _connect(self.registry_path) as connection:
            row = connection.execute(
                "SELECT payload FROM manifests WHERE manifest_id=?", (manifest_id,)
            ).fetchone()
            owner_row = connection.execute(
                "SELECT payload FROM owners WHERE attempt_id=(SELECT attempt_id FROM manifests WHERE manifest_id=?)",
                (manifest_id,),
            ).fetchone()
        if row is None or owner_row is None:
            raise PortError(PortErrorCategory.INTEGRITY, "result manifest is not registered")
        manifest = decode_record(bytes(row["payload"]), ResultManifest)
        attempt = decode_record(bytes(owner_row["payload"]), AttemptRecord)
        _, lineage = self._lineage(attempt)
        if set(manifest.files) != set(self._sealed_entries(lineage)):
            raise PortError(PortErrorCategory.INTEGRITY, "manifest output membership changed")
        for entry in manifest.files:
            self._file_content(attempt, entry)
        with _connect(self.registry_path) as connection:
            self._verify_numeric_observations(connection, manifest, attempt, lineage)
        return manifest

    @_serialized
    def register_numeric_data(self, data: NumericResultData) -> NumericResultData:
        attempt = self._reading_attempt
        if (
            attempt is None
            or data.reference.attempt_id != attempt.attempt_id
            or data.reference.bundle_digest != attempt.bundle_digest
        ):
            raise PortError(
                PortErrorCategory.CONFLICT, "numeric data requires a registered reader invocation"
            )
        _, lineage = self._lineage(attempt)
        profile = decode_record(bytes(lineage["profile"]), CompatibilityProfile)
        if data.mapping not in profile.output_mappings:
            raise PortError(PortErrorCategory.INTEGRITY, "numeric mapping is not registered")
        data.verify_content_digest()
        payload = encode_record(data)
        with _connect(self.registry_path) as connection:
            row = connection.execute(
                "SELECT payload FROM numeric_data WHERE data_id=?", (data.reference.data_id,)
            ).fetchone()
            if row is not None and bytes(row["payload"]) != payload:
                raise PortError(
                    PortErrorCategory.CONFLICT, "numeric data is already registered differently"
                )
            connection.execute(
                "INSERT OR IGNORE INTO numeric_data(data_id,payload) VALUES(?,?)",
                (data.reference.data_id, payload),
            )
        return data

    @_serialized
    def resolve(self, reference: ResultDataRef) -> NumericResultData:
        with _connect(self.registry_path) as connection:
            manifests = connection.execute(
                "SELECT manifest_id FROM manifests WHERE attempt_id=?", (reference.attempt_id,)
            ).fetchall()
            row = connection.execute(
                "SELECT payload FROM numeric_data WHERE data_id=?", (reference.data_id,)
            ).fetchone()
        registered = False
        for candidate in manifests:
            manifest = self.get_manifest(candidate["manifest_id"])
            if manifest.bundle_digest == reference.bundle_digest and any(
                item.data_ref == reference for item in manifest.read_result.observations
            ):
                registered = True
                break
        if not registered:
            raise PortError(
                PortErrorCategory.CONFLICT, "numeric data is outside registered result lineage"
            )
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

    @_serialized
    def resolve_manifest_output(self, manifest_id: str, output_id: str) -> NumericResultData:
        self.get_manifest(manifest_id)
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
