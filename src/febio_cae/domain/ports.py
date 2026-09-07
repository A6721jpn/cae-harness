"""Typed service boundaries for common records; no adapter implementation lives here."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from .artifacts import GeometryInspection, GeometryInspectionRequest, MeshArtifact
from .budget import Budget
from .case_draft import CaseDraft
from .case_revision import CaseRevision
from .compatibility import CompatibilityProfile
from .evidence import EvidenceRef
from .execution import AttemptRecord, ExecutionBundle
from .preview import PreviewReceipt, PreviewRequest
from .results import QualityAssessment, ResultManifest


class PortErrorCategory(str, Enum):
    INVALID_INPUT = "invalid_input"
    NEEDS_PHYSICAL_INPUT = "needs_physical_input"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    ENVIRONMENT = "environment"
    EXECUTION = "execution"
    INTEGRITY = "integrity"
    QUALITY = "quality"
    CANCELLED = "cancelled"
    CONFLICT = "conflict"


class PortError(RuntimeError):
    """Structured boundary error; it carries no authorization state."""

    def __init__(self, category: PortErrorCategory, message: str) -> None:
        if not isinstance(category, PortErrorCategory):
            raise TypeError("category must be a PortErrorCategory")
        if not isinstance(message, str) or not message or message != message.strip():
            raise ValueError("message must be non-empty text")
        super().__init__(message)
        self.category = category


@dataclass(frozen=True, slots=True)
class TrustedOwnerContext:
    """Scoped service context passed by trusted application/controller code."""

    case_id: str
    run_id: str
    attempt_id: str
    owner_generation: int

    def __post_init__(self) -> None:
        for field_name in ("case_id", "run_id", "attempt_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value or value != value.strip():
                raise ValueError(f"{field_name} must be non-empty text")
        if (
            isinstance(self.owner_generation, bool)
            or not isinstance(self.owner_generation, int)
            or self.owner_generation < 0
        ):
            raise ValueError("owner_generation must be a nonnegative integer")


@dataclass(frozen=True, slots=True)
class PollResult:
    attempt: AttemptRecord
    diagnostics: Sequence[str] = ()


@dataclass(frozen=True, slots=True)
class CancelResult:
    attempt: AttemptRecord
    diagnostics: Sequence[str] = ()


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    attempt: AttemptRecord
    diagnostics: Sequence[str] = ()


@runtime_checkable
class GeometryPort(Protocol):
    def inspect(self, request: GeometryInspectionRequest) -> GeometryInspection:
        """Inspect a registered source asset before a CaseRevision exists."""


@runtime_checkable
class MeshingPort(Protocol):
    def mesh(self, revision: CaseRevision) -> MeshArtifact:
        """Return a structural mesh artifact for a complete registered revision."""


@runtime_checkable
class CompilerPort(Protocol):
    def compile(
        self,
        revision: CaseRevision,
        mesh: MeshArtifact,
        profile: CompatibilityProfile,
    ) -> ExecutionBundle:
        """Compile a fixed bundle; the port does not grant process permission."""


@runtime_checkable
class RunnerPort(Protocol):
    def start(
        self,
        bundle: ExecutionBundle,
        owner: TrustedOwnerContext,
        budget: Budget,
    ) -> AttemptRecord:
        """Start only with trusted owner context supplied by the application."""

    def poll(self, attempt: AttemptRecord, owner: TrustedOwnerContext) -> PollResult:
        """Poll a run without collapsing root exit and descendant drain."""

    def cancel(self, attempt: AttemptRecord, owner: TrustedOwnerContext) -> CancelResult:
        """Request cancellation of the owned process group."""

    def reconcile(self, attempt: AttemptRecord, owner: TrustedOwnerContext) -> ReconcileResult:
        """Reconcile persistent state with owned process/output evidence."""


@runtime_checkable
class ResultReaderPort(Protocol):
    def read(self, attempt: AttemptRecord, bundle: ExecutionBundle) -> ResultManifest:
        """Return a validated manifest candidate; trusted storage publishes atomically."""


@runtime_checkable
class QualityPort(Protocol):
    def assess(
        self,
        manifest: ResultManifest,
        revision: CaseRevision,
        mesh: MeshArtifact,
        profile: CompatibilityProfile,
    ) -> QualityAssessment:
        """Create a separate quality record; do not mutate run state."""


@runtime_checkable
class PreviewPort(Protocol):
    def request(self, manifest: ResultManifest, request: PreviewRequest) -> PreviewReceipt:
        """Launch/request a preview without treating launch as confirmation."""

    def confirm(
        self,
        receipt: PreviewReceipt,
        evidence: Sequence[EvidenceRef],
    ) -> PreviewReceipt:
        """Record independent read-confirmation evidence."""


@runtime_checkable
class CaseRegistryPort(Protocol):
    def current_draft(self, case_id: str) -> CaseDraft: ...

    def get_revision(self, case_id: str, revision_id: str) -> CaseRevision: ...

    def register_revision(self, revision: CaseRevision) -> CaseRevision: ...


@runtime_checkable
class CompatibilityRegistryPort(Protocol):
    def get_profile(self, profile_id: str) -> CompatibilityProfile: ...


@runtime_checkable
class OwnershipPort(Protocol):
    def claim(self, owner: TrustedOwnerContext) -> TrustedOwnerContext: ...

    def validate(
        self, owner: TrustedOwnerContext, attempt: AttemptRecord
    ) -> TrustedOwnerContext: ...

    def publish_manifest(
        self, owner: TrustedOwnerContext, manifest: ResultManifest
    ) -> ResultManifest: ...


__all__ = [
    "CancelResult",
    "CaseRegistryPort",
    "CompatibilityRegistryPort",
    "CompilerPort",
    "GeometryPort",
    "MeshingPort",
    "OwnershipPort",
    "PollResult",
    "PortError",
    "PortErrorCategory",
    "PreviewPort",
    "QualityPort",
    "ReconcileResult",
    "ResultReaderPort",
    "RunnerPort",
    "TrustedOwnerContext",
]
