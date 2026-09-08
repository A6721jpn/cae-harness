"""Locally issued, artifact-bound Studio launch and independent confirmation.

Launcher/observer hooks are trusted platform integrations, not native evidence
by themselves. Bare booleans or unbound observations cannot qualify a preview.
"""

from __future__ import annotations

import hashlib
import math
import time
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
class ExistingStudioSession:
    """Private observed process lifetime and window identity, never a launch claim."""

    studio: ToolIdentity
    process_id: int
    process_start_marker: str
    window_id: int

    def __post_init__(self) -> None:
        if not isinstance(self.studio, ToolIdentity):
            raise TypeError("session studio must be a ToolIdentity")
        if any(type(value) is not int or value <= 0 for value in (self.process_id, self.window_id)):
            raise ValueError("session process and window identifiers must be positive integers")
        if (
            not isinstance(self.process_start_marker, str)
            or not self.process_start_marker.strip()
            or self.process_start_marker != self.process_start_marker.strip()
        ):
            raise ValueError("session requires an explicit process lifetime marker")


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
    existing_session: ExistingStudioSession | None = None


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
    existing_session: ExistingStudioSession | None = None


@dataclass(frozen=True)
class _IssuedPreview:
    receipt: PreviewReceipt
    binding: PreviewBinding
    entry: FileEntry
    observation_only: bool = False
    deadline: float | None = None


