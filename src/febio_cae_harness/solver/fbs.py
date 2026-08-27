"""Synthetic, authority-bound FBS result validation.

This module deliberately contains no FEBio Studio runtime.  A manager binds a
single adapter to a non-empty runtime identity and issues an opaque authority.
Only that authority can be used to validate an attempt XPLT artifact; all
issued validations remain synthetic and unverified provenance.
"""

from __future__ import annotations

import hashlib
import math
import os
import stat
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
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

    __slots__ = ("__weakref__",)

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
    authority: FbsAdapterAuthority | None = None


@dataclass(slots=True)
class _AuthorityRecord:
    authority: FbsAdapterAuthority
    manager: FbsAdapterManager
    adapter: object
    reader: _AdapterReader
    runtime_identity: str
    attempt_root: Path | None


_MANAGER_REGISTRY: dict[int, _ManagerRecord] = {}
_AUTHORITY_REGISTRY: dict[int, _AuthorityRecord] = {}


def _manager_record(value: object) -> _ManagerRecord:
    if type(value) is not FbsAdapterManager:
        raise TypeError("value is not an exact FbsAdapterManager instance")
    record = _MANAGER_REGISTRY.get(id(value))
    if record is None or record.manager is not value:
        raise TypeError("FbsAdapterManager is not registry-issued")
    return record


def _authority_record(value: object) -> _AuthorityRecord:
    if type(value) is not FbsAdapterAuthority:
        raise TypeError("value is not an exact FbsAdapterAuthority instance")
    record = _AUTHORITY_REGISTRY.get(id(value))
    if record is None or record.authority is not value:
        raise TypeError("FbsAdapterAuthority is not manager-issued")
    manager_record = _manager_record(record.manager)
    if manager_record.authority is not value:
        raise TypeError("FbsAdapterAuthority manager binding is invalid")
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

    __slots__ = ("__weakref__",)

    def __init__(
        self,
        adapter: object,
        runtime_identity: str,
        attempt_root: str | Path | None = None,
    ) -> None:
        if id(self) in _MANAGER_REGISTRY:
            raise TypeError("FbsAdapterManager is already initialized")
        if not isinstance(runtime_identity, str) or not runtime_identity.strip():
            raise ValueError("runtime_identity must be a non-empty string")
        reader = _adapter_reader(adapter)
        root = _normalise_root(attempt_root) if attempt_root is not None else None
        _MANAGER_REGISTRY[id(self)] = _ManagerRecord(
            manager=self,
            adapter=adapter,
            reader=reader,
            runtime_identity=runtime_identity.strip(),
            attempt_root=root,
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
            record.authority = authority
            _AUTHORITY_REGISTRY[id(authority)] = _AuthorityRecord(
                authority=authority,
                manager=self,
                adapter=record.adapter,
                reader=record.reader,
                runtime_identity=record.runtime_identity,
                attempt_root=record.attempt_root,
            )
        authority = record.authority
        authority_record = _AUTHORITY_REGISTRY.get(id(authority))
        if authority_record is None or authority_record.authority is not authority:
            raise TypeError("FbsAdapterManager authority registry is invalid")
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


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        Path(os.path.realpath(path)).relative_to(Path(os.path.realpath(root)))
    except ValueError:
        return False
    return True


def _require_xplt(path: Path, root: Path | None) -> None:
    if path.suffix.lower() != ".xplt":
        raise ValueError("validation requires an XPLT path")
    if root is not None and not _inside(path, root):
        raise ValueError("XPLT path is outside the solver attempt outputs")
    if path.is_symlink():
        raise ValueError("XPLT path must not be a symlink")
    try:
        if not stat.S_ISREG(path.stat().st_mode):
            raise ValueError("XPLT path must be a regular file")
    except FileNotFoundError as error:
        raise FileNotFoundError(f"XPLT file does not exist: {path}") from error


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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

    def __post_init__(self) -> None:
        if self.authority is not None:
            record = _authority_record(self.authority)
            if self.runtime_identity != record.runtime_identity:
                raise ValueError("validation runtime identity does not match authority")
            if self.official or self.provenance != _PROVENANCE:
                raise ValueError("issued validation cannot claim unverified provenance")
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))

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
    return FbsValidation(
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


def _build_validation(
    authority: FbsAdapterAuthority,
    record: _AuthorityRecord,
    path: Path,
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
    return FbsValidation(
        authority=authority,
        runtime_identity=record.runtime_identity,
        xplt_path=path,
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


def validate_requested_fields(
    authority: FbsAdapterAuthority,
    xplt_path: str | Path,
    requested_fields: Iterable[str],
    *,
    attempt_root: str | Path | None = None,
) -> FbsValidation:
    """Read and validate requested fields through an issued authority."""

    record = _authority_record(authority)
    fields = _normalise_fields(requested_fields)
    path = _path(xplt_path)
    if record.attempt_root is not None:
        _require_xplt(path, record.attempt_root)
    if attempt_root is not None:
        _require_xplt(path, _normalise_root(attempt_root))
    if record.attempt_root is None and attempt_root is None:
        _require_xplt(path, None)

    digest_before = _digest(path)
    try:
        raw_result = record.reader(path, fields)
    except Exception as error:
        try:
            _require_xplt(path, record.attempt_root)
            digest_after = _digest(path)
        except Exception:
            digest_after = ""
        return _invalid_validation(
            authority,
            path,
            fields,
            f"adapter execution failed: {error}",
            digest_before=digest_before,
            digest_after=digest_after,
        )

    try:
        _require_xplt(path, record.attempt_root)
        if attempt_root is not None:
            _require_xplt(path, _normalise_root(attempt_root))
        digest_after = _digest(path)
    except Exception as error:
        return _invalid_validation(
            authority,
            path,
            fields,
            f"XPLT changed or became unavailable during adapter execution: {error}",
            digest_before=digest_before,
        )
    if digest_before != digest_after:
        return _invalid_validation(
            authority,
            path,
            fields,
            "adapter mutated the XPLT artifact",
            digest_before=digest_before,
            digest_after=digest_after,
        )
    if not isinstance(raw_result, Mapping):
        return _invalid_validation(
            authority,
            path,
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
            path,
            fields,
            f"adapter returned an unreadable field mapping: {error}",
            digest_before=digest_before,
            digest_after=digest_after,
        )
    if any(not isinstance(field, str) for field in values):
        return _invalid_validation(
            authority,
            path,
            fields,
            "adapter field mapping keys must be strings",
            digest_before=digest_before,
            digest_after=digest_after,
        )
    return _build_validation(
        authority,
        record,
        path,
        fields,
        values,
        digest_before,
        digest_after,
    )
