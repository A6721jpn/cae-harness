"""Synthetic launcher/observer binding checks; never a Studio or Computer Use run."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from febio_cae.adapters.preview import studio as module
from febio_cae.domain import (
    EvidenceRef,
    FileEntry,
    OutputObservation,
    PortError,
    PreviewRequest,
    PreviewStatus,
    ReadResult,
    ReadStatus,
    ResultManifest,
    ToolIdentity,
)

from .fixtures import WORLD


@dataclass(frozen=True)
class _TaggedObservation(module.PreviewObservation):
    binding: Any = None


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
        self.binding: Any = None

    def target(self, args: tuple[Any, ...]) -> Any:
        if len(args) == 1:
            return args[0]
        # RED-only bridge: the old callback accepted path/tool without a binding.
        return SimpleNamespace(
            launch_id="synthetic-launch",
            receipt_id=self.request.preview_id,
            manifest_id=self.manifest.manifest_id,
            path=args[0],
            studio=args[1],
            xplt_digest=self.digest,
        )

    def launch(self, *args: Any) -> Any:
        self.launches += 1
        self.binding = self.target(args)
        result_type = getattr(module, "PreviewLaunchResult", None)
        if result_type is None:
            return SimpleNamespace(binding=self.binding, launched=True)
        return result_type(self.binding, True)

    def observe(self, *args: Any) -> Any:
        self.observations += 1
        binding = self.target(args)
        if hasattr(module, "PreviewBinding"):
            return module.PreviewObservation((0, 1), ("displacement",), binding)
        return _TaggedObservation((0, 1), ("displacement",), binding)

    def adapter(self, **overrides: Any) -> module.PreviewAdapter:
        options = dict(
            studio=self.studio,
            source=module.FileSystemPreviewSource(self.path),
            launcher=self.launch,
            observer=self.observe,
        )
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

    def launch(*args: Any) -> Any:
        result = case.launch(*args)
        binding = result.binding
        if defect == "foreign-studio":
            tool = replace(case.studio, version="99")
            binding = (
                SimpleNamespace(**(vars(binding) | {"studio": tool}))
                if isinstance(binding, SimpleNamespace)
                else replace(binding, studio=tool)
            )
        if isinstance(result, SimpleNamespace):
            return SimpleNamespace(binding=binding, launched=defect != "not-started")
        return replace(result, binding=binding, launched=defect != "not-started")

    assert (
        case.adapter(launcher=launch).request(case.manifest, case.request).status
        is PreviewStatus.FAILED
    )


@pytest.mark.parametrize("defect", ["manifest", "scope", "confirmed"])
def test_caller_cannot_edit_or_self_confirm_an_issued_receipt(tmp_path: Path, defect: str) -> None:
    case = PreviewCase(tmp_path)
    adapter = case.adapter()
    receipt = adapter.request(case.manifest, case.request)
    if defect == "manifest":
        forged = replace(receipt, manifest_id="foreign-manifest")
    elif defect == "scope":
        forged = replace(receipt, requested_state_ids=(0,))
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

    def observe(*args: Any) -> Any:
        observation = case.observe(*args)
        binding = observation.binding
        field, value = {
            "studio": ("studio", replace(case.studio, version="99")),
            "artifact": ("xplt_digest", "9" * 64),
            "launch": ("launch_id", "foreign-launch"),
        }[defect]
        if isinstance(binding, SimpleNamespace):
            binding = SimpleNamespace(**(vars(binding) | {field: value}))
        else:
            binding = replace(binding, **{field: value})
        return replace(observation, binding=binding)

    adapter = case.adapter(observer=observe)
    receipt = adapter.request(case.manifest, case.request)
    assert adapter.confirm(receipt, case.evidence).status is PreviewStatus.FAILED


@pytest.mark.parametrize("phase", ["launch", "observation", "after-confirmation"])
def test_artifact_mutation_is_not_confirmed(tmp_path: Path, phase: str) -> None:
    case = PreviewCase(tmp_path)

    def launch(*args: Any) -> Any:
        result = case.launch(*args)
        if phase == "launch":
            case.path.write_bytes(b"mutated during launch")
        return result

    def observe(*args: Any) -> Any:
        result = case.observe(*args)
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
