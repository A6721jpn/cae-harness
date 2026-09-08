"""Explicit planar material-edit comparison subset, not a scientific ranking.

ComparisonAxis.unit/interval describe positive imposed tool compression in m.
The two supported axis IDs select World-z force (N, identity on the whole tool)
and displacement (m, peak absolute z over all part nodes at each saved state).
Aggregate first, then linearly interpolate on the union of observed compression
coordinates in the requested common interval. Never extrapolate or subtract
node IDs across runs. FIXED_CONDITIONS must be declared in full.
"""

from __future__ import annotations

import hashlib
import math
from bisect import bisect_left
from dataclasses import replace
from itertools import pairwise
from typing import TYPE_CHECKING

from febio_cae.adapters.febio import QualityAdapter
from febio_cae.domain import (
    ComparisonSpec,
    IsotropicLinearElastic,
    NumericResultData,
    WholeBodyRule,
)
from febio_cae.domain.canonical import canonical_bytes
from febio_cae.domain.codec import encode_record
from febio_cae.domain.ports import PortError, PortErrorCategory
from febio_cae.storage.comparison import ComparisonTarget, publish, target
from febio_cae.storage.mesh_quality import PlanarDemoRegistration
from febio_cae.storage.registry import CaseStorage, StorageConflictError

if TYPE_CHECKING:
    from .service import RegisteredCaseService

