"""Current preparation generation and minimal case-local publication orchestration."""

from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, replace
from typing import TYPE_CHECKING, Any

from febio_cae.adapters.febio.profile_scope import require_profile_scope
from febio_cae.adapters.geometry import StepGeometryMeshAdapter
from febio_cae.adapters.geometry.preparation import (
    CurrentInspection,
    inspection_from_dict,
    resource_snapshot,
    run_preparation,
)
from febio_cae.adapters.meshing.approximation import NATIVE_ALGORITHM, ApproximationCriteria
from febio_cae.domain import CaseRevision, EvidenceRef, MeshArtifact, RigidPrimitive
from febio_cae.domain.canonical import canonical_bytes
from febio_cae.domain.codec import decode_record
from febio_cae.domain.compatibility import CapabilityStatus
from febio_cae.storage.demo_budget import reserve_preparation_mesh_attempt
from febio_cae.storage.mesh_quality import (
    CurrentPreparationRegistration,
    MeshQualityRegistration,
    PlanarPreparationRegistration,
)
from febio_cae.storage.preparation import PreparationStore, digest

from ._mesh_refinement import validate_next_refinement
from ._preparation_request import normalize_request, request_parts

if TYPE_CHECKING:
    from .service import RegisteredCaseService


class _MalformedProducerObjectError(TypeError, ValueError):
    """Type mismatch that remains on the service's persisted-integrity path."""


def _bound_generation_criteria(
    registration: MeshQualityRegistration, primitive_kind: str, max_elements: int
) -> dict[str, object] | None:
    if primitive_kind == "box":
        return None
    if primitive_kind not in {"sphere", "cylinder"}:
        raise ValueError("preparation supports only box, sphere, or cylinder tools")
    if registration.algorithm_id != NATIVE_ALGORITHM:
        raise ValueError(
            "curved preparation requires the registered native approximation algorithm"
        )
    supported = tuple(
        kind for kind in registration.primitive_kinds if kind in {"sphere", "cylinder"}
    )
    try:
        criteria = ApproximationCriteria(
            profile=registration.reference,
            max_boundary_deviation=registration.max_boundary_deviation,
            supported_kinds=supported,
            algorithm_id=registration.algorithm_id,
            evidence_scope=registration.evidence_scope,
            max_elements=max_elements,
        )
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("registered curved approximation criteria are invalid") from error
    if primitive_kind not in criteria.supported_kinds:
        raise ValueError("registered curved approximation criteria do not cover the tool kind")
    return criteria.to_dict()


def geometry_from_output(
    output: dict[str, Any],
    source: Any,
    *,
    expected_primitive: RigidPrimitive | None = None,
    expected_tool_geometry_digest: str | None = None,
) -> StepGeometryMeshAdapter:
    producer = output.get("producer", output)
    if not isinstance(producer, dict):
        raise _MalformedProducerObjectError("preparation producer output is not an object")
    required = {
        "carrier",
        "inspection",
        "backend_id",
        "backend_version",
    }
    if not required <= producer.keys():
        raise ValueError("preparation producer output is incomplete")
    try:
        carrier = decode_record(canonical_bytes(producer["carrier"]), CaseRevision)
        report = inspection_from_dict(producer["inspection"])
        backend_id = producer["backend_id"]
        backend_version = producer["backend_version"]
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise ValueError("preparation producer inspection is malformed") from error
    if canonical_bytes(carrier.to_dict()) != canonical_bytes(
        producer["carrier"]
    ) or canonical_bytes(report.to_dict()) != canonical_bytes(producer["inspection"]):
        raise ValueError("preparation producer records are not canonical")
    if report.source_digest != source.source_asset.content_digest:
        raise ValueError("preparation producer inspection source differs from registered STEP")
    primitive = carrier.spec.rigid_tool.primitive
    if expected_primitive is not None:
        if not isinstance(expected_primitive, RigidPrimitive):
            raise TypeError("expected primitive must be a RigidPrimitive")
        if primitive.to_bytes() != expected_primitive.to_bytes():
            raise ValueError("preparation producer primitive differs from the expected carrier")
    tool_geometry_digest = carrier.spec.rigid_tool.contact_surface.geometry_digest
    if (
        expected_tool_geometry_digest is not None
        and tool_geometry_digest != expected_tool_geometry_digest
    ):
        raise ValueError("preparation producer tool geometry differs from the expected carrier")
    curved_fields = {"generation_criteria", "native_tool_inspection", "native_tool_primitive"}
    if primitive.kind == "box":
        if curved_fields & producer.keys():
            raise ValueError("flat-box preparation must use the core producer format")
        native_raw = native_primitive_raw = None
    elif not curved_fields <= producer.keys():
        raise ValueError("curved preparation producer output is incomplete")
    native_raw = producer.get("native_tool_inspection")
    native_primitive_raw = producer.get("native_tool_primitive")
    native_inspection = None
    native_primitive = None
    if (native_raw is None) != (native_primitive_raw is None):
        raise ValueError("preparation native tool inspection and primitive must be paired")
    if primitive.kind in {"sphere", "cylinder"}:
        if not isinstance(native_raw, dict) or not isinstance(native_primitive_raw, dict):
            raise ValueError("curved preparation requires its native tool record")
        try:
            native_inspection = inspection_from_dict(native_raw)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("preparation native tool record is malformed") from error
        if (
            canonical_bytes(native_inspection.to_dict()) != canonical_bytes(native_raw)
            or canonical_bytes(native_primitive_raw) != primitive.to_bytes()
        ):
            raise ValueError("preparation native tool record differs from the carrier")
        native_primitive = primitive
    elif native_raw is not None or native_primitive_raw is not None:
        raise ValueError("flat-box preparation must not carry a native tool record")
    return StepGeometryMeshAdapter(
        CurrentInspection(
            report,
            backend_id,
            backend_version,
            native_tool_inspection=native_inspection,
            native_tool_primitive=native_primitive,
            native_tool_geometry_digest=tool_geometry_digest
            if native_inspection is not None
            else None,
        ),
        source_asset=source.source_asset,
    )


