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
        "occt_version": "7.8.1",
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
    worker = importlib.import_module("febio_cae.adapters.geometry.preparation")
    config = worker._make_backend(2).config
    assert config.expected_version == "4.15.2"
    assert config.expected_occt_version == "7.8.1"
    assert config.require_step_ap214 and config.cpu_workers == 2
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
        for version in ("8.0.1", "7.9.1", None):
            if version is None:
                del backend.evidence["occt_version"]
            else:
                backend.evidence["occt_version"] = version
            with pytest.raises((ValueError, RuntimeError)):
                service.prepare_planar(created.case_id, request, expected_generation=0)
            assert not backend.mesh_requests
        return
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


def test_stale_prepared_generation_is_rejected_before_compilation(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from dataclasses import replace
    from types import SimpleNamespace

    import test_persistence_authority as profiles

    from febio_cae.adapters.febio import xplt_reader
    from febio_cae.application import _demo
    from febio_cae.domain import CompatibilityProfile, PortError, PortErrorCategory, ToolIdentity

    solver = tmp_path / "identity-only.txt"
    solver.write_bytes(b"synthetic identity; never executed")
    original_profile = profiles._profile

    def profile(name: str) -> CompatibilityProfile:
        return replace(
            original_profile(name),
            solver=ToolIdentity("synthetic", "1", hashlib.sha256(solver.read_bytes()).hexdigest()),
            reader=ToolIdentity(
                "synthetic-reader",
                "1",
                hashlib.sha256(Path(xplt_reader.__file__).read_bytes()).hexdigest(),
            ),
        )

    monkeypatch.setattr(profiles, "_profile", profile)
    service, created, payload, backend, _ = request.getfixturevalue("prepared_input")
    _isolate(monkeypatch, backend)
    prepared = service.prepare_planar(created.case_id, payload, expected_generation=0)
    draft = service.current_draft(created.case_id)
    advanced = service.set_spec(
        created.case_id,
        values=draft.values,
        expected_generation=draft.generation,
        evidence=draft.evidence,
        input_intent="new generation awaiting freeze",
    )
    assert advanced.generation == draft.generation + 1
    compiled: list[str] = []

    class Compiler:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def compile(self, current: Any, mesh: Any, profile: Any) -> Any:
            compiled.append(current.revision_id)
            return SimpleNamespace(files=(), to_dict=lambda: {"synthetic_compiler": True})

    monkeypatch.setattr(_demo, "CompilerAdapter", Compiler)
    with pytest.raises(PortError) as error:
        service.run_demo(
            created.case_id, prepared["revision_id"], executable=str(solver), preflight=True
        )
    assert error.value.category is PortErrorCategory.CONFLICT
    code = main(
        [
            "case",
            "--state-dir",
            str(tmp_path / "state"),
            "run-demo",
            created.case_id,
            "--revision-id",
            prepared["revision_id"],
            "--solver",
            str(solver),
            "--preflight",
            "--json",
        ]
    )
    response = json.loads(capsys.readouterr().out)
    assert (code, response["status"]) == (8, "CONFLICT")
    assert not compiled


@pytest.mark.parametrize("route", ["typed", "natural"])
def test_prepared_material_child(
    route: str,
    request: pytest.FixtureRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from dataclasses import replace
    from types import SimpleNamespace

    import test_persistence_authority as profiles

    from febio_cae.adapters.febio import xplt_reader
    from febio_cae.application import _demo
    from febio_cae.domain import CompatibilityProfile, Quantity, ToolIdentity
    from febio_cae.domain.case_patch import CasePatch, CasePatchEdit

    solver = tmp_path / "identity-only.txt"
    solver.write_bytes(b"synthetic identity; never executed")
    original_profile = profiles._profile

    def profile(name: str) -> CompatibilityProfile:
        return replace(
            original_profile(name),
            solver=ToolIdentity("synthetic", "1", hashlib.sha256(solver.read_bytes()).hexdigest()),
            reader=ToolIdentity(
                "synthetic-reader",
                "1",
                hashlib.sha256(Path(xplt_reader.__file__).read_bytes()).hexdigest(),
            ),
        )

    monkeypatch.setattr(profiles, "_profile", profile)
    service, created, payload, backend, _ = request.getfixturevalue("prepared_input")
    _isolate(monkeypatch, backend)
    if route == "natural":
        payload["values"]["budget"].update(max_llm_calls=2, max_llm_tokens=2200)
    prepared = service.prepare_planar(created.case_id, payload, expected_generation=0)
    parent = service.get_revision(created.case_id, prepared["revision_id"])
    storage = service._storage(created.case_id)
    originals = {p: p.read_bytes() for p in (storage.root / "preparation").rglob("*.json")}
    root_bytes = parent.to_bytes()
    compiled: list[str] = []

    class Compiler:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def compile(self, current: Any, mesh: Any, profile: Any) -> Any:
            compiled.append(current.revision_id)
            assert mesh.nodes and mesh.elements
            return SimpleNamespace(files=(), to_dict=lambda: {"synthetic_compiler": True})

    monkeypatch.setattr(_demo, "CompilerAdapter", Compiler)
    monkeypatch.setenv("FEBIO_CAE_STATE_DIR", str(tmp_path / "state"))

    def freeze_patch(revision: Any, edit: Any, evidence: Any) -> Any:
        patch = CasePatch(revision.revision_id, revision.spec_digest, (edit,), (evidence,))
        path = tmp_path / "patch.json"
        path.write_bytes(patch.to_bytes())
        assert (
            main(
                [
                    "case",
                    "patch",
                    created.case_id,
                    "--file",
                    str(path),
                    "--expected-generation",
                    str(service.current_draft(created.case_id).generation),
                    "--json",
                ]
            )
            == 0
        )
        capsys.readouterr()
        assert main(["case", "validate", created.case_id, "--json"]) == 0
        capsys.readouterr()
        assert main(["case", "freeze", created.case_id, "--json"]) == 0
        return service.get_revision(
            created.case_id, json.loads(capsys.readouterr().out)["revision_id"]
        )

    if route == "natural":
        from febio_cae.adapters.llm import openai_responses

        boundary = importlib.import_module("tests.component.autonomy.test_openai_boundary")
        settings_path = tmp_path / "llm.json"
        settings_path.write_text(json.dumps(boundary.settings()), encoding="utf-8")
        statement = "material.youngs_modulus = 2 MPa"

        def http(endpoint: str, body: dict[str, Any], *_: Any) -> dict[str, Any]:
            if endpoint.endswith("input_tokens"):
                return {"input_tokens": 100}
            source = json.loads(body["input"])["sources"][0]
            return boundary.response(
                [
                    {
                        "field": "material.youngs_modulus",
                        "value": "2",
                        "unit": "MPa",
                        "source": source["id"],
                        "clause": statement,
                        "entity": parent.spec.geometry.body_id.value,
                        "scope": "case",
                    }
                ]
            )

        monkeypatch.setattr(openai_responses, "_http", http)
        monkeypatch.setattr(openai_responses, "_resolve_key", lambda _: "private-injected")
        assert (
            main(
                [
                    "case",
                    "edit",
                    created.case_id,
                    "--text",
                    statement,
                    "--expected-generation",
                    str(service.current_draft(created.case_id).generation),
                    "--operation-id",
                    "prepared-E-edit",
                    "--llm-settings",
                    str(settings_path),
                    "--base",
                    parent.revision_id,
                    "--json",
                ]
            )
            == 0
        ), capsys.readouterr().out
        capsys.readouterr()
        assert main(["case", "validate", created.case_id, "--json"]) == 0
        capsys.readouterr()
        assert main(["case", "freeze", created.case_id, "--json"]) == 0
        child = service.get_revision(
            created.case_id, json.loads(capsys.readouterr().out)["revision_id"]
        )
    else:
        child = freeze_patch(
            parent,
            CasePatchEdit(
                "material", replace(parent.spec.material, youngs_modulus=Quantity(2e6, "Pa")), True
            ),
            parent.spec.material.youngs_modulus_evidence,
        )
    assert child.parent_revision_id == parent.revision_id
    args = [
        "case",
        "run-demo",
        created.case_id,
        "--revision-id",
        child.revision_id,
        "--solver",
        str(solver),
        "--preflight",
        "--json",
    ]
    assert main(args) == 0, capsys.readouterr().out
    capsys.readouterr()
    assert compiled == [child.revision_id]
    changed = freeze_patch(
        child,
        CasePatchEdit(
            "mesh_policy", replace(child.spec.mesh_policy, global_size=Quantity(100, "mm")), True
        ),
        replace(
            child.spec.material.youngs_modulus_evidence, target_field="mesh_policy.global_size"
        ),
    )
    args[args.index("--revision-id") + 1] = changed.revision_id
    assert main(args) != 0
    capsys.readouterr()
    assert compiled == [child.revision_id]
    assert len(backend.mesh_requests) == 1
    assert service.get_revision(created.case_id, parent.revision_id).to_bytes() == root_bytes
    assert all(path.read_bytes() == data for path, data in originals.items())


def _mesh_study_request(request: dict[str, Any]) -> dict[str, Any]:
    import copy

    from febio_cae.domain import QualityThreshold, Quantity

    payload = copy.deepcopy(request)
    values = payload["values"]
    values["mesh_policy"]["max_refinements"] = 2
    values["budget"]["max_attempts"] = 4
    criterion = copy.deepcopy(values["quality_policy"]["criteria"][0])
    criterion.update(
        criterion_id="mesh_study",
        metric_id="mesh_dependence",
        thresholds=[
            QualityThreshold(name, Quantity(value, unit)).to_dict()
            for name, value, unit in (
                ("coarse_size", 2.0, "mm"),
                ("refined_size", 1.0, "mm"),
                ("fine_size", 0.5, "mm"),
                ("relative_max", 0.02, "1"),
                ("absolute_floor", 0.001, "N"),
            )
        ],
    )
    criterion["evidence"]["target_field"] = "quality_policy.criteria.mesh_study"
    values["quality_policy"]["criteria"].append(criterion)
    return payload


def test_explicit_refinement_preserves_parent_mesh_and_publishes_new_origins(
    prepared_input: tuple[Any, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    import copy

    service, created, request, backend, _ = prepared_input
    _isolate(monkeypatch, backend)
    payload = _mesh_study_request(request)
    prepared = service.prepare_planar(created.case_id, payload, expected_generation=0)
    storage = service._storage(created.case_id)
    parent = service.get_revision(created.case_id, prepared["revision_id"])
    parent_registration = storage.resolve_revision_mesh_quality(parent)
    parent_mesh = service._planar_execution_mesh(storage, parent_registration, parent)
    immutable = {
        path: path.read_bytes()
        for path in (storage.root / "preparation" / prepared["preparation_id"]).glob("*.json")
    }
    original_parent = parent
    for size in (1.0, 0.5):
        child_request = copy.deepcopy(payload)
        child_request["values"]["mesh_policy"]["global_size"] = {"value": size, "unit": "mm"}
        prepared = service.prepare_planar(
            created.case_id,
            child_request,
            expected_generation=prepared["generation"],
            parent_revision_id=parent.revision_id,
        )
        child = service.get_revision(created.case_id, prepared["revision_id"])
        assert child.parent_revision_id == parent.revision_id
        assert child.parent_spec_digest == parent.spec_digest
        child_registration = storage.resolve_revision_mesh_quality(child)
        assert child_registration.preparation_id != parent_registration.preparation_id
        assert service.validate_case(created.case_id).status == "VALIDATED"
        assert storage.get_revision(created.case_id, original_parent.revision_id) == original_parent
        assert (
            service._planar_execution_mesh(storage, parent_registration, original_parent)
            == parent_mesh
        )
        assert all(path.read_bytes() == content for path, content in immutable.items())
        parent = child


def test_prepared_case_reservations_are_finite_and_not_reset_by_reopening(
    prepared_input: tuple[Any, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    from febio_cae.storage import CaseStorage, StorageConflictError
    from febio_cae.storage.demo_budget import (
        reserve_preparation_mesh_attempt,
        reserve_prepared_solver_attempt,
    )

    service, created, request, backend, _ = prepared_input
    _isolate(monkeypatch, backend)
    prepared = service.prepare_planar(
        created.case_id, _mesh_study_request(request), expected_generation=0
    )
    storage = service._storage(created.case_id)
    revision = service.get_revision(created.case_id, prepared["revision_id"])
    for number in range(4):
        assert reserve_prepared_solver_attempt(
            CaseStorage(storage.root), revision, f"solver-{number}"
        )
    assert not reserve_prepared_solver_attempt(storage, revision, "solver-0")
    with pytest.raises(StorageConflictError):
        reserve_prepared_solver_attempt(storage, revision, "solver-over-budget")
    for number in (2, 3):
        assert reserve_preparation_mesh_attempt(storage, created.case_id, f"mesh-{number}")
    with pytest.raises(StorageConflictError):
        reserve_preparation_mesh_attempt(CaseStorage(storage.root), created.case_id, "mesh-4")


@pytest.mark.parametrize("change", ["skipped_size", "budget", "material"])
def test_refinement_rejects_changed_physics_or_skipped_size(
    prepared_input: tuple[Any, ...], monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    service, created, request, backend, _ = prepared_input
    _isolate(monkeypatch, backend)
    payload = _mesh_study_request(request)
    prepared = service.prepare_planar(created.case_id, payload, expected_generation=0)
    storage = service._storage(created.case_id)
    before = storage.current_draft(created.case_id)
    payload["values"]["mesh_policy"]["global_size"] = {
        "value": 0.5 if change == "skipped_size" else 1.0,
        "unit": "mm",
    }
    if change == "budget":
        payload["values"]["budget"]["max_attempts"] = 5
    elif change == "material":
        payload["values"]["material"]["youngs_modulus"]["value"] *= 2
    with pytest.raises(ValueError):
        service.prepare_planar(
            created.case_id,
            payload,
            expected_generation=prepared["generation"],
            parent_revision_id=prepared["revision_id"],
        )
    assert storage.current_draft(created.case_id) == before
    assert storage.current_frozen_revision(created.case_id) == prepared["revision_id"]
    assert len(backend.mesh_requests) == 1
