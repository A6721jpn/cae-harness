from __future__ import annotations

import io
import json
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from febio_cae_harness import cli as cli_module
from febio_cae_harness.cli_context import CaseContextService, dump_root_capability
from febio_cae_harness.contracts import IntentContract, IntentState
from febio_cae_harness.solver.execution import issue_execution_authority
from febio_cae_harness.solver.headless import HeadlessRunDiagnostic
from febio_cae_harness.solver.runtime import (
    FebioRuntimeDiagnostic,
    RuntimeProbeError,
    probe_febio,
)
from febio_cae_harness.solver.types import SolverClassification, SolverState
from febio_cae_harness.workspace import AttemptWorkspace


def _complete_intent() -> IntentContract:
    names = (
        "engineering_question",
        "units",
        "material",
        "loads",
        "constraints",
        "contact",
        "analysis_step",
        "roi",
        "evaluation_quantities",
    )
    return IntentContract(
        engineering_question="What is the synthetic response?",
        units={"length": "synthetic-unit"},
        material={"name": "synthetic-material"},
        loads=({"name": "synthetic-load"},),
        constraints=({"name": "synthetic-constraint"},),
        contact={"mode": "synthetic-contact"},
        analysis_step={"name": "synthetic-step"},
        roi=({"name": "synthetic-roi"},),
        evaluation_quantities=({"name": "synthetic-output"},),
        condition_sources={
            name: {"authoritative": True, "current": True, "source": "synthetic-user"}
            for name in names
        },
        state=IntentState.GATHERING,
    )


def _service(root: Path) -> CaseContextService:
    tool_root = root / "tool"
    tool_root.mkdir(parents=True)
    return CaseContextService._for_tests(
        registry_root=root / "registry",
        tool_root=tool_root,
    )


def _register_case(
    root: Path,
    *,
    intent: IntentContract,
    source_names: tuple[str, ...] = ("model.feb",),
) -> tuple[CaseContextService, object, Path]:
    service = _service(root)
    cae_root = root / "02_CAE"
    cae_root.mkdir()
    sources: list[Path] = []
    for name in source_names:
        source = root / name
        source.write_text("<febio_spec version='4.0' />", encoding="utf-8")
        sources.append(source)
    registered = service.register_root(cae_root)
    service.create_case(
        root_capability=registered["capability"],
        case_id="case-a",
        sources=tuple(sources),
        intent=intent,
    )
    return service, registered["capability"], cae_root / "case-a"


def _set_capability_stdin(monkeypatch: pytest.MonkeyPatch, capability: object) -> None:
    stream = io.TextIOWrapper(io.BytesIO(dump_root_capability(capability)), encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", stream)


def _issued_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FebioRuntimeDiagnostic:
    executable = tmp_path / "fake-febio.exe"
    executable.write_bytes(b"synthetic FEBio executable")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)

    class CompletedProcess:
        returncode = 0

        def communicate(self, input: bytes, timeout: float) -> tuple[bytes, bytes]:
            assert input == b"quit\n"
            assert timeout > 0
            return b"version 4.12.0\n", b""

    monkeypatch.setattr(
        "febio_cae_harness.solver.runtime.subprocess.Popen",
        lambda command, **kwargs: CompletedProcess(),
    )
    return probe_febio(executable)


def _run_arguments(runtime: Path, *extra: str) -> list[str]:
    return [
        "run-febio",
        "--capability-stdin",
        "--case-id",
        "case-a",
        "--runtime-probe",
        str(runtime),
        "--expected-steps",
        "1",
        "--expected-final-time",
        "1.0",
        "--requested-field",
        "displacement",
        *extra,
    ]


def _reconnect_arguments(runtime: Path, *extra: str) -> list[str]:
    return [
        "reconnect-febio",
        "--capability-stdin",
        "--case-id",
        "case-a",
        "--attempt-id",
        "run-existing",
        "--runtime-probe",
        str(runtime),
        *extra,
    ]


def _contains_path_key(value: object) -> bool:
    if isinstance(value, dict):
        return any(
            "path" in str(key).casefold() or _contains_path_key(item) for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_path_key(item) for item in value)
    return False


def test_run_febio_parser_accepts_only_case_bound_launch_inputs(tmp_path: Path) -> None:
    parsed = cli_module.build_parser().parse_args(
        _run_arguments(tmp_path / "febio.exe", "--input-name", "model.feb")
    )

    assert parsed.command == "run-febio"
    assert parsed.capability_stdin is True
    assert parsed.case_id == "case-a"
    assert parsed.input_name == "model.feb"
    assert parsed.requested_field == ["displacement"]
    assert not {
        "executable",
        "input",
        "attempt_root",
        "attempt_id",
        "arguments",
        "fbs_adapter",
    }.intersection(vars(parsed))


