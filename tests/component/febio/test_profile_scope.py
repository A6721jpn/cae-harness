"""Scoped compatibility declarations must not authorize unqualified mechanisms."""

from dataclasses import replace
from pathlib import Path

import pytest

from febio_cae.domain import (
    CapabilityRef,
    CapabilityStatus,
    CoulombFriction,
    PortError,
    PortErrorCategory,
    Quantity,
)

from .fixtures import evidence
from .test_compiler_native import _case, _compile


@pytest.mark.parametrize("outside_scope", ["finite_friction", "free_support"])
def test_scoped_profile_refuses_unqualified_conditions(tmp_path: Path, outside_scope: str) -> None:
    revision, mesh, profile = _case()
    spec = revision.spec
    spec = replace(
        spec,
        solver_policy=replace(
            spec.solver_policy,
            increments=replace(
                spec.solver_policy.increments,
                initial_step=Quantity(0.1, "s"),
                adaptive=False,
                max_steps=10,
                max_step_retries=0,
            ),
        ),
        motion=replace(spec.motion, direction=replace(spec.motion.direction, x=0, y=0, z=-1)),
    )
    if outside_scope == "finite_friction":
        spec = replace(
            spec,
            contact=replace(
                spec.contact,
                friction=CoulombFriction(
                    Quantity(0.37, "1"),
                    evidence("contact.friction_model", "scope-friction-model"),
                    evidence("contact.friction_coefficient", "scope-friction-coefficient"),
                ),
            ),
        )
    else:
        support = spec.support.supports[0]
        spec = replace(
            spec,
            support=replace(
                spec.support,
                supports=(replace(support, x=replace(support.x, state="free")),),
            ),
        )
    profile = replace(
        profile,
        capabilities=(
            *profile.capabilities,
            CapabilityRef(
                "febio.scope.planar_linear_frictionless_fixed_xyz",
                CapabilityStatus.SUPPORTED,
                "4.12.0/0x35",
                "none",
                "tet10-canonical-v1",
                "synthetic-scope-only",
                (evidence("compatibility.scope", "synthetic-scope"),),
            ),
        ),
    )
    with pytest.raises(PortError) as rejected:
        _compile(tmp_path, replace(revision, spec=spec), mesh, profile)
    assert rejected.value.category is PortErrorCategory.UNSUPPORTED_CAPABILITY
