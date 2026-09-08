"""Public CompilerPort semantics against documented FEB 4.0 vocabulary.

These synthetic meshes and XML assertions are not native FEBio qualification.
The P0 elastic-patch input supplies MeshDomains, control, plot and argv syntax;
official FEBio feature docs supply rigid BC/loadcurve/contact vocabulary.
"""

from __future__ import annotations

import hashlib
import sys
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path

import pytest

from febio_cae.adapters.febio.compiler import CompilerAdapter, LocalBundleStore
from febio_cae.domain import (
    CaseRevision,
    CompatibilityProfile,
    CompilerPort,
    CoulombFriction,
    ExecutionBundle,
    MeshArtifact,
    MeshNode,
    PortError,
    Quantity,
    RigidTransform,
    SolverControl,
    SpecifiedGap,
    Translation3,
)
from febio_cae.domain.spatial import ProperRotation

from .fixtures import evidence, make_mesh, make_profile, make_revision


def _case() -> tuple[CaseRevision, MeshArtifact, CompatibilityProfile]:
    revision = make_revision()
    controls = (
        SolverControl("max_refs", 30),
        SolverControl("dtol", Quantity(0.002, "1")),
        SolverControl("penalty", Quantity(7.5, "1")),
        SolverControl("auto_penalty", True),
        SolverControl("update_penalty", False),
        SolverControl("laugon", 1),
        SolverControl("tolerance", Quantity(0.15, "1")),
        SolverControl("gaptol", Quantity(0.02, "mm")),
        SolverControl("search_tol", Quantity(0.03, "1")),
        SolverControl("search_radius", Quantity(2, "mm")),
        SolverControl("two_pass", False),
    )
    spec = replace(
        revision.spec,
        solver_policy=replace(
            revision.spec.solver_policy,
            controls=controls,
            increments=replace(revision.spec.solver_policy.increments, max_steps=100),
        ),
    )
    revision = replace(revision, spec=spec)
    mesh = make_mesh(spec)
    # Two nondegenerate Tet10s, with oriented opposing faces and disjoint nodes.
    vertices = (
        ((0.0, 0.0, 0.0), (0.0, 0.01, 0.0), (0.01, 0.0, 0.0), (0.0, 0.0, -0.01)),
        ((0.0, 0.0, 0.001), (0.01, 0.0, 0.001), (0.0, 0.01, 0.001), (0.0, 0.0, 0.011)),
    )
    nodes: list[MeshNode] = []
    for offset, corners in zip((0, 10), vertices, strict=True):
        mids = tuple(
            tuple((corners[a][i] + corners[b][i]) / 2 for i in range(3))
            for a, b in ((0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3))
        )
        nodes.extend(MeshNode(offset + i + 1, xyz) for i, xyz in enumerate(corners + mids))
    mesh = replace(mesh, nodes=tuple(nodes))
    profile = make_profile()
    profile = replace(
        profile,
        output_mappings=tuple(
            replace(item, native_name="rigid force") if item.location == "rigid_body" else item
            for item in profile.output_mappings
        ),
    )
    return revision, mesh, profile


def _compile(
    tmp_path: Path, revision: CaseRevision, mesh: MeshArtifact, profile: CompatibilityProfile
) -> tuple[ExecutionBundle, ET.Element, bytes]:
    store = LocalBundleStore(tmp_path / "bundles")
    compiler: CompilerPort = CompilerAdapter(store=store, executable=sys.executable)
    bundle = compiler.compile(revision, mesh, profile)
    content = store.resolve(bundle, "input/case.feb")
    return bundle, ET.fromstring(content), content


def _required(root: ET.Element, path: str) -> ET.Element:
    item = root.find(path)
    assert item is not None, f"missing native FEB element: {path}"
    return item


def _integers(text: str | None) -> tuple[int, ...]:
    assert text is not None
    return tuple(int(value.strip()) for value in text.split(","))


