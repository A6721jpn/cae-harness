"""Read-only structural inspection of FEB XML input files."""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

_REFERENCE_ATTRIBUTE_NAMES = {
    "idref",
    "ref",
    "refs",
    "set",
    "set_name",
    "node_set",
    "nodeset",
    "node_id",
    "node_ids",
    "element_set",
    "elementset",
    "elem_set",
    "elemset",
    "element_id",
    "element_ids",
    "surface",
    "surface_set",
    "surface_pair",
    "pair",
    "material",
    "material_id",
    "mat",
    "mat_id",
}
_REFERENCE_VALUE_SPLIT = re.compile(r"[,\s]+")


class FEBInspectionError(ValueError):
    """Raised when a source cannot be parsed as XML."""


@dataclass(frozen=True, slots=True)
class XMLNodeFact:
    """One node in the XML tree, without interpreting its physical content."""

    path: str
    tag: str
    depth: int
    attributes: Mapping[str, str]
    text: str | None
    child_count: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "attributes", MappingProxyType(dict(self.attributes)))

    @property
    def attribute_names(self) -> tuple[str, ...]:
        return tuple(self.attributes)

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "tag": self.tag,
            "depth": self.depth,
            "attributes": dict(self.attributes),
            "text": self.text,
            "child_count": self.child_count,
        }


XmlNodeFact = XMLNodeFact


@dataclass(frozen=True, slots=True)
class XMLDefinition:
    """An explicit XML ``id`` or ``name`` definition."""

    identifier: str
    kind: str
    tag: str
    attribute: str
    path: str

    def to_dict(self) -> dict[str, object]:
        return {
            "identifier": self.identifier,
            "kind": self.kind,
            "tag": self.tag,
            "attribute": self.attribute,
            "path": self.path,
        }


XmlDefinition = XMLDefinition


@dataclass(frozen=True, slots=True)
class XMLReference:
    """An explicit structural reference and its closure status."""

    value: str
    target_kind: str
    tag: str
    attribute: str
    path: str
    resolved: bool
    resolved_definition: XMLDefinition | None = None

    @property
    def is_resolved(self) -> bool:
        return self.resolved

    def to_dict(self) -> dict[str, object]:
        return {
            "value": self.value,
            "target_kind": self.target_kind,
            "tag": self.tag,
            "attribute": self.attribute,
            "path": self.path,
            "resolved": self.resolved,
            "resolved_definition": (
                None if self.resolved_definition is None else self.resolved_definition.to_dict()
            ),
        }


XmlReference = XMLReference


@dataclass(frozen=True, slots=True)
class ReferenceClosure:
    """Definitions, references, and unresolved edges in the XML tree."""

    definitions: tuple[XMLDefinition, ...]
    references: tuple[XMLReference, ...]
    unresolved: tuple[XMLReference, ...]

    def __post_init__(self) -> None:
        definitions = tuple(self.definitions)
        references = tuple(self.references)
        unresolved = tuple(self.unresolved)
        if not all(isinstance(item, XMLDefinition) for item in definitions):
            raise TypeError("definitions must contain XMLDefinition records")
        if not all(isinstance(item, XMLReference) for item in references + unresolved):
            raise TypeError("references must contain XMLReference records")
        object.__setattr__(self, "definitions", definitions)
        object.__setattr__(self, "references", references)
        object.__setattr__(self, "unresolved", unresolved)

    @property
    def is_closed(self) -> bool:
        return not self.unresolved

    @property
    def complete(self) -> bool:
        return self.is_closed

    @property
    def unresolved_references(self) -> tuple[XMLReference, ...]:
        return self.unresolved

    @property
    def duplicate_keys(self) -> tuple[tuple[str, str], ...]:
        """Return duplicate ``(kind, identifier)`` definition keys.

        FEBio uses separate identifier namespaces for materials, nodes,
        elements, and other definition kinds.  A raw identifier therefore is
        not a complete uniqueness key: material ``id=1`` and node ``id=1``
        are valid together, while two material definitions with ``id=1`` are
        not.
        """

        counts = Counter((item.kind, item.identifier) for item in self.definitions)
        return tuple(key for key, count in counts.items() if count > 1)

    @property
    def duplicate_identifiers(self) -> tuple[str, ...]:
        """Return identifiers whose kind-scoped definition key is repeated."""

        return tuple(dict.fromkeys(identifier for _, identifier in self.duplicate_keys))

    @property
    def has_duplicate_identifiers(self) -> bool:
        return bool(self.duplicate_identifiers)

    @property
    def missing(self) -> tuple[XMLReference, ...]:
        """Alias for unresolved closure edges."""

        return self.unresolved

    def to_dict(self) -> dict[str, object]:
        return {
            "definitions": [item.to_dict() for item in self.definitions],
            "references": [item.to_dict() for item in self.references],
            "unresolved": [item.to_dict() for item in self.unresolved],
            "is_closed": self.is_closed,
            "duplicate_keys": [list(key) for key in self.duplicate_keys],
            "duplicate_identifiers": list(self.duplicate_identifiers),
        }


