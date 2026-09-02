"""Fail-closed FEBio Studio handoff contracts for STEP-to-FEB work.

The harness never drives a GUI from this module.  It issues an exact request
for the one operation that currently has no headless adapter and accepts the
result only after the external Studio runtime, official Computer Use record,
screenshots, action log, and generated FEB all bind to that request.
"""

from __future__ import annotations

import hashlib
import json
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast
from weakref import WeakKeyDictionary

from ..contracts import IntentState
from ..evidence import EvidenceIntegrityError, IntentSnapshotAuthority
from ..model._immutability import freeze_json
from ..model.feb import inspect_feb_xml
from ..model.preflight import run_preflight
from ..model.step import STEPInspection
from ..model.step_plan import plan_authoritative_step_meshing
from .types import SolverConfigurationError, _reject_alias

_OPERATION = "STEP_IMPORT_MESH_FEB"
_REQUEST_SCHEMA = "studio-fallback-request-v1"
_EVIDENCE_SCHEMA = "studio-fallback-evidence-v1"
_RECEIPT_SCHEMA = "studio-fallback-receipt-v1"
_EXPECTED_OUTPUT = "studio-output.feb"
_REQUIRED_EVIDENCE = (
    "before.png",
    "after.png",
    "actions.json",
    "studio-runtime-identity",
)
_DIGEST_CHARS = frozenset("0123456789abcdef")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_EVIDENCE_FIELDS = frozenset(
    {
        "schema",
        "request_id",
        "operation",
        "official_computer_use",
        "runtime",
        "input_sha256",
        "output_sha256",
        "before_sha256",
        "after_sha256",
        "actions",
    }
)
_RUNTIME_FIELDS = frozenset({"product", "version", "executable_path", "executable_sha256"})
_ACTION_FIELDS = frozenset({"sequence", "action"})


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_sha256(value: object) -> str:
    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise EvidenceIntegrityError("Studio fallback projection is not canonical JSON") from error
    return _sha256(payload)


def _digest(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in _DIGEST_CHARS for character in value)
    ):
        raise EvidenceIntegrityError(f"Studio fallback {label} digest is invalid")
    return value


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(item) for item in value]
    return value


def _segment(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or Path(value).name != value
        or "/" in value
        or "\\" in value
    ):
        raise EvidenceIntegrityError(f"Studio fallback {label} is invalid")
    return value


def _plan_projection(plan: object) -> dict[str, object]:
    try:
        payload = cast(Any, plan).to_dict()
    except Exception as error:
        raise EvidenceIntegrityError("Studio fallback STEP plan is invalid") from error
    if payload.get("status") != "READY" or payload.get("questions") != []:
        raise EvidenceIntegrityError("Studio fallback requires a READY STEP plan")
    projection = {
        "element_family": payload.get("element_family"),
        "length_unit": payload.get("length_unit"),
        "target_size": payload.get("target_size"),
        "quality_criteria": payload.get("quality_criteria"),
    }
    frozen = freeze_json(projection)
    if not isinstance(frozen, Mapping):  # pragma: no cover - fixed projection
        raise EvidenceIntegrityError("Studio fallback STEP plan is invalid")
    thawed = _thaw(frozen)
    if not isinstance(thawed, dict):  # pragma: no cover - fixed projection
        raise EvidenceIntegrityError("Studio fallback STEP plan is invalid")
    return cast(dict[str, object], thawed)


@dataclass(frozen=True, slots=True, eq=False, weakref_slot=True)
class StudioFallbackHandoff:
    request_id: str
    case_id: str
    attempt_id: str
    intent_sha256: str
    step_sha256: str
    plan: Mapping[str, object]

    def __post_init__(self) -> None:
        _digest(self.request_id, "request")
        _segment(self.case_id, "case id")
        _segment(self.attempt_id, "attempt id")
        _digest(self.intent_sha256, "intent")
        _digest(self.step_sha256, "STEP")
        frozen = freeze_json(self.plan)
        if not isinstance(frozen, Mapping):
            raise TypeError("plan must be a mapping")
        object.__setattr__(self, "plan", MappingProxyType(dict(frozen)))

    @property
    def operation(self) -> str:
        return _OPERATION

    def to_dict(self) -> dict[str, object]:
        _require_handoff(self)
        return _handoff_projection(self)


