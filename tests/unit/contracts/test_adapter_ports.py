from __future__ import annotations

import inspect
from typing import Any, get_type_hints

import pytest

try:
    from febio_cae.domain.ports import (
        CaseRegistryPort,
        CompatibilityRegistryPort,
        CompilerPort,
        GeometryPort,
        MeshingPort,
        OwnershipPort,
        PortError,
        PortErrorCategory,
        PreviewPort,
        QualityPort,
        ResultReaderPort,
        RunnerPort,
        TrustedOwnerContext,
    )
except ImportError:
    CaseRegistryPort: Any = None
    CompatibilityRegistryPort: Any = None
    CompilerPort: Any = None
    GeometryPort: Any = None
    MeshingPort: Any = None
    OwnershipPort: Any = None
    PortError: Any = None
    PortErrorCategory: Any = None
    PreviewPort: Any = None
    QualityPort: Any = None
    ResultReaderPort: Any = None
    RunnerPort: Any = None
    TrustedOwnerContext: Any = None


def _require_ports() -> None:
    if PortError is None:
        pytest.skip("P1 typed adapter ports are not implemented")


def test_typed_port_api_is_available() -> None:
    assert PortError is not None, "P1 typed adapter ports are not available"
    assert all(
        port is not None
        for port in (
            GeometryPort,
            MeshingPort,
            CompilerPort,
            RunnerPort,
            ResultReaderPort,
            QualityPort,
            PreviewPort,
            CaseRegistryPort,
            CompatibilityRegistryPort,
            OwnershipPort,
        )
    )


def test_ports_have_typed_methods_without_approval_boolean() -> None:
    _require_ports()
    assert "approved" not in inspect.signature(RunnerPort.start).parameters
    assert "ready" not in inspect.signature(CompilerPort.compile).parameters
    hints = get_type_hints(RunnerPort.start)
    assert hints["owner"] is TrustedOwnerContext
    assert PortErrorCategory.UNSUPPORTED_CAPABILITY.value == "unsupported_capability"


def test_owner_context_is_scoped_and_not_a_public_ready_claim() -> None:
    _require_ports()
    context = TrustedOwnerContext(
        case_id="case-interface",
        run_id="run-interface",
        attempt_id="attempt-interface",
        owner_generation=4,
    )
    assert context.owner_generation == 4
    assert not hasattr(context, "approved")
    assert not hasattr(context, "ready")


def test_runner_exposes_poll_cancel_reconcile_boundaries() -> None:
    _require_ports()
    for method in ("start", "poll", "cancel", "reconcile"):
        assert callable(getattr(RunnerPort, method))
