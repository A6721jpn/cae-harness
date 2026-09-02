"""Integrity authority for one solver attempt and its live FBS provenance."""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any, NoReturn, cast

from febio_cae_harness.solver import FbsValidation, SolverRunResult, SolverSupervisor, validate_log
from febio_cae_harness.solver.fbs import _require_issued_validation
from febio_cae_harness.solver.official_fbs import (
    OfficialFbsResultReceipt,
    require_official_fbs_result,
)
from febio_cae_harness.solver.types import _reject_alias as _solver_reject_alias

from .physical import PhysicalEvidenceAuthority, _validated_physical_evidence
from .types import AttemptIdentity, EvidenceKind

__all__ = ["ReportAuthorityManager", "ReportAuthority"]
_TOKEN = object()
_SYNTHETIC_PROVENANCE = "synthetic-unverified"
_OFFICIAL_PROVENANCE = "official"
_Identity = tuple[int, int]
_FileRecord = tuple[_Identity, str]
_ManagerRecord = tuple[Any, ...]
_AuthorityRecord = tuple[Any, ...]
_MANAGERS: dict[int, _ManagerRecord] = {}
_AUTHORITIES: dict[int, _AuthorityRecord] = {}


def _absolute(value: object, label: str) -> Path:
    try:
        raw = os.fspath(cast(str | os.PathLike[str], value))
    except TypeError as error:
        raise TypeError(f"{label} must be a filesystem path") from error
    if isinstance(raw, bytes) or not raw.strip():
        raise ValueError(f"{label} must be a non-empty path")
    return Path(os.path.abspath(raw))


def _snapshot(path: Path, root: Path, label: str) -> _FileRecord:
    _solver_reject_alias(root, "attempt root")
    _solver_reject_alias(path, label)
    if path == root or not path.is_relative_to(root):
        raise ValueError(f"{label} must be physically inside the attempt root")
    try:
        before = path.stat()
    except FileNotFoundError as error:
        raise FileNotFoundError(f"{label} does not exist") from error
    except OSError as error:
        raise ValueError(f"cannot inspect {label}") from error
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f"{label} must be a regular file")
    if before.st_nlink != 1:
        raise ValueError(f"{label} must not be a hard link")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        after = path.stat()
    except OSError as error:
        raise ValueError(f"{label} changed or became unavailable") from error
    if before[1:4] + (before[6], before[8], before[9]) != after[1:4] + (
        after[6],
        after[8],
        after[9],
    ):
        raise ValueError(f"{label} changed during hashing")
    return (before.st_dev, before.st_ino), digest.hexdigest()


def _manager(value: object) -> _ManagerRecord:
    if type(value) is not ReportAuthorityManager:
        raise TypeError("value is not an exact ReportAuthorityManager instance")
    record = _MANAGERS.get(id(value))
    if record is None or record[0] is not value:
        raise TypeError("ReportAuthorityManager is not registry-issued")
    return record


def _validate_manager(record: _ManagerRecord) -> None:
    _, supervisor, result, identity, root, _, official_result, physical_evidence = record
    if type(supervisor) is not SolverSupervisor or type(result) is not SolverRunResult:
        raise TypeError("report authority requires exact solver instances")
    try:
        if supervisor._validate_result(result) is not result:
            raise TypeError("solver result binding is invalid")
        spec = supervisor.spec
    except (AttributeError, TypeError, ValueError, RuntimeError) as error:
        raise TypeError("solver supervisor/result binding is invalid") from error
    if (supervisor._case_id, supervisor._intent_id, supervisor._attempt_id) != (
        identity.case_id,
        identity.intent_id,
        identity.attempt_id,
    ):
        raise TypeError("attempt identity does not match the solver supervisor")
    log, xplt = spec.expected_outputs.log_path, spec.expected_outputs.xplt_path
    if spec.attempt_root != root or not all(
        isinstance(path, Path) and path.is_absolute() for path in (log, xplt)
    ):
        raise TypeError("solver attempt root/output binding is invalid")
    if any(path == root or not path.is_relative_to(root) for path in (log, xplt)):
        raise TypeError("solver output paths are outside the attempt root")
    if physical_evidence is not None:
        if type(official_result) is not OfficialFbsResultReceipt:
            raise TypeError("physical evidence requires an official FBS result")
        _validated_physical_evidence(
            physical_evidence,
            supervisor=supervisor,
            result=result,
            receipt=official_result,
        )