@dataclass(frozen=True, slots=True, eq=False, weakref_slot=True)
class StudioFallbackReceipt:
    request_id: str
    case_id: str
    attempt_id: str
    intent_sha256: str
    output_sha256: str
    before_sha256: str
    after_sha256: str
    runtime_executable_sha256: str

    @property
    def operation(self) -> str:
        return _OPERATION

    @property
    def headless_ready(self) -> bool:
        _require_receipt(self)
        return True

    def to_dict(self) -> dict[str, object]:
        _require_receipt(self)
        return _receipt_projection(self)


@dataclass(frozen=True, slots=True)
class _HandoffBinding:
    snapshot: IntentSnapshotAuthority
    projection: object


@dataclass(frozen=True, slots=True)
class _ReceiptBinding:
    handoff: StudioFallbackHandoff
    projection: object


_HANDOFFS: WeakKeyDictionary[StudioFallbackHandoff, _HandoffBinding] = WeakKeyDictionary()
_RECEIPTS: WeakKeyDictionary[StudioFallbackReceipt, _ReceiptBinding] = WeakKeyDictionary()


def _handoff_projection(handoff: StudioFallbackHandoff) -> dict[str, object]:
    return {
        "schema": _REQUEST_SCHEMA,
        "request_id": handoff.request_id,
        "case_id": handoff.case_id,
        "attempt_id": handoff.attempt_id,
        "intent_sha256": handoff.intent_sha256,
        "operation": _OPERATION,
        "step_sha256": handoff.step_sha256,
        "plan": _thaw(handoff.plan),
        "expected_output": _EXPECTED_OUTPUT,
        "required_evidence": list(_REQUIRED_EVIDENCE),
        "status": "PENDING_EXTERNAL_STUDIO",
    }


def _require_handoff(handoff: object) -> _HandoffBinding:
    if type(handoff) is not StudioFallbackHandoff:
        raise EvidenceIntegrityError("Studio fallback handoff is not authority-issued")
    binding = _HANDOFFS.get(handoff)
    if binding is None:
        raise EvidenceIntegrityError("Studio fallback handoff is not authority-issued")
    current = _handoff_projection(handoff)
    if freeze_json(current) != binding.projection:
        raise EvidenceIntegrityError("Studio fallback handoff projection changed")
    if binding.snapshot.intent_sha256 != handoff.intent_sha256:
        raise EvidenceIntegrityError("Studio fallback intent snapshot changed")
    return binding


def _require_receipt(receipt: object) -> _ReceiptBinding:
    if type(receipt) is not StudioFallbackReceipt:
        raise EvidenceIntegrityError("Studio fallback receipt is not authority-issued")
    binding = _RECEIPTS.get(receipt)
    if binding is None:
        raise EvidenceIntegrityError("Studio fallback receipt is not authority-issued")
    _require_handoff(binding.handoff)
    if freeze_json(_receipt_projection(receipt)) != binding.projection:
        raise EvidenceIntegrityError("Studio fallback receipt projection changed")
    return binding


def _receipt_projection(receipt: StudioFallbackReceipt) -> dict[str, object]:
    return {
        "schema": _RECEIPT_SCHEMA,
        "request_id": receipt.request_id,
        "case_id": receipt.case_id,
        "attempt_id": receipt.attempt_id,
        "operation": _OPERATION,
        "intent_sha256": receipt.intent_sha256,
        "output_sha256": receipt.output_sha256,
        "before_sha256": receipt.before_sha256,
        "after_sha256": receipt.after_sha256,
        "runtime_executable_sha256": receipt.runtime_executable_sha256,
        "headless_ready": True,
        "official_computer_use": True,
    }