def test_reconnect_febio_parser_cannot_redefine_recorded_launch_context(
    tmp_path: Path,
) -> None:
    parsed = cli_module.build_parser().parse_args(_reconnect_arguments(tmp_path / "febio.exe"))

    assert parsed.command == "reconnect-febio"
    assert parsed.capability_stdin is True
    assert parsed.case_id == "case-a"
    assert parsed.attempt_id == "run-existing"
    assert not {
        "input",
        "input_name",
        "expected_steps",
        "expected_final_time",
        "requested_field",
        "timeout_seconds",
        "arguments",
    }.intersection(vars(parsed))


def test_reconnect_runtime_probe_failure_does_not_append_orphan_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service, capability, case_root = _register_case(tmp_path, intent=_complete_intent())
    opened = service._open_context(capability, "case-a")
    opened.store.record_attempt("run-existing", {"status": "started"})
    _set_capability_stdin(monkeypatch, capability)
    monkeypatch.setattr(cli_module, "_case_service", lambda: service)

    def reject_probe(path: Path) -> None:
        del path
        raise RuntimeProbeError("synthetic probe failure")

    monkeypatch.setattr(cli_module, "probe_febio", reject_probe)

    assert cli_module.main(_reconnect_arguments(tmp_path / "febio.exe")) == 4

    payload = json.loads(capsys.readouterr().err)
    assert payload["error"]["code"] == "RUNTIME_PROBE_FAILED"
    events = [
        json.loads(line)
        for line in (case_root / "90_Temporary" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert events[-1]["event_type"] == "attempt_recorded"
    assert not any(event["event_type"] == "run_febio_terminal" for event in events)


def test_reconnect_febio_reports_authenticated_solver_failure_without_new_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service, capability, case_root = _register_case(tmp_path, intent=_complete_intent())
    opened = service._open_context(capability, "case-a")
    opened.store.record_attempt("run-existing", {"status": "started"})
    runtime = _issued_runtime(tmp_path, monkeypatch)
    _set_capability_stdin(monkeypatch, capability)
    monkeypatch.setattr(cli_module, "_case_service", lambda: service)
    monkeypatch.setattr(cli_module, "probe_febio", lambda path: runtime)

    result = SimpleNamespace(
        state=SolverState.FAILED,
        classification=SolverClassification.FATAL,
        return_code=1,
        pid=123,
        log_path=case_root / "90_Temporary" / "attempts" / "run-existing" / "model.log",
        xplt_path=case_root / "90_Temporary" / "attempts" / "run-existing" / "model.xplt",
    )
    supervisor = SimpleNamespace(wait=lambda: result)
    execution = SimpleNamespace(log_path=result.log_path, xplt_path=result.xplt_path)
    session = SimpleNamespace(supervisor=supervisor, execution=execution)
    monkeypatch.setattr(
        cli_module,
        "recover_headless_febio",
        lambda store, snapshot, runtime_diagnostic, attempt_id: session,
        raising=False,
    )

    assert cli_module.main(_reconnect_arguments(runtime.path)) == 4

    captured = capsys.readouterr()
    assert captured.out == ""
    payload = json.loads(captured.err)
    assert payload["command"] == "reconnect-febio"
    assert payload["error"]["code"] == "SOLVER_FAILED"
    assert payload["attempt"] == {
        "attempt_id": "run-existing",
        "official_fbs": False,
        "status": "FATAL",
    }
    attempts = list((case_root / "90_Temporary" / "attempts").iterdir())
    assert [attempt.name for attempt in attempts] == ["run-existing"]
    events = [
        json.loads(line)
        for line in (case_root / "90_Temporary" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert events[-1]["event_type"] == "run_febio_terminal"
    assert events[-1]["payload"]["status"] == "SOLVER_FAILED"


def test_reconnect_febio_claims_exact_outputs_but_remains_fbs_unverified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service, capability, case_root = _register_case(tmp_path, intent=_complete_intent())
    opened = service._open_context(capability, "case-a")
    opened.store.record_attempt("run-existing", {"status": "started"})
    attempt = AttemptWorkspace._from_manager(
        opened.case,
        "run-existing",
        opened.case.temporary_root / "attempts" / "run-existing",
    )
    input_path = attempt.write_text("model.feb", "<febio_spec version='4.0' />")
    runtime = _issued_runtime(tmp_path, monkeypatch)
    execution = issue_execution_authority(
        attempt,
        opened.store.issue_intent_snapshot(),
        runtime,
        input_path,
        requested_fields=("displacement",),
        expected_steps=1,
        expected_final_time=1.0,
        timeout_seconds=30.0,
    )
    execution.log_path.write_bytes(b"synthetic normal termination\n")
    execution.xplt_path.write_bytes(b"synthetic XPLT\n")
    result = SimpleNamespace(
        state=SolverState.NORMAL_EXIT,
        classification=SolverClassification.FBS_UNVERIFIED,
        return_code=0,
        pid=123,
        log_path=execution.log_path,
        xplt_path=execution.xplt_path,
    )
    session = SimpleNamespace(
        supervisor=SimpleNamespace(wait=lambda: result),
        execution=execution,
    )
    _set_capability_stdin(monkeypatch, capability)
    monkeypatch.setattr(cli_module, "_case_service", lambda: service)
    monkeypatch.setattr(cli_module, "probe_febio", lambda path: runtime)
    monkeypatch.setattr(
        cli_module,
        "recover_headless_febio",
        lambda store, snapshot, runtime_diagnostic, attempt_id: session,
    )

    assert cli_module.main(_reconnect_arguments(runtime.path)) == 5

    captured = capsys.readouterr()
    payload = json.loads(captured.err)
    assert payload["command"] == "reconnect-febio"
    assert payload["error"]["code"] == "FBS_UNAVAILABLE"
    assert payload["attempt"]["official_fbs"] is False
    manifest = service._open_context(capability, "case-a").store.manifest
    paths = {artifact["path"] for artifact in manifest["artifacts"]}
    prefix = "90_Temporary/attempts/run-existing/"
    assert paths == {
        f"{prefix}execution.json",
        f"{prefix}model.feb",
        f"{prefix}model.log",
        f"{prefix}model.xplt",
    }
    assert not (case_root / "50_Reports").exists()


def test_run_febio_solves_but_fails_closed_without_official_fbs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service, capability, case_root = _register_case(tmp_path, intent=_complete_intent())
    runtime = _issued_runtime(tmp_path, monkeypatch)
    _set_capability_stdin(monkeypatch, capability)
    monkeypatch.setattr(cli_module, "_case_service", lambda: service)
    monkeypatch.setattr(cli_module, "probe_febio", lambda path: runtime)

    def run_synthetic(
        attempt: Any,
        snapshot: Any,
        runtime_diagnostic: FebioRuntimeDiagnostic,
        input_path: Path,
        **kwargs: object,
    ) -> HeadlessRunDiagnostic:
        del snapshot, kwargs
        log_path = input_path.with_suffix(".log")
        xplt_path = input_path.with_suffix(".xplt")
        log_path.write_bytes(b"synthetic normal termination\n")
        xplt_path.write_bytes(b"synthetic XPLT\n")
        return HeadlessRunDiagnostic(
            runtime_identity=runtime_diagnostic,
            state=SolverState.NORMAL_EXIT,
            classification=SolverClassification.FBS_UNVERIFIED,
            return_code=0,
            pid=123,
            log_path=log_path,
            xplt_path=xplt_path,
        )

    monkeypatch.setattr(cli_module, "run_headless_febio", run_synthetic)

    assert cli_module.main(_run_arguments(runtime.path)) == 5

    captured = capsys.readouterr()
    assert captured.out == ""
    payload = json.loads(captured.err)
    assert payload["ok"] is False
    assert payload["command"] == "run-febio"
    assert payload["error"]["code"] == "FBS_UNAVAILABLE"
    assert payload["attempt"]["status"] == "FBS_UNVERIFIED"
    assert payload["attempt"]["official_fbs"] is False
    assert payload["solver"] == {
        "classification": "FBS_UNVERIFIED",
        "return_code": 0,
        "state": "NORMAL_EXIT",
    }
    assert not _contains_path_key(payload)
    assert str(tmp_path) not in captured.err
    attempts = list((case_root / "90_Temporary" / "attempts").iterdir())
    assert len(attempts) == 1
    assert (attempts[0] / "execution.json").is_file()
    events = [
        json.loads(line)
        for line in (case_root / "90_Temporary" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert events[-1]["event_type"] == "run_febio_terminal"
    assert events[-1]["payload"] == {
        "attempt_id": attempts[0].name,
        "classification": "FBS_UNVERIFIED",
        "official_fbs": False,
        "return_code": 0,
        "solver_state": "NORMAL_EXIT",
        "status": "FBS_UNAVAILABLE",
    }
    reports = case_root / "50_Reports"
    assert not reports.exists() or not any(reports.iterdir())


def test_run_febio_ask_and_block_does_not_probe_or_create_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service, capability, case_root = _register_case(tmp_path, intent=IntentContract())
    runtime_path = tmp_path / "must-not-probe.exe"
    _set_capability_stdin(monkeypatch, capability)
    monkeypatch.setattr(cli_module, "_case_service", lambda: service)
    monkeypatch.setattr(
        cli_module,
        "probe_febio",
        lambda path: pytest.fail("ASK_AND_BLOCK reached the runtime probe"),
    )

    assert cli_module.main(_run_arguments(runtime_path)) == 4

    captured = capsys.readouterr()
    assert captured.out == ""
    payload = json.loads(captured.err)
    assert payload["error"]["code"] == "ASK_AND_BLOCK"
    assert str(tmp_path) not in captured.err
    assert not list((case_root / "90_Temporary" / "attempts").iterdir())


def test_run_febio_requires_exact_input_name_when_case_has_multiple_febs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service, capability, case_root = _register_case(
        tmp_path,
        intent=_complete_intent(),
        source_names=("first.feb", "second.feb"),
    )
    _set_capability_stdin(monkeypatch, capability)
    monkeypatch.setattr(cli_module, "_case_service", lambda: service)
    monkeypatch.setattr(
        cli_module,
        "probe_febio",
        lambda path: pytest.fail("ambiguous input reached the runtime probe"),
    )

    assert cli_module.main(_run_arguments(tmp_path / "must-not-probe.exe")) == 4

    captured = capsys.readouterr()
    assert captured.out == ""
    payload = json.loads(captured.err)
    assert payload["error"]["code"] == "INPUT_SELECTION_REQUIRED"
    assert not list((case_root / "90_Temporary" / "attempts").iterdir())


def test_run_febio_rejects_relative_runtime_before_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service, capability, case_root = _register_case(tmp_path, intent=_complete_intent())
    _set_capability_stdin(monkeypatch, capability)
    monkeypatch.setattr(cli_module, "_case_service", lambda: service)
    monkeypatch.setattr(
        cli_module,
        "probe_febio",
        lambda path: pytest.fail("relative runtime reached the probe"),
    )

    assert cli_module.main(_run_arguments(Path("relative-febio.exe"))) == 4

    captured = capsys.readouterr()
    assert captured.out == ""
    payload = json.loads(captured.err)
    assert payload["error"]["code"] == "INVALID_INPUT"
    assert not list((case_root / "90_Temporary" / "attempts").iterdir())


def test_run_febio_solver_failure_is_not_relabelled_as_fbs_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service, capability, case_root = _register_case(tmp_path, intent=_complete_intent())
    runtime = _issued_runtime(tmp_path, monkeypatch)
    _set_capability_stdin(monkeypatch, capability)
    monkeypatch.setattr(cli_module, "_case_service", lambda: service)
    monkeypatch.setattr(cli_module, "probe_febio", lambda path: runtime)

    def fail_synthetic(
        attempt: Any,
        snapshot: Any,
        runtime_diagnostic: FebioRuntimeDiagnostic,
        input_path: Path,
        **kwargs: object,
    ) -> HeadlessRunDiagnostic:
        del attempt, snapshot, kwargs
        return HeadlessRunDiagnostic(
            runtime_identity=runtime_diagnostic,
            state=SolverState.FAILED,
            classification=SolverClassification.FATAL,
            return_code=1,
            pid=123,
            log_path=input_path.with_suffix(".log"),
            xplt_path=input_path.with_suffix(".xplt"),
        )

    monkeypatch.setattr(cli_module, "run_headless_febio", fail_synthetic)

    assert cli_module.main(_run_arguments(runtime.path)) == 4

    captured = capsys.readouterr()
    assert captured.out == ""
    payload = json.loads(captured.err)
    assert payload["error"]["code"] == "SOLVER_FAILED"
    assert payload["attempt"]["official_fbs"] is False
    assert payload["attempt"]["status"] == "FATAL"
    assert payload["solver"]["classification"] == "FATAL"
    events = [
        json.loads(line)
        for line in (case_root / "90_Temporary" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert events[-1]["event_type"] == "run_febio_terminal"
    assert events[-1]["payload"]["status"] == "SOLVER_FAILED"