def _issued_fbs(
    result: SolverRunResult,
    official_result: OfficialFbsResultReceipt | None,
) -> tuple[FbsValidation, str]:
    validation = result.fbs_validation
    if type(validation) is not FbsValidation or validation.authority is None:
        raise TypeError("solver result must contain an issued FbsValidation")
    try:
        checked = _require_issued_validation(
            validation, validation.authority, result.xplt_path, validation.requested_fields
        )
    except (AttributeError, TypeError, ValueError, RuntimeError) as error:
        raise TypeError("solver result FbsValidation issuance is invalid") from error
    if checked is not validation:
        raise TypeError("solver result FbsValidation binding is invalid")
    if validation.official is True:
        if type(official_result) is not OfficialFbsResultReceipt:
            raise TypeError("official FBS validation requires its exact live result receipt")
        try:
            current = require_official_fbs_result(validation)
        except (AttributeError, TypeError, ValueError, RuntimeError) as error:
            raise TypeError("official FBS result receipt is unavailable") from error
        if current is not official_result or official_result.validation is not validation:
            raise TypeError("official FBS result receipt binding is invalid")
        if validation.provenance != _OFFICIAL_PROVENANCE:
            raise TypeError("official FbsValidation provenance is invalid")
        return validation, _OFFICIAL_PROVENANCE
    if official_result is not None:
        raise TypeError("non-official FBS validation cannot use an official result receipt")
    if validation.official is not False or validation.provenance != _SYNTHETIC_PROVENANCE:
        raise TypeError("FbsValidation provenance is invalid")
    return validation, _SYNTHETIC_PROVENANCE


def _evidence(value: object) -> dict[EvidenceKind, object]:
    if not isinstance(value, Mapping):
        raise TypeError("evidence must map EvidenceKind values to paths")
    try:
        result = {
            EvidenceKind(key.strip().casefold() if isinstance(key, str) else key): path
            for key, path in value.items()
        }
    except (TypeError, ValueError) as error:
        raise ValueError("evidence keys must be EvidenceKind values") from error
    if len(result) != len(value) or set(result) != set(EvidenceKind):
        raise ValueError("evidence must contain exactly mesh, jacobian, roi, and evaluation")
    return result


def _require_issued_authority(
    authority: object,
    manager: object | None = None,
    supervisor: object | None = None,
    result: object | None = None,
) -> _AuthorityRecord:
    if type(authority) is not ReportAuthority:
        raise TypeError("value is not an exact ReportAuthority instance")
    record = _AUTHORITIES.get(id(authority))
    if record is None or record[0] is not authority:
        raise TypeError("ReportAuthority is not manager-issued")
    if _manager(record[1])[5] is not authority:
        raise TypeError("ReportAuthority manager binding is invalid")
    if manager is not None and manager is not record[1]:
        raise TypeError("report authority belongs to another manager")
    if supervisor is not None and supervisor is not record[2]:
        raise TypeError("report authority belongs to another supervisor")
    if result is not None and result is not record[3]:
        raise TypeError("report authority belongs to another solver result")
    _validate_manager(_manager(record[1]))
    _, provenance = _issued_fbs(record[3], record[11])
    if provenance != record[10]:
        raise TypeError("report authority FBS provenance changed")
    physical_evidence = record[12]
    if physical_evidence is not None:
        _validated_physical_evidence(
            physical_evidence,
            supervisor=record[2],
            result=record[3],
            receipt=record[11],
        )
    for path, expected in record[7].items():
        current = _snapshot(path, record[5], "report artifact")
        if current != expected:
            raise TypeError("report artifact identity or digest changed")
    return record


class _Opaque:
    __slots__ = ()

    def _forbidden(self, *args: object) -> NoReturn:
        del args
        raise TypeError("opaque authority cannot be copied or pickled")

    __copy__ = __deepcopy__ = __reduce__ = __reduce_ex__ = _forbidden


class ReportAuthority(_Opaque):
    __slots__ = ("__weakref__",)

    def __new__(cls, token: object | None = None) -> ReportAuthority:
        if cls is not ReportAuthority or token is not _TOKEN:
            raise TypeError("ReportAuthority instances are manager-issued")
        return super().__new__(cls)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("ReportAuthority cannot be subclassed")

    def __getattr__(self, name: str) -> object:
        record = _require_issued_authority(self)
        values: dict[str, object] = {
            "manager": record[1],
            "supervisor": record[2],
            "result": record[3],
            "identity": record[4],
            "attempt_root": record[5],
            "evidence": record[6],
            "log_path": record[3].log_path,
            "xplt_path": record[3].xplt_path,
            "file_identities": MappingProxyType(
                {path: item[0] for path, item in record[7].items()}
            ),
            "digests": MappingProxyType({path: item[1] for path, item in record[7].items()}),
            "requested_fields": record[8],
            "runtime_identity": record[9],
            "provenance": record[10],
            "official_fbs_result": record[11],
            "physical_evidence": record[12],
        }
        try:
            return values[name]
        except KeyError as error:
            raise AttributeError(name) from error


