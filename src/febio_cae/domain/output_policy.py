"""Immutable output requests and explicit saved-state evaluation intent."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import Literal, cast

from .canonical import canonical_bytes
from .evidence import EvidenceRef
from .mesh_policy import NumericalProfileRef
from .selection import FaceSetRule, SelectionRef
from .spatial import FrameId
from .units import Dimension, Quantity, unit_definition

SCHEMA_VERSION = "1"
_TIME = Dimension(time=1)
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LOCATIONS = frozenset({"node", "element", "integration_point", "face", "surface", "rigid_body"})
type OutputLocation = Literal[
    "node", "element", "integration_point", "face", "surface", "rigid_body"
]


class OutputPolicyValidationError(ValueError):
    """Raised when an output request or evaluation policy is structurally invalid."""


def _require_identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise OutputPolicyValidationError(
            f"{field} must be a non-empty ASCII identifier starting with a letter or underscore"
        )
    return value


def _require_location(value: object) -> OutputLocation:
    if not isinstance(value, str) or value not in _LOCATIONS:
        raise OutputPolicyValidationError(
            "location must be one of node, element, integration_point, face, surface, rigid_body"
        )
    return cast(OutputLocation, value)


def _require_selection(value: object, field: str) -> SelectionRef:
    if not isinstance(value, SelectionRef):
        raise OutputPolicyValidationError(f"{field} must be a SelectionRef")
    return value


def _require_frame(value: object, field: str) -> FrameId:
    if not isinstance(value, FrameId):
        raise OutputPolicyValidationError(f"{field} must be a FrameId")
    return value


def _require_evidence(value: object, field: str, target_field: str) -> EvidenceRef:
    if not isinstance(value, EvidenceRef):
        raise OutputPolicyValidationError(f"{field} must be an EvidenceRef")
    if value.target_field != target_field:
        raise OutputPolicyValidationError(
            f"{field} must target exactly {target_field!r}, got {value.target_field!r}"
        )
    return value


def _require_display_unit(value: object) -> str:
    if not isinstance(value, str):
        raise OutputPolicyValidationError("display_unit must be a known unit symbol")
    try:
        unit_definition(value)
    except ValueError as error:
        raise OutputPolicyValidationError("display_unit must be a known unit symbol") from error
    return value


def _copy_sequence(value: object, field: str) -> tuple[object, ...]:
    if isinstance(value, (str, bytes, bytearray, Mapping, AbstractSet)) or not isinstance(
        value, Sequence
    ):
        raise OutputPolicyValidationError(f"{field} must be a sequence")
    return tuple(value)


def _time_si(value: object, field: str) -> float:
    if not isinstance(value, Quantity) or value.dimension != _TIME:
        raise OutputPolicyValidationError(f"{field} must be a time Quantity")
    try:
        si_value = value.to_si().value
    except (TypeError, ValueError) as error:
        raise OutputPolicyValidationError(f"{field} is not SI-representable") from error
    if si_value < 0:
        raise OutputPolicyValidationError(f"{field} must be nonnegative")
    return float(si_value)


def _time_sequence(value: object, field: str) -> tuple[Quantity, ...]:
    raw_values = _copy_sequence(value, field)
    if not raw_values:
        raise OutputPolicyValidationError(f"{field} must be nonempty")
    result: list[Quantity] = []
    previous: float | None = None
    for index, item in enumerate(raw_values):
        current = _time_si(item, f"{field}[{index}]")
        if previous is not None and current <= previous:
            raise OutputPolicyValidationError(
                f"{field} must be strictly increasing with no duplicates"
            )
        result.append(cast(Quantity, item))
        previous = current
    return tuple(result)


def _time_dict(value: Quantity, field: str) -> dict[str, float | str]:
    return {"value": _time_si(value, field), "unit": "s"}


def _quantity_time_list(values: Sequence[Quantity], field: str) -> list[dict[str, float | str]]:
    return [_time_dict(value, f"{field}[{index}]") for index, value in enumerate(values)]


def _selection_unordered_paths(
    prefix: tuple[str, ...], selection: SelectionRef
) -> list[tuple[str, ...]]:
    paths: list[tuple[str, ...]] = []
    if isinstance(selection.rule, FaceSetRule):
        paths.append(prefix + ("rule", "face_ids"))
    if selection.resolution is not None:
        paths.append(prefix + ("resolution", "faces"))
    return paths


@dataclass(frozen=True, slots=True)
class OutputRequest:
    """One explicit semantic result quantity request."""

    request_id: str
    quantity_id: str
    measure_id: str
    component_id: str
    location: OutputLocation
    selection: SelectionRef
    frame: FrameId
    display_unit: str
    evidence: EvidenceRef

    def __post_init__(self) -> None:
        request_id = _require_identifier(self.request_id, "request_id")
        quantity_id = _require_identifier(self.quantity_id, "quantity_id")
        measure_id = _require_identifier(self.measure_id, "measure_id")
        component_id = _require_identifier(self.component_id, "component_id")
        location = _require_location(self.location)
        selection = _require_selection(self.selection, "selection")
        frame = _require_frame(self.frame, "frame")
        display_unit = _require_display_unit(self.display_unit)
        evidence = _require_evidence(
            self.evidence,
            "evidence",
            f"outputs.requests.{request_id}",
        )
        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "quantity_id", quantity_id)
        object.__setattr__(self, "measure_id", measure_id)
        object.__setattr__(self, "component_id", component_id)
        object.__setattr__(self, "location", location)
        object.__setattr__(self, "selection", selection)
        object.__setattr__(self, "frame", frame)
        object.__setattr__(self, "display_unit", display_unit)
        object.__setattr__(self, "evidence", evidence)
        try:
            self.to_bytes()
        except (TypeError, ValueError, UnicodeError) as error:
            raise OutputPolicyValidationError(
                "output request is not canonically serializable"
            ) from error

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "request_id": self.request_id,
            "quantity_id": self.quantity_id,
            "measure_id": self.measure_id,
            "component_id": self.component_id,
            "location": self.location,
            "selection": self.selection.to_dict(),
            "frame": self.frame.value,
            "display_unit": self.display_unit,
            "evidence": self.evidence.to_dict(),
        }

    def to_bytes(self) -> bytes:
        return canonical_bytes(
            self.to_dict(),
            unordered_paths=_selection_unordered_paths(("selection",), self.selection),
        )


@dataclass(frozen=True, slots=True)
class EvaluationRequest:
    """One explicit evaluation over saved result states and a selection scope."""

    evaluation_id: str
    output_request_id: str
    aggregation_id: str
    selection: SelectionRef
    state_times: Sequence[Quantity]
    evidence: EvidenceRef

    def __post_init__(self) -> None:
        evaluation_id = _require_identifier(self.evaluation_id, "evaluation_id")
        output_request_id = _require_identifier(self.output_request_id, "output_request_id")
        aggregation_id = _require_identifier(self.aggregation_id, "aggregation_id")
        selection = _require_selection(self.selection, "selection")
        state_times = _time_sequence(self.state_times, "state_times")
        evidence = _require_evidence(
            self.evidence,
            "evidence",
            f"outputs.evaluations.{evaluation_id}",
        )
        object.__setattr__(self, "evaluation_id", evaluation_id)
        object.__setattr__(self, "output_request_id", output_request_id)
        object.__setattr__(self, "aggregation_id", aggregation_id)
        object.__setattr__(self, "selection", selection)
        object.__setattr__(self, "state_times", state_times)
        object.__setattr__(self, "evidence", evidence)
        try:
            self.to_bytes()
        except (TypeError, ValueError, UnicodeError) as error:
            raise OutputPolicyValidationError(
                "evaluation request is not canonically serializable"
            ) from error

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "evaluation_id": self.evaluation_id,
            "output_request_id": self.output_request_id,
            "aggregation_id": self.aggregation_id,
            "selection": self.selection.to_dict(),
            "state_times": _quantity_time_list(self.state_times, "state_times"),
            "evidence": self.evidence.to_dict(),
        }

    def to_bytes(self) -> bytes:
        return canonical_bytes(
            self.to_dict(),
            unordered_paths=_selection_unordered_paths(("selection",), self.selection),
        )


@dataclass(frozen=True, slots=True)
class OutputPolicy:
    """Immutable output requests, saved states, and evaluation intent."""

    profile: NumericalProfileRef
    requests: Sequence[OutputRequest]
    saved_times: Sequence[Quantity]
    evaluations: Sequence[EvaluationRequest]

    def __post_init__(self) -> None:
        if not isinstance(self.profile, NumericalProfileRef):
            raise OutputPolicyValidationError("profile must be a NumericalProfileRef")
        if self.profile.purpose != "outputs":
            raise OutputPolicyValidationError("profile must have outputs purpose")

        raw_requests = _copy_sequence(self.requests, "requests")
        if not raw_requests:
            raise OutputPolicyValidationError("requests must be nonempty")
        if any(not isinstance(item, OutputRequest) for item in raw_requests):
            raise OutputPolicyValidationError("requests contains an invalid OutputRequest")
        requests = cast(tuple[OutputRequest, ...], tuple(raw_requests))
        request_ids = [item.request_id for item in requests]
        if len(set(request_ids)) != len(request_ids):
            duplicate = min(
                identifier for identifier in set(request_ids) if request_ids.count(identifier) > 1
            )
            raise OutputPolicyValidationError(f"duplicate request_id: {duplicate}")
        requests = tuple(sorted(requests, key=lambda item: item.request_id))

        saved_times = _time_sequence(self.saved_times, "saved_times")
        saved_time_values = {
            _time_si(value, f"saved_times[{index}]") for index, value in enumerate(saved_times)
        }

        raw_evaluations = _copy_sequence(self.evaluations, "evaluations")
        if any(not isinstance(item, EvaluationRequest) for item in raw_evaluations):
            raise OutputPolicyValidationError("evaluations contains an invalid EvaluationRequest")
        evaluations = cast(tuple[EvaluationRequest, ...], tuple(raw_evaluations))
        evaluation_ids = [item.evaluation_id for item in evaluations]
        if len(set(evaluation_ids)) != len(evaluation_ids):
            duplicate = min(
                identifier
                for identifier in set(evaluation_ids)
                if evaluation_ids.count(identifier) > 1
            )
            raise OutputPolicyValidationError(f"duplicate evaluation_id: {duplicate}")
        evaluations = tuple(sorted(evaluations, key=lambda item: item.evaluation_id))

        requests_by_id = {item.request_id: item for item in requests}
        for evaluation in evaluations:
            referenced = requests_by_id.get(evaluation.output_request_id)
            if referenced is None:
                raise OutputPolicyValidationError(
                    f"evaluation {evaluation.evaluation_id} references missing "
                    f"output_request_id {evaluation.output_request_id!r}"
                )
            if (
                evaluation.selection.geometry_digest != referenced.selection.geometry_digest
                or evaluation.selection.body_id != referenced.selection.body_id
            ):
                raise OutputPolicyValidationError(
                    f"evaluation {evaluation.evaluation_id} selection must match the "
                    "referenced output geometry_digest and body_id"
                )
            for index, state_time in enumerate(evaluation.state_times):
                current = _time_si(
                    state_time, f"evaluations[{evaluation.evaluation_id}].state_times[{index}]"
                )
                if current not in saved_time_values:
                    raise OutputPolicyValidationError(
                        f"evaluation {evaluation.evaluation_id} state time is not in saved_times"
                    )

        object.__setattr__(self, "requests", requests)
        object.__setattr__(self, "saved_times", saved_times)
        object.__setattr__(self, "evaluations", evaluations)
        try:
            self.to_bytes()
        except (TypeError, ValueError, UnicodeError) as error:
            raise OutputPolicyValidationError(
                "output policy is not canonically serializable"
            ) from error

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "profile": self.profile.to_dict(),
            "requests": [request.to_dict() for request in self.requests],
            "saved_times": _quantity_time_list(self.saved_times, "saved_times"),
            "evaluations": [evaluation.to_dict() for evaluation in self.evaluations],
        }

    def to_bytes(self) -> bytes:
        payload = self.to_dict()
        unordered_paths: list[tuple[str, ...]] = []
        for index, request in enumerate(self.requests):
            unordered_paths.extend(
                _selection_unordered_paths(("requests", str(index), "selection"), request.selection)
            )
        for index, evaluation in enumerate(self.evaluations):
            unordered_paths.extend(
                _selection_unordered_paths(
                    ("evaluations", str(index), "selection"), evaluation.selection
                )
            )
        return canonical_bytes(payload, unordered_paths=unordered_paths)


__all__ = [
    "SCHEMA_VERSION",
    "EvaluationRequest",
    "OutputPolicy",
    "OutputPolicyValidationError",
    "OutputRequest",
]