class PreviewAdapter:
    """Launch is not confirmation; only an exact locally issued receipt may advance."""

    def __init__(
        self,
        *,
        studio: ToolIdentity,
        source: PreviewSource,
        launcher: Callable[[PreviewBinding], PreviewLaunchResult] | None = None,
        observer: Callable[[PreviewBinding], PreviewObservation] | None = None,
        session_probe: Callable[[ExistingStudioSession], ExistingStudioSession | None]
        | None = None,
    ) -> None:
        if not isinstance(studio, ToolIdentity):
            raise TypeError("studio must be a ToolIdentity")
        self.studio = studio
        self.source = source
        self.launcher = launcher
        self.observer = observer
        self.session_probe = session_probe
        self._used_ids: set[str] = set()
        self._issued: dict[str, _IssuedPreview] = {}

    def request(self, manifest: ResultManifest, request: PreviewRequest) -> PreviewReceipt:
        issued = self._prepare(manifest, request)
        receipt, binding, entry = issued.receipt, issued.binding, issued.entry
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
            self._read_bound(binding.path, entry)
        except (OSError, RuntimeError, TypeError, ValueError):
            return receipt
        receipt = replace(receipt, status=PreviewStatus.LAUNCHED)
        self._issued[receipt.receipt_id] = replace(issued, receipt=receipt)
        return receipt

    def request_observation(
        self,
        manifest: ResultManifest,
        request: PreviewRequest,
        *,
        session: ExistingStudioSession,
        timeout_seconds: float,
    ) -> PreviewReceipt:
        """Issue for one live session; no launcher, persisted restore, or ledger debit.

        The trusted probe must independently inspect the process/window identity.
        Observation and confirmation must use this retained adapter instance.
        """
        if not isinstance(session, ExistingStudioSession) or session.studio != self.studio:
            raise ValueError("existing session must identify this Studio tool")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("observation timeout must be finite and positive")
        deadline = time.monotonic() + timeout_seconds
        if not math.isfinite(deadline):
            raise ValueError("observation deadline must be finite")
        issued = self._prepare(manifest, request)
        issued = replace(
            issued,
            binding=replace(issued.binding, existing_session=session),
            observation_only=True,
            deadline=deadline,
        )
        try:
            self._verify_issued(issued)
        except (OSError, RuntimeError, TypeError, ValueError):
            return issued.receipt
        receipt = replace(issued.receipt, status=PreviewStatus.REQUESTED)
        self._issued[receipt.receipt_id] = replace(issued, receipt=receipt)
        return receipt

    def observation_binding(self, receipt: PreviewReceipt) -> PreviewBinding:
        """Expose a pending local nonce for fresh capture, never restore authority."""
        if not isinstance(receipt, PreviewReceipt):
            raise TypeError("receipt must be a PreviewReceipt")
        issued = self._issued.get(receipt.receipt_id)
        if (
            issued is None
            or receipt != issued.receipt
            or not issued.observation_only
            or receipt.status is not PreviewStatus.REQUESTED
        ):
            raise PortError(PortErrorCategory.CONFLICT, "no matching pending observation issue")
        try:
            self._verify_issued(issued)
            if self._issued.get(receipt.receipt_id) != issued:
                raise ValueError("observation issue was invalidated during verification")
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            self._invalidate(issued)
            raise PortError(
                PortErrorCategory.INTEGRITY, "observation issue is no longer valid"
            ) from error
        return issued.binding

    def _prepare(self, manifest: ResultManifest, request: PreviewRequest) -> _IssuedPreview:
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
        # Source properties/reads are integration callbacks too. Reserve before
        # the first callback; an unsuccessful read still consumes this identity.
        self._used_ids.add(request.preview_id)
        nonce = uuid4().hex
        path = Path(self.source.path).resolve()
        self._read_bound(path, entry)
        binding = PreviewBinding(
            nonce,
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
        return _IssuedPreview(receipt, binding, entry)

    def confirm(self, receipt: PreviewReceipt, evidence: Sequence[EvidenceRef]) -> PreviewReceipt:
        if not isinstance(receipt, PreviewReceipt):
            raise TypeError("receipt must be a PreviewReceipt")
        issued = self._issued.get(receipt.receipt_id)
        if issued is None or receipt != issued.receipt or receipt.studio != self.studio:
            # A forged caller record does not revoke the actual locally issued record.
            return self._failed(receipt)
        allowed = {PreviewStatus.LAUNCHED, PreviewStatus.CONFIRMED}
        if issued.observation_only:
            allowed = {PreviewStatus.REQUESTED, PreviewStatus.CONFIRMED}
        if receipt.status not in allowed:
            raise PortError(PortErrorCategory.CONFLICT, "preview cannot be confirmed in this state")
        try:
            self._verify_issued(issued)
        except (OSError, RuntimeError, TypeError, ValueError):
            return self._invalidate(issued)
        if self._issued.get(receipt.receipt_id) != issued:
            # A nested source callback may have retained an invalidation even
            # though the outer read sees restored bytes. Do not return stale
            # confirmation or invoke an observer against that superseded issue.
            return self._failed(receipt)
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
                or (
                    issued.observation_only
                    and observation.existing_session != issued.binding.existing_session
                )
                or tuple(observation.evidence) != tuple(evidence)
                or not set(receipt.requested_state_ids).issubset(observation.state_ids)
                or not set(receipt.requested_variables).issubset(observation.variables)
            ):
                return self._invalidate(issued)
            self._verify_issued(issued)
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

    def _verify_issued(self, issued: _IssuedPreview) -> None:
        self._read_bound(issued.binding.path, issued.entry)
        if not issued.observation_only:
            return
        session = issued.binding.existing_session
        if (
            session is None
            or self.session_probe is None
            or issued.deadline is None
            or time.monotonic() >= issued.deadline
        ):
            raise ValueError("live session probe or observation time is unavailable")
        observed = self.session_probe(session)
        if not isinstance(observed, ExistingStudioSession) or observed != session:
            raise ValueError("existing Studio process or window changed")
        self._read_bound(issued.binding.path, issued.entry)
        if time.monotonic() >= issued.deadline:
            raise ValueError("observation deadline expired")

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
    "ExistingStudioSession",
    "FileSystemPreviewSource",
    "PreviewAdapter",
    "PreviewBinding",
    "PreviewLaunchResult",
    "PreviewObservation",
    "PreviewRequest",
]
