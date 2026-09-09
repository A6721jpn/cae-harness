"""Independent bounded whole-clause grounding; no semantic default engine."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from febio_cae.domain.evidence import EvidenceRef
from febio_cae.domain.material import (
    CompressibleNeoHookean,
    IsotropicLinearElastic,
    MaterialApplicability,
    MaterialCandidate,
)
from febio_cae.domain.units import Quantity

from .intent_contracts import ASSIGNMENT_KEYS, MATERIAL_FIELDS, PHYSICAL_COMPONENTS

ALIASES = {
    "\u6750\u6599\u30e2\u30c7\u30eb": "material.model",
    "\u30e4\u30f3\u30b0\u7387": "material.youngs_modulus",
    "\u30dd\u30a2\u30bd\u30f3\u6bd4": "material.poisson_ratio",
    "\u3072\u305a\u307f\u9069\u7528\u6027": "material.strain_applicability",
    "\u901f\u5ea6\u9069\u7528\u6027": "material.rate_applicability",
}
_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
_MODEL = {
    "isotropic_linear_elastic": "isotropic_linear_elastic",
    "compressible_neo_hookean": "compressible_neo_hookean",
    "\u7b49\u65b9\u7dda\u5f62\u5f3e\u6027": "isotropic_linear_elastic",
    "\u5727\u7e2e\u6027Neo-Hookean": "compressible_neo_hookean",
}
_APPLIES = {"applicable": "applicable", "applies": "applicable", "\u9069\u7528\u53ef": "applicable"}


@dataclass(frozen=True)
class Fact:
    field: str
    value: str
    unit: str
    source: str
    clause: str

    def normalized(self) -> tuple[str, str]:
        if self.field in {"material.youngs_modulus", "material.poisson_ratio"}:
            q = Quantity(float(self.value), self.unit).to_si()
            return str(float(q.value)), q.unit
        return self.value, self.unit


def parse_clause(clause: str, source: str) -> tuple[str | None, Fact | None]:
    normalized = unicodedata.normalize("NFKC", clause).strip()
    parts = re.fullmatch(r"([^=:\n]+)\s*[=:]\s*(.+)", normalized)
    if parts is None:
        return None, None
    field = ALIASES.get(parts[1].strip(), parts[1].strip())
    if field not in MATERIAL_FIELDS and field not in PHYSICAL_COMPONENTS:
        return None, None
    value = parts[2].strip()
    unit = ""
    if field == "material.model":
        value = _MODEL.get(value, "")
    elif field in {"material.strain_applicability", "material.rate_applicability"}:
        value = _APPLIES.get(value, "")
    elif field in {"material.youngs_modulus", "material.poisson_ratio"}:
        quantity = re.fullmatch(r"(\S+)\s+(\S+)", value)
        if quantity is None or _NUMBER.fullmatch(quantity[1]) is None:
            return field, None
        value, unit = quantity[1], quantity[2]
        try:
            q = Quantity(float(value), unit)
            if field.endswith("youngs_modulus"):
                if q.dimension != Quantity(1, "Pa").dimension or q.to_si().value <= 0:
                    return field, None
            elif not q.dimension.is_dimensionless or not -1 < q.to_si().value < 0.5:
                return field, None
        except ValueError:
            return field, None
    else:
        if re.fullmatch(r"adopt [A-Za-z0-9_-]+\." + re.escape(field), value) is None:
            return field, None
    return (field, Fact(field, value, unit, source, clause)) if value else (field, None)


def local_facts(sources: list[dict[str, str]]) -> tuple[dict[str, Fact], set[str]]:
    found: dict[str, Fact] = {}
    blocked: set[str] = set()
    for source in sources:
        for clause in source["text"].splitlines():
            field, fact = parse_clause(clause, source["id"])
            if field is None:
                continue
            if fact is None or field in found and found[field].normalized() != fact.normalized():
                blocked.add(field)
            else:
                found[field] = fact
    if "material" in found and any(field in found for field in MATERIAL_FIELDS):
        blocked.update(("material", *MATERIAL_FIELDS))
    return {field: fact for field, fact in found.items() if field not in blocked}, blocked


def ground(
    proposal: dict[str, Any], sources: list[dict[str, str]], *, entity: str, retained_ids: set[str]
) -> dict[str, Fact]:
    candidates, _ = local_facts(sources)
    source_map = {source["id"]: source["text"].splitlines() for source in sources}
    # Previously retained explicit facts are rederived locally, not entrusted to provider recall.
    accepted = {field: fact for field, fact in candidates.items() if fact.source in retained_ids}
    for item in proposal["assignments"]:
        if (
            not isinstance(item, dict)
            or set(item) != set(ASSIGNMENT_KEYS)
            or any(not isinstance(v, str) for v in item.values())
        ):
            raise ValueError("proposal assignment violates the closed application schema")
        if (
            item["scope"] != "case"
            or item["entity"] != entity
            or item["clause"] not in source_map.get(item["source"], [])
        ):
            continue
        field, fact = parse_clause(item["clause"], item["source"])
        if fact is None or field not in candidates or item["field"] != field:
            continue
        try:
            offered = Fact(field, item["value"], item["unit"], item["source"], item["clause"])
            # Reparse proposed value as well: it cannot bypass affirmative enum/unit checks.
            _, parsed = parse_clause(
                f"{field} = {item['value']} {item['unit']}".strip(), item["source"]
            )
            if (
                parsed is not None
                and offered.normalized() == fact.normalized() == candidates[field].normalized()
            ):
                accepted[field] = fact
        except (ValueError, OverflowError):
            continue
    return accepted


def evidence(fact: Fact, sources: list[dict[str, str]]) -> EvidenceRef:
    import hashlib

    source = next(item for item in sources if item["id"] == fact.source)
    return EvidenceRef(
        "1",
        "user_instruction",
        fact.source,
        fact.field,
        hashlib.sha256(source["text"].encode("utf-8")).hexdigest(),
    )


def material_from_facts(
    facts: dict[str, Fact], sources: list[dict[str, str]]
) -> MaterialCandidate | None:
    if any(field not in facts for field in MATERIAL_FIELDS):
        return None
    model = (
        IsotropicLinearElastic
        if facts["material.model"].value == "isotropic_linear_elastic"
        else CompressibleNeoHookean
    )

    def ev(field: str) -> EvidenceRef:
        return evidence(facts["material." + field], sources)

    return model(
        Quantity(
            float(facts["material.youngs_modulus"].value), facts["material.youngs_modulus"].unit
        ),
        Quantity(
            float(facts["material.poisson_ratio"].value), facts["material.poisson_ratio"].unit
        ),
        ev("model"),
        ev("youngs_modulus"),
        ev("poisson_ratio"),
        MaterialApplicability(
            facts["material.strain_applicability"].value,
            ev("strain_applicability"),
            facts["material.rate_applicability"].value,
            ev("rate_applicability"),
        ),
    )


def known_projection(facts: dict[str, Fact]) -> dict[str, Any]:
    return {field: {"value": fact.value, "unit": fact.unit} for field, fact in facts.items()}
