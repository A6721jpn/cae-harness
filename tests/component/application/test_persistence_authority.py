from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from febio_cae.application.service import RegisteredCaseService
from febio_cae.domain import (
    AsPlaced,
    BodyId,
    Budget,
    CaseRevision,
    CaseSpec,
    ContactId,
    ContactIntent,
    DofState,
    EvaluationRequest,
    EvidenceRef,
    FaceId,
    FaceMeasurement,
    FrameId,
    GeometryBodyFact,
    GeometryInspection,
    GeometryInspectionRequest,
    GeometryIntent,
    GeometrySelectionRequest,
    IsotropicLinearElastic,
    MaterialApplicability,
    MeshPolicy,
    MotionApplicability,
    MotionProfile,
    MotionSample,
    NumericalProfileRef,
    OutputPolicy,
    OutputRequest,
    Point3,
    QualityCriterion,
    QualityPolicy,
    QualityThreshold,
    Quantity,
    RigidDofComponent,
    RigidDofSpecification,
    RigidPrimitive,
    RigidToolIntent,
    RigidTransform,
    SelectionRef,
    SolidSupport,
    SolverControl,
    SolverPolicy,
    SupportComponent,
    SupportId,
    SupportSet,
    TimeIncrementPolicy,
    Translation3,
    UnitDirection,
    WholeBodyRule,
)
from febio_cae.domain.artifacts import SourceAssetContent
from febio_cae.domain.compatibility import (
    CapabilityRef,
    CapabilityStatus,
    CompatibilityProfile,
    OutputMapping,
    ToolIdentity,
)
from febio_cae.domain.contact import Frictionless
from febio_cae.domain.partial_case_spec import PartialCaseSpec
from febio_cae.domain.ports import PortError, PortErrorCategory, TrustedOwnerContext
from febio_cae.domain.selection import ResolutionSnapshot
from febio_cae.domain.spatial import ProperRotation
from febio_cae.storage.registry import (
    CaseStorage,
    InjectedStorageFailure,
    StorageConflictError,
)

CAD_BYTES = b"step-content"
CAD_DIGEST = hashlib.sha256(CAD_BYTES).hexdigest()
PART_DIGEST = "a" * 64
TOOL_DIGEST = "b" * 64
INSPECTION_DIGEST = "c" * 64


def _evidence(target: str, seed: str = "same") -> EvidenceRef:
    return EvidenceRef("1", "registered_document", "cad", target, CAD_DIGEST)


def _profile(
    profile_id: str, status: CapabilityStatus = CapabilityStatus.SUPPORTED
) -> CompatibilityProfile:
    return CompatibilityProfile(
        profile_id,
        ToolIdentity("synthetic", "1", "1" * 64),
        ToolIdentity("synthetic-reader", "1", "2" * 64),
        [
            CapabilityRef(
                "synthetic",
                status,
                "1",
                "none",
                "order",
                "sign",
                (_evidence("profile"),),
            )
        ],
        [
            OutputMapping(
                "displacement",
                "displacement",
                "node",
                "scalar",
                "mm",
                FrameId("World"),
                1,
                1,
                "max",
            )
        ],
        (_evidence("profile"),),
    )


def _profile_digest(profile_id: str) -> str:
    return hashlib.sha256(_profile(profile_id).to_bytes()).hexdigest()


def _identity(source: FrameId, target: FrameId) -> RigidTransform:
    return RigidTransform(
        source,
        target,
        Translation3(target, Quantity(0, "m"), Quantity(0, "m"), Quantity(0, "m")),
        ProperRotation(((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))),
    )


