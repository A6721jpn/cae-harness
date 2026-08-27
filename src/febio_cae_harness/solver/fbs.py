"""Injected boundary for official FEBio Studio (FBS) result reads.

The package deliberately has no FBS or UI dependency.  A caller injects an
adapter that knows how to read an XPLT file; this boundary checks requested
field presence and finite values and records whether the adapter explicitly
identified itself as official.  A plain mapping returned by a fixture is
therefore clearly marked as synthetic/unverified evidence.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Protocol, cast

__all__ = [
    "FbsAdapterBoundary",
    "FbsAdapterError",
    "FbsAdapterProtocol",
    "FbsResultValidation",
    "FbsValidation",
    "FBSAdapter",
    "FBSValidation",
    "OfficialFbsAdapter",
    "OfficialFBSAdapter",
    "OfficialFbsAdapterBoundary",
    "OfficialFBSAdapterBoundary",
    "validate_fbs_fields",
    "validate_requested_fields",
]


class FbsAdapterError(RuntimeError):
    """Raised when an injected FBS adapter cannot provide a field read."""


class FbsAdapterProtocol(Protocol):
    """Minimal adapter contract; no FBS implementation is bundled."""

    def read_fields(self, xplt_path: Path, fields: Sequence[str]) -> Mapping[str, object]:
        """Read requested result fields from ``xplt_path``."""


OfficialFbsAdapter = FbsAdapterProtocol
FBSAdapter = FbsAdapterProtocol


def _normalise_fields(fields: Iterable[str]) -> tuple[str, ...]:
    normalised = tuple(fields)
    if any(not isinstance(field, str) or not field.strip() for field in normalised):
        raise ValueError("requested FBS fields must contain non-empty strings")
    if len(set(normalised)) != len(normalised):
        raise ValueError("requested FBS fields must not contain duplicates")
    return normalised


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
    """Validation result for requested XPLT fields."""

    xplt_path: Path
    requested_fields: tuple[str, ...]
    available_fields: tuple[str, ...]
    values: Mapping[str, object]
    missing_fields: tuple[str, ...]
    non_finite_fields: tuple[str, ...]
    valid: bool
    official: bool
    provenance: str
    issues: tuple[str, ...] = ()

    @property
    def is_official(self) -> bool:
        return self.official

    @property
    def all_requested_fields_finite(self) -> bool:
        return not self.missing_fields and not self.non_finite_fields

    @property
    def finite_requested_fields(self) -> bool:
        return self.all_requested_fields_finite

    @property
    def missing_requested_fields(self) -> tuple[str, ...]:
        return self.missing_fields

    @property
    def nonfinite_fields(self) -> tuple[str, ...]:
        return self.non_finite_fields

    @property
    def evidence_kind(self) -> str:
        return self.provenance

    @classmethod
    def invalid(
        cls,
        xplt_path: str | Path,
        requested_fields: Iterable[str],
        issue: str,
        *,
        official: bool = False,
    ) -> FbsValidation:
        fields = _normalise_fields(requested_fields)
        return cls(
            xplt_path=Path(xplt_path),
            requested_fields=fields,
            available_fields=(),
            values=MappingProxyType({}),
            missing_fields=fields,
            non_finite_fields=(),
            valid=False,
            official=official,
            provenance="official-fbs" if official else "synthetic-adapter",
            issues=(issue,),
        )


FbsResultValidation = FbsValidation
FBSValidation = FbsValidation


def _mapping_from_adapter_result(result: object) -> tuple[Mapping[str, object], bool | None]:
    if isinstance(result, FbsValidation):
        return result.values, result.official
    if not isinstance(result, Mapping):
        raise FbsAdapterError("FBS adapter must return a field mapping or FbsValidation")

    # Permit an explicit result envelope while keeping the simple direct
    # mapping form convenient for fixtures and adapters.
    nested = result.get("values")
    if isinstance(nested, Mapping):
        marker = result.get("official")
        return cast(Mapping[str, object], nested), marker if isinstance(marker, bool) else None
    return cast(Mapping[str, object], result), None


def _validate_values(
    xplt_path: Path,
    requested_fields: tuple[str, ...],
    values: Mapping[str, object],
    *,
    official: bool,
    extra_issues: Iterable[str] = (),
) -> FbsValidation:
    available_fields = tuple(str(field) for field in values)
    missing_fields = tuple(field for field in requested_fields if field not in values)
    non_finite_fields = tuple(
        field for field in requested_fields if field in values and not _finite_value(values[field])
    )
    issues = list(extra_issues)
    if missing_fields:
        issues.append("missing requested FBS fields: " + ", ".join(missing_fields))
    if non_finite_fields:
        issues.append("non-finite requested FBS fields: " + ", ".join(non_finite_fields))
    return FbsValidation(
        xplt_path=xplt_path,
        requested_fields=requested_fields,
        available_fields=available_fields,
        values=MappingProxyType(dict(values)),
        missing_fields=missing_fields,
        non_finite_fields=non_finite_fields,
        valid=not missing_fields and not non_finite_fields and not issues,
        official=official,
        provenance="official-fbs" if official else "synthetic-adapter",
        issues=tuple(issues),
    )


class OfficialFbsAdapterBoundary:
    """Call an injected FBS reader and validate requested finite fields."""

    def __init__(self, adapter: object) -> None:
        self._adapter = adapter

    @property
    def adapter(self) -> object:
        return self._adapter

    def _reader(self) -> Callable[[Path, Sequence[str]], object]:
        for name in ("read_fields", "validate_fields", "read"):
            candidate = getattr(self._adapter, name, None)
            if callable(candidate):
                return cast(Callable[[Path, Sequence[str]], object], candidate)
        raise FbsAdapterError(
            "injected FBS adapter must define read_fields(path, fields), "
            "validate_fields(path, fields), or read(path, fields)"
        )

    def validate(
        self,
        xplt_path: str | Path,
        requested_fields: Iterable[str] = (),
    ) -> FbsValidation:
        path = Path(xplt_path)
        fields = _normalise_fields(requested_fields)
        if not path.is_file():
            raise FileNotFoundError(f"XPLT file does not exist: {path}")
        reader = self._reader()
        try:
            raw_result = reader(path, fields)
        except Exception as error:
            raise FbsAdapterError(f"injected FBS adapter failed for {path}") from error
        values, result_marker = _mapping_from_adapter_result(raw_result)
        adapter_marker = getattr(self._adapter, "official", False)
        official = bool(adapter_marker) or bool(result_marker)
        return _validate_values(path, fields, values, official=official)

    def validate_fields(
        self,
        xplt_path: str | Path,
        requested_fields: Iterable[str] = (),
    ) -> FbsValidation:
        return self.validate(xplt_path, requested_fields)

    __call__ = validate


FbsAdapterBoundary = OfficialFbsAdapterBoundary
OfficialFBSAdapterBoundary = OfficialFbsAdapterBoundary
OfficialFBSAdapter = OfficialFbsAdapter


def validate_requested_fields(
    adapter: object,
    xplt_path: str | Path,
    requested_fields: Iterable[str] = (),
) -> FbsValidation:
    """Validate finite requested fields through an injected adapter."""

    boundary = (
        adapter
        if isinstance(adapter, OfficialFbsAdapterBoundary)
        else OfficialFbsAdapterBoundary(adapter)
    )
    return boundary.validate(xplt_path, requested_fields)


validate_fbs_fields = validate_requested_fields
