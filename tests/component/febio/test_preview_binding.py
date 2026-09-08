"""Synthetic launcher/observer binding checks; never a Studio or Computer Use run."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from febio_cae.adapters.preview import studio as module
from febio_cae.domain import (
    EvidenceRef,
    FileEntry,
    OutputObservation,
    PortError,
    PortErrorCategory,
    PreviewReceipt,
    PreviewRequest,
    PreviewStatus,
    ReadResult,
    ReadStatus,
    ResultManifest,
    ToolIdentity,
)
from febio_cae.domain.codec import decode_record, encode_record

from .fixtures import WORLD


class PreviewCase:
    def __init__(self, tmp_path: Path) -> None:
        self.path = tmp_path / "result.xplt"
        self.content = b"synthetic preview artifact; not native XPLT"
        self.path.write_bytes(self.content)
        self.digest = hashlib.sha256(self.content).hexdigest()
        self.studio = ToolIdentity("febio-studio", "2.8", "2" * 64)
        self.manifest = ResultManifest(
            "preview-manifest",
            "preview-attempt",
            "1" * 64,
            (FileEntry("output/results.xplt", self.digest, len(self.content), "result"),),
            ReadResult(
                ReadStatus.VALIDATED,
                ToolIdentity("reader", "4.12", "3" * 64),
                (OutputObservation("displacement", "node", "VEC3F", "m", WORLD, "value", 2),),
                (),
            ),
        )
        self.request = PreviewRequest(
            "preview-request", self.manifest.manifest_id, (0, 1), ("displacement",)
        )
        self.evidence = (
            EvidenceRef(
                "1",
                "registered_document",
                "synthetic-observation",
                "preview.confirmation",
                "4" * 64,
            ),
        )
        self.launches = 0
        self.observations = 0
        self.binding: module.PreviewBinding | None = None

    def launch(self, binding: module.PreviewBinding) -> module.PreviewLaunchResult:
        self.launches += 1
        self.binding = binding
        return module.PreviewLaunchResult(binding, True)

    def observe(self, binding: module.PreviewBinding) -> module.PreviewObservation:
        self.observations += 1
        return module.PreviewObservation((0, 1), ("displacement",), binding, self.evidence)

    def adapter(self, **overrides: Any) -> module.PreviewAdapter:
        options: dict[str, Any] = {
            "studio": self.studio,
            "source": module.FileSystemPreviewSource(self.path),
            "launcher": self.launch,
            "observer": self.observe,
        }
        options.update(overrides)
        return module.PreviewAdapter(**options)


def test_configured_bound_launch_then_independent_confirmation(tmp_path: Path) -> None:
    case = PreviewCase(tmp_path)
    adapter = case.adapter()
    receipt = adapter.request(case.manifest, case.request)
    assert receipt.status is PreviewStatus.LAUNCHED
    assert not receipt.confirmation_evidence and case.observations == 0
    confirmed = adapter.confirm(receipt, case.evidence)
    assert confirmed.status is PreviewStatus.CONFIRMED
    assert case.launches == case.observations == 1
    assert adapter.confirm(confirmed, case.evidence) == confirmed
    assert case.observations == 1
    restored = decode_record(encode_record(confirmed), PreviewReceipt)
    assert adapter.confirm(restored, case.evidence) == confirmed


@pytest.mark.parametrize("value", [True, "failed", 1])
def test_unbound_truthy_launcher_result_never_means_launched(tmp_path: Path, value: object) -> None:
    case = PreviewCase(tmp_path)
    receipt = case.adapter(launcher=lambda *args: value).request(case.manifest, case.request)
    assert receipt.status is PreviewStatus.FAILED


def test_no_configured_launcher_remains_failed(tmp_path: Path) -> None:
    case = PreviewCase(tmp_path)
    assert (
        case.adapter(launcher=None).request(case.manifest, case.request).status
        is PreviewStatus.FAILED
    )


@pytest.mark.parametrize("defect", ["not-started", "foreign-studio"])
def test_launcher_must_return_success_for_the_exact_target(tmp_path: Path, defect: str) -> None:
    case = PreviewCase(tmp_path)

    def launch(target: module.PreviewBinding) -> module.PreviewLaunchResult:
        result = case.launch(target)
        binding = result.binding
        if defect == "foreign-studio":
            tool = replace(case.studio, version="99")
            binding = replace(binding, studio=tool)
        return replace(result, binding=binding, launched=defect != "not-started")

    assert (
        case.adapter(launcher=launch).request(case.manifest, case.request).status
        is PreviewStatus.FAILED
    )


@pytest.mark.parametrize("defect", ["manifest", "scope", "confirmed", "studio"])
def test_caller_cannot_edit_or_self_confirm_an_issued_receipt(tmp_path: Path, defect: str) -> None:
    case = PreviewCase(tmp_path)
    adapter = case.adapter()
    receipt = adapter.request(case.manifest, case.request)
    if defect == "manifest":
        forged = replace(receipt, manifest_id="foreign-manifest")
    elif defect == "scope":
        forged = replace(receipt, requested_state_ids=(0,))
    elif defect == "studio":
        forged = replace(receipt, studio=replace(case.studio, version="99"))
    else:
        forged = receipt.confirmed(
            evidence=case.evidence, observed_state_ids=(0, 1), observed_variables=("displacement",)
        )
    assert adapter.confirm(forged, case.evidence).status is PreviewStatus.FAILED
    assert case.observations == 0
    # Invalid caller input must not revoke the real pending receipt.
    assert adapter.confirm(receipt, case.evidence).status is PreviewStatus.CONFIRMED


def test_receipt_cannot_be_confirmed_by_an_adapter_that_never_launched_it(tmp_path: Path) -> None:
    case = PreviewCase(tmp_path)
    receipt = case.adapter().request(case.manifest, case.request)
    assert case.adapter().confirm(receipt, case.evidence).status is PreviewStatus.FAILED
    assert case.observations == 0


def test_duplicate_request_identity_does_not_launch_twice(tmp_path: Path) -> None:
    case = PreviewCase(tmp_path)
    adapter = case.adapter()
    adapter.request(case.manifest, case.request)
    with pytest.raises(PortError):
        adapter.request(case.manifest, case.request)
    assert case.launches == 1


@pytest.mark.parametrize("defect", ["studio", "artifact", "launch"])
def test_observation_must_identify_the_launched_tool_artifact_and_invocation(
    tmp_path: Path, defect: str
) -> None:
    case = PreviewCase(tmp_path)

    def observe(target: module.PreviewBinding) -> module.PreviewObservation:
        observation = case.observe(target)
        binding = observation.binding
        assert binding is not None
        binding = (
            replace(binding, studio=replace(case.studio, version="99"))
            if defect == "studio"
            else replace(binding, xplt_digest="9" * 64)
            if defect == "artifact"
            else replace(binding, launch_id="foreign-launch")
        )
        return replace(observation, binding=binding)

    adapter = case.adapter(observer=observe)
    receipt = adapter.request(case.manifest, case.request)
    assert adapter.confirm(receipt, case.evidence).status is PreviewStatus.FAILED


@pytest.mark.parametrize("phase", ["launch", "observation", "after-confirmation"])
def test_artifact_mutation_is_not_confirmed(tmp_path: Path, phase: str) -> None:
    case = PreviewCase(tmp_path)

    def launch(target: module.PreviewBinding) -> module.PreviewLaunchResult:
        result = case.launch(target)
        if phase == "launch":
            case.path.write_bytes(b"mutated during launch")
        return result

    def observe(target: module.PreviewBinding) -> module.PreviewObservation:
        result = case.observe(target)
        if phase == "observation":
            case.path.write_bytes(b"mutated during observation")
        return result

    adapter = case.adapter(launcher=launch, observer=observe)
    receipt = adapter.request(case.manifest, case.request)
    if phase == "launch":
        assert receipt.status is PreviewStatus.FAILED
        return
    confirmed = adapter.confirm(receipt, case.evidence)
    if phase == "after-confirmation":
        assert confirmed.status is PreviewStatus.CONFIRMED
        case.path.write_bytes(b"mutated after confirmation")
        confirmed = adapter.confirm(confirmed, case.evidence)
    assert confirmed.status is PreviewStatus.FAILED


@pytest.mark.parametrize("missing", ["observer", "evidence"])
def test_missing_confirmation_authority_is_not_confirmed(tmp_path: Path, missing: str) -> None:
    case = PreviewCase(tmp_path)
    adapter = case.adapter(observer=None) if missing == "observer" else case.adapter()
    receipt = adapter.request(case.manifest, case.request)
    with pytest.raises(PortError) as caught:
        adapter.confirm(receipt, () if missing == "evidence" else case.evidence)
    assert caught.value.category is (
        PortErrorCategory.UNSUPPORTED_CAPABILITY
        if missing == "observer"
        else PortErrorCategory.INTEGRITY
    )
    assert case.observations == 0


def test_caller_evidence_must_be_the_observers_bound_evidence(tmp_path: Path) -> None:
    case = PreviewCase(tmp_path)
    adapter = case.adapter()
    receipt = adapter.request(case.manifest, case.request)
    stale = (replace(case.evidence[0], reference="foreign-observation", content_digest="9" * 64),)
    assert adapter.confirm(receipt, stale).status is PreviewStatus.FAILED


def test_observed_failure_cannot_be_erased_by_restoring_bytes(tmp_path: Path) -> None:
    case = PreviewCase(tmp_path)
    adapter = case.adapter()
    receipt = adapter.request(case.manifest, case.request)
    confirmed = adapter.confirm(receipt, case.evidence)
    case.path.write_bytes(b"changed")
    assert adapter.confirm(confirmed, case.evidence).status is PreviewStatus.FAILED
    case.path.write_bytes(case.content)
    assert adapter.confirm(confirmed, case.evidence).status is PreviewStatus.FAILED


def test_same_bytes_at_another_path_are_not_the_launched_target(tmp_path: Path) -> None:
    case = PreviewCase(tmp_path)
    adapter = case.adapter()
    receipt = adapter.request(case.manifest, case.request)
    other = tmp_path / "other.xplt"
    other.write_bytes(case.content)
    adapter.source = module.FileSystemPreviewSource(other)
    assert adapter.confirm(receipt, case.evidence).status is PreviewStatus.FAILED
    assert case.observations == 0


def test_initial_artifact_mismatch_never_calls_launcher(tmp_path: Path) -> None:
    case = PreviewCase(tmp_path)
    case.path.write_bytes(b"not registered")
    with pytest.raises(PortError) as caught:
        case.adapter().request(case.manifest, case.request)
    assert caught.value.category is PortErrorCategory.INTEGRITY
    assert case.launches == 0


def test_observer_cannot_overwrite_a_concurrent_artifact_invalidation(tmp_path: Path) -> None:
    case = PreviewCase(tmp_path)

    def observe(binding: module.PreviewBinding) -> module.PreviewObservation:
        case.path.write_bytes(b"changed while observation is pending")
        assert adapter.confirm(receipt, case.evidence).status is PreviewStatus.FAILED
        case.path.write_bytes(case.content)
        return case.observe(binding)

    adapter = case.adapter(observer=observe)
    receipt = adapter.request(case.manifest, case.request)
    assert adapter.confirm(receipt, case.evidence).status is PreviewStatus.FAILED


class ReentrantSource:
    """One-shot synchronous callbacks around truthful disposable-file reads."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self.callback: Callable[[], None] | None = None
        self.phase = "read"
        self.path_calls = 0

    def _call(self) -> None:
        callback, self.callback = self.callback, None
        if callback is not None:
            callback()

    @property
    def path(self) -> Path:
        self.path_calls += 1
        if self.phase == "path" or (self.phase == "post-path" and self.path_calls == 2):
            self._call()
        return self._path

    def read(self) -> bytes:
        if self.phase == "read":
            self._call()
        return self._path.read_bytes()