def test_complete_two_body_compiler_port_emits_native_entities_and_references(
    tmp_path: Path,
) -> None:
    revision, mesh, profile = _case()
    bundle, root, content = _compile(tmp_path, revision, mesh, profile)
    assert root.attrib == {"version": "4.0"}
    assert _required(root, "Module").attrib == {"type": "solid"}
    assert root.findtext("Module/units") == "SI"
    assert _required(root, "Control/analysis").attrib == {"type": "static"}
    assert root.findtext("Control/solver/max_refs") == "30"
    assert root.find("Control/max_refs") is None
    assert root.findtext("Control/time_stepper/max_retries") == "1"
    assert float(root.findtext("Control/time_steps", "nan")) * float(
        root.findtext("Control/step_size", "nan")
    ) == pytest.approx(1)
    materials = {item.attrib["name"]: item for item in root.findall("Material/material")}
    groups = {item.attrib["name"]: item for item in root.findall("Mesh/Elements")}
    domains = root.findall("MeshDomains/SolidDomain")
    assert len(domains) == len(groups) == 2
    for domain in domains:
        assert not (domain.text or "").strip()
        elements = groups[domain.attrib["name"]]
        material = materials[domain.attrib["mat"]]
        ids = tuple(int(item.attrib["id"]) for item in elements)
        assert material.attrib["type"] == ("isotropic elastic" if ids == (1,) else "rigid body")
    emitted_nodes = {
        int(node.attrib["id"]): tuple(float(x) for x in (node.text or "").split(","))
        for node in root.findall("Mesh/Nodes/node")
    }
    assert emitted_nodes == {node.node_id: node.coordinates_si for node in mesh.nodes}
    for element in root.findall("Mesh/Elements/elem"):
        expected = next(
            item for item in mesh.elements if item.element_id == int(element.attrib["id"])
        )
        assert _integers(element.text) == tuple(expected.node_ids)
    surfaces = {item.attrib["name"]: item for item in root.findall("Mesh/Surface")}
    for surface in surfaces.values():
        assert all(item.tag == "tri6" and int(item.attrib["id"]) > 0 for item in surface)
    node_sets = {
        item.attrib["name"]: set(_integers(item.text)) for item in root.findall("Mesh/NodeSet")
    }
    support = _required(root, "Boundary/bc")
    assert support.attrib["type"] == "zero displacement"
    assert node_sets[support.attrib["node_set"]] == set(mesh.faces[0].node_ids)
    assert [support.findtext(f"{axis}_dof") for axis in "xyz"] == ["1", "1", "1"]
    pair = _required(root, "Mesh/SurfacePair")
    assert pair.findtext("primary") == "tool-contact"
    assert pair.findtext("secondary") == "part-contact"
    assert pair.findtext("primary") in surfaces and pair.findtext("secondary") in surfaces
    contact = _required(root, "Contact/contact")
    assert contact.attrib["surface_pair"] == pair.attrib["name"]
    assert contact.attrib["type"] == "sliding-elastic"
    fixed = _required(root, "Rigid/rigid_bc[@type='rigid_fixed']")
    assert materials[fixed.findtext("rb", "")].attrib["type"] == "rigid body"
    assert [
        fixed.findtext(axis)
        for axis in ("Rx_dof", "Ry_dof", "Rz_dof", "Ru_dof", "Rv_dof", "Rw_dof")
    ] == ["1", "1", "0", "1", "1", "1"]
    controllers = {item.attrib["id"]: item for item in root.findall("LoadData/load_controller")}
    for item in root.iter():
        if "lc" in item.attrib:
            assert item.attrib["lc"] in controllers
    assert all(int(key) > 0 for key in controllers)
    assert root.find("Loads/prescribed") is None
    plot = _required(root, "Output/plotfile")
    assert plot.attrib["type"] == "febio"
    assert {item.attrib["type"] for item in plot.findall("var")} == {"displacement", "rigid force"}
    assert all(not (item.text or "").strip() for item in plot.findall("var"))
    assert bundle.argv[1:] == (
        "-i",
        "input/case.feb",
        "-o",
        "output/solver.log",
        "-p",
        "output/results.xplt",
        "-noappend",
        "-noconfig",
    )
    assert bundle.files[0].digest == hashlib.sha256(content).hexdigest()
    settings = {item.name: item.value for item in bundle.settings}
    assert settings["feb_schema_version"] == "4.0"
    assert (
        settings["compatibility_profile_digest"] == hashlib.sha256(profile.to_bytes()).hexdigest()
    )
    assert bundle.spec_digest == revision.spec_digest and bundle.mesh_digest == mesh.artifact_digest
    max_curve = controllers[_required(root, "Control/time_stepper/dtmax").attrib["lc"]]
    assert [
        tuple(float(x) for x in (point.text or "").split(","))
        for point in max_curve.findall("points/point")
    ] == [(0.0, 1.0), (1.0, 1.0)]


def test_signed_rigid_motion_is_evaluated_in_the_declared_direction(tmp_path: Path) -> None:
    revision, mesh, profile = _case()
    revision = replace(
        revision,
        spec=replace(
            revision.spec,
            motion=replace(
                revision.spec.motion,
                direction=replace(revision.spec.motion.direction, z=-1),
            ),
        ),
    )
    _, root, _ = _compile(tmp_path, revision, mesh, profile)
    prescribed = _required(root, "Rigid/rigid_bc[@type='rigid_displacement']")
    assert prescribed.findtext("dof") == "z"
    assert prescribed.findtext("relative") == "0"
    value = _required(prescribed, "value")
    curve = _required(root, f"LoadData/load_controller[@id='{value.attrib['lc']}']")
    assert curve.findtext("interpolate") == "LINEAR"
    values = [
        tuple(float(x) for x in (p.text or "").split(",")) for p in curve.findall("points/point")
    ]
    assert values == [(0.0, 0.0), (1.0, 0.0001)]
    assert float(value.text or "nan") * values[-1][1] == pytest.approx(-0.0001)


