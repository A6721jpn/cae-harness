"""Deterministic, capability-gated FEBio input compilation."""

from __future__ import annotations

import hashlib
import math
import os
import tempfile
import xml.etree.ElementTree as ET
from itertools import pairwise
from pathlib import Path
from typing import Final, NoReturn, Protocol

from febio_cae.domain import (
    AsPlaced,
    CapabilityStatus,
    CaseRevision,
    CompatibilityProfile,
    CoulombFriction,
    ExecutionBundle,
    ExecutionSetting,
    FileEntry,
    Frictionless,
    IsotropicLinearElastic,
    MeshArtifact,
    PortError,
    PortErrorCategory,
    Quantity,
    SelectionRef,
)
from febio_cae.domain.artifacts import TET10_NODE_ORDER_ID, validate_logical_path
from febio_cae.domain.rigid_kinematics import check_translational_indentation_compatibility

from ._native_qualification import qualification_document
from .profile_scope import require_profile_scope

# This adapter dialect is deliberately separate from the solver's version.
_FEB_SCHEMA = "4.0"
_SOLVER_VERSION = "4.12.0"
_CAPABILITY_VERSION = "4.12.0/0x35"
_CONTACT_CONTROLS: Final[frozenset[str]] = frozenset(
    {
        "penalty",
        "auto_penalty",
        "update_penalty",
        "laugon",
        "tolerance",
        "gaptol",
        "search_tol",
        "search_radius",
        "two_pass",
    }
)
_OPTIONAL_CONTACT_CONTROLS = frozenset({"minaug", "maxaug"})
_QN_CONTROLS = frozenset({"max_ups"})

_SOLVER_CONTROLS: Final[frozenset[str]] = frozenset(
    {
        "reform_augment",
        "alpha",
        "dtol",
        "etol",
        "rtol",
        "max_refs",
        "diverge_reform",
        "reform_each_time_step",
        "min_residual",
        "symmetric_stiffness",
    }
)
_BOOLEAN_CONTROLS: Final[frozenset[str]] = frozenset(
    {
        "reform_augment",
        "auto_penalty",
        "update_penalty",
        "two_pass",
        "diverge_reform",
        "reform_each_time_step",
    }
)
_PLOT_VARIABLES: Final[dict[str, tuple[str, str, str]]] = {
    "displacement": ("node", "VEC3F", "m"),
    "reaction forces": ("node", "VEC3F", "N"),
    "rigid force": ("rigid_body", "VEC3F", "N"),
    "rigid position": ("rigid_body", "VEC3F", "m"),
    "stress": ("element", "MAT3FS", "Pa"),
}

_REQUIRED_CAPABILITIES: Final[frozenset[str]] = frozenset(
    {
        "febio.material.isotropic_linear_elastic",
        "febio.mesh.tet10",
        "febio.contact.sliding_elastic",
        "febio.rigid_body",
        "febio.support",
        "febio.motion",
        "febio.output.xplt",
    }
)


class BundleStore(Protocol):
    def stage(self, bundle_id: str, logical_path: str, content: bytes) -> None: ...


