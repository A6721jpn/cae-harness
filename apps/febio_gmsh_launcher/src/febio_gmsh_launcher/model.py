from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ComponentReference:
    owner: str
    attribute: str
    value: str


@dataclass
class ReferenceModel:
    nodes: dict[int, tuple[float, float, float]] = field(default_factory=dict)
    tet4: list[tuple[int, int, int, int]] = field(default_factory=list)
    surfaces: dict[str, list[tuple[int, int, int]]] = field(default_factory=dict)
    domains: dict[str, str] = field(default_factory=dict)
    references: list[ComponentReference] = field(default_factory=list)

    def required_surface_names(self) -> set[str]:
        names: set[str] = set()
        for reference in self.references:
            if reference.attribute == "node_set" and reference.value.startswith(
                "@surface:"
            ):
                names.add(reference.value.removeprefix("@surface:"))
            elif reference.attribute in {"surface", "primary", "secondary"}:
                names.add(reference.value)
        return names

    def required_domain_names(self) -> set[str]:
        explicit = {
            reference.value
            for reference in self.references
            if reference.attribute in {"domain", "elem_set"}
        }
        return explicit or set(self.domains)
