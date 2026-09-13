"""Shared identity helpers for measured native rigid-primitive records."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from febio_cae.domain import RigidPrimitive
from febio_cae.domain.canonical import canonical_bytes

from .backend import BACKEND_TET10_ORDER_ID, BackendLocalRefinement


def primitive_source_digest(
    primitive: RigidPrimitive,
    *,
    geometry_digest: str,
    global_size_si: float | None,
    local_refinements: Sequence[BackendLocalRefinement] = (),
) -> str:
    """Return the identity of one native primitive inspection or mesh source."""

    payload: dict[str, object] = {
        "schema_version": "1",
        "backend_id": "gmsh-occ",
        "primitive": primitive.to_dict(),
        "geometry_digest": geometry_digest,
        "native_length_unit": "m",
        "global_size_si": global_size_si,
        "ordering_id": BACKEND_TET10_ORDER_ID if global_size_si is not None else None,
    }
    if local_refinements:
        payload["local_refinements"] = [item.to_dict() for item in local_refinements]
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


_primitive_source_digest = primitive_source_digest
