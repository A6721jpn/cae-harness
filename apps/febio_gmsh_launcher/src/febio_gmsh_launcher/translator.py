from __future__ import annotations

import html
import os
import re
import uuid
from pathlib import Path
from typing import TextIO

import numpy as np

from .errors import ExitCode, LauncherError
from .febio_xml import scan_reference_feb
from .mesher import MeshData


def _transfer_error(message: str) -> LauncherError:
    return LauncherError(message, ExitCode.TRANSFER_ERROR)


def _encoding(path: Path) -> str:
    prefix = path.read_bytes()[:256].decode("ascii", errors="ignore")
    match = re.search(r"encoding=[\"']([^\"']+)", prefix, flags=re.IGNORECASE)
    return match.group(1) if match else "utf-8"


def _validate(reference_path: Path, mesh: MeshData) -> tuple[object, set[str]]:
    reference = scan_reference_feb(reference_path)
    required_surfaces = reference.required_surface_names()
    missing_surfaces = required_surfaces - mesh.surfaces.keys()
    if missing_surfaces:
        raise _transfer_error(
            f"Translated mesh is missing required Surface sets: "
            f"{sorted(missing_surfaces)}"
        )
    missing_domains = reference.required_domain_names() - mesh.domain_element_indices.keys()
    if missing_domains:
        raise _transfer_error(
            f"Translated mesh is missing required domains: {sorted(missing_domains)}"
        )
    if not np.isfinite(mesh.points).all():
        raise _transfer_error("Mesh contains non-finite node coordinates")
    if mesh.tet10.ndim != 2 or mesh.tet10.shape[1] != 10:
        raise _transfer_error("Tet10 connectivity must have width 10")
    arrays = [mesh.tet10, *mesh.surfaces.values()]
    if any(
        array.size
        and (int(array.min()) < 0 or int(array.max()) >= len(mesh.points))
        for array in arrays
    ):
        raise _transfer_error("Mesh connectivity references an unknown node")
    return reference, required_surfaces


def _write_mesh(handle: TextIO, mesh: MeshData) -> None:
    handle.write("\t<Mesh>\n")
    handle.write('\t\t<Nodes name="Gmsh_Tet10">\n')
    for node_id, xyz in enumerate(mesh.points, start=1):
        handle.write(
            f'\t\t\t<node id="{node_id}">'
            f"{xyz[0]:.17g},{xyz[1]:.17g},{xyz[2]:.17g}</node>\n"
        )
    handle.write("\t\t</Nodes>\n")
    for domain_name, indices in mesh.domain_element_indices.items():
        escaped = html.escape(domain_name, quote=True)
        handle.write(f'\t\t<Elements type="tet10" name="{escaped}">\n')
        for element_index in indices:
            nodes = ",".join(str(int(node) + 1) for node in mesh.tet10[element_index])
            handle.write(
                f'\t\t\t<elem id="{int(element_index) + 1}">{nodes}</elem>\n'
            )
        handle.write("\t\t</Elements>\n")
    for surface_name, faces in mesh.surfaces.items():
        escaped = html.escape(surface_name, quote=True)
        handle.write(f'\t\t<Surface name="{escaped}">\n')
        for face_id, face in enumerate(faces, start=1):
            nodes = ",".join(str(int(node) + 1) for node in face)
            handle.write(f'\t\t\t<tri6 id="{face_id}">{nodes}</tri6>\n')
        handle.write("\t\t</Surface>\n")
    handle.write("\t</Mesh>\n")


def _write_domains(handle: TextIO, mesh: MeshData, materials: dict[str, str]) -> None:
    handle.write("\t<MeshDomains>\n")
    for domain_name in mesh.domain_element_indices:
        if domain_name not in materials:
            raise _transfer_error(f"No material mapping for domain {domain_name!r}")
        name = html.escape(domain_name, quote=True)
        material = html.escape(materials[domain_name], quote=True)
        handle.write(f'\t\t<SolidDomain name="{name}" mat="{material}"/>\n')
    handle.write("\t</MeshDomains>\n")


def translate_feb(reference_path: Path, mesh: MeshData, output_path: Path) -> Path:
    source = reference_path.expanduser().resolve()
    output = output_path.expanduser().resolve()
    reference, _ = _validate(source, mesh)
    encoding = _encoding(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    skipping: str | None = None
    found_mesh = False
    found_domains = False
    try:
        with source.open("r", encoding=encoding, newline="") as reader, temporary.open(
            "w", encoding=encoding, newline=""
        ) as writer:
            for line in reader:
                stripped = line.strip()
                if skipping == "Mesh":
                    if stripped == "</Mesh>":
                        skipping = None
                    continue
                if skipping == "MeshDomains":
                    if stripped == "</MeshDomains>":
                        skipping = None
                    continue
                if stripped.startswith("<Mesh>"):
                    _write_mesh(writer, mesh)
                    found_mesh = True
                    skipping = None if stripped.endswith("</Mesh>") else "Mesh"
                    continue
                if stripped.startswith("<MeshDomains>"):
                    _write_domains(writer, mesh, reference.domains)
                    found_domains = True
                    skipping = (
                        None if stripped.endswith("</MeshDomains>") else "MeshDomains"
                    )
                    continue
                writer.write(line)
        if not found_mesh or not found_domains or skipping is not None:
            raise _transfer_error("Reference FEB has no complete Mesh/MeshDomains section")
        generated = scan_reference_feb(temporary)
        if generated.required_surface_names() - generated.surfaces.keys():
            raise _transfer_error("Generated FEB has unresolved Surface references")
        os.replace(temporary, output)
        return output
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