@dataclass(frozen=True, slots=True)
class FEBInspection:
    """Immutable structural inventory for one FEB XML source."""

    source_name: str | None
    source_path: Path | None
    sha256: str
    size_bytes: int
    root_tag: str
    root_attributes: Mapping[str, str]
    nodes: tuple[XMLNodeFact, ...]
    tag_counts: Mapping[str, int]
    attribute_names: Mapping[str, tuple[str, ...]]
    reference_closure: ReferenceClosure

    def __post_init__(self) -> None:
        object.__setattr__(self, "root_attributes", MappingProxyType(dict(self.root_attributes)))
        object.__setattr__(self, "nodes", tuple(self.nodes))
        object.__setattr__(self, "tag_counts", MappingProxyType(dict(self.tag_counts)))
        object.__setattr__(
            self,
            "attribute_names",
            MappingProxyType({key: tuple(value) for key, value in self.attribute_names.items()}),
        )

    @classmethod
    def from_source(
        cls, source: str | bytes | Path, source_name: str | None = None
    ) -> FEBInspection:
        return inspect_feb_xml(source, source_name=source_name)

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def element_count(self) -> int:
        return self.node_count

    @property
    def definitions(self) -> tuple[XMLDefinition, ...]:
        return self.reference_closure.definitions

    @property
    def references(self) -> tuple[XMLReference, ...]:
        return self.reference_closure.references

    @property
    def unresolved_references(self) -> tuple[XMLReference, ...]:
        return self.reference_closure.unresolved

    @property
    def has_unresolved_references(self) -> bool:
        return bool(self.unresolved_references)

    @property
    def duplicate_keys(self) -> tuple[tuple[str, str], ...]:
        return self.reference_closure.duplicate_keys

    @property
    def duplicate_identifiers(self) -> tuple[str, ...]:
        return self.reference_closure.duplicate_identifiers

    @property
    def reference_definitions(self) -> tuple[XMLDefinition, ...]:
        return self.definitions

    @property
    def inventory(self) -> FEBInspection:
        return self

    def to_dict(self) -> dict[str, object]:
        return {
            "source_name": self.source_name,
            "source_path": None if self.source_path is None else str(self.source_path),
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "root_tag": self.root_tag,
            "root_attributes": dict(self.root_attributes),
            "node_count": self.node_count,
            "nodes": [item.to_dict() for item in self.nodes],
            "tag_counts": dict(self.tag_counts),
            "attribute_names": {key: list(value) for key, value in self.attribute_names.items()},
            "reference_closure": self.reference_closure.to_dict(),
        }


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _read_source(
    source: str | bytes | Path,
    source_name: str | None,
) -> tuple[bytes, Path | None, str | None]:
    if isinstance(source, bytes):
        return source, None, source_name
    if isinstance(source, Path):
        payload = source.read_bytes()
        return payload, source, source_name or source.name
    if not isinstance(source, str):
        raise TypeError("FEB source must be bytes, string XML, or pathlib.Path")
    if source.lstrip("\ufeff \t\r\n").startswith("<"):
        return source.encode("utf-8"), None, source_name
    path = Path(source)
    payload = path.read_bytes()
    return payload, path, source_name or path.name