def test_confirmed_source_read_retains_nested_invalidation(tmp_path: Path) -> None:
    case = PreviewCase(tmp_path)
    source = ReentrantSource(case.path)
    adapter = case.adapter(source=source)
    receipt = adapter.request(case.manifest, case.request)
    confirmed = adapter.confirm(receipt, case.evidence)
    assert confirmed.status is PreviewStatus.CONFIRMED
    nested: list[PreviewReceipt] = []

    def invalidate() -> None:
        case.path.write_bytes(b"changed during confirmed source read")
        nested.append(adapter.confirm(confirmed, case.evidence))
        case.path.write_bytes(case.content)

    source.callback = invalidate
    outer = adapter.confirm(confirmed, case.evidence)
    assert len(nested) == 1 and nested[0].status is PreviewStatus.FAILED
    assert adapter.confirm(confirmed, case.evidence).status is PreviewStatus.FAILED
    assert outer.status is PreviewStatus.FAILED
    assert not outer.confirmation_evidence
    assert case.observations == 1


def test_initial_source_read_reserves_identity_before_nested_request(tmp_path: Path) -> None:
    case = PreviewCase(tmp_path)
    source = ReentrantSource(case.path)
    adapter = case.adapter(source=source)
    conflicts: list[PortError] = []

    def request_again() -> None:
        try:
            adapter.request(case.manifest, case.request)
        except PortError as error:
            conflicts.append(error)

    source.callback = request_again
    receipt = adapter.request(case.manifest, case.request)
    assert case.launches == 1
    assert len(conflicts) == 1 and conflicts[0].category is PortErrorCategory.CONFLICT
    assert receipt.status is PreviewStatus.LAUNCHED
    confirmed = adapter.confirm(
        decode_record(encode_record(receipt), PreviewReceipt), case.evidence
    )
    assert confirmed.status is PreviewStatus.CONFIRMED
    assert adapter.confirm(confirmed, case.evidence) == confirmed
    assert case.observations == 1


