from __future__ import annotations

import hashlib
import math
import os
import re
import stat
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from ..autonomy.policy import (
    IntentState,
    IntentStateAuthority,
    Proposal,
    ProposalAction,
    ProposalAuthority,
    decide_proposal,
)
from ..evidence import EvidenceIntegrityError
from .plan import OriginalModel
from .types import EvidenceProvenance, IntentImpact, normalise_provenance

Scalar = str | int | float | bool
_DTD_OR_ENTITY = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True, slots=True)
class FebPatch:
    target: str
    mode: str
    attribute_name: str | None
    value: Scalar
    reason: str
    provenance: tuple[EvidenceProvenance, ...] = ()
    intent_impact: IntentImpact | str = IntentImpact.INTENT_PRESERVING

    def __post_init__(self) -> None:
        if not isinstance(self.target, str) or not self.target.strip():
            raise ValueError("invalid target")
        if self.mode not in {"TEXT", "ATTRIBUTE"}:
            raise ValueError("invalid mode")
        if self.attribute_name is not None and (
            not isinstance(self.attribute_name, str) or not self.attribute_name.strip()
        ):
            raise ValueError("invalid attribute_name")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("invalid reason")
        object.__setattr__(self, "provenance", normalise_provenance(self.provenance))
        object.__setattr__(self, "intent_impact", IntentImpact(self.intent_impact))


@dataclass(frozen=True, slots=True)
class DerivedFebReceipt:
    original_sha256: str
    derived_sha256: str
    destination: Path
    applied_patch_targets: tuple[str, ...]

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or _DIGEST.fullmatch(value) is None
            for value in (self.original_sha256, self.derived_sha256)
        ):
            raise ValueError("receipt digests must be SHA-256 values")
        destination = Path(self.destination)
        if not destination.is_absolute():
            raise ValueError("receipt destination must be absolute")
        targets = tuple(self.applied_patch_targets)
        if not all(isinstance(item, str) and item for item in targets):
            raise TypeError("invalid applied_patch_targets")
        object.__setattr__(self, "destination", destination)
        object.__setattr__(self, "applied_patch_targets", targets)


