"""Synthetic same-session preview contract; no Studio or desktop invocation."""

from dataclasses import replace
from pathlib import Path

import pytest

from febio_cae.adapters.preview import studio as module
from febio_cae.domain import PortError, PreviewStatus

from .test_preview_binding import PreviewCase


def session(case: PreviewCase) -> module.ExistingStudioSession:
    return module.ExistingStudioSession(case.studio, 123, "process-start-123", 456)


def test_existing_session_issues_requested_without_launch_and_confirms(tmp_path: Path) -> None:
    case = PreviewCase(tmp_path)
    current = session(case)
    bindings: list[module.PreviewBinding] = []

    def observe(binding: module.PreviewBinding) -> module.PreviewObservation:
        bindings.append(binding)
        return replace(case.observe(binding), existing_session=current)

    adapter = case.adapter(session_probe=lambda expected: current, observer=observe)
    receipt = adapter.request_observation(
        case.manifest, case.request, session=current, timeout_seconds=60
    )
    assert receipt.status is PreviewStatus.REQUESTED
    assert case.launches == case.observations == 0
    confirmed = adapter.confirm(receipt, case.evidence)
    assert confirmed.status is PreviewStatus.CONFIRMED
    assert bindings[0].existing_session == current and bindings[0].launch_id
    assert case.launches == 0 and case.observations == 1
    assert case.adapter().confirm(receipt, case.evidence).status is PreviewStatus.FAILED
    assert (
        adapter.confirm(replace(receipt, requested_state_ids=(0,)), case.evidence).status
        is PreviewStatus.FAILED
    )
    with pytest.raises(PortError):
        adapter.request_observation(
            case.manifest, case.request, session=current, timeout_seconds=60
        )


@pytest.mark.parametrize("defect", ["probe", "session", "variable", "nonce", "artifact"])
def test_existing_session_confirmation_fails_closed(tmp_path: Path, defect: str) -> None:
    case = PreviewCase(tmp_path)
    current = session(case)
    probe_value = current

    def observe(binding: module.PreviewBinding) -> module.PreviewObservation:
        nonlocal probe_value
        value = replace(case.observe(binding), existing_session=current)
        if defect == "probe":
            probe_value = replace(current, process_start_marker="reused-pid")
        elif defect == "session":
            value = replace(value, existing_session=replace(current, window_id=999))
        elif defect == "variable":
            value = replace(value, variables=())
        elif defect == "nonce":
            value = replace(value, binding=replace(binding, launch_id="old-nonce"))
        else:
            case.path.write_bytes(b"changed")
        return value

    adapter = case.adapter(session_probe=lambda expected: probe_value, observer=observe)
    receipt = adapter.request_observation(
        case.manifest, case.request, session=current, timeout_seconds=60
    )
    assert adapter.confirm(receipt, case.evidence).status is PreviewStatus.FAILED
    case.path.write_bytes(case.content)
    probe_value = current
    assert adapter.confirm(receipt, case.evidence).status is PreviewStatus.FAILED
    assert case.observations == 1 and case.launches == 0


def test_existing_session_requires_probe_and_consumes_failed_identity(tmp_path: Path) -> None:
    case = PreviewCase(tmp_path)
    current = session(case)
    adapter = case.adapter()
    receipt = adapter.request_observation(
        case.manifest, case.request, session=current, timeout_seconds=60
    )
    assert receipt.status is PreviewStatus.FAILED
    assert adapter.confirm(receipt, case.evidence).status is PreviewStatus.FAILED
    with pytest.raises(PortError):
        adapter.request_observation(
            case.manifest, case.request, session=current, timeout_seconds=60
        )
    assert case.launches == case.observations == 0


def test_existing_session_deadline_rechecked_after_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = PreviewCase(tmp_path)
    current = session(case)
    now = [1.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])

    def observe(binding: module.PreviewBinding) -> module.PreviewObservation:
        now[0] = 62.0
        return replace(case.observe(binding), existing_session=current)

    adapter = case.adapter(session_probe=lambda expected: current, observer=observe)
    receipt = adapter.request_observation(
        case.manifest, case.request, session=current, timeout_seconds=60
    )
    assert adapter.confirm(receipt, case.evidence).status is PreviewStatus.FAILED


def test_existing_session_probe_reentrant_invalidation_survives_restored_bytes(
    tmp_path: Path,
) -> None:
    case = PreviewCase(tmp_path)
    current = session(case)
    receipt = None
    nested = False

    def probe(expected: module.ExistingStudioSession) -> module.ExistingStudioSession:
        nonlocal nested
        if receipt is not None and not nested:
            nested = True
            case.path.write_bytes(b"changed")
            assert adapter.confirm(receipt, case.evidence).status is PreviewStatus.FAILED
            case.path.write_bytes(case.content)
        return current

    adapter = case.adapter(session_probe=probe)
    receipt = adapter.request_observation(
        case.manifest, case.request, session=current, timeout_seconds=60
    )
    assert adapter.confirm(receipt, case.evidence).status is PreviewStatus.FAILED
    assert case.observations == case.launches == 0