def issue_step_studio_handoff(
    step: STEPInspection,
    snapshot: IntentSnapshotAuthority,
    *,
    attempt_id: str,
) -> StudioFallbackHandoff:
    """Issue a request for the currently Studio-only STEP import/mesh operation."""

    if not isinstance(step, STEPInspection):
        raise TypeError("step must be a STEPInspection")
    if type(snapshot) is not IntentSnapshotAuthority:
        raise TypeError("snapshot must be an exact IntentSnapshotAuthority")
    if snapshot.intent.state is not IntentState.BOUND:
        raise EvidenceIntegrityError("Studio fallback requires a BOUND intent snapshot")
    plan = plan_authoritative_step_meshing(step, snapshot)
    plan_projection = _plan_projection(plan)
    request_body = {
        "schema": _REQUEST_SCHEMA,
        "case_id": snapshot.case_id,
        "attempt_id": _segment(attempt_id, "attempt id"),
        "intent_sha256": snapshot.intent_sha256,
        "operation": _OPERATION,
        "step_sha256": step.sha256,
        "plan": plan_projection,
        "expected_output": _EXPECTED_OUTPUT,
        "required_evidence": list(_REQUIRED_EVIDENCE),
        "status": "PENDING_EXTERNAL_STUDIO",
    }
    request_id = _canonical_sha256(request_body)
    handoff = StudioFallbackHandoff(
        request_id=request_id,
        case_id=snapshot.case_id,
        attempt_id=cast(str, request_body["attempt_id"]),
        intent_sha256=snapshot.intent_sha256,
        step_sha256=step.sha256,
        plan=plan_projection,
    )
    projection = freeze_json(_handoff_projection(handoff))
    _HANDOFFS[handoff] = _HandoffBinding(snapshot=snapshot, projection=projection)
    _require_handoff(handoff)
    return handoff


def _runtime_digest(runtime: object) -> str:
    if not isinstance(runtime, Mapping) or set(runtime) != _RUNTIME_FIELDS:
        raise EvidenceIntegrityError("Studio fallback runtime evidence is invalid")
    if runtime.get("product") != "FEBio Studio":
        raise EvidenceIntegrityError("Studio fallback runtime is not FEBio Studio")
    version = runtime.get("version")
    if not isinstance(version, str) or not version.strip():
        raise EvidenceIntegrityError("Studio fallback runtime version is invalid")
    executable_value = runtime.get("executable_path")
    if not isinstance(executable_value, str) or not executable_value:
        raise EvidenceIntegrityError("Studio fallback runtime path is invalid")
    executable = Path(executable_value)
    if not executable.is_absolute():
        raise EvidenceIntegrityError("Studio fallback runtime path is not exact")
    expected = _digest(runtime.get("executable_sha256"), "runtime")
    try:
        _reject_alias(executable, "Studio fallback runtime")
        before = executable.stat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise EvidenceIntegrityError("Studio fallback runtime is not an exact regular file")
        actual = hashlib.sha256(executable.read_bytes()).hexdigest()
        after = executable.stat()
    except (OSError, SolverConfigurationError) as error:
        raise EvidenceIntegrityError("Studio fallback runtime could not be verified") from error
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if identity_before != identity_after or actual != expected:
        raise EvidenceIntegrityError("Studio fallback runtime identity changed")
    return actual


def _actions(value: object) -> None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
        raise EvidenceIntegrityError("Studio fallback actions are missing")
    for expected_sequence, item in enumerate(value, start=1):
        if not isinstance(item, Mapping) or set(item) != _ACTION_FIELDS:
            raise EvidenceIntegrityError("Studio fallback actions are invalid")
        if item.get("sequence") != expected_sequence:
            raise EvidenceIntegrityError("Studio fallback actions are not contiguous")
        action = item.get("action")
        if not isinstance(action, str) or not action.strip():
            raise EvidenceIntegrityError("Studio fallback actions are invalid")


