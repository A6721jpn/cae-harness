"""Focused data-driven planar quality regressions."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from typing import Any

from febio_cae.domain import (
    AssessmentStatus,
    AttemptRecord,
    BodyId,
    CaseRevision,
    EvaluationRequest,
    ExecutionBundle,
    FaceId,
    FaceSetRule,
    FileEntry,
    FrameId,
    MeshArtifact,
    MeshElement,
    MeshFace,
    MeshNode,
    MeshProvenance,
    MeshQualityRecord,
    MeshSet,
    NamedAttributeRule,
    NumericResultData,
    OutputMapping,
    OutputObservation,
    Point3,
    QualityCriterion,
    QualityPolicy,
    QualityThreshold,
    Quantity,
    ReadResult,
    ReadStatus,
    ResolvedFileContent,
    ResultDataRef,
    ResultManifest,
    SelectionRef,
    SupportComponent,
)

from .fixtures import (
    PART_BODY,
    PART_DIGEST,
    TOOL_BODY,
    TOOL_DIGEST,
    evidence,
    make_profile,
    make_revision,
)

WORLD = FrameId("World")
_BUNDLE = "b" * 64
_RESULT_CONTENT = b"planar-quality-test-result"
_RESULT_ENTRY = FileEntry(
    "output/results.xplt",
    hashlib.sha256(_RESULT_CONTENT).hexdigest(),
    len(_RESULT_CONTENT),
    "result",
)
_SAVED_TIMES = (0.0, 0.5, 1.0)
_PART_NODES = tuple(str(index) for index in range(1, 11))
_TOOL_NODES = tuple(str(index) for index in range(11, 21))
_SUPPORT_NODES = ("4",)


@dataclass
class _Data:
    records: dict[str, NumericResultData]

    def resolve_manifest_output(self, manifest_id: str, output_id: str) -> NumericResultData:
        return self.records[output_id]

    def resolve(self, reference: ResultDataRef) -> NumericResultData:
        return next(item for item in self.records.values() if item.reference == reference)

    def resolve_file(
        self, entry: FileEntry, bundle: ExecutionBundle, attempt: AttemptRecord
    ) -> ResolvedFileContent:
        if entry != _RESULT_ENTRY:
            raise KeyError(entry.logical_path)
        return ResolvedFileContent(entry, _RESULT_CONTENT)


def _face_selection(name: str, role: str, digest: str, body: BodyId, face_id: str) -> SelectionRef:
    return SelectionRef(
        name=name,
        role=role,
        role_evidence=evidence("selection.role", name),
        geometry_digest=digest,
        body_id=body,
        frame=WORLD,
        rule=FaceSetRule(
            digest,
            body,
            WORLD,
            (FaceId(face_id),),
            evidence("selection.face", name),
        ),
    )


def _mesh(
    part_contact: SelectionRef,
    tool_contact: SelectionRef,
    support: SelectionRef,
    part_output: SelectionRef,
    tool_output: SelectionRef,
) -> MeshArtifact:
    def tet_nodes(offset: int, z: float, top: float) -> tuple[MeshNode, ...]:
        corners = ((0.0, 0.0, z), (0.01, 0.0, z), (0.0, 0.01, z), (0.0, 0.0, top))
        if top < z:
            corners = (corners[0], corners[2], corners[1], corners[3])
        mids = tuple(
            tuple((corners[a][axis] + corners[b][axis]) / 2.0 for axis in range(3))
            for a, b in ((0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3))
        )
        return tuple(
            MeshNode(offset + index + 1, point) for index, point in enumerate(corners + mids)
        )

    nodes = tet_nodes(0, 0.0, -0.01) + tet_nodes(10, 0.0, 0.01)
    elements = (
        MeshElement(1, "tet10", tuple(range(1, 11)), PART_BODY.value),
        MeshElement(2, "tet10", tuple(range(11, 21)), TOOL_BODY.value),
    )
    faces = (
        MeshFace("part-face", PART_BODY.value, (1, 3, 2, 7, 6, 5), (1,), (0,)),
        MeshFace("tool-face", TOOL_BODY.value, (11, 13, 12, 17, 16, 15), (2,), (0,)),
    )
    selections = (part_contact, tool_contact, support, part_output, tool_output)
    source = {item.name: hashlib.sha256(item.to_bytes()).hexdigest() for item in selections}
    sets = (
        # Body projections are needed for rigid-body ROI resolution.
        MeshSet(
            "part-body", "body", PART_BODY.value, (PART_BODY.value,), source[part_contact.name]
        ),
        MeshSet(
            "tool-body", "body", TOOL_BODY.value, (TOOL_BODY.value,), source[tool_contact.name]
        ),
        MeshSet("part-contact", "face", PART_BODY.value, ("part-face",), source[part_contact.name]),
        MeshSet("tool-contact", "face", TOOL_BODY.value, ("tool-face",), source[tool_contact.name]),
        MeshSet("support-region", "node", PART_BODY.value, (4,), source[support.name]),
        MeshSet(
            "part-output", "node", PART_BODY.value, tuple(range(1, 11)), source[part_output.name]
        ),
        MeshSet(
            "tool-output", "node", TOOL_BODY.value, tuple(range(11, 21)), source[tool_output.name]
        ),
    )
    return MeshArtifact(
        artifact_id="planar-quality-mesh",
        frame=WORLD,
        provenance=MeshProvenance(
            source_geometry_digest=PART_DIGEST,
            source_body_ids=(PART_BODY.value, TOOL_BODY.value),
            source_selection_digests=tuple(source.values()),
            mesh_recipe_digest="9" * 64,
            tool_id="synthetic-planar-mesher",
            tool_version="1",
            mapping_id="tet10-canonical-v1",
            node_ordering_id="tet10-canonical-v1",
            face_ordering_id="tet10-face-canonical-v1",
        ),
        nodes=nodes,
        elements=elements,
        faces=faces,
        sets=sets,
        quality_records=(
            MeshQualityRecord("jacobian", 0.5, "1", 0.1, "PASS", "synthetic planar fixture"),
        ),
    )


def _numeric(
    mapping: OutputMapping,
    entities: tuple[str, ...],
    values: tuple[tuple[float, ...], ...],
    times: tuple[float, ...] = _SAVED_TIMES,
) -> NumericResultData:
    result = NumericResultData(
        reference=ResultDataRef(
            mapping.canonical_id,
            "0" * 64,
            "numeric-result-v1",
            "output/results.xplt",
            _BUNDLE,
            "planar-quality-attempt",
        ),
        mapping=mapping,
        axis_id="state_time",
        axis_unit="s",
        axis_values=times,
        entity_ids=entities,
        component_ids=("x", "y", "z"),
        values=values,
    )
    return replace(
        result, reference=replace(result.reference, content_digest=result.expected_content_digest)
    )


def _manifest(records: dict[str, NumericResultData], profile: Any) -> ResultManifest:
    observations = tuple(
        OutputObservation(
            key,
            records[key].mapping.location,
            records[key].mapping.value_type,
            records[key].mapping.unit,
            WORLD,
            records[key].mapping.measure_id,
            len(records[key].axis_values),
            records[key].reference,
        )
        for key in records
    )
    return ResultManifest(
        "planar-quality-manifest",
        "planar-quality-attempt",
        _BUNDLE,
        (_RESULT_ENTRY,),
        ReadResult(ReadStatus.VALIDATED, profile.reader, observations, ()),
    )


def _fixture() -> tuple[CaseRevision, MeshArtifact, Any, ResultManifest, _Data]:
    revision = make_revision()
    spec = revision.spec
    part_contact = _face_selection(
        "part-contact-face", "part_contact_surface", PART_DIGEST, PART_BODY, "part-face"
    )
    tool_contact = _face_selection(
        "tool-contact-face", "tool_contact_surface", TOOL_DIGEST, TOOL_BODY, "tool-face"
    )
    support = replace(
        _face_selection("support-apex", "support_surface", PART_DIGEST, PART_BODY, "part-face"),
        rule=NamedAttributeRule("support-apex"),
    )
    part_output = next(
        item.selection for item in spec.outputs.requests if item.request_id == "request_part"
    )
    tool_output = next(
        item.selection for item in spec.outputs.requests if item.request_id == "request_tool"
    )
    mesh = _mesh(part_contact, tool_contact, support, part_output, tool_output)
    supports = (replace(spec.support.supports[0], selection=support),)
    contact = replace(spec.contact, part_surface=part_contact, tool_surface=tool_contact)
    rigid_tool = replace(spec.rigid_tool, contact_surface=tool_contact)
    motion = replace(
        spec.motion,
        direction=replace(spec.motion.direction, z=-1.0),
        initial_reference_point=Point3(
            WORLD, Quantity(0.0, "m"), Quantity(0.0, "m"), Quantity(0.0, "m")
        ),
    )
    base_part = next(item for item in spec.outputs.requests if item.request_id == "request_part")
    base_tool = next(item for item in spec.outputs.requests if item.request_id == "request_tool")
    requests = (
        replace(
            base_part,
            request_id="part_displacement",
            selection=part_output,
            evidence=evidence("outputs.requests.part_displacement", "part-displacement"),
        ),
        replace(
            base_part,
            request_id="tool_displacement",
            selection=tool_output,
            evidence=evidence("outputs.requests.tool_displacement", "tool-displacement"),
        ),
        replace(
            base_part,
            request_id="support_reaction",
            quantity_id="reaction",
            location="node",
            selection=support,
            display_unit="N",
            evidence=evidence("outputs.requests.support_reaction", "support-reaction"),
        ),
        replace(
            base_tool,
            request_id="tool_force",
            evidence=evidence("outputs.requests.tool_force", "tool-force"),
        ),
        replace(
            base_tool,
            request_id="tool_position",
            quantity_id="rigid_position",
            location="rigid_body",
            selection=tool_output,
            display_unit="m",
            evidence=evidence("outputs.requests.tool_position", "tool-position"),
        ),
    )
    evaluations = tuple(
        EvaluationRequest(
            evaluation_id=identifier,
            output_request_id=request_id,
            aggregation_id="peak",
            selection=selection,
            state_times=tuple(Quantity(value, "s") for value in _SAVED_TIMES),
            evidence=evidence(f"outputs.evaluations.{identifier}", identifier),
        )
        for identifier, request_id, selection in (
            ("ev_part_displacement", "part_displacement", part_output),
            ("ev_tool_displacement", "tool_displacement", tool_output),
            ("ev_support_reaction", "support_reaction", support),
            ("ev_tool_force", "tool_force", tool_output),
            ("ev_tool_position", "tool_position", tool_output),
        )
    )
    criteria = (
        QualityCriterion(
            "declared_contact_obligation",
            "planar_contact",
            ("ev_part_displacement", "ev_tool_displacement", "ev_tool_position", "ev_tool_force"),
            (
                QualityThreshold("initial_interference_max", Quantity(1.0e-9, "m")),
                QualityThreshold("contact_gap_max", Quantity(1.0e-6, "m")),
                QualityThreshold("penetration_max", Quantity(1.0e-6, "m")),
                QualityThreshold("force_absolute_floor", Quantity(1.0e-3, "N")),
                QualityThreshold("contact_start", Quantity(0.5, "s")),
                QualityThreshold("contact_end", Quantity(1.0, "s")),
            ),
            "explicit supported planar contact fixture",
            evidence("quality_policy.criteria.declared_contact_obligation", "contact-criterion"),
        ),
        QualityCriterion(
            "declared_fidelity_obligation",
            "motion_support_contact_fidelity",
            ("ev_part_displacement", "ev_tool_displacement", "ev_tool_position"),
            (
                QualityThreshold("motion_error_max", Quantity(1.0e-4, "m")),
                QualityThreshold("support_displacement_max", Quantity(1.0e-8, "m")),
            ),
            "explicit supported planar contact fixture",
            evidence("quality_policy.criteria.declared_fidelity_obligation", "fidelity-criterion"),
        ),
        QualityCriterion(
            "declared_equilibrium_obligation",
            "quasistatic_equilibrium",
            ("ev_support_reaction", "ev_tool_force"),
            (
                QualityThreshold("relative_max", Quantity(1.0e-6, "1")),
                QualityThreshold("absolute_floor", Quantity(1.0e-6, "N")),
            ),
            "explicit supported planar contact fixture",
            evidence(
                "quality_policy.criteria.declared_equilibrium_obligation", "equilibrium-criterion"
            ),
        ),
    )
    outputs = replace(
        spec.outputs,
        requests=requests,
        evaluations=evaluations,
        saved_times=tuple(Quantity(value, "s") for value in _SAVED_TIMES),
    )
    quality = QualityPolicy(spec.quality_policy.profile, criteria)
    revision = replace(
        revision,
        spec=replace(
            spec,
            support=replace(spec.support, supports=supports),
            rigid_tool=rigid_tool,
            motion=motion,
            contact=contact,
            outputs=outputs,
            quality_policy=quality,
        ),
    )
    profile = make_profile()
    profile = replace(
        profile,
        output_mappings=(
            replace(
                next(
                    item for item in profile.output_mappings if item.canonical_id == "contact_force"
                ),
                raw_sign=1,
                canonical_sign=1,
            ),
            next(item for item in profile.output_mappings if item.canonical_id == "displacement"),
            OutputMapping(
                "reaction", "reaction forces", "node", "VEC3F", "N", WORLD, -1, 1, "value"
            ),
            OutputMapping(
                "rigid_position", "rigid position", "rigid_body", "VEC3F", "m", WORLD, 1, 1, "value"
            ),
        ),
    )
    disp_rows = tuple(
        tuple(
            value
            for node in mesh.nodes[:10]
            for value in (0.0, 0.0, -0.0001 * time * (node.coordinates_si[2] + 0.01) / 0.01)
        )
        + tuple(value for _ in _TOOL_NODES for value in (0.0, 0.0, -0.0001 * time))
        for time in _SAVED_TIMES
    )
    reaction_rows = (
        tuple(0.0 for _ in range(30)),
        tuple(
            value
            for node in _PART_NODES
            for value in (0.0, 0.0, 6.0 if node in _SUPPORT_NODES else 0.0)
        ),
        tuple(
            value
            for node in _PART_NODES
            for value in (0.0, 0.0, 6.0 if node in _SUPPORT_NODES else 0.0)
        ),
    )
    force_rows = ((0.0, 0.0, 0.0), (0.0, 0.0, -6.0), (0.0, 0.0, -6.0))
    position_rows = tuple((0.0, 0.0, -0.0001 * time) for time in _SAVED_TIMES)
    mappings = {item.canonical_id: item for item in profile.output_mappings}
    records = {
        "displacement": _numeric(mappings["displacement"], _PART_NODES + _TOOL_NODES, disp_rows),
        "reaction": _numeric(mappings["reaction"], _PART_NODES, reaction_rows),
        "contact_force": _numeric(mappings["contact_force"], (TOOL_BODY.value,), force_rows),
        "rigid_position": _numeric(mappings["rigid_position"], (TOOL_BODY.value,), position_rows),
    }
    records = {request.request_id: records[request.quantity_id] for request in requests}
    manifest = _manifest(records, profile)
    return revision, mesh, profile, manifest, _Data(records)


def _replace_displacement(
    case: tuple[CaseRevision, MeshArtifact, Any, ResultManifest, _Data],
    numeric: NumericResultData,
    *,
    times: tuple[float, ...] = _SAVED_TIMES,
) -> tuple[CaseRevision, MeshArtifact, Any, ResultManifest, _Data]:
    revision, mesh, profile, manifest, data = case
    displacement = _numeric(
        numeric.mapping,
        tuple(numeric.entity_ids),
        tuple(tuple(row) for row in numeric.values[: len(times)]),
        times,
    )
    records = {
        key: displacement if item.mapping.canonical_id == "displacement" else item
        for key, item in data.records.items()
    }
    observations = tuple(
        replace(item, data_ref=displacement.reference, state_count=len(displacement.axis_values))
        if item.output_id in {"part_displacement", "tool_displacement"}
        else item
        for item in manifest.read_result.observations
    )
    manifest = replace(
        manifest, read_result=replace(manifest.read_result, observations=observations)
    )
    return revision, mesh, profile, manifest, _Data(records)


def _assess(case: tuple[CaseRevision, MeshArtifact, Any, ResultManifest, _Data]) -> tuple[Any, ...]:
    from febio_cae.adapters.febio.planar_quality import assess_planar_requirements

    revision, mesh, profile, manifest, data = case
    return assess_planar_requirements(manifest, revision, mesh, profile, data)


def test_valid_declared_planar_data_returns_fixed_metrics_without_criterion_id_inference() -> None:
    case = _fixture()
    assessments = _assess(case)
    assert tuple(item.criterion_id for item in assessments) == (
        "contact_quality",
        "motion_support_contact_fidelity",
        "quasistatic_equilibrium",
    )
    assert all(item.status is AssessmentStatus.PASS for item in assessments)


def test_local_tool_deviation_fails_full_xyz_translation_check() -> None:
    case = _fixture()
    numeric = case[-1].records["part_displacement"]
    values = [list(row) for row in numeric.values]
    values[-1][len(_PART_NODES) * 3] = 0.0002
    updated = _replace_displacement(
        case, replace(numeric, values=tuple(tuple(row) for row in values))
    )
    assessments = _assess(updated)
    assert (
        next(
            item for item in assessments if item.criterion_id == "motion_support_contact_fidelity"
        ).status
        is AssessmentStatus.FAIL
    )


def test_local_penetration_is_not_hidden_by_mean_face_separation() -> None:
    case = _fixture()
    numeric = case[-1].records["part_displacement"]
    values = [list(row) for row in numeric.values]
    values[-1][(len(_PART_NODES) + 6) * 3 + 2] = -0.0014
    updated = _replace_displacement(
        case, replace(numeric, values=tuple(tuple(row) for row in values))
    )
    assessments = _assess(updated)
    assert (
        next(item for item in assessments if item.criterion_id == "contact_quality").status
        is AssessmentStatus.FAIL
    )


def test_free_support_axis_reaction_is_not_used_and_missing_endpoint_is_unverified() -> None:
    case = _fixture()
    revision, mesh, profile, manifest, data = case
    support = revision.spec.support.supports[0]
    free_support = replace(support, y=SupportComponent("free", evidence("support.y", "free-y")))
    revision = replace(
        revision,
        spec=replace(
            revision.spec, support=replace(revision.spec.support, supports=(free_support,))
        ),
    )
    reaction = data.records["support_reaction"]
    rows = [list(row) for row in reaction.values]
    for row in rows[1:]:
        for index in range(1, len(row), 3):
            row[index] = 100.0
    reaction = _numeric(
        reaction.mapping, tuple(reaction.entity_ids), tuple(tuple(row) for row in rows)
    )
    records = dict(data.records, support_reaction=reaction)
    observations = tuple(
        replace(item, data_ref=reaction.reference) if item.output_id == "support_reaction" else item
        for item in manifest.read_result.observations
    )
    manifest = replace(
        manifest, read_result=replace(manifest.read_result, observations=observations)
    )
    case = (revision, mesh, profile, manifest, _Data(records))
    assert (
        next(
            item for item in _assess(case) if item.criterion_id == "quasistatic_equilibrium"
        ).status
        is AssessmentStatus.PASS
    )

    incomplete = _replace_displacement(case, data.records["part_displacement"], times=(0.0, 0.5))
    assert (
        next(
            item
            for item in _assess(incomplete)
            if item.criterion_id == "motion_support_contact_fidelity"
        ).status
        is AssessmentStatus.UNVERIFIED
    )


def test_small_load_imbalance_cannot_borrow_later_load_tolerance() -> None:
    revision, mesh, profile, _, data = _fixture()
    reaction = data.records["support_reaction"]
    rows = [list(row) for row in reaction.values]
    rows[1][11] = 6.001
    rows[2][11] = 6_000_000.0
    reaction = _numeric(
        reaction.mapping, tuple(reaction.entity_ids), tuple(tuple(row) for row in rows)
    )
    force = data.records["tool_force"]
    force = _numeric(
        force.mapping,
        tuple(force.entity_ids),
        ((0.0, 0.0, 0.0), (0.0, 0.0, -6.0), (0.0, 0.0, -6_000_000.0)),
    )
    records = dict(data.records, support_reaction=reaction, tool_force=force)
    case = (revision, mesh, profile, _manifest(records, profile), _Data(records))
    result = next(row for row in _assess(case) if row.criterion_id == "quasistatic_equilibrium")
    assert result.status is AssessmentStatus.FAIL


def test_contact_uses_registered_projection_of_explicit_coordinate_selection() -> None:
    from febio_cae.domain import CoordinatePredicate, CoordinatePredicateRule

    revision, mesh, profile, manifest, data = _fixture()
    previous = revision.spec.contact.part_surface
    selected = replace(
        previous,
        rule=CoordinatePredicateRule(
            PART_BODY,
            WORLD,
            (
                CoordinatePredicate(
                    replace(revision.spec.motion.direction, z=1.0), "eq", Quantity(0, "m")
                ),
            ),
        ),
    )
    old_digest = hashlib.sha256(previous.to_bytes()).hexdigest()
    new_digest = hashlib.sha256(selected.to_bytes()).hexdigest()
    mesh = replace(
        mesh,
        provenance=replace(
            mesh.provenance,
            source_selection_digests=tuple(
                new_digest if value == old_digest else value
                for value in mesh.provenance.source_selection_digests
            ),
        ),
        sets=tuple(
            replace(item, source_selection_digest=new_digest)
            if item.source_selection_digest == old_digest
            else item
            for item in mesh.sets
        ),
    )
    revision = replace(
        revision,
        spec=replace(revision.spec, contact=replace(revision.spec.contact, part_surface=selected)),
    )
    rows = _assess((revision, mesh, profile, manifest, data))
    assert (
        next(row for row in rows if row.criterion_id == "contact_quality").status
        is AssessmentStatus.PASS
    )


def test_quadratic_contact_gap_cannot_hide_an_interior_extremum() -> None:
    case = _fixture()
    numeric = case[-1].records["part_displacement"]
    values = [list(row) for row in numeric.values]
    face = next(face for face in case[1].faces if face.body_id == PART_BODY.value)
    for node in face.node_ids[3:]:
        values[-1][numeric.entity_ids.index(str(node)) * 3 + 2] -= 0.9e-6
    updated = _replace_displacement(
        case, replace(numeric, values=tuple(tuple(row) for row in values))
    )
    contact = next(row for row in _assess(updated) if row.criterion_id == "contact_quality")
    assert contact.status is not AssessmentStatus.PASS


def test_normal_gap_alone_cannot_prove_contact_after_tangential_departure() -> None:
    case = _fixture()
    numeric = case[-1].records["part_displacement"]
    values = [list(row) for row in numeric.values]
    face = next(face for face in case[1].faces if face.body_id == PART_BODY.value)
    for node in face.node_ids:
        values[-1][numeric.entity_ids.index(str(node)) * 3] += 0.02
    updated = _replace_displacement(
        case, replace(numeric, values=tuple(tuple(row) for row in values))
    )
    contact = next(row for row in _assess(updated) if row.criterion_id == "contact_quality")
    assert contact.status is not AssessmentStatus.PASS


def test_missing_equilibrium_scope_cannot_hide_independent_motion_failure() -> None:
    case = _fixture()
    numeric = case[-1].records["part_displacement"]
    values = [list(row) for row in numeric.values]
    values[-1][len(_PART_NODES) * 3] = 0.0002
    revision, mesh, profile, manifest, data = _replace_displacement(
        case, replace(numeric, values=tuple(tuple(row) for row in values))
    )
    evaluations = tuple(
        replace(item, state_times=item.state_times[:-1])
        if item.evaluation_id == "ev_support_reaction"
        else item
        for item in revision.spec.outputs.evaluations
    )
    revision = replace(
        revision,
        spec=replace(
            revision.spec, outputs=replace(revision.spec.outputs, evaluations=evaluations)
        ),
    )
    rows = {
        row.criterion_id: row.status for row in _assess((revision, mesh, profile, manifest, data))
    }
    assert rows["motion_support_contact_fidelity"] is AssessmentStatus.FAIL
    assert rows["quasistatic_equilibrium"] is AssessmentStatus.UNVERIFIED


def test_evaluation_policy_must_cover_declared_motion_endpoint() -> None:
    revision, mesh, profile, manifest, data = _fixture()
    outputs = revision.spec.outputs
    revision = replace(
        revision,
        spec=replace(
            revision.spec,
            outputs=replace(
                outputs,
                saved_times=outputs.saved_times[:-1],
                evaluations=tuple(
                    replace(item, state_times=item.state_times[:-1]) for item in outputs.evaluations
                ),
            ),
        ),
    )
    rows = {
        row.criterion_id: row.status for row in _assess((revision, mesh, profile, manifest, data))
    }
    assert rows["motion_support_contact_fidelity"] is AssessmentStatus.UNVERIFIED
    assert rows["quasistatic_equilibrium"] is AssessmentStatus.UNVERIFIED


def test_canonical_contact_force_is_converted_back_for_applied_force_balance() -> None:
    revision, mesh, profile, _, data = _fixture()
    previous = data.records["tool_force"]
    mapping = replace(previous.mapping, raw_sign=-1)
    force = _numeric(
        mapping,
        tuple(previous.entity_ids),
        tuple(tuple(-value for value in row) for row in previous.values),
    )
    profile = replace(
        profile,
        output_mappings=tuple(
            mapping if item.canonical_id == mapping.canonical_id else item
            for item in profile.output_mappings
        ),
    )
    records = dict(data.records, tool_force=force)
    rows = _assess((revision, mesh, profile, _manifest(records, profile), _Data(records)))
    assert all(row.status is AssessmentStatus.PASS for row in rows)
