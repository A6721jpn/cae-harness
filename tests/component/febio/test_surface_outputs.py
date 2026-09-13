"""RED coverage for the native FEBio contact-output slice.

The bytes in this module are synthetic observations.  They exercise the
registered 0x35 block layout and canonical projection rules; they are not a
claim of real FEBio or native-model qualification.
"""

from __future__ import annotations

import hashlib
import struct
import sys
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from febio_cae.adapters.febio import QualityAdapter
from febio_cae.adapters.febio.compiler import CompilerAdapter, LocalBundleStore
from febio_cae.adapters.febio.xplt_reader import LocalResultDataStore, XpltReaderAdapter
from febio_cae.domain import (
    AssessmentStatus,
    AttemptRecord,
    EvaluationRequest,
    FileEntry,
    MeshSet,
    NumericResultData,
    OutputMapping,
    OutputRequest,
    PortError,
    ProcessIdentity,
    QualityCriterion,
    QualityThreshold,
    Quantity,
    ResolvedFileContent,
    RunState,
)
from febio_cae.domain.canonical import canonical_bytes
from febio_cae.domain.output_policy import OutputLocation

from .fixtures import evidence
from .test_compiler_native import _case

_CONTACT_SPECS = (
    # canonical ID, native name, location, value type, unit, side, component,
    # raw sign, canonical sign, native type, native storage format
    (
        "contact_nodal_gap",
        "nodal contact gap",
        "surface_node",
        "FLOAT",
        "m",
        "part",
        "value",
        1,
        1,
        0,
        2,
    ),
    (
        "contact_nodal_pressure",
        "nodal contact pressure",
        "surface_node",
        "FLOAT",
        "Pa",
        "part",
        "value",
        1,
        1,
        0,
        2,
    ),
    (
        "contact_nodal_traction",
        "nodal contact traction",
        "surface_node",
        "VEC3F",
        "Pa",
        "part",
        "z",
        1,
        -1,
        1,
        2,
    ),
    (
        "contact_area",
        "contact area",
        "surface",
        "FLOAT",
        "m2",
        "tool",
        "value",
        1,
        1,
        0,
        3,
    ),
    (
        "contact_surface_force",
        "contact force",
        "surface",
        "VEC3F",
        "N",
        "tool",
        "x",
        -1,
        1,
        1,
        3,
    ),
    (
        "contact_face_pressure",
        "contact pressure",
        "face",
        "FLOAT",
        "Pa",
        "part",
        "value",
        1,
        1,
        0,
        1,
    ),
    (
        "contact_status",
        "contact status",
        "face",
        "FLOAT",
        "1",
        "part",
        "value",
        1,
        1,
        0,
        1,
    ),
    (
        "contact_face_traction",
        "contact traction",
        "face",
        "VEC3F",
        "Pa",
        "part",
        "y",
        1,
        1,
        1,
        1,
    ),
)

_NATIVE_SURFACES = (
    (1, "part-contact", "part-face"),
    (2, "support-region", "part-face"),
    (3, "part-output-surface", "part-face"),
    (4, "tool-output-surface", "tool-face"),
    (5, "tool-auxiliary-surface", "tool-face"),
    (6, "tool-contact", "tool-face"),
)

_CONTACT_RAW_VALUES: dict[str, tuple[tuple[float, ...], tuple[float, ...]]] = {
    "contact_nodal_gap": (
        (
            0.1,
            0.2,
            0.3,
            0.4,
            0.5,
            0.6,
            0.1,
            0.25,
            0.35,
            0.45,
            0.55,
            0.65,
        ),
        (
            0.3,
            0.4,
            0.5,
            0.6,
            0.7,
            0.8,
            0.2,
            0.4,
            0.6,
            0.7,
            0.8,
            0.9,
        ),
    ),
    "contact_nodal_pressure": (
        (
            101.0,
            102.0,
            103.0,
            104.0,
            105.0,
            106.0,
            201.0,
            202.0,
            203.0,
            204.0,
            205.0,
            206.0,
        ),
        (
            201.0,
            202.0,
            203.0,
            204.0,
            205.0,
            206.0,
            301.0,
            302.0,
            303.0,
            304.0,
            305.0,
            306.0,
        ),
    ),
    "contact_nodal_traction": (
        tuple(float(value) for value in range(1, 19))
        + tuple(float(value) for value in range(101, 119)),
        tuple(float(value) for value in range(2, 20))
        + tuple(float(value) for value in range(102, 120)),
    ),
    "contact_area": ((0.1, 0.8), (0.9, 0.5)),
    "contact_surface_force": (
        (1.0, 2.0, 3.0, 2.0, 3.0, 4.0),
        (3.0, 4.0, 5.0, 4.0, 5.0, 6.0),
    ),
    "contact_face_pressure": ((10.0, 20.0), (25.0, 35.0)),
    "contact_status": ((0.0, 1.0), (1.0, 0.0)),
    "contact_face_traction": (
        (7.0, 8.0, 9.0, 17.0, 18.0, 19.0),
        (10.0, 11.0, 12.0, 20.0, 21.0, 22.0),
    ),
}

