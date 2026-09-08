"""Registered case lifecycle service used by the explicit CLI and adapters."""

from __future__ import annotations

import hashlib
import stat
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from febio_cae.domain.artifacts import (
    FileEntry,
    GeometryInspectionRequest,
    GeometrySelectionRequest,
    MeshArtifact,
    SourceAssetContent,
    SourceAssetRef,
)
from febio_cae.domain.canonical import canonical_bytes
from febio_cae.domain.case_draft import CaseDraft
from febio_cae.domain.case_patch import CasePatch
from febio_cae.domain.case_revision import CaseRevision
from febio_cae.domain.compatibility import CapabilityStatus, CompatibilityProfile
from febio_cae.domain.evidence import EvidenceRef
from febio_cae.domain.execution import AttemptRecord, ExecutionBundle
from febio_cae.domain.lifecycle import RunState, ServiceDiagnostic, ServiceErrorCategory
from febio_cae.domain.mesh_policy import NumericalProfileRef
from febio_cae.domain.partial_case_spec import PartialCaseSpec
from febio_cae.domain.ports import (
    CompatibilityRegistryPort,
    GeometryPort,
    OwnershipPort,
    PortError,
    PortErrorCategory,
    RunnerPort,
    TrustedOwnerContext,
)
from febio_cae.domain.questions import IssuedQuestion
from febio_cae.domain.results import ResultManifest
from febio_cae.domain.selection import FaceSetRule, ResolutionSnapshot, SelectionRef
from febio_cae.domain.units import Quantity
from febio_cae.storage.catalog import CaseCatalog, CaseCatalogError
from febio_cae.storage.mesh_quality import MeshQualityRecord, PlanarDemoRegistration
from febio_cae.storage.profiles import SQLiteCompatibilityRegistry
from febio_cae.storage.registry import (
    CaseStorage,
    StorageConflictError,
    StorageIntegrityError,
    check_answer_values,
    nested_evidence,
)
from febio_cae.storage.state import ProductState

from ._execution import _CleanupObligation, _pending_cleanup, _RunnerOwner
from ._geometry import PlacedSelection, _PlacedGeometry
from .specs import SourceDeclaration


class ServiceConflictError(RuntimeError):
    """Raised when caller input would bypass a registered authority boundary."""


class ConcurrentUpdateError(ServiceConflictError):
    """Raised when an expected draft generation is stale."""


@dataclass(frozen=True, slots=True)
class CreatedCase:
    case_id: str
    case_root: Path
    source_asset: SourceAssetRef
    draft: CaseDraft


@dataclass(frozen=True, slots=True)
class ServiceResult:
    status: str
    case_id: str
    revision_id: str | None = None
    draft: CaseDraft | None = None
    revision: CaseRevision | None = None
    diagnostics: tuple[ServiceDiagnostic, ...] = ()
    next_actions: tuple[str, ...] = ()
    geometry: object | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "1",
            "status": self.status,
            "case_id": self.case_id,
            "revision_id": self.revision_id,
            "run_id": None,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "next_actions": list(self.next_actions),
            "draft": None if self.draft is None else self.draft.to_dict(),
            "revision": None if self.revision is None else self.revision.to_dict(),
            "geometry": (
                None
                if self.geometry is None
                else getattr(self.geometry, "to_dict", lambda: self.geometry)()
            ),
        }


def _now_source_media_type(path: Path) -> str:
    if path.suffix.casefold() in {".step", ".stp"}:
        return "model/step"
    return "application/octet-stream"


