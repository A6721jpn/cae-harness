"""Installed-package synthetic numerical acceptance flow.

This gate deliberately stays outside the normal local test paths.  It exercises
only the installed CLI and the configured synthetic inputs; no source checkout
module is imported by the product subprocesses.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import sqlite3
import struct
import subprocess
import uuid
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest


_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_EVIDENCE_KEYS = {
    "schema_version",
    "source_kind",
    "reference",
    "target_field",
    "content_digest",
}
_SOURCE_KINDS = {
    "user_instruction",
    "registered_document",
    "registered_material",
    "registered_test_condition",
}
_CASE_FIELDS = (
    "geometry",
    "material",
    "support",
    "rigid_tool",
    "motion",
    "contact",
    "mesh_policy",
    "solver_policy",
    "outputs",
    "quality_policy",
    "budget",
)
_FIXED_CONDITIONS = (
    "geometry",
    "support",
    "rigid_tool",
    "motion",
    "contact",
    "mesh_policy",
    "solver_policy",
    "outputs",
    "quality_policy",
    "budget",
    "material.kind",
    "material.poisson_ratio",
    "material.applicability",
    "material.model_evidence",
    "material.poisson_ratio_evidence",
)
_INTENDED_CHANGES = ("material.youngs_modulus",)
_COMPARISON_AXES = {
    "tool_compression.force_z": (
        "contact_force.world_z",
        "identity",
    ),
    "tool_compression.part_peak_abs_displacement_z": (
        "displacement.world_z",
        "peak_abs",
    ),
}
_REQUIRED_NUMERICAL = {
    "execution_result_completeness",
    "contact_quality",
    "motion_support_contact_fidelity",
    "quasistatic_equilibrium",
    "solver_residual",
    "mesh_dependence",
}
_ALLOWED_NUMERICAL = {"PASS", "NOT_APPLICABLE"}
_FORBIDDEN_INPUT_KEYS = {
    "argv",
    "command",
    "commands",
    "executable",
    "shell",
    "shell_command",
}
_UNIT_FACTORS = {
    "1": 1.0,
    "Pa": 1.0,
    "kPa": 1.0e3,
    "MPa": 1.0e6,
    "GPa": 1.0e9,
    "m": 1.0,
    "mm": 1.0e-3,
    "um": 1.0e-6,
    "s": 1.0,
    "ms": 1.0e-3,
}


class _EnvironmentNotReady(RuntimeError):
    """The configured installed environment cannot be exercised."""


class _AcceptanceFailure(AssertionError):
    """An observable acceptance contract failed."""


@dataclass(frozen=True, slots=True)
class _FileIdentity:
    path: Path
    digest: str
    size: int


@dataclass(frozen=True, slots=True)
class _Settings:
    installed_python: _FileIdentity
    wheel: _FileIdentity
    solver: _FileIdentity
    source_step: _FileIdentity
    preparation_request: _FileIdentity
    youngs_modulus_pa: float
    instruction: str
    axes: tuple[dict[str, Any], ...]
    fixed_conditions: tuple[str, ...]
    preparation_calls: int
    solver_calls: int
    command_timeout_seconds: float
    request: dict[str, Any]
    wheel_version: str
    part_body_id: str
    tool_body_id: str
    saved_time_count: int


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise ValueError(f"non-standard JSON constant {value!r}")


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        _canonicalize(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _canonicalize(value: object) -> object:
    if isinstance(value, float) and value == 0.0:
        return 0.0
    if isinstance(value, dict):
        return {key: _canonicalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_canonicalize(item) for item in value]
    return value


def _as_object(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise _EnvironmentNotReady(f"{field} must be a JSON object")
    return cast(dict[str, Any], value)


def _strict_object(
    value: object,
    *,
    required: set[str],
    allowed: set[str],
    field: str,
) -> dict[str, Any]:
    item = _as_object(value, field)
    actual = set(item)
    missing = required - actual
    unknown = actual - allowed
    if missing or unknown:
        raise _EnvironmentNotReady(
            f"{field} has missing={sorted(missing)!r}, unknown={sorted(unknown)!r}"
        )
    return item


def _text(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise _EnvironmentNotReady(f"{field} must be non-empty control-free text")
    return value


def _digest(value: object, field: str) -> str:
    text = _text(value, field)
    if _DIGEST.fullmatch(text) is None:
        raise _EnvironmentNotReady(f"{field} must be a lowercase SHA-256 digest")
    return text


def _finite(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _EnvironmentNotReady(f"{field} must be finite numeric data")
    result = float(value)
    if not math.isfinite(result):
        raise _EnvironmentNotReady(f"{field} must be finite numeric data")
    return result


def _read_json(path: Path, field: str, *, environment: bool) -> dict[str, Any]:
    error_type: type[Exception] = (
        _EnvironmentNotReady if environment else _AcceptanceFailure
    )
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeError, ValueError) as error:
        raise error_type(f"cannot read {field}: {error}") from error
    if not isinstance(value, dict):
        raise error_type(f"{field} must be a JSON object")
    return cast(dict[str, Any], value)


def _contains_02_cae(path: Path) -> bool:
    return any(part.casefold() == "02_cae" for part in path.parts)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise _EnvironmentNotReady(f"cannot hash configured file {path}: {error}") from error
    return digest.hexdigest()


def _file_identity(value: object, field: str) -> _FileIdentity:
    item = _strict_object(
        value,
        required={"path", "sha256", "size"},
        allowed={"path", "sha256", "size"},
        field=field,
    )
    raw_path = _text(item["path"], f"{field}.path")
    path = Path(raw_path)
    if not path.is_absolute() or _contains_02_cae(path):
        raise _EnvironmentNotReady(f"{field}.path must be absolute and outside 02_CAE")
    if isinstance(item["size"], bool) or type(item["size"]) is not int or item["size"] < 1:
        raise _EnvironmentNotReady(f"{field}.size must be a positive integer")
    expected_digest = _digest(item["sha256"], f"{field}.sha256")
    try:
        if path.is_symlink():
            raise _EnvironmentNotReady(f"{field}.path must not be a symbolic link")
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        if attributes & 0x400:
            raise _EnvironmentNotReady(f"{field}.path must not be a reparse point")
        stat_result = path.stat()
    except OSError as error:
        raise _EnvironmentNotReady(f"{field}.path is unavailable: {error}") from error
    if not path.is_file() or stat_result.st_size != item["size"]:
        raise _EnvironmentNotReady(f"{field}.path size/type does not match its identity")
    actual_digest = _file_sha256(path)
    if actual_digest != expected_digest:
        raise _EnvironmentNotReady(f"{field}.path digest does not match its identity")
    return _FileIdentity(path, expected_digest, item["size"])


def _walk_forbidden_keys(value: object, path: str = "settings") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise _EnvironmentNotReady(f"{path} has a non-text key")
            if key.casefold() in _FORBIDDEN_INPUT_KEYS:
                raise _EnvironmentNotReady(f"{path}.{key} is not an allowed input")
            _walk_forbidden_keys(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _walk_forbidden_keys(child, f"{path}[{index}]")


def _quantity_si(value: object, field: str) -> float:
    item = _strict_object(
        value,
        required={"value", "unit"},
        allowed={"value", "unit"},
        field=field,
    )
    unit = _text(item["unit"], f"{field}.unit")
    if unit not in _UNIT_FACTORS:
        raise _EnvironmentNotReady(f"{field}.unit is not a supported explicit test unit")
    return _finite(item["value"], f"{field}.value") * _UNIT_FACTORS[unit]


def _binary32(value: float, field: str) -> float:
    try:
        return struct.unpack("<f", struct.pack("<f", value))[0]
    except (OverflowError, struct.error) as error:
        raise _AcceptanceFailure(f"{field} is not binary32-representable") from error


def _validate_evidence(value: object, field: str) -> dict[str, Any]:
    item = _strict_object(
        value,
        required=_EVIDENCE_KEYS,
        allowed=_EVIDENCE_KEYS,
        field=field,
    )
    if item["schema_version"] != "1":
        raise _EnvironmentNotReady(f"{field}.schema_version must be '1'")
    source_kind = _text(item["source_kind"], f"{field}.source_kind")
    if source_kind not in _SOURCE_KINDS:
        raise _EnvironmentNotReady(f"{field}.source_kind is unsupported")
    _text(item["reference"], f"{field}.reference")
    _text(item["target_field"], f"{field}.target_field")
    _digest(item["content_digest"], f"{field}.content_digest")
    return item


def _validate_source_declaration(value: object, field: str) -> dict[str, Any]:
    item = _strict_object(
        value,
        required={"source_kind", "reference", "target_field"},
        allowed={
            "source_kind",
            "reference",
            "target_field",
            "content",
            "content_digest",
            "media_type",
        },
        field=field,
    )
    source_kind = _text(item["source_kind"], f"{field}.source_kind")
    if source_kind not in _SOURCE_KINDS:
        raise _EnvironmentNotReady(f"{field}.source_kind is unsupported")
    reference = _text(item["reference"], f"{field}.reference")
    _text(item["target_field"], f"{field}.target_field")
    content = item.get("content")
    if content is not None and not isinstance(content, str):
        raise _EnvironmentNotReady(f"{field}.content must be UTF-8 text or null")
    digest = item.get("content_digest")
    if content is not None:
        if digest is None:
            raise _EnvironmentNotReady(f"{field}.content_digest is required with content")
        if _digest(digest, f"{field}.content_digest") != hashlib.sha256(
            content.encode("utf-8")
        ).hexdigest():
            raise _EnvironmentNotReady(f"{field}.content digest does not match content")
    elif reference != "cad":
        raise _EnvironmentNotReady(
            f"{field} without content can only refer to the fresh registered CAD source"
        )
    if digest is not None:
        _digest(digest, f"{field}.content_digest")
    if "media_type" in item:
        _text(item["media_type"], f"{field}.media_type")
    return item


def _validate_request(
    request: dict[str, Any], source: _FileIdentity
) -> tuple[str, str, int]:
    allowed = {
        "schema_version",
        "values",
        "evidence",
        "source_declarations",
        "input_intent",
        "preparation",
    }
    item = _strict_object(
        request,
        required={"schema_version", "values", "evidence", "source_declarations"},
        allowed=allowed,
        field="preparation_request",
    )
    if item["schema_version"] != "1":
        raise _EnvironmentNotReady("preparation_request.schema_version must be '1'")
    if "input_intent" in item:
        input_intent = item["input_intent"]
        if not isinstance(input_intent, str) or input_intent not in {"", "explicit"}:
            raise _EnvironmentNotReady("preparation_request.input_intent must be explicit")
    if "preparation" in item:
        preparation = _as_object(item["preparation"], "preparation_request.preparation")
        if set(preparation) - {"wall_seconds", "max_tetrahedra", "max_nodes", "cpu_workers"}:
            raise _EnvironmentNotReady("preparation_request.preparation has unsupported limits")
        for key, value in preparation.items():
            if value is not None and (
                isinstance(value, bool) or type(value) is not int and key != "wall_seconds"
            ):
                raise _EnvironmentNotReady(f"preparation_request.preparation.{key} is invalid")
            if key == "wall_seconds":
                if _finite(value, f"preparation_request.preparation.{key}") <= 0:
                    raise _EnvironmentNotReady(
                        f"preparation_request.preparation.{key} must be positive"
                    )
            elif value is not None and value <= 0:
                raise _EnvironmentNotReady(
                    f"preparation_request.preparation.{key} must be positive"
                )

    values = _strict_object(
        item["values"],
        required={"schema_version", *_CASE_FIELDS},
        allowed={"schema_version", *_CASE_FIELDS},
        field="preparation_request.values",
    )
    if values["schema_version"] != "1":
        raise _EnvironmentNotReady("preparation_request.values.schema_version must be '1'")
    if any(values[field] is None for field in _CASE_FIELDS):
        raise _EnvironmentNotReady("preparation_request.values must be complete")

    raw_top_evidence = item["evidence"]
    if not isinstance(raw_top_evidence, list):
        raise _EnvironmentNotReady("preparation_request.evidence must be an array")
    evidence: list[dict[str, Any]] = [
        _validate_evidence(entry, f"preparation_request.evidence[{index}]")
        for index, entry in enumerate(raw_top_evidence)
    ]
    raw_sources = item["source_declarations"]
    if not isinstance(raw_sources, list):
        raise _EnvironmentNotReady("preparation_request.source_declarations must be an array")
    sources = [
        _validate_source_declaration(entry, f"preparation_request.source_declarations[{index}]")
        for index, entry in enumerate(raw_sources)
    ]
    references = [entry["reference"] for entry in sources]
    if len(set(references)) != len(references):
        raise _EnvironmentNotReady(
            "preparation_request.source_declarations has duplicate references"
        )
    source_by_reference = {entry["reference"]: entry for entry in sources}

    nested_evidence: list[dict[str, Any]] = []
    part_body_id: str | None = None
    tool_body_id: str | None = None

    def visit(value: object, path: str) -> None:
        nonlocal part_body_id, tool_body_id
        if isinstance(value, dict):
            keys = set(value)
            if _EVIDENCE_KEYS <= keys:
                nested_evidence.append(_validate_evidence(value, path))
            for key, child in value.items():
                if key == "geometry_digest" or key == "inspection_digest":
                    if child is not None:
                        raise _EnvironmentNotReady(f"{path}.{key} must be null before preparation")
                if key == "resolution" and child is not None:
                    raise _EnvironmentNotReady(f"{path}.resolution must be null before preparation")
                visit(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]")

    visit(values, "preparation_request.values")
    geometry = _as_object(values["geometry"], "preparation_request.values.geometry")
    if (
        "geometry_digest" not in geometry
        or geometry["geometry_digest"] is not None
        or "inspection_digest" not in geometry
        or geometry["inspection_digest"] is not None
    ):
        raise _EnvironmentNotReady(
            "preparation geometry/inspection digests must be explicit nulls before preparation"
        )
    source_digest = _digest(
        geometry.get("source_step_digest"),
        "preparation_request.values.geometry.source_step_digest",
    )
    if source_digest != source.digest:
        raise _EnvironmentNotReady("preparation geometry is not bound to the configured STEP")
    part_body_id = _text(geometry.get("body_id"), "preparation_request.values.geometry.body_id")
    rigid_tool = _as_object(values["rigid_tool"], "preparation_request.values.rigid_tool")
    primitive = _as_object(
        rigid_tool.get("primitive"),
        "preparation_request.values.rigid_tool.primitive",
    )
    tool_body_id = _text(
        primitive.get("body_id"),
        "preparation_request.values.rigid_tool.primitive.body_id",
    )
    if part_body_id == tool_body_id:
        raise _EnvironmentNotReady("part and tool body IDs must be distinct")

    material = _as_object(values["material"], "preparation_request.values.material")
    if material.get("kind") != "isotropic_linear_elastic":
        raise _EnvironmentNotReady("synthetic acceptance requires isotropic linear elasticity")
    youngs_modulus = _quantity_si(
        material.get("youngs_modulus"),
        "preparation_request.values.material.youngs_modulus",
    )
    if youngs_modulus <= 0:
        raise _EnvironmentNotReady("preparation material Young's modulus must be positive")
    poisson = _quantity_si(
        material.get("poisson_ratio"),
        "preparation_request.values.material.poisson_ratio",
    )
    if not -1.0 < poisson < 0.5:
        raise _EnvironmentNotReady("preparation material Poisson ratio is outside its domain")
    if not nested_evidence and not evidence:
        raise _EnvironmentNotReady("preparation request has no explicit evidence")
    all_evidence = evidence + nested_evidence
    for entry in all_evidence:
        reference = entry["reference"]
        if reference == "cad":
            if (
                entry["source_kind"] != "registered_document"
                or entry["content_digest"] != source.digest
            ):
                raise _EnvironmentNotReady("CAD evidence is not bound to the configured STEP")
            continue
        declaration = source_by_reference.get(reference)
        if declaration is None:
            raise _EnvironmentNotReady(f"evidence source {reference!r} has no declaration")
        if (
            declaration.get("source_kind") != entry["source_kind"]
            or declaration.get("target_field") != entry["target_field"]
        ):
            raise _EnvironmentNotReady(f"evidence declaration {reference!r} is not field-bound")
        if (
            declaration.get("content_digest") is not None
            and declaration["content_digest"] != entry["content_digest"]
        ):
            raise _EnvironmentNotReady(f"evidence declaration {reference!r} has a stale digest")
    if not any(
        entry["target_field"] == "material.youngs_modulus" for entry in nested_evidence
    ):
        raise _EnvironmentNotReady("material Young's modulus lacks field-bound evidence")

    outputs = _as_object(values["outputs"], "preparation_request.values.outputs")
    requests = outputs.get("requests")
    if not isinstance(requests, list):
        raise _EnvironmentNotReady("outputs.requests must be an array")
    by_quantity: dict[str, list[dict[str, Any]]] = {}
    for index, request_value in enumerate(requests):
        request = _as_object(request_value, f"outputs.requests[{index}]")
        quantity_id = _text(request.get("quantity_id"), f"outputs.requests[{index}].quantity_id")
        by_quantity.setdefault(quantity_id, []).append(request)
    if any(
        len(by_quantity.get(quantity, [])) != 1
        for quantity in ("contact_force", "displacement")
    ):
        raise _EnvironmentNotReady("exactly one force and one displacement output are required")
    for quantity, location, unit, body in (
        ("contact_force", "rigid_body", "N", tool_body_id),
        ("displacement", "node", "m", part_body_id),
    ):
        request = by_quantity[quantity][0]
        if (
            request.get("measure_id") != "value"
            or request.get("component_id") != "z"
            or request.get("location") != location
            or request.get("frame") != "World"
            or request.get("display_unit") != unit
        ):
            raise _EnvironmentNotReady(
                f"{quantity} output semantic binding is not comparison-ready"
            )
        selection = _as_object(request.get("selection"), f"{quantity}.selection")
        rule = _as_object(selection.get("rule"), f"{quantity}.selection.rule")
        if (
            selection.get("body_id") != body
            or selection.get("frame") != "World"
            or rule.get("kind") != "whole_body"
            or rule.get("body_id") != body
        ):
            raise _EnvironmentNotReady(f"{quantity} output ROI is not the registered whole body")
    saved_times = outputs.get("saved_times")
    if not isinstance(saved_times, list) or len(saved_times) < 2:
        raise _EnvironmentNotReady("outputs.saved_times must contain at least two states")
    for index, value in enumerate(saved_times):
        _quantity_si(value, f"outputs.saved_times[{index}]")

    motion = _as_object(values["motion"], "preparation_request.values.motion")
    direction = _as_object(motion.get("direction"), "preparation_request.values.motion.direction")
    if (
        direction.get("frame") != "World"
        or _finite(direction.get("x"), "motion.direction.x") != 0.0
        or _finite(direction.get("y"), "motion.direction.y") != 0.0
        or _finite(direction.get("z"), "motion.direction.z") != -1.0
    ):
        raise _EnvironmentNotReady("comparison requires explicit negative World-z motion")
    samples = motion.get("samples")
    if not isinstance(samples, list) or len(samples) < 2:
        raise _EnvironmentNotReady("motion.samples must contain at least two states")
    previous_time: float | None = None
    previous_displacement: float | None = None
    for index, sample_value in enumerate(samples):
        sample = _as_object(sample_value, f"motion.samples[{index}]")
        current_time = _quantity_si(sample.get("time"), f"motion.samples[{index}].time")
        current_displacement = _quantity_si(
            sample.get("displacement"),
            f"motion.samples[{index}].displacement",
        )
        if (
            previous_time is not None
            and (
                current_time <= previous_time
                or current_displacement <= cast(float, previous_displacement)
            )
        ):
            raise _EnvironmentNotReady("motion samples must increase in time and compression")
        previous_time, previous_displacement = current_time, current_displacement
    return cast(str, part_body_id), cast(str, tool_body_id), len(saved_times)


def _validate_axes(
    value: object,
    *,
    part_body_id: str,
    tool_body_id: str,
) -> tuple[tuple[dict[str, Any], ...], tuple[str, ...]]:
    comparison = _strict_object(
        value,
        required={"axes", "fixed_conditions"},
        allowed={"axes", "fixed_conditions", "intended_changes"},
        field="settings.comparison",
    )
    raw_fixed = comparison["fixed_conditions"]
    if (
        not isinstance(raw_fixed, list)
        or any(not isinstance(item, str) or not item.strip() for item in raw_fixed)
        or len(set(raw_fixed)) != len(raw_fixed)
        or set(raw_fixed) != set(_FIXED_CONDITIONS)
    ):
        raise _EnvironmentNotReady(
            "comparison.fixed_conditions must declare every fixed condition once"
        )
    if (
        "intended_changes" in comparison
        and comparison["intended_changes"] != list(_INTENDED_CHANGES)
    ):
        raise _EnvironmentNotReady("comparison.intended_changes is fixed to the E-only change")
    raw_axes = comparison["axes"]
    if not isinstance(raw_axes, list) or len(raw_axes) != len(_COMPARISON_AXES):
        raise _EnvironmentNotReady(
            "comparison.axes must contain both supported actual axis records"
        )
    axes: list[dict[str, Any]] = []
    intervals: list[tuple[float, float]] = []
    for index, raw_axis in enumerate(raw_axes):
        axis = _strict_object(
            raw_axis,
            required={
                "schema_version",
                "axis_id",
                "unit",
                "roi_id",
                "measure_id",
                "aggregation_id",
                "interval",
                "interpolation",
            },
            allowed={
                "schema_version",
                "axis_id",
                "unit",
                "roi_id",
                "measure_id",
                "aggregation_id",
                "interval",
                "interpolation",
            },
            field=f"settings.comparison.axes[{index}]",
        )
        if axis["schema_version"] != "1":
            raise _EnvironmentNotReady(f"comparison.axes[{index}].schema_version must be '1'")
        axis_id = _text(axis["axis_id"], f"comparison.axes[{index}].axis_id")
        if axis_id not in _COMPARISON_AXES:
            raise _EnvironmentNotReady(f"unsupported comparison axis {axis_id!r}")
        if axis["unit"] != "m" or axis["interpolation"] != "linear":
            raise _EnvironmentNotReady(
                "comparison axes require metre units and linear interpolation"
            )
        expected_measure, expected_aggregation = _COMPARISON_AXES[axis_id]
        expected_roi = tool_body_id if axis_id.endswith("force_z") else part_body_id
        if (
            axis["measure_id"] != expected_measure
            or axis["aggregation_id"] != expected_aggregation
            or axis["roi_id"] != expected_roi
        ):
            raise _EnvironmentNotReady(
                f"comparison axis {axis_id!r} is not an actual registered ROI"
            )
        interval = _strict_object(
            axis["interval"],
            required={"schema_version", "unit", "lower", "upper"},
            allowed={"schema_version", "unit", "lower", "upper"},
            field=f"comparison.axes[{index}].interval",
        )
        if interval["schema_version"] != "1" or interval["unit"] != "m":
            raise _EnvironmentNotReady("comparison interval must be an explicit metre interval")
        lower = _finite(interval["lower"], f"comparison.axes[{index}].interval.lower")
        upper = _finite(interval["upper"], f"comparison.axes[{index}].interval.upper")
        if lower < 0.0 or lower >= upper:
            raise _EnvironmentNotReady(
                "comparison interval must be finite, positive-direction, and ordered"
            )
        intervals.append((lower, upper))
        axes.append(axis)
    if {axis["axis_id"] for axis in axes} != set(_COMPARISON_AXES) or len(
        {axis["axis_id"] for axis in axes}
    ) != len(axes):
        raise _EnvironmentNotReady("comparison.axes must contain each supported axis once")
    if len(set(intervals)) != 1:
        raise _EnvironmentNotReady("comparison axes must share one common interval")
    return tuple(axes), tuple(cast(str, item) for item in raw_fixed)


def _wheel_version(wheel: _FileIdentity) -> str:
    try:
        with zipfile.ZipFile(wheel.path) as archive:
            metadata_names = [
                name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
            ]
            if len(metadata_names) != 1:
                raise _EnvironmentNotReady("wheel must contain exactly one dist-info METADATA")
            metadata = archive.read(metadata_names[0]).decode("utf-8")
    except (OSError, UnicodeError, ValueError, zipfile.BadZipFile) as error:
        raise _EnvironmentNotReady(f"cannot inspect configured wheel metadata: {error}") from error
    versions = [
        line.partition(":")[2].strip()
        for line in metadata.splitlines()
        if line.startswith("Version:")
    ]
    if len(versions) != 1 or not versions[0]:
        raise _EnvironmentNotReady("wheel metadata must declare exactly one Version")
    return versions[0]


_RUNTIME_PROBE = """\
import json
import pathlib
import site
import sys
import sysconfig