_OBSERVED_CONTACT_ANCILLARY_UNITS: dict[str, str | None] = {
    "contact_nodal_gap": "L",
    "contact_nodal_pressure": "P",
    "contact_nodal_traction": "P",
    "contact_area": "L^2",
    "contact_surface_force": "F",
    "contact_face_pressure": "P",
    "contact_status": None,
    "contact_face_traction": "P",
}


def block(identifier: int, payload: bytes) -> bytes:
    return struct.pack("<II", identifier, len(payload)) + payload


def uint(identifier: int, value: int) -> bytes:
    return block(identifier, struct.pack("<I", value))


def text(identifier: int, value: str) -> bytes:
    raw = value.encode("utf-8")
    return block(identifier, struct.pack("<I", len(raw)) + raw)


def fixed(identifier: int, value: str) -> bytes:
    raw = value.encode("utf-8") + b"\0"
    return block(identifier, raw + b"\xa5" * (64 - len(raw)))


def vector(identifier: int, values: tuple[float, ...]) -> bytes:
    return block(identifier, struct.pack("<" + "f" * len(values), *values))


def descriptor(name: str, value_type: int, storage_format: int, unit: str | None) -> bytes:
    ancillary = b"" if unit is None else fixed(0x01020007, unit)
    return (
        uint(0x01020002, value_type)
        + uint(0x01020003, storage_format)
        + uint(0x01020005, 0)
        + ancillary
        + fixed(0x01020004, name)
    )


def _surface_node_id(face_id: str, node_id: int) -> str:
    return canonical_bytes([face_id, node_id]).decode("utf-8")


def _contact_entities(revision: Any, mesh: Any) -> dict[str, tuple[str, ...]]:
    selections = {
        "part": revision.spec.contact.part_surface,
        "tool": revision.spec.contact.tool_surface,
    }
    faces = {face.face_id: face for face in mesh.faces}
    face_sets = {
        side: next(
            item for item in mesh.sets if item.set_id == selection.name and item.kind == "face"
        )
        for side, selection in selections.items()
    }
    result: dict[str, tuple[str, ...]] = {}
    face_ids = tuple(str(face_sets[item].member_ids[0]) for item in ("part", "tool"))
    for spec in _CONTACT_SPECS:
        canonical_id, _, location = spec[:3]
        if location == "surface_node":
            result[canonical_id] = tuple(
                _surface_node_id(face.face_id, node_id)
                for face_id in face_ids
                for face in (faces[face_id],)
                for node_id in face.node_ids
            )
        elif location == "surface":
            result[canonical_id] = tuple(face_sets[item].set_id for item in ("part", "tool"))
        else:
            result[canonical_id] = tuple(
                str(face_sets[item].member_ids[0]) for item in ("part", "tool")
            )
    return result


