# mypy: disable-error-code="no-redef,assignment,misc"

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

try:
    from febio_cae.domain.artifacts import GeometrySelectionRequest, SourceAssetContent
    from febio_cae.domain.ports import ResultDataPort, SourceAssetResolverPort
    from febio_cae.domain.results import NumericResultData, ResultDataRef

    REPAIR_PORTS_API: Any = True
except ImportError:
    GeometrySelectionRequest = None
    SourceAssetContent = None
    ResultDataPort = None
    SourceAssetResolverPort = None
    NumericResultData = None
    ResultDataRef = None
    REPAIR_PORTS_API: Any = None


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


def test_ports_expose_injected_source_selection_and_result_data_resolution() -> None:
    assert REPAIR_PORTS_API is not None, "P1 source/result resolution ports are not available"
    assert callable(getattr(SourceAssetResolverPort, "resolve"))
    assert callable(getattr(GeometryPort, "resolve_selection"))
    assert callable(getattr(ResultDataPort, "resolve"))
    assert callable(getattr(ResultDataPort, "resolve_manifest_output"))
    assert "source" in inspect.signature(GeometryPort.inspect).parameters
    assert "data" in inspect.signature(QualityPort.assess).parameters


def test_connected_synthetic_consumer_reads_source_and_actual_numeric_states() -> None:
    assert REPAIR_PORTS_API is not None, "P1 source/result resolution ports are not available"
    import hashlib

    from febio_cae.domain import (
        BodyId,
        EvidenceRef,
        FrameId,
        NamedAttributeRule,
        SelectionRef,
    )

    raw_source = b"registered-source"
    source_ref = __import__("febio_cae.domain", fromlist=["SourceAssetRef"]).SourceAssetRef(
        "source-interface",
        hashlib.sha256(raw_source).hexdigest(),
        "model/step",
    )

    class SourceResolver:
        def resolve(self, asset: Any) -> Any:
            return SourceAssetContent(asset, raw_source)

    source_resolver = SourceResolver()
    assert isinstance(source_resolver, SourceAssetResolverPort)
    resolved_source = source_resolver.resolve(source_ref)
    assert resolved_source.content == raw_source

    selection = SelectionRef(
        "part-selection",
        "support",
        EvidenceRef("1", "registered_document", "source:1", "selection.role", "e" * 64),
        "a" * 64,
        BodyId("part-body"),
        FrameId("World"),
        NamedAttributeRule("support-face"),
    )
    geometry_request = GeometrySelectionRequest(source_ref, selection)
    assert geometry_request.selection is selection

    data_ref = ResultDataRef(
        "displacement-data", "f" * 64, "numeric-result-v1", "output/case.xplt"
    )
    numeric = NumericResultData(
        reference=data_ref,
        output_id="displacement",
        value_type="VEC3F",
        unit="m",
        frame=FrameId("World"),
        state_ids=(0, 1),
        state_coordinates=(0.0, 1.0),
        values=((0.0, 0.0, 0.0), (0.2, 0.0, 0.0)),
    )

    class ResultResolver:
        def resolve(self, reference: Any) -> Any:
            assert reference == data_ref
            return numeric

        def resolve_manifest_output(self, manifest_id: str, output_id: str) -> Any:
            assert manifest_id == "manifest-interface"
            assert output_id == "displacement"
            return numeric

    result_resolver = ResultResolver()
    assert isinstance(result_resolver, ResultDataPort)
    assert result_resolver.resolve(data_ref).values[1][0] == 0.2
    assert result_resolver.resolve_manifest_output("manifest-interface", "displacement").state_ids == (
        0,
        1,
    )
