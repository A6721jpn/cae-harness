"""Mechanism limits for the explicitly tagged planar FEBio profile."""

from __future__ import annotations

from febio_cae.domain import (
    AsPlaced,
    CapabilityStatus,
    CaseSpec,
    CompatibilityProfile,
    Frictionless,
    IsotropicLinearElastic,
    PortError,
    PortErrorCategory,
)

PLANAR_SCOPE_CAPABILITY = "febio.scope.planar_linear_frictionless_fixed_xyz"


def _unsupported(reason: str) -> None:
    raise PortError(PortErrorCategory.UNSUPPORTED_CAPABILITY, reason)


def require_profile_scope(spec: CaseSpec, profile: CompatibilityProfile) -> None:
    """Require the explicit planar mechanism contract for tagged profiles.

    Compatibility profiles without :data:`PLANAR_SCOPE_CAPABILITY` retain the
    existing compiler/preparation behavior.  The marker is an authorization
    for the fixed planar mechanism only; it never supplies or changes case
    physics, frames, constraints, or numerical values.
    """

    if not isinstance(spec, CaseSpec) or not isinstance(profile, CompatibilityProfile):
        raise PortError(
            PortErrorCategory.INVALID_INPUT,
            "case spec and compatibility profile are required",
        )

    capability = next(
        (
            item
            for item in profile.capabilities
            if item.capability_id == PLANAR_SCOPE_CAPABILITY
        ),
        None,
    )
    if capability is None:
        return
    if capability.status is not CapabilityStatus.SUPPORTED:
        _unsupported(f"profile capability is not supported: {PLANAR_SCOPE_CAPABILITY}")

    if not isinstance(spec.material, IsotropicLinearElastic):
        _unsupported("planar profile requires isotropic linear-elastic material")
    if not isinstance(spec.contact.friction, Frictionless):
        _unsupported("planar profile requires frictionless contact")
    if spec.rigid_tool.primitive.kind != "box":
        _unsupported("planar profile requires one box rigid tool")
    if not isinstance(spec.contact.arrangement, AsPlaced):
        _unsupported("planar profile requires AsPlaced contact arrangement")

    direction = spec.motion.direction
    if (direction.frame.value, direction.x, direction.y, direction.z) != (
        "World",
        0.0,
        0.0,
        -1.0,
    ):
        _unsupported("planar profile requires negative World-z motion")

    supports = spec.support.supports
    if not supports or any(
        support.frame.value != "World"
        or support.transform is not None
        or any(getattr(support, axis).state != "fixed" for axis in ("x", "y", "z"))
        for support in supports
    ):
        _unsupported("planar profile requires untransformed World supports fixed in x/y/z")

    dofs = spec.rigid_tool.dofs
    if (
        dofs.frame.value != "World"
        or any(getattr(dofs, axis).state != "fixed" for axis in ("x", "y", "rx", "ry", "rz"))
        or dofs.z.state != "prescribed"
    ):
        _unsupported(
            "planar profile requires World rigid-tool x/y/rx/ry/rz fixed and z prescribed"
        )

    increments = spec.solver_policy.increments
    start = float(spec.motion.samples[0].time.to_si().value)
    end = float(spec.motion.samples[-1].time.to_si().value)
    step = float(increments.initial_step.to_si().value)
    if (
        start != 0.0
        or end != 1.0
        or increments.adaptive
        or step != 0.1
        or increments.max_steps < 10
        or increments.max_step_retries != 0
    ):
        _unsupported(
            "planar profile requires nonadaptive 0.1-second increments over 0..1 seconds "
            "with at least 10 steps and zero retries"
        )


__all__ = ["PLANAR_SCOPE_CAPABILITY", "require_profile_scope"]