FIXED_CONDITIONS = (
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
_MEASURES = {
    "tool_compression.force_z": ("contact_force.world_z", "identity", "contact_force", "N"),
    "tool_compression.part_peak_abs_displacement_z": (
        "displacement.world_z",
        "peak_abs",
        "displacement",
        "m",
    ),
}


def _interpolate(xs: tuple[float, ...], ys: tuple[float, ...], x: float) -> float:
    if x < xs[0] or x > xs[-1]:
        raise ValueError("comparison cannot extrapolate outside observed history")
    index = bisect_left(xs, x)
    if xs[index] == x:
        return ys[index]
    weight = (x - xs[index - 1]) / (xs[index] - xs[index - 1])
    return ys[index - 1] * (1 - weight) + ys[index] * weight


def _compression(item: ComparisonTarget, data: NumericResultData) -> tuple[float, ...]:
    motion = item.revision.spec.motion
    direction = motion.direction
    if (direction.frame.value, direction.x, direction.y, direction.z) != ("World", 0, 0, -1):
        raise ValueError("comparison subset requires explicit negative World-z tool motion")
    times = tuple(float(s.time.to_si().value) for s in motion.samples)
    travel = tuple(float(s.displacement.to_si().value) for s in motion.samples)
    if any(b <= a for a, b in pairwise(travel)):
        raise ValueError("comparison requires strictly increasing imposed compression")
    if data.axis_id != "state_time" or data.axis_unit != "s":
        raise ValueError("comparison requires registered seconds state-time histories")
    return tuple(_interpolate(times, travel, t) - travel[0] for t in data.axis_values)


def _curve(
    storage: CaseStorage, item: ComparisonTarget, quantity: str
) -> tuple[NumericResultData, tuple[float, ...]]:
    spec = item.revision.spec
    requests = [r for r in spec.outputs.requests if r.quantity_id == quantity]
    if len(requests) != 1:
        raise ValueError("comparison requires exactly one explicitly registered output per measure")
    request = requests[0]
    force = quantity == "contact_force"
    body = spec.rigid_tool.primitive.body_id if force else spec.geometry.body_id
    if (
        request.component_id != "z"
        or request.frame.value != "World"
        or request.measure_id != "value"
        or request.location != ("rigid_body" if force else "node")
        or request.display_unit != ("N" if force else "m")
        or request.selection.body_id != body
        or not isinstance(request.selection.rule, WholeBodyRule)
        or request.selection.rule.body_id != body
    ):
        raise ValueError("comparison subset requires explicit whole-body World-z value outputs")
    data = storage.resolve_manifest_output(item.manifest.manifest_id, request.request_id)
    data.verify_content_digest()
    if (
        data.mapping != item.profile.mapping_for(quantity)
        or data.mapping.frame.value != "World"
        or data.mapping.unit != ("N" if force else "m")
        or data.mapping.location != request.location
        or data.mapping.measure_id != "value"
        or data.mapping.value_type != "VEC3F"
        or tuple(data.component_ids) != ("x", "y", "z")
    ):
        raise PortError(PortErrorCategory.INTEGRITY, "comparison numeric mapping is incompatible")
    if force:
        wanted = {body.value}
        if set(data.entity_ids) != wanted:
            raise PortError(PortErrorCategory.INTEGRITY, "tool force ROI is incomplete or foreign")
    else:
        wanted = {
            str(n)
            for element in item.mesh.elements
            if element.body_id == body.value
            for n in element.node_ids
        }
        if not wanted or not wanted <= set(data.entity_ids) <= {
            str(n.node_id) for n in item.mesh.nodes
        }:
            raise PortError(
                PortErrorCategory.INTEGRITY, "part displacement ROI lacks registered nodes"
            )
    positions = [index * 3 + 2 for index, entity in enumerate(data.entity_ids) if entity in wanted]
    values = tuple(
        row[positions[0]] if force else max(abs(row[p]) for p in positions) for row in data.values
    )
    return data, values


def _eligible(
    service: RegisteredCaseService, storage: CaseStorage, item: ComparisonTarget
) -> dict[str, object]:
    profile = service.compatibility.get_profile(item.profile.profile_id)
    if profile.to_bytes() != item.profile.to_bytes():
        raise PortError(
            PortErrorCategory.INTEGRITY, "comparison profile differs from registered execution"
        )
    service._resolve_declarations(
        storage, (), (*profile.evidence, *(e for cap in profile.capabilities for e in cap.evidence))
    )
    registration = storage.resolve_revision_mesh_quality(item.revision)
    if not isinstance(registration, PlanarDemoRegistration):
        raise PortError(
            PortErrorCategory.INVALID_INPUT,
            "comparison subset requires registered planar admission",
        )
    service._verify_execution_mesh(storage, registration, item.revision, item.mesh)
    quality = QualityAdapter().assess(item.manifest, item.revision, item.mesh, profile, storage)
    if quality.overall_status.value != "PASS":
        raise PortError(PortErrorCategory.QUALITY, "comparison requires passing registered quality")
    try:
        asset = storage.source_asset("quality-" + quality.assessment_id[:24])
    except StorageConflictError as error:
        raise PortError(
            PortErrorCategory.INTEGRITY, "registered comparison quality is unavailable"
        ) from error
    if storage.resolve_source(asset).content != encode_record(quality):
        raise PortError(
            PortErrorCategory.INTEGRITY, "comparison quality differs from registered assessment"
        )
    return {
        "manifest_id": item.manifest.manifest_id,
        "manifest_sha256": hashlib.sha256(item.manifest.to_bytes()).hexdigest(),
        "attempt_id": item.attempt.attempt_id,
        "run_id": item.attempt.run_id,
        "run_status": item.attempt.state.value,
        "revision_id": item.revision.revision_id,
        "revision_sha256": hashlib.sha256(item.revision.to_bytes()).hexdigest(),
        "spec_digest": item.revision.spec_digest,
        "bundle_digest": item.bundle.bundle_digest,
        "mesh_digest": item.mesh.artifact_digest,
        "quality": quality.to_dict(),
        "profile_digest": hashlib.sha256(profile.to_bytes()).hexdigest(),
        "root_mesh_digest": registration.original_mesh_digest,
        "source_step_digest": registration.source_step_digest,
    }


def compare(
    service: RegisteredCaseService,
    case_id: str,
    request: ComparisonSpec,
    baseline_run_id: str,
    candidate_run_id: str,
) -> dict[str, object]:
    if tuple(request.intended_changes) != ("material.youngs_modulus",) or set(
        request.fixed_conditions
    ) != set(FIXED_CONDITIONS):
        raise ValueError(
            "comparison must declare the E-only change and every supported fixed condition"
        )
    if {axis.axis_id for axis in request.axes} != set(_MEASURES):
        raise ValueError("comparison requires both supported compression curve/ROI axes")
    interval = request.axes[0].interval
    if any(
        axis.unit != "m" or axis.interval != interval or axis.interpolation != "linear"
        for axis in request.axes
    ):
        raise ValueError(
            "comparison axes require one common metre interval and linear interpolation"
        )
    storage = service._storage(case_id)
    with storage.evidence_snapshot():
        baseline = target(storage, request.baseline_manifest_id, baseline_run_id)
        candidate = target(storage, request.candidate_manifest_id, candidate_run_id)
        a, b = baseline.revision, candidate.revision
        if (
            a.case_id != case_id
            or b.case_id != case_id
            or b.parent_revision_id != a.revision_id
            or b.parent_spec_digest != a.spec_digest
        ):
            raise ValueError("comparison subset requires a registered direct material-edit child")
        if not isinstance(a.spec.material, IsotropicLinearElastic) or not isinstance(
            b.spec.material, IsotropicLinearElastic
        ):
            raise PortError(
                PortErrorCategory.INVALID_INPUT,
                "comparison subset requires isotropic-linear-elastic material",
            )
        restored_material = replace(
            b.spec.material,
            youngs_modulus=a.spec.material.youngs_modulus,
            youngs_modulus_evidence=a.spec.material.youngs_modulus_evidence,
        )
        if (
            replace(b.spec, material=restored_material).to_bytes() != a.spec.to_bytes()
            or a.spec.material.youngs_modulus.to_si().value
            == b.spec.material.youngs_modulus.to_si().value
        ):
            raise ValueError("comparison has an unintended fixed-condition change or no E change")
        identities = [_eligible(service, storage, item) for item in (baseline, candidate)]
        if (
            baseline.mesh.nodes != candidate.mesh.nodes
            or baseline.mesh.elements != candidate.mesh.elements
            or baseline.mesh.faces != candidate.mesh.faces
            or baseline.mesh.quality_records != candidate.mesh.quality_records
        ):
            raise ValueError(
                "comparison subset requires unchanged verified geometry/connectivity/quality"
            )
        curves: dict[str, tuple[tuple[float, ...], tuple[float, ...]]] = {}
        observed: list[tuple[float, ...]] = []
        for index, item in enumerate((baseline, candidate)):
            histories = []
            references = {}
            for quantity in ("contact_force", "displacement"):
                data, values = _curve(storage, item, quantity)
                observed_axis = _compression(item, data)
                histories.append(observed_axis)
                curves[f"{index}:{quantity}"] = (observed_axis, values)
                references[quantity] = {
                    "data_id": data.reference.data_id,
                    "content_digest": data.reference.content_digest,
                    "codec_id": data.reference.codec_id,
                    "logical_path": data.reference.logical_path,
                    "bundle_digest": data.reference.bundle_digest,
                    "attempt_id": data.reference.attempt_id,
                }
            if histories[0] != histories[1]:
                raise ValueError("comparison outputs have incompatible observed histories")
            observed.append(histories[0])
            identities[index]["numeric_sources"] = references
        lower, upper = max(xs[0] for xs in observed), min(xs[-1] for xs in observed)
        if interval.lower < lower or interval.upper > upper:
            raise ValueError("comparison requested interval would require extrapolation")
        grid = tuple(
            sorted({x for xs in observed for x in xs if interval.lower <= x <= interval.upper})
        )
        if len(grid) < 2:
            raise ValueError("comparison shared observed interval has fewer than two coordinates")
        series = []
        for axis in sorted(request.axes, key=lambda row: row.axis_id):
            measure, aggregation, quantity, unit = _MEASURES[axis.axis_id]
            body = (
                b.spec.rigid_tool.primitive.body_id
                if quantity == "contact_force"
                else b.spec.geometry.body_id
            )
            if (axis.measure_id, axis.aggregation_id, axis.roi_id) != (
                measure,
                aggregation,
                body.value,
            ):
                raise ValueError("comparison measure/aggregation/ROI is unsupported")
            before = [_interpolate(*curves[f"0:{quantity}"], x) for x in grid]
            after = [_interpolate(*curves[f"1:{quantity}"], x) for x in grid]
            difference = [y - x for x, y in zip(before, after)]
            relative = [None if x == 0 else delta / x for x, delta in zip(before, difference)]
            if any(
                not math.isfinite(v) for v in (*difference, *(r for r in relative if r is not None))
            ):
                raise PortError(PortErrorCategory.QUALITY, "comparison arithmetic is not finite")
            series.append(
                {
                    "axis_id": axis.axis_id,
                    "measure_id": measure,
                    "unit": unit,
                    "frame": "World",
                    "component": "z",
                    "roi_id": body.value,
                    "aggregation": aggregation,
                    "baseline": before,
                    "candidate": after,
                    "difference": difference,
                    "relative_difference": relative,
                    "relative_status": [
                        "undefined_zero_baseline" if x == 0 else "defined" for x in before
                    ],
                }
            )
        comparison = {
            "request": request.to_dict(),
            "sources": identities,
            "intended_change": {
                "field": "material.youngs_modulus",
                "unit": "Pa",
                "baseline": a.spec.material.youngs_modulus.to_si().value,
                "candidate": b.spec.material.youngs_modulus.to_si().value,
            },
            "common_axis": {
                "id": "positive_imposed_tool_compression",
                "definition": "minus World-z change from initial tool position; not contact-onset depth",
                "unit": "m",
                "values": list(grid),
                "observed_baseline": list(observed[0]),
                "observed_candidate": list(observed[1]),
                "shared_observed_interval": [lower, upper],
                "requested_interval": interval.to_dict(),
                "grid": "union_of_observed_coordinates_inside_requested_shared_interval",
                "interpolation": "linear",
                "extrapolation": "forbidden",
            },
            "series": series,
            "difference_definition": "candidate_minus_baseline",
            "relative_difference_definition": "(candidate-baseline)/baseline; undefined when baseline is zero",
            "aggregation_order": "aggregate_each_saved_state_then_interpolate",
            "surface_approximation": "UNVERIFIED",
            "limitations": [
                "registered synthetic planar demonstration; not scientific accuracy qualification",
                "descriptive comparison only; no ranking or force-doubling acceptance tolerance",
                "whole-part node ROI aggregate; no node-wise field subtraction or mapped field image",
            ],
        }
        payload: dict[str, object] = {
            "schema_version": "1",
            "status": "COMPARED",
            "case_id": case_id,
            "revision_id": None,
            "run_id": None,
            "diagnostics": [],
            "next_actions": [],
            "comparison_id": request.comparison_id,
            "comparison": comparison,
        }
        encoded = canonical_bytes(payload)
        path = publish(storage, request.comparison_id, encoded)
        return payload | {
            "record_path": str(path),
            "record_digest": hashlib.sha256(encoded).hexdigest(),
        }