def native_bytes(
    mesh: Any, *, defect: str = "", observed_metadata: bool = False
) -> bytes:
    header = (
        uint(0x01010001, 0x35)
        + uint(0x01010004, 0)
        + text(0x01010006, "FEBio 4.12.0")
        + text(0x01010007, "SI")
    )
    node_dictionary = block(0x01020001, descriptor("displacement", 1, 0, "m"))
    domain_dictionary = block(0x01020001, descriptor("rigid force", 1, 3, "N"))
    contact_dictionary = b"".join(
        block(
            0x01020001,
            descriptor(
                spec[1],
                spec[9],
                spec[10],
                (
                    _OBSERVED_CONTACT_ANCILLARY_UNITS[spec[0]]
                    if observed_metadata
                    else (
                        "P"
                        if defect == "wrong-contact-unit"
                        and spec[0] == "contact_nodal_gap"
                        else spec[4]
                    )
                ),
            ),
        )
        for spec in _CONTACT_SPECS
    )
    if defect == "extra-dictionary":
        contact_dictionary += block(
            0x01020001, descriptor("unregistered contact output", 0, 1, "1")
        )
    dictionary = (
        block(0x01023000, node_dictionary)
        + block(0x01024000, domain_dictionary)
        + block(0x01025000, contact_dictionary)
    )
    root = block(0x01000000, block(0x01010000, header) + block(0x01020000, dictionary))

    nodes = tuple(mesh.nodes)
    node_index = {node.node_id: index for index, node in enumerate(nodes)}
    node_data = b"".join(struct.pack("<Ifff", node.node_id, *node.coordinates_si) for node in nodes)
    mesh_nodes = block(
        0x01041000,
        block(0x01041100, uint(0x01041101, len(nodes)) + uint(0x01041102, 3))
        + block(0x01041200, node_data),
    )

    domains = b""
    parts = b""
    for ordinal, element in enumerate(mesh.elements, 1):
        connectivity = tuple(node_index[node_id] for node_id in element.node_ids)
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
        parts += block(
            0x01045100,
            uint(0x01045101, ordinal) + fixed(0x01045102, element.body_id),
        )

    surface_records = list(_NATIVE_SURFACES)
    if defect == "foreign-surface":
        surface_records[0] = (1, "unregistered-contact", "part-face")
    elif defect == "duplicate-surface":
        surface_records[0] = (1, "tool-contact", "part-face")
    surfaces = b""
    # Six compiled surfaces are present, but contact data uses only the
    # non-contiguous native IDs 1 and 6.  Canonical projection must bind the
    # active records by surface identity, not assume every header is active.
    for native_id, name, face_id in surface_records:
        face = next(item for item in mesh.faces if item.face_id == face_id)
        indices = tuple(node_index[node_id] for node_id in face.node_ids)
        if defect == "surface-connectivity" and native_id == 1:
            indices = (len(nodes), *indices[1:])
        facet = struct.pack("<II6I", 1, 6, *indices)
        surface_header = (
            uint(0x01043102, native_id)
            + uint(0x01043103, 1)
            + text(0x01043104, name)
            + uint(0x01043105, 6)
        )
        surfaces += block(
            0x01043100,
            block(0x01043101, surface_header) + block(0x01043200, block(0x01043201, facet)),
        )
    mesh_bytes = block(
        0x01040000,
        mesh_nodes
        + block(0x01042000, domains)
        + block(0x01043000, surfaces)
        + block(0x01045000, parts),
    )

    def variable(index: int, region: int, values: tuple[float, ...]) -> bytes:
        return block(
            0x02020001,
            uint(0x02020002, index) + block(0x02020003, vector(region, values)),
        )

    states: list[bytes] = []
    for state_index, time_value in enumerate((0.0, 1.0)):
        node_values = tuple(
            value for node in nodes for value in (0.0, 0.0, time_value * node.node_id / 100.0)
        )
        domain_values = (0.0, 0.0, 10.0 + time_value)
        domain_fields = variable(1, 2, domain_values)
        contact_fields = b""
        for index, spec in enumerate(_CONTACT_SPECS, 1):
            canonical_id = spec[0]
            raw_values = _CONTACT_RAW_VALUES[canonical_id][state_index]
            if defect == "nonfinite-contact" and index == 1:
                raw_values = (float("nan"), *raw_values[1:])
            if defect == "wrong-contact-width" and index == 1:
                raw_values = raw_values[:-1]
            per_surface_width = (6 if spec[10] == 2 else 1) * (3 if spec[3] == "VEC3F" else 1)
            # Rows are registered canonically as part then tool.  Native
            # contact regions intentionally arrive as active ID 6 then ID 1;
            # compiled inactive surfaces 2 through 5 have no contact values.
            regions: tuple[tuple[int, tuple[float, ...]], ...] = (
                (6, raw_values[per_surface_width:]),
                (1, raw_values[:per_surface_width]),
            )
            if defect == "missing-contact-region" and index == 1:
                regions = ((6, raw_values[per_surface_width:]),)
            elif defect == "duplicate-contact-region" and index == 1:
                regions = (
                    (6, raw_values[per_surface_width:]),
                    (6, raw_values[per_surface_width:]),
                    (1, raw_values[:per_surface_width]),
                )
            elif defect == "substituted-contact-region" and index == 1:
                regions = (
                    (6, raw_values[per_surface_width:]),
                    (2, raw_values[:per_surface_width]),
                )
            contact_fields += block(
                0x02020001,
                uint(0x02020002, index)
                + block(
                    0x02020003,
                    b"".join(vector(region, values) for region, values in regions),
                ),
            )
        state_data = (
            block(0x02020300, variable(1, 0, node_values))
            + block(0x02020400, domain_fields)
            + block(0x02020500, contact_fields)
        )
        state = (
            block(0x02010000, vector(0x02010002, (time_value,)) + uint(0x02010003, 0))
            + block(0x02030000, block(0x02030001, struct.pack("<2I", 1, 1)))
            + block(0x02020000, state_data)
        )
        states.append(block(0x02000000, state))
    payload = struct.pack("<I", 0x00464542) + root + mesh_bytes + b"".join(states)
    return payload[:-1] if defect == "truncated-contact" else payload


