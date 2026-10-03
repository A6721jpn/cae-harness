"""Durable independent static records and runner-issued publication authority."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

from febio_cae.domain import (
    AttemptRecord,
    ExecutionBundle,
    ResultManifest,
    RunState,
    TrustedOwnerContext,
)
from febio_cae.domain.canonical import canonical_bytes
from febio_cae.domain.codec import decode_record
from febio_cae.domain.ports import PortError, PortErrorCategory

from ._ownership import lease, pin_directories, pinned_read


class StaticLoadStore:
    """One explicit preparation and one solver launch per isolated root.

    A process-owned publication lease must cover the controller operation. After
    a crash records remain inspectable, but no PID adoption or solver retry is
    permitted. The static lineage does not impersonate a contact CaseRevision.
    """

    def __init__(self, root: Path, *, create: bool = True) -> None:
        self.root = root.absolute()
        with pin_directories(self.root, create=create):
            pass
        self._issued: AttemptRecord | None = None
        self._runner: Any = None

    def operation(self) -> Any:
        return lease(self.root, blocking=False)

    def write_record(self, name: str, value: dict[str, Any], *, immutable: bool = False) -> None:
        self.write_bytes(
            name, canonical_bytes({"schema_version": "1", **value}), immutable=immutable
        )

    def write_bytes(self, name: str, content: bytes, *, immutable: bool = False) -> None:
        if Path(name).name != name:
            raise ValueError("static record name must be a filename")
        target = self.root / name
        with pin_directories(self.root):
            if target.exists():
                with pinned_read(target) as stream:
                    previous = stream.read()
                if immutable:
                    if previous != content:
                        raise ValueError("immutable static record changed")
                    return
            descriptor, temporary = tempfile.mkstemp(prefix=".static-", dir=self.root)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, target)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)

    def read_record(self, name: str) -> dict[str, Any]:
        if Path(name).name != name:
            raise ValueError("static record name must be a filename")

        def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in items:
                if key in result:
                    raise ValueError(f"duplicate JSON key: {key}")
                result[key] = value
            return result

        def constant(value: str) -> Any:
            raise ValueError(f"nonfinite JSON constant: {value}")

        try:
            with pinned_read(self.root / name) as stream:
                value = json.loads(stream.read(), object_pairs_hook=pairs, parse_constant=constant)
            if not isinstance(value, dict) or value.get("schema_version") != "1":
                raise ValueError("static stored record must be a schema-1 object")
        except (ValueError, TypeError) as error:
            raise PortError(
                PortErrorCategory.INTEGRITY, f"malformed static record {name}: {error}"
            ) from error
        return value

    def issue(self, bundle: ExecutionBundle) -> TrustedOwnerContext:
        if (self.root / "run.json").exists():
            raise PortError(
                PortErrorCategory.CONFLICT,
                "static root has already reserved its single solver attempt",
            )
        owner = TrustedOwnerContext(
            bundle.case_id,
            "static-run-" + uuid.uuid4().hex,
            "static-attempt-" + uuid.uuid4().hex,
            0,
        )
        self.write_bytes("bundle.json", bundle.to_bytes(), immutable=True)
        self.write_record(
            "run.json",
            {
                "owner": {
                    "case_id": owner.case_id,
                    "run_id": owner.run_id,
                    "attempt_id": owner.attempt_id,
                    "owner_generation": owner.owner_generation,
                },
                "bundle_digest": bundle.bundle_digest,
                "claimed": False,
                "attempt": None,
                "status": "CREATED",
            },
        )
        return owner

    def _owner_record(self, owner: TrustedOwnerContext) -> dict[str, Any]:
        record = self.read_record("run.json")
        expected = {
            "case_id": owner.case_id,
            "run_id": owner.run_id,
            "attempt_id": owner.attempt_id,
            "owner_generation": owner.owner_generation,
        }
        if record["owner"] != expected:
            raise PortError(
                PortErrorCategory.CONFLICT, "static ownership scope or generation mismatch"
            )
        bundle = decode_record(canonical_bytes(self.read_record("bundle.json")), ExecutionBundle)
        if bundle.bundle_digest != record["bundle_digest"] or bundle.case_id != owner.case_id:
            raise PortError(PortErrorCategory.INTEGRITY, "static ownership bundle binding changed")
        return record

    def claim(self, owner: TrustedOwnerContext) -> TrustedOwnerContext:
        record = self._owner_record(owner)
        if record["claimed"] or record["status"] != "CREATED":
            raise PortError(PortErrorCategory.CONFLICT, "static solver scope is already claimed")
        record["claimed"] = True
        record["status"] = "PREPARING"
        self.write_record("run.json", record)
        return owner

    def validate(self, owner: TrustedOwnerContext, attempt: AttemptRecord) -> TrustedOwnerContext:
        record = self._owner_record(owner)
        if (
            not record["claimed"]
            or record["attempt"] != attempt.to_dict()
            or self._issued is not attempt
        ):
            raise PortError(
                PortErrorCategory.CONFLICT,
                "attempt is not the current locally issued static runner snapshot",
            )
        if attempt.bundle_digest != record["bundle_digest"]:
            raise PortError(PortErrorCategory.INTEGRITY, "static attempt bundle identity changed")
        return owner

    def _accept(self, attempt: AttemptRecord, owner: TrustedOwnerContext) -> AttemptRecord:
        record = self._owner_record(owner)
        if (
            attempt.case_id,
            attempt.run_id,
            attempt.attempt_id,
            attempt.owner_generation,
            attempt.bundle_digest,
        ) != (
            owner.case_id,
            owner.run_id,
            owner.attempt_id,
            owner.owner_generation,
            record["bundle_digest"],
        ):
            raise ValueError("runner returned an unexpected static scope")
        self._issued = attempt
        record["attempt"] = attempt.to_dict()
        record["status"] = attempt.state.value
        self.write_record("run.json", record)
        return attempt

    def start(
        self, runner: Any, bundle: ExecutionBundle, owner: TrustedOwnerContext, budget: Any
    ) -> AttemptRecord:
        self._runner = runner
        self._issued = runner.start(bundle, owner, budget)
        return self._accept(self._issued, owner)

    def poll(self, owner: TrustedOwnerContext) -> AttemptRecord:
        if self._issued is None or self._runner is None:
            raise ValueError("no locally retained static process")
        previous = self._issued
        result = self._runner.poll(previous, owner)
        self._issued = result.attempt
        return self._accept(result.attempt, owner)

    def cancel_and_drain(self, owner: TrustedOwnerContext) -> None:
        import time

        if self._runner is None:
            return
        while self._runner.retry_failed_launch_cleanup():
            time.sleep(0.02)
        if self._issued is None:
            return
        persistence_error: BaseException | None = None
        while self._issued.state in (RunState.RUNNING, RunState.DRAINING):
            result = self._runner.cancel(self._issued, owner)
            self._issued = result.attempt
            try:
                self._accept(result.attempt, owner)
            except BaseException as error:  # noqa: BLE001 - rethrow any persistence interruption only after the owned process has drained.
                persistence_error = error
            if self._issued.state in (RunState.RUNNING, RunState.DRAINING):
                time.sleep(0.02)
        if persistence_error is not None:
            raise persistence_error

    def publish_manifest(
        self, owner: TrustedOwnerContext, manifest: ResultManifest
    ) -> ResultManifest:
        if self._issued is None:
            raise ValueError("no issued static attempt")
        self.validate(owner, self._issued)
        if (
            self._issued.state is not RunState.VALIDATING
            or manifest.attempt_id != owner.attempt_id
            or manifest.bundle_digest != self._issued.bundle_digest
            or manifest.read_result.status.value != "VALIDATED"
        ):
            raise ValueError("static publication requires drained validated outputs")
        for entry in manifest.files:
            with pinned_read(self.root / "sealed-results.xplt") as stream:
                content = stream.read()
            if entry.digest != hashlib.sha256(content).hexdigest() or entry.size_bytes != len(
                content
            ):
                raise ValueError("sealed static result identity changed")
        self.write_bytes("manifest.json", manifest.to_bytes(), immutable=True)
        self._accept(self._issued.transition_to(RunState.SUCCEEDED), owner)
        return manifest

    def fail(self, error: str) -> None:
        if (self.root / "run.json").exists():
            record = self.read_record("run.json")
            record["status"] = "FAILED"
            record["error"] = error
            if self._issued is not None:
                if self._issued.state is RunState.VALIDATING:
                    self._issued = self._issued.transition_to(RunState.FAILED)
                record["attempt"] = self._issued.to_dict()
            self.write_record("run.json", record)
