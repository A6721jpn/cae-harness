"""Connected geometry and meshing adapter for the frozen V2 ports."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, NoReturn, cast

if TYPE_CHECKING:
    from febio_cae.adapters.meshing.primitives import GeneratedPrimitiveMesh
from febio_cae.domain import (
    AsPlaced,
    CaseRevision,
    FaceId,
    FaceMeasurement,
    FaceSetRule,
    GeometryBodyFact,
    GeometryInspection,
    GeometryInspectionRequest,
    GeometryPort,
    GeometrySelectionRequest,
    MeshArtifact,
    MeshElement,
    MeshFace,
    MeshingPort,
    MeshNode,
    MeshProvenance,
    MeshQualityRecord,
    MeshSet,
    NamedAttributeRule,
    PortError,
    PortErrorCategory,
    Quantity,
    ResolutionSnapshot,
    RigidTransform,
    SelectionRef,
    SourceAssetContent,
    SourceAssetRef,
    SourceAssetResolverPort,
    SpecifiedGap,
    UnitDirection,
    WholeBodyRule,
)
from febio_cae.domain.artifacts import TET10_FACE_NODE_POSITIONS
from febio_cae.domain.canonical import canonical_bytes
from febio_cae.domain.case_spec import CaseSpec
from febio_cae.domain.selection import CoordinatePredicateRule
from febio_cae.domain.spatial import Point3, ProperRotation, Translation3
from febio_cae.domain.units import Dimension, unit_definition

from .backend import (
    BACKEND_TET10_ORDER_ID,
    BACKEND_TET10_TO_CANONICAL_POSITIONS,
    BackendBody,
    BackendError,
    BackendErrorCategory,
    BackendFace,
    BackendInspection,
    BackendMesh,
    GeometryMeshBackend,
)

_LENGTH = Dimension(length=1)
_GEOMETRY_TOLERANCE = 1.0e-12
_MAPPING_ID = "backend-tet10-to-domain-tet10-v1"


@dataclass(frozen=True, slots=True)
class InitialContactPlacement:
    """Recorded result of an explicit initial-arrangement calculation."""

    calculated: bool
    reason: str
    placement: RigidTransform
    direction: UnitDirection | None = None
    current_gap_si: float | None = None
    requested_gap_si: float | None = None
    translation_delta_si: tuple[float, float, float] = (0.0, 0.0, 0.0)
    part_face_id: str | None = None
    tool_face_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.calculated, bool):
            raise TypeError("calculated must be a bool")
        if (
            not isinstance(self.reason, str)
            or not self.reason
            or self.reason != self.reason.strip()
        ):
            raise ValueError("reason must be non-empty text")
        if not isinstance(self.placement, RigidTransform):
            raise TypeError("placement must be a RigidTransform")
        if self.direction is not None and not isinstance(self.direction, UnitDirection):
            raise TypeError("direction must be a UnitDirection or None")
        if self.current_gap_si is not None and not math.isfinite(self.current_gap_si):
            raise ValueError("current_gap_si must be finite")
        if self.requested_gap_si is not None and not math.isfinite(self.requested_gap_si):
            raise ValueError("requested_gap_si must be finite")
        if len(self.translation_delta_si) != 3 or any(
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
            for value in self.translation_delta_si
        ):
            raise ValueError("translation_delta_si must have three finite components")
        if self.calculated:
            if self.direction is None:
                raise ValueError("calculated placement requires a direction")
            if self.current_gap_si is None or self.requested_gap_si is None:
                raise ValueError("calculated placement requires current and requested gaps")
            if self.part_face_id is None or self.tool_face_id is None:
                raise ValueError("calculated placement requires selected face IDs")

    def to_dict(self) -> dict[str, object]:
        return {
            "calculated": self.calculated,
            "reason": self.reason,
            "placement": self.placement.to_dict(),
            "direction": None if self.direction is None else self.direction.to_dict(),
            "current_gap_si": self.current_gap_si,
            "requested_gap_si": self.requested_gap_si,
            "translation_delta_si": list(self.translation_delta_si),
            "part_face_id": self.part_face_id,
            "tool_face_id": self.tool_face_id,
        }


@dataclass(frozen=True, slots=True)
class _MappedMesh:
    nodes: tuple[MeshNode, ...]
    elements: tuple[MeshElement, ...]
    faces: tuple[MeshFace, ...]
    signed_volumes: tuple[float, ...]


class StepGeometryMeshAdapter(GeometryPort, MeshingPort):
    """Concrete source-connected adapter with an explicit backend boundary.

    The backend owns CAD/native interpretation.  This class owns source
    identity checks, selection semantics, SI transformation, primitive
    generation, canonical Tet10 mapping, and structural artifact validation.
    """

    def __init__(
        self,
        backend: GeometryMeshBackend,
        *,
        source_resolver: SourceAssetResolverPort | None = None,
        source_asset: SourceAssetRef | None = None,
    ) -> None:
        if not isinstance(backend, GeometryMeshBackend):
            raise TypeError("backend must implement GeometryMeshBackend")
        if not isinstance(getattr(backend, "backend_id", None), str) or not backend.backend_id:
            raise TypeError("backend.backend_id must be non-empty text")
        if (
            not isinstance(getattr(backend, "backend_version", None), str)
            or not backend.backend_version
        ):
            raise TypeError("backend.backend_version must be non-empty text")
        if source_resolver is not None and not isinstance(source_resolver, SourceAssetResolverPort):
            raise TypeError("source_resolver must implement SourceAssetResolverPort")
        if source_asset is not None and not isinstance(source_asset, SourceAssetRef):
            raise TypeError("source_asset must be a SourceAssetRef")
        self._backend = backend
        self._source_resolver = source_resolver
        self._source_asset = source_asset
        self._inspection_details: dict[str, BackendInspection] = {}

    @property
    def backend_id(self) -> str:
        return self._backend.backend_id

    @property
    def backend_version(self) -> str:
        return self._backend.backend_version

    def inspect(
        self, request: GeometryInspectionRequest, source: SourceAssetContent
    ) -> GeometryInspection:
        self._validate_source_request(request.source_asset, source)
        report = self._inspect_backend(source, request.requested_body_ids)
        inspection = self._inspection_record(
            request.source_asset, report, request.requested_body_ids
        )
        self._inspection_details[inspection.inspection_digest] = report
        return inspection

    def inspection_details(self, inspection: GeometryInspection) -> BackendInspection:
        """Return backend boundary-face geometry and defect facts for one inspection."""

        if not isinstance(inspection, GeometryInspection):
            raise TypeError("inspection must be a GeometryInspection")
        try:
            return self._inspection_details[inspection.inspection_digest]
        except KeyError as error:
            raise PortError(
                PortErrorCategory.INTEGRITY,
                "inspection details are unavailable for the supplied inspection digest",
            ) from error

    def resolve_selection(
        self, request: GeometrySelectionRequest, source: SourceAssetContent
    ) -> ResolutionSnapshot:
        self._validate_source_request(request.source_asset, source)
        report = self._inspect_backend(source, ())
        inspection = self._inspection_record(request.source_asset, report, ())
        self._inspection_details[inspection.inspection_digest] = report
        return self._resolve_selection_from_report(request.selection, report)

    def inspect_rigid_tool(self, primitive: object, geometry_digest: str) -> BackendInspection:
        """Inspect generated rigid-tool boundary faces in the placement target frame."""

        from febio_cae.adapters.meshing.primitives import generate_primitive_mesh
        from febio_cae.domain.rigid import RigidPrimitive

        if not isinstance(primitive, RigidPrimitive):
            raise TypeError("primitive must be a RigidPrimitive")
        generated = generate_primitive_mesh(primitive, geometry_digest=geometry_digest)
        return self._tool_inspection(generated, primitive, geometry_digest)

    def calculate_initial_contact_placement(
        self, revision: CaseRevision
    ) -> InitialContactPlacement:
        """Apply only a fully explicit, unique ``SpecifiedGap`` arrangement."""

        from febio_cae.adapters.meshing.primitives import generate_primitive_mesh

        source = self._resolve_registered_source(revision)
        spec = revision.spec
        inspection = self.inspect(
            GeometryInspectionRequest(self._configured_source_asset()), source
        )
        if spec.geometry.inspection_digest != inspection.inspection_digest:
            self._raise(
                PortErrorCategory.INTEGRITY,
                "case geometry inspection digest does not match the registered source",
            )
        if spec.geometry.step_unit != inspection.declared_unit:
            self._raise(
                PortErrorCategory.INTEGRITY, "case STEP unit conflicts with source inspection"
            )
        report = _placed_inspection(self.inspection_details(inspection), spec.geometry.placement)
        generated = generate_primitive_mesh(
            spec.rigid_tool.primitive,
            geometry_digest=spec.rigid_tool.contact_surface.geometry_digest,
        )
        return self._initial_placement(spec, report, generated)

    def _initial_placement(
        self, spec: CaseSpec, report: BackendInspection, generated: GeneratedPrimitiveMesh
    ) -> InitialContactPlacement:
        tool_report = self._tool_inspection(
            generated,
            spec.rigid_tool.primitive,
            spec.rigid_tool.contact_surface.geometry_digest,
        )
        part_resolution = self._resolve_selection_from_report(spec.contact.part_surface, report)
        tool_resolution = self._resolve_selection_from_report(
            spec.contact.tool_surface, tool_report
        )
        arrangement = spec.contact.arrangement
        if isinstance(arrangement, AsPlaced):
            return InitialContactPlacement(
                calculated=False,
                reason="as_placed",
                placement=spec.rigid_tool.primitive.placement,
            )
        if not isinstance(arrangement, SpecifiedGap):
            self._raise(PortErrorCategory.INVALID_INPUT, "unsupported contact arrangement")
        if len(part_resolution.faces) != 1 or len(tool_resolution.faces) != 1:
            self._raise(
                PortErrorCategory.INVALID_INPUT,
                "initial contact placement requires unique one-face part and tool selections",
            )
        part_face = part_resolution.faces[0]
        tool_face = tool_resolution.faces[0]
        direction = arrangement.direction
        requested_gap = float(arrangement.gap.to_si().value)
        from .planar_gap import directed_planar_gap

        current_gap = directed_planar_gap(
            report,
            tool_report,
            spec.geometry.body_id.value,
            spec.rigid_tool.primitive.body_id.value,
            part_face.face_id.value,
            tool_face.face_id.value,
            direction,
            requested_gap,
        )
        delta = requested_gap - current_gap
        translation_delta = (delta * direction.x, delta * direction.y, delta * direction.z)
        old = spec.rigid_tool.primitive.placement.translation
        old_values = _quantity_values(old)
        new_translation = Translation3(
            old.frame,
            Quantity(old_values[0] + translation_delta[0], "m"),
            Quantity(old_values[1] + translation_delta[1], "m"),
            Quantity(old_values[2] + translation_delta[2], "m"),
        )
        placement = RigidTransform(
            source_frame=spec.rigid_tool.primitive.placement.source_frame,
            target_frame=spec.rigid_tool.primitive.placement.target_frame,
            translation=new_translation,
            rotation=spec.rigid_tool.primitive.placement.rotation,
        )
        return InitialContactPlacement(
            calculated=True,
            reason="specified_gap_applied",
            placement=placement,
            direction=direction,
            current_gap_si=current_gap,
            requested_gap_si=requested_gap,
            translation_delta_si=translation_delta,
            part_face_id=part_face.face_id.value,
            tool_face_id=tool_face.face_id.value,
        )

    def mesh(self, revision: CaseRevision) -> MeshArtifact:
        from febio_cae.adapters.meshing.primitives import generate_primitive_mesh

        if not isinstance(revision, CaseRevision):
            self._raise(PortErrorCategory.INVALID_INPUT, "revision must be a CaseRevision")
        source = self._resolve_registered_source(revision)
        spec = revision.spec
        inspection = self.inspect(
            GeometryInspectionRequest(self._configured_source_asset()), source
        )
        if spec.geometry.inspection_digest != inspection.inspection_digest:
            self._raise(
                PortErrorCategory.INTEGRITY,
                "case geometry inspection digest does not match the registered source",
            )
        if spec.geometry.step_unit != inspection.declared_unit:
            self._raise(
                PortErrorCategory.INTEGRITY, "case STEP unit conflicts with source inspection"
            )
        report = _placed_inspection(self.inspection_details(inspection), spec.geometry.placement)
        part_body_id = spec.geometry.body_id.value
        if part_body_id not in inspection.body_ids:
            self._raise(
                PortErrorCategory.INVALID_INPUT, "declared geometry body is not in inspection"
            )
        if part_body_id not in inspection.closed_solid_body_ids:
            self._raise(
                PortErrorCategory.UNSUPPORTED_CAPABILITY,
                "declared geometry body is not a closed solid",
            )
        if spec.mesh_policy.local_refinements:
            self._raise(
                PortErrorCategory.UNSUPPORTED_CAPABILITY,
                "backend local-refinement mapping is not qualified for this adapter",
            )
        try:
            part_mesh = self._backend.mesh(
                source.content,
                part_body_id,
                float(spec.mesh_policy.global_size.to_si().value),
            )
        except BackendError as error:
            self._raise_backend(error)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            self._raise(PortErrorCategory.ENVIRONMENT, f"backend meshing failed: {error}")
        if not isinstance(part_mesh, BackendMesh):
            self._raise(PortErrorCategory.ENVIRONMENT, "backend returned an invalid mesh record")
        expected_source_digest = hashlib.sha256(source.content).hexdigest()
        if part_mesh.source_digest != expected_source_digest:
            self._raise(
                PortErrorCategory.INTEGRITY,
                "backend mesh source digest does not match source bytes",
            )
        if part_mesh.geometry_digest != spec.geometry.geometry_digest:
            self._raise(PortErrorCategory.INTEGRITY, "backend mesh geometry digest is stale")
        generated = generate_primitive_mesh(
            spec.rigid_tool.primitive,
            geometry_digest=spec.rigid_tool.contact_surface.geometry_digest,
        )
        applied = self._initial_placement(spec, report, generated)
        placed_tool = replace(spec.rigid_tool.primitive, placement=applied.placement)
        tool_mesh = generated.mesh
        part_mapped = self._map_backend_mesh(
            part_mesh,
            transform=spec.geometry.placement,
            expected_body_id=part_body_id,
            node_start=1,
            element_start=1,
            expected_geometry_digest=spec.geometry.geometry_digest,
        )
        tool_node_start = len(part_mapped.nodes) + 1
        tool_element_start = len(part_mapped.elements) + 1
        tool_mapped = self._map_backend_mesh(
            tool_mesh,
            transform=applied.placement,
            expected_body_id=spec.rigid_tool.primitive.body_id.value,
            node_start=tool_node_start,
            element_start=tool_element_start,
            expected_geometry_digest=spec.rigid_tool.contact_surface.geometry_digest,
        )
        all_faces = part_mapped.faces + tool_mapped.faces
        if len({face.face_id for face in all_faces}) != len(all_faces):
            self._raise(PortErrorCategory.INTEGRITY, "part and rigid-tool mesh face IDs overlap")
        all_nodes = part_mapped.nodes + tool_mapped.nodes
        all_elements = part_mapped.elements + tool_mapped.elements
        tool_report = self._tool_inspection(
            generated,
            placed_tool,
            spec.rigid_tool.contact_surface.geometry_digest,
        )
        selections = _spec_selections(spec)
        selection_resolutions: dict[str, ResolutionSnapshot] = {}
        for selection in selections:
            selection_digest = _selection_digest(selection)
            if selection_digest in selection_resolutions:
                continue
            selected_report = report if selection.body_id.value == part_body_id else tool_report
            if selection.body_id.value not in {
                part_body_id,
                spec.rigid_tool.primitive.body_id.value,
            }:
                self._raise(
                    PortErrorCategory.INVALID_INPUT,
                    f"selection {selection.name!r} identifies an unknown mesh body",
                )
            selection_resolutions[selection_digest] = self._resolve_selection_from_report(
                selection, selected_report
            )
        coverage: dict[tuple[str, str], list[str]] = {}
        for backend_mesh in (part_mesh, tool_mesh):
            for face in backend_mesh.faces:
                if len(face.adjacent_element_ids) == 1 and face.source_face_id is not None:
                    coverage.setdefault((backend_mesh.body_id, face.source_face_id), []).append(
                        face.face_id
                    )
        sets: list[MeshSet] = []
        for selection in selections:
            digest = _selection_digest(selection)
            if any(item.set_id == f"selection:{digest[:24]}" for item in sets):
                continue
            resolution = selection_resolutions[digest]
            members_list: list[str] = []
            for resolved_face in resolution.faces:
                face_members = coverage.get(
                    (selection.body_id.value, resolved_face.face_id.value), []
                )
                if not face_members:
                    self._raise(
                        PortErrorCategory.INTEGRITY,
                        f"selection {selection.name!r} face {resolved_face.face_id.value!r} is not covered by the backend mesh",
                    )
                members_list.extend(sorted(face_members))
            members = tuple(dict.fromkeys(members_list))
            sets.append(
                MeshSet(
                    set_id=f"selection:{digest[:24]}",
                    kind="face",
                    body_id=selection.body_id.value,
                    member_ids=members,
                    source_selection_digest=digest,
                )
            )
        signed_volumes = part_mapped.signed_volumes + tool_mapped.signed_volumes
        minimum_volume = min(signed_volumes)
        selection_digests = tuple(sorted(selection_resolutions))
        recipe_digest = _mesh_recipe_digest(
            source_asset=self._source_asset,
            spec=spec,
            selection_digests=selection_digests,
            backend_id=self.backend_id,
            backend_version=self.backend_version,
            applied=applied,
        )
        provenance = MeshProvenance(
            source_geometry_digest=spec.geometry.geometry_digest,
            source_body_ids=(part_body_id, spec.rigid_tool.primitive.body_id.value),
            source_selection_digests=selection_digests,
            mesh_recipe_digest=recipe_digest,
            tool_id=self.backend_id,
            tool_version=self.backend_version,
            mapping_id=_MAPPING_ID,
            node_ordering_id="tet10-canonical-v1",
            face_ordering_id="tet10-face-canonical-v1",
        )
        quality_records = (
            MeshQualityRecord(
                "initial-contact-placement",
                math.sqrt(sum(v * v for v in applied.translation_delta_si)),
                "m",
                None,
                "PASS",
                canonical_bytes(applied.to_dict()).decode("utf-8"),
            ),
            MeshQualityRecord(
                "tet10-positive-corner-volume",
                minimum_volume,
                "m3",
                0.0,
                "PASS",
                "all mapped Tet10 corner Jacobians have positive signed volume",
            ),
            MeshQualityRecord(
                "tet10-backend-mapping",
                1.0,
                "1",
                1.0,
                "PASS",
                _MAPPING_ID,
            ),
            MeshQualityRecord(
                "part-tool-node-independence",
                1.0,
                "1",
                1.0,
                "PASS",
                "part and rigid-tool node IDs are disjoint",
            ),
            MeshQualityRecord(
                "surface-approximation",
                0.0,
                "m",
                None,
                "UNVERIFIED",
                "bidirectional CAD-to-mesh approximation evidence is backend-dependent",
            ),
        )
        return MeshArtifact(
            artifact_id=f"mesh:{recipe_digest[:24]}",
            frame=spec.geometry.placement.target_frame,
            provenance=provenance,
            nodes=all_nodes,
            elements=all_elements,
            faces=all_faces,
            sets=tuple(sets),
            quality_records=quality_records,
        )

    def _resolve_registered_source(self, revision: CaseRevision) -> SourceAssetContent:
        if not isinstance(revision, CaseRevision):
            self._raise(PortErrorCategory.INVALID_INPUT, "revision must be a CaseRevision")
        if self._source_resolver is None or self._source_asset is None:
            self._raise(
                PortErrorCategory.ENVIRONMENT,
                "a registered source resolver and source identity are required for meshing",
            )
        if self._source_asset.content_digest != revision.spec.geometry.source_step_digest:
            self._raise(
                PortErrorCategory.INTEGRITY,
                "configured source identity does not match the case source digest",
            )
        try:
            source = self._source_resolver.resolve(self._source_asset)
        except PortError:
            raise
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            self._raise(
                PortErrorCategory.INTEGRITY, f"registered source resolution failed: {error}"
            )
        if not isinstance(source, SourceAssetContent):
            self._raise(
                PortErrorCategory.INTEGRITY, "source resolver returned an invalid source record"
            )
        self._validate_source_request(self._source_asset, source)
        return source

    def _configured_source_asset(self) -> SourceAssetRef:
        if self._source_asset is None:
            self._raise(
                PortErrorCategory.ENVIRONMENT,
                "a registered source identity is required for geometry inspection",
            )
        return self._source_asset

    @staticmethod
    def _validate_source_request(source_asset: SourceAssetRef, source: SourceAssetContent) -> None:
        if not isinstance(source_asset, SourceAssetRef):
            raise PortError(
                PortErrorCategory.INVALID_INPUT, "request source must be a SourceAssetRef"
            )
        if not isinstance(source, SourceAssetContent):
            raise PortError(
                PortErrorCategory.INTEGRITY, "resolved source must be SourceAssetContent"
            )
        if source.source_asset != source_asset:
            raise PortError(
                PortErrorCategory.INVALID_INPUT, "request and resolved source identities differ"
            )
        if hashlib.sha256(source.content).hexdigest() != source_asset.content_digest:
            raise PortError(
                PortErrorCategory.INTEGRITY, "resolved source bytes do not match registered digest"
            )

    def _inspect_backend(
        self, source: SourceAssetContent, requested_body_ids: Sequence[str]
    ) -> BackendInspection:
        try:
            report = self._backend.inspect(source.content, requested_body_ids)
        except BackendError as error:
            self._raise_backend(error)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            self._raise(PortErrorCategory.ENVIRONMENT, f"backend inspection failed: {error}")
        if not isinstance(report, BackendInspection):
            self._raise(
                PortErrorCategory.ENVIRONMENT, "backend returned an invalid inspection record"
            )
        source_digest = hashlib.sha256(source.content).hexdigest()
        if report.source_digest != source_digest:
            self._raise(
                PortErrorCategory.INTEGRITY,
                "backend inspection source digest does not match source bytes",
            )
        body_ids = {body.body_id for body in report.bodies}
        if any(body_id not in body_ids for body_id in requested_body_ids):
            self._raise(PortErrorCategory.INVALID_INPUT, "requested inspection body is not present")
        return report

    def _inspection_record(
        self,
        source_asset: SourceAssetRef,
        report: BackendInspection,
        requested_body_ids: Sequence[str],
    ) -> GeometryInspection:
        units = tuple(dict.fromkeys(report.declared_units))
        if len(units) != 1:
            self._raise(
                PortErrorCategory.INVALID_INPUT,
                "STEP inspection must provide exactly one declared length unit",
            )
        declared_unit = units[0]
        try:
            definition = unit_definition(declared_unit)
        except ValueError:
            self._raise(
                PortErrorCategory.UNSUPPORTED_CAPABILITY,
                f"STEP unit is unsupported: {declared_unit!r}",
            )
        if definition.dimension != _LENGTH:
            self._raise(
                PortErrorCategory.UNSUPPORTED_CAPABILITY,
                f"STEP unit is not a length unit: {declared_unit!r}",
            )
        if report.unsupported_topology:
            self._raise(
                PortErrorCategory.UNSUPPORTED_CAPABILITY,
                "unsupported topology: " + "; ".join(report.unsupported_topology),
            )
        body_ids = tuple(sorted(body.body_id for body in report.bodies))
        closed_body_ids = tuple(sorted(body.body_id for body in report.bodies if body.closed_solid))
        facts = tuple(
            GeometryBodyFact(body.body_id, len(body.faces), body.volume_si)
            for body in sorted(report.bodies, key=lambda item: item.body_id)
        )
        digest = hashlib.sha256(canonical_bytes(report.to_dict())).hexdigest()
        return GeometryInspection(
            source_asset=source_asset,
            inspection_digest=digest,
            declared_unit=declared_unit,
            body_ids=body_ids,
            closed_solid_body_ids=closed_body_ids,
            body_facts=facts,
        )

    def _resolve_selection_from_report(
        self, selection: SelectionRef, report: BackendInspection
    ) -> ResolutionSnapshot:
        if not isinstance(selection, SelectionRef):
            self._raise(PortErrorCategory.INVALID_INPUT, "selection must be a SelectionRef")
        if selection.geometry_digest != report.geometry_digest:
            self._raise(
                PortErrorCategory.INTEGRITY,
                f"selection {selection.name!r} references stale geometry digest",
            )
        body = next(
            (item for item in report.bodies if item.body_id == selection.body_id.value), None
        )
        if body is None:
            self._raise(
                PortErrorCategory.INVALID_INPUT,
                f"selection {selection.name!r} identifies a foreign body",
            )
        if not body.closed_solid:
            self._raise(
                PortErrorCategory.UNSUPPORTED_CAPABILITY,
                f"selection body {selection.body_id.value!r} is not a closed solid",
            )
        if selection.frame != report.frame:
            self._raise(
                PortErrorCategory.INVALID_INPUT,
                f"selection {selection.name!r} frame does not match backend geometry frame",
            )
        faces_by_id = {face.face_id: face for face in body.faces}
        rule = selection.rule
        selected: list[BackendFace]
        if isinstance(rule, WholeBodyRule):
            selected = list(body.faces)
        elif isinstance(rule, NamedAttributeRule):
            selected = [face for face in body.faces if rule.attribute in face.attributes]
        elif isinstance(rule, CoordinatePredicateRule):
            selected = [
                face
                for face in body.faces
                if all(_predicate_matches(face, predicate) for predicate in rule.predicates)
            ]
        elif isinstance(rule, FaceSetRule):
            unknown = [
                face_id.value for face_id in rule.face_ids if face_id.value not in faces_by_id
            ]
            if unknown:
                self._raise(
                    PortErrorCategory.INVALID_INPUT,
                    f"selection {selection.name!r} references unknown faces: {unknown[0]!r}",
                )
            selected = [faces_by_id[face_id.value] for face_id in rule.face_ids]
        else:
            self._raise(PortErrorCategory.UNSUPPORTED_CAPABILITY, "selection rule is unsupported")
        selected.sort(key=lambda face: face.face_id)
        if not selected:
            self._raise(
                PortErrorCategory.INVALID_INPUT,
                f"selection {selection.name!r} resolved to no faces",
            )
        measurements = tuple(
            FaceMeasurement(
                face_id=FaceId(face.face_id),
                area=Quantity(face.area_si, "m2"),
                centroid=Point3(
                    report.frame,
                    Quantity(face.centroid_si[0], "m"),
                    Quantity(face.centroid_si[1], "m"),
                    Quantity(face.centroid_si[2], "m"),
                ),
            )
            for face in selected
        )
        resolution = ResolutionSnapshot(
            geometry_digest=report.geometry_digest,
            body_id=selection.body_id,
            frame=selection.frame,
            faces=measurements,
        )
        self._verify_existing_resolution(selection, resolution)
        return resolution

    @staticmethod
    def _verify_existing_resolution(selection: SelectionRef, current: ResolutionSnapshot) -> None:
        previous = selection.resolution
        if previous is None:
            return
        if (
            previous.geometry_digest != current.geometry_digest
            or previous.body_id != current.body_id
            or previous.frame != current.frame
        ):
            raise PortError(PortErrorCategory.INTEGRITY, "selection resolution context is stale")
        previous_by_id = {face.face_id.value: face for face in previous.faces}
        current_by_id = {face.face_id.value: face for face in current.faces}
        if set(previous_by_id) != set(current_by_id):
            raise PortError(
                PortErrorCategory.INTEGRITY,
                "selection resolution coverage changed after re-meshing",
            )
        for face_id, old in previous_by_id.items():
            new = current_by_id[face_id]
            values = (
                (old.area.to_si().value, new.area.to_si().value),
                (old.centroid.x.to_si().value, new.centroid.x.to_si().value),
                (old.centroid.y.to_si().value, new.centroid.y.to_si().value),
                (old.centroid.z.to_si().value, new.centroid.z.to_si().value),
            )
            if any(
                not math.isclose(left, right, rel_tol=1.0e-9, abs_tol=_GEOMETRY_TOLERANCE)
                for left, right in values
            ):
                raise PortError(
                    PortErrorCategory.INTEGRITY,
                    f"selection face {face_id!r} geometry measurements changed",
                )

    def _tool_inspection(
        self,
        generated: GeneratedPrimitiveMesh,
        primitive: object,
        geometry_digest: str,
    ) -> BackendInspection:
        from febio_cae.domain.rigid import RigidPrimitive

        if not isinstance(primitive, RigidPrimitive):
            self._raise(PortErrorCategory.INVALID_INPUT, "primitive must be a RigidPrimitive")
        faces = tuple(
            _transform_backend_face(face, primitive.placement) for face in generated.boundary_faces
        )
        volume = _mesh_volume(generated.mesh)
        body = BackendBody(primitive.body_id.value, True, volume, faces)
        return BackendInspection(
            source_digest=generated.mesh.source_digest,
            geometry_digest=geometry_digest,
            declared_units=("m",),
            frame=primitive.placement.target_frame,
            bodies=(body,),
        )

    def _map_backend_mesh(
        self,
        mesh: BackendMesh,
        *,
        transform: RigidTransform,
        expected_body_id: str,
        node_start: int,
        element_start: int,
        expected_geometry_digest: str,
    ) -> _MappedMesh:
        if mesh.ordering_id != BACKEND_TET10_ORDER_ID:
            self._raise(
                PortErrorCategory.UNSUPPORTED_CAPABILITY,
                f"unsupported backend Tet10 ordering: {mesh.ordering_id!r}",
            )
        if mesh.body_id != expected_body_id:
            self._raise(
                PortErrorCategory.INTEGRITY, "backend mesh body does not match declared body"
            )
        if mesh.geometry_digest != expected_geometry_digest:
            self._raise(
                PortErrorCategory.INTEGRITY, "backend mesh geometry digest does not match recipe"
            )
        if mesh.frame != transform.source_frame:
            self._raise(
                PortErrorCategory.INTEGRITY,
                "backend mesh frame does not match placement source frame",
            )
        source_nodes = {node.node_id: node for node in mesh.nodes}
        ordered_source_nodes = sorted(source_nodes)
        node_map = {
            source_id: node_start + index for index, source_id in enumerate(ordered_source_nodes)
        }
        nodes = tuple(
            MeshNode(
                node_map[source_id],
                _transform_point(source_nodes[source_id].coordinates_si, transform),
            )
            for source_id in ordered_source_nodes
        )
        source_elements = {element.element_id: element for element in mesh.elements}
        ordered_source_elements = sorted(source_elements)
        element_map = {
            source_id: element_start + index
            for index, source_id in enumerate(ordered_source_elements)
        }
        elements: list[MeshElement] = []
        signed_volumes: list[float] = []
        for source_id in ordered_source_elements:
            source_element = source_elements[source_id]
            if source_element.ordering_id != BACKEND_TET10_ORDER_ID:
                self._raise(
                    PortErrorCategory.UNSUPPORTED_CAPABILITY,
                    f"unsupported element ordering: {source_element.ordering_id!r}",
                )
            try:
                canonical_node_ids = tuple(
                    node_map[source_element.node_ids[position]]
                    for position in BACKEND_TET10_TO_CANONICAL_POSITIONS
                )
            except KeyError:
                self._raise(
                    PortErrorCategory.INTEGRITY, "backend element references an unknown node"
                )
            corner_points = tuple(
                nodes[node_id - node_start].coordinates_si for node_id in canonical_node_ids[:4]
            )
            signed_volume = _signed_volume_points(corner_points)
            if signed_volume <= 0.0:
                self._raise(
                    PortErrorCategory.QUALITY,
                    f"element {source_id} has non-positive canonical Tet10 corner volume",
                )
            signed_volumes.append(signed_volume)
            from .quadratic_quality import require_positive_quadratic_mapping

            try:
                require_positive_quadratic_mapping(
                    tuple(
                        nodes[node_id - node_start].coordinates_si for node_id in canonical_node_ids
                    )
                )
            except ValueError as error:
                self._raise(PortErrorCategory.QUALITY, f"element {source_id}: {error}")
            elements.append(
                MeshElement(
                    element_id=element_map[source_id],
                    element_type="tet10",
                    node_ids=canonical_node_ids,
                    body_id=expected_body_id,
                )
            )
        faces: list[MeshFace] = []
        for source_face in mesh.faces:
            adjacency = tuple(element_map[item] for item in source_face.adjacent_element_ids)
            local_face_ids = tuple(source_face.local_face_ids)
            first_element = next(
                element for element in elements if element.element_id == adjacency[0]
            )
            first_local_face = local_face_ids[0]
            face_node_ids = tuple(
                first_element.node_ids[position]
                for position in TET10_FACE_NODE_POSITIONS[first_local_face]
            )
            faces.append(
                MeshFace(
                    face_id=source_face.face_id,
                    body_id=expected_body_id,
                    node_ids=face_node_ids,
                    adjacent_element_ids=adjacency,
                    local_face_ids=local_face_ids,
                )
            )
        return _MappedMesh(tuple(nodes), tuple(elements), tuple(faces), tuple(signed_volumes))

    @staticmethod
    def _raise_backend(error: BackendError) -> NoReturn:
        mapping = {
            BackendErrorCategory.INVALID_INPUT: PortErrorCategory.INVALID_INPUT,
            BackendErrorCategory.UNSUPPORTED_CAPABILITY: PortErrorCategory.UNSUPPORTED_CAPABILITY,
            BackendErrorCategory.ENVIRONMENT: PortErrorCategory.ENVIRONMENT,
            BackendErrorCategory.INTEGRITY: PortErrorCategory.INTEGRITY,
            BackendErrorCategory.QUALITY: PortErrorCategory.QUALITY,
        }
        raise PortError(mapping[error.category], str(error)) from error

    @staticmethod
    def _raise(category: PortErrorCategory, message: str) -> NoReturn:
        raise PortError(category, message)


def _point_values(point: Point3) -> tuple[float, float, float]:
    return (
        float(point.x.to_si().value),
        float(point.y.to_si().value),
        float(point.z.to_si().value),
    )


def _quantity_values(translation: Translation3) -> tuple[float, float, float]:
    return (
        float(translation.x.to_si().value),
        float(translation.y.to_si().value),
        float(translation.z.to_si().value),
    )


def _predicate_matches(face: BackendFace, predicate: object) -> bool:
    from febio_cae.domain.selection import CoordinatePredicate

    if not isinstance(predicate, CoordinatePredicate):
        return False
    direction = predicate.axis
    value = _dot(face.centroid_si, (direction.x, direction.y, direction.z))
    lower = float(predicate.value.to_si().value)
    if predicate.operator == "eq":
        return math.isclose(value, lower, rel_tol=1.0e-9, abs_tol=_GEOMETRY_TOLERANCE)
    if predicate.operator == "gte":
        return value >= lower
    if predicate.operator == "lte":
        return value <= lower
    if predicate.upper is None:
        return False
    return lower <= value <= float(predicate.upper.to_si().value)


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    return left[0] * right[0] + left[1] * right[1] + left[2] * right[2]


def _sub(left: Sequence[float], right: Sequence[float]) -> tuple[float, float, float]:
    return (left[0] - right[0], left[1] - right[1], left[2] - right[2])


def _signed_volume_points(points: Sequence[Sequence[float]]) -> float:
    first, second, third, fourth = points
    a = _sub(second, first)
    b = _sub(third, first)
    c = _sub(fourth, first)
    cross = (
        b[1] * c[2] - b[2] * c[1],
        b[2] * c[0] - b[0] * c[2],
        b[0] * c[1] - b[1] * c[0],
    )
    return _dot(a, cross) / 6.0


def _transform_point(
    point: Sequence[float], transform: RigidTransform
) -> tuple[float, float, float]:
    rotation = transform.rotation
    if not isinstance(rotation, ProperRotation):
        rotation = ProperRotation(rotation)
    matrix = cast(Sequence[Sequence[float]], rotation.matrix)
    translation = _quantity_values(transform.translation)
    return (
        matrix[0][0] * point[0]
        + matrix[0][1] * point[1]
        + matrix[0][2] * point[2]
        + translation[0],
        matrix[1][0] * point[0]
        + matrix[1][1] * point[1]
        + matrix[1][2] * point[2]
        + translation[1],
        matrix[2][0] * point[0]
        + matrix[2][1] * point[1]
        + matrix[2][2] * point[2]
        + translation[2],
    )


def _transform_backend_face(face: BackendFace, transform: RigidTransform) -> BackendFace:
    return BackendFace(
        face_id=face.face_id,
        body_id=face.body_id,
        frame=transform.target_frame,
        area_si=face.area_si,
        centroid_si=_transform_point(face.centroid_si, transform),
        boundary_points_si=tuple(
            _transform_point(point, transform) for point in face.boundary_points_si
        ),
        attributes=face.attributes,
        defects=face.defects,
    )


def _placed_inspection(report: BackendInspection, transform: RigidTransform) -> BackendInspection:
    if report.frame != transform.source_frame:
        raise PortError(
            PortErrorCategory.INTEGRITY, "inspection frame does not match placement source frame"
        )
    return replace(
        report,
        frame=transform.target_frame,
        bodies=tuple(
            replace(
                body, faces=tuple(_transform_backend_face(face, transform) for face in body.faces)
            )
            for body in report.bodies
        ),
    )


def _mesh_volume(mesh: BackendMesh) -> float:
    nodes = {node.node_id: node.coordinates_si for node in mesh.nodes}
    return sum(
        abs(_signed_volume_points(tuple(nodes[node_id] for node_id in element.node_ids[:4])))
        for element in mesh.elements
    )


def _selection_digest(selection: SelectionRef) -> str:
    return hashlib.sha256(selection.to_bytes()).hexdigest()


def _spec_selections(spec: CaseSpec) -> tuple[SelectionRef, ...]:
    candidates: list[SelectionRef] = []
    candidates.extend(support.selection for support in spec.support.supports)
    candidates.extend((spec.contact.part_surface, spec.contact.tool_surface))
    candidates.append(spec.rigid_tool.contact_surface)
    candidates.extend(item.selection for item in spec.mesh_policy.local_refinements)
    candidates.extend(item.selection for item in spec.outputs.requests)
    candidates.extend(item.selection for item in spec.outputs.evaluations)
    result: list[SelectionRef] = []
    seen: set[str] = set()
    for selection in candidates:
        digest = _selection_digest(selection)
        if digest not in seen:
            seen.add(digest)
            result.append(selection)
    return tuple(result)


def _mesh_recipe_digest(
    *,
    source_asset: SourceAssetRef | None,
    spec: CaseSpec,
    selection_digests: Sequence[str],
    backend_id: str,
    backend_version: str,
    applied: InitialContactPlacement,
) -> str:
    if source_asset is None:
        raise PortError(PortErrorCategory.ENVIRONMENT, "mesh recipe requires a source identity")
    payload = {
        "schema_version": "1",
        "source_asset": source_asset.to_dict(),
        "source_step_digest": spec.geometry.source_step_digest,
        "geometry": spec.geometry.to_dict(),
        "rigid_tool": spec.rigid_tool.to_dict(),
        "mesh_policy": spec.mesh_policy.to_dict(),
        "arrangement": spec.contact.arrangement.to_dict(),
        "applied_placement": applied.to_dict(),
        "selection_digests": sorted(selection_digests),
        "backend": {
            "id": backend_id,
            "version": backend_version,
            "mapping_id": _MAPPING_ID,
        },
    }
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


__all__ = ["InitialContactPlacement", "StepGeometryMeshAdapter"]