import febio_cae

paths = sysconfig.get_paths()
print(json.dumps({
    "sys_executable": str(pathlib.Path(sys.executable).resolve()),
    "version": febio_cae.__version__,
    "module": str(pathlib.Path(febio_cae.__file__).resolve()),
    "purelib": str(pathlib.Path(paths["purelib"]).resolve()),
    "platlib": str(pathlib.Path(paths["platlib"]).resolve()),
    "site_packages": [str(pathlib.Path(item).resolve()) for item in site.getsitepackages()],
}, sort_keys=True, separators=(",", ":")))
"""


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(str(left))) == os.path.normcase(
        os.path.abspath(str(right))
    )


def _under(path: Path, parent: Path) -> bool:
    try:
        path.absolute().relative_to(parent.absolute())
    except ValueError:
        return False
    return True


def _validate_runtime(
    settings: _Settings,
    report: "_Report",
    cwd: Path,
) -> None:
    argv = [str(settings.installed_python.path), "-I", "-c", _RUNTIME_PROBE]
    code, payload = _run_subprocess(
        report,
        stage="installed-runtime-probe",
        argv=argv,
        cwd=cwd,
        timeout=settings.command_timeout_seconds,
        native_kind=None,
    )
    if code != 0:
        raise _EnvironmentNotReady(f"installed runtime probe exited {code}")
    executable = Path(_text(payload.get("sys_executable"), "runtime.sys_executable"))
    module = Path(_text(payload.get("module"), "runtime.module"))
    version = _text(payload.get("version"), "runtime.version")
    purelib = Path(_text(payload.get("purelib"), "runtime.purelib"))
    platlib = Path(_text(payload.get("platlib"), "runtime.platlib"))
    site_packages = payload.get("site_packages")
    if not isinstance(site_packages, list) or any(
        not isinstance(item, str) for item in site_packages
    ):
        raise _EnvironmentNotReady("runtime.site_packages is not a string array")
    sites = [Path(item) for item in site_packages]
    allowed_sites = [purelib, platlib, *sites]
    if not _same_path(executable, settings.installed_python.path):
        raise _EnvironmentNotReady("installed runtime used a different Python executable")
    if version != settings.wheel_version:
        raise _EnvironmentNotReady("installed package version differs from the configured wheel")
    if _under(module, Path(__file__).resolve().parents[2]):
        raise _EnvironmentNotReady("installed package resolved to the source checkout")
    if not any(_under(module, site_path) for site_path in allowed_sites):
        raise _EnvironmentNotReady("installed package did not resolve under site-packages")
    report.data["environment"] = {
        "installed_python": str(settings.installed_python.path),
        "wheel": str(settings.wheel.path),
        "wheel_version": settings.wheel_version,
        "module": str(module),
        "site_packages": [str(item) for item in allowed_sites],
    }
    report.write()


def _load_settings() -> _Settings:
    raw_path = os.environ.get("FEBIO_CAE_E2E_SETTINGS", "").strip()
    if not raw_path:
        raise _EnvironmentNotReady("FEBIO_CAE_E2E_SETTINGS is not configured")
    settings_path = Path(raw_path)
    if not settings_path.is_absolute() or _contains_02_cae(settings_path):
        raise _EnvironmentNotReady("FEBIO_CAE_E2E_SETTINGS must be an absolute non-02_CAE path")
    raw = _read_json(settings_path, "settings", environment=True)
    allowed = {
        "schema_version",
        "scope",
        "installed_python",
        "wheel",
        "solver",
        "source_step",
        "preparation_request",
        "edit",
        "comparison",
        "limits",
    }
    settings = _strict_object(raw, required=allowed, allowed=allowed, field="settings")
    if settings["schema_version"] != "1" or settings["scope"] != "synthetic_explicit":
        raise _EnvironmentNotReady(
            "settings must use schema_version '1' and synthetic_explicit scope"
        )
    identities = {
        name: _file_identity(settings[name], f"settings.{name}")
        for name in ("installed_python", "wheel", "solver", "source_step", "preparation_request")
    }
    wheel_version = _wheel_version(identities["wheel"])
    edit = _strict_object(
        settings["edit"],
        required={"youngs_modulus_Pa", "instruction"},
        allowed={"youngs_modulus_Pa", "instruction"},
        field="settings.edit",
    )
    youngs_modulus_pa = _finite(edit["youngs_modulus_Pa"], "settings.edit.youngs_modulus_Pa")
    if youngs_modulus_pa <= 0:
        raise _EnvironmentNotReady("settings.edit.youngs_modulus_Pa must be positive")
    instruction = _text(edit["instruction"], "settings.edit.instruction")
    instruction_lower = instruction.casefold()
    if not (
        "youngs_modulus" in instruction_lower
        or "young's modulus" in instruction_lower
        or re.search(r"(?<![a-z])e(?![a-z])", instruction_lower)
    ) or not any(
        marker in instruction_lower for marker in ("only", "unchanged", "no other", "all other")
    ):
        raise _EnvironmentNotReady(
            "settings.edit.instruction must be an explicit E-only instruction"
        )
    request = _read_json(
        identities["preparation_request"].path,
        "preparation_request",
        environment=True,
    )
    part_body_id, tool_body_id, saved_time_count = _validate_request(
        request,
        identities["source_step"],
    )
    axes, fixed_conditions = _validate_axes(
        settings["comparison"],
        part_body_id=part_body_id,
        tool_body_id=tool_body_id,
    )
    limits = _strict_object(
        settings["limits"],
        required={"preparation_calls", "solver_calls", "command_timeout_seconds"},
        allowed={"preparation_calls", "solver_calls", "command_timeout_seconds"},
        field="settings.limits",
    )
    preparation_calls = limits["preparation_calls"]
    solver_calls = limits["solver_calls"]
    if type(preparation_calls) is not int or preparation_calls != 1:
        raise _EnvironmentNotReady("settings.limits.preparation_calls must be exactly 1")
    if type(solver_calls) is not int or solver_calls != 2:
        raise _EnvironmentNotReady("settings.limits.solver_calls must be exactly 2")
    command_timeout = _finite(
        limits["command_timeout_seconds"],
        "settings.limits.command_timeout_seconds",
    )
    if command_timeout <= 0:
        raise _EnvironmentNotReady("settings.limits.command_timeout_seconds must be positive")
    _walk_forbidden_keys(raw)
    return _Settings(
        installed_python=identities["installed_python"],
        wheel=identities["wheel"],
        solver=identities["solver"],
        source_step=identities["source_step"],
        preparation_request=identities["preparation_request"],
        youngs_modulus_pa=youngs_modulus_pa,
        instruction=instruction,
        axes=axes,
        fixed_conditions=fixed_conditions,
        preparation_calls=preparation_calls,
        solver_calls=solver_calls,
        command_timeout_seconds=command_timeout,
        request=request,
        wheel_version=wheel_version,
        part_body_id=part_body_id,
        tool_body_id=tool_body_id,
        saved_time_count=saved_time_count,
    )


class _Report:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, Any] = {
            "schema_version": "1",
            "scope": "synthetic_explicit",
            "status": "RUNNING",
            "commands": [],
            "native_attempts": [],
            "runs": [],
            "artifacts": {},
            "unverified_boundaries": [
                "Studio/VW-01 is not exercised by this CLI gate",
                "live LLM/AI-02 is not exercised by this CLI gate",
                "real-model E2E-02 is not exercised by this synthetic gate",
                "BottomFrame E2E-03 is not exercised by this synthetic gate",
                "synthetic numerical evidence is not scientific or native qualification",
            ],
        }
        self.write()

    def write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

    def begin_command(
        self,
        *,
        stage: str,
        argv: Sequence[str],
        cwd: Path,
        native_kind: str | None,
    ) -> tuple[dict[str, Any], int | None]:
        command: dict[str, Any] = {
            "stage": stage,
            "argv": [str(item) for item in argv],
            "cwd": str(cwd),
            "status": "STARTED",
        }
        self.data["commands"].append(command)
        command_index = len(self.data["commands"]) - 1
        native_index: int | None = None
        if native_kind is not None:
            native: dict[str, Any] = {
                "kind": native_kind,
                "command_index": command_index,
                "state": "RECORDED_BEFORE_LAUNCH",
                "argv": [str(item) for item in argv],
                "cwd": str(cwd),
            }
            self.data["native_attempts"].append(native)
            native_index = len(self.data["native_attempts"]) - 1
        self.write()
        return command, native_index

    def finish_command(
        self,
        command: dict[str, Any],
        *,
        returncode: int | None,
        stdout: str = "",
        stderr: str = "",
        reply: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        command.update(
            status="FINISHED" if error is None else "FAILED",
            exit_code=returncode,
            stdout=stdout,
            stderr=stderr,
        )
        if reply is not None:
            command["reply"] = reply
        if error is not None:
            command["error"] = error
        self.write()

    def observe_native(
        self,
        native_index: int | None,
        *,
        observed: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        if native_index is None:
            return
        native = self.data["native_attempts"][native_index]
        if observed is not None:
            native.update(state="OBSERVED", observed=observed)
        elif error is not None:
            native.update(state="OUTCOME_UNKNOWN", error=error)
        self.write()

    def finish(self, status: str, *, error: str | None = None) -> None:
        self.data["status"] = status
        if error is not None:
            self.data["failure"] = error
        self.write()


def _as_text_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _run_subprocess(
    report: _Report,
    *,
    stage: str,
    argv: Sequence[str],
    cwd: Path,
    timeout: float,
    native_kind: str | None,
) -> tuple[int, dict[str, Any]]:
    command, native_index = report.begin_command(
        stage=stage,
        argv=argv,
        cwd=cwd,
        native_kind=native_kind,
    )
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    environment["FEBIO_CAE_STATE_DIR"] = str(
        Path(cast(str, report.data.get("artifacts", {}).get("state_dir", cwd)))
    )
    try:
        completed = subprocess.run(
            [str(item) for item in argv],
            cwd=str(cwd),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            timeout=timeout,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired as error:
        stdout = _as_text_output(error.output)
        stderr = _as_text_output(error.stderr)
        report.finish_command(
            command,
            returncode=None,
            stdout=stdout,
            stderr=stderr,
            error=f"timeout after {timeout} seconds; process cleanup receipt is not asserted",
        )
        report.observe_native(
            native_index,
            error="timeout; native process outcome and cleanup are not asserted",
        )
        raise _AcceptanceFailure(f"{stage} timed out after {timeout} seconds") from error
    except (OSError, ValueError) as error:
        report.finish_command(command, returncode=None, error=str(error))
        report.observe_native(native_index, error=f"launch outcome unknown: {error}")
        raise _EnvironmentNotReady(f"{stage} could not launch: {error}") from error
    stdout = completed.stdout
    stderr = completed.stderr
    reply: dict[str, Any] | None = None
    try:
        parsed = json.loads(
            stdout,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_constant,
        )
        if not isinstance(parsed, dict):
            raise ValueError("CLI output is not a JSON object")
        reply = cast(dict[str, Any], parsed)
    except (UnicodeError, ValueError) as error:
        report.finish_command(
            command,
            returncode=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            error=f"invalid JSON reply: {error}",
        )
        report.observe_native(native_index, error="command returned no usable JSON result")
        raise _AcceptanceFailure(f"{stage} returned invalid JSON: {error}") from error
    report.finish_command(
        command,
        returncode=completed.returncode,
        stdout=stdout,
        stderr=stderr,
        reply=reply,
    )
    return completed.returncode, reply


def _run_cli(
    settings: _Settings,
    report: _Report,
    *,
    stage: str,
    cwd: Path,
    timeout: float,
    args: Sequence[str],
    native_kind: str | None = None,
) -> tuple[int, dict[str, Any], int | None]:
    argv = [str(settings.installed_python.path), "-I", "-m", "febio_cae", *map(str, args)]
    return_code, reply = _run_subprocess(
        report,
        stage=stage,
        argv=argv,
        cwd=cwd,
        timeout=timeout,
        native_kind=native_kind,
    )
    native_index = (
        len(report.data["native_attempts"]) - 1 if native_kind is not None else None
    )
    return return_code, reply, native_index


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise _AcceptanceFailure(message)


def _require_dict(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _AcceptanceFailure(f"{field} is not an object")
    return cast(dict[str, Any], value)


def _require_list(value: object, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise _AcceptanceFailure(f"{field} is not an array")
    return value


def _require_digest(value: object, field: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise _AcceptanceFailure(f"{field} is not a lowercase SHA-256 digest")
    return value


def _require_id(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise _AcceptanceFailure(f"{field} is not a non-empty identifier")
    return value


def _revision_file(case_root: Path, case_id: str, revision_id: str) -> dict[str, Any]:
    path = case_root / "cases" / case_id / "revisions" / revision_id / "revision.json"
    if not path.is_file():
        raise _AcceptanceFailure(f"registered revision file is missing: {path}")
    return _read_json(path, "registered revision", environment=False)


def _material_quantity(spec: dict[str, Any], field: str) -> float:
    material = _require_dict(spec.get("material"), f"{field}.material")
    return _quantity_si(material.get("youngs_modulus"), f"{field}.material.youngs_modulus")


def _spec_without_material(spec: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(spec)
    result.pop("material", None)
    return result


def _read_db_payload(case_root: Path, sql: str, value: str, field: str) -> dict[str, Any]:
    database = case_root / "registry.sqlite3"
    try:
        with sqlite3.connect(database) as connection:
            row = connection.execute(sql, (value,)).fetchone()
    except sqlite3.Error as error:
        raise _AcceptanceFailure(f"cannot read {field} from registered storage: {error}") from error
    if row is None:
        raise _AcceptanceFailure(f"registered storage has no {field} for {value!r}")
    payload = row[0]
    if isinstance(payload, memoryview):
        payload = payload.tobytes()
    if not isinstance(payload, bytes):
        raise _AcceptanceFailure(f"registered {field} payload is not bytes")
    try:
        result = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, ValueError) as error:
        raise _AcceptanceFailure(f"registered {field} payload is invalid JSON: {error}") from error
    return _require_dict(result, f"registered {field}")


def _record_preparation_process(
    report: _Report,
    case_root: Path,
    preparation: dict[str, Any],
    native_index: int | None,
) -> None:
    preparation_id = _require_id(preparation.get("preparation_id"), "preparation_id")
    prepared_path = case_root / "preparation" / preparation_id / "prepared.json"
    prepared = _read_json(prepared_path, "prepared producer receipt", environment=False)
    producer = _require_dict(prepared.get("producer"), "prepared producer")
    process = _require_dict(producer.get("process"), "prepared producer process")
    argv = _require_list(process.get("argv"), "prepared producer process.argv")
    _expect(bool(argv), "prepared producer process.argv is empty")
    _expect(
        len(argv) >= 4
        and argv[1] == "-I"
        and argv[2] == "-c"
        and isinstance(argv[3], str)
        and "febio_cae.adapters.geometry.preparation" in argv[3]
        and "_main" in argv[3],
        "preparation child did not use the installed isolated producer entry point",
    )
    _expect(
        _same_path(Path(str(argv[0])), Path(report.data["environment"]["installed_python"])),
        "preparation child did not use the configured installed Python",
    )
    _expect(process.get("exit_code") == 0, "preparation child did not report exit code zero")
    work = case_root / "preparation" / preparation_id / "work"
    _expect(work.is_dir(), "preparation child work directory is missing")
    report.observe_native(
        native_index,
        observed={
            "argv": [str(item) for item in argv],
            "cwd": str(work),
            "exit_code": process.get("exit_code"),
            "pid": process.get("pid"),
            "creation_time": process.get("creation_time"),
        },
    )


def _locate_run(
    case_root: Path,
    case_id: str,
    *,
    attempt_id: str,
    manifest_id: str,
) -> tuple[str, Path]:
    runs_root = case_root / "cases" / case_id / "runs"
    candidates: list[tuple[str, Path]] = []
    if runs_root.is_dir():
        for run_root in sorted(runs_root.iterdir()):
            attempt_root = run_root / "attempts" / attempt_id
            result_path = attempt_root / "output" / "results.xplt"
            if attempt_root.is_dir() and result_path.is_file():
                candidates.append((run_root.name, attempt_root))
    _expect(
        len(candidates) == 1,
        "expected one persisted result for attempt "
        f"{attempt_id!r} and manifest {manifest_id!r}, "
        f"found {len(candidates)}",
    )
    return candidates[0]


def _record_solver_process(
    report: _Report,
    case_root: Path,
    *,
    case_id: str,
    run_id: str,
    attempt_id: str,
    solver: _FileIdentity,
    native_index: int | None,
) -> dict[str, Any]:
    attempt = _read_db_payload(
        case_root,
        "SELECT payload FROM owners WHERE attempt_id=?",
        attempt_id,
        "attempt",
    )
    _expect(attempt.get("case_id") == case_id, "registered attempt case binding differs")
    _expect(attempt.get("run_id") == run_id, "registered attempt run binding differs")
    _expect(attempt.get("attempt_id") == attempt_id, "registered attempt ID differs")
    _expect(attempt.get("state") == "SUCCEEDED", "registered attempt is not SUCCEEDED")
    process = _require_dict(attempt.get("process"), "registered attempt process")
    argv = _require_list(process.get("argv"), "registered attempt process.argv")
    _expect(bool(argv), "registered solver process.argv is empty")
    _expect(_same_path(Path(str(argv[0])), solver.path), "registered solver executable differs")
    _expect(
        _require_digest(process.get("executable_digest"), "registered solver executable digest")
        == solver.digest,
        "registered solver executable digest differs",
    )
    process_cwd = Path(_text(process.get("cwd"), "registered solver process.cwd"))
    _expect(process_cwd.is_dir(), "registered solver process cwd is missing")
    _expect(
        _under(process_cwd, case_root / "native"),
        "registered solver cwd escaped native storage",
    )
    owner_generation = attempt.get("owner_generation")
    _expect(
        type(owner_generation) is int and owner_generation >= 0,
        "registered attempt owner generation is invalid",
    )
    _expect(
        _same_path(
            process_cwd,
            case_root
            / "native"
            / case_id
            / run_id
            / attempt_id
            / str(owner_generation),
        ),
        "registered solver process cwd is not the exact owned attempt directory",
    )
    process_executable = Path(
        _text(process.get("executable"), "registered solver process.executable")
    )
    _expect(
        _same_path(process_executable, solver.path),
        "registered solver process executable differs",
    )
    observed = {
        "argv": [str(item) for item in argv],
        "cwd": str(process_cwd),
        "executable": process.get("executable"),
        "executable_digest": process.get("executable_digest"),
        "thread_count": process.get("thread_count"),
        "start_marker": process.get("start_marker"),
        "state": attempt.get("state"),
    }
    report.observe_native(native_index, observed=observed)
    return attempt


def _validate_numeric_data(
    case_root: Path,
    *,
    manifest: dict[str, Any],
    revision: dict[str, Any],
    run_id: str,
    attempt_id: str,
) -> None:
    read_result = _require_dict(manifest.get("read_result"), "manifest.read_result")
    _expect(read_result.get("status") == "VALIDATED", "manifest read result is not VALIDATED")
    observations = [
        _require_dict(item, f"manifest.read_result.observations[{index}]")
        for index, item in enumerate(
            _require_list(read_result.get("observations"), "manifest.read_result.observations")
        )
    ]
    spec = _require_dict(revision.get("spec"), "revision.spec")
    outputs = _require_dict(spec.get("outputs"), "revision.spec.outputs")
    requests = _require_list(outputs.get("requests"), "revision.spec.outputs.requests")
    requests_by_id = {
        _require_id(_require_dict(item, "output request").get("request_id"), "request_id"):
        _require_dict(item, "output request")
        for item in requests
    }
    request_ids = {
        _require_id(_require_dict(item, "output request").get("request_id"), "request_id")
        for item in requests
    }
    _expect(
        {item.get("output_id") for item in observations} == request_ids,
        "manifest output inventory differs",
    )
    _expect(
        len(observations) == len(requests),
        "manifest contains duplicate or missing observations",
    )
    files = _require_list(manifest.get("files"), "manifest.files")
    result_entries = [
        _require_dict(entry, "manifest file")
        for entry in files
        if _require_dict(entry, "manifest file").get("logical_path") == "output/results.xplt"
    ]
    _expect(len(result_entries) == 1, "manifest lacks exactly one results.xplt entry")
    result_entry = result_entries[0]
    result_path = (
        case_root
        / "cases"
        / revision["case_id"]
        / "runs"
        / run_id
        / "attempts"
        / attempt_id
        / "output"
        / "results.xplt"
    )
    _expect(result_path.is_file(), "persisted XPLT result is missing")
    try:
        result_bytes = result_path.read_bytes()
    except OSError as error:
        raise _AcceptanceFailure(f"cannot read persisted XPLT result: {error}") from error
    _expect(
        len(result_bytes) == result_entry.get("size_bytes"),
        "persisted XPLT size differs from the manifest",
    )
    _expect(
        hashlib.sha256(result_bytes).hexdigest() == result_entry.get("digest"),
        "persisted XPLT digest differs from the manifest",
    )
    saved_times = _require_list(
        outputs.get("saved_times"), "revision.spec.outputs.saved_times"
    )
    motion = _require_dict(spec.get("motion"), "revision.spec.motion")
    samples = _require_list(motion.get("samples"), "revision.spec.motion.samples")
    _expect(bool(samples), "revision motion has no terminal sample")
    endpoint = _quantity_si(
        _require_dict(samples[-1], "revision.spec.motion.samples[-1]").get("time"),
        "revision.spec.motion.samples[-1].time",
    )
    expected_times = tuple(
        sorted(
            {
                _quantity_si(value, f"revision.spec.outputs.saved_times[{index}]")
                for index, value in enumerate(saved_times)
            }
            | {endpoint}
        )
    )
    expected_state_count = len(expected_times)
    for observation_value in observations:
        observation = _require_dict(observation_value, "output observation")
        output_id = _require_id(observation.get("output_id"), "output observation.output_id")
        request = requests_by_id[output_id]
        _expect(
            (
                observation.get("location"),
                observation.get("measure_id"),
                observation.get("frame"),
                observation.get("unit"),
            )
            == (
                request.get("location"),
                request.get("measure_id"),
                request.get("frame"),
                request.get("display_unit"),
            ),
            f"observation {output_id!r} differs from its declared output request",
        )
        data_ref = _require_dict(observation.get("data_ref"), "output observation.data_ref")
        data_id = _require_id(data_ref.get("data_id"), "output observation.data_ref.data_id")
        _expect(data_ref.get("attempt_id") == attempt_id, "numeric data escaped its attempt")
        _expect(
            data_ref.get("bundle_digest") == manifest.get("bundle_digest"),
            "numeric data escaped its execution bundle",
        )
        numeric = _read_db_payload(
            case_root,
            "SELECT payload FROM numeric_data WHERE data_id=?",
            data_id,
            "numeric result",
        )
        numeric_reference = _require_dict(numeric.get("reference"), "numeric.reference")
        expected_reference = {
            "schema_version": "1",
            "data_id": data_ref.get("data_id"),
            "codec_id": data_ref.get("codec_id"),
            "logical_path": data_ref.get("logical_path"),
            "bundle_digest": data_ref.get("bundle_digest"),
            "attempt_id": data_ref.get("attempt_id"),
        }
        _expect(
            numeric_reference == expected_reference,
            "numeric reference differs from manifest reference",
        )
        _expect(
            numeric.get("content_digest") == data_ref.get("content_digest"),
            "numeric content digest differs from the manifest reference",
        )
        mapping = _require_dict(numeric.get("mapping"), "numeric.mapping")
        _expect(
            (
                mapping.get("canonical_id"),
                mapping.get("location"),
                mapping.get("unit"),
                mapping.get("frame"),
                mapping.get("measure_id"),
                mapping.get("value_type"),
            )
            == (
                request.get("quantity_id"),
                request.get("location"),
                request.get("display_unit"),
                request.get("frame"),
                request.get("measure_id"),
                "VEC3F",
            ),
            f"numeric mapping for {output_id!r} differs from its output request",
        )
        _expect(
            numeric.get("axis_id") == "state_time"
            and numeric.get("axis_unit") == "s",
            "numeric result is not an explicit state-time history",
        )
        axis_values = _require_list(numeric.get("axis_values"), "numeric.axis_values")
        values = _require_list(numeric.get("values"), "numeric.values")
        entity_ids = _require_list(numeric.get("entity_ids"), "numeric.entity_ids")
        component_ids = _require_list(numeric.get("component_ids"), "numeric.component_ids")
        _expect(
            observation.get("state_count") == len(axis_values),
            "output state_count differs from its numeric history",
        )
        _expect(
            len(axis_values) >= max(2, expected_state_count)
            and len(axis_values) == len(values),
            "numeric history lacks complete states",
        )
        axis_floats = [
            _finite(value, f"numeric.axis_values[{index}]")
            for index, value in enumerate(axis_values)
        ]
        previous: float | None = None
        for current in axis_floats:
            if previous is not None:
                _expect(current > previous, "numeric state times are not strictly increasing")
            previous = current
        for target in expected_times:
            matches = [
                index
                for index, current in enumerate(axis_floats)
                if current == target or current == _binary32(target, "required state time")
            ]
            _expect(
                len(matches) == 1,
                f"numeric history does not contain exactly one required state at {target!r}",
            )
        _expect(
            bool(entity_ids) and bool(component_ids),
            "numeric result has no entity/component inventory",
        )
        _expect(
            component_ids == ["x", "y", "z"],
            "numeric result does not use the required vector component order",
        )
        width = len(entity_ids) * len(component_ids)
        for index, row_value in enumerate(values):
            row = _require_list(row_value, f"numeric.values[{index}]")
            _expect(len(row) == width, "numeric result row width differs from its mapping")
            for component_index, component in enumerate(row):
                _finite(component, f"numeric.values[{index}][{component_index}]")
        content = copy.deepcopy(numeric)
        content.pop("content_digest", None)
        expected_digest = hashlib.sha256(_json_bytes(content)).hexdigest()
        _expect(
            _require_digest(data_ref.get("content_digest"), "numeric content digest")
            == expected_digest,
            "numeric content digest does not match the persisted payload",
        )


def _validate_run(
    report: _Report,
    *,
    response: dict[str, Any],
    code: int,
    case_root: Path,
    case_id: str,
    revision: dict[str, Any],
    run_label: str,
    native_index: int | None,
    solver: _FileIdentity,
) -> dict[str, Any]:
    status = response.get("status")
    expected_code = 0 if status == "NEEDS_PREVIEW" else 6 if status == "NEEDS_REVIEW" else None
    _expect(
        expected_code is not None and code == expected_code,
        f"{run_label} returned {status!r} with exit {code}",
    )
    _expect(
        response.get("run_status") == "SUCCEEDED",
        f"{run_label} did not report SUCCEEDED",
    )
    _expect(response.get("case_id") == case_id, f"{run_label} case binding differs")
    _expect(
        response.get("revision_id") == revision.get("revision_id"),
        f"{run_label} revision binding differs",
    )
    manifest = _require_dict(response.get("manifest"), f"{run_label}.manifest")
    manifest_id = _require_id(manifest.get("manifest_id"), f"{run_label}.manifest_id")
    attempt_id = _require_id(manifest.get("attempt_id"), f"{run_label}.attempt_id")
    _require_digest(manifest.get("bundle_digest"), f"{run_label}.manifest.bundle_digest")
    _expect(
        manifest.get("attempt_id") == attempt_id,
        f"{run_label}.manifest attempt binding differs",
    )
    persisted_manifest = _read_db_payload(
        case_root,
        "SELECT payload FROM manifests WHERE manifest_id=?",
        manifest_id,
        f"{run_label} manifest",
    )
    _expect(
        persisted_manifest == manifest,
        f"{run_label} manifest differs from registered storage",
    )
    run_id, attempt_root = _locate_run(
        case_root,
        case_id,
        attempt_id=attempt_id,
        manifest_id=manifest_id,
    )
    _validate_numeric_data(
        case_root,
        manifest=manifest,
        revision=revision,
        run_id=run_id,
        attempt_id=attempt_id,
    )
    attempt = _record_solver_process(
        report,
        case_root,
        case_id=case_id,
        run_id=run_id,
        attempt_id=attempt_id,
        solver=solver,
        native_index=native_index,
    )
    required_quality = _require_dict(
        response.get("required_quality"), f"{run_label}.required_quality"
    )
    numerical = _require_list(
        required_quality.get("numerical"), f"{run_label}.required_quality.numerical"
    )
    rows = [
        _require_dict(row, f"{run_label}.required_quality.numerical row")
        for row in numerical
    ]
    _expect(
        len(rows) == len(_REQUIRED_NUMERICAL),
        f"{run_label} required numerical inventory contains duplicates",
    )
    row_ids = {
        _require_id(row.get("criterion_id"), "required quality criterion_id") for row in rows
    }
    _expect(
        row_ids == _REQUIRED_NUMERICAL,
        f"{run_label} required numerical inventory is incomplete or renamed",
    )
    quality_statuses = {
        _require_id(row.get("criterion_id"), "criterion_id"): _text(
            row.get("status"), "criterion status"
        )
        for row in rows
    }
    bindings = _require_dict(
        required_quality.get("bindings"), f"{run_label}.required_quality.bindings"
    )
    _expect(
        bindings.get("manifest_id") == manifest_id,
        f"{run_label} quality manifest binding differs",
    )
    _expect(
        bindings.get("attempt_id") == attempt_id,
        f"{run_label} quality attempt binding differs",
    )
    _expect(
        quality_statuses["execution_result_completeness"] == "PASS",
        f"{run_label} execution/result completeness is not PASS",
    )
    report.data["runs"].append(
        {
            "label": run_label,
            "run_id": run_id,
            "attempt_id": attempt_id,
            "attempt_root": str(attempt_root),
            "manifest_id": manifest_id,
            "revision_id": revision.get("revision_id"),
            "status": status,
            "exit_code": code,
            "run_status": response.get("run_status"),
            "quality_status": response.get("quality_status"),
            "required_numerical_statuses": quality_statuses,
            "persisted_attempt_state": attempt.get("state"),
        }
    )
    report.write()
    return {
        "run_id": run_id,
        "attempt_id": attempt_id,
        "manifest_id": manifest_id,
        "quality_status": response.get("quality_status"),
        "numerical_statuses": quality_statuses,
        "manifest": manifest,
    }


def _mark_native_observed_from_command(
    report: _Report,
    native_index: int | None,
    *,
    response: dict[str, Any],
) -> None:
    if native_index is None:
        return
    manifest = response.get("manifest")
    if isinstance(manifest, dict):
        report.observe_native(
            native_index,
            observed={
                "manifest_id": manifest.get("manifest_id"),
                "attempt_id": manifest.get("attempt_id"),
                "run_status": response.get("run_status"),
            },
        )


def _comparison_payload(
    comparison_id: str,
    *,
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    axes: tuple[dict[str, Any], ...],
    fixed_conditions: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "schema_version": "1",
        "comparison_id": comparison_id,
        "baseline_manifest_id": baseline["manifest_id"],
        "candidate_manifest_id": candidate["manifest_id"],
        "intended_changes": list(_INTENDED_CHANGES),
        "fixed_conditions": list(fixed_conditions),
        "axes": [copy.deepcopy(axis) for axis in axes],
    }


def _validate_comparison(
    response: dict[str, Any],
    *,
    code: int,
    case_id: str,
    comparison_id: str,
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    expected_request: dict[str, Any],
) -> None:
    _expect(
        code == 0 and response.get("status") == "COMPARED",
        "public comparison did not complete",
    )
    _expect(response.get("case_id") == case_id, "comparison case binding differs")
    _expect(response.get("comparison_id") == comparison_id, "comparison ID differs")
    comparison = _require_dict(response.get("comparison"), "comparison")
    request = _require_dict(comparison.get("request"), "comparison.request")
    _expect(
        request == expected_request,
        "comparison request differs from the configured axes/lineage",
    )
    _expect(
        request.get("baseline_manifest_id") == baseline["manifest_id"],
        "comparison baseline manifest differs",
    )
    _expect(
        request.get("candidate_manifest_id") == candidate["manifest_id"],
        "comparison candidate manifest differs",
    )
    common_axis = _require_dict(comparison.get("common_axis"), "comparison.common_axis")
    values = _require_list(common_axis.get("values"), "comparison.common_axis.values")
    _expect(len(values) >= 2, "comparison common axis has fewer than two coordinates")
    for index, value in enumerate(values):
        _finite(value, f"comparison.common_axis.values[{index}]")
    series = [
        _require_dict(item, f"comparison.series[{index}]")
        for index, item in enumerate(
            _require_list(comparison.get("series"), "comparison.series")
        )
    ]
    _expect(
        len(series) == len(_COMPARISON_AXES)
        and {item.get("axis_id") for item in series} == set(_COMPARISON_AXES),
        "comparison series inventory differs",
    )
    for index, item in enumerate(series):
        for field in ("baseline", "candidate", "difference"):
            curve = _require_list(item.get(field), f"comparison.series[{index}].{field}")
            _expect(
                len(curve) == len(values) >= 2,
                f"comparison series {field} is not aligned to the common axis",
            )
            for curve_index, value in enumerate(curve):
                _finite(value, f"comparison.series[{index}].{field}[{curve_index}]")
    sources = _require_list(comparison.get("sources"), "comparison.sources")
    _expect(len(sources) == 2, "comparison did not retain both actual source identities")
    source_manifest_ids = {
        _require_id(
            _require_dict(item, "comparison source").get("manifest_id"),
            "source manifest_id",
        )
        for item in sources
    }
    _expect(
        source_manifest_ids == {baseline["manifest_id"], candidate["manifest_id"]},
        "comparison source manifests differ",
    )


@pytest.mark.e2e
def test_installed_synthetic_cli_flow(tmp_path: Path) -> None:
    """Run the finite installed synthetic flow and keep every boundary honest."""

    report = _Report(tmp_path / "installed-synthetic-attempt-report.json")
    try:
        settings = _load_settings()
        cwd = tmp_path / "installed-cwd"
        cwd.mkdir()
        report.data["artifacts"] = {
            "cwd": str(cwd),
            "state_dir": str(tmp_path / "state"),
            "case_root": str(tmp_path / "case-root"),
            "generated_inputs": str(tmp_path / "generated-inputs"),
        }
        report.write()
        _validate_runtime(settings, report, cwd)
        state_dir = tmp_path / "state"
        case_root = tmp_path / "case-root"
        generated_inputs = tmp_path / "generated-inputs"
        generated_inputs.mkdir()
        timeout = settings.command_timeout_seconds
        native_counts = {"preparation": 0, "solver": 0}

        def cli(
            stage: str,
            args: Sequence[str],
            *,
            native_kind: str | None = None,
        ) -> tuple[int, dict[str, Any], int | None]:
            if native_kind is not None:
                native_counts[native_kind] += 1
                limit = (
                    settings.preparation_calls
                    if native_kind == "preparation"
                    else settings.solver_calls
                )
                _expect(
                    native_counts[native_kind] <= limit,
                    f"{native_kind} native-attempt budget exceeded before {stage}",
                )
            return _run_cli(
                settings,
                report,
                stage=stage,
                cwd=cwd,
                timeout=timeout,
                args=["case", "--state-dir", str(state_dir), *args],
                native_kind=native_kind,
            )

        create_code, created, _ = cli(
            "create",
            [
                "create",
                "--case-root",
                str(case_root),
                "--cad",
                str(settings.source_step.path),
                "--json",
            ],
        )
        _expect(create_code == 0 and created.get("status") == "REGISTERED", "case creation failed")
        case_id = _require_id(created.get("case_id"), "created.case_id")
        source_asset = _require_dict(created.get("source_asset"), "created.source_asset")
        _expect(
            source_asset.get("content_digest") == settings.source_step.digest,
            "created source digest differs",
        )
        draft = _require_dict(created.get("draft"), "created.draft")
        _expect(draft.get("generation") == 0, "created draft generation is not zero")
        _expect(
            created.get("revision_id") is None and created.get("run_id") is None,
            "create returned fabricated lifecycle IDs",
        )
        report.data["artifacts"]["case_id"] = case_id
        report.write()

        prepared_code, prepared, preparation_native_index = cli(
            "prepare-planar",
            [
                "prepare-planar",
                case_id,
                "--file",
                str(settings.preparation_request.path),
                "--expected-generation",
                "0",
                "--json",
            ],
            native_kind="preparation",
        )
        _expect(
            prepared_code == 0 and prepared.get("status") == "PREPARED",
            "planar preparation failed",
        )
        parent_revision_id = _require_id(prepared.get("revision_id"), "prepared.revision_id")
        preparation_id = _require_id(prepared.get("preparation_id"), "prepared.preparation_id")
        _expect(prepared.get("generation") == 1, "prepared generation is not one")
        _record_preparation_process(report, case_root, prepared, preparation_native_index)
        parent_revision = _revision_file(case_root, case_id, parent_revision_id)
        _expect(parent_revision.get("case_id") == case_id, "prepared revision case binding differs")
        _expect(
            parent_revision.get("revision_id") == parent_revision_id,
            "prepared revision ID differs",
        )
        _expect(
            parent_revision.get("parent_revision_id") is None,
            "prepared origin unexpectedly has a parent",
        )
        parent_spec_digest = _require_digest(
            parent_revision.get("spec_digest"), "parent spec digest"
        )
        report.data["artifacts"]["preparation_id"] = preparation_id
        report.data["artifacts"]["revisions"] = [
            {
                "label": "baseline",
                "revision_id": parent_revision_id,
                "spec_digest": parent_spec_digest,
                "generation": prepared.get("generation"),
            }
        ]
        report.write()

        validate_code, validated, _ = cli("validate-prepared", ["validate", case_id, "--json"])
        _expect(
            validate_code == 0 and validated.get("status") == "VALIDATED",
            "prepared case did not validate",
        )
        validated_draft = _require_dict(validated.get("draft"), "validated.draft")
        _expect(
            validated_draft.get("generation") == prepared.get("generation"),
            "validation changed prepared generation",
        )

        baseline_preflight_code, baseline_preflight, _ = cli(
            "baseline-preflight",
            [
                "run-demo",
                case_id,
                "--revision-id",
                parent_revision_id,
                "--solver",
                str(settings.solver.path),
                "--preflight",
                "--json",
            ],
        )
        _expect(
            baseline_preflight_code == 0
            and baseline_preflight.get("status") == "PREFLIGHT_PASSED"
            and baseline_preflight.get("native_starts") == 0,
            "baseline preflight failed or launched native software",
        )

        baseline_code, baseline_response, baseline_native_index = cli(
            "baseline-real-run",
            [
                "run-demo",
                case_id,
                "--revision-id",
                parent_revision_id,
                "--solver",
                str(settings.solver.path),
                "--json",
            ],
            native_kind="solver",
        )
        _mark_native_observed_from_command(
            report, baseline_native_index, response=baseline_response
        )
        baseline = _validate_run(
            report,
            response=baseline_response,
            code=baseline_code,
            case_root=case_root,
            case_id=case_id,
            revision=parent_revision,
            run_label="baseline",
            native_index=baseline_native_index,
            solver=settings.solver,
        )

        registration_request = {
            "schema_version": "1",
            "values": {"schema_version": "1", **{field: None for field in _CASE_FIELDS}},
            "evidence": [],
            "source_declarations": [
                {
                    "source_kind": "user_instruction",
                    "reference": "synthetic-e-only-instruction",
                    "target_field": "material.youngs_modulus",
                    "content": settings.instruction,
                    "content_digest": hashlib.sha256(
                        settings.instruction.encode("utf-8")
                    ).hexdigest(),
                    "media_type": "text/plain",
                }
            ],
        }
        registration_path = generated_inputs / "e-only-instruction.json"
        registration_path.write_bytes(_json_bytes(registration_request))
        registration_code, registration, _ = cli(
            "register-e-only-evidence",
            [
                "spec",
                case_id,
                "--file",
                str(registration_path),
                "--expected-generation",
                str(validated_draft["generation"]),
                "--json",
            ],
        )
        _expect(
            registration_code == 0 and registration.get("status") == "UPDATED",
            "E-only evidence registration failed",
        )
        registration_draft = _require_dict(registration.get("draft"), "registration.draft")
        registration_evidence = _require_list(
            registration_draft.get("evidence"), "registration.draft.evidence"
        )
        matching_evidence = [
            _validate_evidence(item, "registration evidence")
            for item in registration_evidence
            if isinstance(item, dict)
            and item.get("target_field") == "material.youngs_modulus"
            and item.get("reference") == "synthetic-e-only-instruction"
        ]
        _expect(
            len(matching_evidence) == 1,
            "registration did not return one actual E-only evidence reference",
        )
        e_evidence = matching_evidence[0]
        _expect(
            e_evidence["content_digest"]
            == hashlib.sha256(settings.instruction.encode("utf-8")).hexdigest(),
            "registration evidence digest differs from the submitted instruction",
        )

        parent_spec = _require_dict(parent_revision.get("spec"), "parent_revision.spec")
        baseline_modulus = _material_quantity(parent_spec, "parent_revision.spec")
        _expect(
            not math.isclose(
                baseline_modulus,
                settings.youngs_modulus_pa,
                rel_tol=0.0,
                abs_tol=0.0,
            ),
            "configured E-only edit does not change Young's modulus",
        )
        child_material = copy.deepcopy(
            _require_dict(parent_spec.get("material"), "parent material")
        )
        child_material["youngs_modulus"] = {"value": settings.youngs_modulus_pa, "unit": "Pa"}
        child_material["youngs_modulus_evidence"] = copy.deepcopy(e_evidence)
        patch_request = {
            "schema_version": "1",
            "parent_revision_id": parent_revision_id,
            "parent_spec_digest": parent_spec_digest,
            "edits": [
                {
                    "schema_version": "1",
                    "field": "material",
                    "present": True,
                    "value": child_material,
                }
            ],
            "evidence": [copy.deepcopy(e_evidence)],
        }
        patch_path = generated_inputs / "e-only-patch.json"
        patch_path.write_bytes(_json_bytes(patch_request))
        patch_code, patched, _ = cli(
            "apply-e-only-patch",
            [
                "patch",
                case_id,
                "--file",
                str(patch_path),
                "--expected-generation",
                str(registration_draft["generation"]),
                "--json",
            ],
        )
        _expect(
            patch_code == 0 and patched.get("status") == "UPDATED",
            "E-only patch was not accepted",
        )
        patched_draft = _require_dict(patched.get("draft"), "patched.draft")
        _expect(
            patched_draft.get("generation") == registration_draft["generation"] + 1,
            "patch generation is not monotonic",
        )

        child_validate_code, child_validated, _ = cli(
            "validate-child", ["validate", case_id, "--json"]
        )
        _expect(
            child_validate_code == 0 and child_validated.get("status") == "VALIDATED",
            "E-only child did not validate",
        )
        child_freeze_code, child_frozen, _ = cli(
            "freeze-child", ["freeze", case_id, "--json"]
        )
        _expect(
            child_freeze_code == 0 and child_frozen.get("status") == "FROZEN",
            "E-only child was not frozen",
        )
        child_revision = _require_dict(child_frozen.get("revision"), "child_frozen.revision")
        child_revision_id = _require_id(child_revision.get("revision_id"), "child revision ID")
        _expect(
            child_frozen.get("revision_id") == child_revision_id,
            "child freeze revision ID differs",
        )
        _expect(
            _revision_file(case_root, case_id, child_revision_id) == child_revision,
            "child freeze reply differs from the persisted revision",
        )
        _expect(
            child_revision.get("parent_revision_id") == parent_revision_id,
            "child parent revision link differs",
        )
        _expect(
            child_revision.get("parent_spec_digest") == parent_spec_digest,
            "child parent spec digest differs",
        )
        child_spec = _require_dict(child_revision.get("spec"), "child_revision.spec")
        _expect(
            _spec_without_material(child_spec) == _spec_without_material(parent_spec),
            "E-only patch changed non-material physics",
        )
        child_material_actual = _require_dict(child_spec.get("material"), "child material")
        _expect(
            math.isclose(
                _quantity_si(
                    child_material_actual.get("youngs_modulus"),
                    "child Young's modulus",
                ),
                settings.youngs_modulus_pa,
                rel_tol=0.0,
                abs_tol=0.0,
            ),
            "child Young's modulus differs from the configured E-only edit",
        )
        for key in (
            "schema_version",
            "kind",
            "poisson_ratio",
            "model_evidence",
            "poisson_ratio_evidence",
            "applicability",
        ):
            _expect(
                child_material_actual.get(key) == parent_spec["material"].get(key),
                f"E-only patch changed material.{key}",
            )
        _expect(
            child_material_actual.get("youngs_modulus_evidence") == e_evidence,
            "child E evidence is not the registered evidence",
        )
        child_spec_digest = _require_digest(child_revision.get("spec_digest"), "child spec digest")
        _expect(child_spec_digest != parent_spec_digest, "E-only child spec digest did not change")
        report.data["artifacts"]["revisions"].append(
            {
                "label": "candidate",
                "revision_id": child_revision_id,
                "spec_digest": child_spec_digest,
                "parent_revision_id": parent_revision_id,
                "parent_spec_digest": parent_spec_digest,
            }
        )
        report.write()

        candidate_preflight_code, candidate_preflight, _ = cli(
            "candidate-preflight",
            [
                "run-demo",
                case_id,
                "--revision-id",
                child_revision_id,
                "--solver",
                str(settings.solver.path),
                "--preflight",
                "--json",
            ],
        )
        _expect(
            candidate_preflight_code == 0
            and candidate_preflight.get("status") == "PREFLIGHT_PASSED"
            and candidate_preflight.get("native_starts") == 0,
            "candidate preflight failed or launched native software",
        )
        candidate_code, candidate_response, candidate_native_index = cli(
            "candidate-real-run",
            [
                "run-demo",
                case_id,
                "--revision-id",
                child_revision_id,
                "--solver",
                str(settings.solver.path),
                "--json",
            ],
            native_kind="solver",
        )
        _mark_native_observed_from_command(
            report, candidate_native_index, response=candidate_response
        )
        candidate = _validate_run(
            report,
            response=candidate_response,
            code=candidate_code,
            case_root=case_root,
            case_id=case_id,
            revision=child_revision,
            run_label="candidate",
            native_index=candidate_native_index,
            solver=settings.solver,
        )
        _expect(
            native_counts == {"preparation": 1, "solver": 2},
            "native attempt count differs from the finite configured budget",
        )
        _expect(
            baseline["run_id"] != candidate["run_id"],
            "baseline and candidate run IDs are not distinct",
        )
        _expect(
            baseline["attempt_id"] != candidate["attempt_id"],
            "baseline and candidate attempt IDs are not distinct",
        )
        _expect(
            baseline["manifest_id"] != candidate["manifest_id"],
            "baseline and candidate manifest IDs are not distinct",
        )

        comparison_id = f"synthetic-comparison-{uuid.uuid4().hex}"
        comparison_request = _comparison_payload(
            comparison_id,
            baseline=baseline,
            candidate=candidate,
            axes=settings.axes,
            fixed_conditions=settings.fixed_conditions,
        )
        comparison_path = generated_inputs / "comparison.json"
        comparison_path.write_bytes(_json_bytes(comparison_request))
        compare_code, comparison_response, _ = _run_cli(
            settings,
            report,
            stage="public-compare",
            cwd=cwd,
            timeout=timeout,
            args=[
                "compare",
                baseline["run_id"],
                candidate["run_id"],
                "--case-id",
                case_id,
                "--state-dir",
                str(state_dir),
                "--spec",
                str(comparison_path),
                "--json",
            ],
        )
        _validate_comparison(
            comparison_response,
            code=compare_code,
            case_id=case_id,
            comparison_id=comparison_id,
            baseline=baseline,
            candidate=candidate,
            expected_request=comparison_request,
        )
        comparison_record_path = Path(
            _text(comparison_response.get("record_path"), "comparison.record_path")
        )
        expected_record_path = (
            case_root
            / "cases"
            / case_id
            / "comparisons"
            / comparison_id
            / "comparison.json"
        )
        _expect(
            _same_path(comparison_record_path, expected_record_path),
            "comparison record escaped the case-owned path",
        )
        try:
            comparison_record_bytes = comparison_record_path.read_bytes()
        except OSError as error:
            raise _AcceptanceFailure(f"cannot read persisted comparison: {error}") from error
        _expect(
            hashlib.sha256(comparison_record_bytes).hexdigest()
            == _require_digest(
                comparison_response.get("record_digest"), "comparison.record_digest"
            ),
            "persisted comparison digest differs from the public reply",
        )
        persisted_comparison = _read_json(
            comparison_record_path, "persisted comparison", environment=False
        )
        public_comparison = copy.deepcopy(comparison_response)
        public_comparison.pop("record_path", None)
        public_comparison.pop("record_digest", None)
        _expect(
            persisted_comparison == public_comparison,
            "persisted comparison differs from the public reply",
        )
        report.data["artifacts"]["comparison_id"] = comparison_id
        report.data["artifacts"]["comparison_record_path"] = str(comparison_record_path)
        numerical_rows = {
            label: run["numerical_statuses"]
            for label, run in (("baseline", baseline), ("candidate", candidate))
        }
        bad_rows = {
            f"{label}.{criterion}": status
            for label, statuses in numerical_rows.items()
            for criterion, status in statuses.items()
            if status not in _ALLOWED_NUMERICAL
        }
        quality_statuses = {
            label: run["quality_status"]
            for label, run in (("baseline", baseline), ("candidate", candidate))
        }
        final_status = (
            "PASS"
            if not bad_rows and all(status == "PASS" for status in quality_statuses.values())
            else "UNVERIFIED"
        )
        report.data["final_numerical_status"] = final_status
        report.data["final_quality_statuses"] = quality_statuses
        report.data["quality_failures"] = bad_rows
        report.write()
        _expect(
            final_status == "PASS",
            "mandatory numerical quality remains unverified or failed: "
            f"{bad_rows or quality_statuses}",
        )
        report.finish("NUMERICAL_GATE_PASSED_NOT_OVERALL")
    except _EnvironmentNotReady as error:
        report.finish("ENVIRONMENT_NOT_READY", error=str(error))
        pytest.fail(f"ENVIRONMENT_NOT_READY: {error}; report={report.path}")
    except _AcceptanceFailure as error:
        report.finish("FAILED", error=str(error))
        pytest.fail(f"{error}; report={report.path}")
    except BaseException as error:
        report.finish("INTERRUPTED", error=f"{type(error).__name__}: {error}")
        raise
