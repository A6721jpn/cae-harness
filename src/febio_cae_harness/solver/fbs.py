"""Synthetic, authority-bound FBS result validation.

This module deliberately contains no FEBio Studio runtime.  A manager binds a
single adapter to a non-empty runtime identity and issues an opaque authority.
Only that authority can be used to validate an attempt XPLT artifact; all
issued validations remain synthetic and unverified provenance.
"""

from __future__ import annotations

import contextlib
import hashlib
import math
import os
import stat
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol, SupportsIndex, cast

__all__ = [
    "FbsAdapterAuthority",
    "FbsAdapterManager",
    "FbsAdapterProtocol",
    "FbsValidation",
    "validate_requested_fields",
]

_AdapterReader = Callable[[Path, Sequence[str]], object]
_PROVENANCE = "synthetic-unverified"
_AUTHORITY_TOKEN = object()


class FbsAdapterProtocol(Protocol):
    """Minimal adapter contract; no FBS implementation is bundled."""

    def read_fields(self, xplt_path: Path, fields: Sequence[str]) -> Mapping[str, object]:
        """Read requested result fields from ``xplt_path``."""


class FbsAdapterAuthority:
    """Opaque capability issued by :class:`FbsAdapterManager`."""

    __slots__ = ("_record", "__weakref__")

    def __new__(cls, token: object | None = None) -> FbsAdapterAuthority:
        if cls is not FbsAdapterAuthority or token is not _AUTHORITY_TOKEN:
            raise TypeError("FbsAdapterAuthority instances are manager-issued")
        return super().__new__(cls)

    def __init_subclass__(cls, **kwargs: object) -> None:
        del kwargs
        raise TypeError("FbsAdapterAuthority cannot be subclassed")

    def __copy__(self) -> FbsAdapterAuthority:
        raise TypeError("FbsAdapterAuthority cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> FbsAdapterAuthority:
        del memo
        raise TypeError("FbsAdapterAuthority cannot be copied")

    def __reduce__(self) -> str | tuple[Any, ...]:
        raise TypeError("FbsAdapterAuthority cannot be serialized")

    def __reduce_ex__(self, protocol: SupportsIndex) -> str | tuple[Any, ...]:
        del protocol
        raise TypeError("FbsAdapterAuthority cannot be serialized")


@dataclass(slots=True)
class _ManagerRecord:
    manager: FbsAdapterManager
    adapter: object
    reader: _AdapterReader
    runtime_identity: str
    attempt_root: Path | None
    root_binding: _RootBinding | None
    authority: FbsAdapterAuthority | None = None


@dataclass(slots=True)
class _AuthorityRecord:
    authority: FbsAdapterAuthority
    manager: FbsAdapterManager
    adapter: object
    reader: _AdapterReader
    runtime_identity: str
    attempt_root: Path | None
    root_binding: _RootBinding | None


@dataclass(slots=True)
class _ValidationRecord:
    validation: FbsValidation
    authority: FbsAdapterAuthority
    runtime_identity: str
    xplt_path: Path
    requested_fields: tuple[str, ...]
    available_fields: tuple[str, ...]
    values: Mapping[str, object]
    missing_fields: tuple[str, ...]
    non_finite_fields: tuple[str, ...]
    valid: bool
    official: bool
    provenance: str
    issues: tuple[str, ...]
    digest_before: str
    digest_after: str
    owner: object | None = None
    result: object | None = None
    revoked: bool = False


@dataclass(slots=True)
class _RootBinding:
    path: Path
    fd: int | None
    device: int
    inode: int


def _hold_root(root: Path) -> _RootBinding:
    """Hold the issued attempt root used for descriptor-relative reads."""

    path = _normalise_root(root)
    if os.path.normcase(os.path.realpath(os.fspath(path))) != os.path.normcase(os.fspath(path)):
        raise ValueError("attempt root must not be an alias")
    if os.name == "posix":
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(os.fspath(path), flags)
            metadata = os.fstat(fd)
        except OSError as error:
            if "fd" in locals():
                with contextlib.suppress(OSError):
                    os.close(fd)
            raise ValueError("attempt root is not a held directory") from error
        if not stat.S_ISDIR(metadata.st_mode):
            os.close(fd)
            raise ValueError("attempt root is not a directory")
        return _RootBinding(path, fd, int(metadata.st_dev), int(metadata.st_ino))
    try:
        metadata = os.lstat(os.fspath(path))
    except OSError as error:
        raise ValueError("attempt root is unavailable") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError("attempt root is not a directory")
    return _RootBinding(path, None, int(metadata.st_dev), int(metadata.st_ino))


def _verify_root(binding: _RootBinding | None) -> None:
    if binding is None:
        raise TypeError("issued attempt root authority is unavailable")
    try:
        metadata = os.fstat(binding.fd) if binding.fd is not None else os.lstat(binding.path)
    except OSError as error:
        raise TypeError("issued attempt root authority is unavailable") from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or int(metadata.st_dev) != binding.device
        or int(metadata.st_ino) != binding.inode
    ):
        raise TypeError("issued attempt root authority changed")


