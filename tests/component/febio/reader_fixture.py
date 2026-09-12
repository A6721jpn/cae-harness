"""Synthetic 0x35 bytes independently laid out from same-V2 P0 observations.

No generated native artifact is stored here. Node/domain dictionaries, float32
times, region blocks and bounded object metadata follow the hash-pinned P0
elastic-patch/contact observations. The values below are invented test values.
"""

from __future__ import annotations

import hashlib
import struct
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from febio_cae.adapters.febio.compiler import CompilerAdapter, LocalBundleStore
from febio_cae.adapters.febio.xplt_reader import LocalResultDataStore, XpltReaderAdapter
from febio_cae.domain import (
    AttemptRecord,
    FileEntry,
    MeshArtifact,
    MeshSet,
    OutputMapping,
    ProcessIdentity,
    ResolvedFileContent,
    RunState,
)

from .fixtures import evidence
from .test_compiler_native import _case


def block(tag: int, payload: bytes) -> bytes:
    return struct.pack("<II", tag, len(payload)) + payload


def uint(tag: int, value: int) -> bytes:
    return block(tag, struct.pack("<I", value))


def text(tag: int, value: str) -> bytes:
    payload = value.encode()
    return block(tag, struct.pack("<I", len(payload)) + payload)


def fixed(tag: int, value: str) -> bytes:
    # Native fixed strings can contain nonzero uninitialized bytes after NUL.
    payload = value.encode() + b"\0"
    return block(tag, payload + b"\xa5" * (64 - len(payload)))


def vector(tag: int, values: tuple[float, ...]) -> bytes:
    return block(tag, struct.pack("<" + "f" * len(values), *values))


OBJECT_NAMES = (
    "Position",
    "Velocity",
    "Acceleration",
    "Euler angles (deg)",
    "Angular velocity",
    "Angular acceleration",
    "Force",
    "Moment",
)


def descriptor(name: str, value_type: int, storage_format: int) -> bytes:
    return (
        uint(0x01020002, value_type)
        + uint(0x01020003, storage_format)
        + uint(0x01020005, 0)
        + fixed(0x01020004, name)
    )


