"""Four public initial-inspection contracts; all native dependencies are synthetic."""

from __future__ import annotations

import importlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from febio_cae.application.service import RegisteredCaseService
from febio_cae.cli.main import main
from febio_cae.domain.canonical import canonical_bytes


@pytest.fixture
def initial(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    fixtures = importlib.import_module("geometry.conftest")
    application = importlib.import_module("febio_cae.application._inspection")
    worker = importlib.import_module("febio_cae.adapters.geometry.inspection")
    step = tmp_path / "input.step"
    step.write_bytes(
        b"ISO-10303-21; HEADER; FILE_SCHEMA(('AUTOMOTIVE_DESIGN')); ENDSEC; "
        b"DATA; ENDSEC; END-ISO-10303-21;"
    )
    service = RegisteredCaseService(state_dir=tmp_path / "state")
    created = service.create_case(case_root=tmp_path / "case", cad_path=step)
    backend = fixtures.SyntheticBackend()
    backend.backend_id, backend.backend_version = "gmsh-occ", "4.15.2"
    backend.evidence = {
        "module": "synthetic-module-only",
        "module_sha256": "a" * 64,
        "gmsh_version": "4.15.2",
        "occt_version": "8.0.1",
        "build_info": "Synthetic fixture; OCC version: 8.0.1",
    }
    limits = {
        "available_cpus": 2,
        "cpu_workers": 2,
        "total_physical_bytes": 512 * 1024 * 1024,
        "available_physical_bytes": 320 * 1024 * 1024,
        "memory_bytes": 256 * 1024 * 1024,
    }
    calls: list[dict[str, Any]] = []
    state = SimpleNamespace(
        service=service,
        created=created,
        worker=worker,
        application=application,
        backend=backend,
        limits=limits,
        calls=calls,
        mutate=None,
        after=None,
        oversized=False,
        root=tmp_path,
    )

    def resources(cpu: int) -> dict[str, int]:
        return {**limits, "cpu_workers": min(cpu, limits["available_cpus"])}

    def launch(argv: tuple[str, ...], directory: Path, **budget: Any) -> dict[str, int]:
        calls.append({"argv": argv, "directory": directory, **budget})
        assert 0 < budget["timeout_seconds"] <= 600
        assert budget["memory_bytes"] == limits["memory_bytes"]
        # Real child serialization and adapter composition, private synthetic backend only.
        with monkeypatch.context() as child:
            child.chdir(directory)
            worker._main()
        output = directory / "output.json"
        if state.mutate is not None:
            raw = json.loads(output.read_bytes())
            state.mutate(raw)
            output.write_bytes(canonical_bytes(raw))
        if state.oversized:
            with output.open("wb") as stream:
                stream.truncate(16 * 1024 * 1024 + 1)
        if state.after is not None:
            state.after()
        return {"pid": 123, "creation_time": 456, "exit_code": 0}

    monkeypatch.setattr(application, "resource_snapshot", resources, raising=False)
    monkeypatch.setattr(worker, "_make_backend", lambda cpu: backend, raising=False)
    monkeypatch.setattr(worker, "_run_owned", launch, raising=False)
    return state


def invoke(initial: Any, capsys: pytest.CaptureFixture[str], *options: str) -> tuple[int, Any]:
    argv = [
        "case",
        "--state-dir",
        str(initial.root / "state"),
        "inspect",
        initial.created.case_id,
        "--native",
        "--json",
        *options,
    ]
    try:
        code = main(argv)
    except SystemExit as error:
        # An absent command is a genuine public behavior failure, not collection failure.
        assert isinstance(error.code, int)
        code = error.code
    output = capsys.readouterr().out
    return code, json.loads(output) if output else {}


def test_initial_native_topology_without_spec(
    initial: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    before = initial.service.current_draft(initial.created.case_id).to_dict()
    assert before["values"]["geometry"] is None
    code, payload = invoke(initial, capsys)
    assert code == 0 and payload.get("status") == "INSPECTED"
    assert payload["generation"] == before["generation"]
    assert payload["geometry"]["declared_unit"] == "mm"
    assert payload["geometry"]["body_ids"] == ["part-body"]
    source = initial.service.resolve_source(initial.created.case_id, "cad")
    report = initial.backend.inspect(source.content, ())
    assert payload["topology"] == report.to_dict()
    import hashlib

    assert (
        payload["geometry"]["inspection_digest"]
        == hashlib.sha256(canonical_bytes(report.to_dict())).hexdigest()
    )
    assert payload["native_qualification"] == "UNVERIFIED"
    assert payload["revision_id"] is None and payload["run_id"] is None
    assert payload["limits"]["max_response_bytes"] == 16 * 1024 * 1024
    assert payload["limits"]["mesh_generations"] == 0
    assert len(initial.calls) == 1 and initial.backend.mesh_requests == []
    assert initial.service.current_draft(initial.created.case_id).to_dict() == before
    assert (
        initial.service._storage(initial.created.case_id).current_frozen_revision(
            initial.created.case_id
        )
        is None
    )


def test_source_report_and_generation_admission(
    initial: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    before = initial.service.current_draft(initial.created.case_id)
    initial.mutate = lambda raw: raw["inspection"].update(source_digest="0" * 64)
    code, payload = invoke(initial, capsys)
    assert code == 6 and payload["diagnostics"][0]["code"] == "integrity"
    assert initial.service.current_draft(initial.created.case_id) == before
    initial.mutate = None

    def concurrent_edit() -> None:
        # Must complete while child is active: parent must not retain its source/database lease.
        initial.service.set_spec(
            initial.created.case_id,
            values=before.values,
            expected_generation=before.generation,
            input_intent="explicit concurrent edit",
        )

    initial.after = concurrent_edit
    code, payload = invoke(initial, capsys)
    assert code == 8 and payload["status"] == "CONFLICT"
    assert payload["generation"] == before.generation
    assert (
        initial.service.current_draft(initial.created.case_id).generation == before.generation + 1
    )
    initial.after = None
    source_file = next((initial.root / "case").rglob("*.step"), None)
    # Resolve the registered source path from its actual fixture bytes, not an invented layout.
    if source_file is None:
        source = initial.service.resolve_source(initial.created.case_id, "cad")
        source_file = next(
            p
            for p in (initial.root / "case").rglob("*")
            if p.is_file() and p.read_bytes() == source.content
        )
    source_file.write_bytes(b"tampered registered source")
    calls_before = len(initial.calls)
    code, payload = invoke(initial, capsys)
    assert code == 6 and payload["diagnostics"][0]["code"] == "integrity"
    assert len(initial.calls) == calls_before


def test_unsupported_policy_and_response_bounds(
    initial: Any, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    initial.backend.evidence["occt_version"] = "wrong"
    code, payload = invoke(initial, capsys)
    assert code == 4 and payload["status"] == "UNSUPPORTED_ENVIRONMENT"
    initial.backend.evidence["occt_version"] = "8.0.1"
    calls_before = len(initial.calls)
    for options in (("--wall-seconds", "nan"), ("--wall-seconds", "601"), ("--cpu-workers", "3")):
        code, payload = invoke(initial, capsys, *options)
        assert code == 2 and payload["status"] == "INVALID_INPUT"
    assert len(initial.calls) == calls_before
    policy = initial.application.InspectionPolicy
    for policy_options in ({"wall_seconds": True}, {"wall_seconds": 0}, {"cpu_workers": False}):
        with pytest.raises((ValueError, TypeError)):
            policy(**policy_options)
    initial.oversized = True
    original_read = Path.read_bytes

    def bounded_read(path: Path) -> bytes:
        if path.name == "output.json":
            assert path.stat().st_size <= 16 * 1024 * 1024, "oversized JSON was read"
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", bounded_read)
    code, payload = invoke(initial, capsys)
    assert code == 6 and payload["diagnostics"][0]["code"] == "integrity"


def test_owned_deadline_failure(
    initial: Any, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    owned = importlib.import_module("febio_cae.adapters.geometry.preparation")
    clock = [0.0]
    events: list[str] = []

    class HangingChild:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            events.append("launch")

        def poll(self) -> None:
            return None

        def active_processes(self) -> int:
            return 1

        def terminate_tree(self) -> None:
            events.append("terminate")

        def retry_launch_cleanup(self) -> bool:
            events.append("cleanup")
            return True

    def sleep(seconds: float) -> None:
        clock[0] += seconds

    fake_time = SimpleNamespace(monotonic=lambda: clock[0], sleep=sleep)

    def resources(cpu: int) -> Any:
        clock[0] += 0.02
        return initial.limits

    monkeypatch.setattr(initial.application, "resource_snapshot", resources)
    monkeypatch.setattr(owned, "time", fake_time)
    monkeypatch.setattr(owned, "WindowsJobProcess", HangingChild)
    monkeypatch.setattr(initial.application, "time", fake_time, raising=False)
    monkeypatch.setattr(initial.worker, "time", fake_time, raising=False)
    monkeypatch.setattr(initial.worker, "_run_owned", owned._run_owned)
    code, payload = invoke(initial, capsys, "--wall-seconds", "0.05")
    assert code == 4 and payload["status"] == "UNSUPPORTED_ENVIRONMENT"
    assert events == ["launch", "terminate", "cleanup"]
    assert clock[0] < 0.06  # Resource work consumes the same enclosing 0.05s budget.
    assert not owned._pending and payload["pending_cleanup"] == 0
    assert "deadline" in payload["diagnostics"][0]["message"]
    assert initial.service.current_draft(initial.created.case_id) == initial.created.draft

    from febio_cae.storage.registry import CaseStorage

    original_snapshot = CaseStorage.evidence_snapshot

    @contextmanager
    def slow_snapshot(storage: CaseStorage) -> Iterator[None]:
        with original_snapshot(storage):
            clock[0] += 0.1
            yield

    clock[0] = 0.0
    events.clear()
    monkeypatch.setattr(CaseStorage, "evidence_snapshot", slow_snapshot)
    code, payload = invoke(initial, capsys, "--wall-seconds", "0.05")
    assert code == 4 and payload["status"] == "UNSUPPORTED_ENVIRONMENT"
    assert payload["diagnostics"][0]["code"] == "environment"
    assert "deadline" in payload["diagnostics"][0]["message"]
    assert events == [] and not owned._pending
    assert initial.service.current_draft(initial.created.case_id) == initial.created.draft