def _surface_case(
    tmp_path: Path,
    *,
    quality: bool = False,
    defect: str = "",
    observed_metadata: bool = False,
) -> tuple[
    Any,
    Any,
    AttemptRecord,
    Any,
    Any,
    Any,
    LocalResultDataStore,
    LocalBundleStore,
]:
    revision, mesh, profile = _case()
    part_output = next(item for item in mesh.sets if item.set_id == "part-output")
    tool_output = next(item for item in mesh.sets if item.set_id == "tool-output")
    mesh = replace(
        mesh,
        sets=(
            *mesh.sets,
            MeshSet(
                "part-output-surface",
                "face",
                part_output.body_id,
                ("part-face",),
                part_output.source_selection_digest,
            ),
            MeshSet(
                "tool-output-surface",
                "face",
                tool_output.body_id,
                ("tool-face",),
                tool_output.source_selection_digest,
            ),
            MeshSet(
                "tool-auxiliary-surface",
                "face",
                tool_output.body_id,
                ("tool-face",),
                tool_output.source_selection_digest,
            ),
        ),
    )
    part_selection = revision.spec.contact.part_surface
    tool_selection = revision.spec.contact.tool_surface
    contact_requests = tuple(
        OutputRequest(
            request_id=spec[0],
            quantity_id=spec[0],
            measure_id="value",
            component_id=spec[6],
            location=cast(OutputLocation, spec[2]),
            selection=part_selection if spec[5] == "part" else tool_selection,
            frame=mesh.frame,
            display_unit=spec[4],
            evidence=evidence(f"outputs.requests.{spec[0]}", spec[0]),
        )
        for spec in _CONTACT_SPECS
    )
    outputs = replace(
        revision.spec.outputs,
        requests=(*revision.spec.outputs.requests, *contact_requests),
    )
    if quality:
        evaluations = (
            *outputs.evaluations,
            EvaluationRequest(
                "evaluation_contact_gap",
                "contact_nodal_gap",
                "peak",
                part_selection,
                (Quantity(0.0, "s"), Quantity(1.0, "s")),
                evidence("outputs.evaluations.evaluation_contact_gap", "contact-gap-evaluation"),
            ),
            EvaluationRequest(
                "evaluation_contact_area",
                "contact_area",
                "peak",
                tool_selection,
                (Quantity(0.0, "s"), Quantity(1.0, "s")),
                evidence("outputs.evaluations.evaluation_contact_area", "contact-area-evaluation"),
            ),
            EvaluationRequest(
                "evaluation_contact_pressure",
                "contact_face_pressure",
                "peak",
                part_selection,
                (Quantity(0.0, "s"), Quantity(1.0, "s")),
                evidence(
                    "outputs.evaluations.evaluation_contact_pressure",
                    "contact-pressure-evaluation",
                ),
            ),
        )
        outputs = replace(outputs, evaluations=evaluations)
    revision = replace(revision, spec=replace(revision.spec, outputs=outputs))

    contact_mappings = tuple(
        OutputMapping(
            spec[0],
            spec[1],
            spec[2],
            spec[3],
            spec[4],
            mesh.frame,
            spec[7],
            spec[8],
            "value",
        )
        for spec in _CONTACT_SPECS
    )
    profile = replace(profile, output_mappings=(*profile.output_mappings, *contact_mappings))
    if quality:
        criteria = tuple(
            QualityCriterion(
                criterion_id,
                "peak_abs_value",
                (evaluation_id,),
                (QualityThreshold("max_value", Quantity(limit, unit)),),
                "synthetic native contact-output quality evidence",
                evidence(f"quality_policy.criteria.{criterion_id}", criterion_id),
            )
            for criterion_id, evaluation_id, limit, unit in (
                ("criterion_contact_gap", "evaluation_contact_gap", 1.0, "m"),
                ("criterion_contact_area", "evaluation_contact_area", 1.0, "m2"),
                ("criterion_contact_pressure", "evaluation_contact_pressure", 100.0, "Pa"),
            )
        )
        revision = replace(
            revision,
            spec=replace(
                revision.spec,
                quality_policy=replace(revision.spec.quality_policy, criteria=criteria),
            ),
        )

    store = LocalBundleStore(tmp_path / "bundles")
    bundle = CompilerAdapter(store=store, executable=sys.executable).compile(
        revision, mesh, profile
    )
    attempt_root = tmp_path / "attempt"
    payload = native_bytes(mesh, defect=defect, observed_metadata=observed_metadata)
    output_path = attempt_root / "output/results.xplt"
    output_path.parent.mkdir(parents=True)
    output_path.write_bytes(payload)
    attempt = AttemptRecord(
        "surface-output-attempt",
        "surface-output-run",
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
            "synthetic-contact-output-source",
        ),
        bundle.settings,
    )
    data_store = LocalResultDataStore()
    entity_ids = {
        "displacement": tuple(str(node.node_id) for node in mesh.nodes),
        "contact_force": (mesh.elements[1].body_id,),
        **_contact_entities(revision, mesh),
    }
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
        state_times=(0.0, 1.0),
        part_bodies={1: mesh.elements[0].body_id, 2: mesh.elements[1].body_id},
        entity_ids=entity_ids,
    )
    reader = XpltReaderAdapter(profile=profile, data_store=data_store)
    return revision, reader, attempt, bundle, mesh, profile, data_store, store