def native_bytes(
    mesh: MeshArtifact,
    *,
    times: tuple[float, ...] = (0.0, 1.0),
    defect: str = "",
    quality_outputs: bool = False,
) -> bytes:
    header = (
        uint(0x01010001, 0x99 if defect == "version" else 0x35)
        + uint(0x01010004, 1 if defect == "compression" else 0)
        + text(0x01010006, "FEBio 4.12.0")
        + text(0x01010007, "SI")
    )
    node_dictionary = block(0x01020001, descriptor("displacement", 1, 0))
    domain_dictionary = block(0x01020001, descriptor("stress", 2, 1)) + block(
        0x01020001, descriptor("rigid force", 1, 1 if defect == "rigid-layout" else 3)
    )
    if quality_outputs:
        node_dictionary += block(0x01020001, descriptor("reaction forces", 1, 0))
        domain_dictionary += block(0x01020001, descriptor("rigid position", 1, 3))
    dictionary = block(0x01023000, node_dictionary) + block(0x01024000, domain_dictionary)
    root = block(0x01000000, block(0x01010000, header) + block(0x01020000, dictionary))
    nodes = tuple(mesh.nodes)
    node_index = {node.node_id: index for index, node in enumerate(nodes)}
    node_data = b"".join(
        struct.pack(
            "<Ifff",
            999 if defect == "node-identity" and index == 0 else node.node_id,
            *node.coordinates_si,
        )
        for index, node in enumerate(nodes)
    )
    mesh_nodes = block(
        0x01041000,
        block(0x01041100, uint(0x01041101, len(nodes)) + uint(0x01041102, 3))
        + block(0x01041200, node_data),
    )
    domains = b""
    parts = b""
    for ordinal, element in enumerate(mesh.elements, 1):
        connectivity = tuple(node_index[node_id] for node_id in element.node_ids)
        if defect == "connectivity" and ordinal == 1:
            connectivity = (len(nodes), *connectivity[1:])
        domains += block(
            0x01042100,
            block(
                0x01042101,
                uint(0x01042102, 7)
                + uint(0x01042103, ordinal)
                + uint(0x01032104, 1)
                + text(0x01032105, f"domain-{ordinal}"),
            )
            + block(
                0x01042200,
                block(0x01042201, struct.pack("<11I", element.element_id, *connectivity)),
            ),
        )
        parts += block(0x01045100, uint(0x01045101, ordinal) + fixed(0x01045102, element.body_id))
    object_definition = (
        uint(0x01050001, 1)
        + text(0x01050002, "rigid_tool")
        + uint(0x01050003, 1)
        + vector(0x01050004, (0.0, 0.0, 0.0))
        + vector(0x01050005, (0.0, 0.0, 0.0, 1.0))
        + b"".join(block(0x01050006, descriptor(name, 1, 1)) for name in OBJECT_NAMES)
    )
    mesh_bytes = block(
        0x01040000,
        mesh_nodes
        + block(0x01042000, domains)
        + block(0x01045000, parts)
        + block(0x01050000, block(0x01051000, object_definition)),
    )
    states = []
    for state_index, time_value in enumerate(times):
        node_values = tuple(
            value for node in nodes for value in (0.0, 0.0, time_value * node.node_id / 100.0)
        )
        if defect == "nonfinite" and state_index == 0:
            node_values = (float("nan"), *node_values[1:])

        def variable(index: int, region: int, values: tuple[float, ...]) -> bytes:
            return block(
                0x02020001, uint(0x02020002, index) + block(0x02020003, vector(region, values))
            )

        node_fields = variable(1, 0, node_values)
        domain_fields = variable(1, 1, (time_value * 10.0, 2.0, 3.0, 4.0, 5.0, 6.0)) + variable(
            2, 99 if defect == "rigid-region" else 2, (0.0, 0.0, time_value * -2.0)
        )
        if quality_outputs:
            node_fields += variable(
                2,
                0,
                tuple(value for node in nodes for value in (0.0, 0.0, -time_value * node.node_id)),
            )
            domain_fields += variable(3, 2, (0.0, 0.0, -0.001 * time_value))
        state_data = block(0x02020300, node_fields) + block(0x02020400, domain_fields)
        object_state = (
            uint(0x01050001, 1)
            + vector(0x01050004, (0.0, 0.0, 0.0))
            + vector(0x01050005, (0.0, 0.0, 0.0, 1.0))
            + vector(0x01051001, (0.0, 0.0, 0.0))
            + block(0x01050006, b"".join(vector(index, (0.0, 0.0, 0.0)) for index in range(8)))
        )
        state = (
            block(0x02010000, vector(0x02010002, (time_value,)) + uint(0x02010003, 0))
            + block(0x02030000, block(0x02030001, struct.pack("<2I", 1, 1)))
            + block(0x02020000, state_data)
            + block(0x02040000, block(0x01051000, object_state))
        )
        if defect == "unknown-inner":
            state += block(0xDEADBEEF, b"")
        states.append(block(0xDEADBEEF if defect == "unknown-state" else 0x02000000, state))
    return struct.pack("<I", 0x00464542) + root + mesh_bytes + b"".join(states)