@pytest.mark.parametrize("phase", ["path", "post-path"])
def test_source_path_callbacks_cannot_launch_same_identity_twice(
    tmp_path: Path, phase: str
) -> None:
    case = PreviewCase(tmp_path)
    source = ReentrantSource(case.path)
    source.phase = phase
    adapter = case.adapter(source=source)

    def request_again() -> None:
        with pytest.raises(PortError) as caught:
            adapter.request(case.manifest, case.request)
        assert caught.value.category is PortErrorCategory.CONFLICT

    source.callback = request_again
    assert adapter.request(case.manifest, case.request).status is PreviewStatus.LAUNCHED
    assert case.launches == 1


@pytest.mark.parametrize("phase", ["path", "read", "post-path"])
@pytest.mark.parametrize("already_confirmed", [False, True])
def test_source_callback_invalidation_prevents_any_confirmation_success(
    tmp_path: Path, phase: str, already_confirmed: bool
) -> None:
    case = PreviewCase(tmp_path)
    source = ReentrantSource(case.path)
    adapter = case.adapter(source=source)
    receipt = adapter.request(case.manifest, case.request)
    if already_confirmed:
        receipt = adapter.confirm(receipt, case.evidence)
    observations = case.observations

    def invalidate() -> None:
        case.path.write_bytes(b"changed during source callback")
        assert adapter.confirm(receipt, case.evidence).status is PreviewStatus.FAILED
        case.path.write_bytes(case.content)

    source.phase, source.path_calls, source.callback = phase, 0, invalidate
    failed = adapter.confirm(receipt, case.evidence)
    assert failed.status is PreviewStatus.FAILED
    assert not failed.confirmation_evidence
    assert case.observations == observations


def test_launcher_reentrancy_and_failed_initial_read_keep_identity_reserved(tmp_path: Path) -> None:
    case = PreviewCase(tmp_path)

    def launch(binding: module.PreviewBinding) -> module.PreviewLaunchResult:
        with pytest.raises(PortError) as caught:
            adapter.request(case.manifest, case.request)
        assert caught.value.category is PortErrorCategory.CONFLICT
        return case.launch(binding)

    adapter = case.adapter(launcher=launch)
    assert adapter.request(case.manifest, case.request).status is PreviewStatus.LAUNCHED
    assert case.launches == 1
    failed_request = replace(case.request, preview_id="initial-read-failure")
    case.path.write_bytes(b"not registered")
    with pytest.raises(PortError) as caught:
        adapter.request(case.manifest, failed_request)
    assert caught.value.category is PortErrorCategory.INTEGRITY
    case.path.write_bytes(case.content)
    with pytest.raises(PortError) as caught:
        adapter.request(case.manifest, failed_request)
    assert caught.value.category is PortErrorCategory.CONFLICT
    assert case.launches == 1
