"""Geometry and optional native CAD backend adapters."""

from .adapter import InitialContactPlacement, StepGeometryMeshAdapter
from .backend import (
    BACKEND_TET10_ORDER_ID,
    BACKEND_TET10_TO_CANONICAL_POSITIONS,
    BackendBody,
    BackendElement,
    BackendError,
    BackendErrorCategory,
    BackendFace,
    BackendInspection,
    BackendLocalRefinement,
    BackendMesh,
    BackendMeshFace,
    BackendNode,
    GeometryMeshBackend,
    NativeCurvedGeometryBackend,
)
from .gmsh_backend import GmshOCCBackend, GmshOCCConfig

__all__ = [
    "BACKEND_TET10_ORDER_ID",
    "BACKEND_TET10_TO_CANONICAL_POSITIONS",
    "BackendBody",
    "BackendElement",
    "BackendError",
    "BackendErrorCategory",
    "BackendFace",
    "BackendInspection",
    "BackendLocalRefinement",
    "BackendMesh",
    "BackendMeshFace",
    "BackendNode",
    "GeometryMeshBackend",
    "NativeCurvedGeometryBackend",
    "GmshOCCBackend",
    "GmshOCCConfig",
    "InitialContactPlacement",
    "StepGeometryMeshAdapter",
]