class ReportAuthorityManager(_Opaque):
    __slots__ = ("__weakref__",)

    def __init__(
        self,
        supervisor: SolverSupervisor,
        result: SolverRunResult,
        identity: AttemptIdentity,
        attempt_root: str | os.PathLike[str] | None = None,
        *,
        official_fbs_result: OfficialFbsResultReceipt | None = None,
        physical_evidence: PhysicalEvidenceAuthority | None = None,
    ) -> None:
        if type(supervisor) is not SolverSupervisor or type(result) is not SolverRunResult:
            raise TypeError("manager requires exact solver instances")
        if type(identity) is not AttemptIdentity:
            raise TypeError("manager requires an exact AttemptIdentity instance")
        root = _absolute(
            supervisor.spec.attempt_root if attempt_root is None else attempt_root,
            "attempt root",
        )
        if root != _absolute(supervisor.spec.attempt_root, "attempt root"):
            raise ValueError("attempt root does not match the solver supervisor")
        if (
            official_fbs_result is not None
            and type(official_fbs_result) is not OfficialFbsResultReceipt
        ):
            raise TypeError("official_fbs_result must be an exact OfficialFbsResultReceipt")
        if (
            physical_evidence is not None
            and type(physical_evidence) is not PhysicalEvidenceAuthority
        ):
            raise TypeError("physical_evidence must be an exact PhysicalEvidenceAuthority")
        record: _ManagerRecord = (
            self,
            supervisor,
            result,
            identity,
            root,
            None,
            official_fbs_result,
            physical_evidence,
        )
        _validate_manager(record)
        _MANAGERS[id(self)] = record

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("ReportAuthorityManager cannot be subclassed")

    def issue(self, evidence: Mapping[EvidenceKind, str | os.PathLike[str]]) -> ReportAuthority:
        """Issue one authority carrying the exact live FBS provenance."""
        record = _manager(self)
        _validate_manager(record)
        paths = {
            kind: _absolute(path, f"{kind.value} evidence")
            for kind, path in _evidence(evidence).items()
        }
        fbs, provenance = _issued_fbs(record[2], record[6])
        if record[7] is not None:
            _validated_physical_evidence(
                record[7],
                supervisor=record[1],
                result=record[2],
                receipt=record[6],
            )
        outputs = (record[2].log_path, record[2].xplt_path)
        all_paths = tuple(paths.values()) + outputs
        if len({os.path.normcase(os.fspath(path)) for path in all_paths}) != len(all_paths):
            raise ValueError("evidence and solver output paths must be distinct")
        files: dict[Path, _FileRecord] = {
            path: _snapshot(path, record[4], f"{kind.value} evidence")
            for kind, path in paths.items()
        }
        files.update(
            {
                path: _snapshot(path, record[4], label)
                for path, label in zip(outputs, ("LOG", "XPLT"), strict=True)
            }
        )
        live_log = validate_log(
            record[2].log_path,
            expected_steps=record[1].spec.expected_steps,
            expected_final_time=record[1].spec.expected_final_time,
        )
        if not live_log.valid:
            raise ValueError("live LOG validation failed")
        for path in outputs:
            if _snapshot(path, record[4], "solver output") != files[path]:
                raise ValueError("solver output changed during LOG validation")
        if record[5] is not None:
            _require_issued_authority(record[5], self, record[1], record[2])
            return cast(ReportAuthority, record[5])
        authority = ReportAuthority(_TOKEN)
        _AUTHORITIES[id(authority)] = (
            authority,
            self,
            record[1],
            record[2],
            record[3],
            record[4],
            MappingProxyType(paths),
            MappingProxyType(files),
            fbs.requested_fields,
            fbs.runtime_identity,
            provenance,
            record[6],
            record[7],
        )
        _MANAGERS[id(self)] = (
            self,
            record[1],
            record[2],
            record[3],
            record[4],
            authority,
            record[6],
            record[7],
        )
        return authority
