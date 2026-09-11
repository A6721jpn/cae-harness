"""One explicit synthetic compiler/reader/public-codec quality consumer fixture."""

from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass, replace
from pathlib import Path

from febio_cae.adapters.febio.compiler import CompilerAdapter, LocalBundleStore
from febio_cae.adapters.febio.xplt_reader import LocalResultDataStore, XpltReaderAdapter
from febio_cae.domain import (
    AttemptRecord,
    CaseRevision,
    CompatibilityProfile,
    FileEntry,
    MeshArtifact,
    MeshSet,
    NumericResultData,
    OutputMapping,
    ProcessIdentity,
    QualityThreshold,
    Quantity,
    ResolvedFileContent,
    ResultManifest,
    RunState,
)
from febio_cae.domain.codec import decode_record, encode_record

from .fixtures import evidence
from .reader_fixture import native_bytes
from .test_compiler_native import _case


@dataclass
class QualityCase:
    revision: CaseRevision
    mesh: MeshArtifact
    profile: CompatibilityProfile
    manifest: ResultManifest
    store: LocalResultDataStore

    def numeric(self, output_id: str = "displacement") -> NumericResultData:
        return self.store.resolve_manifest_output(self.manifest.manifest_id, output_id)

    def replace_numeric(self, numeric: NumericResultData, *, bind: bool = True) -> None:
        numeric = replace(
            numeric,
            reference=replace(
                numeric.reference,
                content_digest=numeric.expected_content_digest,
            ),
        )
        numeric = decode_record(encode_record(numeric), NumericResultData)
        # A new consumer store deliberately has no privileged reader internals.
        store = LocalResultDataStore()
        for observation in self.manifest.read_result.observations:
            value = (
                numeric
                if observation.output_id == numeric.mapping.canonical_id
                else (self.numeric(observation.output_id))
            )
            store.register(self.manifest.manifest_id, observation.output_id, value)
        self.store = store
        if bind:
            self.manifest = replace(
                self.manifest,
                read_result=replace(
                    self.manifest.read_result,
                    observations=tuple(
                        replace(
                            item,
                            data_ref=numeric.reference,
                            unit=numeric.mapping.unit,
                            state_count=len(numeric.axis_values),
                        )
                        if item.output_id == numeric.mapping.canonical_id
                        else item
                        for item in self.manifest.read_result.observations
                    ),
                ),
            )


