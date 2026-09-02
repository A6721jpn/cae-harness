"""Safe, testable deployment primitives for the development launcher.

The deployment contract deliberately has no dependency on the case workspace or
solver layers.  A build is copied into a private staging directory first, then
published by renaming directory entries.  The previous ``latest-development``
directory is retained under a private rollback directory so a failed publish or
an explicit rollback never has to mutate files in place.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Self

PRODUCT_DIRECTORY_NAME = "FEBioCaeWorkbench"
LATEST_DIRECTORY_NAME = "latest-development"
STAGING_DIRECTORY_NAME = ".staging"
ROLLBACK_DIRECTORY_NAME = ".rollback"
LAUNCHER_NAME = "febio-cae.exe"
BUILD_IDENTITY_NAME = "build-identity.json"
DEPLOYMENT_LOCK_NAME = ".deployment.lock"

_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_IDENTITY_FIELDS = frozenset(
    {
        "artifact_sha256",
        "build_id",
        "commit_sha",
        "payload_sha256",
        "version",
    }
)
_PAYLOAD_DIGEST_PREFIX = b"FEBio CAE payload v1\0"


class DeploymentError(RuntimeError):
    """Raised when a deployment cannot be validated or published safely."""


class DeploymentRollbackError(DeploymentError):
    """Raised when an already-published deployment cannot be rolled back."""


def _absolute_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return Path(os.path.abspath(os.fspath(path)))


def _reject_reparse_alias(value: str | Path, label: str) -> Path:
    """Reject symlinks, junctions, and other reparse-point ancestors."""

    absolute = _absolute_path(value)
    ancestors: list[Path] = []
    current = absolute
    while True:
        ancestors.append(current)
        if current == current.parent:
            break
        current = current.parent

    for ancestor in reversed(ancestors):
        try:
            if not os.path.lexists(os.fspath(ancestor)):
                continue
            metadata = ancestor.lstat()
        except OSError as error:
            raise DeploymentError(f"cannot inspect {label}: {absolute}") from error
        if stat.S_ISLNK(metadata.st_mode) or bool(
            getattr(metadata, "st_file_attributes", 0) & _REPARSE_POINT
        ):
            raise DeploymentError(f"{label} cannot contain a reparse-point alias")
    return absolute


def _normalise_token(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    if "\x00" in value or "/" in value or "\\" in value:
        raise ValueError(f"{label} must not contain path separators")
    return value


def _validate_sha256(value: str | None) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or len(value) != hashlib.sha256().digest_size * 2
        or any(character not in "0123456789abcdefABCDEF" for character in value)
    ):
        raise ValueError("artifact_sha256 must be a SHA-256 digest or None")
    return value.casefold()


@dataclass(frozen=True, slots=True)
class BuildIdentity:
    """Identity displayed by the launcher and persisted in a staged build."""

    commit_sha: str
    build_id: str
    version: str
    artifact_sha256: str | None = None
    payload_sha256: str | None = None

    def __post_init__(self) -> None:
        _normalise_token(self.commit_sha, "commit_sha")
        _normalise_token(self.build_id, "build_id")
        _normalise_token(self.version, "version")
        object.__setattr__(self, "artifact_sha256", _validate_sha256(self.artifact_sha256))
        object.__setattr__(self, "payload_sha256", _validate_sha256(self.payload_sha256))

    @property
    def commit(self) -> str:
        """Short compatibility alias for callers that call the commit ``commit``."""

        return self.commit_sha

    @property
    def identity(self) -> str:
        """Return the build-specific identity token."""

        return self.build_id

    def to_dict(self) -> dict[str, str | None]:
        payload: dict[str, str | None] = {
            "artifact_sha256": self.artifact_sha256,
            "build_id": self.build_id,
            "commit_sha": self.commit_sha,
            "version": self.version,
        }
        if self.payload_sha256 is not None:
            payload["payload_sha256"] = self.payload_sha256
        return payload

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> Self:
        unknown = set(payload) - _IDENTITY_FIELDS
        if unknown:
            names = ", ".join(sorted(str(name) for name in unknown))
            raise ValueError(f"unknown build identity field(s): {names}")
        missing = _IDENTITY_FIELDS - set(payload)
        required = missing - {"artifact_sha256"}
        if required:
            names = ", ".join(sorted(required))
            raise ValueError(f"missing build identity field(s): {names}")
        return cls(
            commit_sha=payload["commit_sha"],
            build_id=payload["build_id"],
            version=payload["version"],
            artifact_sha256=payload.get("artifact_sha256"),
            payload_sha256=payload.get("payload_sha256"),
        )


@dataclass(frozen=True, slots=True)
class DeploymentLayout:
    """Canonical application directories below ``%LOCALAPPDATA%``."""

    local_app_data: Path
    app_root: Path = field(init=False)
    latest: Path = field(init=False)
    staging_root: Path = field(init=False)
    rollback_root: Path = field(init=False)
    lock_path: Path = field(init=False)

    def __post_init__(self) -> None:
        local_app_data = _reject_reparse_alias(self.local_app_data, "LOCALAPPDATA")
        app_root = local_app_data / PRODUCT_DIRECTORY_NAME
        object.__setattr__(self, "local_app_data", local_app_data)
        object.__setattr__(self, "app_root", app_root)
        object.__setattr__(self, "latest", app_root / LATEST_DIRECTORY_NAME)
        object.__setattr__(self, "staging_root", app_root / STAGING_DIRECTORY_NAME)
        object.__setattr__(self, "rollback_root", app_root / ROLLBACK_DIRECTORY_NAME)
        object.__setattr__(self, "lock_path", app_root / DEPLOYMENT_LOCK_NAME)

    @classmethod
    def from_local_app_data(cls, local_app_data: str | Path) -> Self:
        return cls(Path(local_app_data))

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> Self:
        values = os.environ if environ is None else environ
        local_app_data = values.get("LOCALAPPDATA")
        if not local_app_data:
            raise DeploymentError("LOCALAPPDATA is not set")
        return cls.from_local_app_data(local_app_data)

    @property
    def latest_development(self) -> Path:
        """Descriptive alias for the fixed latest-development directory."""

        return self.latest

    @property
    def latest_development_path(self) -> Path:
        return self.latest

    @property
    def application_root(self) -> Path:
        return self.app_root

    @property
    def launcher(self) -> Path:
        return self.latest / LAUNCHER_NAME

    @property
    def launcher_path(self) -> Path:
        return self.launcher

    @property
    def identity_path(self) -> Path:
        return self.latest / BUILD_IDENTITY_NAME

    @property
    def deployment_lock(self) -> Path:
        """Path of the stable cross-process deployment lock file."""

        return self.lock_path


def _ensure_application_root(layout: DeploymentLayout) -> Path:
    app_root = _reject_reparse_alias(layout.app_root, "application root")
    try:
        app_root.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise DeploymentError(f"cannot create application root: {app_root}") from error
    return _reject_reparse_alias(app_root, "application root")


@contextmanager
def deployment_lock(layout: DeploymentLayout) -> Iterator[None]:
    """Hold the stable OS-owned lock across a complete publish or launch."""

    app_root = _ensure_application_root(layout)
    lock_path = _reject_reparse_alias(app_root / DEPLOYMENT_LOCK_NAME, "deployment lock")
    stream = None
    locked = False
    try:
        stream = lock_path.open("a+b")
    except OSError as error:
        raise DeploymentError(f"cannot acquire deployment lock: {lock_path}") from error
    try:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            flock = fcntl.flock  # type: ignore[attr-defined]
            flock(stream.fileno(), fcntl.LOCK_EX)  # type: ignore[attr-defined]
        locked = True
    except OSError as error:
        if stream is not None:
            with suppress(OSError):
                stream.close()
        raise DeploymentError(f"cannot acquire deployment lock: {lock_path}") from error
    try:
        yield
    finally:
        if stream is not None:
            if locked:
                with suppress(OSError):
                    if os.name == "nt":
                        import msvcrt

                        stream.seek(0)
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        flock = fcntl.flock  # type: ignore[attr-defined]
                        flock(stream.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]
            with suppress(OSError):
                stream.close()


def _write_json_atomic(path: Path, payload: Mapping[str, object]) -> None:
    parent = _reject_reparse_alias(path.parent, "deployment file parent")
    parent.mkdir(parents=True, exist_ok=True)
    _reject_reparse_alias(parent, "deployment file parent")
    target = _reject_reparse_alias(path, "deployment file")
    descriptor: int | None = None
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            dir=os.fspath(parent),
        )
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            descriptor = None
            json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(os.fspath(temporary), os.fspath(target))
    except OSError as error:
        raise DeploymentError(f"cannot write deployment file: {target}") from error
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink()


def _validate_tree(root: Path, label: str) -> None:
    root = _reject_reparse_alias(root, label)
    if not root.is_dir():
        raise DeploymentError(f"{label} must be a directory: {root}")
    for current, directory_names, file_names in os.walk(root, topdown=True, followlinks=False):
        current_path = _reject_reparse_alias(current, label)
        for name in directory_names + file_names:
            _reject_reparse_alias(current_path / name, label)


def _payload_entries(root: Path) -> Iterator[tuple[str, Path, str]]:
    _validate_tree(root, "payload")
    for current, directory_names, file_names in os.walk(root, topdown=True, followlinks=False):
        current_path = _reject_reparse_alias(current, "payload")
        directory_names.sort()
        file_names.sort()
        relative = current_path.relative_to(root)
        for name in directory_names:
            entry = current_path / name
            _reject_reparse_alias(entry, "payload")
            relative_name = (relative / name).as_posix()
            yield "directory", entry, relative_name
        for name in file_names:
            entry = current_path / name
            _reject_reparse_alias(entry, "payload")
            if relative == Path() and name == BUILD_IDENTITY_NAME:
                continue
            if not entry.is_file():
                raise DeploymentError(f"payload contains a non-file entry: {entry}")
            relative_name = (relative / name).as_posix()
            yield "file", entry, relative_name


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise DeploymentError(f"cannot read payload file: {path}") from error
    return digest.hexdigest()


def compute_payload_sha256(root: str | Path) -> str:
    """Return a deterministic SHA-256 digest for an exact, safe payload tree."""

    payload_root = _reject_reparse_alias(root, "payload")
    digest = hashlib.sha256(_PAYLOAD_DIGEST_PREFIX)
    for kind, path, relative_name in _payload_entries(payload_root):
        encoded_name = relative_name.encode("utf-8", "surrogateescape")
        digest.update(kind[0].encode("ascii"))
        digest.update(len(encoded_name).to_bytes(8, "big"))
        digest.update(encoded_name)
        if kind == "file":
            try:
                size_before = path.stat().st_size
            except OSError as error:
                raise DeploymentError(f"cannot inspect payload file: {path}") from error
            digest.update(size_before.to_bytes(8, "big"))
            try:
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                size_after = path.stat().st_size
            except OSError as error:
                raise DeploymentError(f"cannot read payload file: {path}") from error
            if size_before != size_after:
                raise DeploymentError(f"payload changed while it was hashed: {path}")
    return digest.hexdigest()


def _bound_payload_sha256(
    payload_sha256: str,
    identity: BuildIdentity,
) -> str:
    digest = hashlib.sha256(b"FEBio CAE bound payload v1\0")
    for value in (
        identity.commit_sha,
        identity.build_id,
        identity.version,
        identity.artifact_sha256 or "",
        payload_sha256,
    ):
        encoded = value.encode("utf-8", "surrogateescape")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _artifact_candidates(root: Path, payload_sha256: str) -> set[str]:
    candidates = {payload_sha256}
    wheel_paths = tuple(
        path
        for kind, path, relative_name in _payload_entries(root)
        if kind == "file" and relative_name.casefold().endswith(".whl")
    )
    if len(wheel_paths) == 1:
        candidates.add(_hash_file(wheel_paths[0]))
    return candidates


def _copy_tree(source: Path, destination: Path) -> None:
    _validate_tree(source, "build source")
    if not destination.is_dir():
        raise DeploymentError(f"build staging directory is missing: {destination}")
    for current, directory_names, file_names in os.walk(source, topdown=True, followlinks=False):
        current_path = _reject_reparse_alias(current, "build source")
        relative = current_path.relative_to(source)
        destination_directory = destination / relative
        for name in directory_names:
            source_directory = current_path / name
            _reject_reparse_alias(source_directory, "build source")
            (destination_directory / name).mkdir()
        for name in file_names:
            source_file = current_path / name
            _reject_reparse_alias(source_file, "build source")
            if not source_file.is_file():
                raise DeploymentError(f"build source contains a non-file entry: {source_file}")
            shutil.copy2(source_file, destination_directory / name)


def _remove_owned_tree(path: Path, label: str) -> None:
    if not os.path.lexists(os.fspath(path)):
        return
    _validate_tree(path, label)
    shutil.rmtree(path)


def _new_owned_directory(parent: Path, prefix: str) -> Path:
    parent = _reject_reparse_alias(parent, "deployment directory")
    parent.mkdir(parents=True, exist_ok=True)
    _reject_reparse_alias(parent, "deployment directory")
    try:
        return Path(tempfile.mkdtemp(prefix=prefix, dir=os.fspath(parent)))
    except OSError as error:
        raise DeploymentError(
            f"cannot create deployment staging directory under {parent}"
        ) from error


def _read_identity_file(directory: Path) -> BuildIdentity:
    identity_path = directory / BUILD_IDENTITY_NAME
    try:
        payload = json.loads(identity_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DeploymentError(f"cannot read build identity from {directory}") from error
    if not isinstance(payload, Mapping):
        raise DeploymentError(f"build identity is not an object: {identity_path}")
    try:
        return BuildIdentity.from_mapping(payload)
    except (TypeError, ValueError) as error:
        raise DeploymentError(f"invalid build identity: {identity_path}") from error


def verify_payload_identity(directory: str | Path, identity: BuildIdentity) -> BuildIdentity:
    """Verify that a persisted identity still describes its payload tree."""

    if identity.payload_sha256 is None:
        raise DeploymentError("build identity is missing payload_sha256")
    payload_root = _reject_reparse_alias(directory, "published payload")
    actual_tree_digest = compute_payload_sha256(payload_root)
    expected_digest = _bound_payload_sha256(actual_tree_digest, identity)
    if identity.payload_sha256 != expected_digest:
        raise DeploymentError("published payload digest does not match build identity")
    return identity


def _identity_from_directory(directory: Path) -> BuildIdentity:
    try:
        identity = _read_identity_file(directory)
        return verify_payload_identity(directory, identity)
    except DeploymentError as error:
        raise DeploymentRollbackError(str(error)) from error


def _materialise_identity(staging: Path, requested: BuildIdentity) -> BuildIdentity:
    actual_tree_digest = compute_payload_sha256(staging)
    artifact_digest = requested.artifact_sha256 or actual_tree_digest
    effective = replace(requested, artifact_sha256=artifact_digest)
    if requested.payload_sha256 is not None:
        bound_digest = _bound_payload_sha256(actual_tree_digest, effective)
        if requested.payload_sha256 not in {actual_tree_digest, bound_digest}:
            raise DeploymentError("claimed payload digest does not match staged payload")
    elif requested.artifact_sha256 is not None:
        if requested.artifact_sha256 not in _artifact_candidates(staging, actual_tree_digest):
            raise DeploymentError("claimed artifact digest does not match staged payload")
    return replace(effective, payload_sha256=_bound_payload_sha256(actual_tree_digest, effective))


@dataclass(frozen=True, slots=True)
class DeploymentReceipt:
    """Receipt for a published build and its optional rollback directory."""

    layout: DeploymentLayout
    build_identity: BuildIdentity
    latest: Path
    rollback_path: Path | None
    previous_identity: BuildIdentity | None = None

    @property
    def identity(self) -> BuildIdentity:
        return self.build_identity

    @property
    def previous_latest(self) -> Path | None:
        return self.rollback_path

    def rollback(self) -> Path:
        """Restore the exact build replaced by this receipt.

        The current latest build must still have this receipt's identity.  This
        compare-and-swap style check prevents an old receipt from overwriting a
        newer deployment.
        """

        with deployment_lock(self.layout):
            current = _reject_reparse_alias(self.latest, "latest deployment")
            if not current.is_dir():
                raise DeploymentRollbackError("latest deployment is missing")
            if _identity_from_directory(current) != self.build_identity:
                raise DeploymentRollbackError("latest deployment identity does not match receipt")

            if self.rollback_path is None:
                raise DeploymentRollbackError("rollback build is missing")
            rollback = _reject_reparse_alias(self.rollback_path, "rollback deployment")
            if not rollback.is_dir():
                raise DeploymentRollbackError("rollback build is missing")
            rollback_identity = _identity_from_directory(rollback)
            if self.previous_identity is not None and rollback_identity != self.previous_identity:
                raise DeploymentRollbackError("rollback deployment identity does not match receipt")

            temporary = _new_owned_directory(self.layout.staging_root, "rollback-current-")
            _remove_owned_tree(temporary, "rollback staging")
            moved_current = False
            try:
                os.replace(os.fspath(current), os.fspath(temporary))
                moved_current = True
                os.replace(os.fspath(rollback), os.fspath(current))
                _remove_owned_tree(temporary, "rollback staging")
            except DeploymentRollbackError:
                if moved_current and not current.exists() and temporary.exists():
                    os.replace(os.fspath(temporary), os.fspath(current))
                raise
            except OSError as error:
                if moved_current and not current.exists() and temporary.exists():
                    try:
                        os.replace(os.fspath(temporary), os.fspath(current))
                    except OSError as restore_error:
                        raise DeploymentRollbackError(
                            "rollback failed and restoring latest also failed"
                        ) from restore_error
                raise DeploymentRollbackError("rollback failed") from error
            return current


def stage_latest_development(
    source: str | Path,
    local_app_data: str | Path | None = None,
    identity: BuildIdentity | Mapping[str, Any] | None = None,
    *,
    layout: DeploymentLayout | None = None,
    keep_rollback: bool = True,
) -> DeploymentReceipt:
    """Copy and atomically publish a clean build as ``latest-development``.

    ``local_app_data`` is explicit by default in tests and callers that want a
    non-default root.  If omitted, ``LOCALAPPDATA`` is read but no directory is
    created until this function is called.  A source directory inside the target
    application root is rejected to avoid staging from a path that is being
    replaced.
    """

    if layout is not None and local_app_data is not None:
        raise TypeError("pass either layout or local_app_data, not both")
    if layout is None:
        if local_app_data is None:
            layout = DeploymentLayout.from_environment()
        else:
            layout = DeploymentLayout.from_local_app_data(local_app_data)
    if identity is None:
        raise TypeError("identity is required")
    build_identity = (
        identity if isinstance(identity, BuildIdentity) else BuildIdentity.from_mapping(identity)
    )

    source_path = _reject_reparse_alias(source, "build source")
    if not source_path.is_dir():
        raise DeploymentError(f"build source must be a directory: {source_path}")
    if source_path == layout.app_root or source_path.is_relative_to(layout.app_root):
        raise DeploymentError("build source cannot be inside the application root")

    with deployment_lock(layout):
        staging = _new_owned_directory(
            layout.staging_root,
            f"{build_identity.build_id}-",
        )
        try:
            if (source_path / BUILD_IDENTITY_NAME).exists():
                raise DeploymentError(
                    f"build source cannot contain reserved file: {BUILD_IDENTITY_NAME}"
                )
            _copy_tree(source_path, staging)
            effective_identity = _materialise_identity(staging, build_identity)
            _write_json_atomic(staging / BUILD_IDENTITY_NAME, effective_identity.to_dict())
            verify_payload_identity(staging, effective_identity)

            latest = _reject_reparse_alias(layout.latest, "latest deployment")
            rollback_path: Path | None = None
            empty_legacy_path: Path | None = None
            previous_identity: BuildIdentity | None = None
            if os.path.lexists(os.fspath(latest)):
                _validate_tree(latest, "latest deployment")
                try:
                    previous_identity = _identity_from_directory(latest)
                except DeploymentRollbackError as error:
                    try:
                        is_empty = next(latest.iterdir(), None) is None
                    except OSError as inspection_error:
                        raise DeploymentError(
                            "cannot inspect unidentified existing deployment"
                        ) from inspection_error
                    if not is_empty:
                        raise DeploymentError("existing deployment identity is invalid") from error
                    empty_legacy_path = layout.staging_root / f"empty-{uuid.uuid4().hex}"
                    os.replace(os.fspath(latest), os.fspath(empty_legacy_path))
                    _validate_tree(empty_legacy_path, "empty legacy deployment")
                    if next(empty_legacy_path.iterdir(), None) is not None:
                        os.replace(os.fspath(empty_legacy_path), os.fspath(latest))
                        empty_legacy_path = None
                        raise DeploymentError("unidentified existing deployment changed") from error
                else:
                    rollback_parent = _reject_reparse_alias(layout.rollback_root, "rollback root")
                    rollback_parent.mkdir(parents=True, exist_ok=True)
                    _reject_reparse_alias(rollback_parent, "rollback root")
                    rollback_path = rollback_parent / f"previous-{uuid.uuid4().hex}"
                    os.replace(os.fspath(latest), os.fspath(rollback_path))

            try:
                os.replace(os.fspath(staging), os.fspath(latest))
            except OSError as error:
                if rollback_path is not None and not latest.exists() and rollback_path.exists():
                    try:
                        os.replace(os.fspath(rollback_path), os.fspath(latest))
                    except OSError as restore_error:
                        raise DeploymentError(
                            "publish failed and restoring the previous deployment also failed"
                        ) from restore_error
                elif (
                    empty_legacy_path is not None
                    and not latest.exists()
                    and empty_legacy_path.exists()
                ):
                    try:
                        os.replace(os.fspath(empty_legacy_path), os.fspath(latest))
                    except OSError as restore_error:
                        raise DeploymentError(
                            "publish failed and restoring the empty deployment also failed"
                        ) from restore_error
                raise DeploymentError("cannot atomically publish latest-development") from error
            if empty_legacy_path is not None:
                try:
                    empty_legacy_path.rmdir()
                except OSError as error:
                    raise DeploymentError(
                        "published build but could not remove empty legacy deployment"
                    ) from error
            if not keep_rollback and rollback_path is not None:
                _remove_owned_tree(rollback_path, "rollback deployment")
                rollback_path = None
                previous_identity = None
            return DeploymentReceipt(
                layout,
                effective_identity,
                latest,
                rollback_path,
                previous_identity,
            )
        except DeploymentError:
            if staging.exists():
                _remove_owned_tree(staging, "deployment staging")
            raise
        except (OSError, ValueError) as error:
            if staging.exists():
                _remove_owned_tree(staging, "deployment staging")
            raise DeploymentError("deployment failed") from error


@dataclass(frozen=True, slots=True)
class AtomicDeployer:
    """Object-oriented facade for callers that reuse one deployment layout."""

    layout: DeploymentLayout

    def stage(
        self,
        source: str | Path,
        identity: BuildIdentity | Mapping[str, Any],
        *,
        keep_rollback: bool = True,
    ) -> DeploymentReceipt:
        return stage_latest_development(
            source,
            identity=identity,
            layout=self.layout,
            keep_rollback=keep_rollback,
        )


DeploymentManager = AtomicDeployer


def deployment_layout(local_app_data: str | Path | None = None) -> DeploymentLayout:
    """Resolve the canonical layout from an explicit root or LOCALAPPDATA."""

    return (
        DeploymentLayout.from_environment()
        if local_app_data is None
        else DeploymentLayout.from_local_app_data(local_app_data)
    )


__all__ = [
    "AtomicDeployer",
    "BUILD_IDENTITY_NAME",
    "BuildIdentity",
    "compute_payload_sha256",
    "DEPLOYMENT_LOCK_NAME",
    "DeploymentError",
    "DeploymentLayout",
    "DeploymentManager",
    "DeploymentReceipt",
    "DeploymentRollbackError",
    "LAUNCHER_NAME",
    "LATEST_DIRECTORY_NAME",
    "PRODUCT_DIRECTORY_NAME",
    "ROLLBACK_DIRECTORY_NAME",
    "STAGING_DIRECTORY_NAME",
    "deployment_lock",
    "deployment_layout",
    "stage_latest_development",
    "verify_payload_identity",
]
