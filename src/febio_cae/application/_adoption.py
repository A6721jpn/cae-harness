"""Explicit planar-demo metadata derivation, never native remeshing or qualification."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from febio_cae.domain import CaseRevision, MeshArtifact, MeshSet, SelectionRef, WholeBodyRule
from febio_cae.domain.canonical import canonical_bytes
from febio_cae.storage.mesh_quality import PlanarDemoRegistration


def _without_evidence(value: Any) -> Any:
    if isinstance(value, dict):
        if {"source_kind", "reference", "target_field", "content_digest"} <= value.keys():
            return None
        return {key: _without_evidence(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_without_evidence(child) for child in value]
    return value


def _selection_key(selection: SelectionRef) -> bytes:
    projection = selection.to_dict()
    projection.pop("resolution", None)
    return canonical_bytes(_without_evidence(projection))


def _mesh_sets_by_selection_digest(
    sets: Sequence[MeshSet],
) -> dict[str, tuple[MeshSet, ...]]:
    grouped: dict[str, list[MeshSet]] = {}
    for item in sets:
        grouped.setdefault(item.source_selection_digest, []).append(item)
    return {digest: tuple(items) for digest, items in grouped.items()}


def adopt(
    registration: PlanarDemoRegistration,
    original: MeshArtifact,
    carrier: CaseRevision,
    revision: CaseRevision,
    old_selections: tuple[SelectionRef, ...],
    selections: tuple[SelectionRef, ...],
) -> tuple[MeshArtifact, dict[str, object]]:
    registration.check_spec(revision.spec)
    old, current = carrier.spec, revision.spec
    if (
        original.artifact_digest != registration.original_mesh_digest
        or original.provenance.mesh_recipe_digest != registration.original_recipe_digest
        or old.mesh_policy.quality_profile != registration.generation_profile
        or current.mesh_policy.quality_profile != registration.reference
        or original.provenance.source_geometry_digest != registration.geometry_digest
        or old.geometry.source_step_digest != registration.source_step_digest
    ):
        raise ValueError("original mesh/generation provenance differs from admission")
    # Rebinding a registered inspection/evidence reference is explicit. No physical
    # placement, primitive dimensions, source/body, mesh size or refinement changes.
    old_geometry, geometry = old.geometry.to_dict(), current.geometry.to_dict()
    old_geometry.pop("inspection_digest")
    geometry.pop("inspection_digest")
    old_policy, policy = old.mesh_policy.to_dict(), current.mesh_policy.to_dict()
    old_policy.pop("quality_profile")
    policy.pop("quality_profile")
    for before, after in (
        (old_geometry, geometry),
        (old_policy, policy),
        (old.rigid_tool.primitive.to_dict(), current.rigid_tool.primitive.to_dict()),
    ):
        if canonical_bytes(_without_evidence(before)) != canonical_bytes(_without_evidence(after)):
            raise ValueError("adoption cannot alter mesh-generating physical inputs")
    required = {
        "tet10-positive-corner-volume",
        "tet10-backend-mapping",
        "part-tool-node-independence",
    }
    checks = {check.metric_id: check for check in original.quality_records}
    if (
        not required <= checks.keys()
        or any(checks[name].status != "PASS" for name in required)
        or any(
            check.status != "PASS"
            and not (check.metric_id == "surface-approximation" and check.status == "UNVERIFIED")
            for check in original.quality_records
        )
        or "surface-approximation" not in checks
    ):
        raise ValueError("planar admission cannot waive other missing or nonpassing mesh checks")
    bodies = {current.geometry.body_id.value, current.rigid_tool.primitive.body_id.value}
    if set(original.provenance.source_body_ids) != bodies:
        raise ValueError("adoption body membership differs")
    body_nodes = {
        body: {n for e in original.elements if e.body_id == body for n in e.node_ids}
        for body in bodies
    }
    if any(not ids for ids in body_nodes.values()) or set.intersection(*body_nodes.values()):
        raise ValueError("adoption requires independent part/tool nodes")
    old_by_key = {_selection_key(selection): selection for selection in old_selections}
    targets = {
        hashlib.sha256(selection.to_bytes()).hexdigest(): selection for selection in selections
    }
    original_sets_by_digest = _mesh_sets_by_selection_digest(original.sets)
    aliases: list[dict[str, object]] = []
    sets: list[MeshSet] = []
    for digest, selection in sorted(targets.items()):
        source = old_by_key.get(_selection_key(selection))
        if source is None:
            raise ValueError(
                "new selection is not an evidence-only adoption of a generated selection"
            )
        if source.resolution is not None and source.resolution != selection.resolution:
            raise ValueError("adoption changes an existing resolved selection snapshot")
        original_digest = hashlib.sha256(source.to_bytes()).hexdigest()
        matching = original_sets_by_digest.get(original_digest, ())
        if not matching or any(item.body_id != selection.body_id.value for item in matching):
            raise ValueError("original generated selection is absent or crosses body ownership")
        mapped = [
            replace(item, set_id=f"adopt:{digest[:24]}:{index}", source_selection_digest=digest)
            for index, item in enumerate(matching)
        ]
        # WholeBody is explicit semantic input, so all nodes/elements of that
        # declared body are enumerable without inferring a physical ROI.
        if isinstance(selection.rule, WholeBodyRule):
            locations = {
                request.location
                for request in current.outputs.requests
                if request.selection == selection
            }
            for location in sorted(locations):
                kind = str(location)
                if location in {"face", "surface", "surface_node"}:
                    if any(item.kind == "face" for item in mapped):
                        continue
                    raise ValueError("unsupported whole-body output projection")
                if any(item.kind == location for item in mapped):
                    continue
                body = selection.body_id.value
                members: tuple[int | str, ...]
                if location == "node":
                    members = tuple(sorted(body_nodes[body]))
                elif location == "element":
                    members = tuple(
                        sorted(e.element_id for e in original.elements if e.body_id == body)
                    )
                elif location == "rigid_body":
                    kind, members = "body", (body,)
                else:
                    raise ValueError("unsupported whole-body output projection")
                mapped.append(MeshSet(f"adopt:{digest[:24]}:{kind}", kind, body, members, digest))
        sets.extend(mapped)
        aliases.append(
            {
                "original_selection_digest": original_digest,
                "registered_selection_digest": digest,
                "body_id": selection.body_id.value,
                "derived_sets": [item.to_dict() for item in mapped],
            }
        )
    contact_selections = (
        current.contact.part_surface,
        current.contact.tool_surface,
    )
    contact_output_locations = {
        request.location
        for request in current.outputs.requests
        if request.selection in contact_selections
        and request.location in {"face", "surface", "surface_node"}
    }
    if contact_output_locations:
        adopted_sets_by_digest = _mesh_sets_by_selection_digest(sets)
        for selection in contact_selections:
            digest = hashlib.sha256(selection.to_bytes()).hexdigest()
            face_sets = tuple(
                item
                for item in adopted_sets_by_digest.get(digest, ())
                if item.kind == "face" and item.body_id == selection.body_id.value
            )
            if len(face_sets) != 1:
                raise ValueError(
                    f"contact selection does not resolve to one adopted face set: {selection.name}"
                )
    receipt: dict[str, object] = {
        "format_version": 1,
        "operation": "explicit-planar-metadata-adoption",
        "original_mesh_digest": original.artifact_digest,
        "original_recipe_digest": original.provenance.mesh_recipe_digest,
        "original_generation_profile": registration.generation_profile.to_dict(),
        "original_carrier_digest": hashlib.sha256(carrier.to_bytes()).hexdigest(),
        "registered_revision_digest": hashlib.sha256(revision.to_bytes()).hexdigest(),
        "admission_profile": registration.reference.to_dict(),
        "selection_correspondence": aliases,
        "geometry_operation": "none; coordinates/connectivity/faces unchanged",
        "approximation_status": registration.approximation_status,
    }
    recipe = hashlib.sha256(canonical_bytes(receipt)).hexdigest()
    adopted = replace(
        original,
        artifact_id=f"adopted:{recipe[:24]}",
        sets=tuple(sets),
        provenance=replace(
            original.provenance,
            mesh_recipe_digest=recipe,
            source_selection_digests=tuple(sorted(targets)),
        ),
    )
    receipt["adopted_mesh_digest"] = adopted.artifact_digest
    receipt["adopted_binding_recipe_digest"] = recipe
    return adopted, receipt