def complete_spec(*, solver_digest: str | None = None) -> CaseSpec:
    world = FrameId("World")
    part = BodyId("part")
    tool = BodyId("tool")
    part_selection = SelectionRef(
        "part-contact",
        "part_contact_surface",
        _evidence("selection.role"),
        PART_DIGEST,
        part,
        world,
        WholeBodyRule(part),
    )
    tool_selection = SelectionRef(
        "tool-contact",
        "tool_contact_surface",
        _evidence("selection.role"),
        TOOL_DIGEST,
        tool,
        world,
        WholeBodyRule(tool),
    )
    output_selection = SelectionRef(
        "part-output",
        "output_region",
        _evidence("selection.role"),
        PART_DIGEST,
        part,
        world,
        WholeBodyRule(part),
    )
    geometry = GeometryIntent(
        CAD_DIGEST,
        PART_DIGEST,
        INSPECTION_DIGEST,
        part,
        "mm",
        _identity(FrameId("PartLocal"), world),
        _evidence("geometry.body_id"),
        _evidence("geometry.step_unit"),
        _evidence("geometry.placement"),
    )
    material = IsotropicLinearElastic(
        Quantity(1, "MPa"),
        Quantity(0.3, "1"),
        _evidence("material.model"),
        _evidence("material.youngs_modulus"),
        _evidence("material.poisson_ratio"),
        MaterialApplicability(
            "strain applies",
            _evidence("material.strain_applicability"),
            "rate applies",
            _evidence("material.rate_applicability"),
        ),
    )
    support = SupportSet(
        [
            SolidSupport(
                SupportId("support"),
                SelectionRef(
                    "support",
                    "support_surface",
                    _evidence("selection.role"),
                    PART_DIGEST,
                    part,
                    world,
                    WholeBodyRule(part),
                ),
                world,
                SupportComponent("fixed", _evidence("support.x")),
                SupportComponent("free", _evidence("support.y")),
                SupportComponent("fixed", _evidence("support.z")),
                frame_evidence=_evidence("support.frame"),
            )
        ]
    )
    rigid = RigidToolIntent(
        RigidPrimitive(
            "sphere",
            tool,
            FrameId("ToolLocal"),
            _identity(FrameId("ToolLocal"), world),
            {"radius": Quantity(2, "mm")},
            {"radius": _evidence("rigid_tool.radius")},
            _evidence("rigid_tool.model"),
            _evidence("rigid_tool.placement"),
        ),
        tool_selection,
        RigidDofSpecification(
            world,
            RigidDofComponent(DofState.FIXED, _evidence("rigid_tool.x")),
            RigidDofComponent(DofState.FIXED, _evidence("rigid_tool.y")),
            RigidDofComponent(DofState.PRESCRIBED, _evidence("rigid_tool.z")),
            RigidDofComponent(DofState.FIXED, _evidence("rigid_tool.rx")),
            RigidDofComponent(DofState.FIXED, _evidence("rigid_tool.ry")),
            RigidDofComponent(DofState.FIXED, _evidence("rigid_tool.rz")),
            _evidence("rigid_tool.frame"),
        ),
        _evidence("rigid_tool.contact_surface"),
    )
    motion = MotionProfile(
        UnitDirection(world, 0.0, 0.0, 1.0),
        Point3(world, Quantity(0, "mm"), Quantity(0, "mm"), Quantity(0, "mm")),
        [
            MotionSample(Quantity(0, "s"), Quantity(0, "mm")),
            MotionSample(Quantity(1, "s"), Quantity(1, "mm")),
        ],
        MotionApplicability(
            "quasi-static",
            _evidence("motion.quasi_static_applicability"),
            "rate-independent",
            _evidence("motion.rate_independent_applicability"),
        ),
        _evidence("motion.direction"),
        _evidence("motion.initial_reference_point"),
        _evidence("motion.history"),
    )
    contact = ContactIntent(
        ContactId("contact"),
        part_selection,
        tool_selection,
        world,
        _evidence("contact.part_surface"),
        _evidence("contact.tool_surface"),
        _evidence("contact.frame"),
        Frictionless(_evidence("contact.friction_model")),
        AsPlaced(_evidence("contact.arrangement")),
    )
    outputs = OutputPolicy(
        NumericalProfileRef("outputs", "outputs", _profile_digest("outputs")),
        [
            OutputRequest(
                "request",
                "displacement",
                "max",
                "z",
                "node",
                output_selection,
                world,
                "mm",
                _evidence("outputs.requests.request"),
            )
        ],
        [Quantity(0, "s"), Quantity(1, "s")],
        [
            EvaluationRequest(
                "evaluation",
                "request",
                "peak",
                output_selection,
                [Quantity(1, "s")],
                _evidence("outputs.evaluations.evaluation"),
            )
        ],
    )
    quality = QualityPolicy(
        NumericalProfileRef("quality", "quality", _profile_digest("quality")),
        [
            QualityCriterion(
                "criterion",
                "residual",
                ["evaluation"],
                [QualityThreshold("tol", Quantity(1, "MPa"))],
                "applicable",
                _evidence("quality_policy.criteria.criterion"),
            )
        ],
    )
    return CaseSpec(
        geometry,
        material,
        support,
        rigid,
        motion,
        contact,
        MeshPolicy(
            "tet10",
            Quantity(2, "mm"),
            [],
            NumericalProfileRef("mesh", "mesh_quality", _profile_digest("mesh")),
            0,
        ),
        SolverPolicy(
            NumericalProfileRef(
                "solver",
                "solver",
                _profile_digest("solver") if solver_digest is None else solver_digest,
            ),
            [SolverControl("alpha", 1)],
            TimeIncrementPolicy(
                Quantity(100, "ms"),
                Quantity(10, "ms"),
                Quantity(1, "s"),
                True,
                2,
                0,
                [Quantity(0, "s")],
            ),
            [],
        ),
        outputs,
        quality,
        Budget(Quantity(10, "s"), 1, 1, 0, 0),
    )