def _f32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", value))[0]


def test_native_contact_outputs_compile_and_project_exact_entities(tmp_path: Path) -> None:
    from febio_cae.domain.results import surface_node_entity_id

    revision, reader, attempt, bundle, mesh, profile, data_store, store = _surface_case(tmp_path)
    input_root = ET.fromstring(store.resolve(bundle, "input/case.feb"))
    compiled_surfaces = input_root.findall("Mesh/Surface")
    assert {item.attrib["name"] for item in compiled_surfaces} == {
        name for _, name, _ in _NATIVE_SURFACES
    }
    assert len(compiled_surfaces) == 6
    assert all(len(surface) == 1 and surface[0].tag == "tri6" for surface in compiled_surfaces)
    expected_native_names = {
        "displacement",
        "rigid force",
        *(spec[1] for spec in _CONTACT_SPECS),
    }
    assert {
        item.attrib["type"] for item in input_root.findall("Output/plotfile/var")
    } == expected_native_names

    manifest = reader.read(attempt, bundle)
    assert manifest.read_result.status.value == "VALIDATED"
    observations = {item.output_id: item for item in manifest.read_result.observations}
    assert set(observations) == {
        "displacement",
        "contact_force",
        *(spec[0] for spec in _CONTACT_SPECS),
    }
    assert {
        output_id: (item.location, item.value_type, item.unit)
        for output_id, item in observations.items()
        if output_id in {spec[0] for spec in _CONTACT_SPECS}
    } == {spec[0]: (spec[2], spec[3], spec[4]) for spec in _CONTACT_SPECS}

    expected_entities = _contact_entities(revision, mesh)
    for spec in _CONTACT_SPECS:
        canonical_id, _, location, value_type, unit, _, _, raw_sign, canonical_sign, _, _ = spec
        numeric = data_store.resolve_manifest_output(manifest.manifest_id, canonical_id)
        assert isinstance(numeric, NumericResultData)
        assert numeric.mapping == profile.mapping_for(canonical_id)
        assert numeric.mapping.location == location
        assert numeric.mapping.value_type == value_type
        assert numeric.mapping.unit == unit
        assert numeric.entity_ids == expected_entities[canonical_id]
        assert numeric.component_ids == (("value",) if value_type == "FLOAT" else ("x", "y", "z"))
        sign = raw_sign * canonical_sign
        assert numeric.values == tuple(
            tuple(_f32(sign * value) for value in row) for row in _CONTACT_RAW_VALUES[canonical_id]
        )

    assert expected_entities["contact_area"] == ("part-contact", "tool-contact")
    assert expected_entities["contact_surface_force"] == ("part-contact", "tool-contact")
    assert expected_entities["contact_face_pressure"] == ("part-face", "tool-face")
    assert expected_entities["contact_nodal_gap"] == tuple(
        surface_node_entity_id(face.face_id, node_id)
        for face in mesh.faces
        for node_id in face.node_ids
    )
    assert len(expected_entities["contact_nodal_gap"]) == 12
    assert len(set(expected_entities["contact_nodal_gap"])) == 12
    assert profile.mapping_for("contact_force").native_name == "rigid force"
    assert profile.mapping_for("contact_surface_force").native_name == "contact force"
    assert profile.mapping_for("contact_force").canonical_id != "contact_surface_force"
    rigid = data_store.resolve_manifest_output(manifest.manifest_id, "contact_force")
    surface_force = data_store.resolve_manifest_output(
        manifest.manifest_id, "contact_surface_force"
    )
    assert rigid.values[-1] == (0.0, 0.0, -11.0)
    assert surface_force.values[-1] == (-3.0, -4.0, -5.0, -4.0, -5.0, -6.0)


