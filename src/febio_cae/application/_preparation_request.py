"""Private normalization for the existing explicit-spec request; no domain changes."""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from typing import Any

from .specs import SpecInputError, SpecUpdateRequest, parse_spec_request


@dataclass(frozen=True, slots=True)
class PreparationLimits:
    wall_seconds: float = 600.0
    max_tetrahedra: int = 100000
    max_nodes: int = 250000
    cpu_workers: int | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.wall_seconds, bool)
            or not isinstance(self.wall_seconds, (int, float))
            or not math.isfinite(self.wall_seconds)
            or self.wall_seconds <= 0
        ):
            raise SpecInputError("preparation wall_seconds must be finite and positive")
        for value in (self.max_tetrahedra, self.max_nodes, self.cpu_workers):
            if value is not None and (type(value) is not int or value <= 0):
                raise SpecInputError("preparation counts must be positive integers")
        if self.max_tetrahedra is None or self.max_nodes is None:
            raise SpecInputError("preparation counts cannot be unlimited")


def request_parts(payload: object) -> tuple[dict[str, Any], PreparationLimits]:
    if not isinstance(payload, dict):
        raise SpecInputError("preparation request must be an object")
    request = copy.deepcopy(payload)
    raw = request.pop("preparation", {})
    if not isinstance(raw, dict) or set(raw) - {
        "wall_seconds",
        "max_tetrahedra",
        "max_nodes",
        "cpu_workers",
    }:
        raise SpecInputError("unsupported preparation limits")
    limits = PreparationLimits(**raw)
    normalize_request(request)  # Complete structural validation before any operation.
    return request, limits


def normalize_request(
    payload: dict[str, Any],
    *,
    geometry_digest: str | None = None,
    inspection_digest: str | None = None,
) -> SpecUpdateRequest:
    request = copy.deepcopy(payload)
    try:
        geometry = request["values"]["geometry"]
        body = geometry["body_id"]
    except (KeyError, TypeError) as error:
        raise SpecInputError("explicit geometry source/body/unit/placement required") from error
    if not isinstance(geometry, dict) or not isinstance(body, str):
        raise SpecInputError("explicit geometry source/body/unit/placement required")

    def bind(item: dict[str, Any], key: str, actual: str | None) -> None:
        if key not in item:
            raise SpecInputError(f"generated field {key} must be explicit text or null")
        value = item[key]
        if value is None:
            item[key] = actual or "0" * 64
        elif actual is not None and value != actual:
            raise SpecInputError(f"asserted {key} differs from current inspection")

    bind(geometry, "geometry_digest", geometry_digest)
    bind(geometry, "inspection_digest", inspection_digest)

    def selections(value: Any) -> None:
        if isinstance(value, dict):
            if {
                "name",
                "stated_role",
                "rule",
                "geometry_digest",
                "body_id",
            } <= value.keys() and value["body_id"] == body:
                bind(value, "geometry_digest", geometry_digest)
            for child in value.values():
                selections(child)
        elif isinstance(value, list):
            for child in value:
                selections(child)

    selections(request["values"])
    parsed = parse_spec_request(request)
    parsed.values.to_case_spec()  # Missing physics is never filled by this normalizer.
    return parsed
