from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from .errors import ExitCode, LauncherError
from .model import ComponentReference, ReferenceModel


def _numbers(text: str | None, cast: type[int] | type[float]) -> tuple:
    if not text:
        return ()
    return tuple(cast(part.strip()) for part in text.split(","))


def scan_reference_feb(path: Path) -> ReferenceModel:
    model = ReferenceModel()
    stack: list[tuple[str, dict[str, str]]] = []
    mesh_depth = 0
    current_elements_type = ""
    current_surface = ""
    reference_attributes = {
        "surface",
        "node_set",
        "elem_set",
        "domain",
        "primary",
        "secondary",
    }
    try:
        events = ET.iterparse(path, events=("start", "end"))
        for event, element in events:
            tag = element.tag.rsplit("}", 1)[-1]
            if event == "start":
                stack.append((tag, dict(element.attrib)))
                if tag == "Mesh":
                    mesh_depth += 1
                elif mesh_depth and tag == "Elements":
                    current_elements_type = element.attrib.get("type", "")
                elif mesh_depth and tag == "Surface":
                    current_surface = element.attrib.get("name", "")
                    model.surfaces.setdefault(current_surface, [])
                elif not mesh_depth:
                    owner = element.attrib.get("name", tag)
                    for attribute in reference_attributes & element.attrib.keys():
                        value = element.attrib[attribute]
                        if attribute == "node_set" and not value.startswith("@surface:"):
                            raise LauncherError(
                                f"Unsupported selection in {owner}: "
                                f"node_set={value}",
                                ExitCode.TRANSFER_ERROR,
                            )
                        model.references.append(
                            ComponentReference(owner, attribute, value)
                        )
                continue

            parent_tag = stack[-2][0] if len(stack) >= 2 else ""
            if mesh_depth and tag == "node" and parent_tag == "Nodes":
                values = _numbers(element.text, float)
                if len(values) == 3:
                    model.nodes[int(element.attrib["id"])] = values
            elif mesh_depth and tag == "elem" and parent_tag == "Elements":
                values = _numbers(element.text, int)
                if current_elements_type == "tet4" and len(values) == 4:
                    model.tet4.append(values)
            elif mesh_depth and tag in {"tri3", "tri6"} and parent_tag == "Surface":
                values = _numbers(element.text, int)
                if len(values) >= 3:
                    model.surfaces[current_surface].append(values[:3])
            elif tag == "SolidDomain":
                name = element.attrib.get("name", "")
                if name:
                    model.domains[name] = element.attrib.get("mat", "")

            if tag == "Elements":
                current_elements_type = ""
            elif tag == "Surface":
                current_surface = ""
            elif tag == "Mesh":
                mesh_depth -= 1
            stack.pop()
            element.clear()
    except ET.ParseError as exc:
        raise LauncherError(
            f"Invalid FEB XML {path}: {exc}", ExitCode.TRANSFER_ERROR
        ) from exc
    missing = model.required_surface_names() - model.surfaces.keys()
    if missing:
        raise LauncherError(
            f"Referenced Surface sets are missing: {sorted(missing)}",
            ExitCode.TRANSFER_ERROR,
        )
    return model