def prepare_planar(
    service: RegisteredCaseService,
    case_id: str,
    payload: object,
    expected_generation: int,
    *,
    parent_revision_id: str | None = None,
) -> dict[str, object]:
    from .service import (
        ConcurrentUpdateError,
        _nested_evidence,
        _registered_selections,
        _resolved_values,
    )

    request, policy = request_parts(payload)
    parsed = normalize_request(request)
    preliminary = parsed.values.to_case_spec()
    storage = service._storage(case_id)
    records = PreparationStore(storage)
    with storage.transaction():
        current = storage.current_draft(case_id)
        if type(expected_generation) is not int or current.generation != expected_generation:
            raise ConcurrentUpdateError("preparation draft generation is stale")
        frozen_id = storage.current_frozen_revision(case_id)
        parent = None
        if parent_revision_id is not None and frozen_id != parent_revision_id:
            raise ConcurrentUpdateError("refinement parent is not the current frozen revision")
        if frozen_id is not None:
            old = storage.get_revision(case_id, frozen_id)
            registration = storage.resolve_revision_mesh_quality(old)
            if parent_revision_id is None:
                if (
                    not isinstance(registration, CurrentPreparationRegistration)
                    or old.parent_revision_id is not None
                    or records.read(registration.preparation_id)["status"] != "FAILED"
                ):
                    raise ValueError(
                        "initial preparation cannot replace a frozen descendant or accepted origin"
                    )
            else:
                if not isinstance(registration, CurrentPreparationRegistration):
                    raise ValueError("refinement requires a current prepared origin")
                records.origin_output(registration, old)
                if current.values.to_case_spec().to_bytes() != old.spec.to_bytes():
                    raise ConcurrentUpdateError("refinement cannot discard a changed draft")
                parsed = normalize_request(
                    request,
                    geometry_digest=old.spec.geometry.geometry_digest,
                    inspection_digest=old.spec.geometry.inspection_digest,
                )
                preliminary = parsed.values.to_case_spec()
                if preliminary.mesh_policy.quality_profile != registration.generation_profile:
                    raise ValueError("refinement must preserve the generation profile")
                parent_selections = {
                    replace(selection, resolution=None).to_bytes(): selection.resolution
                    for selection in _registered_selections(current.values)
                }
                resolutions = {}
                for selection in _registered_selections(parsed.values):
                    resolution = parent_selections.get(
                        replace(selection, resolution=None).to_bytes()
                    )
                    if resolution is None:
                        raise ValueError("refinement changed a declared selection")
                    resolutions[selection.to_bytes()] = resolution
                restored = _resolved_values(preliminary, resolutions)
                validate_next_refinement(
                    old.spec.mesh_policy,
                    replace(
                        restored.mesh_policy,
                        quality_profile=old.spec.mesh_policy.quality_profile,
                    ),
                    old.spec.quality_policy.criteria,
                )
                restored = replace(
                    restored,
                    mesh_policy=replace(
                        restored.mesh_policy,
                        global_size=old.spec.mesh_policy.global_size,
                        local_refinements=old.spec.mesh_policy.local_refinements,
                        quality_profile=old.spec.mesh_policy.quality_profile,
                    ),
                )
                if restored.to_bytes() != old.spec.to_bytes():
                    raise ValueError("refinement may change only the declared mesh sizes")
                parent = old
        source = storage.resolve_source(storage.source_asset("cad"))
        if preliminary.geometry.source_step_digest != source.source_asset.content_digest:
            raise ValueError("preparation source digest differs from registered STEP")
        # Resolve every human evidence reference before costly child generation.
        evidence = service._resolve_declarations(
            storage,
            parsed.source_declarations,
            (*parsed.evidence, *_nested_evidence(parsed.values)),
        )
        for ref in (
            preliminary.solver_policy.profile,
            preliminary.outputs.profile,
            preliminary.quality_policy.profile,
        ):
            profile = service.compatibility.get_profile(ref.profile_id)
            require_profile_scope(preliminary, profile)
            if hashlib.sha256(profile.to_bytes()).hexdigest() != ref.record_digest or any(
                c.status is not CapabilityStatus.SUPPORTED for c in profile.capabilities
            ):
                raise ValueError("reviewed compatibility profile digest/status mismatch")
        generation_quality = storage.resolve_mesh_quality(preliminary.mesh_policy.quality_profile)
        if not isinstance(generation_quality, MeshQualityRegistration):
            raise ValueError(  # noqa: TRY004 - unsupported registered input category
                "preparation requires a registered generation profile, not a replay admission"
            )
        cpu_limit = min(
            preliminary.budget.cpu_workers, policy.cpu_workers or preliminary.budget.cpu_workers
        )
        limits = {**asdict(policy), **resource_snapshot(cpu_limit), "mesh_generations": 1}
        generation_criteria = _bound_generation_criteria(
            generation_quality,
            preliminary.rigid_tool.primitive.kind,
            limits["max_tetrahedra"],
        )
        if generation_criteria is not None:
            limits["_generation_criteria"] = generation_criteria
        record = records.begin(
            case_id,
            input_generation=expected_generation,
            input_snapshot_digest=digest(current.to_dict()),
            source_digest=source.source_asset.content_digest,
            request_digest=digest(payload),
            limits=limits,
        )
        started = time.monotonic()
        previous_geometry, previous_selection = service.geometry, service._placed_selection
        try:
            request_asset = storage.ingest_source(
                asset_id="prepare-" + record["preparation_id"],
                source_kind="user_instruction",
                media_type="application/json",
                content=canonical_bytes(payload),
            )
            admission = EvidenceRef(
                "1",
                "user_instruction",
                request_asset.asset_id,
                "mesh.admission",
                request_asset.content_digest,
            )
            with storage.evidence_snapshot():
                if not reserve_preparation_mesh_attempt(storage, case_id, record["preparation_id"]):
                    raise ValueError("preparation generation was already reserved")
                producer = run_preparation(
                    source,
                    request,
                    limits,
                    storage.root / "preparation" / record["preparation_id"] / "work",
                )
                original = decode_record(canonical_bytes(producer["mesh"]), MeshArtifact)
                carrier = decode_record(canonical_bytes(producer["carrier"]), CaseRevision)
                report = inspection_from_dict(producer["inspection"])
                if (
                    producer["mesh_generations"] != 1
                    or report.source_digest != source.source_asset.content_digest
                ):
                    raise ValueError("producer source/generation binding mismatch")
                if (
                    producer["backend"].get("gmsh_version") != "4.15.2"
                    or producer["backend"].get("occt_version") != "7.8.1"
                ):
                    raise ValueError("producer version evidence mismatch")
                if (
                    len(original.nodes) > policy.max_nodes
                    or len(original.elements) > policy.max_tetrahedra
                ):
                    raise ValueError("producer mesh exceeds finite counts")
                try:
                    if carrier.spec.rigid_tool.primitive.kind == "box":
                        criteria_match = not any(
                            key in producer
                            for key in (
                                "generation_criteria",
                                "native_tool_inspection",
                                "native_tool_primitive",
                            )
                        )
                    else:
                        criteria_match = canonical_bytes(
                            producer["generation_criteria"]
                        ) == canonical_bytes(limits.get("_generation_criteria"))
                except (KeyError, TypeError, ValueError):
                    criteria_match = False
                if (
                    not criteria_match
                    or producer.get("backend_id") != original.provenance.tool_id
                    or producer.get("backend_version") != original.provenance.tool_version
                ):
                    raise ValueError("producer criteria/backend provenance binding mismatch")
                geometry = geometry_from_output(
                    producer,
                    source,
                    expected_primitive=carrier.spec.rigid_tool.primitive,
                    expected_tool_geometry_digest=carrier.spec.rigid_tool.contact_surface.geometry_digest,
                )
                normalized = normalize_request(
                    request,
                    geometry_digest=report.geometry_digest,
                    inspection_digest=digest(report.to_dict()),
                )
                spec = normalized.values.to_case_spec()
                resolutions = {
                    s.to_bytes(): geometry.resolve_placed_selection(
                        source, spec.geometry, spec.rigid_tool, s
                    )
                    for s in _registered_selections(normalized.values)
                }
                values = _resolved_values(normalized.values, resolutions)
                if values.to_case_spec().to_bytes() != carrier.spec.to_bytes():
                    raise ValueError(
                        "producer changed explicit specification or selection snapshot"
                    )
                registration_type = (
                    PlanarPreparationRegistration
                    if carrier.spec.rigid_tool.primitive.kind == "box"
                    else CurrentPreparationRegistration
                )
                registration = registration_type(
                    "prepare-" + record["preparation_id"],
                    source.source_asset.content_digest,
                    report.geometry_digest,
                    original.artifact_digest,
                    original.provenance.mesh_recipe_digest,
                    preliminary.mesh_policy.quality_profile,
                    (admission,),
                    record["preparation_id"],
                )
                storage.register_mesh_quality(registration)
                values = replace(
                    values,
                    mesh_policy=replace(values.mesh_policy, quality_profile=registration.reference),
                )
                draft = service.set_spec(
                    case_id,
                    values=values,
                    expected_generation=expected_generation,
                    evidence=evidence,
                    input_intent=parsed.input_intent,
                    parent_revision_id=parent.revision_id if parent is not None else None,
                )
                service.geometry = geometry
                service._placed_selection = geometry.resolve_placed_selection
                frozen = service.freeze_case(case_id)
                if frozen.status != "FROZEN" or frozen.revision is None:
                    raise ValueError(
                        f"prepared specification failed validation: {frozen.to_dict()}"
                    )
                revision = storage.get_revision(case_id, frozen.revision.revision_id)
                record.update(
                    revision_id=revision.revision_id,
                    generation=draft.generation,
                    snapshot_digest=digest(draft.to_dict()),
                    inspection_digest=digest(report.to_dict()),
                    mesh_digest=original.artifact_digest,
                    recipe_digest=original.provenance.mesh_recipe_digest,
                    backend_id=producer["backend_id"],
                    backend_version=producer["backend_version"],
                    backend=producer["backend"],
                )
                if carrier.spec.rigid_tool.primitive.kind != "box":
                    record.update(
                        generation_criteria=producer["generation_criteria"],
                        native_tool_inspection_digest=(
                            None
                            if producer["native_tool_inspection"] is None
                            else digest(producer["native_tool_inspection"])
                        ),
                        native_tool_primitive_digest=(
                            None
                            if producer["native_tool_primitive"] is None
                            else digest(producer["native_tool_primitive"])
                        ),
                        native_tool_geometry_digest=carrier.spec.rigid_tool.contact_surface.geometry_digest,
                    )
                mesh, receipt = service._adopt_planar_mesh(
                    registration, original, carrier, revision
                )
                if time.monotonic() - started >= policy.wall_seconds:
                    raise TimeoutError("preparation budget exhausted before publication")
                records.publish(
                    record,
                    revision,
                    {"producer": producer, "mesh": mesh.to_dict(), "adoption": receipt},
                )
                return {
                    "schema_version": "1",
                    "status": "PREPARED",
                    "case_id": case_id,
                    "revision_id": revision.revision_id,
                    "preparation_id": record["preparation_id"],
                    "generation": draft.generation,
                    "mesh_digest": mesh.artifact_digest,
                    "surface_approximation": "UNVERIFIED",
                    "limits": limits,
                    "native_qualification": "UNVERIFIED",
                }
        except BaseException as error:
            records.failed(record, error)
            raise
        finally:
            service.geometry, service._placed_selection = previous_geometry, previous_selection
