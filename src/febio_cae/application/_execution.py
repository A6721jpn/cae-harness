"""Private controller ownership adapter; validation never adopts process state."""

from __future__ import annotations

from dataclasses import dataclass

from febio_cae.domain import AttemptRecord, ResultManifest, RunState
from febio_cae.domain.ports import PortError, PortErrorCategory, TrustedOwnerContext
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