def _manager_record(value: object) -> _ManagerRecord:
    if type(value) is not FbsAdapterManager:
        raise TypeError("value is not an exact FbsAdapterManager instance")
    try:
        record = object.__getattribute__(value, "_record")
    except AttributeError as error:
        raise TypeError("FbsAdapterManager is not manager-issued") from error
    if not isinstance(record, _ManagerRecord) or record.manager is not value:
        raise TypeError("FbsAdapterManager binding is invalid")
    return record


def _authority_record(value: object) -> _AuthorityRecord:
    if type(value) is not FbsAdapterAuthority:
        raise TypeError("value is not an exact FbsAdapterAuthority instance")
    try:
        record = object.__getattribute__(value, "_record")
    except AttributeError as error:
        raise TypeError("FbsAdapterAuthority is not manager-issued") from error
    if not isinstance(record, _AuthorityRecord) or record.authority is not value:
        raise TypeError("FbsAdapterAuthority binding is invalid")
    manager_record = _manager_record(record.manager)
    if manager_record.authority is not value:
        raise TypeError("FbsAdapterAuthority manager binding is invalid")
    if manager_record.root_binding is not record.root_binding:
        raise TypeError("FbsAdapterAuthority root binding is invalid")
    if record.root_binding is not None:
        _verify_root(record.root_binding)
    return record


def _adapter_reader(adapter: object) -> _AdapterReader:
    candidate = getattr(adapter, "read_fields", None)
    if callable(candidate):
        return cast(_AdapterReader, candidate)
    if callable(adapter):
        return cast(_AdapterReader, adapter)
    raise TypeError("FBS adapter must provide read_fields(path, fields) or be callable")


def _normalise_root(root: str | Path) -> Path:
    return Path(os.path.abspath(os.fspath(root)))