class SyntheticGeometry:
    def inspect(
        self, request: GeometryInspectionRequest, source: SourceAssetContent
    ) -> GeometryInspection:
        return GeometryInspection(
            request.source_asset,
            INSPECTION_DIGEST,
            "mm",
            ("part",),
            ("part",),
            (GeometryBodyFact("part", 1, 1.0),),
        )

    def resolve_selection(
        self, request: GeometrySelectionRequest, source: SourceAssetContent
    ) -> ResolutionSnapshot:
        selection = request.selection
        return ResolutionSnapshot(
            selection.geometry_digest,
            selection.body_id,
            selection.frame,
            (
                FaceMeasurement(
                    FaceId(f"{selection.name}-face"),
                    Quantity(1, "mm2"),
                    Point3(
                        selection.frame,
                        Quantity(0, "mm"),
                        Quantity(0, "mm"),
                        Quantity(0, "mm"),
                    ),
                ),
            ),
        )


class SyntheticProfiles:
    def get_profile(self, profile_id: str) -> CompatibilityProfile:
        return _profile(profile_id)


class SelectionFailGeometry(SyntheticGeometry):
    def resolve_selection(
        self, request: GeometrySelectionRequest, source: SourceAssetContent
    ) -> ResolutionSnapshot:
        raise PortError(PortErrorCategory.INTEGRITY, "selection resolution failed")


class ForeignBodyGeometry(SyntheticGeometry):
    def resolve_selection(
        self, request: GeometrySelectionRequest, source: SourceAssetContent
    ) -> ResolutionSnapshot:
        selection = request.selection
        return ResolutionSnapshot(
            selection.geometry_digest,
            BodyId("foreign-body"),
            selection.frame,
            (
                FaceMeasurement(
                    FaceId(f"{selection.name}-face"),
                    Quantity(1, "mm2"),
                    Point3(
                        selection.frame,
                        Quantity(0, "mm"),
                        Quantity(0, "mm"),
                        Quantity(0, "mm"),
                    ),
                ),
            ),
        )


class UnverifiedProfiles(SyntheticProfiles):
    def get_profile(self, profile_id: str) -> CompatibilityProfile:
        return _profile(profile_id, CapabilityStatus.UNVERIFIED)


def _created(
    tmp_path: Path,
    *,
    failure_injector: Callable[[str], None] | None = None,
    geometry: Any | None = None,
    compatibility: Any | None = None,
) -> tuple[RegisteredCaseService, Any, CaseStorage]:
    source = tmp_path / "source.step"
    source.write_bytes(CAD_BYTES)
    service = RegisteredCaseService(
        state_dir=tmp_path / "state",
        geometry=SyntheticGeometry() if geometry is None else geometry,
        compatibility=SyntheticProfiles() if compatibility is None else compatibility,
    )
    created = service.create_case(case_root=tmp_path / "case", cad_path=source)
    storage = CaseStorage(tmp_path / "case", failure_injector=failure_injector)
    return service, created, storage