def _scalar_text(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str | int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("patch values must be finite scalars")
        return repr(value)
    raise TypeError("patch value must be a finite scalar")


def _parse_feb(payload: bytes) -> ET.Element:
    if _DTD_OR_ENTITY.search(payload):
        raise ValueError("DTD and entity declarations are not allowed")
    try:
        root = ET.fromstring(payload)
    except (ET.ParseError, UnicodeDecodeError, ValueError) as error:
        raise ValueError(f"invalid FEB XML: {error}") from error
    if root.tag != "febio_spec":
        raise ValueError("FEB XML root must be febio_spec")
    return root


def _path_index(root: ET.Element) -> dict[str, ET.Element]:
    paths: dict[str, ET.Element] = {}

    def visit(element: ET.Element, path: str) -> None:
        paths[path] = element
        counts: dict[str, int] = {}
        for child in element:
            if not isinstance(child.tag, str):
                raise ValueError("FEB XML contains a non-element node")
            counts[child.tag] = counts.get(child.tag, 0) + 1
            visit(child, f"{path}/{child.tag}[{counts[child.tag]}]")

    visit(root, "/febio_spec")
    return paths


_PATCH_CHANGE_KEYS = frozenset({"target", "mode", "value", "attribute_name"})


def _proposal_patch_records(proposal: Proposal) -> tuple[Mapping[str, object], ...]:
    """Return the explicit FEB patch records declared by a mesh proposal.

    Policy declarations remain evidence-shaped JSON, so the mesh change may
    describe one patch directly or carry an ordered ``patches`` sequence.  No
    fields outside the FEB patch contract are accepted here; in particular,
    proposal metadata cannot silently become a model edit.
    """

    changes = proposal.changes
    if not isinstance(changes, Mapping) or set(changes) != {"mesh"}:
        raise EvidenceIntegrityError("proposal must declare only mesh patch changes")
    mesh_changes = changes["mesh"]
    if isinstance(mesh_changes, Mapping):
        if set(mesh_changes) == {"patches"}:
            raw_records = mesh_changes["patches"]
        elif {"target", "mode", "value"}.issubset(mesh_changes):
            raw_records = (mesh_changes,)
        else:
            raise EvidenceIntegrityError("proposal mesh changes do not declare FEB patches")
    else:
        raw_records = mesh_changes
    if not isinstance(raw_records, tuple) or not raw_records:
        raise EvidenceIntegrityError("proposal mesh patches must be a non-empty sequence")
    records: list[Mapping[str, object]] = []
    for raw_record in raw_records:
        if not isinstance(raw_record, Mapping):
            raise EvidenceIntegrityError("proposal mesh patches must be mappings")
        if not set(raw_record).issubset(_PATCH_CHANGE_KEYS):
            raise EvidenceIntegrityError("proposal mesh patch has unexpected fields")
        if not {"target", "mode", "value"}.issubset(raw_record):
            raise EvidenceIntegrityError("proposal mesh patch omits a required field")
        mode = raw_record["mode"]
        if mode == "TEXT":
            if "attribute_name" in raw_record:
                raise EvidenceIntegrityError("TEXT proposal patches cannot name an attribute")
        elif mode == "ATTRIBUTE":
            if "attribute_name" not in raw_record:
                raise EvidenceIntegrityError("ATTRIBUTE proposal patches require an attribute name")
        else:
            raise EvidenceIntegrityError("proposal mesh patch mode is invalid")
        records.append(raw_record)
    return tuple(records)


def _scalar_exactly_matches(left: object, right: object) -> bool:
    """Compare patch values without allowing bool/int equality to collapse."""

    return type(left) is type(right) and left == right


def _patch_exactly_matches(patch: FebPatch, declared: Mapping[str, object]) -> bool:
    """Return whether every executable patch field matches its declaration."""

    target = declared.get("target")
    mode = declared.get("mode")
    value = declared.get("value")
    if type(target) is not str or type(mode) is not str:
        return False
    if patch.target != target or patch.mode != mode:
        return False
    if not _scalar_exactly_matches(patch.value, value):
        return False
    if patch.mode == "TEXT":
        return patch.attribute_name is None and "attribute_name" not in declared
    attribute_name = declared.get("attribute_name")
    return (
        isinstance(attribute_name, str)
        and bool(attribute_name.strip())
        and patch.attribute_name == attribute_name
    )


def _require_authorized_patch_set(
    state_authority: IntentStateAuthority,
    proposal: Proposal,
    proposal_authority: ProposalAuthority,
    patches: tuple[FebPatch, ...],
) -> None:
    """Require live policy authorization and an exact executable patch set."""

    if type(state_authority) is not IntentStateAuthority:
        raise TypeError("state_authority must be an exact IntentStateAuthority")
    if type(proposal) is not Proposal:
        raise TypeError("proposal must be an exact Proposal")
    if type(proposal_authority) is not ProposalAuthority:
        raise TypeError("proposal_authority must be an exact ProposalAuthority")
    if state_authority.current is not IntentState.BOUND:
        raise EvidenceIntegrityError("derived FEB writes require a BOUND state authority")
    decision = decide_proposal(state_authority, proposal, proposal_authority)
    if decision.action is not ProposalAction.AUTO_APPLY:
        raise EvidenceIntegrityError("derived FEB write requires an AUTO_APPLY proposal decision")
    declared = _proposal_patch_records(proposal)
    if len(declared) != len(patches) or any(
        not isinstance(record, FebPatch) or not _patch_exactly_matches(record, change)
        for record, change in zip(patches, declared, strict=True)
    ):
        raise EvidenceIntegrityError("derived FEB patches do not exactly match the proposal")


def _apply_patches(root: ET.Element, patches: tuple[FebPatch, ...]) -> tuple[str, ...]:
    paths = _path_index(root)
    applied: list[str] = []
    for patch in patches:
        if not isinstance(patch, FebPatch):
            raise TypeError("patches must contain FebPatch records")
        target = paths.get(patch.target)
        if target is None:
            raise ValueError(f"patch target must match exactly one element: {patch.target}")
        value = _scalar_text(patch.value)
        if patch.mode == "TEXT":
            if patch.attribute_name is not None:
                raise ValueError("TEXT patches cannot name an attribute")
            target.text = value
        else:
            attribute = patch.attribute_name
            if attribute is None or attribute not in target.attrib:
                raise ValueError("ATTRIBUTE patches require an existing named attribute")
            target.set(attribute, value)
        applied.append(patch.target)
    return tuple(applied)


def _is_link_or_reparse(path: Path) -> bool:
    info = os.lstat(path)
    attributes = getattr(info, "st_file_attributes", 0)
    return stat.S_ISLNK(info.st_mode) or bool(
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _normalised(path: Path) -> str:
    return os.path.normcase(os.path.normpath(os.fspath(path)))


def _validate_destination(destination: str | Path, attempt_root: str | Path) -> Path:
    target, root = Path(destination), Path(attempt_root)
    if not target.is_absolute() or not root.is_absolute():
        raise ValueError("destination and attempt_root must be absolute")
    if target.suffix.lower() != ".feb":
        raise ValueError("destination must have a .feb suffix")
    if any(part == ".." for part in root.parts + target.parts):
        raise ValueError("destination path cannot contain escaping parents")
    root_text = _normalised(root)
    target_text = _normalised(target)
    try:
        common = os.path.commonpath((root_text, target_text))
    except ValueError as error:
        raise ValueError("destination and attempt_root must share a path root") from error
    if common != root_text:
        raise ValueError("destination must be lexically beneath attempt_root")
    if not root.is_dir() or _is_link_or_reparse(root):
        raise ValueError("invalid attempt_root")
    parent = target.parent
    if not parent.is_dir():
        raise ValueError("invalid destination parent")
    current = parent
    while _normalised(current) != root_text:
        if _is_link_or_reparse(current):
            raise ValueError("destination parents cannot be links or reparse points")
        current = current.parent
    try:
        if not parent.resolve(strict=True).is_relative_to(root.resolve(strict=True)):
            raise ValueError("destination escapes attempt_root physically")
    except (OSError, RuntimeError) as error:
        raise ValueError("cannot resolve destination parent") from error
    if os.path.lexists(target):
        raise FileExistsError(target)
    return target


def write_derived_feb(
    original: OriginalModel,
    patches: Iterable[FebPatch],
    destination: str | Path,
    attempt_root: str | Path,
    *,
    state_authority: IntentStateAuthority,
    proposal: Proposal,
    proposal_authority: ProposalAuthority,
) -> DerivedFebReceipt:
    if not isinstance(original, OriginalModel):
        raise TypeError("original must be an OriginalModel")
    if not original.verify():
        raise ValueError("original model digest changed before derivation")
    payload = original.read_bytes()
    if hashlib.sha256(payload).hexdigest() != original.sha256:
        raise ValueError("original model digest changed before derivation")
    patch_records = tuple(patches)
    _require_authorized_patch_set(
        state_authority,
        proposal,
        proposal_authority,
        patch_records,
    )
    target = _validate_destination(destination, attempt_root)
    root = _parse_feb(payload)
    applied = _apply_patches(root, patch_records)
    output = ET.tostring(root, encoding="utf-8", xml_declaration=True, short_empty_elements=True)
    created = False
    identity: tuple[int, int] | None = None
    fd: int | None = None
    try:
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
        fd = os.open(os.fspath(target), flags, 0o600)
        created = True
        info = os.fstat(fd)
        identity = (info.st_dev, info.st_ino)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("destination must be a single-link regular file")
        with os.fdopen(fd, "wb") as stream:
            fd = None
            stream.write(output)
            stream.flush()
            os.fsync(stream.fileno())
        info = os.stat(target, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("destination identity changed during write")
        if (info.st_dev, info.st_ino) != identity:
            raise ValueError("destination identity changed during write")
        written = target.read_bytes()
        derived_sha256 = hashlib.sha256(written).hexdigest()
        if not original.verify():
            raise ValueError("original model digest changed after derivation")
        _require_authorized_patch_set(
            state_authority,
            proposal,
            proposal_authority,
            patch_records,
        )
        return DerivedFebReceipt(original.sha256, derived_sha256, target, applied)
    except BaseException:
        if fd is not None:
            os.close(fd)
        if created and os.path.lexists(target):
            try:
                info = os.lstat(target)
                if stat.S_ISREG(info.st_mode) and (
                    identity is None or (info.st_dev, info.st_ino) == identity
                ):
                    target.unlink()
            except OSError:
                pass
        raise


__all__ = ["DerivedFebReceipt", "FebPatch", "write_derived_feb"]
