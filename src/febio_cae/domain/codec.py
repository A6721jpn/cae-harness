"""Strict schema-1 codecs for existing and common workflow records.

This module is the single decode registry.  It deliberately calls existing
domain constructors instead of adding parallel child serializers or using
dynamic import/eval dispatch.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal, NoReturn, cast

from .artifacts import (
    FileEntry,
    GeometryBodyFact,
    GeometryInspection,
    GeometryInspectionRequest,
    GeometrySelectionRequest,
    MeshArtifact,
    MeshElement,
    MeshFace,
    MeshNode,
    MeshProvenance,
    MeshQualityRecord,
    MeshSet,
    SourceAssetRef,
)
from .budget import Budget
from .canonical import SCHEMA_VERSION, canonical_bytes
from .case_draft import CaseDraft
from .case_patch import CasePatch, CasePatchEdit
from .case_revision import CaseRevision
from .case_spec import CaseSpec
from .comparison import ComparisonAxis, ComparisonInterval, ComparisonSpec
from .compatibility import (
    CapabilityRef,
    CapabilityStatus,
    CompatibilityProfile,
    OutputMapping,
    ToolIdentity,
)
from .contact import (
    AsPlaced,
    ContactId,
    ContactIntent,
    CoulombFriction,
    Frictionless,
    SpecifiedGap,
)
from .evidence import EvidenceRef
from .execution import (
    AttemptRecord,
    ExecutionBundle,
    ExecutionSetting,
    ProcessIdentity,
)
from .geometry import GeometryIntent
from .lifecycle import (
    OperationStatus,
    PreviewStatus,
    RunState,
    ServiceDiagnostic,
    ServiceErrorCategory,
    TaskStatus,
)
from .material import CompressibleNeoHookean, IsotropicLinearElastic
from .mesh_policy import LocalRefinement, MeshPolicy, NumericalProfileRef
from .motion import MotionProfile
from .output_policy import EvaluationRequest, OutputLocation, OutputPolicy, OutputRequest
from .partial_case_spec import PartialCaseSpec
from .preview import PreviewReceipt, PreviewRequest
from .quality_policy import QualityCriterion, QualityPolicy, QualityThreshold
from .results import (
    AssessmentStatus,
    CriterionAssessment,
    MeasuredValue,
    OutputObservation,
    QualityAssessment,
    ReadResult,
    ReadStatus,
    ResultDataRef,
    ResultManifest,
)
from .rigid import RigidPrimitive
from .rigid_kinematics import (
    RigidDofComponent,
    RigidDofSpecification,
    RigidToolIntent,
)
from .selection import (
    CoordinatePredicate,
    CoordinatePredicateRule,
    FaceMeasurement,
    FaceSetRule,
    NamedAttributeRule,
    ResolutionSnapshot,
    SelectionRef,
    WholeBodyRule,
)
from .solver_policy import SolverControl, SolverPolicy, TimeIncrementPolicy
from .spatial import (
    BodyId,
    FaceId,
    FrameId,
    Point3,
    ProperRotation,
    RigidTransform,
    Translation3,
    UnitDirection,
)
from .support import SolidSupport, SupportComponent, SupportId, SupportSet
from .units import Quantity
from .questions import IssuedQuestion

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TARGET = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*(?:\.[A-Za-z_][A-Za-z0-9_-]*)*$")


class CodecError(ValueError):
    """Raised when a record cannot satisfy the explicit schema-1 codec."""


def _fail(field: str, message: str) -> NoReturn:
    raise CodecError(f"{field}: {message}")


def _mapping(value: object, keys: set[str], field: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        _fail(field, "must be an object")
    actual = set(value.keys())
    if any(not isinstance(key, str) for key in actual) or actual != keys:
        missing = sorted(keys - actual)
        unknown = sorted(actual - keys)
        _fail(field, f"unknown or missing keys (missing={missing!r}, unknown={unknown!r})")
    return dict(cast(Mapping[str, object], value))


def _schema(payload: Mapping[str, object], field: str) -> None:
    if payload.get("schema_version") != SCHEMA_VERSION:
        _fail(field, "unsupported schema_version")


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        _fail(field, "must be non-empty text without surrounding whitespace")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        _fail(field, "contains a control character")
    return value


def _optional_text(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _text(value, field)


def _digest(value: object, field: str) -> str:
    result = _text(value, field)
    if _SHA256.fullmatch(result) is None:
        _fail(field, "must be a lowercase SHA-256 digest")
    return result


def _integer(value: object, field: str, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(field, "must be an integer excluding bool")
    if minimum is not None and value < minimum:
        _fail(field, f"must be >= {minimum}")
    return value


def _number(value: object, field: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(field, "must be numeric excluding bool")
    if isinstance(value, float) and not math.isfinite(value):
        _fail(field, "must be finite")
    return value


def _sequence(value: object, field: str, *, allow_empty: bool = True) -> tuple[object, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        _fail(field, "must be a sequence")
    result = tuple(value)
    if not allow_empty and not result:
        _fail(field, "must not be empty")
    return result


def _quantity(value: object, field: str) -> Quantity:
    payload = _mapping(value, {"value", "unit"}, field)
    return Quantity(
        _number(payload["value"], f"{field}.value"), _text(payload["unit"], f"{field}.unit")
    )


def _evidence(value: object, field: str) -> EvidenceRef:
    payload = _mapping(
        value,
        {"schema_version", "source_kind", "reference", "target_field", "content_digest"},
        field,
    )
    _schema(payload, field)
    return EvidenceRef(
        schema_version=_text(payload["schema_version"], f"{field}.schema_version"),
        source_kind=_text(payload["source_kind"], f"{field}.source_kind"),
        reference=_text(payload["reference"], f"{field}.reference"),
        target_field=_text(payload["target_field"], f"{field}.target_field"),
        content_digest=_digest(payload["content_digest"], f"{field}.content_digest"),
    )


def _evidence_sequence(
    value: object, field: str, *, allow_empty: bool = True
) -> tuple[EvidenceRef, ...]:
    result = tuple(
        _evidence(item, f"{field}[{index}]")
        for index, item in enumerate(_sequence(value, field, allow_empty=allow_empty))
    )
    if len({canonical_bytes(item.to_dict()) for item in result}) != len(result):
        _fail(field, "contains duplicate complete evidence declarations")
    return result


def _frame(value: object, field: str) -> FrameId:
    return FrameId(_text(value, field))


def _body(value: object, field: str) -> BodyId:
    return BodyId(_text(value, field))


def _face(value: object, field: str) -> FaceId:
    if isinstance(value, Mapping):
        payload = _mapping(value, {"schema_version", "value"}, field)
        _schema(payload, field)
        value = payload["value"]
    return FaceId(_text(value, field))


def _point(value: object, field: str) -> Point3:
    payload = _mapping(value, {"schema_version", "frame", "x", "y", "z"}, field)
    _schema(payload, field)
    return Point3(
        _frame(payload["frame"], f"{field}.frame"),
        _quantity(payload["x"], f"{field}.x"),
        _quantity(payload["y"], f"{field}.y"),
        _quantity(payload["z"], f"{field}.z"),
    )


def _translation(value: object, field: str) -> Translation3:
    payload = _mapping(value, {"schema_version", "frame", "x", "y", "z"}, field)
    _schema(payload, field)
    return Translation3(
        _frame(payload["frame"], f"{field}.frame"),
        _quantity(payload["x"], f"{field}.x"),
        _quantity(payload["y"], f"{field}.y"),
        _quantity(payload["z"], f"{field}.z"),
    )


def _direction(value: object, field: str) -> UnitDirection:
    payload = _mapping(value, {"schema_version", "frame", "x", "y", "z"}, field)
    _schema(payload, field)
    return UnitDirection(
        _frame(payload["frame"], f"{field}.frame"),
        _number(payload["x"], f"{field}.x"),
        _number(payload["y"], f"{field}.y"),
        _number(payload["z"], f"{field}.z"),
    )


def _transform(value: object, field: str) -> RigidTransform:
    payload = _mapping(
        value, {"schema_version", "source_frame", "target_frame", "translation", "rotation"}, field
    )
    _schema(payload, field)
    rotation_payload = _mapping(
        payload["rotation"], {"schema_version", "matrix"}, f"{field}.rotation"
    )
    _schema(rotation_payload, f"{field}.rotation")
    matrix = tuple(
        _sequence(item, f"{field}.rotation.matrix[{index}]", allow_empty=False)
        for index, item in enumerate(
            _sequence(rotation_payload["matrix"], f"{field}.rotation.matrix", allow_empty=False)
        )
    )
    return RigidTransform(
        _frame(payload["source_frame"], f"{field}.source_frame"),
        _frame(payload["target_frame"], f"{field}.target_frame"),
        _translation(payload["translation"], f"{field}.translation"),
        ProperRotation(matrix),
    )


def _profile_ref(value: object, field: str) -> NumericalProfileRef:
    payload = _mapping(value, {"schema_version", "profile_id", "purpose", "record_digest"}, field)
    _schema(payload, field)
    return NumericalProfileRef(
        _text(payload["profile_id"], f"{field}.profile_id"),
        _text(payload["purpose"], f"{field}.purpose"),
        _digest(payload["record_digest"], f"{field}.record_digest"),
    )


def _selection_rule(
    value: object, field: str
) -> NamedAttributeRule | CoordinatePredicateRule | FaceSetRule | WholeBodyRule:
    raw = _mapping(value, set(value.keys()) if isinstance(value, Mapping) else set(), field)
    kind = _text(raw.get("kind"), f"{field}.kind")
    _schema(raw, field)
    if kind == "named_attribute":
        payload = _mapping(raw, {"schema_version", "kind", "attribute"}, field)
        return NamedAttributeRule(_text(payload["attribute"], f"{field}.attribute"))
    if kind == "whole_body":
        payload = _mapping(raw, {"schema_version", "kind", "body_id"}, field)
        return WholeBodyRule(_body(payload["body_id"], f"{field}.body_id"))
    if kind == "face_set":
        payload = _mapping(
            raw,
            {
                "schema_version",
                "kind",
                "geometry_digest",
                "body_id",
                "frame",
                "face_ids",
                "provenance",
            },
            field,
        )
        faces = tuple(
            _face(item, f"{field}.face_ids[{index}]")
            for index, item in enumerate(
                _sequence(payload["face_ids"], f"{field}.face_ids", allow_empty=False)
            )
        )
        return FaceSetRule(
            _digest(payload["geometry_digest"], f"{field}.geometry_digest"),
            _body(payload["body_id"], f"{field}.body_id"),
            _frame(payload["frame"], f"{field}.frame"),
            faces,
            _evidence(payload["provenance"], f"{field}.provenance"),
        )
    if kind == "coordinate_predicate":
        payload = _mapping(raw, {"schema_version", "kind", "body_id", "frame", "predicates"}, field)
        predicates: list[CoordinatePredicate] = []
        for index, item in enumerate(
            _sequence(payload["predicates"], f"{field}.predicates", allow_empty=False)
        ):
            predicate_payload = _mapping(
                item,
                {"schema_version", "axis", "operator", "value", "upper"},
                f"{field}.predicates[{index}]",
            )
            _schema(predicate_payload, f"{field}.predicates[{index}]")
            upper = (
                None
                if predicate_payload["upper"] is None
                else _quantity(predicate_payload["upper"], f"{field}.predicates[{index}].upper")
            )
            operator = _text(predicate_payload["operator"], f"{field}.predicates[{index}].operator")
            if operator not in {"eq", "gte", "lte", "between"}:
                _fail(f"{field}.predicates[{index}].operator", "unsupported operator")
            predicates.append(
                CoordinatePredicate(
                    _direction(predicate_payload["axis"], f"{field}.predicates[{index}].axis"),
                    cast(Literal["eq", "gte", "lte", "between"], operator),
                    _quantity(predicate_payload["value"], f"{field}.predicates[{index}].value"),
                    upper,
                )
            )
        return CoordinatePredicateRule(
            _body(payload["body_id"], f"{field}.body_id"),
            _frame(payload["frame"], f"{field}.frame"),
            predicates,
        )
    _fail(field, f"unsupported selection rule kind {kind!r}")


def _selection(value: object, field: str) -> SelectionRef:
    payload = _mapping(
        value,
        {
            "schema_version",
            "name",
            "stated_role",
            "role_evidence",
            "geometry_digest",
            "body_id",
            "frame",
            "rule",
            "resolution",
        },
        field,
    )
    _schema(payload, field)
    resolution_value = payload["resolution"]
    resolution = None
    if resolution_value is not None:
        resolution_payload = _mapping(
            resolution_value,
            {"schema_version", "geometry_digest", "body_id", "frame", "faces"},
            f"{field}.resolution",
        )
        _schema(resolution_payload, f"{field}.resolution")
        measurements: list[FaceMeasurement] = []
        for index, item in enumerate(
            _sequence(resolution_payload["faces"], f"{field}.resolution.faces", allow_empty=False)
        ):
            measurement_payload = _mapping(
                item,
                {"schema_version", "face_id", "area", "centroid"},
                f"{field}.resolution.faces[{index}]",
            )
            _schema(measurement_payload, f"{field}.resolution.faces[{index}]")
            measurements.append(
                FaceMeasurement(
                    _face(
                        measurement_payload["face_id"], f"{field}.resolution.faces[{index}].face_id"
                    ),
                    _quantity(
                        measurement_payload["area"], f"{field}.resolution.faces[{index}].area"
                    ),
                    _point(
                        measurement_payload["centroid"],
                        f"{field}.resolution.faces[{index}].centroid",
                    ),
                )
            )
        resolution = ResolutionSnapshot(
            _digest(resolution_payload["geometry_digest"], f"{field}.resolution.geometry_digest"),
            _body(resolution_payload["body_id"], f"{field}.resolution.body_id"),
            _frame(resolution_payload["frame"], f"{field}.resolution.frame"),
            measurements,
        )
    return SelectionRef(
        name=_text(payload["name"], f"{field}.name"),
        role=_text(payload["stated_role"], f"{field}.stated_role"),
        role_evidence=_evidence(payload["role_evidence"], f"{field}.role_evidence"),
        geometry_digest=_digest(payload["geometry_digest"], f"{field}.geometry_digest"),
        body_id=_body(payload["body_id"], f"{field}.body_id"),
        frame=_frame(payload["frame"], f"{field}.frame"),
        rule=_selection_rule(payload["rule"], f"{field}.rule"),
        resolution=resolution,
    )


def _geometry(value: object, field: str) -> GeometryIntent:
    payload = _mapping(
        value,
        {
            "schema_version",
            "source_step_digest",
            "geometry_digest",
            "inspection_digest",
            "body_id",
            "step_unit",
            "placement",
            "body_evidence",
            "unit_evidence",
            "placement_evidence",
        },
        field,
    )
    _schema(payload, field)
    return GeometryIntent(
        _digest(payload["source_step_digest"], f"{field}.source_step_digest"),
        _digest(payload["geometry_digest"], f"{field}.geometry_digest"),
        _digest(payload["inspection_digest"], f"{field}.inspection_digest"),
        _body(payload["body_id"], f"{field}.body_id"),
        _text(payload["step_unit"], f"{field}.step_unit"),
        _transform(payload["placement"], f"{field}.placement"),
        _evidence(payload["body_evidence"], f"{field}.body_evidence"),
        _evidence(payload["unit_evidence"], f"{field}.unit_evidence"),
        _evidence(payload["placement_evidence"], f"{field}.placement_evidence"),
    )


def _material(value: object, field: str) -> IsotropicLinearElastic | CompressibleNeoHookean:
    payload = (
        cast(Mapping[str, object], value)
        if isinstance(value, Mapping)
        else _fail(field, "must be an object")
    )
    kind = payload.get("kind")
    if kind == "isotropic_linear_elastic":
        return cast(IsotropicLinearElastic, IsotropicLinearElastic.from_dict(value))
    if kind == "compressible_neo_hookean":
        return cast(CompressibleNeoHookean, CompressibleNeoHookean.from_dict(value))
    _fail(field, "unsupported material kind")


def _support_component(value: object, field: str) -> SupportComponent:
    payload = _mapping(value, {"schema_version", "state", "evidence"}, field)
    _schema(payload, field)
    return SupportComponent(
        _text(payload["state"], f"{field}.state"),
        _evidence(payload["evidence"], f"{field}.evidence"),
    )


def _support(value: object, field: str) -> SupportSet:
    payload = _mapping(value, {"schema_version", "supports"}, field)
    _schema(payload, field)
    supports: list[SolidSupport] = []
    for index, item in enumerate(
        _sequence(payload["supports"], f"{field}.supports", allow_empty=False)
    ):
        support_payload = _mapping(
            item,
            {
                "schema_version",
                "support_id",
                "selection",
                "frame",
                "frame_evidence",
                "x",
                "y",
                "z",
                "transform",
                "transform_evidence",
            },
            f"{field}.supports[{index}]",
        )
        _schema(support_payload, f"{field}.supports[{index}]")
        supports.append(
            SolidSupport(
                SupportId(
                    _text(support_payload["support_id"], f"{field}.supports[{index}].support_id")
                ),
                _selection(support_payload["selection"], f"{field}.supports[{index}].selection"),
                _frame(support_payload["frame"], f"{field}.supports[{index}].frame"),
                _support_component(support_payload["x"], f"{field}.supports[{index}].x"),
                _support_component(support_payload["y"], f"{field}.supports[{index}].y"),
                _support_component(support_payload["z"], f"{field}.supports[{index}].z"),
                None
                if support_payload["transform"] is None
                else _transform(
                    support_payload["transform"], f"{field}.supports[{index}].transform"
                ),
                _evidence(
                    support_payload["frame_evidence"], f"{field}.supports[{index}].frame_evidence"
                ),
                None
                if support_payload["transform_evidence"] is None
                else _evidence(
                    support_payload["transform_evidence"],
                    f"{field}.supports[{index}].transform_evidence",
                ),
            )
        )
    return SupportSet(supports)


def _rigid_primitive(value: object, field: str) -> RigidPrimitive:
    payload = _mapping(
        value,
        {
            "schema_version",
            "kind",
            "body_id",
            "local_frame",
            "placement",
            "dimensions",
            "dimension_evidence",
            "model_evidence",
            "placement_evidence",
            "convention",
        },
        field,
    )
    _schema(payload, field)
    kind = _text(payload["kind"], f"{field}.kind")
    dimensions_raw = _mapping(
        payload["dimensions"],
        set(payload["dimensions"].keys()) if isinstance(payload["dimensions"], Mapping) else set(),
        f"{field}.dimensions",
    )
    evidence_raw = _mapping(
        payload["dimension_evidence"],
        set(payload["dimension_evidence"].keys())
        if isinstance(payload["dimension_evidence"], Mapping)
        else set(),
        f"{field}.dimension_evidence",
    )
    dimensions = {
        name: _quantity(item, f"{field}.dimensions.{name}") for name, item in dimensions_raw.items()
    }
    dimension_evidence = {
        name: _evidence(item, f"{field}.dimension_evidence.{name}")
        for name, item in evidence_raw.items()
    }
    result = RigidPrimitive(
        kind,
        _body(payload["body_id"], f"{field}.body_id"),
        _frame(payload["local_frame"], f"{field}.local_frame"),
        _transform(payload["placement"], f"{field}.placement"),
        dimensions,
        dimension_evidence,
        _evidence(payload["model_evidence"], f"{field}.model_evidence"),
        _evidence(payload["placement_evidence"], f"{field}.placement_evidence"),
    )
    if payload["convention"] != result.convention:
        _fail(field, "convention does not match kind")
    return result


def _rigid_dof(value: object, field: str) -> RigidDofComponent:
    payload = _mapping(value, {"schema_version", "state", "evidence"}, field)
    _schema(payload, field)
    return RigidDofComponent(
        _text(payload["state"], f"{field}.state"),
        _evidence(payload["evidence"], f"{field}.evidence"),
    )


def _rigid_tool(value: object, field: str) -> RigidToolIntent:
    payload = _mapping(
        value,
        {"schema_version", "primitive", "contact_surface", "contact_surface_evidence", "dofs"},
        field,
    )
    _schema(payload, field)
    dof_payload = _mapping(
        payload["dofs"],
        {"schema_version", "frame", "frame_evidence", "x", "y", "z", "rx", "ry", "rz"},
        f"{field}.dofs",
    )
    _schema(dof_payload, f"{field}.dofs")
    dofs = RigidDofSpecification(
        _frame(dof_payload["frame"], f"{field}.dofs.frame"),
        _rigid_dof(dof_payload["x"], f"{field}.dofs.x"),
        _rigid_dof(dof_payload["y"], f"{field}.dofs.y"),
        _rigid_dof(dof_payload["z"], f"{field}.dofs.z"),
        _rigid_dof(dof_payload["rx"], f"{field}.dofs.rx"),
        _rigid_dof(dof_payload["ry"], f"{field}.dofs.ry"),
        _rigid_dof(dof_payload["rz"], f"{field}.dofs.rz"),
        _evidence(dof_payload["frame_evidence"], f"{field}.dofs.frame_evidence"),
    )
    return RigidToolIntent(
        _rigid_primitive(payload["primitive"], f"{field}.primitive"),
        _selection(payload["contact_surface"], f"{field}.contact_surface"),
        dofs,
        _evidence(payload["contact_surface_evidence"], f"{field}.contact_surface_evidence"),
    )


def _contact(value: object, field: str) -> ContactIntent:
    payload = _mapping(
        value,
        {
            "schema_version",
            "contact_id",
            "part_surface",
            "tool_surface",
            "pair_frame",
            "part_surface_evidence",
            "tool_surface_evidence",
            "pair_frame_evidence",
            "friction",
            "arrangement",
        },
        field,
    )
    _schema(payload, field)
    friction_value = payload["friction"]
    if not isinstance(friction_value, Mapping):
        _fail(f"{field}.friction", "must be an object")
    friction_kind = _text(friction_value.get("kind"), f"{field}.friction.kind")
    friction: Frictionless | CoulombFriction
    if friction_kind == "frictionless":
        friction_payload = _mapping(
            friction_value,
            {"schema_version", "kind", "model_evidence"},
            f"{field}.friction",
        )
        _schema(friction_payload, f"{field}.friction")
        friction = Frictionless(
            _evidence(friction_payload["model_evidence"], f"{field}.friction.model_evidence")
        )
    elif friction_kind == "coulomb":
        friction_payload = _mapping(
            friction_value,
            {
                "schema_version",
                "kind",
                "coefficient",
                "model_evidence",
                "coefficient_evidence",
            },
            f"{field}.friction",
        )
        _schema(friction_payload, f"{field}.friction")
        friction = CoulombFriction(
            _quantity(friction_payload["coefficient"], f"{field}.friction.coefficient"),
            _evidence(friction_payload["model_evidence"], f"{field}.friction.model_evidence"),
            _evidence(
                friction_payload["coefficient_evidence"], f"{field}.friction.coefficient_evidence"
            ),
        )
    else:
        _fail(f"{field}.friction", "unsupported kind")
    arrangement_value = payload["arrangement"]
    if not isinstance(arrangement_value, Mapping):
        _fail(f"{field}.arrangement", "must be an object")
    arrangement_kind = _text(arrangement_value.get("kind"), f"{field}.arrangement.kind")
    arrangement: AsPlaced | SpecifiedGap
    if arrangement_kind == "as_placed":
        arrangement_payload = _mapping(
            arrangement_value,
            {"schema_version", "kind", "arrangement_evidence"},
            f"{field}.arrangement",
        )
        _schema(arrangement_payload, f"{field}.arrangement")
        arrangement = AsPlaced(
            _evidence(
                arrangement_payload["arrangement_evidence"],
                f"{field}.arrangement.arrangement_evidence",
            )
        )
    elif arrangement_kind == "specified_gap":
        arrangement_payload = _mapping(
            arrangement_value,
            {
                "schema_version",
                "kind",
                "gap",
                "direction",
                "arrangement_evidence",
                "gap_evidence",
                "direction_evidence",
            },
            f"{field}.arrangement",
        )
        _schema(arrangement_payload, f"{field}.arrangement")
        arrangement = SpecifiedGap(
            _quantity(arrangement_payload["gap"], f"{field}.arrangement.gap"),
            _direction(arrangement_payload["direction"], f"{field}.arrangement.direction"),
            _evidence(
                arrangement_payload["arrangement_evidence"],
                f"{field}.arrangement.arrangement_evidence",
            ),
            _evidence(arrangement_payload["gap_evidence"], f"{field}.arrangement.gap_evidence"),
            _evidence(
                arrangement_payload["direction_evidence"], f"{field}.arrangement.direction_evidence"
            ),
        )
    else:
        _fail(f"{field}.arrangement", "unsupported kind")
    return ContactIntent(
        ContactId(_text(payload["contact_id"], f"{field}.contact_id")),
        _selection(payload["part_surface"], f"{field}.part_surface"),
        _selection(payload["tool_surface"], f"{field}.tool_surface"),
        _frame(payload["pair_frame"], f"{field}.pair_frame"),
        _evidence(payload["part_surface_evidence"], f"{field}.part_surface_evidence"),
        _evidence(payload["tool_surface_evidence"], f"{field}.tool_surface_evidence"),
        _evidence(payload["pair_frame_evidence"], f"{field}.pair_frame_evidence"),
        friction,
        arrangement,
    )


def _mesh_policy(value: object, field: str) -> MeshPolicy:
    payload = _mapping(
        value,
        {
            "schema_version",
            "element_type",
            "global_size",
            "local_refinements",
            "quality_profile",
            "max_refinements",
        },
        field,
    )
    _schema(payload, field)
    refinements: list[LocalRefinement] = []
    for index, item in enumerate(
        _sequence(payload["local_refinements"], f"{field}.local_refinements")
    ):
        item_payload = _mapping(
            item,
            {"schema_version", "refinement_id", "selection", "size"},
            f"{field}.local_refinements[{index}]",
        )
        _schema(item_payload, f"{field}.local_refinements[{index}]")
        refinements.append(
            LocalRefinement(
                _text(item_payload["refinement_id"], "refinement_id"),
                _selection(
                    item_payload["selection"], f"{field}.local_refinements[{index}].selection"
                ),
                _quantity(item_payload["size"], f"{field}.local_refinements[{index}].size"),
            )
        )
    return MeshPolicy(
        _text(payload["element_type"], f"{field}.element_type"),
        _quantity(payload["global_size"], f"{field}.global_size"),
        refinements,
        _profile_ref(payload["quality_profile"], f"{field}.quality_profile"),
        _integer(payload["max_refinements"], f"{field}.max_refinements", 0),
    )


def _solver_policy(value: object, field: str) -> SolverPolicy:
    payload = _mapping(
        value, {"schema_version", "profile", "controls", "increments", "retry_recipe_ids"}, field
    )
    _schema(payload, field)
    controls: list[SolverControl] = []
    for index, item in enumerate(
        _sequence(payload["controls"], f"{field}.controls", allow_empty=False)
    ):
        item_payload = _mapping(
            item, {"schema_version", "name", "value"}, f"{field}.controls[{index}]"
        )
        _schema(item_payload, f"{field}.controls[{index}]")
        value_payload = _mapping(
            item_payload["value"], {"kind", "value"}, f"{field}.controls[{index}].value"
        )
        kind = _text(value_payload["kind"], f"{field}.controls[{index}].value.kind")
        if kind == "quantity":
            control_value: Quantity | int | bool = _quantity(
                value_payload["value"], f"{field}.controls[{index}].value.value"
            )
        elif kind == "integer":
            control_value = _integer(
                value_payload["value"], f"{field}.controls[{index}].value.value"
            )
        elif kind == "boolean":
            if type(value_payload["value"]) is not bool:
                _fail(f"{field}.controls[{index}].value.value", "must be a boolean")
            control_value = value_payload["value"]
        else:
            _fail(f"{field}.controls[{index}].value", "unsupported control value kind")
        controls.append(SolverControl(_text(item_payload["name"], "control.name"), control_value))
    increment_payload = _mapping(
        payload["increments"],
        {
            "schema_version",
            "initial_step",
            "minimum_step",
            "maximum_step",
            "adaptive",
            "max_steps",
            "max_step_retries",
            "must_points",
        },
        f"{field}.increments",
    )
    _schema(increment_payload, f"{field}.increments")
    if type(increment_payload["adaptive"]) is not bool:
        _fail(f"{field}.increments.adaptive", "must be a boolean")
    increments = TimeIncrementPolicy(
        _quantity(increment_payload["initial_step"], "initial_step"),
        _quantity(increment_payload["minimum_step"], "minimum_step"),
        _quantity(increment_payload["maximum_step"], "maximum_step"),
        increment_payload["adaptive"],
        _integer(increment_payload["max_steps"], "max_steps", 1),
        _integer(increment_payload["max_step_retries"], "max_step_retries", 0),
        tuple(
            _quantity(item, f"{field}.increments.must_points[]")
            for item in _sequence(
                increment_payload["must_points"], f"{field}.increments.must_points"
            )
        ),
    )
    return SolverPolicy(
        _profile_ref(payload["profile"], f"{field}.profile"),
        controls,
        increments,
        tuple(
            _text(item, f"{field}.retry_recipe_ids[]")
            for item in _sequence(payload["retry_recipe_ids"], f"{field}.retry_recipe_ids")
        ),
    )


def _output_policy(value: object, field: str) -> OutputPolicy:
    payload = _mapping(
        value, {"schema_version", "profile", "requests", "saved_times", "evaluations"}, field
    )
    _schema(payload, field)
    requests: list[OutputRequest] = []
    for index, item in enumerate(
        _sequence(payload["requests"], f"{field}.requests", allow_empty=False)
    ):
        item_payload = _mapping(
            item,
            {
                "schema_version",
                "request_id",
                "quantity_id",
                "measure_id",
                "component_id",
                "location",
                "selection",
                "frame",
                "display_unit",
                "evidence",
            },
            f"{field}.requests[{index}]",
        )
        _schema(item_payload, f"{field}.requests[{index}]")
        requests.append(
            OutputRequest(
                _text(item_payload["request_id"], "request_id"),
                _text(item_payload["quantity_id"], "quantity_id"),
                _text(item_payload["measure_id"], "measure_id"),
                _text(item_payload["component_id"], "component_id"),
                cast(OutputLocation, _text(item_payload["location"], "location")),
                _selection(item_payload["selection"], f"{field}.requests[{index}].selection"),
                _frame(item_payload["frame"], "frame"),
                _text(item_payload["display_unit"], "display_unit"),
                _evidence(item_payload["evidence"], "evidence"),
            )
        )
    evaluations: list[EvaluationRequest] = []
    for index, item in enumerate(
        _sequence(payload["evaluations"], f"{field}.evaluations", allow_empty=False)
    ):
        item_payload = _mapping(
            item,
            {
                "schema_version",
                "evaluation_id",
                "output_request_id",
                "aggregation_id",
                "selection",
                "state_times",
                "evidence",
            },
            f"{field}.evaluations[{index}]",
        )
        _schema(item_payload, f"{field}.evaluations[{index}]")
        evaluations.append(
            EvaluationRequest(
                _text(item_payload["evaluation_id"], "evaluation_id"),
                _text(item_payload["output_request_id"], "output_request_id"),
                _text(item_payload["aggregation_id"], "aggregation_id"),
                _selection(item_payload["selection"], f"{field}.evaluations[{index}].selection"),
                tuple(
                    _quantity(item_time, "state_time")
                    for item_time in _sequence(
                        item_payload["state_times"], "state_times", allow_empty=False
                    )
                ),
                _evidence(item_payload["evidence"], "evidence"),
            )
        )
    return OutputPolicy(
        _profile_ref(payload["profile"], f"{field}.profile"),
        requests,
        tuple(
            _quantity(item, f"{field}.saved_times[]")
            for item in _sequence(payload["saved_times"], f"{field}.saved_times")
        ),
        evaluations,
    )


def _quality_policy(value: object, field: str) -> QualityPolicy:
    payload = _mapping(value, {"schema_version", "profile", "criteria"}, field)
    _schema(payload, field)
    criteria: list[QualityCriterion] = []
    for index, item in enumerate(
        _sequence(payload["criteria"], f"{field}.criteria", allow_empty=False)
    ):
        item_payload = _mapping(
            item,
            {
                "schema_version",
                "criterion_id",
                "metric_id",
                "evaluation_ids",
                "thresholds",
                "applicability_reason",
                "evidence",
            },
            f"{field}.criteria[{index}]",
        )
        _schema(item_payload, f"{field}.criteria[{index}]")
        thresholds: list[QualityThreshold] = []
        for threshold_index, threshold in enumerate(
            _sequence(item_payload["thresholds"], "thresholds", allow_empty=False)
        ):
            threshold_payload = _mapping(
                threshold,
                {"schema_version", "parameter_id", "value"},
                f"thresholds[{threshold_index}]",
            )
            _schema(threshold_payload, f"thresholds[{threshold_index}]")
            thresholds.append(
                QualityThreshold(
                    _text(threshold_payload["parameter_id"], "parameter_id"),
                    _quantity(threshold_payload["value"], "value"),
                )
            )
        criteria.append(
            QualityCriterion(
                _text(item_payload["criterion_id"], "criterion_id"),
                _text(item_payload["metric_id"], "metric_id"),
                tuple(
                    _text(item_id, "evaluation_id")
                    for item_id in _sequence(
                        item_payload["evaluation_ids"], "evaluation_ids", allow_empty=False
                    )
                ),
                thresholds,
                _text(item_payload["applicability_reason"], "applicability_reason"),
                _evidence(item_payload["evidence"], "evidence"),
            )
        )
    return QualityPolicy(_profile_ref(payload["profile"], f"{field}.profile"), criteria)


def _budget(value: object, field: str) -> Budget:
    payload = _mapping(
        value,
        {
            "schema_version",
            "max_elapsed",
            "max_attempts",
            "cpu_workers",
            "max_llm_calls",
            "max_llm_tokens",
        },
        field,
    )
    _schema(payload, field)
    return Budget(
        _quantity(payload["max_elapsed"], "max_elapsed"),
        _integer(payload["max_attempts"], "max_attempts", 1),
        _integer(payload["cpu_workers"], "cpu_workers", 1),
        _integer(payload["max_llm_calls"], "max_llm_calls", 0),
        _integer(payload["max_llm_tokens"], "max_llm_tokens", 0),
    )


def _motion(value: object, field: str) -> MotionProfile:
    try:
        return MotionProfile.from_dict(value)
    except (TypeError, ValueError) as error:
        raise CodecError(f"{field}: invalid motion projection: {error}") from error


def _case_spec(value: object, field: str) -> CaseSpec:
    payload = _mapping(
        value,
        {
            "schema_version",
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
        },
        field,
    )
    _schema(payload, field)
    return CaseSpec(
        _geometry(payload["geometry"], f"{field}.geometry"),
        _material(payload["material"], f"{field}.material"),
        _support(payload["support"], f"{field}.support"),
        _rigid_tool(payload["rigid_tool"], f"{field}.rigid_tool"),
        _motion(payload["motion"], f"{field}.motion"),
        _contact(payload["contact"], f"{field}.contact"),
        _mesh_policy(payload["mesh_policy"], f"{field}.mesh_policy"),
        _solver_policy(payload["solver_policy"], f"{field}.solver_policy"),
        _output_policy(payload["outputs"], f"{field}.outputs"),
        _quality_policy(payload["quality_policy"], f"{field}.quality_policy"),
        _budget(payload["budget"], f"{field}.budget"),
    )


def _partial_case_spec(value: object, field: str) -> Any:
    from .partial_case_spec import PartialCaseSpec

    if isinstance(value, Mapping) and value.get("schema_version") != SCHEMA_VERSION:
        _fail(field, "unsupported schema_version")
    payload = _mapping(
        value,
        {
            "schema_version",
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
        },
        field,
    )
    _schema(payload, field)
    decoders: dict[str, Any] = {
        "geometry": _geometry,
        "material": _material,
        "support": _support,
        "rigid_tool": _rigid_tool,
        "motion": _motion,
        "contact": _contact,
        "mesh_policy": _mesh_policy,
        "solver_policy": _solver_policy,
        "outputs": _output_policy,
        "quality_policy": _quality_policy,
        "budget": _budget,
    }
    kwargs = {
        name: None if payload[name] is None else decoder(payload[name], f"{field}.{name}")
        for name, decoder in decoders.items()
    }
    return PartialCaseSpec(**kwargs)


def _case_draft(value: object, field: str) -> CaseDraft:
    payload = _mapping(
        value,
        {
            "schema_version",
            "case_id",
            "draft_id",
            "generation",
            "input_intent",
            "parent_revision_id",
            "parent_spec_digest",
            "values",
            "evidence",
            "unresolved_fields",
        },
        field,
    )
    _schema(payload, field)
    draft = CaseDraft(
        _text(payload["case_id"], f"{field}.case_id"),
        _text(payload["draft_id"], f"{field}.draft_id"),
        _integer(payload["generation"], f"{field}.generation", 0),
        payload["input_intent"]
        if isinstance(payload["input_intent"], str)
        else _fail(f"{field}.input_intent", "must be text"),
        _optional_text(payload["parent_revision_id"], f"{field}.parent_revision_id"),
        None
        if payload["parent_spec_digest"] is None
        else _digest(payload["parent_spec_digest"], f"{field}.parent_spec_digest"),
        _partial_case_spec(payload["values"], f"{field}.values"),
        _evidence_sequence(payload["evidence"], f"{field}.evidence"),
    )
    unresolved = tuple(
        _text(item, f"{field}.unresolved_fields[]")
        for item in _sequence(payload["unresolved_fields"], f"{field}.unresolved_fields")
    )
    if unresolved != draft.unresolved_fields:
        _fail(field, "unresolved_fields does not match values")
    return draft


def _case_revision(value: object, field: str) -> CaseRevision:
    payload = _mapping(
        value,
        {
            "schema_version",
            "case_id",
            "revision_id",
            "parent_revision_id",
            "parent_spec_digest",
            "spec",
            "evidence",
            "spec_digest",
        },
        field,
    )
    _schema(payload, field)
    revision = CaseRevision(
        _text(payload["case_id"], f"{field}.case_id"),
        _text(payload["revision_id"], f"{field}.revision_id"),
        _optional_text(payload["parent_revision_id"], f"{field}.parent_revision_id"),
        None
        if payload["parent_spec_digest"] is None
        else _digest(payload["parent_spec_digest"], f"{field}.parent_spec_digest"),
        _case_spec(payload["spec"], f"{field}.spec"),
        _evidence_sequence(payload["evidence"], f"{field}.evidence", allow_empty=False),
    )
    if revision.spec_digest != _digest(payload["spec_digest"], f"{field}.spec_digest"):
        _fail(field, "spec_digest does not match content")
    return revision


def _source_asset(value: object, field: str) -> SourceAssetRef:
    payload = _mapping(value, {"schema_version", "asset_id", "content_digest", "media_type"}, field)
    _schema(payload, field)
    return SourceAssetRef(
        _text(payload["asset_id"], "asset_id"),
        _digest(payload["content_digest"], "content_digest"),
        _text(payload["media_type"], "media_type"),
    )


def _mesh_artifact(value: object, field: str) -> MeshArtifact:
    payload = _mapping(
        value,
        {
            "schema_version",
            "artifact_id",
            "frame",
            "provenance",
            "nodes",
            "elements",
            "faces",
            "sets",
            "quality_records",
            "artifact_digest",
        },
        field,
    )
    _schema(payload, field)
    provenance_payload = _mapping(
        payload["provenance"],
        {
            "schema_version",
            "source_geometry_digest",
            "source_body_ids",
            "source_selection_digests",
            "mesh_recipe_digest",
            "tool_id",
            "tool_version",
            "mapping_id",
            "node_ordering_id",
            "face_ordering_id",
        },
        f"{field}.provenance",
    )
    _schema(provenance_payload, f"{field}.provenance")
    provenance = MeshProvenance(
        _digest(provenance_payload["source_geometry_digest"], "source_geometry_digest"),
        tuple(
            _text(item, "source_body_id")
            for item in _sequence(
                provenance_payload["source_body_ids"], "source_body_ids", allow_empty=False
            )
        ),
        tuple(
            _digest(item, "source_selection_digest")
            for item in _sequence(
                provenance_payload["source_selection_digests"], "source_selection_digests"
            )
        ),
        _digest(provenance_payload["mesh_recipe_digest"], "mesh_recipe_digest"),
        _text(provenance_payload["tool_id"], "tool_id"),
        _text(provenance_payload["tool_version"], "tool_version"),
        _text(provenance_payload["mapping_id"], "mapping_id"),
        _text(provenance_payload["node_ordering_id"], "node_ordering_id"),
        _text(provenance_payload["face_ordering_id"], "face_ordering_id"),
    )
    nodes: list[MeshNode] = []
    for index, item in enumerate(_sequence(payload["nodes"], f"{field}.nodes", allow_empty=False)):
        item_payload = _mapping(
            item, {"schema_version", "node_id", "coordinates_si"}, f"{field}.nodes[{index}]"
        )
        _schema(item_payload, f"{field}.nodes[{index}]")
        nodes.append(
            MeshNode(
                _integer(item_payload["node_id"], "node_id", 1),
                tuple(
                    _number(coord, "coordinate")
                    for coord in _sequence(
                        item_payload["coordinates_si"], "coordinates_si", allow_empty=False
                    )
                ),
            )
        )
    elements: list[MeshElement] = []
    for index, item in enumerate(
        _sequence(payload["elements"], f"{field}.elements", allow_empty=False)
    ):
        item_payload = _mapping(
            item,
            {"schema_version", "element_id", "element_type", "node_ids", "body_id"},
            f"{field}.elements[{index}]",
        )
        _schema(item_payload, f"{field}.elements[{index}]")
        elements.append(
            MeshElement(
                _integer(item_payload["element_id"], "element_id", 1),
                _text(item_payload["element_type"], "element_type"),
                tuple(
                    _integer(node, "node_id", 1)
                    for node in _sequence(item_payload["node_ids"], "node_ids", allow_empty=False)
                ),
                _text(item_payload["body_id"], "body_id"),
            )
        )
    faces: list[MeshFace] = []
    for index, item in enumerate(_sequence(payload["faces"], f"{field}.faces")):
        item_payload = _mapping(
            item,
            {
                "schema_version",
                "face_id",
                "body_id",
                "node_ids",
                "adjacent_element_ids",
                "local_face_ids",
            },
            f"{field}.faces[{index}]",
        )
        _schema(item_payload, f"{field}.faces[{index}]")
        faces.append(
            MeshFace(
                _text(item_payload["face_id"], "face_id"),
                _text(item_payload["body_id"], "body_id"),
                tuple(
                    _integer(node, "node_id", 1)
                    for node in _sequence(item_payload["node_ids"], "node_ids", allow_empty=False)
                ),
                tuple(
                    _integer(element, "element_id", 1)
                    for element in _sequence(
                        item_payload["adjacent_element_ids"],
                        "adjacent_element_ids",
                        allow_empty=False,
                    )
                ),
                tuple(
                    _integer(local, "local_face_id", 0)
                    for local in _sequence(
                        item_payload["local_face_ids"], "local_face_ids", allow_empty=False
                    )
                ),
            )
        )
    sets: list[MeshSet] = []
    for index, item in enumerate(_sequence(payload["sets"], f"{field}.sets")):
        item_payload = _mapping(
            item,
            {
                "schema_version",
                "set_id",
                "kind",
                "body_id",
                "member_ids",
                "source_selection_digest",
            },
            f"{field}.sets[{index}]",
        )
        _schema(item_payload, f"{field}.sets[{index}]")
        kind = _text(item_payload["kind"], "kind").lower()
        if kind in {"node", "element"}:
            members: tuple[int | str, ...] = tuple(
                _integer(member, "member_id", 1)
                for member in _sequence(item_payload["member_ids"], "member_ids", allow_empty=False)
            )
        elif kind in {"face", "body"}:
            members = tuple(
                _text(member, "member_id")
                for member in _sequence(item_payload["member_ids"], "member_ids", allow_empty=False)
            )
        else:
            _fail(f"{field}.sets[{index}].kind", "unsupported mesh set kind")
        sets.append(
            MeshSet(
                _text(item_payload["set_id"], "set_id"),
                kind,
                _text(item_payload["body_id"], "body_id"),
                members,
                _digest(item_payload["source_selection_digest"], "source_selection_digest"),
            )
        )
    quality: list[MeshQualityRecord] = []
    for index, item in enumerate(_sequence(payload["quality_records"], f"{field}.quality_records")):
        item_payload = _mapping(
            item,
            {"schema_version", "metric_id", "value", "unit", "threshold", "status", "reason"},
            f"{field}.quality_records[{index}]",
        )
        _schema(item_payload, f"{field}.quality_records[{index}]")
        quality.append(
            MeshQualityRecord(
                _text(item_payload["metric_id"], "metric_id"),
                _number(item_payload["value"], "value"),
                _text(item_payload["unit"], "unit"),
                None
                if item_payload["threshold"] is None
                else _number(item_payload["threshold"], "threshold"),
                _text(item_payload["status"], "status"),
                _text(item_payload["reason"], "reason"),
            )
        )
    result = MeshArtifact(
        _text(payload["artifact_id"], "artifact_id"),
        _frame(payload["frame"], "frame"),
        provenance,
        nodes,
        elements,
        faces,
        sets,
        quality,
    )
    if result.artifact_digest != _digest(payload["artifact_digest"], f"{field}.artifact_digest"):
        _fail(field, "artifact_digest does not match content")
    return result


def _file_entry(value: object, field: str) -> FileEntry:
    payload = _mapping(
        value, {"schema_version", "logical_path", "digest", "size_bytes", "role"}, field
    )
    _schema(payload, field)
    return FileEntry(
        _text(payload["logical_path"], "logical_path"),
        _digest(payload["digest"], "digest"),
        _integer(payload["size_bytes"], "size_bytes", 0),
        _text(payload["role"], "role"),
    )


def _tool(value: object, field: str) -> ToolIdentity:
    payload = _mapping(value, {"schema_version", "tool_id", "version", "executable_digest"}, field)
    _schema(payload, field)
    return ToolIdentity(
        _text(payload["tool_id"], "tool_id"),
        _text(payload["version"], "version"),
        _digest(payload["executable_digest"], "executable_digest"),
    )


def _compatibility(value: object, field: str) -> CompatibilityProfile:
    payload = _mapping(
        value,
        {
            "schema_version",
            "profile_id",
            "solver",
            "reader",
            "capabilities",
            "output_mappings",
            "evidence",
        },
        field,
    )
    _schema(payload, field)
    capabilities: list[CapabilityRef] = []
    for index, item in enumerate(_sequence(payload["capabilities"], "capabilities")):
        item_payload = _mapping(
            item,
            {
                "schema_version",
                "capability_id",
                "status",
                "version",
                "compression",
                "ordering_id",
                "sign_mapping_id",
                "evidence",
            },
            f"capabilities[{index}]",
        )
        _schema(item_payload, f"capabilities[{index}]")
        try:
            status = CapabilityStatus(_text(item_payload["status"], "status"))
        except ValueError as error:
            raise CodecError(f"capabilities[{index}].status: unsupported value") from error
        capabilities.append(
            CapabilityRef(
                _text(item_payload["capability_id"], "capability_id"),
                status,
                _text(item_payload["version"], "version"),
                _text(item_payload["compression"], "compression"),
                _text(item_payload["ordering_id"], "ordering_id"),
                _text(item_payload["sign_mapping_id"], "sign_mapping_id"),
                _evidence_sequence(item_payload["evidence"], "evidence"),
            )
        )
    mappings: list[OutputMapping] = []
    for index, item in enumerate(_sequence(payload["output_mappings"], "output_mappings")):
        item_payload = _mapping(
            item,
            {
                "schema_version",
                "canonical_id",
                "native_name",
                "location",
                "value_type",
                "unit",
                "frame",
                "raw_sign",
                "canonical_sign",
                "measure_id",
            },
            f"output_mappings[{index}]",
        )
        _schema(item_payload, f"output_mappings[{index}]")
        mappings.append(
            OutputMapping(
                _text(item_payload["canonical_id"], "canonical_id"),
                _text(item_payload["native_name"], "native_name"),
                _text(item_payload["location"], "location"),
                _text(item_payload["value_type"], "value_type"),
                _text(item_payload["unit"], "unit"),
                _frame(item_payload["frame"], "frame"),
                _integer(item_payload["raw_sign"], "raw_sign"),
                _integer(item_payload["canonical_sign"], "canonical_sign"),
                _text(item_payload["measure_id"], "measure_id"),
            )
        )
    return CompatibilityProfile(
        _text(payload["profile_id"], "profile_id"),
        _tool(payload["solver"], "solver"),
        _tool(payload["reader"], "reader"),
        capabilities,
        mappings,
        _evidence_sequence(payload["evidence"], "evidence"),
    )


def _setting(value: object, field: str) -> ExecutionSetting:
    payload = _mapping(value, {"schema_version", "name", "value"}, field)
    _schema(payload, field)
    raw_value = payload["value"]
    if not isinstance(raw_value, (str, int, float, bool)):
        _fail(f"{field}.value", "must be str, int, float, or bool")
    return ExecutionSetting(_text(payload["name"], "name"), raw_value)


def _process(value: object, field: str) -> ProcessIdentity:
    payload = _mapping(
        value,
        {
            "schema_version",
            "executable",
            "executable_digest",
            "argv",
            "cwd",
            "thread_count",
            "start_marker",
        },
        field,
    )
    _schema(payload, field)
    return ProcessIdentity(
        _text(payload["executable"], "executable"),
        _digest(payload["executable_digest"], "executable_digest"),
        tuple(
            _text(item, "argv[]") for item in _sequence(payload["argv"], "argv", allow_empty=False)
        ),
        _text(payload["cwd"], "cwd"),
        _integer(payload["thread_count"], "thread_count", 1),
        _text(payload["start_marker"], "start_marker"),
    )


def _bundle(value: object, field: str) -> ExecutionBundle:
    payload = _mapping(
        value,
        {
            "schema_version",
            "bundle_id",
            "case_id",
            "revision_id",
            "spec_digest",
            "mesh_digest",
            "profile_id",
            "tool",
            "files",
            "argv",
            "cwd",
            "thread_count",
            "settings",
            "bundle_digest",
        },
        field,
    )
    _schema(payload, field)
    result = ExecutionBundle(
        _text(payload["bundle_id"], "bundle_id"),
        _text(payload["case_id"], "case_id"),
        _text(payload["revision_id"], "revision_id"),
        _digest(payload["spec_digest"], "spec_digest"),
        _digest(payload["mesh_digest"], "mesh_digest"),
        _text(payload["profile_id"], "profile_id"),
        _tool(payload["tool"], "tool"),
        tuple(
            _file_entry(item, f"{field}.files[{index}]")
            for index, item in enumerate(_sequence(payload["files"], "files", allow_empty=False))
        ),
        tuple(
            _text(item, "argv[]") for item in _sequence(payload["argv"], "argv", allow_empty=False)
        ),
        _text(payload["cwd"], "cwd"),
        _integer(payload["thread_count"], "thread_count", 1),
        tuple(
            _setting(item, f"{field}.settings[{index}]")
            for index, item in enumerate(_sequence(payload["settings"], "settings"))
        ),
    )
    if result.bundle_digest != _digest(payload["bundle_digest"], f"{field}.bundle_digest"):
        _fail(field, "bundle_digest does not match content")
    return result


def _attempt(value: object, field: str) -> AttemptRecord:
    payload = _mapping(
        value,
        {
            "schema_version",
            "attempt_id",
            "run_id",
            "case_id",
            "revision_id",
            "owner_generation",
            "bundle_digest",
            "state",
            "process",
            "settings",
        },
        field,
    )
    _schema(payload, field)
    try:
        state = RunState(_text(payload["state"], "state"))
    except ValueError as error:
        raise CodecError(f"{field}.state: unsupported value") from error
    return AttemptRecord(
        _text(payload["attempt_id"], "attempt_id"),
        _text(payload["run_id"], "run_id"),
        _text(payload["case_id"], "case_id"),
        _text(payload["revision_id"], "revision_id"),
        _integer(payload["owner_generation"], "owner_generation", 0),
        _digest(payload["bundle_digest"], "bundle_digest"),
        state,
        None if payload["process"] is None else _process(payload["process"], f"{field}.process"),
        tuple(
            _setting(item, f"{field}.settings[{index}]")
            for index, item in enumerate(_sequence(payload["settings"], "settings"))
        ),
    )


def _result_data_ref(value: object, field: str) -> ResultDataRef:
    payload = _mapping(
        value,
        {
            "schema_version",
            "data_id",
            "content_digest",
            "codec_id",
            "logical_path",
            "bundle_digest",
            "attempt_id",
        },
        field,
    )
    _schema(payload, field)
    return ResultDataRef(
        _text(payload["data_id"], f"{field}.data_id"),
        _digest(payload["content_digest"], f"{field}.content_digest"),
        _text(payload["codec_id"], f"{field}.codec_id"),
        _text(payload["logical_path"], f"{field}.logical_path"),
        None
        if payload["bundle_digest"] is None
        else _digest(payload["bundle_digest"], f"{field}.bundle_digest"),
        None
        if payload["attempt_id"] is None
        else _text(payload["attempt_id"], f"{field}.attempt_id"),
    )


def _observation(value: object, field: str) -> OutputObservation:
    payload = _mapping(
        value,
        {
            "schema_version",
            "output_id",
            "location",
            "value_type",
            "unit",
            "frame",
            "measure_id",
            "state_count",
            "data_ref",
        },
        field,
    )
    _schema(payload, field)
    data_ref = (
        None
        if payload["data_ref"] is None
        else _result_data_ref(payload["data_ref"], f"{field}.data_ref")
    )
    return OutputObservation(
        _text(payload["output_id"], "output_id"),
        _text(payload["location"], "location"),
        _text(payload["value_type"], "value_type"),
        _text(payload["unit"], "unit"),
        _frame(payload["frame"], "frame"),
        _text(payload["measure_id"], "measure_id"),
        _integer(payload["state_count"], "state_count", 0),
        data_ref,
    )


def _read_result(value: object, field: str) -> ReadResult:
    payload = _mapping(
        value, {"schema_version", "status", "reader", "observations", "diagnostics"}, field
    )
    _schema(payload, field)
    try:
        status = ReadStatus(_text(payload["status"], "status"))
    except ValueError as error:
        raise CodecError(f"{field}.status: unsupported value") from error
    return ReadResult(
        status,
        _tool(payload["reader"], "reader"),
        tuple(
            _observation(item, f"{field}.observations[{index}]")
            for index, item in enumerate(_sequence(payload["observations"], "observations"))
        ),
        tuple(
            _text(item, "diagnostics[]")
            for item in _sequence(payload["diagnostics"], "diagnostics")
        ),
    )


def _manifest(value: object, field: str) -> ResultManifest:
    payload = _mapping(
        value,
        {"schema_version", "manifest_id", "attempt_id", "bundle_digest", "files", "read_result"},
        field,
    )
    _schema(payload, field)
    return ResultManifest(
        _text(payload["manifest_id"], "manifest_id"),
        _text(payload["attempt_id"], "attempt_id"),
        _digest(payload["bundle_digest"], "bundle_digest"),
        tuple(
            _file_entry(item, f"{field}.files[{index}]")
            for index, item in enumerate(_sequence(payload["files"], "files", allow_empty=False))
        ),
        _read_result(payload["read_result"], f"{field}.read_result"),
    )


def _quality(value: object, field: str) -> QualityAssessment:
    payload = _mapping(
        value,
        {
            "schema_version",
            "assessment_id",
            "manifest_id",
            "policy_digest",
            "criteria",
            "overall_status",
        },
        field,
    )
    _schema(payload, field)
    criteria: list[CriterionAssessment] = []
    for index, item in enumerate(_sequence(payload["criteria"], "criteria", allow_empty=False)):
        item_payload = _mapping(
            item,
            {"schema_version", "criterion_id", "dimension", "status", "measured", "reason"},
            f"{field}.criteria[{index}]",
        )
        _schema(item_payload, f"{field}.criteria[{index}]")
        try:
            status = AssessmentStatus(_text(item_payload["status"], "status"))
        except ValueError as error:
            raise CodecError(f"{field}.criteria[{index}].status: unsupported value") from error
        measured: list[MeasuredValue] = []
        for measured_index, raw in enumerate(_sequence(item_payload["measured"], "measured")):
            measured_payload = _mapping(
                raw, {"schema_version", "metric_id", "value", "unit"}, f"measured[{measured_index}]"
            )
            _schema(measured_payload, f"measured[{measured_index}]")
            measured.append(
                MeasuredValue(
                    _text(measured_payload["metric_id"], "metric_id"),
                    _number(measured_payload["value"], "value"),
                    _text(measured_payload["unit"], "unit"),
                )
            )
        criteria.append(
            CriterionAssessment(
                _text(item_payload["criterion_id"], "criterion_id"),
                _text(item_payload["dimension"], "dimension"),
                status,
                measured,
                _text(item_payload["reason"], "reason"),
            )
        )
    try:
        overall = AssessmentStatus(_text(payload["overall_status"], "overall_status"))
    except ValueError as error:
        raise CodecError(f"{field}.overall_status: unsupported value") from error
    return QualityAssessment(
        _text(payload["assessment_id"], "assessment_id"),
        _text(payload["manifest_id"], "manifest_id"),
        _digest(payload["policy_digest"], "policy_digest"),
        criteria,
        overall,
    )


def _preview_request(value: object, field: str) -> PreviewRequest:
    payload = _mapping(
        value, {"schema_version", "preview_id", "manifest_id", "state_ids", "variables"}, field
    )
    _schema(payload, field)
    return PreviewRequest(
        _text(payload["preview_id"], "preview_id"),
        _text(payload["manifest_id"], "manifest_id"),
        tuple(
            _integer(item, "state_id", 0)
            for item in _sequence(payload["state_ids"], "state_ids", allow_empty=False)
        ),
        tuple(
            _text(item, "variable")
            for item in _sequence(payload["variables"], "variables", allow_empty=False)
        ),
    )


def _preview(value: object, field: str) -> PreviewReceipt:
    payload = _mapping(
        value,
        {
            "schema_version",
            "receipt_id",
            "manifest_id",
            "xplt_digest",
            "studio",
            "status",
            "requested_state_ids",
            "requested_variables",
            "observed_state_ids",
            "observed_variables",
            "confirmation_evidence",
        },
        field,
    )
    _schema(payload, field)
    try:
        status = PreviewStatus(_text(payload["status"], "status"))
    except ValueError as error:
        raise CodecError(f"{field}.status: unsupported value") from error
    return PreviewReceipt(
        _text(payload["receipt_id"], "receipt_id"),
        _text(payload["manifest_id"], "manifest_id"),
        _digest(payload["xplt_digest"], "xplt_digest"),
        _tool(payload["studio"], "studio"),
        status,
        tuple(
            _integer(item, "requested_state_id", 0)
            for item in _sequence(payload["requested_state_ids"], "requested_state_ids")
        ),
        tuple(
            _text(item, "requested_variable")
            for item in _sequence(payload["requested_variables"], "requested_variables")
        ),
        tuple(
            _integer(item, "observed_state_id", 0)
            for item in _sequence(payload["observed_state_ids"], "observed_state_ids")
        ),
        tuple(
            _text(item, "observed_variable")
            for item in _sequence(payload["observed_variables"], "observed_variables")
        ),
        _evidence_sequence(payload["confirmation_evidence"], "confirmation_evidence"),
    )


def _comparison(value: object, field: str) -> ComparisonSpec:
    payload = _mapping(
        value,
        {
            "schema_version",
            "comparison_id",
            "baseline_manifest_id",
            "candidate_manifest_id",
            "intended_changes",
            "fixed_conditions",
            "axes",
        },
        field,
    )
    _schema(payload, field)
    axes: list[ComparisonAxis] = []
    for index, raw in enumerate(_sequence(payload["axes"], "axes", allow_empty=False)):
        item = _mapping(
            raw,
            {
                "schema_version",
                "axis_id",
                "unit",
                "roi_id",
                "measure_id",
                "aggregation_id",
                "interval",
                "interpolation",
            },
            f"axes[{index}]",
        )
        _schema(item, f"axes[{index}]")
        interval_payload = _mapping(
            item["interval"],
            {"schema_version", "unit", "lower", "upper"},
            f"axes[{index}].interval",
        )
        _schema(interval_payload, f"axes[{index}].interval")
        axes.append(
            ComparisonAxis(
                _text(item["axis_id"], "axis_id"),
                _text(item["unit"], "unit"),
                _text(item["roi_id"], "roi_id"),
                _text(item["measure_id"], "measure_id"),
                _text(item["aggregation_id"], "aggregation_id"),
                ComparisonInterval(
                    _text(interval_payload["unit"], "interval.unit"),
                    _number(interval_payload["lower"], "interval.lower"),
                    _number(interval_payload["upper"], "interval.upper"),
                ),
                _text(item["interpolation"], "interpolation"),
            )
        )
    return ComparisonSpec(
        _text(payload["comparison_id"], "comparison_id"),
        _text(payload["baseline_manifest_id"], "baseline_manifest_id"),
        _text(payload["candidate_manifest_id"], "candidate_manifest_id"),
        tuple(
            _text(item, "intended_change")
            for item in _sequence(
                payload["intended_changes"], "intended_changes", allow_empty=False
            )
        ),
        tuple(
            _text(item, "fixed_condition")
            for item in _sequence(
                payload["fixed_conditions"], "fixed_conditions", allow_empty=False
            )
        ),
        axes,
    )


def _question(value: object, field: str) -> Any:
    from .questions import IssuedQuestion

    payload = _mapping(
        value,
        {
            "schema_version",
            "question_id",
            "case_id",
            "draft_id",
            "generation",
            "target_fields",
            "question_time_evidence",
        },
        field,
    )
    _schema(payload, field)
    return IssuedQuestion(
        _text(payload["question_id"], "question_id"),
        _text(payload["case_id"], "case_id"),
        _text(payload["draft_id"], "draft_id"),
        _integer(payload["generation"], "generation", 0),
        tuple(
            _text(item, "target_field")
            for item in _sequence(payload["target_fields"], "target_fields", allow_empty=False)
        ),
        _evidence_sequence(
            payload["question_time_evidence"], "question_time_evidence", allow_empty=False
        ),
    )


def _patch(value: object, field: str) -> CasePatch:
    payload = _mapping(
        value,
        {"schema_version", "parent_revision_id", "parent_spec_digest", "edits", "evidence"},
        field,
    )
    _schema(payload, field)
    edits: list[CasePatchEdit] = []
    for index, raw in enumerate(_sequence(payload["edits"], "edits", allow_empty=False)):
        item = _mapping(raw, {"schema_version", "field", "present", "value"}, f"edits[{index}]")
        _schema(item, f"edits[{index}]")
        # Patch values deliberately use the same explicit top-level field decoders
        # as PartialCaseSpec; absent edits carry null and no decoder is called.
        field_name = _text(item["field"], "field")
        present = item["present"]
        if type(present) is not bool:
            _fail(f"edits[{index}].present", "must be a boolean")
        decoder = {
            "geometry": _geometry,
            "material": _material,
            "support": _support,
            "rigid_tool": _rigid_tool,
            "motion": _motion,
            "contact": _contact,
            "mesh_policy": _mesh_policy,
            "solver_policy": _solver_policy,
            "outputs": _output_policy,
            "quality_policy": _quality_policy,
            "budget": _budget,
        }.get(field_name)
        if decoder is None:
            _fail(f"edits[{index}].field", "unsupported patch field")
        if not present:
            if item["value"] is not None:
                _fail(f"edits[{index}].value", "absent edits must carry value=None")
            item_value = None
        else:
            item_value = decoder(item["value"], f"edits[{index}].value")
        edits.append(CasePatchEdit(field_name, item_value, present))
    return CasePatch(
        _text(payload["parent_revision_id"], "parent_revision_id"),
        _digest(payload["parent_spec_digest"], "parent_spec_digest"),
        edits,
        _evidence_sequence(payload["evidence"], "evidence", allow_empty=False),
    )


def _operation_status(value: object, field: str) -> OperationStatus:
    payload = _mapping(
        value,
        {
            "schema_version",
            "status",
            "case_id",
            "revision_id",
            "run_id",
            "diagnostics",
            "next_actions",
            "run_status",
            "quality_status",
            "preview_status",
            "task_status",
        },
        field,
    )
    _schema(payload, field)
    diagnostics: list[ServiceDiagnostic] = []
    for index, raw in enumerate(_sequence(payload["diagnostics"], "diagnostics")):
        item = _mapping(
            raw,
            {"schema_version", "code", "message", "field", "retryable"},
            f"diagnostics[{index}]",
        )
        _schema(item, f"diagnostics[{index}]")
        try:
            code = ServiceErrorCategory(_text(item["code"], "code"))
        except ValueError as error:
            raise CodecError(f"diagnostics[{index}].code: unsupported value") from error
        if type(item["retryable"]) is not bool:
            _fail(f"diagnostics[{index}].retryable", "must be a boolean")
        diagnostics.append(
            ServiceDiagnostic(
                code,
                _text(item["message"], "message"),
                _optional_text(item["field"], "field"),
                item["retryable"],
            )
        )
    run_status = (
        None
        if payload["run_status"] is None
        else RunState(_text(payload["run_status"], "run_status"))
    )
    preview_status = (
        None
        if payload["preview_status"] is None
        else PreviewStatus(_text(payload["preview_status"], "preview_status"))
    )
    task_status = (
        None
        if payload["task_status"] is None
        else TaskStatus(_text(payload["task_status"], "task_status"))
    )
    return OperationStatus(
        _text(payload["status"], "status"),
        _optional_text(payload["case_id"], "case_id"),
        _optional_text(payload["revision_id"], "revision_id"),
        _optional_text(payload["run_id"], "run_id"),
        diagnostics,
        tuple(
            _text(item, "next_action")
            for item in _sequence(payload["next_actions"], "next_actions")
        ),
        run_status,
        _optional_text(payload["quality_status"], "quality_status"),
        preview_status,
        task_status,
    )


def _inspection_request(value: object, field: str) -> GeometryInspectionRequest:
    payload = _mapping(value, {"schema_version", "source_asset", "requested_body_ids"}, field)
    _schema(payload, field)
    return GeometryInspectionRequest(
        _source_asset(payload["source_asset"], "source_asset"),
        tuple(
            _text(item, "requested_body_id")
            for item in _sequence(payload["requested_body_ids"], "requested_body_ids")
        ),
    )


def _selection_request(value: object, field: str) -> GeometrySelectionRequest:
    payload = _mapping(value, {"schema_version", "source_asset", "selection"}, field)
    _schema(payload, field)
    return GeometrySelectionRequest(
        _source_asset(payload["source_asset"], f"{field}.source_asset"),
        _selection(payload["selection"], f"{field}.selection"),
    )


def _inspection(value: object, field: str) -> GeometryInspection:
    payload = _mapping(
        value,
        {
            "schema_version",
            "source_asset",
            "inspection_digest",
            "declared_unit",
            "body_ids",
            "closed_solid_body_ids",
            "body_facts",
        },
        field,
    )
    _schema(payload, field)
    body_facts: list[GeometryBodyFact] = []
    for index, raw in enumerate(_sequence(payload["body_facts"], "body_facts")):
        fact = _mapping(
            raw,
            {"schema_version", "body_id", "face_count", "volume_si"},
            f"{field}.body_facts[{index}]",
        )
        _schema(fact, f"{field}.body_facts[{index}]")
        body_facts.append(
            GeometryBodyFact(
                _text(fact["body_id"], "body_id"),
                _integer(fact["face_count"], "face_count", 1),
                _number(fact["volume_si"], "volume_si"),
            )
        )
    return GeometryInspection(
        _source_asset(payload["source_asset"], "source_asset"),
        _digest(payload["inspection_digest"], "inspection_digest"),
        _text(payload["declared_unit"], "declared_unit"),
        tuple(
            _text(item, "body_id")
            for item in _sequence(payload["body_ids"], "body_ids", allow_empty=False)
        ),
        tuple(
            _text(item, "closed_body_id")
            for item in _sequence(payload["closed_solid_body_ids"], "closed_solid_body_ids")
        ),
        tuple(body_facts),
    )


_DECODERS: dict[type[object], Any] = {
    CaseDraft: _case_draft,
    CaseRevision: _case_revision,
    CaseSpec: _case_spec,
    PartialCaseSpec: _partial_case_spec,
    GeometryIntent: _geometry,
    MeshArtifact: _mesh_artifact,
    GeometryInspectionRequest: _inspection_request,
    GeometrySelectionRequest: _selection_request,
    GeometryInspection: _inspection,
    SourceAssetRef: _source_asset,
    FileEntry: _file_entry,
    CompatibilityProfile: _compatibility,
    ExecutionBundle: _bundle,
    AttemptRecord: _attempt,
    ResultManifest: _manifest,
    QualityAssessment: _quality,
    PreviewRequest: _preview_request,
    PreviewReceipt: _preview,
    ComparisonSpec: _comparison,
    CasePatch: _patch,
    IssuedQuestion: _question,
    OperationStatus: _operation_status,
}


def _duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise CodecError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _parse(data: bytes | str) -> object:
    if isinstance(data, bytes):
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise CodecError("input is not UTF-8") from error
    elif not isinstance(data, str):
        raise CodecError("encoded record must be bytes or str")

    def reject_constant(value: str) -> object:
        raise CodecError(f"non-finite JSON constant is not allowed: {value}")

    try:
        parsed = json.loads(
            data, object_pairs_hook=_duplicate_pairs, parse_constant=reject_constant
        )
    except CodecError:
        raise
    except (TypeError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise CodecError(f"invalid JSON: {error}") from error
    if not isinstance(parsed, dict):
        raise CodecError("record root must be an object")
    return parsed


def _record_type[T](record_type: type[T] | str) -> tuple[type[T], Any]:
    if isinstance(record_type, str):
        for candidate, decoder in _DECODERS.items():
            if candidate.__name__ == record_type:
                return cast(type[T], candidate), decoder
        raise CodecError(f"unsupported record type: {record_type!r}")
    decoder = _DECODERS.get(cast(type[object], record_type))
    if decoder is None:
        raise CodecError(f"unsupported record type: {record_type!r}")
    return record_type, decoder


def encode_record(value: object) -> bytes:
    """Encode one explicitly registered record through its public projection."""

    record_type, _ = _record_type(type(value))
    to_bytes = getattr(value, "to_bytes", None)
    if not callable(to_bytes):
        raise CodecError(f"registered record {record_type.__name__} has no to_bytes()")
    try:
        encoded = to_bytes()
        if not isinstance(encoded, bytes):
            raise CodecError("record to_bytes() must return bytes")
        _parse(encoded)
        return encoded
    except CodecError:
        raise
    except (TypeError, UnicodeError, ValueError, OverflowError) as error:
        raise CodecError(f"cannot encode {record_type.__name__}: {error}") from error


def decode_record[T](data: bytes | str, record_type: type[T] | str) -> T:
    """Decode only through the explicit registry and validated constructors."""

    resolved_type, decoder = _record_type(record_type)
    parsed = _parse(data)
    try:
        result = decoder(parsed, resolved_type.__name__)
    except CodecError:
        raise
    except (TypeError, UnicodeError, ValueError, OverflowError) as error:
        raise CodecError(f"invalid {resolved_type.__name__}: {error}") from error
    if not isinstance(result, resolved_type):
        raise CodecError(f"decoder returned the wrong type for {resolved_type.__name__}")
    return result


__all__ = ["SCHEMA_VERSION", "CodecError", "decode_record", "encode_record"]
