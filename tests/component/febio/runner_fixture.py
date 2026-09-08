"""Committed synthetic compiler-to-runner fixture, independent of parked tests."""

import sys
from pathlib import Path
from typing import Any

from febio_cae.adapters.febio.compiler import CompilerAdapter, LocalBundleStore
from febio_cae.domain import TrustedOwnerContext

from .test_compiler_native import _case


def _compiled(tmp_path: Path) -> tuple[Any, Any, Any, Any, Any]:
    revision, mesh, profile = _case()
    store = LocalBundleStore(tmp_path / "bundles")
    bundle = CompilerAdapter(store=store, executable=sys.executable).compile(
        revision, mesh, profile
    )
    return revision, mesh, profile, bundle, store


def _owner(run_id: str = "run-p3") -> TrustedOwnerContext:
    return TrustedOwnerContext("case-p3", run_id, "attempt-p3", 1)


class _Ownership:
    def __init__(self) -> None:
        self.revoked = False

    def claim(self, owner: TrustedOwnerContext) -> TrustedOwnerContext:
        return owner

    def validate(self, owner: TrustedOwnerContext, attempt: Any) -> TrustedOwnerContext:
        if self.revoked or owner != TrustedOwnerContext(
            attempt.case_id, attempt.run_id, attempt.attempt_id, attempt.owner_generation
        ):
            raise RuntimeError("owner revoked or mismatched")
        return owner

    def publish_manifest(self, owner: TrustedOwnerContext, manifest: Any) -> Any:
        return manifest
