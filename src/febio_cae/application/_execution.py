"""Private controller ownership adapter; validation never adopts process state."""

from __future__ import annotations

from dataclasses import dataclass

from febio_cae.domain import AttemptRecord, ResultManifest, RunState
from febio_cae.domain.ports import PortError, PortErrorCategory, RunnerPort, TrustedOwnerContext
from febio_cae.storage.registry import CaseStorage


@dataclass
class _RunnerOwner:
    storage: CaseStorage
    owner: TrustedOwnerContext
    claimed: bool = False

    def claim(self, owner: TrustedOwnerContext) -> TrustedOwnerContext:
        if owner != self.owner or self.claimed:
            raise PortError(PortErrorCategory.CONFLICT, "runner claim differs or is repeated")
        attempt = self.storage._attempt(owner)
        self.storage.validate(owner, attempt)
        if attempt.state is not RunState.CREATED:
            raise PortError(PortErrorCategory.CONFLICT, "runner claim is not prepared")
        self.claimed = True
        return owner

    def validate(self, owner: TrustedOwnerContext, attempt: AttemptRecord) -> TrustedOwnerContext:
        if owner != self.owner or not self.claimed:
            raise PortError(PortErrorCategory.CONFLICT, "runner owner is not claimed")
        return self.storage.validate(owner, attempt)

    def publish_manifest(
        self, owner: TrustedOwnerContext, manifest: ResultManifest
    ) -> ResultManifest:
        raise PortError(PortErrorCategory.CONFLICT, "only the application publishes results")


@dataclass
class _CleanupObligation:
    runner: RunnerPort
    storage: CaseStorage
    owner: TrustedOwnerContext
    attempt: AttemptRecord

    def retry(self) -> bool:
        observed = self.runner.cancel(self.attempt, self.owner).attempt
        self.storage._accept_runner_poll(self.owner, self.attempt, observed)
        self.attempt = observed
        return observed.state in {RunState.CANCELLED, RunState.FAILED}


# Process-owned strong references survive a service object's reconstruction.
# These are live native handles, not serializable/recoverable ownership claims.
_pending_cleanup: dict[str, _CleanupObligation] = {}
