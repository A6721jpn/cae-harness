"""Read-only structural and unit-fact inspection of STEP text."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from .types import EvidenceProvenance


class STEPInspectionError(ValueError):
    """Raised when a STEP source cannot be decoded or structurally inspected."""


@dataclass(frozen=True, slots=True)
class StepEntity:
    """One explicit STEP entity assignment."""

    entity_id: int
    entity_type: str
    arguments: str
    line: int

    @property
    def id(self) -> int:
        return self.entity_id

    @property
    def type(self) -> str:
        return self.entity_type

    def to_dict(self) -> dict[str, object]:
        return {
            "entity_id": self.entity_id,
            "entity_type": self.entity_type,
            "arguments": self.arguments,
            "line": self.line,
        }


@dataclass(frozen=True, slots=True)
class StepUnitFact:
    """An explicit unit declaration with source provenance."""

    category: str
    name: str
    prefix: str | None
    symbol: str
    entity_id: int | None
    provenance: EvidenceProvenance
    line: int = 0

    @property
    def unit_name(self) -> str:
        return self.name

    def to_dict(self) -> dict[str, object]:
        return {
            "category": self.category,
            "name": self.name,
            "prefix": self.prefix,
            "symbol": self.symbol,
            "entity_id": self.entity_id,
            "provenance": self.provenance.to_dict(),
            "line": self.line,
        }


@dataclass(frozen=True, slots=True)
class STEPInspection:
    """Immutable STEP inventory, preserving explicit unit provenance."""

    source_name: str | None
    source_path: Path | None
    sha256: str
    size_bytes: int
    entities: tuple[StepEntity, ...]
    entity_counts: Mapping[str, int]
    schema_identifiers: tuple[str, ...]
    units: tuple[StepUnitFact, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "entities", tuple(self.entities))
        object.__setattr__(self, "entity_counts", MappingProxyType(dict(self.entity_counts)))
        object.__setattr__(self, "schema_identifiers", tuple(self.schema_identifiers))
        object.__setattr__(self, "units", tuple(self.units))

    @classmethod
    def from_source(
        cls, source: str | bytes | Path, source_name: str | None = None
    ) -> STEPInspection:
        return inspect_step(source, source_name=source_name)

    @property
    def entity_count(self) -> int:
        return len(self.entities)

    @property
    def units_explicit(self) -> bool:
        return bool(self.units)

    @property
    def unit_provenance(self) -> tuple[EvidenceProvenance, ...]:
        return tuple(item.provenance for item in self.units)

    @property
    def explicit_units(self) -> tuple[StepUnitFact, ...]:
        return self.units

    def to_dict(self) -> dict[str, object]:
        return {
            "source_name": self.source_name,
            "source_path": None if self.source_path is None else str(self.source_path),
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "entity_count": self.entity_count,
            "entities": [item.to_dict() for item in self.entities],
            "entity_counts": dict(self.entity_counts),
            "schema_identifiers": list(self.schema_identifiers),
            "units": [item.to_dict() for item in self.units],
            "units_explicit": self.units_explicit,
        }


StepInspection = STEPInspection
StepInspectionError = STEPInspectionError

_ENTITY_RE = re.compile(r"#(?P<id>[0-9]+)\s*=\s*(?P<body>.*?);", re.DOTALL)
_ENTITY_TYPE_RE = re.compile(r"(?:\(\s*)?(?P<type>[A-Z][A-Z0-9_]*)\s*\(", re.IGNORECASE)
_SCHEMA_RE = re.compile(r"FILE_SCHEMA\s*\(\s*\((?P<body>.*?)\)\s*\)", re.IGNORECASE | re.DOTALL)
_STRING_RE = re.compile(r"'([^']*)'")
_SI_UNIT_RE = re.compile(r"\bSI_UNIT\s*\((?P<body>.*?)\)", re.IGNORECASE | re.DOTALL)
_CONVERSION_UNIT_RE = re.compile(
    r"\bCONVERSION_BASED_UNIT\s*\((?P<body>.*?)\)", re.IGNORECASE | re.DOTALL
)
_CATEGORY_RE = re.compile(
    r"\b(?P<category>LENGTH_UNIT|AREA_UNIT|VOLUME_UNIT|PLANE_ANGLE_UNIT|SOLID_ANGLE_UNIT)\s*\(\s*\)",
    re.IGNORECASE,
)
_ENUM_RE = re.compile(r"\.([A-Z][A-Z0-9_]*)\.", re.IGNORECASE)


def _read_source(
    source: str | bytes | Path,
    source_name: str | None,
) -> tuple[bytes, str, Path | None, str | None]:
    if isinstance(source, bytes):
        payload = source
        return payload, _decode(payload), None, source_name
    if isinstance(source, Path):
        payload = source.read_bytes()
        return payload, _decode(payload), source, source_name or source.name
    if not isinstance(source, str):
        raise TypeError("STEP source must be bytes, string STEP text, or pathlib.Path")
    if source.lstrip("\ufeff \t\r\n").startswith(("ISO-10303-21", "HEADER", "DATA")):
        payload = source.encode("utf-8")
        return payload, source, None, source_name
    path = Path(source)
    payload = path.read_bytes()
    return payload, _decode(payload), path, source_name or path.name


def _decode(payload: bytes) -> str:
    try:
        return payload.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise STEPInspectionError(f"STEP source is not UTF-8/ASCII text: {error}") from error


def _unit_category(body: str) -> str:
    match = _CATEGORY_RE.search(body)
    return "unit" if match is None else match.group("category").lower()


def _unit_facts(
    entity: StepEntity,
) -> tuple[StepUnitFact, ...]:
    facts: list[StepUnitFact] = []
    body = entity.arguments
    category = _unit_category(body)
    for match in _SI_UNIT_RE.finditer(body):
        tokens = [token.lower() for token in _ENUM_RE.findall(match.group("body"))]
        if not tokens:
            continue
        prefix: str | None
        symbol: str
        if len(tokens) >= 2:
            prefix, symbol = tokens[-2], tokens[-1]
        else:
            prefix, symbol = None, tokens[-1]
        name = f"{prefix} {symbol}" if prefix is not None else symbol
        provenance = EvidenceProvenance(
            source="STEP",
            location=f"#{entity.entity_id}:SI_UNIT",
            excerpt=match.group(0).strip(),
            authoritative=True,
        )
        facts.append(
            StepUnitFact(
                category=category,
                name=name,
                prefix=prefix,
                symbol=symbol,
                entity_id=entity.entity_id,
                provenance=provenance,
                line=entity.line,
            )
        )
    for match in _CONVERSION_UNIT_RE.finditer(body):
        labels = _STRING_RE.findall(match.group("body"))
        if not labels:
            continue
        name = labels[0]
        provenance = EvidenceProvenance(
            source="STEP",
            location=f"#{entity.entity_id}:CONVERSION_BASED_UNIT",
            excerpt=match.group(0).strip(),
            authoritative=True,
        )
        facts.append(
            StepUnitFact(
                category=category,
                name=name,
                prefix=None,
                symbol=name,
                entity_id=entity.entity_id,
                provenance=provenance,
                line=entity.line,
            )
        )
    return tuple(facts)


def inspect_step(source: str | bytes | Path, source_name: str | None = None) -> STEPInspection:
    """Inspect STEP structure and explicit unit declarations without CAD inference."""

    payload, text, source_path, resolved_name = _read_source(source, source_name)
    entities: list[StepEntity] = []
    for match in _ENTITY_RE.finditer(text):
        body = match.group("body").strip()
        type_match = _ENTITY_TYPE_RE.search(body)
        if type_match is None:
            continue
        entities.append(
            StepEntity(
                entity_id=int(match.group("id")),
                entity_type=type_match.group("type").upper(),
                arguments=body,
                line=text.count("\n", 0, match.start()) + 1,
            )
        )
    schema_match = _SCHEMA_RE.search(text)
    schema = (
        tuple(item for item in _STRING_RE.findall(schema_match.group("body")))
        if schema_match is not None
        else ()
    )
    units: list[StepUnitFact] = []
    for entity in entities:
        units.extend(_unit_facts(entity))
    # A repeated nested declaration in a complex entity is still one explicit
    # source fact.  De-duplicate only exact records, preserving source order.
    unique_units: list[StepUnitFact] = []
    seen: set[tuple[object, ...]] = set()
    for unit in units:
        key = (unit.category, unit.name, unit.prefix, unit.symbol, unit.entity_id)
        if key not in seen:
            seen.add(key)
            unique_units.append(unit)
    counts = Counter(entity.entity_type for entity in entities)
    return STEPInspection(
        source_name=resolved_name,
        source_path=source_path,
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        entities=tuple(entities),
        entity_counts=counts,
        schema_identifiers=schema,
        units=tuple(unique_units),
    )


inspect_step_file = inspect_step
STEPInventory = STEPInspection
STEPStructuralInventory = STEPInspection
inspect_step_text = inspect_step


__all__ = [
    "STEPInspection",
    "STEPInspectionError",
    "STEPInventory",
    "STEPStructuralInventory",
    "StepEntity",
    "StepInspection",
    "StepInspectionError",
    "StepUnitFact",
    "inspect_step",
    "inspect_step_file",
    "inspect_step_text",
]