def _definition_kind(tag: str) -> str:
    lowered = tag.lower()
    if lowered == "node":
        return "node"
    if lowered in {"elem", "element"}:
        return "element"
    if "material" in lowered:
        return "material"
    return lowered


def _reference_kind(attribute: str, value: str) -> str | None:
    lowered = attribute.lower().replace("-", "_")
    if lowered not in _REFERENCE_ATTRIBUTE_NAMES and not (
        lowered.endswith("_id") or lowered.endswith("_ids") or lowered.endswith("_ref")
    ):
        return None
    if "material" in lowered or lowered.startswith("mat"):
        return "material"
    if "node" in lowered:
        return "name-or-node" if "set" in lowered else "node"
    if "elem" in lowered or "element" in lowered:
        return "name-or-element" if "set" in lowered else "element"
    if "surface" in lowered or lowered in {"pair", "set", "set_name", "idref", "ref", "refs"}:
        return "name"
    if lowered.endswith("_id") or lowered.endswith("_ids"):
        return lowered.rsplit("_", 1)[0]
    return "name"


def _reference_values(value: str) -> tuple[str, ...]:
    return tuple(item for item in _REFERENCE_VALUE_SPLIT.split(value.strip()) if item)


def _text_reference_kind(tag: str) -> str | None:
    lowered = tag.lower().replace("-", "_")
    if lowered in {"elem", "element"}:
        return "node"
    if lowered in {"nodeset", "node_set"}:
        return "node"
    if lowered in {"elementset", "element_set", "elemset", "elem_set"}:
        return "element"
    return None


def _resolve(
    value: str,
    target_kind: str,
    definitions: tuple[XMLDefinition, ...],
) -> XMLDefinition | None:
    candidates = tuple(item for item in definitions if item.identifier == value)
    if target_kind == "name-or-node":
        accepted = {"name", "node"}
    elif target_kind == "name-or-element":
        accepted = {"name", "element"}
    else:
        accepted = {target_kind}
    for item in candidates:
        if item.kind in accepted:
            return item
        if (
            target_kind == "material"
            and item.tag.lower() == "material"
            and item.attribute == "name"
        ):
            return item
    # A generic named reference is allowed to resolve against a specialized
    # named definition (for example a contact pair or node set).
    if target_kind == "name":
        return next((item for item in candidates if item.attribute == "name"), None)
    return None


def _walk(
    element: ET.Element,
    parent_path: str,
    depth: int,
    nodes: list[XMLNodeFact],
    definitions: list[XMLDefinition],
) -> None:
    tag = _local_name(str(element.tag))
    path = parent_path or f"/{tag}"
    text = (element.text or "").strip() or None
    nodes.append(
        XMLNodeFact(
            path=path,
            tag=tag,
            depth=depth,
            attributes={str(key): str(value) for key, value in element.attrib.items()},
            text=text,
            child_count=len(element),
        )
    )
    for attribute, value in element.attrib.items():
        if attribute in {"id", "name"} and str(value).strip():
            definition_kind = _definition_kind(tag)
            if attribute == "name":
                definition_kind = "domain" if tag.lower().endswith("domain") else "name"
            definitions.append(
                XMLDefinition(
                    identifier=str(value).strip(),
                    kind=definition_kind,
                    tag=tag,
                    attribute=attribute,
                    path=path,
                )
            )
    sibling_counts: Counter[str] = Counter()
    for child in element:
        child_tag = _local_name(str(child.tag))
        sibling_counts[child_tag] += 1
        child_path = f"{path}/{child_tag}[{sibling_counts[child_tag]}]"
        _walk(child, child_path, depth + 1, nodes, definitions)


