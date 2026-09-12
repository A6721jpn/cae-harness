"""Current planar generation and minimal case-local publication orchestration."""

from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, replace
from typing import TYPE_CHECKING, Any

from febio_cae.adapters.geometry import StepGeometryMeshAdapter
from febio_cae.adapters.geometry.preparation import (
    CurrentInspection,
    inspection_from_dict,
    resource_snapshot,
    run_preparation,
)
from febio_cae.domain import CaseRevision, EvidenceRef, MeshArtifact, Quantity
from febio_cae.domain.canonical import canonical_bytes
from febio_cae.domain.codec import decode_record
from febio_cae.domain.compatibility import CapabilityStatus
from febio_cae.storage.demo_budget import reserve_preparation_mesh_attempt
from febio_cae.storage.mesh_quality import MeshQualityRegistration, PlanarPreparationRegistration
from febio_cae.storage.preparation import PreparationStore, digest

from ._preparation_request import normalize_request, request_parts

if TYPE_CHECKING:
    from .service import RegisteredCaseService


def geometry_from_output(output: dict[str, Any], source: Any) -> StepGeometryMeshAdapter:
    producer = output.get("producer", output)
    report = inspection_from_dict(producer["inspection"])
    return StepGeometryMeshAdapter(
        CurrentInspection(report, producer["backend_id"], producer["backend_version"]),
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
                    not isinstance(registration, PlanarPreparationRegistration)
                    or records.read(registration.preparation_id)["status"] != "FAILED"
                ):
                    raise ValueError(
                        "preparation cannot replace an accepted or unresolved frozen origin"
                    )
            else:
                if not isinstance(registration, PlanarPreparationRegistration):
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
                restored = replace(
                    restored,
                    mesh_policy=replace(
                        restored.mesh_policy,
                        global_size=old.spec.mesh_policy.global_size,
                        quality_profile=old.spec.mesh_policy.quality_profile,
                    ),
                )
                if restored.to_bytes() != old.spec.to_bytes():
                    raise ValueError("refinement may change only the declared global mesh size")
                criteria = [
                    criterion
                    for criterion in old.spec.quality_policy.criteria
                    if criterion.metric_id == "mesh_dependence"
                ]
                if len(criteria) != 1:
                    raise ValueError("refinement requires one declared mesh-dependence criterion")
                thresholds = {
                    threshold.parameter_id: threshold.value for threshold in criteria[0].thresholds
                }
                names = ("coarse_size", "refined_size", "fine_size")
                if any(
                    name not in thresholds
                    or thresholds[name].dimension != Quantity(1, "m").dimension
                    for name in names
                ):
                    raise ValueError("refinement requires three declared physical mesh sizes")
                sizes = tuple(float(thresholds[name].to_si().value) for name in names)
                if not sizes[0] > sizes[1] > sizes[2] > 0:
                    raise ValueError("refinement sizes must be positive and strictly decreasing")
                previous_size = float(old.spec.mesh_policy.global_size.to_si().value)
                if previous_size not in sizes:
                    raise ValueError("parent mesh size is outside the declared refinement study")
                next_index = sizes.index(previous_size) + 1
                if (
                    next_index >= len(sizes)
                    or next_index > old.spec.mesh_policy.max_refinements
                    or float(preliminary.mesh_policy.global_size.to_si().value) != sizes[next_index]
                ):
                    raise ValueError("refinement must use the next declared mesh size")
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
                geometry = geometry_from_output(producer, source)
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
                registration = PlanarPreparationRegistration(
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
                    mesh_policy=replace(spec.mesh_policy, quality_profile=registration.reference),
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
                    backend=producer["backend"],
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