def quality_case(
    tmp_path: Path,
    times: tuple[float, ...] = (0.0, 1.0),
    *,
    output_request_id: str = "request_part",
    limit: Quantity | None = None,
) -> QualityCase:
    revision, mesh, profile = _case()
    # Element identity must not accidentally equal a node ID in the ROI.
    mesh = replace(
        mesh,
        sets=tuple(
            replace(
                item, member_ids=tuple(71 if member == 1 else member for member in item.member_ids)
            )
            if item.kind == "element"
            else item
            for item in mesh.sets
        ),
        elements=tuple(
            replace(item, element_id=71) if item.element_id == 1 else item for item in mesh.elements
        ),
        faces=tuple(
            replace(
                item,
                adjacent_element_ids=tuple(
                    71 if identifier == 1 else identifier
                    for identifier in item.adjacent_element_ids
                ),
            )
            for item in mesh.faces
        ),
    )
    request = revision.spec.outputs.requests[0]
    stress = replace(
        request,
        request_id="request_stress",
        quantity_id="stress",
        location="element",
        component_id="xx",
        display_unit="Pa",
        evidence=evidence("outputs.requests.request_stress", "quality-stress"),
    )
    evaluation = revision.spec.outputs.evaluations[0]
    requests = (*revision.spec.outputs.requests, stress)
    selected = next(item for item in requests if item.request_id == output_request_id)
    if output_request_id != "request_part":
        assert limit is not None, "non-default output requires its explicit test threshold"
    policy = revision.spec.quality_policy
    if limit is not None:
        policy = replace(
            policy,
            criteria=(
                replace(
                    policy.criteria[0],
                    thresholds=(QualityThreshold("max_value", limit),),
                ),
            ),
        )
    revision = replace(
        revision,
        spec=replace(
            revision.spec,
            quality_policy=policy,
            outputs=replace(
                revision.spec.outputs,
                requests=requests,
                saved_times=tuple(Quantity(t, "s") for t in times),
                evaluations=(
                    replace(
                        evaluation,
                        output_request_id=output_request_id,
                        selection=selected.selection,
                        state_times=tuple(Quantity(t, "s") for t in times),
                    ),
                ),
            ),
        ),
    )
    mesh = replace(
        mesh,
        sets=tuple(
            replace(item, member_ids=(1, 2)) if item.set_id == "part-output" else item
            for item in mesh.sets
        ),
    )
    nodes = next(item for item in mesh.sets if item.set_id == "part-output")
    mesh = replace(
        mesh,
        sets=(
            *mesh.sets,
            MeshSet(
                "stress-elements",
                "element",
                nodes.body_id,
                (mesh.elements[0].element_id,),
                nodes.source_selection_digest,
            ),
        ),
    )
    profile = replace(
        profile,
        output_mappings=(
            *profile.output_mappings,
            OutputMapping(
                "stress",
                "stress",
                "element",
                "MAT3FS",
                "Pa",
                mesh.frame,
                1,
                1,
                "value",
            ),
        ),
    )
    bundle = CompilerAdapter(
        store=LocalBundleStore(tmp_path / "bundles"), executable=sys.executable
    ).compile(revision, mesh, profile)
    attempt = AttemptRecord(
        "quality-attempt",
        "quality-run",
        bundle.case_id,
        bundle.revision_id,
        1,
        bundle.bundle_digest,
        RunState.VALIDATING,
        ProcessIdentity(
            bundle.argv[0],
            bundle.tool.executable_digest,
            bundle.argv,
            str(tmp_path),
            bundle.thread_count,
            "synthetic-quality",
        ),
        bundle.settings,
    )
    content = native_bytes(mesh, times=times)
    store = LocalResultDataStore()
    store.register_source(
        attempt,
        bundle,
        ResolvedFileContent(
            FileEntry(
                "output/results.xplt", hashlib.sha256(content).hexdigest(), len(content), "result"
            ),
            content,
        ),
        mesh=mesh,
        state_times=times,
        part_bodies={1: mesh.elements[0].body_id, 2: mesh.elements[1].body_id},
        entity_ids={
            "displacement": tuple(str(n.node_id) for n in mesh.nodes),
            "stress": (str(mesh.elements[0].element_id),),
            "contact_force": (mesh.elements[1].body_id,),
        },
    )
    manifest = XpltReaderAdapter(profile=profile, data_store=store).read(attempt, bundle)
    consumer = LocalResultDataStore()
    for observation in manifest.read_result.observations:
        numeric = store.resolve_manifest_output(manifest.manifest_id, observation.output_id)
        consumer.register(
            manifest.manifest_id,
            observation.output_id,
            decode_record(encode_record(numeric), NumericResultData),
        )
    return QualityCase(revision, mesh, profile, manifest, consumer)


def signed_force_case(tmp_path: Path, tool_values: tuple[float, ...]) -> QualityCase:
    """Explicit synthetic canonical forces; no native force-sign qualification."""
    case = quality_case(tmp_path, times=(0.0, 0.5, 1.0))
    numeric = case.numeric()
    mapping = replace(numeric.mapping, unit="N")
    case.profile = replace(
        case.profile,
        output_mappings=tuple(
            mapping if item.canonical_id == mapping.canonical_id else item
            for item in case.profile.output_mappings
        ),
    )
    outputs = case.revision.spec.outputs
    part = replace(outputs.requests[0], display_unit="N")
    tool = next(item for item in outputs.requests if item.request_id == "request_tool")
    first = replace(outputs.evaluations[0], aggregation_id="sum")
    second = replace(
        first,
        evaluation_id="tool_sum",
        output_request_id=tool.request_id,
        selection=tool.selection,
        evidence=evidence("outputs.evaluations.tool_sum", "signed-force-arithmetic"),
    )
    criterion = replace(
        case.revision.spec.quality_policy.criteria[0],
        metric_id="signed_force_sum",
        evaluation_ids=(first.evaluation_id, second.evaluation_id),
        thresholds=(QualityThreshold("max_value", Quantity(0.1, "N")),),
    )
    case.revision = replace(
        case.revision,
        spec=replace(
            case.revision.spec,
            outputs=replace(
                outputs,
                requests=tuple(
                    part if r.request_id == part.request_id else r for r in outputs.requests
                ),
                evaluations=(first, second),
            ),
            quality_policy=replace(case.revision.spec.quality_policy, criteria=(criterion,)),
        ),
    )
    case.replace_numeric(
        replace(
            numeric,
            mapping=mapping,
            values=tuple(
                tuple(value for _ in numeric.entity_ids for value in (0.0, 0.0, 1.0))
                for _ in numeric.axis_values
            ),
        )
    )
    tool_numeric = case.numeric("contact_force")
    case.replace_numeric(
        replace(tool_numeric, values=tuple((0.0, 0.0, value) for value in tool_values))
    )
    return case
