from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import gmsh


@dataclass(frozen=True)
class LocalRefinementConfig:
    global_size_mm: float
    local_size_mm: float
    transition_mm: float

    def __post_init__(self) -> None:
        if not 0.0 < self.local_size_mm < self.global_size_mm:
            raise ValueError("local_size_mm must be positive and below global_size_mm")
        if self.transition_mm <= 0.0:
            raise ValueError("transition_mm must be positive")


@dataclass(frozen=True)
class FaceSignature:
    face_tag: int
    area: float
    centroid: tuple[float, float, float]
    bounding_box: tuple[float, float, float, float, float, float]


def face_signature(face_tag: int) -> FaceSignature:
    return FaceSignature(
        face_tag=face_tag,
        area=float(gmsh.model.occ.getMass(2, face_tag)),
        centroid=tuple(
            float(value) for value in gmsh.model.occ.getCenterOfMass(2, face_tag)
        ),
        bounding_box=tuple(
            float(value) for value in gmsh.model.getBoundingBox(2, face_tag)
        ),
    )


def validate_seed_signatures(signatures: Sequence[FaceSignature]) -> None:
    if not signatures:
        raise ValueError("local-refinement seed selection must be nonempty")
    tags: set[int] = set()
    for signature in signatures:
        if signature.face_tag in tags:
            raise ValueError("local-refinement seed signatures must have unique face tags")
        tags.add(signature.face_tag)
        if signature.area <= 0.0:
            raise ValueError("local-refinement seed signature areas must be positive")
        if not all(math.isfinite(value) for value in (*signature.centroid, *signature.bounding_box)):
            raise ValueError("local-refinement seed signature coordinates must be finite")


def configure_distance_threshold_field(
    face_tags: Sequence[int],
    config: LocalRefinementConfig,
) -> tuple[int, int]:
    if not face_tags:
        raise ValueError("local-refinement face selection must be nonempty")
    distance = gmsh.model.mesh.field.add("Distance")
    gmsh.model.mesh.field.setNumbers(distance, "FacesList", list(face_tags))
    gmsh.model.mesh.field.setNumber(distance, "Sampling", 200)
    threshold = gmsh.model.mesh.field.add("Threshold")
    gmsh.model.mesh.field.setNumber(threshold, "InField", distance)
    gmsh.model.mesh.field.setNumber(threshold, "SizeMin", config.local_size_mm)
    gmsh.model.mesh.field.setNumber(threshold, "SizeMax", config.global_size_mm)
    gmsh.model.mesh.field.setNumber(threshold, "DistMin", 0.0)
    gmsh.model.mesh.field.setNumber(threshold, "DistMax", config.transition_mm)
    gmsh.model.mesh.field.setAsBackgroundMesh(threshold)
    return distance, threshold