def _collect_references(
    root: ET.Element,
    definitions: tuple[XMLDefinition, ...],
) -> tuple[XMLReference, ...]:
    references: list[XMLReference] = []
    nodes: list[tuple[ET.Element, str]] = []

    def collect(element: ET.Element, path: str) -> None:
        nodes.append((element, path))
        sibling_counts: Counter[str] = Counter()
        for child in element:
            child_tag = _local_name(str(child.tag))
            sibling_counts[child_tag] += 1
            collect(child, f"{path}/{child_tag}[{sibling_counts[child_tag]}]")

    collect(root, f"/{_local_name(str(root.tag))}")
    for element, path in nodes:
        tag = _local_name(str(element.tag))
        for attribute, raw_value in element.attrib.items():
            target_kind = _reference_kind(str(attribute), str(raw_value))
            if target_kind is None:
                continue
            for value in _reference_values(str(raw_value)):
                resolved_definition = _resolve(value, target_kind, definitions)
                references.append(
                    XMLReference(
                        value=value,
                        target_kind=target_kind,
                        tag=tag,
                        attribute=str(attribute),
                        path=path,
                        resolved=resolved_definition is not None,
                        resolved_definition=resolved_definition,
                    )
                )
        target_kind = _text_reference_kind(tag)
        if target_kind is None or not (element.text or "").strip():
            continue
        for value in _reference_values(element.text or ""):
            # Text membership is structural only for integer IDs.  Arbitrary
            # text in a node/element body is not treated as a physical guess.
            if not value.isdigit():
                continue
            resolved_definition = _resolve(value, target_kind, definitions)
            references.append(
                XMLReference(
                    value=value,
                    target_kind=target_kind,
                    tag=tag,
                    attribute="#text",
                    path=path,
                    resolved=resolved_definition is not None,
                    resolved_definition=resolved_definition,
                )
            )
    return tuple(references)


def inspect_feb_xml(source: str | bytes | Path, source_name: str | None = None) -> FEBInspection:
    """Build a structural FEB inventory and reference closure without writing."""

    payload, source_path, resolved_name = _read_source(source, source_name)
    try:
        root = ET.fromstring(payload)
    except (ET.ParseError, UnicodeDecodeError) as error:
        raise FEBInspectionError(f"invalid FEB XML: {error}") from error
    nodes: list[XMLNodeFact] = []
    definitions_list: list[XMLDefinition] = []
    _walk(root, "", 0, nodes, definitions_list)
    definitions = tuple(definitions_list)
    references = _collect_references(root, definitions)
    closure = ReferenceClosure(
        definitions=definitions,
        references=references,
        unresolved=tuple(item for item in references if not item.resolved),
    )
    counts = Counter(item.tag for item in nodes)
    attributes: dict[str, tuple[str, ...]] = {}
    for item in nodes:
        existing = list(attributes.get(item.tag, ()))
        for name in item.attributes:
            if name not in existing:
                existing.append(name)
        attributes[item.tag] = tuple(existing)
    return FEBInspection(
        source_name=resolved_name,
        source_path=source_path,
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        root_tag=_local_name(str(root.tag)),
        root_attributes={str(key): str(value) for key, value in root.attrib.items()},
        nodes=tuple(nodes),
        tag_counts=counts,
        attribute_names=attributes,
        reference_closure=closure,
    )


inspect_feb = inspect_feb_xml
inspect_feb_file = inspect_feb_xml
inventory_feb_xml = inspect_feb_xml


def build_reference_closure(source: str | bytes | Path) -> ReferenceClosure:
    """Inspect a FEB source and return only its structural reference closure."""

    return inspect_feb_xml(source).reference_closure


reference_closure = build_reference_closure
FEBXmlInspection = FEBInspection
FEBXMLInspection = FEBInspection
FEBInventory = FEBInspection
FEBStructuralInventory = FEBInspection


__all__ = [
    "FEBInspection",
    "FEBInspectionError",
    "FEBInventory",
    "FEBStructuralInventory",
    "FEBXMLInspection",
    "FEBXmlInspection",
    "ReferenceClosure",
    "XMLDefinition",
    "XMLNodeFact",
    "XMLReference",
    "XmlDefinition",
    "XmlNodeFact",
    "XmlReference",
    "inspect_feb",
    "inspect_feb_file",
    "inspect_feb_xml",
    "inventory_feb_xml",
    "build_reference_closure",
    "reference_closure",
]
