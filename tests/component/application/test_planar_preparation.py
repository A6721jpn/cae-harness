"""Public preparation boundary, using only isolated synthetic dependencies."""

from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
from typing import Any

import pytest

from febio_cae.cli.main import main


@pytest.fixture
def prepared_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, ...]:
    from test_persistence_authority import _profile

    from febio_cae.application.service import RegisteredCaseService
    from febio_cae.domain import EvidenceRef, Quantity
    from febio_cae.storage.mesh_quality import MeshQualityRegistration

    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    fixtures = importlib.import_module("geometry.conftest")
    step = b"ISO-10303-21; HEADER; FILE_SCHEMA(('AUTOMOTIVE_DESIGN')); ENDSEC; DATA; ENDSEC; END-ISO-10303-21;"
    cad = tmp_path / "input.step"
    cad.write_bytes(step)
    service = RegisteredCaseService(state_dir=tmp_path / "state")
    created = service.create_case(case_root=tmp_path / "case", cad_path=cad)
    raw = fixtures.make_case_spec("0" * 64).to_dict()
    digest = hashlib.sha256(step).hexdigest()

    def explicit(value: Any) -> None:
        if isinstance(value, dict):
            if {"reference", "target_field", "content_digest"} <= value.keys():
                value.update(reference="cad", content_digest=digest)
            for child in value.values():
                explicit(child)
            if value.get("body_id") == "part-body" and "geometry_digest" in value:
                value["geometry_digest"] = None
        elif isinstance(value, list):
            for child in value:
                explicit(child)

    explicit(raw)
    raw["geometry"].update(source_step_digest=digest, inspection_digest=None)
    for field, name in (
        ("solver_policy", "solver"),
        ("outputs", "outputs"),
        ("quality_policy", "quality"),
    ):
        profile = _profile(name)
        service.register_profile(profile)
        raw[field]["profile"]["profile_id"] = name
        raw[field]["profile"]["record_digest"] = hashlib.sha256(profile.to_bytes()).hexdigest()
    quality = MeshQualityRegistration(
        "prepared-mesh",
        Quantity(0.01, "mm"),
        ("box",),
        "synthetic",
        "1",
        "synthetic",
        (EvidenceRef("1", "registered_document", "cad", "mesh_quality.qualification", digest),),
    )
    raw["mesh_policy"]["quality_profile"] = service.register_mesh_quality(
        created.case_id, quality
    ).to_dict()
    request = {"schema_version": "1", "values": raw, "evidence": [], "source_declarations": []}
    return service, created, request, fixtures.SyntheticBackend(), step


def _isolate(monkeypatch: pytest.MonkeyPatch, backend: Any) -> None:
    worker = importlib.import_module("febio_cae.adapters.geometry.preparation")
    application = importlib.import_module("febio_cae.application._preparation")
    backend.evidence = {
        "module": "synthetic-only",
        "module_sha256": "f" * 64,
        "gmsh_version": "4.15.2",
        "occt_version": "8.0.1",
        "build_info": "synthetic injected backend",
    }
    monkeypatch.setattr(worker, "_make_backend", lambda cpu: backend)
    monkeypatch.setattr(
        application,
        "run_preparation",
        lambda source, request, limits, directory: worker.produce(source, request, limits),
    )


def test_current_operation_publishes_bound_preparation(
    prepared_input: tuple[Any, ...],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service, created, request, backend, step = prepared_input
    _isolate(monkeypatch, backend)
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    assert (
        main(
            [
                "case",
                "--state-dir",
                str(tmp_path / "state"),
                "prepare-planar",
                created.case_id,
                "--file",
                str(request_path),
                "--expected-generation",
                "0",
                "--json",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "PREPARED"
    assert len(backend.mesh_requests) == 1
    assert service.resolve_source(created.case_id, "cad").content == step
    revision = service.get_revision(created.case_id, result["revision_id"])
    storage = service._storage(created.case_id)
    registration = storage.resolve_revision_mesh_quality(revision)
    mesh = service._planar_execution_mesh(storage, registration, revision)
    assert mesh.nodes and mesh.elements
    assert any(
        q.metric_id == "surface-approximation" and q.status == "UNVERIFIED"
        for q in mesh.quality_records
    )
    assert revision.spec.geometry.geometry_digest == backend.geometry_digest
    assert service.validate_case(created.case_id).status == "VALIDATED"


@pytest.mark.parametrize("bad", ["source", "geometry", "version"])
def test_preparation_refuses_source_or_backend_mismatch(
    prepared_input: tuple[Any, ...], monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    service, created, request, backend, _ = prepared_input
    _isolate(monkeypatch, backend)
    if bad == "source":
        request["values"]["geometry"]["source_step_digest"] = "f" * 64
    elif bad == "geometry":
        request["values"]["geometry"]["geometry_digest"] = "e" * 64
    else:
        backend.evidence["occt_version"] = "7.9.1"
    with pytest.raises((ValueError, RuntimeError)):
        service.prepare_planar(created.case_id, request, expected_generation=0)
    assert not backend.mesh_requests


def test_freeze_without_publication_cannot_supply_execution_mesh(
    prepared_input: tuple[Any, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    service, created, request, backend, _ = prepared_input
    _isolate(monkeypatch, backend)
    records = importlib.import_module("febio_cae.storage.preparation")

    def fail(*args: Any, **kwargs: Any) -> None:
        raise OSError("injected final publication failure")

    monkeypatch.setattr(records.PreparationStore, "publish", fail)
    with pytest.raises(RuntimeError, match="publication"):
        service.prepare_planar(created.case_id, request, expected_generation=0)
    record = records.PreparationStore(service._storage(created.case_id)).latest(created.case_id)
    assert record["status"] == "FAILED" and record["revision_id"]
    revision = service.get_revision(created.case_id, record["revision_id"])
    storage = service._storage(created.case_id)
    registration = storage.resolve_revision_mesh_quality(revision)
    with pytest.raises((ValueError, RuntimeError), match="PREPARED"):
        service._planar_execution_mesh(storage, registration, revision)
    with pytest.raises((ValueError, RuntimeError), match="PREPARED"):
        service.run_demo(
            created.case_id, revision.revision_id, executable="never-opened.exe", preflight=True
        )


def test_public_prepare_reports_invalid_request_without_native_start(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    request = tmp_path / "request.json"
    request.write_text("{}", encoding="utf-8")
    assert (
        main(
            [
                "case",
                "--state-dir",
                str(tmp_path / "state"),
                "prepare-planar",
                "missing",
                "--file",
                str(request),
                "--expected-generation",
                "0",
                "--json",
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().out)["status"] == "INVALID_INPUT"


def test_preparation_uses_finite_owned_process_deadline(tmp_path: Path) -> None:
    module = importlib.import_module("febio_cae.adapters.geometry.preparation")
    # A Python-only child that never enters native code must still be stopped.
    import sys
    import time

    started = time.monotonic()
    with pytest.raises(TimeoutError):
        module._run_owned(
            (sys.executable, "-I", "-c", "import time; time.sleep(60)"),
            tmp_path,
            timeout_seconds=0.15,
            memory_bytes=128 * 1024 * 1024,
        )
    assert time.monotonic() - started < 5