class LocalBundleStore:
    """Small byte store for component use; publication authority remains external."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, bundle_id: str, logical_path: str) -> Path:
        validate_logical_path(logical_path)
        candidate = (self.root / bundle_id / Path(*logical_path.split("/"))).resolve()
        try:
            candidate.relative_to((self.root / bundle_id).resolve())
        except ValueError as error:
            raise PortError(
                PortErrorCategory.INTEGRITY, "bundle path escapes its bundle root"
            ) from error
        return candidate

    def stage(self, bundle_id: str, logical_path: str, content: bytes) -> None:
        if not isinstance(content, bytes):
            raise TypeError("bundle content must be bytes")
        target = self._path(bundle_id, logical_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".stage-", dir=str(target.parent))
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, target)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def resolve(self, bundle: ExecutionBundle, logical_path: str) -> bytes:
        entries = {entry.logical_path: entry for entry in bundle.files}
        entry = entries.get(logical_path)
        if entry is None:
            raise PortError(
                PortErrorCategory.INTEGRITY, f"file is not registered in bundle: {logical_path}"
            )
        path = self._path(bundle.bundle_id, logical_path)
        if not path.is_file() or path.is_symlink():
            raise PortError(
                PortErrorCategory.INTEGRITY, f"staged bundle file is unavailable: {logical_path}"
            )
        content = path.read_bytes()
        if len(content) != entry.size_bytes or hashlib.sha256(content).hexdigest() != entry.digest:
            raise PortError(
                PortErrorCategory.INTEGRITY, f"staged bundle file digest mismatch: {logical_path}"
            )
        return content


class CompilerAdapter:
    """Compile common intent into one self-contained FEBio input bundle."""

    def __init__(self, *, store: BundleStore, executable: str | Path) -> None:
        self.store = store
        self.executable = str(Path(executable).resolve())

    def compile(
        self,
        revision: CaseRevision,
        mesh: MeshArtifact,
        profile: CompatibilityProfile,
    ) -> ExecutionBundle:
        self._validate(revision, mesh, profile)
        content = self._render(revision, mesh, profile)
        runtime_descriptor = qualification_document(profile.solver)
        identity_parts = [
            revision.spec_digest.encode(),
            mesh.artifact_digest.encode(),
            profile.to_bytes(),
            content,
            self.executable.encode(),
        ]
        if runtime_descriptor is not None:
            identity_parts.append(runtime_descriptor)
        identity = hashlib.sha256(b"|".join(identity_parts)).hexdigest()
        bundle_id = f"bundle-{identity[:24]}"
        files = [
            FileEntry("input/case.feb", hashlib.sha256(content).hexdigest(), len(content), "input")
        ]
        if runtime_descriptor is not None:
            files.append(
                FileEntry(
                    "input/native-runtime.json",
                    hashlib.sha256(runtime_descriptor).hexdigest(),
                    len(runtime_descriptor),
                    "native-runtime",
                )
            )
        bundle = ExecutionBundle(
            bundle_id=bundle_id,
            case_id=revision.case_id,
            revision_id=revision.revision_id,
            spec_digest=revision.spec_digest,
            mesh_digest=mesh.artifact_digest,
            profile_id=profile.profile_id,
            tool=profile.solver,
            files=tuple(files),
            argv=(
                self.executable,
                "-i",
                "input/case.feb",
                "-o",
                "output/solver.log",
                "-p",
                "output/results.xplt",
                "-noappend",
                "-noconfig",
            ),
            cwd=str(getattr(self.store, "root", Path.cwd())),
            thread_count=1,
            settings=(
                ExecutionSetting("solver_threads", 1),
                ExecutionSetting("xplt_version", "0x35"),
                ExecutionSetting("feb_schema_version", _FEB_SCHEMA),
                ExecutionSetting(
                    "compatibility_profile_digest", hashlib.sha256(profile.to_bytes()).hexdigest()
                ),
                ExecutionSetting("compiler_dialect", "feb4-static-tet10-v1"),
                ExecutionSetting("output_plot_path", "output/results.xplt"),
                ExecutionSetting("output_log_path", "output/solver.log"),
            ),
        )
        self.store.stage(bundle.bundle_id, files[0].logical_path, content)
        if runtime_descriptor is not None:
            self.store.stage(bundle.bundle_id, "input/native-runtime.json", runtime_descriptor)
        return bundle

    def _validate(
        self, revision: CaseRevision, mesh: MeshArtifact, profile: CompatibilityProfile
    ) -> None:
        if not isinstance(revision, CaseRevision) or not isinstance(mesh, MeshArtifact):
            raise PortError(
                PortErrorCategory.INVALID_INPUT, "revision and mesh must be common records"
            )
        if not isinstance(profile, CompatibilityProfile):
            raise PortError(
                PortErrorCategory.INVALID_INPUT, "profile must be a CompatibilityProfile"
            )
        require_profile_scope(revision.spec, profile)
        if profile.solver.version != _SOLVER_VERSION:
            self._unsupported("solver version is not supported by the FEB 4.0 dialect")
        if not profile.evidence:
            self._unsupported("compatibility profile has no registration evidence")
        if not isinstance(revision.spec.material, IsotropicLinearElastic):
            self._unsupported("material model is not enabled by this compiler")
        if not isinstance(revision.spec.contact.arrangement, AsPlaced):
            self._unsupported(
                "specified-gap arrangement requires verified placement provenance not available to this compiler"
            )
        if mesh.frame != revision.spec.geometry.placement.target_frame:
            self._unsupported("mesh and physical conditions must use the same frame")
        compatibility = check_translational_indentation_compatibility(
            revision.spec.rigid_tool, revision.spec.motion
        )
        if not compatibility.supported:
            self._unsupported(compatibility.reason or "unsupported rigid motion")
        self._validate_controls(revision)
        self._timeline(revision)
        if mesh.provenance.source_geometry_digest != revision.spec.geometry.geometry_digest:
            raise PortError(
                PortErrorCategory.INTEGRITY, "mesh geometry identity does not match revision"
            )
        if mesh.provenance.node_ordering_id != TET10_NODE_ORDER_ID:
            raise PortError(
                PortErrorCategory.UNSUPPORTED_CAPABILITY, "mesh is not the canonical Tet10 ordering"
            )
        if any(element.element_type != "tet10" for element in mesh.elements):
            raise PortError(
                PortErrorCategory.UNSUPPORTED_CAPABILITY, "all compiled elements must be Tet10"
            )
        part_body = revision.spec.geometry.body_id.value
        tool_body = revision.spec.rigid_tool.primitive.body_id.value
        if part_body == tool_body:
            raise PortError(
                PortErrorCategory.INTEGRITY, "part and rigid-tool bodies must be distinct"
            )
        source_bodies = set(mesh.provenance.source_body_ids)
        if source_bodies != {part_body, tool_body}:
            raise PortError(
                PortErrorCategory.INTEGRITY,
                "mesh provenance does not contain both declared part and tool bodies",
            )
        mesh_bodies = {element.body_id for element in mesh.elements}
        if mesh_bodies != {part_body, tool_body}:
            raise PortError(
                PortErrorCategory.INTEGRITY,
                "mesh does not contain separate part and rigid-tool element ownership",
            )
        part_nodes = {
            node
            for element in mesh.elements
            if element.body_id == part_body
            for node in element.node_ids
        }
        tool_nodes = {
            node
            for element in mesh.elements
            if element.body_id == tool_body
            for node in element.node_ids
        }
        if part_nodes & tool_nodes:
            raise PortError(PortErrorCategory.INTEGRITY, "part and rigid tool must not share nodes")
        for support in revision.spec.support.supports:
            if support.frame != mesh.frame or support.transform is not None:
                self._unsupported("transformed support conditions are not enabled by this dialect")
        selections = (
            revision.spec.contact.part_surface,
            revision.spec.contact.tool_surface,
            *(support.selection for support in revision.spec.support.supports),
            *(request.selection for request in revision.spec.outputs.requests),
        )
        mesh_sets = tuple(mesh.sets)
        for selection in selections:
            selection_digest = hashlib.sha256(selection.to_bytes()).hexdigest()
            matching = [
                item for item in mesh_sets if item.source_selection_digest == selection_digest
            ]
            if not matching or any(item.body_id != selection.body_id.value for item in matching):
                raise PortError(
                    PortErrorCategory.INTEGRITY,
                    f"mesh selection mapping is missing or crosses body ownership: {selection.name}",
                )
        capabilities = {item.capability_id: item for item in profile.capabilities}
        required = _REQUIRED_CAPABILITIES | {self._contact_direction(profile)}
        missing = sorted(required - capabilities.keys())
        unavailable = sorted(
            capability_id
            for capability_id in required
            if capability_id in capabilities
            and capabilities[capability_id].status is not CapabilityStatus.SUPPORTED
        )
        if missing or unavailable:
            detail = ", ".join((*missing, *unavailable))
            raise PortError(
                PortErrorCategory.UNSUPPORTED_CAPABILITY,
                f"required capabilities unavailable: {detail}",
            )
        for capability_id in required:
            capability = capabilities[capability_id]
            if (
                capability.version != _CAPABILITY_VERSION
                or capability.compression != "none"
                or capability.ordering_id != TET10_NODE_ORDER_ID
                or not capability.evidence
            ):
                self._unsupported(f"unsupported capability profile declaration: {capability_id}")
        for request in revision.spec.outputs.requests:
            try:
                mapping = profile.mapping_for(request.quantity_id)
            except ValueError as error:
                raise PortError(
                    PortErrorCategory.UNSUPPORTED_CAPABILITY,
                    f"no output mapping for {request.quantity_id}",
                ) from error
            if mapping.location != request.location:
                raise PortError(
                    PortErrorCategory.UNSUPPORTED_CAPABILITY,
                    f"output location mismatch for {request.request_id}: {mapping.location} != {request.location}",
                )
            native = _PLOT_VARIABLES.get(mapping.native_name)
            if native != (mapping.location, mapping.value_type, mapping.unit):
                self._unsupported(
                    f"unsupported native output mapping: {mapping.native_name}/{mapping.location}"
                )
            if (
                mapping.frame != mesh.frame
                or request.frame != mesh.frame
                or mapping.measure_id != request.measure_id
            ):
                self._unsupported(f"output frame or measure mismatch: {request.request_id}")
            if mapping.location == "rigid_body" and request.selection.body_id.value != tool_body:
                self._unsupported("rigid output must identify the rigid-tool body")

    def _contact_direction(self, profile: CompatibilityProfile) -> str:
        directions = [
            item.capability_id
            for item in profile.capabilities
            if item.capability_id
            in {
                "febio.contact.primary_tool_secondary_part",
                "febio.contact.primary_part_secondary_tool",
            }
        ]
        if len(directions) != 1:
            self._unsupported("exactly one explicit contact direction capability is required")
        return directions[0]

    @staticmethod
    def _unsupported(reason: str) -> NoReturn:
        raise PortError(PortErrorCategory.UNSUPPORTED_CAPABILITY, reason)

    def _validate_controls(self, revision: CaseRevision) -> None:
        controls = {item.name: item.value for item in revision.spec.solver_policy.controls}
        missing = _CONTACT_CONTROLS - controls.keys()
        unknown = controls.keys() - (
            _CONTACT_CONTROLS | _OPTIONAL_CONTACT_CONTROLS | _SOLVER_CONTROLS | _QN_CONTROLS
        )
        if missing or unknown:
            self._unsupported(
                f"explicit supported contact/solver controls required; missing={sorted(missing)}, unknown={sorted(unknown)}"
            )
        for name, value in controls.items():
            if name in _BOOLEAN_CONTROLS:
                if not isinstance(value, bool):
                    self._unsupported(f"{name} requires an explicit boolean")
            elif name in {
                "laugon",
                "max_refs",
                "minaug",
                "maxaug",
                "max_ups",
                "symmetric_stiffness",
            }:
                if (
                    type(value) is not int
                    or value < 0
                    or (name == "laugon" and value not in {0, 1})
                    or (name == "maxaug" and value == 0)
                ):
                    self._unsupported(f"{name} requires a supported integer")
            else:
                expected_unit = "m" if name in {"gaptol", "search_radius"} else "1"
                if isinstance(value, bool) or not isinstance(value, (Quantity, int)):
                    self._unsupported(f"{name} requires numeric data")
                if isinstance(value, Quantity):
                    if value.dimension != Quantity(1, expected_unit).dimension:
                        self._unsupported(f"{name} requires units compatible with {expected_unit}")
                    number = value.to_si().value
                else:
                    if expected_unit != "1":
                        self._unsupported(f"{name} requires a length Quantity")
                    number = value
                if number < 0 or (name in {"penalty", "search_radius"} and number == 0):
                    self._unsupported(
                        f"{name} must be nonnegative (penalty/radius strictly positive)"
                    )
        if (
            "minaug" in controls
            and "maxaug" in controls
            and isinstance(controls["minaug"], int)
            and isinstance(controls["maxaug"], int)
            and controls["minaug"] > controls["maxaug"]
        ):
            self._unsupported("minaug exceeds maxaug")
        has_zero_max_ups = controls.get("max_ups") == 0
        has_symmetric_stiffness = "symmetric_stiffness" in controls
        if (has_zero_max_ups or has_symmetric_stiffness) and (
            type(controls.get("max_ups")) is not int
            or controls["max_ups"] != 0
            or type(controls.get("symmetric_stiffness")) is not int
            or controls["symmetric_stiffness"] != 0
        ):
            self._unsupported(
                "nonsymmetric full Newton requires max_ups=0 and symmetric_stiffness=0"
            )
        if controls["auto_penalty"] is not True:
            self._unsupported(
                "this dialect requires explicit auto_penalty=true and a dimensionless penalty factor"
            )

    def _timeline(self, revision: CaseRevision) -> tuple[int, tuple[float, ...]]:
        spec = revision.spec
        increments = spec.solver_policy.increments
        start = spec.motion.samples[0].time.to_si().value
        end = spec.motion.samples[-1].time.to_si().value
        if start != 0:
            self._unsupported(
                "this single-step dialect requires the motion timeline to start at zero"
            )
        initial = increments.initial_step.to_si().value
        steps = round(end / initial)
        if steps < 1 or not math.isclose(steps * initial, end, rel_tol=1e-12, abs_tol=0):
            self._unsupported("motion duration must be an integer multiple of initial_step")
        points = tuple(
            sorted(
                {
                    *(sample.time.to_si().value for sample in spec.motion.samples),
                    *(point.to_si().value for point in increments.must_points),
                    *(point.to_si().value for point in spec.outputs.saved_times),
                }
            )
        )
        if any(point < 0 or point > end for point in points):
            self._unsupported("required state or must-point is outside the motion timeline")
        if increments.adaptive:
            minimum = increments.minimum_step.to_si().value
            # A final fragment at each must-point may be smaller than dtmin.
            bound = sum(math.ceil((b - a) / minimum - 1e-12) for a, b in pairwise(points))
            if bound > increments.max_steps:
                self._unsupported("adaptive dtmin/must-points cannot honor the max_steps bound")
        else:
            if steps > increments.max_steps or any(
                not math.isclose(
                    round(point / initial) * initial, point, rel_tol=1e-12, abs_tol=1e-15
                )
                for point in points
            ):
                self._unsupported("fixed time grid cannot honor required state times or max_steps")
        return steps, points

    def _render(
        self, revision: CaseRevision, mesh: MeshArtifact, profile: CompatibilityProfile
    ) -> bytes:
        spec = revision.spec
        root = ET.Element("febio_spec", {"version": _FEB_SCHEMA})
        module = ET.SubElement(root, "Module", {"type": "solid"})
        ET.SubElement(module, "units").text = "SI"
        control = ET.SubElement(root, "Control")
        steps, must_points = self._timeline(revision)
        ET.SubElement(control, "analysis", {"type": "static"})
        ET.SubElement(control, "time_steps").text = str(steps)
        ET.SubElement(control, "step_size").text = self._quantity(
            spec.solver_policy.increments.initial_step
        )
        ET.SubElement(control, "plot_level").text = "PLOT_MAJOR_ITRS"
        ET.SubElement(control, "plot_zero_state").text = "1"
        if spec.solver_policy.increments.adaptive:
            stepper = ET.SubElement(control, "time_stepper", {"type": "default"})
            ET.SubElement(stepper, "max_retries").text = str(
                spec.solver_policy.increments.max_step_retries
            )
            ET.SubElement(stepper, "dtmin").text = self._quantity(
                spec.solver_policy.increments.minimum_step
            )
            ET.SubElement(stepper, "dtmax", {"lc": "2"}).text = self._quantity(
                spec.solver_policy.increments.maximum_step
            )
        solver = ET.SubElement(control, "solver", {"type": "solid"})
        for setting in spec.solver_policy.controls:
            if setting.name in _SOLVER_CONTROLS:
                ET.SubElement(solver, setting.name).text = self._control_value(setting.value)

        for setting in spec.solver_policy.controls:
            if setting.name == "max_ups":
                qn_type = "Broyden" if setting.value == 0 else "BFGS"
                qn = ET.SubElement(solver, "qn_method", {"type": qn_type})
                ET.SubElement(qn, "max_ups").text = self._control_value(setting.value)

        materials = ET.SubElement(root, "Material")
        if isinstance(spec.material, IsotropicLinearElastic):
            material = ET.SubElement(
                materials,
                "material",
                {"id": "1", "name": "part-material", "type": "isotropic elastic"},
            )
            ET.SubElement(material, "E").text = self._quantity(spec.material.youngs_modulus)
            ET.SubElement(material, "v").text = self._quantity(spec.material.poisson_ratio)
        else:
            raise PortError(
                PortErrorCategory.UNSUPPORTED_CAPABILITY,
                "material model is not enabled by this compiler",
            )
        ET.SubElement(
            materials,
            "material",
            {"id": "2", "name": "tool-rigid", "type": "rigid body"},
        )

        mesh_section = ET.SubElement(root, "Mesh")
        part_body = spec.geometry.body_id.value
        tool_body = spec.rigid_tool.primitive.body_id.value
        # Elements define implicit element sets; never collide with registered sets.
        allocated = {item.set_id for item in mesh.sets}
        group_names = {
            part_body: self._allocate_name("compiled-part-elements", allocated),
            tool_body: self._allocate_name("compiled-tool-elements", allocated),
        }
        body_node_ids = {
            body: {
                node_id
                for element in mesh.elements
                if element.body_id == body
                for node_id in element.node_ids
            }
            for body in (part_body, tool_body)
        }
        for body in (part_body, tool_body):
            # Names define implicit node sets; these groups need no references.
            # Leave them unnamed so registered/generated subsets stay unambiguous.
            nodes = ET.SubElement(mesh_section, "Nodes")
            for node in mesh.nodes:
                if node.node_id in body_node_ids[body]:
                    ET.SubElement(nodes, "node", {"id": str(node.node_id)}).text = ",".join(
                        self._float(value) for value in node.coordinates_si
                    )
        for body, name in group_names.items():
            elements = ET.SubElement(mesh_section, "Elements", {"type": "tet10", "name": name})
            for element in mesh.elements:
                if element.body_id == body:
                    ET.SubElement(
                        elements, "elem", {"id": str(element.element_id)}
                    ).text = ",".join(str(value) for value in element.node_ids)

        resolved = self._resolved_set_ids(revision, mesh)
        for item in mesh.sets:
            if item.kind == "face":
                surface = ET.SubElement(mesh_section, "Surface", {"name": item.set_id})
                for facet_index, face_id in enumerate(item.member_ids, 1):
                    face = next(
                        (candidate for candidate in mesh.faces if candidate.face_id == face_id),
                        None,
                    )
                    if face is None:
                        raise PortError(
                            PortErrorCategory.INTEGRITY,
                            f"mesh face set member is missing: {face_id}",
                        )
                    ET.SubElement(surface, "tri6", {"id": str(facet_index)}).text = ",".join(
                        str(value) for value in face.node_ids
                    )
            elif item.kind in {"node", "element"}:
                set_tag = "NodeSet" if item.kind == "node" else "ElementSet"
                set_element = ET.SubElement(mesh_section, set_tag, {"name": item.set_id})
                set_element.text = ",".join(str(member) for member in item.member_ids)
        registered_node_sets = {item.set_id for item in mesh.sets if item.kind == "node"}
        for (digest, kind), set_id in resolved.items():
            if kind != "node" or set_id in registered_node_sets:
                continue
            item = next(
                item
                for item in mesh.sets
                if item.kind == "face" and item.source_selection_digest == digest
            )
            node_members = sorted(
                {
                    node_id
                    for face_id in item.member_ids
                    for face in mesh.faces
                    if face.face_id == face_id
                    for node_id in face.node_ids
                }
            )
            node_set = ET.SubElement(mesh_section, "NodeSet", {"name": set_id})
            node_set.text = ",".join(str(node) for node in node_members)
            registered_node_sets.add(set_id)

        pair_name = self._allocate_name("compiled-contact-pair", allocated)
        pair = ET.SubElement(mesh_section, "SurfacePair", {"name": pair_name})
        primary, secondary = spec.contact.tool_surface, spec.contact.part_surface
        if self._contact_direction(profile) == "febio.contact.primary_part_secondary_tool":
            primary, secondary = secondary, primary
        ET.SubElement(pair, "primary").text = resolved[(self._selection_digest(primary), "face")]
        ET.SubElement(pair, "secondary").text = resolved[
            (self._selection_digest(secondary), "face")
        ]

        domains = ET.SubElement(root, "MeshDomains")
        ET.SubElement(
            domains,
            "SolidDomain",
            {"name": group_names[part_body], "mat": "part-material"},
        )
        ET.SubElement(
            domains,
            "SolidDomain",
            {"name": group_names[tool_body], "mat": "tool-rigid"},
        )

        boundary = ET.SubElement(root, "Boundary")
        for support in spec.support.supports:
            support_node_set_id: str | None = None
            for axis in ("x", "y", "z"):
                if getattr(support, axis).state != "fixed":
                    continue
                if support_node_set_id is None:
                    support_node_set_id = resolved[
                        (self._selection_digest(support.selection), "node")
                    ]
                fixed = ET.SubElement(
                    boundary,
                    "bc",
                    {
                        "type": "prescribed displacement",
                        "name": self._allocate_name(
                            f"{support.support_id.value}-{axis}", allocated
                        ),
                        "node_set": support_node_set_id,
                    },
                )
                ET.SubElement(fixed, "dof").text = axis
                ET.SubElement(fixed, "value", {"lc": "1"}).text = "0"
                ET.SubElement(fixed, "relative").text = "0"
        rigid = ET.SubElement(root, "Rigid")
        fixed_rigid = ET.SubElement(rigid, "rigid_bc", {"type": "rigid_fixed"})
        ET.SubElement(fixed_rigid, "rb").text = "tool-rigid"
        for axis, native_dof in zip(
            ("x", "y", "z", "rx", "ry", "rz"),
            ("Rx_dof", "Ry_dof", "Rz_dof", "Ru_dof", "Rv_dof", "Rw_dof"),
            strict=True,
        ):
            ET.SubElement(fixed_rigid, native_dof).text = (
                "1" if getattr(spec.rigid_tool.dofs, axis).state == "fixed" else "0"
            )
        for axis in ("x", "y", "z"):
            if getattr(spec.rigid_tool.dofs, axis).state == "prescribed":
                prescribed = ET.SubElement(rigid, "rigid_bc", {"type": "rigid_displacement"})
                ET.SubElement(prescribed, "rb").text = "tool-rigid"
                ET.SubElement(prescribed, "dof").text = axis
                ET.SubElement(prescribed, "value", {"lc": "1"}).text = self._float(
                    getattr(spec.motion.direction, axis)
                )
                ET.SubElement(prescribed, "relative").text = "0"
        contacts = ET.SubElement(root, "Contact")
        contact = ET.SubElement(
            contacts,
            "contact",
            {
                "type": "sliding-elastic",
                "name": spec.contact.contact_id.value,
                "surface_pair": pair_name,
            },
        )
        if isinstance(spec.contact.friction, Frictionless):
            friction_value = "0"
        elif isinstance(spec.contact.friction, CoulombFriction):
            friction_value = self._quantity(spec.contact.friction.coefficient)
        else:
            raise PortError(PortErrorCategory.UNSUPPORTED_CAPABILITY, "unsupported friction intent")
        ET.SubElement(contact, "fric_coeff").text = friction_value
        for setting in spec.solver_policy.controls:
            if setting.name in _CONTACT_CONTROLS | _OPTIONAL_CONTACT_CONTROLS:
                ET.SubElement(contact, setting.name).text = self._control_value(setting.value)
        motion = ET.SubElement(root, "LoadData")
        load = ET.SubElement(motion, "load_controller", {"id": "1", "type": "loadcurve"})
        ET.SubElement(load, "interpolate").text = "LINEAR"
        ET.SubElement(load, "extend").text = "CONSTANT"
        points = ET.SubElement(load, "points")
        for sample in spec.motion.samples:
            ET.SubElement(
                points, "point"
            ).text = f"{self._quantity(sample.time)},{self._quantity(sample.displacement)}"
        if spec.solver_policy.increments.adaptive:
            must_curve = ET.SubElement(motion, "load_controller", {"id": "2", "type": "loadcurve"})
            ET.SubElement(must_curve, "interpolate").text = "STEP"
            ET.SubElement(must_curve, "extend").text = "CONSTANT"
            points = ET.SubElement(must_curve, "points")
            for value in must_points:
                ET.SubElement(
                    points, "point"
                ).text = f"{self._float(value)},{self._quantity(spec.solver_policy.increments.maximum_step)}"
        output = ET.SubElement(root, "Output")
        # CLI -p owns the path; do not introduce a second potentially relative path.
        plotfile = ET.SubElement(output, "plotfile", {"type": "febio"})
        ET.SubElement(plotfile, "compression").text = "0"
        variables: set[str] = set()
        for request in spec.outputs.requests:
            mapping = profile.mapping_for(request.quantity_id)
            if mapping.native_name not in variables:
                ET.SubElement(plotfile, "var", {"type": mapping.native_name})
                variables.add(mapping.native_name)

        ET.indent(root, space="  ")
        return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8")

    @staticmethod
    def _selection_digest(selection: SelectionRef) -> str:
        return hashlib.sha256(selection.to_bytes()).hexdigest()

    @staticmethod
    def _allocate_name(base: str, allocated: set[str]) -> str:
        name = base
        suffix = 1
        while name in allocated:
            suffix += 1
            name = f"{base}-{suffix}"
        allocated.add(name)
        return name

    def _resolved_set_ids(
        self, revision: CaseRevision, mesh: MeshArtifact
    ) -> dict[tuple[str, str], str]:
        needs = (
            (revision.spec.contact.part_surface, "face"),
            (revision.spec.contact.tool_surface, "face"),
            *((support.selection, "node") for support in revision.spec.support.supports),
            *(
                (
                    request.selection,
                    {"element": "element", "rigid_body": "body"}.get(request.location, "node"),
                )
                for request in revision.spec.outputs.requests
            ),
        )
        resolved: dict[tuple[str, str], str] = {}
        allocated = {item.set_id for item in mesh.sets}
        for selection, expected_kind in needs:
            digest = self._selection_digest(selection)
            if (digest, expected_kind) in resolved:
                continue
            if expected_kind == "body":
                body = selection.body_id.value
                projections = [
                    item
                    for item in mesh.sets
                    if item.source_selection_digest == digest and item.body_id == body
                ]
                # A node/face projection identifies its owning rigid body, not
                # a nodal output. Explicit body sets must identify only it.
                if any(
                    item.kind not in {"body", "node", "face"}
                    or (item.kind == "body" and tuple(item.member_ids) != (body,))
                    for item in projections
                ):
                    self._unsupported(
                        "rigid output requires a single-body set or node/face projection"
                    )
                if not projections:
                    raise PortError(
                        PortErrorCategory.INTEGRITY, "rigid output body binding missing"
                    )
                resolved[(digest, "body")] = body
                continue
            matching = [
                item
                for item in mesh.sets
                if item.source_selection_digest == digest
                and item.body_id == selection.body_id.value
                and item.kind == expected_kind
            ]
            if not matching and expected_kind == "node":
                faces = [
                    item
                    for item in mesh.sets
                    if item.source_selection_digest == digest
                    and item.body_id == selection.body_id.value
                    and item.kind == "face"
                ]
                if len(faces) == 1:
                    resolved[(digest, "face")] = faces[0].set_id
                    resolved[(digest, "node")] = self._allocate_name(
                        f"{faces[0].set_id}-nodes", allocated
                    )
                    continue
            if len(matching) != 1:
                raise PortError(
                    PortErrorCategory.INTEGRITY,
                    f"selection does not resolve to one {expected_kind} mesh set: {selection.name}",
                )
            resolved[(digest, expected_kind)] = matching[0].set_id
        return resolved

    @staticmethod
    def _control_value(value: Quantity | int | bool) -> str:
        if isinstance(value, Quantity):
            return f"{value.to_si().value:.17g}"
        if isinstance(value, bool):
            return "1" if value else "0"
        return str(value)

    @staticmethod
    def _quantity(value: Quantity) -> str:
        return f"{value.to_si().value:.17g}"

    @staticmethod
    def _float(value: float) -> str:
        return "0" if value == 0 else f"{value:.17g}"


__all__ = ["BundleStore", "CompilerAdapter", "LocalBundleStore"]
