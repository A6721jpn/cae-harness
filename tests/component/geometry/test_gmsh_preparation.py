"""Synthetic admission evidence only; no native modules or mesh quality claims."""

from typing import Any

import pytest

from febio_cae.adapters.geometry import (
    BackendError,
    BackendErrorCategory,
    GmshOCCBackend,
    GmshOCCConfig,
)

from .test_gmsh_units import _FakeGmsh


def _source(header: str = "FILE_SCHEMA(('AUTOMOTIVE_DESIGN'));") -> bytes:
    return (
        "ISO-10303-21; HEADER; "
        + header
        + " ENDSEC; DATA; #1=SI_UNIT($,.METRE.); ENDSEC; END-ISO-10303-21;"
    ).encode("ascii")


def _backend(monkeypatch: pytest.MonkeyPatch, **settings: Any) -> tuple[GmshOCCBackend, Any]:
    fake = _FakeGmsh()

    def get_string(name: str) -> str:
        fake.events.append(("getString", name))
        return "Version: 4.15.2; Build options: OpenCASCADE; OCC version: 8.0.1;"

    monkeypatch.setattr(fake.option, "getString", get_string, raising=False)
    monkeypatch.setattr(
        fake.option,
        "setNumber",
        lambda name, value: fake.events.append(("setNumber", name, value)),
    )
    monkeypatch.setattr(
        "febio_cae.adapters.geometry.gmsh_occ.importlib.import_module", lambda name: fake
    )
    backend = GmshOCCBackend(GmshOCCConfig(**settings))
    # Admission is the boundary under test; existing meshing logic is unchanged.
    monkeypatch.setattr(backend, "_mesh_context", lambda *args: "mesh-admitted")
    return backend, fake


def _run(backend: GmshOCCBackend, operation: str, content: bytes) -> Any:
    if operation == "inspect":
        return backend.inspect(content, ())
    return backend.mesh(content, "body-1", 0.001)


def test_optional_configuration_defaults_and_strict_values() -> None:
    config = GmshOCCConfig()
    assert config.expected_occt_version is None
    assert config.require_step_ap214 is False
    assert config.cpu_workers is None
    for field, values in (
        ("expected_occt_version", [False, 8, "", " 8.0.1", "8.0.1\n", "8\x7f"]),
        ("require_step_ap214", [None, 0, "true"]),
        ("cpu_workers", [True, 0, -1, 1.5, "2"]),
    ):
        for value in values:
            settings: dict[str, Any] = {field: value}
            with pytest.raises(ValueError):
                GmshOCCConfig(**settings)


@pytest.mark.parametrize("operation", ["inspect", "mesh"])
def test_configured_preparation_precedes_import_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    backend, fake = _backend(
        monkeypatch, expected_occt_version="8.0.1", require_step_ap214=True, cpu_workers=2
    )
    for schema in ("AUTOMOTIVE_DESIGN", "AUTOMOTIVE_DESIGN { 1 0 10303 214 3 1 1 }"):
        fake.events.clear()
        result = _run(backend, operation, _source(f"/* comment */ FILE_SCHEMA(('{schema}'));"))
        assert result is not None
        events = fake.events
        imported = next(i for i, event in enumerate(events) if event[0] == "importShapes")
        assert 0 < events.index(("getString", "General.BuildInfo")) < imported
        assert 0 < events.index(("setNumber", "General.NumThreads", 2)) < imported
        assert events[0] == ("initialize",)
        assert events[-2:] == [("clear",), ("finalize",)]


@pytest.mark.parametrize("operation", ["inspect", "mesh"])
def test_ap214_refusals_happen_before_module_loading(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    backend = GmshOCCBackend(GmshOCCConfig(require_step_ap214=True))

    def forbidden(name: str) -> Any:
        pytest.fail("invalid AP214 input loaded a module")

    monkeypatch.setattr("febio_cae.adapters.geometry.gmsh_occ.importlib.import_module", forbidden)
    for content in (
        b"",
        _source("/* FILE_SCHEMA(('AUTOMOTIVE_DESIGN')); */"),
        _source("FILE_DESCRIPTION(('FILE_SCHEMA((''AUTOMOTIVE_DESIGN''));'),'2;1');"),
        _source("").replace(b"DATA;", b"DATA; FILE_SCHEMA(('AUTOMOTIVE_DESIGN'));"),
        _source("FILE_SCHEMA(('AUTOMOTIVE_DESIGN')); FILE_SCHEMA(('OTHER'));"),
        _source("FILE_SCHEMA(('AUTOMOTIVE_DESIGN','OTHER'));"),
        _source("FILE_SCHEMA(('AUTOMOTIVE_DESIGN_EXTRA'));"),
        _source("FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 242 3 1 1 }'));"),
        _source("FILE_SCHEMA(('AUTOMOTIVE_DESIGN')); /* unterminated"),
        _source().replace(b"HEADER;", b"DATA;"),
    ):
        with pytest.raises(BackendError):
            _run(backend, operation, content)


@pytest.mark.parametrize("operation", ["inspect", "mesh"])
def test_occt_evidence_refused_before_import_and_session_reusable(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    backend, fake = _backend(monkeypatch, expected_occt_version="8.0.1")
    for evidence in (
        None,
        "Build options: OpenCASCADE;",
        "OCC version: 8.0.10;",
        "OCC version: 8.0.1; OCC version: 8.0.1;",
        "OCC version: 8.0.1; OCC version: 7.9.1;",
        "Build host: OCC version: 8.0.1;",
    ):
        fake.events.clear()
        monkeypatch.setattr(fake.option, "getString", lambda name, evidence=evidence: evidence)
        with pytest.raises(BackendError) as caught:
            _run(backend, operation, _source())
        assert caught.value.category == BackendErrorCategory.UNSUPPORTED_CAPABILITY
        assert not any(event[0] == "importShapes" for event in fake.events)
        assert fake.events[-2:] == [("clear",), ("finalize",)]
    monkeypatch.delattr(fake.option, "getString")
    with pytest.raises(BackendError) as caught:
        _run(backend, operation, _source())
    assert caught.value.category == BackendErrorCategory.UNSUPPORTED_CAPABILITY


def test_unowned_session_does_not_query_or_set_preparation_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, fake = _backend(monkeypatch, expected_occt_version="8.0.1", cpu_workers=2)
    monkeypatch.setattr(fake, "isInitialized", lambda: True)
    with pytest.raises(BackendError, match="unowned"):
        backend.inspect(_source(), ())
    assert fake.events == []