def _path_is_reparse(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    except FileNotFoundError:
        return False


def _diagnostic(
    category: ServiceErrorCategory,
    message: str,
    field: str | None = None,
    retryable: bool = False,
) -> ServiceDiagnostic:
    return ServiceDiagnostic(category, message, field, retryable)


def _service_category(category: PortErrorCategory) -> ServiceErrorCategory:
    return ServiceErrorCategory(category.value)


def _registered_selections(values: PartialCaseSpec) -> tuple[SelectionRef, ...]:
    selections: list[SelectionRef] = []
    if values.support is not None:
        selections.extend(item.selection for item in values.support.supports)
    if values.rigid_tool is not None:
        selections.append(values.rigid_tool.contact_surface)
    if values.contact is not None:
        selections.extend((values.contact.part_surface, values.contact.tool_surface))
    if values.mesh_policy is not None:
        selections.extend(item.selection for item in values.mesh_policy.local_refinements)
    if values.outputs is not None:
        selections.extend(item.selection for item in values.outputs.requests)
        selections.extend(item.selection for item in values.outputs.evaluations)
    return tuple(selections)


def _nested_evidence(values: PartialCaseSpec) -> tuple[EvidenceRef, ...]:
    return nested_evidence(values)


def _merge_evidence(*groups: Sequence[EvidenceRef]) -> tuple[EvidenceRef, ...]:
    unique = {canonical_bytes(item.to_dict()): item for group in groups for item in group}
    return tuple(unique.values())


def _resolved_values(value: Any, resolutions: Mapping[bytes, ResolutionSnapshot]) -> Any:
    if isinstance(value, SelectionRef):
        return replace(value, resolution=resolutions[value.to_bytes()])
    if isinstance(value, Mapping):
        return {key: _resolved_values(child, resolutions) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return tuple(_resolved_values(child, resolutions) for child in value)
    if is_dataclass(value):
        return replace(
            cast(Any, value),
            **{
                field.name: _resolved_values(getattr(value, field.name), resolutions)
                for field in fields(value)
                if field.init
            },
        )
    return value


class RegisteredCaseService:
    """Application authority for case registration, drafts, validation and freeze."""

    def __init__(
        self,
        *,
        state_dir: Path | str | None = None,
        geometry: GeometryPort | None = None,
        compatibility: CompatibilityRegistryPort | None = None,
        placed_selection: PlacedSelection | None = None,
    ) -> None:
        self.state = ProductState(state_dir)
        self.catalog = CaseCatalog(self.state.catalog_path)
        self.geometry = geometry
        self._placed_selection = placed_selection
        self.compatibility = compatibility or SQLiteCompatibilityRegistry(
            self.state.compatibility_path
        )

    def _storage(self, case_id: str) -> CaseStorage:
        try:
            return CaseStorage(self.catalog.resolve(case_id))
        except CaseCatalogError as error:
            raise ServiceConflictError(str(error)) from error

    def create_case(self, *, case_root: Path | str, cad_path: Path | str) -> CreatedCase:
        cad = Path(cad_path).expanduser().absolute()
        if not cad.is_file() or _path_is_reparse(cad):
            raise ServiceConflictError("--cad must be an existing regular file, not a link")
        root = Path(case_root).expanduser().absolute()
        if root.exists() and _path_is_reparse(root):
            raise ServiceConflictError("--case-root must not be a link or reparse point")
        content = cad.read_bytes()
        source = SourceAssetRef(
            asset_id="cad",
            content_digest=hashlib.sha256(content).hexdigest(),
            media_type=_now_source_media_type(cad),
        )
        case_id = f"case-{uuid.uuid4().hex[:12]}"
        created_at = datetime.now(UTC).isoformat()
        try:
            CaseStorage.initialize(
                root,
                case_id=case_id,
                source_asset=source,
                source_kind="registered_document",
                source_content=content,
                created_at=created_at,
            )
            self.catalog.register(case_id, root, created_at)
        except (CaseCatalogError, StorageConflictError, StorageIntegrityError) as error:
            raise ServiceConflictError(str(error)) from error
        storage = self._storage(case_id)
        return CreatedCase(case_id, root, source, storage.current_draft(case_id))

    def current_draft(self, case_id: str) -> CaseDraft:
        return self._storage(case_id).current_draft(case_id)

    def get_revision(self, case_id: str, revision_id: str) -> CaseRevision:
        return self._storage(case_id).get_revision(case_id, revision_id)

    def resolve_source(self, case_id: str, asset_id: str) -> SourceAssetContent:
        return self._storage(case_id).resolve_source(self._storage(case_id).source_asset(asset_id))

    def _merge_values(self, current: PartialCaseSpec, incoming: PartialCaseSpec) -> PartialCaseSpec:
        fields = (
            "geometry",
            "material",
            "support",
            "rigid_tool",
            "motion",
            "contact",
            "mesh_policy",
            "solver_policy",
            "outputs",
            "quality_policy",
            "budget",
        )
        return PartialCaseSpec(
            **{
                field: getattr(incoming, field)
                if getattr(incoming, field) is not None
                else getattr(current, field)
                for field in fields
            }
        )

    def _resolve_declarations(
        self,
        storage: CaseStorage,
        declarations: Sequence[SourceDeclaration],
        evidence: Sequence[EvidenceRef],
    ) -> tuple[EvidenceRef, ...]:
        created: list[EvidenceRef] = list(evidence)
        for declaration in declarations:
            if declaration.content is None:
                try:
                    asset = storage.source_asset(declaration.reference)
                    if storage.source_kind(declaration.reference) != declaration.source_kind:
                        raise ServiceConflictError(
                            "source declaration kind does not match registration"
                        )
                except StorageConflictError as error:
                    raise ServiceConflictError(
                        f"source declaration {declaration.reference!r} is not registered"
                    ) from error
                if (
                    declaration.content_digest is not None
                    and declaration.content_digest != asset.content_digest
                ):
                    raise ServiceConflictError("registered source declaration digest is stale")
            else:
                try:
                    asset = storage.ingest_source(
                        asset_id=declaration.reference,
                        source_kind=declaration.source_kind,
                        media_type=declaration.media_type,
                        content=declaration.content,
                        expected_digest=declaration.content_digest,
                    )
                except (StorageConflictError, StorageIntegrityError, ValueError) as error:
                    raise ServiceConflictError(str(error)) from error
            created.append(
                EvidenceRef(
                    schema_version="1",
                    source_kind=declaration.source_kind,
                    reference=asset.asset_id,
                    target_field=declaration.target_field,
                    content_digest=asset.content_digest,
                )
            )
        unique: dict[bytes, EvidenceRef] = {}
        for item in created:
            try:
                asset = storage.source_asset(item.reference)
            except StorageConflictError as error:
                raise ServiceConflictError(
                    f"evidence source {item.reference!r} is not registered"
                ) from error
            if storage.source_kind(item.reference) != item.source_kind:
                raise ServiceConflictError("evidence source kind does not match registration")
            if asset.content_digest != item.content_digest:
                raise ServiceConflictError("evidence refers to stale source bytes")
            try:
                storage.resolve_source(asset)
            except PortError as error:
                raise ServiceConflictError(str(error)) from error
            unique[canonical_bytes(item.to_dict())] = item
        return tuple(unique.values())

    def set_spec(
        self,
        case_id: str,
        *,
        values: PartialCaseSpec,
        expected_generation: int,
        evidence: Sequence[EvidenceRef] = (),
        source_declarations: Sequence[SourceDeclaration] = (),
        input_intent: str = "",
    ) -> CaseDraft:
        if not isinstance(values, PartialCaseSpec):
            raise ServiceConflictError("values must be a PartialCaseSpec")
        storage = self._storage(case_id)
        current = storage.current_draft(case_id)
        resolved_evidence = self._resolve_declarations(storage, source_declarations, evidence)
        merged_values = self._merge_values(current.values, values)
        merged_evidence = _merge_evidence(current.evidence, resolved_evidence)
        draft = CaseDraft(
            case_id=case_id,
            draft_id=f"draft-{uuid.uuid4().hex[:12]}",
            generation=expected_generation + 1,
            input_intent=input_intent if input_intent else current.input_intent,
            parent_revision_id=current.parent_revision_id,
            parent_spec_digest=current.parent_spec_digest,
            values=merged_values,
            evidence=merged_evidence,
        )
        try:
            return storage.set_draft(draft, expected_generation=expected_generation)
        except StorageConflictError as error:
            raise ConcurrentUpdateError(str(error)) from error

    def issue_question(
        self,
        case_id: str,
        *,
        target_fields: Sequence[str],
        question_time_evidence: Sequence[EvidenceRef],
    ) -> IssuedQuestion:
        storage = self._storage(case_id)
        draft = storage.current_draft(case_id)
        self._resolve_declarations(storage, (), question_time_evidence)
        question = IssuedQuestion(
            question_id=f"question-{uuid.uuid4().hex[:12]}",
            case_id=case_id,
            draft_id=draft.draft_id,
            generation=draft.generation,
            target_fields=tuple(target_fields),
            question_time_evidence=tuple(question_time_evidence),
        )
        try:
            storage.issue_question(question)
        except StorageConflictError as error:
            raise ServiceConflictError(str(error)) from error
        return question

    def answer_question(
        self,
        case_id: str,
        question_id: str,
        *,
        values: PartialCaseSpec,
        expected_generation: int,
        evidence: Sequence[EvidenceRef],
        source_declarations: Sequence[SourceDeclaration] = (),
    ) -> CaseDraft:
        storage = self._storage(case_id)
        current = storage.current_draft(case_id)
        question = storage.get_question(question_id)
        if question.case_id != case_id or question.generation != expected_generation:
            raise ServiceConflictError("question is bound to another case or generation")
        try:
            check_answer_values(
                current.values,
                self._merge_values(current.values, values),
                tuple(question.target_fields),
            )
        except StorageConflictError as error:
            raise ServiceConflictError(str(error)) from error
        resolved = self._resolve_declarations(storage, source_declarations, evidence)
        provided = {repr(item.to_dict()) for item in resolved}
        if any(repr(item.to_dict()) not in provided for item in question.question_time_evidence):
            raise ServiceConflictError("question-time evidence must be retained in the answer")
        draft = CaseDraft(
            case_id=case_id,
            draft_id=f"draft-{uuid.uuid4().hex[:12]}",
            generation=expected_generation + 1,
            input_intent=current.input_intent,
            parent_revision_id=current.parent_revision_id,
            parent_spec_digest=current.parent_spec_digest,
            values=self._merge_values(current.values, values),
            evidence=_merge_evidence(current.evidence, resolved),
        )
        try:
            return storage.consume_question(
                question_id, draft, expected_generation=expected_generation
            )
        except StorageConflictError as error:
            raise ServiceConflictError(str(error)) from error

    def apply_patch(
        self, case_id: str, patch: CasePatch, *, expected_generation: int | None = None
    ) -> CaseDraft:
        storage = self._storage(case_id)
        current = storage.current_draft(case_id)
        parent = storage.get_revision(case_id, patch.parent_revision_id)
        if parent.spec_digest != patch.parent_spec_digest:
            raise ServiceConflictError("patch parent digest is stale")
        if expected_generation is None:
            expected_generation = storage.revision_generation(parent.revision_id)
        if expected_generation != current.generation:
            raise ServiceConflictError("patch requires the current explicit draft context")
        if storage.current_frozen_revision(case_id) != parent.revision_id:
            raise ServiceConflictError("patch parent is not the current frozen revision")
        self._resolve_declarations(storage, (), patch.evidence)
        fields = (
            "geometry",
            "material",
            "support",
            "rigid_tool",
            "motion",
            "contact",
            "mesh_policy",
            "solver_policy",
            "outputs",
            "quality_policy",
            "budget",
        )
        values = {field: getattr(current.values, field) for field in fields}
        for edit in patch.edits:
            values[edit.field] = edit.value if edit.present else None
        draft = CaseDraft(
            case_id=case_id,
            draft_id=f"draft-{uuid.uuid4().hex[:12]}",
            generation=current.generation + 1,
            input_intent=current.input_intent,
            parent_revision_id=parent.revision_id,
            parent_spec_digest=parent.spec_digest,
            values=PartialCaseSpec(**values),
            evidence=_merge_evidence(current.evidence, patch.evidence),
        )
        try:
            return storage.set_draft(
                draft,
                expected_generation=expected_generation,
                patch_digest=hashlib.sha256(patch.to_bytes()).hexdigest(),
            )
        except StorageConflictError as error:
            raise ConcurrentUpdateError(str(error)) from error

    def inspect_case(self, case_id: str) -> ServiceResult:
        storage = self._storage(case_id)
        draft = storage.current_draft(case_id)
        if draft.values.geometry is None or self.geometry is None:
            return ServiceResult("REGISTERED", case_id, draft=draft)
        try:
            source_ref = storage.source_asset("cad")
            source = storage.resolve_source(source_ref)
            inspection = self.geometry.inspect(GeometryInspectionRequest(source_ref), source)
            return ServiceResult("INSPECTED", case_id, draft=draft, geometry=inspection)
        except PortError as error:
            return ServiceResult(
                "UNSUPPORTED_ENVIRONMENT",
                case_id,
                draft=draft,
                diagnostics=(
                    _diagnostic(_service_category(error.category), str(error), "geometry"),
                ),
            )

    def _validate(self, case_id: str) -> ServiceResult:
        with self._storage(case_id).evidence_snapshot():
            return self._validate_pinned(case_id)

    def _validate_pinned(self, case_id: str) -> ServiceResult:
        storage = self._storage(case_id)
        draft = storage.current_draft(case_id)
        diagnostics: list[ServiceDiagnostic] = []
        resolutions: dict[bytes, ResolutionSnapshot] = {}
        for evidence in (*draft.evidence, *_nested_evidence(draft.values)):
            try:
                asset = storage.source_asset(evidence.reference)
                if storage.source_kind(evidence.reference) != evidence.source_kind:
                    diagnostics.append(
                        _diagnostic(
                            ServiceErrorCategory.INTEGRITY,
                            f"evidence target {evidence.target_field!r} has a mismatched source kind",
                            "evidence",
                        )
                    )
                elif asset.content_digest != evidence.content_digest:
                    diagnostics.append(
                        _diagnostic(
                            ServiceErrorCategory.INTEGRITY,
                            f"evidence target {evidence.target_field!r} has a stale source digest",
                            "evidence",
                        )
                    )
                else:
                    storage.resolve_source(asset)
            except (StorageConflictError, PortError) as error:
                diagnostics.append(
                    _diagnostic(
                        ServiceErrorCategory.INTEGRITY,
                        f"evidence target {evidence.target_field!r} is not registered: {error}",
                        "evidence",
                    )
                )

        if draft.parent_revision_id is not None:
            try:
                parent = storage.get_revision(case_id, draft.parent_revision_id)
                if parent.spec_digest != draft.parent_spec_digest:
                    diagnostics.append(
                        _diagnostic(
                            ServiceErrorCategory.CONFLICT,
                            "draft parent digest is not the registered revision digest",
                            "parent_spec_digest",
                        )
                    )
            except StorageConflictError as error:
                diagnostics.append(
                    _diagnostic(ServiceErrorCategory.CONFLICT, str(error), "parent_revision_id")
                )

        geometry = draft.values.geometry
        if geometry is None:
            diagnostics.append(
                _diagnostic(
                    ServiceErrorCategory.NEEDS_PHYSICAL_INPUT,
                    "geometry inspection and body selection are unresolved",
                    "geometry",
                )
            )
            if self.geometry is None:
                diagnostics.append(
                    _diagnostic(
                        ServiceErrorCategory.UNSUPPORTED_CAPABILITY,
                        "no registered geometry inspection capability is available",
                        "geometry",
                    )
                )
        elif self.geometry is None:
            diagnostics.append(
                _diagnostic(
                    ServiceErrorCategory.UNSUPPORTED_CAPABILITY,
                    "no registered geometry inspection capability is available",
                    "geometry",
                )
            )
        else:
            try:
                source_ref = storage.source_asset("cad")
                if source_ref.content_digest != geometry.source_step_digest:
                    diagnostics.append(
                        _diagnostic(
                            ServiceErrorCategory.INTEGRITY,
                            "geometry source_step_digest is not the registered CAD digest",
                            "geometry.source_step_digest",
                        )
                    )
                source = storage.resolve_source(source_ref)
                inspection = self.geometry.inspect(
                    GeometryInspectionRequest(source_ref, (geometry.body_id.value,)), source
                )
                if (
                    Quantity(1, inspection.declared_unit).to_si()
                    != Quantity(1, geometry.step_unit).to_si()
                ):
                    diagnostics.append(
                        _diagnostic(
                            ServiceErrorCategory.CONFLICT,
                            "declared STEP unit conflicts with inspected source",
                            "geometry.step_unit",
                        )
                    )
                if inspection.inspection_digest != geometry.inspection_digest:
                    diagnostics.append(
                        _diagnostic(
                            ServiceErrorCategory.INTEGRITY,
                            "geometry inspection digest is stale",
                            "geometry.inspection_digest",
                        )
                    )
                if geometry.body_id.value not in inspection.closed_solid_body_ids:
                    diagnostics.append(
                        _diagnostic(
                            ServiceErrorCategory.NEEDS_PHYSICAL_INPUT,
                            "declared geometry body is not an inspected closed solid",
                            "geometry.body_id",
                        )
                    )
                selection_geometry: GeometryPort = self.geometry
                if self._placed_selection is not None and draft.values.rigid_tool is not None:
                    selection_geometry = _PlacedGeometry(
                        self.geometry,
                        source,
                        geometry,
                        draft.values.rigid_tool,
                        self._placed_selection,
                    )
                for selection in _registered_selections(draft.values):
                    try:
                        resolved = selection_geometry.resolve_selection(
                            GeometrySelectionRequest(
                                source_ref,
                                selection,
                                geometry
                                if (
                                    geometry.source_step_digest == source_ref.content_digest
                                    and geometry.body_id == selection.body_id
                                    and geometry.geometry_digest == selection.geometry_digest
                                )
                                else None,
                            ),
                            source,
                        )
                        resolutions[selection.to_bytes()] = resolved
                        if not isinstance(resolved, ResolutionSnapshot) or (
                            resolved.geometry_digest != selection.geometry_digest
                            or resolved.body_id != selection.body_id
                            or resolved.frame != selection.frame
                            or (
                                isinstance(selection.rule, FaceSetRule)
                                and {face.face_id for face in resolved.faces}
                                != set(selection.rule.face_ids)
                            )
                            or (
                                selection.resolution is not None
                                and canonical_bytes(
                                    resolved.to_dict(), unordered_paths=(("faces",),)
                                )
                                != canonical_bytes(
                                    selection.resolution.to_dict(), unordered_paths=(("faces",),)
                                )
                            )
                        ):
                            diagnostics.append(
                                _diagnostic(
                                    ServiceErrorCategory.INTEGRITY,
                                    "geometry selection resolution does not match the registered selection context",
                                    "selection",
                                )
                            )
                    except NotImplementedError as error:
                        diagnostics.append(
                            _diagnostic(
                                ServiceErrorCategory.UNSUPPORTED_CAPABILITY,
                                f"registered geometry selection resolution is unavailable: {error}",
                                "selection",
                            )
                        )
                    except ValueError as error:
                        diagnostics.append(
                            _diagnostic(ServiceErrorCategory.INTEGRITY, str(error), "selection")
                        )
                    except PortError as error:
                        diagnostics.append(
                            _diagnostic(_service_category(error.category), str(error), "selection")
                        )
            except PortError as error:
                diagnostics.append(
                    _diagnostic(_service_category(error.category), str(error), "geometry")
                )

        solver = draft.values.solver_policy
        if solver is None:
            diagnostics.append(
                _diagnostic(
                    ServiceErrorCategory.NEEDS_PHYSICAL_INPUT,
                    "solver profile and controls are unresolved",
                    "solver_policy",
                )
            )
            if isinstance(self.compatibility, SQLiteCompatibilityRegistry):
                diagnostics.append(
                    _diagnostic(
                        ServiceErrorCategory.UNSUPPORTED_CAPABILITY,
                        "no compatibility profile can be resolved before solver_policy is supplied",
                        "solver_policy.profile",
                    )
                )
        else:
            profile_requests = (
                ("solver_policy.profile", solver.profile),
                ("mesh_policy.quality_profile", draft.values.mesh_policy.quality_profile)
                if draft.values.mesh_policy is not None
                else None,
                ("outputs.profile", draft.values.outputs.profile)
                if draft.values.outputs is not None
                else None,
                ("quality_policy.profile", draft.values.quality_policy.profile)
                if draft.values.quality_policy is not None
                else None,
            )
            for request in profile_requests:
                if request is None:
                    continue
                field, profile_ref = request
                try:
                    if field == "mesh_policy.quality_profile":
                        registered_quality = storage.resolve_mesh_quality(profile_ref)
                        if (
                            isinstance(registered_quality, PlanarDemoRegistration)
                            and not draft.unresolved_fields
                        ):
                            try:
                                registered_quality.check_spec(draft.values.to_case_spec())
                            except ValueError as error:
                                raise PortError(PortErrorCategory.INTEGRITY, str(error)) from error
                        continue
                    profile = self.compatibility.get_profile(profile_ref.profile_id)
                    if not isinstance(profile, CompatibilityProfile):
                        diagnostics.append(
                            _diagnostic(
                                ServiceErrorCategory.INTEGRITY,
                                "compatibility registry returned an invalid profile record",
                                field,
                            )
                        )
                        continue
                    if profile.profile_id != profile_ref.profile_id:
                        diagnostics.append(
                            _diagnostic(
                                ServiceErrorCategory.INTEGRITY,
                                "profile identity mismatch",
                                field,
                            )
                        )
                    if hashlib.sha256(profile.to_bytes()).hexdigest() != profile_ref.record_digest:
                        diagnostics.append(
                            _diagnostic(
                                ServiceErrorCategory.INTEGRITY,
                                "profile record digest does not match the registered profile",
                                field,
                            )
                        )
                    for capability in profile.capabilities:
                        if capability.status is not CapabilityStatus.SUPPORTED:
                            diagnostics.append(
                                _diagnostic(
                                    ServiceErrorCategory.UNSUPPORTED_CAPABILITY,
                                    f"profile capability {capability.capability_id!r} is {capability.status.value}",
                                    field,
                                )
                            )
                except PortError as error:
                    diagnostics.append(
                        _diagnostic(_service_category(error.category), str(error), field)
                    )

        if draft.unresolved_fields:
            diagnostics.append(
                _diagnostic(
                    ServiceErrorCategory.NEEDS_PHYSICAL_INPUT,
                    "unresolved required fields: " + ", ".join(draft.unresolved_fields),
                    "values",
                )
            )
        if diagnostics:
            status = (
                "UNSUPPORTED_ENVIRONMENT"
                if any(
                    item.code
                    in {
                        ServiceErrorCategory.UNSUPPORTED_CAPABILITY,
                        ServiceErrorCategory.ENVIRONMENT,
                    }
                    for item in diagnostics
                )
                else "NEEDS_INPUT"
            )
            return ServiceResult(
                status,
                case_id,
                draft=draft,
                diagnostics=tuple(diagnostics),
                next_actions=("register the missing source/geometry/profile capability",),
            )
        try:
            draft.values.to_case_spec()
        except ValueError as error:
            return ServiceResult(
                "NEEDS_INPUT",
                case_id,
                draft=draft,
                diagnostics=(
                    _diagnostic(ServiceErrorCategory.NEEDS_PHYSICAL_INPUT, str(error), "values"),
                ),
            )
        return ServiceResult(
            "VALIDATED",
            case_id,
            draft=replace(draft, values=_resolved_values(draft.values, resolutions)),
        )

    def validate_case(self, case_id: str) -> ServiceResult:
        return self._validate(case_id)

    def freeze_case(
        self, case_id: str, *, caller_payload: Mapping[str, object] | None = None
    ) -> ServiceResult:
        try:
            with self._storage(case_id).evidence_snapshot():
                return self._freeze_pinned(case_id, caller_payload=caller_payload)
        except (StorageIntegrityError, OSError) as error:
            return ServiceResult(
                "NEEDS_INPUT",
                case_id,
                diagnostics=(_diagnostic(ServiceErrorCategory.INTEGRITY, str(error), "evidence"),),
            )

    def _freeze_pinned(
        self, case_id: str, *, caller_payload: Mapping[str, object] | None = None
    ) -> ServiceResult:
        if caller_payload is not None and any(
            key in caller_payload for key in ("approved", "ready", "success")
        ):
            raise ServiceConflictError("caller approval or readiness fields are not authority")
        result = self._validate(case_id)
        if result.status != "VALIDATED" or result.draft is None:
            return result
        draft = result.draft
        revision = CaseRevision(
            case_id=case_id,
            revision_id=f"revision-{uuid.uuid4().hex[:12]}",
            parent_revision_id=draft.parent_revision_id,
            parent_spec_digest=draft.parent_spec_digest,
            spec=draft.values.to_case_spec(),
            evidence=draft.evidence,
        )
        try:
            stored = self._storage(case_id).register_revision_if_current(
                revision, expected_generation=draft.generation, mesh_quality_required=True
            )
        except StorageConflictError as error:
            raise ConcurrentUpdateError(str(error)) from error
        return ServiceResult(
            "FROZEN",
            case_id,
            revision_id=stored.revision_id,
            draft=draft,
            revision=stored,
        )

    def register_profile(self, profile: CompatibilityProfile) -> CompatibilityProfile:
        register = getattr(self.compatibility, "register", None)
        if not callable(register):
            raise ServiceConflictError("configured compatibility registry is read-only")
        return register(profile)

    def register_mesh_quality(self, case_id: str, record: MeshQualityRecord) -> NumericalProfileRef:
        return self._storage(case_id).register_mesh_quality(record)

    def resolve_mesh_quality(self, case_id: str, ref: NumericalProfileRef) -> MeshQualityRecord:
        return self._storage(case_id).resolve_mesh_quality(ref)

    @staticmethod
    def _adopt_planar_mesh(
        registration: PlanarDemoRegistration,
        original: MeshArtifact,
        carrier: CaseRevision,
        revision: CaseRevision,
    ) -> tuple[MeshArtifact, dict[str, object]]:
        from ._adoption import adopt

        def partial(spec: Any) -> PartialCaseSpec:
            return PartialCaseSpec(
                **{f.name: getattr(spec, f.name) for f in fields(PartialCaseSpec) if f.init}
            )

        return adopt(
            registration,
            original,
            carrier,
            revision,
            _registered_selections(partial(carrier.spec)),
            _registered_selections(partial(revision.spec)),
        )

    def observe_preview(
        self,
        case_id: str,
        manifest_id: str,
        *,
        studio_executable: str,
        window_id: int,
        timeout_seconds: float,
        capture: Callable[[dict[str, Any], float], dict[str, Any]],
    ) -> dict[str, object]:
        from febio_cae.storage.preview import RegisteredPreviewStore

        from ._preview import observe_preview
        from ._preview_windows import WindowsStudioProbe

        probe = WindowsStudioProbe(studio_executable)
        session = probe.identify(window_id)
        return observe_preview(
            RegisteredPreviewStore(self._storage(case_id)),
            manifest_id,
            session,
            session_probe=probe,
            capture=capture,
            timeout_seconds=timeout_seconds,
        )

    def preview_status(self, case_id: str, preview_id: str) -> dict[str, object]:
        from febio_cae.storage.preview import RegisteredPreviewStore

        from ._preview import preview_summary

        return preview_summary(RegisteredPreviewStore(self._storage(case_id)), preview_id)

    def run_demo(
        self, case_id: str, revision_id: str, *, executable: str, preflight: bool = False
    ) -> dict[str, object]:
        from ._demo import run_demo

        return run_demo(self, case_id, revision_id, executable=executable, preflight=preflight)

    def _verify_execution_mesh(
        self,
        storage: CaseStorage,
        registration: MeshQualityRecord,
        revision: CaseRevision,
        mesh: MeshArtifact,
    ) -> None:
        if not isinstance(registration, PlanarDemoRegistration):
            if not mesh.quality_records or any(r.status != "PASS" for r in mesh.quality_records):
                raise ValueError("execution requires passing mesh quality")
            return
        from febio_cae.domain.canonical import canonical_bytes
        from febio_cae.domain.codec import decode_record, encode_record

        def source(asset_id: str) -> bytes:
            return storage.resolve_source(storage.source_asset(asset_id)).content

        original = decode_record(source("gm03-mesh"), MeshArtifact)
        carrier = decode_record(source("gm03-carrier"), CaseRevision)
        expected, receipt = self._adopt_planar_mesh(registration, original, carrier, revision)
        if (
            encode_record(mesh) != encode_record(expected)
            or source("adopted-mesh") != encode_record(expected)
            or source("adoption-receipt") != canonical_bytes(receipt)
        ):
            raise ValueError("execution adoption differs from registered origin derivation")

    def _execute_ports(
        self,
        case_id: str,
        revision_id: str,
        *,
        build: Callable[
            [CaseRevision, Path], tuple[MeshArtifact, ExecutionBundle, Mapping[str, bytes]]
        ],
        runner_factory: Callable[[OwnershipPort, Path, Mapping[str, bytes]], RunnerPort],
        read: Callable[
            [AttemptRecord, ExecutionBundle, tuple[FileEntry, ...], CaseStorage], ResultManifest
        ],
    ) -> ResultManifest:
        """Trusted adapter composition, separate from the synchronous producer seam.

        The build dependency invokes the configured mesher/compiler, not a caller
        artifact proposal. Its complete returned recipe/provenance is stored with
        the compiled lineage. Only actual issued runner snapshots are persisted.
        """
        storage = self._storage(case_id)
        if any(item.owner.case_id == case_id for item in _pending_cleanup.values()):
            raise PortError(
                PortErrorCategory.CONFLICT, "an exact runner cleanup obligation is pending"
            )
        with (
            storage.evidence_snapshot(),
            storage.revision_snapshot(case_id, revision_id) as revision,
        ):
            registration = storage.resolve_revision_mesh_quality(revision)
            validated = self._validate(case_id)
            draft = validated.draft
            if (
                validated.status != "VALIDATED"
                or draft is None
                or storage.revision_generation(revision_id) != draft.generation
                or draft.values.to_case_spec().to_bytes() != revision.spec.to_bytes()
                or tuple(draft.evidence) != tuple(revision.evidence)
            ):
                raise PortError(
                    PortErrorCategory.CONFLICT,
                    "execution requires current validated frozen revision",
                )
            deadline = time.monotonic() + revision.spec.budget.max_elapsed.to_si().value
            owner = TrustedOwnerContext(
                case_id,
                f"run-{uuid.uuid4().hex[:12]}",
                f"attempt-{uuid.uuid4().hex[:12]}",
                draft.generation,
            )
            destination = (
                storage.root / f"cases/{case_id}/runs/{owner.run_id}/attempts/{owner.attempt_id}"
            )
            mesh, bundle, inputs = build(revision, destination)
            try:
                self._verify_execution_mesh(storage, registration, revision, mesh)
            except ValueError as error:
                raise PortError(PortErrorCategory.INTEGRITY, str(error)) from error
            profile = self.compatibility.get_profile(revision.spec.solver_policy.profile.profile_id)
            if (
                mesh.provenance.source_geometry_digest != revision.spec.geometry.geometry_digest
                or set(mesh.provenance.source_body_ids)
                != {
                    revision.spec.geometry.body_id.value,
                    revision.spec.rigid_tool.primitive.body_id.value,
                }
                or bundle.thread_count > revision.spec.budget.cpu_workers
            ):
                raise PortError(
                    PortErrorCategory.INTEGRITY, "mesh or resource lineage differs from revision"
                )
            storage.claim(owner)
            storage._register_execution(owner, bundle, mesh, profile, inputs)
            runner_root = storage.root / "native"
            process_root = (
                runner_root
                / case_id
                / owner.run_id
                / owner.attempt_id
                / str(owner.owner_generation)
            )
            storage._prepare_runner(owner, process_root)
            runner = runner_factory(_RunnerOwner(storage, owner), runner_root, inputs)
            issued: AttemptRecord | None = None
            try:
                if time.monotonic() >= deadline:
                    raise PortError(
                        PortErrorCategory.EXECUTION, "preparation exhausted operation budget"
                    )
                if isinstance(registration, PlanarDemoRegistration):
                    from febio_cae.storage.demo_budget import reserve_demo_attempt

                    if not reserve_demo_attempt(storage, case_id, "febio", owner.attempt_id):
                        raise PortError(PortErrorCategory.CONFLICT, "native start already reserved")
                issued = runner.start(bundle, owner, revision.spec.budget)
                storage._accept_runner_start(owner, issued)
                while issued.state in {RunState.RUNNING, RunState.DRAINING}:
                    previous = issued
                    observed = runner.poll(previous, owner).attempt
                    storage._accept_runner_poll(owner, previous, observed)
                    issued = observed
                    if time.monotonic() > deadline:
                        raise PortError(
                            PortErrorCategory.EXECUTION, "owned runner exceeded operation budget"
                        )
                    if issued.state in {RunState.RUNNING, RunState.DRAINING}:
                        time.sleep(0.02)
                if issued.state is not RunState.VALIDATING:
                    raise PortError(
                        PortErrorCategory.EXECUTION, f"runner ended {issued.state.value}"
                    )
                storage._seal_native_output(owner)
                manifest = storage.publish_manifest(owner, storage._read_candidate(owner, read))
                storage._transition_attempt(owner, RunState.SUCCEEDED)
                return manifest
            except BaseException:
                if issued is not None and issued.state in {RunState.RUNNING, RunState.DRAINING}:
                    # Keep the issued object for cleanup; never cancel a forged poll result.
                    obligation = _CleanupObligation(runner, storage, owner, issued)
                    _pending_cleanup[owner.attempt_id] = obligation
                    if obligation.retry():
                        del _pending_cleanup[owner.attempt_id]
                elif issued is not None and issued.state is RunState.VALIDATING:
                    storage._transition_attempt(owner, RunState.FAILED)
                elif issued is None:
                    storage._transition_attempt(owner, RunState.PREPARING)
                    storage._transition_attempt(owner, RunState.FAILED)
                raise

    def _retry_pending_cleanup(self) -> int:
        """One bounded cancellation per retained runner; unfinished handles stay owned.

        This is an in-process continuation, not a claim that native ownership can
        be reconstructed from a persisted PID after a controller process exits.
        """
        for key, obligation in tuple(_pending_cleanup.items()):
            try:
                root = self.catalog.resolve(obligation.owner.case_id)
            except CaseCatalogError:
                continue
            if root != obligation.storage.root:
                continue
            if obligation.retry():
                del _pending_cleanup[key]
        return len(_pending_cleanup)

    def _execute_registered(
        self,
        case_id: str,
        revision_id: str,
        *,
        build: Callable[
            [CaseRevision, Path], tuple[MeshArtifact, ExecutionBundle, Mapping[str, bytes]]
        ],
        produce: Callable[[ExecutionBundle, dict[str, bytes]], Mapping[str, bytes]],
        read: Callable[
            [AttemptRecord, ExecutionBundle, tuple[FileEntry, ...], CaseStorage], ResultManifest
        ],
    ) -> ResultManifest:
        """Internal synchronous byte-producer integration, with service-owned writers.

        These injected callables are trusted application dependencies, never CLI
        payloads. This route does not start or authorize an external process.
        """
        storage = self._storage(case_id)
        with (
            storage.evidence_snapshot(),
            storage.revision_snapshot(case_id, revision_id) as revision,
        ):
            storage.resolve_revision_mesh_quality(revision)
            validated = self._validate(case_id)
            draft = validated.draft
            if (
                validated.status != "VALIDATED"
                or draft is None
                or storage.revision_generation(revision_id) != draft.generation
                or draft.values.to_case_spec().to_bytes() != revision.spec.to_bytes()
                or tuple(draft.evidence) != tuple(revision.evidence)
            ):
                raise PortError(
                    PortErrorCategory.CONFLICT,
                    "execution requires the current validated frozen revision",
                )
            owner = TrustedOwnerContext(
                case_id,
                f"run-{uuid.uuid4().hex[:12]}",
                f"attempt-{uuid.uuid4().hex[:12]}",
                draft.generation,
            )
            destination = (
                storage.root / f"cases/{case_id}/runs/{owner.run_id}/attempts/{owner.attempt_id}"
            )
            started = time.monotonic()
            mesh, bundle, inputs = build(revision, destination)
            profile = self.compatibility.get_profile(revision.spec.solver_policy.profile.profile_id)
            if (
                mesh.provenance.source_geometry_digest != revision.spec.geometry.source_step_digest
                or revision.spec.geometry.body_id.value not in mesh.provenance.source_body_ids
                or mesh.provenance.mesh_recipe_digest
                != hashlib.sha256(revision.spec.mesh_policy.to_bytes()).hexdigest()
                or not mesh.quality_records
                or any(item.status != "PASS" for item in mesh.quality_records)
                or bundle.thread_count > revision.spec.budget.cpu_workers
            ):
                raise PortError(
                    PortErrorCategory.INTEGRITY, "mesh or resource lineage differs from revision"
                )
            storage.claim(owner)
            storage._register_execution(owner, bundle, mesh, profile, inputs)
            storage._transition_attempt(owner, RunState.PREPARING)
            storage._transition_attempt(owner, RunState.RUNNING)
            try:
                outputs = dict(produce(bundle, dict(inputs)))
            except Exception:
                storage._transition_attempt(owner, RunState.INTERRUPTED)
                raise
            storage._transition_attempt(owner, RunState.DRAINING)
            try:
                if time.monotonic() - started > revision.spec.budget.max_elapsed.to_si().value:
                    raise PortError(
                        PortErrorCategory.EXECUTION, "execution exhausted elapsed budget"
                    )
                storage._seal_outputs(owner, outputs)
            except Exception:
                storage._transition_attempt(owner, RunState.FAILED)
                raise
            storage._transition_attempt(owner, RunState.VALIDATING)
            try:
                manifest = storage.publish_manifest(owner, storage._read_candidate(owner, read))
            except Exception:
                storage._transition_attempt(owner, RunState.FAILED)
                raise
            storage._transition_attempt(owner, RunState.SUCCEEDED)
            return manifest


__all__ = [
    "ConcurrentUpdateError",
    "CreatedCase",
    "RegisteredCaseService",
    "ServiceConflictError",
    "ServiceResult",
]
