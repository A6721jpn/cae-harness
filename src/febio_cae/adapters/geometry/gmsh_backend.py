"""Stable import surface for the optional native Gmsh/OpenCASCADE backend.

The implementation remains in ``gmsh_occ`` so existing preparation and native
gates retain their established module boundary.
"""

from .gmsh_occ import GmshOCCBackend, GmshOCCConfig

__all__ = ["GmshOCCBackend", "GmshOCCConfig"]
