"""Deterministic, capability-gated FEBio input compilation."""

from __future__ import annotations

import hashlib
import os
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Final, Protocol

from febio_cae.domain import (
    CapabilityStatus,
    CaseRevision,
    CompatibilityProfile,
    ExecutionBundle,
    ExecutionSetting,
    FileEntry,
    MeshArtifact,
    PortError,
    PortErrorCategory,
)
from febio_cae.domain.artifacts import TET10_NODE_ORDER_ID, validate_logical_path

_REQUIRED_CAPABILITIES: Final[frozenset[str]] = frozenset(
    {
        "febio.material.isotropic_linear_elastic",
        "febio.mesh.tet10",
        "febio.contact.sliding_elastic",
        "febio.contact.primary_tool_secondary_part",
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
        identity = hashlib.sha256(
            b"|".join(
                (
                    revision.spec_digest.encode(),
                    mesh.artifact_digest.encode(),
                    profile.profile_id.encode(),
                )
            )
        ).hexdigest()
        bundle_id = f"bundle-{identity[:24]}"
        file_entry = FileEntry(
            "input/case.feb", hashlib.sha256(content).hexdigest(), len(content), "input"
        )
        bundle = ExecutionBundle(
            bundle_id=bundle_id,
            case_id=revision.case_id,
            revision_id=revision.revision_id,
            spec_digest=revision.spec_digest,
            mesh_digest=mesh.artifact_digest,
            profile_id=profile.profile_id,
            tool=profile.solver,
            files=(file_entry,),
            argv=(self.executable, "-i", "input/case.feb", "-o", "output/results.xplt"),
            cwd=str(getattr(self.store, "root", Path.cwd())),
            thread_count=1,
            settings=(
                ExecutionSetting("solver_threads", 1),
                ExecutionSetting("xplt_version", "0x35"),
            ),
        )
        self.store.stage(bundle.bundle_id, file_entry.logical_path, content)
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
        if not {part_body, tool_body}.issubset(source_bodies):
            raise PortError(
                PortErrorCategory.INTEGRITY,
                "mesh provenance does not contain both declared part and tool bodies",
            )
        mesh_bodies = {element.body_id for element in mesh.elements}
        if not {part_body, tool_body}.issubset(mesh_bodies):
            raise PortError(
                PortErrorCategory.INTEGRITY,
                "mesh does not contain separate part and rigid-tool element ownership",
            )
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
        missing = sorted(_REQUIRED_CAPABILITIES - capabilities.keys())
        unavailable = sorted(
            capability_id
            for capability_id in _REQUIRED_CAPABILITIES
            if capability_id in capabilities
            and capabilities[capability_id].status is not CapabilityStatus.SUPPORTED
        )
        if missing or unavailable:
            detail = ", ".join((*missing, *unavailable))
            raise PortError(
                PortErrorCategory.UNSUPPORTED_CAPABILITY,
                f"required capabilities unavailable: {detail}",
            )
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

    def _render(
        self, revision: CaseRevision, mesh: MeshArtifact, profile: CompatibilityProfile
    ) -> bytes:
        spec = revision.spec
        root = ET.Element("febio_spec", {"version": self._febio_version(profile.solver.version)})
        ET.SubElement(root, "Module", {"type": "solid"})
        control = ET.SubElement(root, "Control")
        ET.SubElement(control, "analysis").text = "static"
        ET.SubElement(control, "time_steps").text = str(spec.solver_policy.increments.max_steps)
        ET.SubElement(control, "step_size").text = self._quantity(
            spec.solver_policy.increments.initial_step
        )
        ET.SubElement(control, "max_refs").text = str(
            spec.solver_policy.increments.max_step_retries
        )
        ET.SubElement(control, "max_ups").text = "0"

        materials = ET.SubElement(root, "Material")
        if hasattr(spec.material, "youngs_modulus"):
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

        mesh_section = ET.SubElement(root, "Mesh")
        nodes = ET.SubElement(mesh_section, "Nodes", {"name": "part-and-tool"})
        for node in mesh.nodes:
            ET.SubElement(nodes, "node", {"id": str(node.node_id)}).text = " ".join(
                self._float(value) for value in node.coordinates_si
            )
        elements = ET.SubElement(
            mesh_section, "Elements", {"type": "tet10", "name": "part-and-tool"}
        )
        for element in mesh.elements:
            ET.SubElement(
                elements,
                "elem",
                {"id": str(element.element_id), "type": "tet10", "body": element.body_id},
            ).text = " ".join(str(value) for value in element.node_ids)
        surfaces = ET.SubElement(mesh_section, "SurfacePair", {"name": "contact-main"})
        ET.SubElement(surfaces, "primary").text = spec.rigid_tool.contact_surface.name
        ET.SubElement(surfaces, "secondary").text = spec.contact.part_surface.name
        for item in mesh.sets:
            set_element = ET.SubElement(
                mesh_section, "Set", {"name": item.set_id, "type": item.kind, "body": item.body_id}
            )
            set_element.text = " ".join(str(value) for value in item.member_ids)

        boundary = ET.SubElement(root, "Boundary")
        for support in spec.support.supports:
            fixed = ",".join(
                axis for axis in ("x", "y", "z") if getattr(support, axis).state == "fixed"
            )
            ET.SubElement(
                boundary,
                "fix",
                {"bc": fixed, "set": support.selection.name, "frame": support.frame.value},
            )
        rigid = ET.SubElement(
            root,
            "Rigid",
            {
                "body": spec.rigid_tool.primitive.body_id.value,
                "kind": spec.rigid_tool.primitive.kind,
            },
        )
        for axis in ("x", "y", "z", "rx", "ry", "rz"):
            ET.SubElement(
                rigid, "dof", {"axis": axis, "state": getattr(spec.rigid_tool.dofs, axis).state}
            )
        motion = ET.SubElement(root, "LoadData")
        load = ET.SubElement(
            motion,
            "load_controller",
            {"id": "motion-main", "type": "loadcurve", "interpolate": "linear"},
        )
        for sample in spec.motion.samples:
            ET.SubElement(
                load, "point"
            ).text = f"{sample.time.to_si().value:.17g},{sample.displacement.to_si().value:.17g}"
        loads = ET.SubElement(root, "Loads")
        ET.SubElement(
            loads,
            "prescribed",
            {"bc": "z", "set": spec.rigid_tool.contact_surface.name, "lc": "motion-main"},
        )
        contacts = ET.SubElement(root, "Contact")
        contact = ET.SubElement(
            contacts, "contact", {"type": "sliding-elastic", "id": spec.contact.contact_id.value}
        )
        ET.SubElement(contact, "primary").text = spec.rigid_tool.contact_surface.name
        ET.SubElement(contact, "secondary").text = spec.contact.part_surface.name
        ET.SubElement(contact, "friction").text = (
            "0" if spec.contact.friction.__class__.__name__ == "Frictionless" else "explicit"
        )
        ET.SubElement(contact, "penalty").text = "explicit-profile"
        output = ET.SubElement(root, "Output")
        ET.SubElement(output, "plotfile", {"type": "plot", "file": "output/results.xplt"})
        for request in spec.outputs.requests:
            mapping = profile.mapping_for(request.quantity_id)
            ET.SubElement(
                output,
                "plotvar",
                {
                    "name": mapping.native_name,
                    "canonical": mapping.canonical_id,
                    "location": request.location,
                    "set": request.selection.name,
                    "unit": mapping.unit,
                },
            )

        ET.indent(root, space="  ")
        return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8")

    @staticmethod
    def _quantity(value: Any) -> str:
        return f"{value.to_si().value:.17g}"

    @staticmethod
    def _float(value: float) -> str:
        return f"{value:.17g}"

    @staticmethod
    def _febio_version(version: str) -> str:
        parts = version.split(".")
        return ".".join(parts[:2]) if len(parts) >= 2 else version


__all__ = ["BundleStore", "CompilerAdapter", "LocalBundleStore"]
