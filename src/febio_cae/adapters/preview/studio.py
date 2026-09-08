"""Locally issued, artifact-bound Studio launch and independent confirmation.

Launcher/observer hooks are trusted platform integrations, not native evidence
by themselves. Bare booleans or unbound observations cannot qualify a preview.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from febio_cae.domain import (
    EvidenceRef,
    FileEntry,
    PortError,
    PortErrorCategory,
    PreviewReceipt,
    PreviewRequest,
    PreviewStatus,
    ResultManifest,
    ToolIdentity,
)


class PreviewSource(Protocol):
    @property
    def path(self) -> Path: ...

    def read(self) -> bytes: ...


@dataclass(frozen=True)
class FileSystemPreviewSource:
    path: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", Path(self.path).resolve())

    def read(self) -> bytes:
        if not self.path.is_file() or self.path.is_symlink():
            raise OSError(f"preview file is unavailable: {self.path}")
        return self.path.read_bytes()


@dataclass(frozen=True)
class PreviewBinding:
    """One locally issued invocation; hook results must identify this target."""

    launch_id: str
    receipt_id: str
    manifest_id: str
    path: Path
    studio: ToolIdentity
    xplt_digest: str
    requested_state_ids: tuple[int, ...]
    requested_variables: tuple[str, ...]


@dataclass(frozen=True)
class PreviewLaunchResult:
    """Trusted launcher attests actual launch success for the exact binding."""

    binding: PreviewBinding
    launched: bool


@dataclass(frozen=True)
class PreviewObservation:
    """Trusted observer attests the tool/artifact actually loaded and evidence.

    An integration must derive the returned identity from its observation, not
    merely echo the requested target. Native integration qualification is separate.
    """

    state_ids: tuple[int, ...]
    variables: tuple[str, ...]
    binding: PreviewBinding | None = None
    evidence: tuple[EvidenceRef, ...] = ()


@dataclass(frozen=True)
class _IssuedPreview:
    receipt: PreviewReceipt
    binding: PreviewBinding
    entry: FileEntry


class PreviewAdapter:
    """Launch is not confirmation; only an exact locally issued receipt may advance."""

    def __init__(
        self,
        *,
        studio: ToolIdentity,
        source: PreviewSource,
        launcher: Callable[[PreviewBinding], PreviewLaunchResult] | None = None,
        observer: Callable[[PreviewBinding], PreviewObservation] | None = None,
    ) -> None:
        if not isinstance(studio, ToolIdentity):
            raise TypeError("studio must be a ToolIdentity")
        self.studio = studio
        self.source = source
        self.launcher = launcher
        self.observer = observer
        self._used_ids: set[str] = set()
        self._issued: dict[str, _IssuedPreview] = {}

    def request(self, manifest: ResultManifest, request: PreviewRequest) -> PreviewReceipt:
        if request.manifest_id != manifest.manifest_id:
            raise PortError(
                PortErrorCategory.CONFLICT, "preview request does not target the manifest"
            )
        if request.preview_id in self._used_ids:
            raise PortError(PortErrorCategory.CONFLICT, "preview identity was already used")
        entries = [
            entry
            for entry in manifest.files
            if entry.role == "result" and entry.logical_path.endswith(".xplt")
        ]
        if len(entries) != 1:
            raise PortError(
                PortErrorCategory.INTEGRITY, "manifest must contain exactly one result XPLT"
            )
        entry = entries[0]
        path = Path(self.source.path).resolve()
        self._read_bound(path, entry)
        binding = PreviewBinding(
            uuid4().hex,
            request.preview_id,
            manifest.manifest_id,
            path,
            self.studio,
            entry.digest,
            tuple(request.state_ids),
            tuple(request.variables),
        )
        receipt = PreviewReceipt(
            request.preview_id,
            manifest.manifest_id,
            entry.digest,
            self.studio,
            PreviewStatus.FAILED,
            request.state_ids,
            request.variables,
            (),
            (),
            (),
        )
        # Reserve before calling an integration: reentrant/repeated requests
        # cannot create a second invocation under the same public receipt ID.
        self._used_ids.add(request.preview_id)
        if self.launcher is None:
            return receipt
        try:
            result = self.launcher(binding)
            if (
                not isinstance(result, PreviewLaunchResult)
                or result.launched is not True
                or result.binding != binding
            ):
                return receipt
            self._read_bound(path, entry)
        except (OSError, RuntimeError, TypeError, ValueError):
            return receipt
        receipt = replace(receipt, status=PreviewStatus.LAUNCHED)
        self._issued[receipt.receipt_id] = _IssuedPreview(receipt, binding, entry)
        return receipt

    def confirm(self, receipt: PreviewReceipt, evidence: Sequence[EvidenceRef]) -> PreviewReceipt:
        if not isinstance(receipt, PreviewReceipt):
            raise TypeError("receipt must be a PreviewReceipt")
        issued = self._issued.get(receipt.receipt_id)
        if issued is None or receipt != issued.receipt or receipt.studio != self.studio:
            # A forged caller record does not revoke the actual locally issued record.
            return self._failed(receipt)
        if receipt.status not in {PreviewStatus.LAUNCHED, PreviewStatus.CONFIRMED}:
            raise PortError(PortErrorCategory.CONFLICT, "preview cannot be confirmed in this state")
        try:
            self._read_bound(issued.binding.path, issued.entry)
        except (OSError, RuntimeError, TypeError, ValueError):
            return self._invalidate(issued)
        if receipt.status is PreviewStatus.CONFIRMED:
            return receipt
        if not evidence:
            raise PortError(
                PortErrorCategory.INTEGRITY, "confirmation requires independent evidence"
            )
        if self.observer is None:
            raise PortError(
                PortErrorCategory.UNSUPPORTED_CAPABILITY,
                "independent Studio observation is unavailable",
            )
        try:
            observation = self.observer(issued.binding)
            if (
                not isinstance(observation, PreviewObservation)
                or observation.binding != issued.binding
                or tuple(observation.evidence) != tuple(evidence)
                or not set(receipt.requested_state_ids).issubset(observation.state_ids)
                or not set(receipt.requested_variables).issubset(observation.variables)
            ):
                return self._invalidate(issued)
            self._read_bound(issued.binding.path, issued.entry)
            if self._issued.get(receipt.receipt_id) != issued:
                # Observation callbacks may allow another check to invalidate
                # the receipt. Restored bytes do not erase that observed failure.
                return self._failed(receipt)
            confirmed = receipt.confirmed(
                evidence=observation.evidence,
                observed_state_ids=observation.state_ids,
                observed_variables=observation.variables,
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            return self._invalidate(issued)
        self._issued[receipt.receipt_id] = replace(issued, receipt=confirmed)
        return confirmed

    def _read_bound(self, path: Path, entry: FileEntry) -> None:
        try:
            if Path(self.source.path).resolve() != path:
                raise ValueError("preview source path changed")
            content = self.source.read()
            if (
                not isinstance(content, bytes)
                or Path(self.source.path).resolve() != path
                or len(content) != entry.size_bytes
                or hashlib.sha256(content).hexdigest() != entry.digest
            ):
                raise ValueError("preview XPLT differs from its registered artifact")
        except (OSError, ValueError, TypeError) as error:
            raise PortError(
                PortErrorCategory.INTEGRITY, f"preview source is unavailable or changed: {error}"
            ) from error

    def _invalidate(self, issued: _IssuedPreview) -> PreviewReceipt:
        failed = self._failed(issued.receipt)
        self._issued[failed.receipt_id] = replace(issued, receipt=failed)
        return failed

    @staticmethod
    def _failed(receipt: PreviewReceipt) -> PreviewReceipt:
        return replace(
            receipt,
            status=PreviewStatus.FAILED,
            observed_state_ids=(),
            observed_variables=(),
            confirmation_evidence=(),
        )


__all__ = [
    "FileSystemPreviewSource",
    "PreviewAdapter",
    "PreviewBinding",
    "PreviewLaunchResult",
    "PreviewObservation",
    "PreviewRequest",
]