def setup_reader(
    tmp_path: Path,
    *,
    times: tuple[float, ...] = (0.0, 1.0),
    required_times: tuple[float, ...] | None = None,
    defect: str = "",
    register: bool = True,
    renumber: bool = False,
    quality_outputs: bool = False,
    revision_update: Any = None,
) -> tuple[Any, Any, Any, Any, Any]:
    revision, mesh, profile = _case()
    if renumber:
        node_ids = {node.node_id: 100 + node.node_id * 3 for node in mesh.nodes}
        element_ids = {1: 71, 2: 19}
        mesh = replace(
            mesh,
            nodes=tuple(replace(node, node_id=node_ids[node.node_id]) for node in mesh.nodes),
            elements=tuple(
                replace(
                    element,
                    element_id=element_ids[element.element_id],
                    node_ids=tuple(node_ids[n] for n in element.node_ids),
                )
                for element in mesh.elements
            ),
            faces=tuple(
                replace(
                    face,
                    node_ids=tuple(node_ids[n] for n in face.node_ids),
                    adjacent_element_ids=tuple(element_ids[e] for e in face.adjacent_element_ids),
                )
                for face in mesh.faces
            ),
            sets=tuple(
                replace(
                    item,
                    member_ids=tuple(
                        (node_ids if item.kind == "node" else element_ids)[int(n)]
                        for n in item.member_ids
                    ),
                )
                if item.kind in {"node", "element"}
                else item
                for item in mesh.sets
            ),
        )
    output = revision.spec.outputs.requests[0]
    stress_request = replace(
        output,
        request_id="request_stress",
        quantity_id="stress",
        location="element",
        component_id="xx",
        display_unit="Pa",
        evidence=evidence("outputs.requests.request_stress", "reader-stress"),
    )
    revision = replace(
        revision,
        spec=replace(
            revision.spec,
            outputs=replace(
                revision.spec.outputs, requests=(*revision.spec.outputs.requests, stress_request)
            ),
        ),
    )
    node_set = next(item for item in mesh.sets if item.set_id == "part-output")
    mesh = replace(
        mesh,
        sets=(
            *mesh.sets,
            MeshSet(
                "stress-elements",
                "element",
                node_set.body_id,
                (mesh.elements[0].element_id,),
                node_set.source_selection_digest,
            ),
        ),
    )
    profile = replace(
        profile,
        output_mappings=(
            *profile.output_mappings,
            OutputMapping("stress", "stress", "element", "MAT3FS", "Pa", mesh.frame, 1, 1, "value"),
        ),
    )
    if quality_outputs:
        force = next(
            item for item in revision.spec.outputs.requests if item.quantity_id == "contact_force"
        )
        revision = replace(
            revision,
            spec=replace(
                revision.spec,
                outputs=replace(
                    revision.spec.outputs,
                    requests=(
                        *revision.spec.outputs.requests,
                        replace(
                            output,
                            request_id="support_reaction",
                            quantity_id="reaction",
                            display_unit="N",
                            evidence=evidence(
                                "outputs.requests.support_reaction", "reader-reaction"
                            ),
                        ),
                        replace(
                            force,
                            request_id="tool_position",
                            quantity_id="rigid_position",
                            display_unit="m",
                            evidence=evidence("outputs.requests.tool_position", "reader-position"),
                        ),
                    ),
                ),
            ),
        )
        profile = replace(
            profile,
            output_mappings=(
                *profile.output_mappings,
                OutputMapping(
                    "reaction", "reaction forces", "node", "VEC3F", "N", mesh.frame, -1, 1, "value"
                ),
                OutputMapping(
                    "rigid_position",
                    "rigid position",
                    "rigid_body",
                    "VEC3F",
                    "m",
                    mesh.frame,
                    1,
                    1,
                    "value",
                ),
            ),
        )
    if revision_update is not None:
        revision = revision_update(revision)
    store = LocalBundleStore(tmp_path / "bundles")
    bundle = CompilerAdapter(store=store, executable=sys.executable).compile(
        revision, mesh, profile
    )
    attempt_root = tmp_path / "attempt"
    output_path = attempt_root / "output/results.xplt"
    output_path.parent.mkdir(parents=True)
    payload = native_bytes(mesh, times=times, defect=defect, quality_outputs=quality_outputs)
    output_path.write_bytes(payload)
    attempt = AttemptRecord(
        "reader-attempt",
        "reader-run",
        bundle.case_id,
        bundle.revision_id,
        1,
        bundle.bundle_digest,
        RunState.VALIDATING,
        ProcessIdentity(
            bundle.argv[0],
            bundle.tool.executable_digest,
            bundle.argv,
            str(attempt_root),
            bundle.thread_count,
            "synthetic-reader-source",
        ),
        bundle.settings,
    )
    data_store = LocalResultDataStore()
    if register:
        data_store.register_source(
            attempt,
            bundle,
            ResolvedFileContent(
                FileEntry(
                    "output/results.xplt",
                    hashlib.sha256(payload).hexdigest(),
                    len(payload),
                    "result",
                ),
                payload,
            ),
            mesh=mesh,
            state_times=times if required_times is None else required_times,
            part_bodies={1: mesh.elements[0].body_id, 2: mesh.elements[1].body_id},
            entity_ids={
                "displacement": tuple(str(node.node_id) for node in mesh.nodes),
                "stress": (str(mesh.elements[0].element_id),),
                "contact_force": (mesh.elements[1].body_id,),
                **(
                    {
                        "reaction": tuple(str(node.node_id) for node in mesh.nodes),
                        "rigid_position": (mesh.elements[1].body_id,),
                    }
                    if quality_outputs
                    else {}
                ),
            },
        )
    return XpltReaderAdapter(profile=profile, data_store=data_store), attempt, bundle, mesh, payload
