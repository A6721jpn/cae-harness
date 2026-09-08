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
    BackendMesh,
    BackendMeshFace,
    BackendNode,
    GeometryMeshBackend,
)
from .gmsh_occ import GmshOCCBackend, GmshOCCConfig

__all__ = [
    "BACKEND_TET10_ORDER_ID",
    "BACKEND_TET10_TO_CANONICAL_POSITIONS",
    "BackendBody",
    "BackendElement",
    "BackendError",
    "BackendErrorCategory",
    "BackendFace",
    "BackendInspection",
    "BackendMesh",
    "BackendMeshFace",
    "BackendNode",
    "GeometryMeshBackend",
    "GmshOCCBackend",
    "GmshOCCConfig",
    "InitialContactPlacement",
    "StepGeometryMeshAdapter",
]