class FbsAdapterManager:
    """Bind one adapter and issue one opaque validation authority."""

    __slots__ = ("_record", "__weakref__")

    def __init__(
        self,
        adapter: object,
        runtime_identity: str,
        attempt_root: str | Path | None = None,
    ) -> None:
        try:
            object.__getattribute__(self, "_record")
        except AttributeError:
            pass
        else:
            raise TypeError("FbsAdapterManager is already initialized")
        if not isinstance(runtime_identity, str) or not runtime_identity.strip():
            raise ValueError("runtime_identity must be a non-empty string")
        reader = _adapter_reader(adapter)
        root = _normalise_root(attempt_root) if attempt_root is not None else None
        root_binding = _hold_root(root) if root is not None else None
        object.__setattr__(
            self,
            "_record",
            _ManagerRecord(
                manager=self,
                adapter=adapter,
                reader=reader,
                runtime_identity=runtime_identity.strip(),
                attempt_root=root,
                root_binding=root_binding,
            ),
        )

    def __init_subclass__(cls, **kwargs: object) -> None:
        del kwargs
        raise TypeError("FbsAdapterManager cannot be subclassed")

    def __copy__(self) -> FbsAdapterManager:
        raise TypeError("FbsAdapterManager cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> FbsAdapterManager:
        del memo
        raise TypeError("FbsAdapterManager cannot be copied")

    def __reduce__(self) -> str | tuple[Any, ...]:
        raise TypeError("FbsAdapterManager cannot be serialized")

    def __reduce_ex__(self, protocol: SupportsIndex) -> str | tuple[Any, ...]:
        del protocol
        raise TypeError("FbsAdapterManager cannot be serialized")

    def issue_authority(self) -> FbsAdapterAuthority:
        """Issue the manager's exact-instance authority."""

        record = _manager_record(self)
        if record.authority is None:
            authority = FbsAdapterAuthority(_AUTHORITY_TOKEN)
            authority_record = _AuthorityRecord(
                authority=authority,
                manager=self,
                adapter=record.adapter,
                reader=record.reader,
                runtime_identity=record.runtime_identity,
                attempt_root=record.attempt_root,
                root_binding=record.root_binding,
            )
            object.__setattr__(authority, "_record", authority_record)
            record.authority = authority
        authority = record.authority
        if authority is None:  # pragma: no cover - defensive state guard
            raise TypeError("FbsAdapterManager authority is unavailable")
        _authority_record(authority)
        return authority


def _normalise_fields(fields: Iterable[str]) -> tuple[str, ...]:
    if isinstance(fields, (str, bytes, bytearray)):
        raise ValueError("requested fields must be an iterable of strings")
    try:
        normalised = tuple(fields)
    except TypeError as error:
        raise ValueError("requested fields must be an iterable of strings") from error
    if not normalised:
        raise ValueError("at least one requested field is required")
    if any(not isinstance(field, str) or not field.strip() for field in normalised):
        raise ValueError("requested fields must contain non-empty strings")
    if len(set(normalised)) != len(normalised):
        raise ValueError("requested fields must not contain duplicates")
    return normalised


def _path(path: str | Path) -> Path:
    try:
        return Path(os.path.abspath(os.fspath(path)))
    except TypeError as error:
        raise ValueError("XPLT path must be a filesystem path") from error


@dataclass(slots=True)
class _OpenedXplt:
    fd: int
    parent_fd: int
    name: str
    device: int
    inode: int


def _fd_alias_parts(path: Path) -> tuple[int, tuple[str, ...]] | None:
    parts = path.parts
    if len(parts) < 5 or parts[:4] != (os.sep, "proc", "self", "fd"):
        return None
    fd_text = parts[4]
    if not fd_text.isdigit():
        return None
    return int(fd_text), tuple(parts[5:])


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    try:
        Path(os.path.realpath(path)).relative_to(Path(os.path.realpath(root)))
    except ValueError:
        return False
    return True


def _require_xplt(
    path: Path,
    root: Path | None,
    *,
    require_exists: bool = True,
) -> None:
    if path.suffix.lower() != ".xplt":
        raise ValueError("validation requires an XPLT path")
    if _fd_alias_parts(path) is not None:
        raise ValueError("descriptor XPLT paths require a bound physical root")
    if root is not None and not _inside(path, root):
        raise ValueError("XPLT path is outside the solver attempt outputs")
    if path.is_symlink():
        raise ValueError("XPLT path must not be a symlink")
    if not require_exists:
        return
    try:
        if not stat.S_ISREG(path.stat(follow_symlinks=False).st_mode):
            raise ValueError("XPLT path must be a regular file")
    except FileNotFoundError as error:
        raise FileNotFoundError(f"XPLT file does not exist: {path}") from error


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _digest_fd(fd: int) -> str:
    digest = hashlib.sha256()
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        os.lseek(fd, 0, os.SEEK_SET)
    except OSError as error:
        raise ValueError("unable to hash the held XPLT descriptor") from error
    return digest.hexdigest()


def _same_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return int(left.st_dev) == int(right.st_dev) and int(left.st_ino) == int(right.st_ino)


def _relative_to_root(path: Path, root: Path) -> tuple[str, ...]:
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise ValueError("XPLT path is outside the issued attempt root") from error
    parts = relative.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("XPLT path is not a direct relative artifact")
    return parts


def _open_bound_xplt(record: _AuthorityRecord, reported_path: Path) -> _OpenedXplt:
    root = record.attempt_root
    binding = record.root_binding
    if root is None or binding is None or binding.fd is None:
        raise ValueError("FBS validation requires a held issued attempt root")
    _verify_root(binding)
    parts = _relative_to_root(reported_path, root)
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    file_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    current_fd = os.dup(binding.fd)
    try:
        for component in parts[:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        file_fd = os.open(parts[-1], file_flags, dir_fd=current_fd)
        try:
            metadata = os.fstat(file_fd)
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("XPLT path must be a regular file")
            if int(metadata.st_nlink) != 1:
                raise ValueError("XPLT path must not be a hard link")
            return _OpenedXplt(
                fd=file_fd,
                parent_fd=current_fd,
                name=parts[-1],
                device=int(metadata.st_dev),
                inode=int(metadata.st_ino),
            )
        except BaseException:
            os.close(file_fd)
            raise
    except BaseException:
        with contextlib.suppress(OSError):
            os.close(current_fd)
        raise


def _verify_opened_xplt(opened: _OpenedXplt) -> None:
    try:
        path_metadata = os.stat(opened.name, dir_fd=opened.parent_fd, follow_symlinks=False)
        descriptor_metadata = os.fstat(opened.fd)
    except OSError as error:
        raise ValueError("XPLT changed or became unavailable") from error
    if (
        not stat.S_ISREG(path_metadata.st_mode)
        or int(path_metadata.st_nlink) != 1
        or not _same_identity(path_metadata, descriptor_metadata)
        or int(descriptor_metadata.st_dev) != opened.device
        or int(descriptor_metadata.st_ino) != opened.inode
    ):
        raise ValueError("XPLT changed or became unavailable")


def _close_opened_xplt(opened: _OpenedXplt) -> None:
    failures: list[OSError] = []
    for fd in (opened.fd, opened.parent_fd):
        try:
            os.close(fd)
        except OSError as error:
            failures.append(error)
    if failures:
        raise ValueError("unable to close held XPLT descriptors") from failures[0]


def _finite_value(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        try:
            return math.isfinite(float(value))
        except (OverflowError, ValueError):
            return False
    if isinstance(value, Mapping):
        return bool(value) and all(_finite_value(item) for item in value.values())
    if isinstance(value, (str, bytes, bytearray)):
        return False
    if isinstance(value, Sequence):
        return bool(value) and all(_finite_value(item) for item in value)
    return False


def _freeze_value(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {_freeze_value(key): _freeze_value(item) for key, item in value.items()}
        )
    if isinstance(value, (str, bytes, int, float, bool, type(None))):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_value(item) for item in value)
    if isinstance(value, Sequence):
        return tuple(_freeze_value(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class FbsValidation:
    """Immutable, synthetic validation bound to one issued authority."""

    xplt_path: Path
    requested_fields: tuple[str, ...]
    available_fields: tuple[str, ...]
    values: Mapping[str, object]
    missing_fields: tuple[str, ...]
    non_finite_fields: tuple[str, ...]
    valid: bool
    official: bool = False
    provenance: str = _PROVENANCE
    issues: tuple[str, ...] = ()
    authority: FbsAdapterAuthority | None = None
    runtime_identity: str = ""
    digest_before: str = ""
    digest_after: str = ""
    _issuance: _ValidationRecord | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.authority is not None:
            record = _authority_record(self.authority)
            if self.runtime_identity != record.runtime_identity:
                raise ValueError("validation runtime identity does not match authority")
            if self.official or self.provenance != _PROVENANCE:
                raise ValueError("issued validation cannot claim unverified provenance")
        values = dict(self.values)
        object.__setattr__(
            self,
            "values",
            MappingProxyType({key: _freeze_value(value) for key, value in values.items()}),
        )

    @property
    def all_requested_fields_finite(self) -> bool:
        return not self.missing_fields and not self.non_finite_fields


def _field_snapshot(fields: object) -> tuple[str, ...]:
    if isinstance(fields, (str, bytes, bytearray)):
        return ()
    try:
        values: tuple[object, ...] = tuple(cast(Iterable[object], fields))
    except TypeError:
        return ()
    return tuple(field for field in values if isinstance(field, str))


def _register_validation(
    validation: FbsValidation, authority: FbsAdapterAuthority
) -> FbsValidation:
    if type(validation) is not FbsValidation:
        raise TypeError("validation is not an exact FbsValidation instance")
    authority_record = _authority_record(authority)
    if validation.authority is not authority:
        raise TypeError("validation authority binding is invalid")
    try:
        existing = object.__getattribute__(validation, "_issuance")
    except AttributeError as error:  # pragma: no cover - exact dataclass guard
        raise TypeError("validation issuance state is unavailable") from error
    if existing is not None:
        raise TypeError("validation is already issued")
    record = _ValidationRecord(
        validation=validation,
        authority=authority,
        runtime_identity=authority_record.runtime_identity,
        xplt_path=validation.xplt_path,
        requested_fields=validation.requested_fields,
        available_fields=validation.available_fields,
        values=validation.values,
        missing_fields=validation.missing_fields,
        non_finite_fields=validation.non_finite_fields,
        valid=validation.valid,
        official=validation.official,
        provenance=validation.provenance,
        issues=validation.issues,
        digest_before=validation.digest_before,
        digest_after=validation.digest_after,
    )
    object.__setattr__(validation, "_issuance", record)
    return validation


def _unregister_validation(value: object) -> bool:
    """Revoke exactly one validation issued by this module.

    Rollback is deliberately identity based.  A caller-provided object or a
    replacement occupying the same integer key must never remove an issued
    validation belonging to another operation.
    """

    if type(value) is not FbsValidation:
        return False
    try:
        record = object.__getattribute__(value, "_issuance")
    except AttributeError:
        return False
    if not isinstance(record, _ValidationRecord) or record.validation is not value:
        return False
    if record.revoked:
        return False
    record.revoked = True
    record.owner = None
    record.result = None
    return True


def _adopt_validation(value: object, owner: object, result: object) -> FbsValidation:
    record = _issued_validation_record(value)
    if record.owner is not None or record.result is not None:
        raise TypeError("FbsValidation is already adopted")
    record.owner = owner
    record.result = result
    return cast(FbsValidation, value)


def _issued_validation_record(value: object) -> _ValidationRecord:
    if type(value) is not FbsValidation:
        raise TypeError("value is not an exact FbsValidation instance")
    try:
        record = object.__getattribute__(value, "_issuance")
    except AttributeError as error:
        raise TypeError("FbsValidation was not issued by validate_requested_fields") from error
    if not isinstance(record, _ValidationRecord) or record.validation is not value:
        raise TypeError("FbsValidation was not issued by validate_requested_fields")
    if record.revoked:
        raise TypeError("FbsValidation issuance has been revoked")
    try:
        authority_record = _authority_record(record.authority)
        matches = (
            authority_record.runtime_identity == record.runtime_identity
            and record.xplt_path.is_absolute()
            and value.authority is record.authority
            and value.runtime_identity == record.runtime_identity
            and value.xplt_path == record.xplt_path
            and value.requested_fields == record.requested_fields
            and value.available_fields == record.available_fields
            and value.values is record.values
            and value.missing_fields == record.missing_fields
            and value.non_finite_fields == record.non_finite_fields
            and value.valid is record.valid
            and value.official is record.official
            and value.provenance == record.provenance
            and value.issues == record.issues
            and value.digest_before == record.digest_before
            and value.digest_after == record.digest_after
        )
    except (AttributeError, TypeError, ValueError):
        matches = False
    if not matches:
        raise TypeError("FbsValidation issuance binding is invalid")
    return record


def _require_issued_validation(
    value: object,
    authority: FbsAdapterAuthority,
    xplt_path: str | Path,
    requested_fields: Iterable[str],
) -> FbsValidation:
    record = _issued_validation_record(value)
    owner = record.owner
    result = record.result
    try:
        latched = object.__getattribute__(owner, "_result_latch") if owner is not None else None
        result_validation = getattr(result, "fbs_validation", None)
    except (AttributeError, TypeError):
        latched = None
        result_validation = None
    if owner is None or result is None or latched is not result or result_validation is not value:
        raise TypeError("FbsValidation is not adopted by a supervisor transaction")
    expected_authority = _authority_record(authority)
    expected_path = _path(xplt_path)
    try:
        expected_fields = _normalise_fields(requested_fields)
    except (TypeError, ValueError) as error:
        raise TypeError("validation context is invalid") from error
    if (
        record.authority is not authority
        or expected_authority.runtime_identity != record.runtime_identity
        or expected_path != record.xplt_path
        or expected_fields != record.requested_fields
    ):
        raise TypeError("validation does not match the requested authority context")
    return cast(FbsValidation, value)


def _invalid_validation(
    authority: FbsAdapterAuthority,
    xplt_path: str | Path,
    requested_fields: object,
    issue: str,
    *,
    digest_before: str = "",
    digest_after: str = "",
) -> FbsValidation:
    record = _authority_record(authority)
    fields = _field_snapshot(requested_fields)
    validation = FbsValidation(
        authority=authority,
        runtime_identity=record.runtime_identity,
        xplt_path=_path(xplt_path),
        requested_fields=fields,
        available_fields=(),
        values=MappingProxyType({}),
        missing_fields=fields,
        non_finite_fields=(),
        valid=False,
        provenance=_PROVENANCE,
        digest_before=digest_before,
        digest_after=digest_after,
        issues=(issue,),
    )
    return _register_validation(validation, authority)


def _build_validation(
    authority: FbsAdapterAuthority,
    record: _AuthorityRecord,
    reported_path: Path,
    fields: tuple[str, ...],
    values: Mapping[str, object],
    digest_before: str,
    digest_after: str,
) -> FbsValidation:
    available = tuple(values)
    missing = tuple(field for field in fields if field not in values)
    non_finite = tuple(
        field for field in fields if field in values and not _finite_value(values[field])
    )
    issues: list[str] = []
    if missing:
        issues.append("missing requested fields: " + ", ".join(missing))
    if non_finite:
        issues.append("non-finite requested fields: " + ", ".join(non_finite))
    validation = FbsValidation(
        authority=authority,
        runtime_identity=record.runtime_identity,
        xplt_path=reported_path,
        requested_fields=fields,
        available_fields=available,
        values=values,
        missing_fields=missing,
        non_finite_fields=non_finite,
        valid=not issues and digest_before == digest_after,
        provenance=_PROVENANCE,
        digest_before=digest_before,
        digest_after=digest_after,
        issues=tuple(issues),
    )
    return _register_validation(validation, authority)


def validate_requested_fields(
    authority: FbsAdapterAuthority,
    xplt_path: str | Path,
    requested_fields: Iterable[str],
    *,
    attempt_root: str | Path | None = None,
    physical_path: str | Path | None = None,
    physical_root: str | Path | None = None,
) -> FbsValidation:
    """Read and validate requested fields through an issued authority."""

    record = _authority_record(authority)
    fields = _normalise_fields(requested_fields)
    if physical_path is not None or physical_root is not None:
        raise ValueError("caller-supplied physical paths cannot establish authority")
    if attempt_root is not None:
        supplied_root = _normalise_root(attempt_root)
        if record.attempt_root is None or supplied_root != record.attempt_root:
            raise ValueError("caller-supplied attempt root does not match authority")
    if record.attempt_root is None or record.root_binding is None:
        raise ValueError("FBS validation requires an issued attempt-root authority")
    reported_path = _path(xplt_path)
    _require_xplt(reported_path, record.attempt_root, require_exists=False)

    opened: _OpenedXplt | None = None
    path = reported_path
    digest_before = ""
    raw_result: object = None
    try:
        if os.name == "posix":
            opened = _open_bound_xplt(record, reported_path)
            path = Path("/proc/self/fd") / str(opened.fd)
            digest_before = _digest_fd(opened.fd)
        else:
            _require_xplt(reported_path, record.attempt_root)
            digest_before = _digest(path)
        try:
            raw_result = record.reader(path, fields)
        except Exception as error:
            try:
                if opened is not None:
                    _verify_root(record.root_binding)
                    _verify_opened_xplt(opened)
                    digest_after = _digest_fd(opened.fd)
                else:
                    _require_xplt(reported_path, record.attempt_root)
                    digest_after = _digest(path)
            except Exception:
                digest_after = ""
            return _invalid_validation(
                authority,
                reported_path,
                fields,
                f"adapter execution failed: {error}",
                digest_before=digest_before,
                digest_after=digest_after,
            )

        try:
            if opened is not None:
                _verify_root(record.root_binding)
                _verify_opened_xplt(opened)
                digest_after = _digest_fd(opened.fd)
            else:
                _require_xplt(reported_path, record.attempt_root)
                digest_after = _digest(path)
        except Exception as error:
            return _invalid_validation(
                authority,
                reported_path,
                fields,
                f"XPLT changed or became unavailable during adapter execution: {error}",
                digest_before=digest_before,
            )
    finally:
        if opened is not None:
            _close_opened_xplt(opened)

    if digest_before != digest_after:
        return _invalid_validation(
            authority,
            reported_path,
            fields,
            "adapter mutated the XPLT artifact",
            digest_before=digest_before,
            digest_after=digest_after,
        )
    if not isinstance(raw_result, Mapping):
        return _invalid_validation(
            authority,
            reported_path,
            fields,
            "adapter must return a field mapping",
            digest_before=digest_before,
            digest_after=digest_after,
        )
    try:
        values: dict[str, object] = dict(raw_result)
    except Exception as error:
        return _invalid_validation(
            authority,
            reported_path,
            fields,
            f"adapter returned an unreadable field mapping: {error}",
            digest_before=digest_before,
            digest_after=digest_after,
        )
    if any(not isinstance(field, str) for field in values):
        return _invalid_validation(
            authority,
            reported_path,
            fields,
            "adapter field mapping keys must be strings",
            digest_before=digest_before,
            digest_after=digest_after,
        )
    return _build_validation(
        authority,
        record,
        reported_path,
        fields,
        values,
        digest_before,
        digest_after,
    )