def _validate_output_feb(output_feb: bytes, expected_family: object) -> str:
    try:
        inspection = inspect_feb_xml(output_feb, source_name=_EXPECTED_OUTPUT)
        preflight = run_preflight(feb=inspection)
    except Exception as error:
        raise EvidenceIntegrityError("Studio fallback output FEB inspection failed") from error
    if not preflight.ready:
        raise EvidenceIntegrityError("Studio fallback output FEB preflight failed")
    element_nodes = [item for item in inspection.nodes if item.tag.casefold() == "elements"]
    if not element_nodes:
        raise EvidenceIntegrityError("Studio fallback output FEB contains no mesh elements")
    families = {item.attributes.get("type") for item in element_nodes}
    if families != {expected_family}:
        raise EvidenceIntegrityError("Studio fallback output element family differs from the plan")
    if not any(item.tag.casefold() == "elem" for item in inspection.nodes):
        raise EvidenceIntegrityError("Studio fallback output FEB contains no mesh element records")
    return inspection.sha256


def accept_step_studio_output(
    handoff: StudioFallbackHandoff,
    *,
    output_feb: bytes,
    before_png: bytes,
    after_png: bytes,
    action_evidence: Mapping[str, object],
) -> StudioFallbackReceipt:
    """Validate external Studio evidence and issue a live headless-return receipt."""

    _require_handoff(handoff)
    if not all(type(value) is bytes for value in (output_feb, before_png, after_png)):
        raise TypeError("Studio fallback artifacts must be bytes")
    if not before_png.startswith(_PNG_SIGNATURE) or not after_png.startswith(_PNG_SIGNATURE):
        raise EvidenceIntegrityError("Studio fallback screenshots are not PNG evidence")
    before_sha256 = _sha256(before_png)
    after_sha256 = _sha256(after_png)
    if before_sha256 == after_sha256:
        raise EvidenceIntegrityError("Studio fallback before and after screenshots are identical")
    output_sha256 = _validate_output_feb(output_feb, handoff.plan["element_family"])
    if not isinstance(action_evidence, Mapping) or set(action_evidence) != _EVIDENCE_FIELDS:
        raise EvidenceIntegrityError("Studio fallback action evidence schema is invalid")
    if action_evidence.get("schema") != _EVIDENCE_SCHEMA:
        raise EvidenceIntegrityError("Studio fallback action evidence schema is invalid")
    if action_evidence.get("official_computer_use") is not True:
        raise EvidenceIntegrityError("Studio fallback requires official Computer Use evidence")
    exact_claims = {
        "request_id": handoff.request_id,
        "operation": _OPERATION,
        "input_sha256": handoff.step_sha256,
        "output_sha256": output_sha256,
        "before_sha256": before_sha256,
        "after_sha256": after_sha256,
    }
    for field, expected in exact_claims.items():
        actual = action_evidence.get(field)
        if actual != expected:
            label = "output" if field == "output_sha256" else field.replace("_", " ")
            raise EvidenceIntegrityError(f"Studio fallback {label} evidence differs")
    _actions(action_evidence.get("actions"))
    runtime_sha256 = _runtime_digest(action_evidence.get("runtime"))
    _require_handoff(handoff)
    receipt = StudioFallbackReceipt(
        request_id=handoff.request_id,
        case_id=handoff.case_id,
        attempt_id=handoff.attempt_id,
        intent_sha256=handoff.intent_sha256,
        output_sha256=output_sha256,
        before_sha256=before_sha256,
        after_sha256=after_sha256,
        runtime_executable_sha256=runtime_sha256,
    )
    projection = freeze_json(_receipt_projection(receipt))
    _RECEIPTS[receipt] = _ReceiptBinding(handoff=handoff, projection=projection)
    _require_receipt(receipt)
    return receipt


__all__ = [
    "StudioFallbackHandoff",
    "StudioFallbackReceipt",
    "accept_step_studio_output",
    "issue_step_studio_handoff",
]