def test_native_contact_dictionary_accepts_observed_ancillary_metadata(tmp_path: Path) -> None:
    _, reader, attempt, bundle, *_ = _surface_case(tmp_path, observed_metadata=True)

    manifest = reader.read(attempt, bundle)

    assert manifest.read_result.status.value == "VALIDATED"


def test_quality_evaluates_contact_surface_node_surface_and_face_scopes(tmp_path: Path) -> None:
    revision, reader, attempt, bundle, mesh, profile, data_store, _ = _surface_case(
        tmp_path, quality=True
    )
    manifest = reader.read(attempt, bundle)

    assessment = QualityAdapter().assess(
        manifest,
        revision,
        mesh,
        profile,
        data_store,
    )
    assert assessment.overall_status is AssessmentStatus.PASS
    criteria = {item.criterion_id: item for item in assessment.criteria}
    assert {item.status for item in criteria.values()} == {AssessmentStatus.PASS}
    measured = {item.criterion_id: item.measured[0].value for item in criteria.values()}
    assert measured["criterion_contact_gap"] == pytest.approx(0.8)
    # Tool-side peak precedes the endpoint (0.5); the part-side peak is 0.9.
    assert measured["criterion_contact_area"] == pytest.approx(0.8)
    assert measured["criterion_contact_pressure"] == pytest.approx(25.0)


@pytest.mark.parametrize(
    "defect",
    [
        "foreign-surface",
        "duplicate-surface",
        "surface-connectivity",
        "substituted-contact-region",
        "missing-contact-region",
        "duplicate-contact-region",
        "nonfinite-contact",
        "truncated-contact",
        "extra-dictionary",
        "wrong-contact-unit",
        "wrong-contact-width",
    ],
)
def test_contact_native_identity_and_layout_fail_closed(tmp_path: Path, defect: str) -> None:
    _, reader, attempt, bundle, *_ = _surface_case(tmp_path, defect=defect)
    with pytest.raises(PortError):
        reader.read(attempt, bundle)
