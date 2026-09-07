"""Hash-bound FEBio Studio launch and independent read confirmation."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from febio_cae.domain import (
    EvidenceRef,
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
class PreviewObservation:
    state_ids: tuple[int, ...]
    variables: tuple[str, ...]


class PreviewAdapter:
    """The request hash and independent observation are separate evidence steps."""

    def __init__(
        self,
        *,
        studio: ToolIdentity,
        source: PreviewSource,
        launcher: Callable[[Path, ToolIdentity], bool] | None = None,
        observer: Callable[[Path, ToolIdentity], PreviewObservation] | None = None,
    ) -> None:
        if not isinstance(studio, ToolIdentity):
            raise TypeError("studio must be a ToolIdentity")
        self.studio = studio
        self.source = source
        self.launcher = launcher or (lambda _path, _studio: True)
        self.observer = observer

    def request(self, manifest: ResultManifest, request: PreviewRequest) -> PreviewReceipt:
        if request.manifest_id != manifest.manifest_id:
            raise PortError(
                PortErrorCategory.CONFLICT, "preview request does not target the manifest"
            )
        digest = self._current_digest(manifest)
        launched = bool(self.launcher(self.source.path, self.studio))
        status = PreviewStatus.LAUNCHED if launched else PreviewStatus.FAILED
        return PreviewReceipt(
            receipt_id=request.preview_id,
            manifest_id=manifest.manifest_id,
            xplt_digest=digest,
            studio=self.studio,
            status=status,
            requested_state_ids=request.state_ids,
            requested_variables=request.variables,
            observed_state_ids=(),
            observed_variables=(),
            confirmation_evidence=(),
        )

    def confirm(
        self, receipt: PreviewReceipt, evidence: tuple[EvidenceRef, ...] | list[EvidenceRef]
    ) -> PreviewReceipt:
        if not isinstance(receipt, PreviewReceipt):
            raise TypeError("receipt must be a PreviewReceipt")
        if receipt.status is PreviewStatus.CONFIRMED:
            try:
                current_digest = hashlib.sha256(self.source.read()).hexdigest()
            except OSError:
                return self._failed(receipt, "preview file is unavailable after confirmation")
            if current_digest != receipt.xplt_digest:
                return self._failed(receipt, "preview XPLT changed after confirmation")
            return receipt
        if receipt.status is not PreviewStatus.LAUNCHED:
            raise PortError(PortErrorCategory.CONFLICT, "only a launched preview can be confirmed")
        try:
            current_digest = hashlib.sha256(self.source.read()).hexdigest()
        except OSError as error:
            return self._failed(receipt, f"preview file is unavailable: {error}")
        if current_digest != receipt.xplt_digest:
            return self._failed(receipt, "preview XPLT changed after launch")
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
            observation = self.observer(self.source.path, self.studio)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            return self._failed(receipt, f"Studio observation failed: {error}")
        if not isinstance(observation, PreviewObservation):
            return self._failed(receipt, "Studio observation has an invalid shape")
        observed_states = tuple(observation.state_ids)
        observed_variables = tuple(observation.variables)
        if not set(receipt.requested_state_ids).issubset(observed_states):
            return self._failed(receipt, "Studio observation did not include all requested states")
        if not set(receipt.requested_variables).issubset(observed_variables):
            return self._failed(
                receipt, "Studio observation did not include all requested variables"
            )
        return receipt.confirmed(
            evidence=evidence,
            observed_state_ids=observed_states,
            observed_variables=observed_variables,
        )

    def _current_digest(self, manifest: ResultManifest) -> str:
        entries = [
            entry
            for entry in manifest.files
            if entry.role == "result" and entry.logical_path.endswith(".xplt")
        ]
        if len(entries) != 1:
            raise PortError(
                PortErrorCategory.INTEGRITY, "manifest must contain exactly one result XPLT"
            )
        try:
            content = self.source.read()
        except OSError as error:
            raise PortError(
                PortErrorCategory.INTEGRITY, f"preview XPLT is unavailable: {error}"
            ) from error
        digest = hashlib.sha256(content).hexdigest()
        if digest != entries[0].digest or len(content) != entries[0].size_bytes:
            raise PortError(
                PortErrorCategory.INTEGRITY, "preview XPLT does not match the registered manifest"
            )
        return digest

    @staticmethod
    def _failed(receipt: PreviewReceipt, _reason: str) -> PreviewReceipt:
        return PreviewReceipt(
            receipt_id=receipt.receipt_id,
            manifest_id=receipt.manifest_id,
            xplt_digest=receipt.xplt_digest,
            studio=receipt.studio,
            status=PreviewStatus.FAILED,
            requested_state_ids=receipt.requested_state_ids,
            requested_variables=receipt.requested_variables,
            observed_state_ids=(),
            observed_variables=(),
            confirmation_evidence=(),
        )


__all__ = ["FileSystemPreviewSource", "PreviewAdapter", "PreviewObservation", "PreviewRequest"]