def _populate_complete(
    service: RegisteredCaseService, created: Any, spec: CaseSpec | None = None
) -> None:
    spec = complete_spec() if spec is None else spec
    service.set_spec(
        created.case_id,
        values=PartialCaseSpec(
            geometry=spec.geometry,
            material=spec.material,
            support=spec.support,
            rigid_tool=spec.rigid_tool,
            motion=spec.motion,
            contact=spec.contact,
            mesh_policy=spec.mesh_policy,
            solver_policy=spec.solver_policy,
            outputs=spec.outputs,
            quality_policy=spec.quality_policy,
            budget=spec.budget,
        ),
        expected_generation=0,
        evidence=(_evidence("case_revision.spec"),),
    )


@pytest.mark.parametrize("geometry", [SelectionFailGeometry(), ForeignBodyGeometry()])
def test_validation_re_resolves_registered_selections(
    tmp_path: Path, geometry: SyntheticGeometry
) -> None:
    service, created, _ = _created(tmp_path, geometry=geometry)
    _populate_complete(service, created)

    result = service.validate_case(created.case_id)

    assert result.status != "VALIDATED"
    assert any(d.field == "selection" for d in result.diagnostics)


def test_validation_rejects_same_id_profile_with_stale_record_digest(tmp_path: Path) -> None:
    service, created, _ = _created(tmp_path)
    _populate_complete(service, created, complete_spec(solver_digest="0" * 64))

    result = service.validate_case(created.case_id)

    assert result.status != "VALIDATED"
    assert any(d.field == "solver_policy.profile" for d in result.diagnostics)


def test_validation_rejects_unverified_profile_capability(tmp_path: Path) -> None:
    service, created, _ = _created(tmp_path, compatibility=UnverifiedProfiles())
    _populate_complete(service, created)

    result = service.validate_case(created.case_id)

    assert result.status != "VALIDATED"
    assert any(d.field == "solver_policy.profile" for d in result.diagnostics)


def test_synthetic_validated_freeze_publishes_immutable_revision(tmp_path: Path) -> None:
    service, created, _ = _created(tmp_path)
    _populate_complete(service, created)
    draft = service.current_draft(created.case_id)
    assert service.validate_case(created.case_id).status == "VALIDATED"
    result = service.freeze_case(created.case_id)
    assert result.status == "FROZEN"
    assert result.revision_id is not None
    assert service.get_revision(created.case_id, result.revision_id).spec_digest
    assert draft.generation == 1


@pytest.mark.parametrize(
    "boundary", ["after_prepare", "after_file_write", "after_file_replace", "after_commit"]
)
def test_revision_publication_recovers_after_each_boundary(tmp_path: Path, boundary: str) -> None:
    service, created, _ = _created(tmp_path)
    _populate_complete(service, created)
    spec = complete_spec()

    def gate(point: str) -> None:
        if point == boundary:
            raise InjectedStorageFailure(point)

    failing = CaseStorage(tmp_path / "case", failure_injector=gate)
    draft = failing.current_draft(created.case_id)
    revision = CaseRevision(
        created.case_id, "revision-crash", None, None, spec, (_evidence("case_revision.spec"),)
    )
    with pytest.raises(InjectedStorageFailure):
        failing.register_revision_if_current(revision, expected_generation=draft.generation)
    reopened = CaseStorage(tmp_path / "case")
    if boundary in {"after_file_replace", "after_commit"}:
        assert (
            reopened.get_revision(created.case_id, "revision-crash").revision_id == "revision-crash"
        )
    else:
        with pytest.raises(StorageConflictError):
            reopened.get_revision(created.case_id, "revision-crash")


def test_duplicate_owner_claim_is_rejected(tmp_path: Path) -> None:
    _, created, storage = _created(tmp_path)
    owner = TrustedOwnerContext(created.case_id, "run-1", "attempt-1", 0)
    storage.claim(owner)
    with pytest.raises(PortError):
        storage.claim(owner)
