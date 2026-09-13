"""Non-native boundary checks for source-local refinement requests."""

from __future__ import annotations

from typing import Any

import pytest

from febio_cae.adapters.geometry.backend import BackendError, BackendErrorCategory
from febio_cae.adapters.geometry.gmsh_occ import GmshOCCBackend, GmshOCCConfig
from febio_cae.domain import (
    BodyId,
    EvidenceRef,
    FrameId,
    ProperRotation,
    Quantity,
    RigidPrimitive,
    RigidTransform,
    Translation3,
)


def _evidence(target_field: str, seed: str) -> EvidenceRef:
    return EvidenceRef(
        schema_version="1",
        source_kind="registered_test_condition",
        reference="NativeLocalRefinementComponentTest:1",
        target_field=target_field,
        content_digest=seed * 64,
    )


def _primitive() -> RigidPrimitive:
    local_frame = FrameId("ToolLocal")
    world_frame = FrameId("World")
    return RigidPrimitive(
        kind="sphere",
        body_id=BodyId("native-local-body"),
        local_frame=local_frame,
        placement=RigidTransform(
            source_frame=local_frame,
            target_frame=world_frame,
            translation=Translation3(
                world_frame,
                Quantity(0, "m"),
                Quantity(0, "m"),
                Quantity(0, "m"),
            ),
            rotation=ProperRotation(
                ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
            ),
        ),
        dimensions={"radius": Quantity(10, "mm")},
        dimension_evidence={"radius": _evidence("rigid_tool.radius", "a")},
        model_evidence=_evidence("rigid_tool.model", "b"),
        placement_evidence=_evidence("rigid_tool.placement", "c"),
    )


def test_malformed_local_refinement_is_rejected_before_native_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = GmshOCCBackend(
        GmshOCCConfig(module_name="module-that-must-not-be-loaded")
    )

    def forbidden_native_entry() -> Any:
        pytest.fail("malformed local refinement entered the native backend")

    monkeypatch.setattr(backend, "_load_module", forbidden_native_entry)
    with pytest.raises(BackendError, match="local_refinement") as error:
        backend.mesh_rigid_primitive(
            _primitive(),
            geometry_digest="a" * 64,
            global_size_si=0.005,
            local_refinements=(object(),),
        )
    assert error.value.category is BackendErrorCategory.INVALID_INPUT