def test_numeric_contact_and_renamed_resolved_sets_are_not_lost(tmp_path: Path) -> None:
    revision, mesh, profile = _case()
    friction = CoulombFriction(
        Quantity(0.37, "1"),
        evidence("contact.friction_model", "coulomb"),
        evidence("contact.friction_coefficient", "mu"),
    )
    revision = replace(
        revision,
        spec=replace(revision.spec, contact=replace(revision.spec.contact, friction=friction)),
    )
    mesh = replace(
        mesh, sets=tuple(replace(item, set_id="renamed-" + item.set_id) for item in mesh.sets)
    )
    _, root, _ = _compile(tmp_path, revision, mesh, profile)
    contact = _required(root, "Contact/contact")
    assert float(contact.findtext("fric_coeff", "nan")) == pytest.approx(0.37)
    assert float(contact.findtext("penalty", "nan")) == 7.5
    assert float(contact.findtext("search_radius", "nan")) == pytest.approx(0.002)
    assert float(contact.findtext("gaptol", "nan")) == pytest.approx(0.00002)
    assert contact.findtext("auto_penalty") == "1" and contact.findtext("two_pass") == "0"
    assert root.find("Control/penalty") is None
    pair = _required(root, "Mesh/SurfacePair")
    assert pair.findtext("primary") == "renamed-tool-contact"
    assert pair.findtext("secondary") == "renamed-part-contact"
    for item in mesh.sets:
        if item.kind in {"node", "element"}:
            tag = "NodeSet" if item.kind == "node" else "ElementSet"
            rendered = _required(root, f"Mesh/{tag}[@name='{item.set_id}']")
            assert _integers(rendered.text) == tuple(item.member_ids)
    nodes = {item.attrib["name"]: _integers(item.text) for item in root.findall("Mesh/NodeSet")}
    for bc in root.findall("Boundary/bc"):
        assert set(nodes[bc.attrib["node_set"]]) == set(mesh.faces[0].node_ids)


@pytest.mark.parametrize(
    "invalid",
    [
        "solver-version",
        "capability-version",
        "compression",
        "native-location",
        "unknown-control",
        "missing-penalty",
        "penalty-unit",
    ],
)
def test_unsupported_profile_and_numerics_fail_before_staging(tmp_path: Path, invalid: str) -> None:
    revision, mesh, profile = _case()
    if invalid == "solver-version":
        profile = replace(profile, solver=replace(profile.solver, version="99.0.0"))
    elif invalid in {"capability-version", "compression"}:
        capability = profile.capabilities[0]
        capability = (
            replace(capability, version="unknown")
            if invalid == "capability-version"
            else replace(capability, compression="zlib")
        )
        profile = replace(profile, capabilities=(capability, *profile.capabilities[1:]))
    elif invalid == "native-location":
        profile = replace(
            profile,
            output_mappings=tuple(
                replace(item, native_name="reaction forces")
                if item.location == "rigid_body"
                else item
                for item in profile.output_mappings
            ),
        )
    else:
        controls = tuple(revision.spec.solver_policy.controls)
        if invalid == "unknown-control":
            controls += (SolverControl("invented_native_control", 1),)
        elif invalid == "missing-penalty":
            controls = tuple(item for item in controls if item.name != "penalty")
        else:
            controls = tuple(
                replace(item, value=Quantity(7.5, "m")) if item.name == "penalty" else item
                for item in controls
            )
        revision = replace(
            revision,
            spec=replace(
                revision.spec, solver_policy=replace(revision.spec.solver_policy, controls=controls)
            ),
        )
    with pytest.raises(PortError):
        _compile(tmp_path, revision, mesh, profile)
    assert not list((tmp_path / "bundles").rglob("*.feb"))


def test_resolved_node_support_does_not_require_a_face_set(tmp_path: Path) -> None:
    revision, mesh, profile = _case()
    mesh = replace(
        mesh,
        sets=tuple(
            replace(item, kind="node", member_ids=mesh.faces[0].node_ids)
            if item.set_id == "support-region"
            else item
            for item in mesh.sets
        ),
    )
    _, root, _ = _compile(tmp_path, revision, mesh, profile)
    assert _required(root, "Boundary/bc").attrib["node_set"] == "support-region"


@pytest.mark.parametrize("condition", ["specified-gap", "rotated-support"])
def test_unrepresented_physical_conditions_are_not_silently_ignored(
    tmp_path: Path, condition: str
) -> None:
    revision, mesh, profile = _case()
    spec = revision.spec
    if condition == "specified-gap":
        arrangement = SpecifiedGap(
            Quantity(0.02, "mm"),
            spec.motion.direction,
            evidence("contact.arrangement", "specified-gap"),
            evidence("contact.gap", "gap"),
            evidence("contact.direction", "gap-direction"),
        )
        spec = replace(spec, contact=replace(spec.contact, arrangement=arrangement))
    else:
        support = spec.support.supports[0]
        transform = RigidTransform(
            support.frame,
            support.frame,
            Translation3(support.frame, Quantity(0, "m"), Quantity(0, "m"), Quantity(0, "m")),
            ProperRotation(((0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0))),
        )
        support = replace(
            support,
            transform=transform,
            transform_evidence=evidence("support.transform", "rotation"),
        )
        spec = replace(spec, support=replace(spec.support, supports=(support,)))
    with pytest.raises(PortError):
        _compile(tmp_path, replace(revision, spec=spec), mesh, profile)
